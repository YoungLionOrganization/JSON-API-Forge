from __future__ import annotations

import asyncio
import hashlib
import threading
import time
import uuid
from collections.abc import Awaitable, Callable, Iterable, Mapping, Sequence
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass
from enum import Enum
from itertools import islice
from typing import Any, Generic, TypeVar

from .client import AsyncForgeClient, ForgeClient, _route, _segment
from .errors import ForgeClusterUnavailable, ForgeHTTPError, ForgeTransportError
from .models import ForgeResponse

T = TypeVar("T")


class RoutingStrategy(str, Enum):
    # Preserve StrEnum wire/format behavior on Python 3.10 as well.
    __str__ = str.__str__
    __format__ = str.__format__

    ROUND_ROBIN = "round_robin"
    RENDEZVOUS = "rendezvous"


@dataclass(frozen=True, slots=True)
class ForgeEndpoint:
    name: str
    base_url: str
    api_key: str | None = None
    weight: int = 1
    allow_insecure_http: bool = False

    def __post_init__(self) -> None:
        if not self.name or len(self.name) > 64 or any(character in self.name for character in "\r\n\0"):
            raise ValueError("endpoint name is invalid")
        if not 1 <= self.weight <= 100:
            raise ValueError("endpoint weight must be between 1 and 100")


@dataclass(frozen=True, slots=True)
class CircuitBreakerPolicy:
    failure_threshold: int = 3
    recovery_seconds: float = 15.0

    def __post_init__(self) -> None:
        if not 1 <= self.failure_threshold <= 100:
            raise ValueError("failure_threshold must be between 1 and 100")
        if not 0 <= self.recovery_seconds <= 3600:
            raise ValueError("recovery_seconds must be between 0 and 3600")


@dataclass(slots=True)
class _EndpointState:
    failures: int = 0
    opened_at: float | None = None


@dataclass(frozen=True, slots=True)
class BulkResult(Generic[T]):
    index: int
    value: T | None = None
    error: Exception | None = None

    @property
    def succeeded(self) -> bool:
        return self.error is None


def _retryable(error: Exception) -> bool:
    return isinstance(error, ForgeTransportError) or (isinstance(error, ForgeHTTPError) and error.status_code >= 500)


def _failover_permitted(method: str, options: Mapping[str, Any]) -> bool:
    if options.get("files") is not None:
        # Multipart bodies may contain consumed streams and cannot be safely replayed.
        return False
    return method.upper() in {"GET", "HEAD", "OPTIONS", "PUT", "DELETE"} or bool(options.get("idempotency_key"))


def _bounded_values(values: Iterable[T], maximum: int) -> list[T]:
    if not 1 <= maximum <= 100_000:
        raise ValueError("max_items must be between 1 and 100000")
    result = list(islice(values, maximum + 1))
    if len(result) > maximum:
        raise ValueError(f"bulk input exceeds max_items={maximum}")
    return result


