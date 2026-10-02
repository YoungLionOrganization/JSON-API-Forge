from __future__ import annotations

import asyncio
import json
import logging
from collections import defaultdict
from collections.abc import AsyncIterator
from dataclasses import dataclass
from typing import Any

from fastapi import HTTPException, WebSocket

log = logging.getLogger("json_api_forge.events")


@dataclass(eq=False)
class _SocketClient:
    websocket: WebSocket
    queue: asyncio.Queue
    sender: asyncio.Task | None = None


class EventHub:
    """In-process bounded best-effort pub/sub for a single worker."""

    def __init__(self):
        self._subscribers: dict[str, set[asyncio.Queue]] = defaultdict(set)
        self._websockets: dict[str, set[_SocketClient]] = defaultdict(set)
        self._lock = asyncio.Lock()
        self.dropped_messages = 0

    def _record_drop(self, channel: str) -> None:
        self.dropped_messages += 1
        if self.dropped_messages & (self.dropped_messages - 1) == 0:
            log.warning("Realtime queue overflow channel=%s dropped_total=%d", channel, self.dropped_messages)

    async def publish(self, channel: str, event: Any) -> int:
        async with self._lock:
            queues = list(self._subscribers[channel])
            sockets = list(self._websockets[channel])
        fanout = 0
        for queue in queues:
            try:
                queue.put_nowait(event)
                fanout += 1
            except asyncio.QueueFull:
                self._record_drop(channel)
        for client in sockets:
            try:
                client.queue.put_nowait(event)
                fanout += 1
            except asyncio.QueueFull:
                self._record_drop(channel)
        return fanout

    async def subscribe(self, channel: str, queue_size: int, max_subscribers: int | None = None) -> AsyncIterator[Any]:
        queue = asyncio.Queue(maxsize=queue_size)
        async with self._lock:
            if max_subscribers is not None and len(self._subscribers[channel]) >= max_subscribers:
                raise HTTPException(status_code=503, detail="SSE connection limit reached", headers={"Retry-After": "1"})
            self._subscribers[channel].add(queue)
        try:
            while True:
                yield await queue.get()
        finally:
            async with self._lock:
                self._subscribers[channel].discard(queue)

    async def _socket_sender(self, channel: str, client: _SocketClient) -> None:
        try:
            while True:
                event = await client.queue.get()
                await client.websocket.send_json(event)
        except asyncio.CancelledError:
            raise
        except Exception:
            async with self._lock:
                self._websockets[channel].discard(client)

    async def connect_ws(self, channel: str, websocket: WebSocket, queue_size: int = 256, max_connections: int | None = None) -> None:
        client = _SocketClient(websocket=websocket, queue=asyncio.Queue(maxsize=queue_size))
        async with self._lock:
            if max_connections is not None and len(self._websockets[channel]) >= max_connections:
                raise HTTPException(status_code=503, detail="WebSocket connection limit reached", headers={"Retry-After": "1"})
            self._websockets[channel].add(client)
        try:
            await websocket.accept()
            client.sender = asyncio.create_task(self._socket_sender(channel, client), name=f"forge-ws-sender-{channel}")
        except BaseException:
            async with self._lock:
                self._websockets[channel].discard(client)
            raise

    async def disconnect_ws(self, channel: str, websocket: WebSocket) -> None:
        client = None
        async with self._lock:
            for candidate in list(self._websockets[channel]):
                if candidate.websocket is websocket:
                    client = candidate
                    self._websockets[channel].discard(candidate)
                    break
        if client and client.sender:
            client.sender.cancel()
            await asyncio.gather(client.sender, return_exceptions=True)

    async def ping(self) -> bool:
        return True

    async def close(self) -> None:
        clients = [client for values in self._websockets.values() for client in values]
        for client in clients:
            if client.sender:
                client.sender.cancel()
        if clients:
            await asyncio.gather(*(client.sender for client in clients if client.sender), return_exceptions=True)
        self._websockets.clear()
        self._subscribers.clear()


class RedisEventHub(EventHub):
    def __init__(self, url: str, prefix: str, project_slug: str):
        super().__init__()
        from redis.asyncio import from_url

        self.redis = from_url(url, encoding="utf-8", decode_responses=True)
        self.prefix = f"{prefix}:{project_slug}"
        self._listeners = {}
        self._listener_ready = {}
        self._listener_lock = asyncio.Lock()

    def _redis_channel(self, channel: str) -> str:
        return f"{self.prefix}:{channel}"

    async def _ensure_listener(self, channel: str) -> None:
        async with self._listener_lock:
            task = self._listeners.get(channel)
            ready = self._listener_ready.get(channel)
            if not task or task.done():
                ready = asyncio.Event()
                self._listener_ready[channel] = ready
                task = asyncio.create_task(self._listen(channel, ready), name=f"forge-redis-event-{channel}")
                self._listeners[channel] = task
        if ready is None:
            raise RuntimeError(f"Redis listener readiness state was not created for {channel!r}")
        ready_waiter = asyncio.create_task(ready.wait(), name=f"forge-redis-ready-{channel}")
        try:
            done, _ = await asyncio.wait({ready_waiter, task}, timeout=5.0, return_when=asyncio.FIRST_COMPLETED)
            if task in done and not ready.is_set():
                task.result()
            if ready_waiter in done and ready_waiter.result():
                return
            if ready.is_set():
                return
            raise RuntimeError(f"Redis event listener did not become ready for channel {channel!r}")
        finally:
            if not ready_waiter.done():
                ready_waiter.cancel()
                await asyncio.gather(ready_waiter, return_exceptions=True)

    async def _listen(self, channel: str, ready: asyncio.Event) -> None:
        pubsub = self.redis.pubsub()
        await pubsub.subscribe(self._redis_channel(channel))
        ready.set()
        try:
            async for message in pubsub.listen():
                if message.get("type") != "message":
                    continue
                try:
                    event = json.loads(message["data"])
                except Exception:
                    event = {"event": message["data"]}
                await super().publish(channel, event)
        except asyncio.CancelledError:
            raise
        finally:
            ready.set()
            try:
                await pubsub.unsubscribe(self._redis_channel(channel))
            finally:
                await pubsub.aclose()

    async def publish(self, channel: str, event: Any) -> int:
        await self._ensure_listener(channel)
        return int(await self.redis.publish(self._redis_channel(channel), json.dumps(event, separators=(",", ":"), default=str)))

    async def subscribe(self, channel: str, queue_size: int, max_subscribers: int | None = None) -> AsyncIterator[Any]:
        await self._ensure_listener(channel)
        async for event in super().subscribe(channel, queue_size, max_subscribers=max_subscribers):
            yield event

    async def connect_ws(self, channel: str, websocket: WebSocket, queue_size: int = 256, max_connections: int | None = None) -> None:
        await self._ensure_listener(channel)
        await super().connect_ws(channel, websocket, queue_size=queue_size, max_connections=max_connections)

    async def ping(self) -> bool:
        return bool(await self.redis.ping())

    async def close(self) -> None:
        for task in self._listeners.values():
            task.cancel()
        if self._listeners:
            await asyncio.gather(*self._listeners.values(), return_exceptions=True)
        self._listeners.clear()
        self._listener_ready.clear()
        await super().close()
        await self.redis.aclose()


def build_event_hub(backend: str, redis_url: str | None, prefix: str, project_slug: str):
    if backend == "memory":
        return EventHub()
    if not redis_url:
        raise RuntimeError("realtime.backend='redis' requires REDIS_URL")
    return RedisEventHub(redis_url, prefix, project_slug)


def sse_encode(event: Any) -> bytes:
    return ("data: " + json.dumps(event, separators=(",", ":"), default=str) + "\n\n").encode("utf-8")
