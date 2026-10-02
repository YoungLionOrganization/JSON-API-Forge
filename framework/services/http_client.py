from __future__ import annotations

import asyncio
import ipaddress
import socket
import time
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from functools import lru_cache

import httpcore
import httpx

_RETRYABLE_STATUS = {408, 425, 429, 500, 502, 503, 504}
_IDEMPOTENT_METHODS = {"GET", "HEAD", "OPTIONS", "PUT", "DELETE"}


@lru_cache(maxsize=1)
def _dns_executor():
    # Threads start only on the first DNS request, after Passenger worker start.
    # A timed-out libc lookup cannot be stopped; isolate it from file/hook work.
    return ThreadPoolExecutor(max_workers=8, thread_name_prefix="forge-dns")


@dataclass
class CircuitState:
    failures: int = 0
    opened_at: float | None = None


class ResponseTooLarge(RuntimeError):
    pass


def blocked_network_address(address: str) -> bool:
    """Return true for addresses that must not be reachable by public egress."""
    try:
        return not ipaddress.ip_address(address).is_global
    except ValueError:
        return True


class _AddressPolicyBackend(httpcore.AsyncNetworkBackend):
    """Resolve once, validate every answer, then connect to the validated IP.

    TLS still receives the original hostname from httpcore, so certificate and
    SNI checks are preserved while DNS-rebinding time-of-check/time-of-use races
    are removed.
    """

    def __init__(self, *, block_private_networks: bool):
        self._block_private_networks = block_private_networks
        self._backend = httpcore.AnyIOBackend()

    async def connect_tcp(
        self,
        host: str,
        port: int,
        timeout: float | None = None,
        local_address: str | None = None,
        socket_options=None,
    ) -> httpcore.AsyncNetworkStream:
        if timeout is None:
            return await self._connect_validated(host, port, None, local_address, socket_options)
        try:
            return await asyncio.wait_for(
                self._connect_validated(host, port, time.monotonic() + timeout, local_address, socket_options), timeout
            )
        except asyncio.TimeoutError as exc:
            raise httpcore.ConnectTimeout("Outbound DNS/connection deadline exceeded") from exc

    async def _connect_validated(self, host, port, deadline, local_address, socket_options):
        try:
            loop = asyncio.get_running_loop()
            infos = await loop.run_in_executor(_dns_executor(), lambda: socket.getaddrinfo(host, port, type=socket.SOCK_STREAM))
        except OSError as exc:
            raise httpcore.ConnectError(f"Cannot resolve outbound host {host!r}") from exc
        addresses: list[str] = []
        for info in infos:
            address = info[4][0]
            if address not in addresses:
                addresses.append(address)
        if not addresses:
            raise httpcore.ConnectError(f"Outbound host {host!r} returned no usable addresses")
        if self._block_private_networks and any(blocked_network_address(address) for address in addresses):
            raise httpcore.ConnectError(f"Outbound host {host!r} resolved to a private or non-routable address")
        last_error: Exception | None = None
        for address in addresses:
            try:
                return await self._backend.connect_tcp(
                    address,
                    port,
                    timeout=None if deadline is None else max(0.0, deadline - time.monotonic()),
                    local_address=local_address,
                    socket_options=socket_options,
                )
            except (httpcore.ConnectError, httpcore.ConnectTimeout) as exc:
                last_error = exc
        if last_error is None:
            raise httpcore.ConnectError(f"Outbound host {host!r} could not be connected")
        raise last_error

    async def connect_unix_socket(self, *args, **kwargs) -> httpcore.AsyncNetworkStream:
        raise httpcore.UnsupportedProtocol("Unix sockets are disabled for outbound HTTP")

    async def sleep(self, seconds: float) -> None:
        await self._backend.sleep(seconds)


