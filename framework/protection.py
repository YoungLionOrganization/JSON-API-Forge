from __future__ import annotations

import asyncio
import ipaddress
import json
from collections.abc import Callable
from typing import Any

from fastapi import HTTPException


class ConcurrencyGate:
    def __init__(self, limit: int, wait_seconds: float, reject_when_saturated: bool = True):
        self.limit = max(1, int(limit))
        self.wait_seconds = max(0.0, float(wait_seconds))
        self.reject = bool(reject_when_saturated)
        self._active = 0
        self._condition = asyncio.Condition()

    @property
    def active(self) -> int:
        return self._active

    async def __aenter__(self):
        async with self._condition:
            if self.reject and self._active >= self.limit:
                raise HTTPException(status_code=503, detail="Server is temporarily saturated", headers={"Retry-After": "1"})

            async def wait_for_slot() -> None:
                while self._active >= self.limit:
                    await self._condition.wait()

            if self._active >= self.limit:
                try:
                    await asyncio.wait_for(wait_for_slot(), timeout=self.wait_seconds)
                except asyncio.TimeoutError as exc:
                    raise HTTPException(status_code=503, detail="Server is temporarily saturated", headers={"Retry-After": "1"}) from exc
            self._active += 1
        return self

    async def __aexit__(self, exc_type, exc, tb):
        async with self._condition:
            self._active = max(0, self._active - 1)
            self._condition.notify(1)


class RequestExecutionTimeoutMiddleware:
    """Cancel the actual endpoint task if it does not start a response in time.

    Timing only BaseHTTPMiddleware.call_next cancels its response waiter while
    the endpoint can keep running. Keep streaming lifetime semantics unchanged:
    once response headers start, the deadline no longer applies.
    """

    def __init__(self, app, timeout_for_path):
        self.app = app
        self.timeout_for_path = timeout_for_path

    async def __call__(self, scope, receive, send):
        timeout = self.timeout_for_path(scope.get("path", "")) if scope.get("type") == "http" else None
        if timeout is None:
            await self.app(scope, receive, send)
            return
        started = asyncio.Event()

        async def tracked_send(message):
            if message["type"] == "http.response.start":
                started.set()
            await send(message)

        task = asyncio.create_task(self.app(scope, receive, tracked_send))
        waiter = asyncio.create_task(started.wait())
        try:
            done, _ = await asyncio.wait((task, waiter), timeout=timeout, return_when=asyncio.FIRST_COMPLETED)
            if not done and not started.is_set():
                task.cancel()
                await asyncio.gather(task, return_exceptions=True)
                from starlette.responses import JSONResponse

                if not started.is_set():
                    await JSONResponse({"detail": "Request timed out"}, status_code=504)(scope, receive, send)
            else:
                await task
        finally:
            for pending in (task, waiter):
                if not pending.done():
                    pending.cancel()
            await asyncio.gather(task, waiter, return_exceptions=True)


def _address_in_rules(raw: str, rules: list[str]) -> bool:
    try:
        address = ipaddress.ip_address(raw)
    except ValueError:
        return False
    for rule in rules:
        try:
            if address in ipaddress.ip_network(rule, strict=False):
                return True
        except ValueError:
            if raw == rule:
                return True
    return False


def direct_peer(connection: Any) -> str:
    client = getattr(connection, "client", None)
    return client.host if client else "unknown"


def client_ip(connection: Any, trusted_proxy_cidrs: list[str] | None = None) -> str:
    peer = direct_peer(connection)
    trusted = trusted_proxy_cidrs or []
    if not trusted or not _address_in_rules(peer, trusted):
        return peer
    headers = getattr(connection, "headers", {})
    xff = headers.get("x-forwarded-for") if headers else None
    if not xff:
        return peer
    chain = [part.strip() for part in xff.split(",") if part.strip()]
    for candidate in reversed(chain):
        try:
            ipaddress.ip_address(candidate)
        except ValueError:
            continue
        if not _address_in_rules(candidate, trusted):
            return candidate
    return chain[0] if chain else peer


def request_is_https(connection: Any, trusted_proxy_cidrs: list[str] | None = None) -> bool:
    scheme = getattr(getattr(connection, "url", None), "scheme", None) or getattr(connection, "scope", {}).get("scheme")
    if str(scheme).lower() in {"https", "wss"}:
        return True
    peer = direct_peer(connection)
    trusted = trusted_proxy_cidrs or []
    if trusted and _address_in_rules(peer, trusted):
        headers = getattr(connection, "headers", {})
        forwarded = headers.get("x-forwarded-proto") if headers else None
        if forwarded:
            return forwarded.split(",", 1)[0].strip().lower() in {"https", "wss"}
    return False


def ip_allowed(connection: Any, allowed: list[str], denied: list[str], trusted_proxy_cidrs: list[str] | None = None) -> bool:
    raw = client_ip(connection, trusted_proxy_cidrs)
    try:
        ip = ipaddress.ip_address(raw)
    except ValueError:
        return not allowed
    for rule in denied:
        try:
            if ip in ipaddress.ip_network(rule, strict=False):
                return False
        except ValueError:
            if raw == rule:
                return False
    if not allowed:
        return True
    for rule in allowed:
        try:
            if ip in ipaddress.ip_network(rule, strict=False):
                return True
        except ValueError:
            if raw == rule:
                return True
    return False


def host_allowed(host_header: str | None, allowed_hosts: list[str]) -> bool:
    if not allowed_hosts or "*" in allowed_hosts:
        return True
    if not host_header:
        return False
    raw = host_header.strip()
    if not raw or any(character in raw for character in "/\\@\r\n\0 \t"):
        return False
    if raw.startswith("["):
        closing = raw.find("]")
        if closing < 0 or (raw[closing + 1 :] and not raw[closing + 1 :].startswith(":")):
            return False
        port = raw[closing + 2 :] if raw[closing + 1 :].startswith(":") else ""
        if port and not port.isdigit():
            return False
        host = raw[1:closing].lower()
    elif raw.count(":") == 1:
        host, port = raw.rsplit(":", 1)
        if port and not port.isdigit():
            return False
        host = host.lower().rstrip(".")
    else:
        host = raw.lower().rstrip(".")
    for pattern in allowed_hosts:
        pattern = pattern.strip().lower().rstrip(".")
        if pattern.startswith("[") and pattern.endswith("]"):
            pattern = pattern[1:-1]
        if pattern == host:
            return True
        if pattern.startswith("*.") and host.endswith(pattern[1:]) and host != pattern[2:]:
            return True
    return False


class RequestBodyLimitMiddleware:
    def __init__(self, app, limit_for_path: Callable[[str], int | None]):
        self.app = app
        self.limit_for_path = limit_for_path

    async def __call__(self, scope: dict[str, Any], receive, send) -> None:
        if scope.get("type") != "http":
            await self.app(scope, receive, send)
            return
        limit = self.limit_for_path(scope.get("path", ""))
        if not limit:
            await self.app(scope, receive, send)
            return
        headers = {k.lower(): v for k, v in scope.get("headers", [])}
        raw_length = headers.get(b"content-length")
        if raw_length is not None:
            try:
                if int(raw_length) > limit:
                    await self._reject(send, limit)
                    return
            except ValueError:
                await self._bad_length(send)
                return
        total = 0
        exceeded = False
        response_started = False

        async def limited_receive():
            nonlocal total, exceeded
            message = await receive()
            if message.get("type") == "http.request":
                total += len(message.get("body", b""))
                if total > limit:
                    exceeded = True
                    raise _BodyTooLarge
            return message

        async def tracking_send(message):
            nonlocal response_started
            if message.get("type") == "http.response.start":
                response_started = True
            await send(message)

        try:
            await self.app(scope, limited_receive, tracking_send)
        except _BodyTooLarge:
            if not response_started:
                await self._reject(send, limit)
            elif exceeded:
                await send({"type": "http.response.body", "body": b"", "more_body": False})

    @staticmethod
    async def _reject(send, limit: int) -> None:
        payload = json.dumps({"detail": f"Request body exceeds max_request_body_bytes={limit}"}).encode("utf-8")
        await send(
            {
                "type": "http.response.start",
                "status": 413,
                "headers": [(b"content-type", b"application/json"), (b"content-length", str(len(payload)).encode())],
            }
        )
        await send({"type": "http.response.body", "body": payload, "more_body": False})

    @staticmethod
    async def _bad_length(send) -> None:
        payload = b'{"detail":"Invalid Content-Length"}'
        await send(
            {
                "type": "http.response.start",
                "status": 400,
                "headers": [(b"content-type", b"application/json"), (b"content-length", str(len(payload)).encode())],
            }
        )
        await send({"type": "http.response.body", "body": payload, "more_body": False})


class _BodyTooLarge(Exception):
    pass
