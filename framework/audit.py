from __future__ import annotations

import asyncio
import logging
from datetime import datetime, timezone
from typing import Any

from sqlalchemy import insert

from .observability import observe_audit_drop, observe_audit_queue, observe_audit_write_failure
from .security import audit_table

log = logging.getLogger("json_api_forge.audit")


class AuditWriter:
    """Bounded, batched best-effort audit writer with visible failure semantics.

    Audit writes stay off the request critical path. Queue overflow and database
    failures are never silent: counters, queue-depth metrics and logs expose loss.
    Transient database failures are retried with bounded exponential backoff before
    the batch is declared lost.

    This component is operational/audit telemetry, not an immutable compliance
    ledger. Deployments that require guaranteed non-repudiable audit retention should
    forward events to a durable external log/queue with its own replication policy.
    """

    def __init__(
        self,
        engine,
        max_queue: int = 20_000,
        batch_size: int = 200,
        flush_interval: float = 0.25,
        write_retries: int = 3,
        retry_backoff_seconds: float = 0.1,
        shutdown_timeout_seconds: float = 5.0,
    ):
        self.engine = engine
        self.queue: asyncio.Queue[dict[str, Any]] = asyncio.Queue(maxsize=max_queue)
        self.batch_size = max(1, batch_size)
        self.flush_interval = max(0.01, flush_interval)
        self.write_retries = max(0, write_retries)
        self.retry_backoff_seconds = max(0.0, retry_backoff_seconds)
        self.shutdown_timeout_seconds = max(0.1, shutdown_timeout_seconds)
        self._task: asyncio.Task | None = None
        self.dropped = 0
        self.write_failures = 0

    async def start(self) -> None:
        if self._task and not self._task.done():
            return
        self._task = asyncio.create_task(self._run(), name="forge-audit-writer")

    def submit(self, **values) -> None:
        values["created_at"] = datetime.now(timezone.utc)
        try:
            self.queue.put_nowait(values)
            observe_audit_queue(self.queue.qsize())
        except asyncio.QueueFull:
            self.dropped += 1
            observe_audit_drop()
            log.error("Audit queue full; dropping audit event dropped_total=%d", self.dropped)

    async def _write_batch(self, batch: list[dict[str, Any]]) -> None:
        async with self.engine.begin() as conn:
            await conn.execute(insert(audit_table), batch)

    async def _write_batch_with_retry(self, batch: list[dict[str, Any]]) -> bool:
        for attempt in range(self.write_retries + 1):
            try:
                await self._write_batch(batch)
                return True
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                self.write_failures += 1
                observe_audit_write_failure()
                final = attempt >= self.write_retries
                log.log(
                    logging.ERROR if final else logging.WARNING,
                    "Audit batch write failed attempt=%d/%d batch_size=%d failures_total=%d error=%s",
                    attempt + 1,
                    self.write_retries + 1,
                    len(batch),
                    self.write_failures,
                    type(exc).__name__,
                    exc_info=final,
                )
                if final:
                    self.dropped += len(batch)
                    observe_audit_drop(len(batch))
                    log.error(
                        "Audit batch permanently dropped after retries batch_size=%d dropped_total=%d",
                        len(batch),
                        self.dropped,
                    )
                    return False
                if self.retry_backoff_seconds:
                    await asyncio.sleep(min(self.retry_backoff_seconds * (2**attempt), 2.0))
        return False

    async def _run(self) -> None:
        while True:
            batch: list[dict[str, Any]] = []
            try:
                first = await self.queue.get()
                batch.append(first)
                deadline = asyncio.get_running_loop().time() + self.flush_interval
                while len(batch) < self.batch_size:
                    timeout = max(0.0, deadline - asyncio.get_running_loop().time())
                    if timeout == 0:
                        break
                    try:
                        batch.append(await asyncio.wait_for(self.queue.get(), timeout))
                    except asyncio.TimeoutError:
                        break
                await self._write_batch_with_retry(batch)
            except asyncio.CancelledError:
                if batch:
                    self.dropped += len(batch)
                    observe_audit_drop(len(batch))
                    log.error("Audit writer cancelled with in-flight batch size=%d", len(batch))
                raise
            finally:
                for _ in batch:
                    try:
                        self.queue.task_done()
                    except ValueError:
                        break
                observe_audit_queue(self.queue.qsize())

    async def close(self) -> None:
        try:
            await asyncio.wait_for(self.queue.join(), timeout=self.shutdown_timeout_seconds)
        except asyncio.TimeoutError:
            remaining = self.queue.qsize()
            if remaining:
                self.dropped += remaining
                observe_audit_drop(remaining)
            log.error("Audit queue did not drain before shutdown remaining=%d dropped_total=%d", remaining, self.dropped)
        if self._task:
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass
