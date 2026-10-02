from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Any

from .cache import build_cache
from .config import ForgeConfig, ProjectConfig
from .datasources import DataSourceManager
from .db import build_registry
from .events import EventHub, build_event_hub
from .media import build_media_store
from .mongo import build_mongo_registry
from .observability import measure_stage
from .protection import ConcurrencyGate
from .rate_limit import MemoryRateLimiter, RedisRateLimiter
from .settings import settings

log = logging.getLogger("json_api_forge.runtime")


@dataclass(slots=True)
class ProjectRuntime:
    config: ProjectConfig
    registry: Any = None
    mongo_registry: Any = None
    limiter: Any = None
    cache: Any = None
    gate: ConcurrencyGate | None = None
    media_store: Any = None
    data_sources: DataSourceManager | None = None
    event_hub: EventHub | None = None
    available: bool = False
    error_type: str | None = None
    _leases: int = field(default=0, repr=False)


class RuntimeManager:
    def __init__(self, forge: ForgeConfig, reuse: dict[str, ProjectRuntime] | None = None):
        self.forge = forge
        reuse = reuse or {}
        self.runtimes = {}
        self._borrowed: set[int] = set()
        self._leased: set[int] = set()
        for project in forge.projects:
            previous = reuse.get(project.slug)
            if previous is not None and previous.available and previous.config == project:
                self.runtimes[project.slug] = previous
                self._borrowed.add(id(previous))
            else:
                self.runtimes[project.slug] = ProjectRuntime(config=project)
        self._prefixes = sorted(
            ((runtime.config.api_prefix.rstrip("/"), slug) for slug, runtime in self.runtimes.items()),
            key=lambda pair: len(pair[0]),
            reverse=True,
        )
        self._started = False
        self._closed = False

    def for_path(self, path: str) -> ProjectRuntime | None:
        return next((self.runtimes[slug] for prefix, slug in self._prefixes if path == prefix or path.startswith(prefix + "/")), None)

    def body_limit_for_path(self, path: str) -> int | None:
        runtime = self.for_path(path)
        return runtime.config.protection.max_request_body_bytes if runtime else None

    async def _start_runtime(self, runtime: ProjectRuntime) -> None:
        cfg = runtime.config
        try:
            with measure_stage(cfg.slug, "sql_startup"):
                runtime.registry = await build_registry(cfg)
            with measure_stage(cfg.slug, "mongo_startup"):
                runtime.mongo_registry = await build_mongo_registry(cfg)
            if cfg.rate_limit.backend == "redis":
                if not settings.redis_url:
                    raise RuntimeError(f"Project {cfg.slug}: Redis rate limiter requires REDIS_URL")
                runtime.limiter = RedisRateLimiter(settings.redis_url, prefix=f"json-api-forge:rl:{cfg.slug}")
            else:
                runtime.limiter = MemoryRateLimiter(
                    max_buckets=cfg.rate_limit.memory_max_buckets,
                    idle_ttl_seconds=cfg.rate_limit.memory_idle_ttl_seconds,
                    cleanup_interval_seconds=cfg.rate_limit.memory_cleanup_interval_seconds,
                )
            runtime.cache = build_cache(cfg.cache, settings.redis_url)
            runtime.gate = ConcurrencyGate(
                cfg.protection.max_concurrent_requests, cfg.protection.max_queue_wait_seconds, cfg.protection.reject_when_saturated
            )
            runtime.data_sources = DataSourceManager(cfg)
            runtime.event_hub = build_event_hub(cfg.realtime.backend, settings.redis_url, cfg.realtime.redis_prefix, cfg.slug)
            if cfg.media.enabled:
                runtime.media_store = build_media_store(cfg.media)
        except Exception:
            await self._close_runtime(runtime, suppress=True)
            raise

    async def start(self) -> None:
        if self._started:
            return
        self._closed = False
        for runtime in self.runtimes.values():
            if runtime.error_type:
                continue
            try:
                if not runtime.available:
                    await self._start_runtime(runtime)
                    runtime.available = True
                runtime._leases += 1
                self._leased.add(id(runtime))
                cfg = runtime.config
                log.info(
                    "Loaded project=%s resources=%d operations=%d data_sources=%d prefix=%s",
                    cfg.slug,
                    len(cfg.resources),
                    len(cfg.operations),
                    len(cfg.data_sources),
                    cfg.api_prefix,
                )
            except Exception as exc:
                runtime.error_type = type(exc).__name__
                await self._close_runtime(runtime, suppress=True)
                log.error("App unavailable project=%s error_type=%s; other apps remain active", runtime.config.slug, runtime.error_type)
        self._started = True

    async def _close_runtime(self, runtime: ProjectRuntime, *, suppress: bool) -> None:
        runtime.available = False
        errors: list[Exception] = []
        for attr, close_name in (
            ("event_hub", "close"),
            ("data_sources", "close"),
            ("cache", "close"),
            ("limiter", "close"),
            ("mongo_registry", "dispose"),
            ("registry", "dispose"),
        ):
            service = getattr(runtime, attr, None)
            if service is None:
                continue
            try:
                await getattr(service, close_name)()
            except Exception as exc:
                log.exception("Project cleanup failed project=%s service=%s", runtime.config.slug, attr)
                errors.append(exc)
            finally:
                setattr(runtime, attr, None)
        runtime.gate = None
        runtime.media_store = None
        if errors and not suppress:
            raise RuntimeError(f"Project {runtime.config.slug}: {len(errors)} cleanup operation(s) failed") from errors[0]

    async def close(self) -> None:
        if self._closed:
            return
        self._closed = True
        errors: list[Exception] = []
        for runtime in reversed(list(self.runtimes.values())):
            try:
                if id(runtime) in self._leased:
                    self._leased.remove(id(runtime))
                    runtime._leases -= 1
                    if runtime._leases > 0:
                        continue
                elif id(runtime) in self._borrowed:
                    continue
                await self._close_runtime(runtime, suppress=False)
            except Exception as exc:
                errors.append(exc)
        self._started = False
        if errors:
            raise RuntimeError(f"Runtime cleanup completed with {len(errors)} failure(s)") from errors[0]