class ForgeCluster:
    """Failover/routing facade for horizontally deployed Forge services."""

    def __init__(
        self,
        endpoints: Sequence[ForgeEndpoint],
        *,
        strategy: RoutingStrategy = RoutingStrategy.ROUND_ROBIN,
        circuit_breaker: CircuitBreakerPolicy | None = None,
        client_factory: Callable[[ForgeEndpoint], ForgeClient] | None = None,
    ):
        if not endpoints:
            raise ValueError("at least one endpoint is required")
        names = [endpoint.name for endpoint in endpoints]
        if len(names) != len(set(names)):
            raise ValueError("endpoint names must be unique")
        self.endpoints = tuple(endpoints)
        self.strategy = RoutingStrategy(strategy)
        self.circuit_breaker = circuit_breaker or CircuitBreakerPolicy()
        self._clients = {
            endpoint.name: (
                client_factory(endpoint)
                if client_factory
                else ForgeClient(
                    endpoint.base_url,
                    api_key=endpoint.api_key,
                    allow_insecure_http=endpoint.allow_insecure_http,
                )
            )
            for endpoint in self.endpoints
        }
        self._states = {endpoint.name: _EndpointState() for endpoint in self.endpoints}
        self._cursor = 0
        self._lock = threading.Lock()

    def _available(self) -> list[ForgeEndpoint]:
        now = time.monotonic()
        result = []
        for endpoint in self.endpoints:
            state = self._states[endpoint.name]
            if state.opened_at is None or now - state.opened_at >= self.circuit_breaker.recovery_seconds:
                result.append(endpoint)
        return result

    def _ordered(self, routing_key: str | None) -> list[ForgeEndpoint]:
        with self._lock:
            candidates = self._available()
            if not candidates:
                oldest = min(self.endpoints, key=lambda endpoint: self._states[endpoint.name].opened_at or 0)
                candidates = [oldest]
            offset = self._cursor % len(candidates)
            self._cursor += 1
        if self.strategy == RoutingStrategy.RENDEZVOUS and routing_key is not None:

            def score(endpoint: ForgeEndpoint) -> int:
                digest = hashlib.blake2b(f"{routing_key}\0{endpoint.name}".encode(), digest_size=16).digest()
                return int.from_bytes(digest, "big") * endpoint.weight

            return sorted(candidates, key=score, reverse=True)
        return candidates[offset:] + candidates[:offset]

    def _success(self, endpoint: ForgeEndpoint) -> None:
        with self._lock:
            state = self._states[endpoint.name]
            state.failures = 0
            state.opened_at = None

    def _failure(self, endpoint: ForgeEndpoint) -> None:
        with self._lock:
            state = self._states[endpoint.name]
            state.failures += 1
            if state.failures >= self.circuit_breaker.failure_threshold:
                state.opened_at = time.monotonic()

    def request(self, method: str, path: str, *, routing_key: str | None = None, **kwargs: Any) -> ForgeResponse[Any]:
        options = dict(kwargs)
        options.setdefault("request_id", str(uuid.uuid4()))
        failures: list[str] = []
        for endpoint in self._ordered(routing_key):
            try:
                response = self._clients[endpoint.name].request(method, path, **options)
            except Exception as exc:
                if not _retryable(exc):
                    raise
                self._failure(endpoint)
                if not _failover_permitted(method, options):
                    raise
                failures.append(f"{endpoint.name}: {exc}")
                continue
            self._success(endpoint)
            return response
        detail = "; ".join(failures) if failures else "all endpoint circuits are open"
        raise ForgeClusterUnavailable(f"No Forge cluster endpoint completed the request: {detail}")

    def call_operation(
        self,
        project: str,
        operation: str,
        payload: Mapping[str, Any],
        *,
        routing_key: str | None = None,
        idempotency_key: str | None = None,
    ) -> ForgeResponse[Any]:
        return self.request(
            "POST",
            f"api/{_segment(project)}/v1/rpc/{_segment(operation)}",
            routing_key=routing_key,
            json_body=dict(payload),
            idempotency_key=idempotency_key,
        )

    def bulk_create(
        self,
        project: str,
        resource: str,
        payloads: Iterable[Mapping[str, Any]],
        *,
        max_workers: int = 8,
        max_items: int = 10_000,
        idempotency_key: Callable[[int, Mapping[str, Any]], str | None] | None = None,
        routing_key: Callable[[int, Mapping[str, Any]], str | None] | None = None,
    ) -> list[BulkResult[ForgeResponse[Any]]]:
        values = [dict(payload) for payload in _bounded_values(payloads, max_items)]
        if not 1 <= max_workers <= 64:
            raise ValueError("max_workers must be between 1 and 64")
        results: list[BulkResult[ForgeResponse[Any]] | None] = [None] * len(values)

        def submit(index: int) -> ForgeResponse[Any]:
            payload = values[index]
            return self.request(
                "POST",
                f"api/{_segment(project)}/v1/{_route(resource)}",
                routing_key=routing_key(index, payload) if routing_key else None,
                json_body=payload,
                idempotency_key=idempotency_key(index, payload) if idempotency_key else None,
            )

        with ThreadPoolExecutor(max_workers=max_workers, thread_name_prefix="forge-bulk") as executor:
            futures = {executor.submit(submit, index): index for index in range(len(values))}
            for future in as_completed(futures):
                index = futures[future]
                try:
                    results[index] = BulkResult(index=index, value=future.result())
                except Exception as exc:
                    results[index] = BulkResult(index=index, error=exc)
        return [result for result in results if result is not None]

    def health_all(self) -> dict[str, ForgeResponse[Any] | Exception]:
        result: dict[str, ForgeResponse[Any] | Exception] = {}
        for endpoint in self.endpoints:
            try:
                result[endpoint.name] = self._clients[endpoint.name].health()
            except Exception as exc:
                result[endpoint.name] = exc
        return result

    def close(self) -> None:
        for client in self._clients.values():
            client.close()

    def __enter__(self) -> ForgeCluster:
        return self

    def __exit__(self, *_args: Any) -> None:
        self.close()


class AsyncForgeCluster:
    """Async cluster facade with bounded bulk concurrency."""

    def __init__(
        self,
        endpoints: Sequence[ForgeEndpoint],
        *,
        strategy: RoutingStrategy = RoutingStrategy.ROUND_ROBIN,
        circuit_breaker: CircuitBreakerPolicy | None = None,
        client_factory: Callable[[ForgeEndpoint], AsyncForgeClient] | None = None,
    ):
        if not endpoints:
            raise ValueError("at least one endpoint is required")
        names = [endpoint.name for endpoint in endpoints]
        if len(names) != len(set(names)):
            raise ValueError("endpoint names must be unique")
        self.endpoints = tuple(endpoints)
        self.strategy = RoutingStrategy(strategy)
        self.circuit_breaker = circuit_breaker or CircuitBreakerPolicy()
        self._clients = {
            endpoint.name: (
                client_factory(endpoint)
                if client_factory
                else AsyncForgeClient(
                    endpoint.base_url,
                    api_key=endpoint.api_key,
                    allow_insecure_http=endpoint.allow_insecure_http,
                )
            )
            for endpoint in self.endpoints
        }
        self._states = {endpoint.name: _EndpointState() for endpoint in self.endpoints}
        self._cursor = 0
        self._lock = asyncio.Lock()

    async def _ordered(self, routing_key: str | None) -> list[ForgeEndpoint]:
        now = time.monotonic()
        candidates = [
            endpoint
            for endpoint in self.endpoints
            if self._states[endpoint.name].opened_at is None
            or now - (self._states[endpoint.name].opened_at or 0) >= self.circuit_breaker.recovery_seconds
        ]
        if not candidates:
            candidates = [min(self.endpoints, key=lambda endpoint: self._states[endpoint.name].opened_at or 0)]
        if self.strategy == RoutingStrategy.RENDEZVOUS and routing_key is not None:
            return sorted(
                candidates,
                key=lambda endpoint: (
                    int.from_bytes(hashlib.blake2b(f"{routing_key}\0{endpoint.name}".encode(), digest_size=16).digest(), "big")
                    * endpoint.weight
                ),
                reverse=True,
            )
        async with self._lock:
            offset = self._cursor % len(candidates)
            self._cursor += 1
        return candidates[offset:] + candidates[:offset]

    def _success(self, endpoint: ForgeEndpoint) -> None:
        self._states[endpoint.name] = _EndpointState()

    def _failure(self, endpoint: ForgeEndpoint) -> None:
        state = self._states[endpoint.name]
        state.failures += 1
        if state.failures >= self.circuit_breaker.failure_threshold:
            state.opened_at = time.monotonic()

    async def request(self, method: str, path: str, *, routing_key: str | None = None, **kwargs: Any) -> ForgeResponse[Any]:
        options = dict(kwargs)
        options.setdefault("request_id", str(uuid.uuid4()))
        failures: list[str] = []
        for endpoint in await self._ordered(routing_key):
            try:
                response = await self._clients[endpoint.name].request(method, path, **options)
            except Exception as exc:
                if not _retryable(exc):
                    raise
                self._failure(endpoint)
                if not _failover_permitted(method, options):
                    raise
                failures.append(f"{endpoint.name}: {exc}")
                continue
            self._success(endpoint)
            return response
        detail = "; ".join(failures) if failures else "all endpoint circuits are open"
        raise ForgeClusterUnavailable(f"No Forge cluster endpoint completed the request: {detail}")

    async def call_operation(
        self,
        project: str,
        operation: str,
        payload: Mapping[str, Any],
        *,
        routing_key: str | None = None,
        idempotency_key: str | None = None,
    ) -> ForgeResponse[Any]:
        return await self.request(
            "POST",
            f"api/{_segment(project)}/v1/rpc/{_segment(operation)}",
            routing_key=routing_key,
            json_body=dict(payload),
            idempotency_key=idempotency_key,
        )

    async def bulk(
        self,
        operations: Iterable[Callable[[], Awaitable[T]]],
        *,
        concurrency: int = 16,
        max_items: int = 10_000,
    ) -> list[BulkResult[T]]:
        if not 1 <= concurrency <= 256:
            raise ValueError("concurrency must be between 1 and 256")
        bounded_operations = _bounded_values(operations, max_items)
        semaphore = asyncio.Semaphore(concurrency)

        async def run(index: int, operation: Callable[[], Awaitable[T]]) -> BulkResult[T]:
            async with semaphore:
                try:
                    return BulkResult(index=index, value=await operation())
                except Exception as exc:
                    return BulkResult(index=index, error=exc)

        tasks = [asyncio.create_task(run(index, operation)) for index, operation in enumerate(bounded_operations)]
        try:
            return list(await asyncio.gather(*tasks))
        except BaseException:
            # A cancelled item makes gather return before its siblings finish.
            # Own and drain them so mutations cannot continue after bulk exits.
            for task in tasks:
                task.cancel()
            await asyncio.gather(*tasks, return_exceptions=True)
            raise

    async def health_all(self) -> dict[str, ForgeResponse[Any] | Exception]:
        async def health(endpoint: ForgeEndpoint):
            try:
                return endpoint.name, await self._clients[endpoint.name].health()
            except Exception as exc:
                return endpoint.name, exc

        return dict(await asyncio.gather(*(health(endpoint) for endpoint in self.endpoints)))

    async def aclose(self) -> None:
        await asyncio.gather(*(client.aclose() for client in self._clients.values()))

    async def __aenter__(self) -> AsyncForgeCluster:
        return self

    async def __aexit__(self, *_args: Any) -> None:
        await self.aclose()