class ResilientHTTPClient:
    def __init__(
        self,
        *,
        timeout: float = 10.0,
        max_connections: int = 200,
        max_keepalive: int = 50,
        failure_threshold: int = 5,
        reset_seconds: float = 30.0,
        trust_env: bool = False,
        block_private_networks: bool = True,
    ):
        limits = httpx.Limits(max_connections=max_connections, max_keepalive_connections=max_keepalive)
        transport = httpx.AsyncHTTPTransport(trust_env=trust_env, limits=limits)
        # HTTPX intentionally delegates connection creation to httpcore. Replacing
        # only that backend keeps HTTPX's public request/response behavior while
        # ensuring the address that was validated is the address that is dialed.
        pool = getattr(transport, "_pool", None)
        if pool is None or not hasattr(pool, "_network_backend"):
            raise RuntimeError("Installed HTTPX/httpcore versions do not expose the required egress policy hook")
        pool._network_backend = _AddressPolicyBackend(block_private_networks=block_private_networks)
        self.client = httpx.AsyncClient(
            timeout=httpx.Timeout(timeout),
            limits=limits,
            follow_redirects=False,
            trust_env=trust_env,
            transport=transport,
        )
        self.failure_threshold = max(1, int(failure_threshold))
        self.reset_seconds = max(0.1, float(reset_seconds))
        self.states: dict[str, CircuitState] = {}

    @staticmethod
    def _retry_delay(attempt: int, response: httpx.Response | None = None) -> float:
        if response is not None:
            raw = response.headers.get("Retry-After")
            if raw:
                try:
                    return min(max(float(raw), 0.0), 10.0)
                except ValueError:
                    pass
        return min(0.25 * (2**attempt), 2.0)

    async def _send_once(self, method: str, url: str, *, max_response_bytes: int | None = None, **kwargs) -> httpx.Response:
        request = self.client.build_request(method, url, **kwargs)
        response = await self.client.send(request, stream=True)
        try:
            if max_response_bytes is not None:
                raw_length = response.headers.get("content-length")
                if raw_length:
                    try:
                        if int(raw_length) > max_response_bytes:
                            raise ResponseTooLarge(f"Upstream response exceeds max_response_bytes={max_response_bytes}")
                    except ValueError:
                        pass
            chunks: list[bytes] = []
            total = 0
            async for chunk in response.aiter_bytes():
                total += len(chunk)
                if max_response_bytes is not None and total > max_response_bytes:
                    raise ResponseTooLarge(f"Upstream response exceeds max_response_bytes={max_response_bytes}")
                chunks.append(chunk)
            headers = [
                (name, value)
                for name, value in response.headers.multi_items()
                if name.lower() not in {"content-encoding", "content-length"}
            ]
            return httpx.Response(
                status_code=response.status_code,
                headers=headers,
                content=b"".join(chunks),
                request=request,
                extensions=response.extensions,
            )
        finally:
            await response.aclose()

    async def request(
        self,
        method: str,
        url: str,
        *,
        retries: int = 2,
        retry_non_idempotent: bool = False,
        max_response_bytes: int | None = None,
        total_timeout: float | None = None,
        **kwargs,
    ) -> httpx.Response:
        if total_timeout is not None:
            try:
                return await asyncio.wait_for(
                    self.request(
                        method,
                        url,
                        retries=retries,
                        retry_non_idempotent=retry_non_idempotent,
                        max_response_bytes=max_response_bytes,
                        **kwargs,
                    ),
                    total_timeout,
                )
            except asyncio.TimeoutError as exc:
                raise httpx.TimeoutException("Outbound request deadline exceeded", request=httpx.Request(method, url)) from exc
        method = method.upper()
        host = httpx.URL(url).host or "unknown"
        state = self.states.setdefault(host, CircuitState())
        now = time.monotonic()
        if state.opened_at is not None and now - state.opened_at < self.reset_seconds:
            raise RuntimeError(f"Circuit open for {host}")
        if state.opened_at is not None:
            state.opened_at = None
            state.failures = 0
        configured_retries = max(0, int(retries))
        effective_retries = configured_retries if method in _IDEMPOTENT_METHODS or retry_non_idempotent else 0
        last: Exception | None = None
        for attempt in range(effective_retries + 1):
            response: httpx.Response | None = None
            try:
                response = await self._send_once(method, url, max_response_bytes=max_response_bytes, **kwargs)
                if response.status_code not in _RETRYABLE_STATUS:
                    response.raise_for_status()
                    state.failures = 0
                    return response
                response.raise_for_status()
                state.failures = 0
                return response
            except ResponseTooLarge:
                raise
            except httpx.HTTPStatusError as exc:
                if exc.response.status_code not in _RETRYABLE_STATUS:
                    raise
                last = exc
            except (httpx.TransportError, httpx.TimeoutException) as exc:
                last = exc
            state.failures += 1
            if state.failures >= self.failure_threshold:
                state.opened_at = time.monotonic()
            if attempt < effective_retries:
                await asyncio.sleep(self._retry_delay(attempt, response))
        if last is None:
            raise RuntimeError("HTTP retry loop ended without a response or transport error")
        raise last

    async def close(self):
        await self.client.aclose()
