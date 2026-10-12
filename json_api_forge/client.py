from __future__ import annotations

import asyncio
import ipaddress
import json
import math
import re
import time
import unicodedata
import uuid
from collections.abc import AsyncIterator, Callable, Iterator, Mapping, Sequence
from contextlib import asynccontextmanager
from typing import Any, Literal
from urllib.parse import quote, unquote, urlsplit

import httpx

from ._io import run_io
from .errors import ForgeHTTPError, ForgeResponseTooLarge, ForgeTransportError, safe_error_text
from .models import ForgeResponse, JsonObject, RequestAttempt
from .options import RetryPolicy
from .pagination import Pagination, batch_payload

_JSON_ACCEPT = "application/json"
_HEADER_NAME = re.compile(r"[!#$%&'*+.^_`|~0-9A-Za-z-]+\Z")
_REQUEST_ID = re.compile(r"[A-Za-z0-9._:-]{1,64}\Z")


def _controls(value: str) -> bool:
    return any(unicodedata.category(char) in {"Cc", "Cf"} for char in value)


def _path_parts(path: str) -> list[str]:
    if not path.strip("/"):
        return []
    parts = path.strip("/").split("/")
    for index, part in enumerate(parts):
        for _ in range(8):
            if not part or part in {".", ".."} or any(char in part for char in "/\\?#:") or _controls(part):
                raise ValueError("URL path contains traversal or an unsafe segment")
            if "%" not in part:
                break
            if re.search(r"%(?![0-9a-fA-F]{2})", part):
                raise ValueError("URL path has malformed percent encoding")
            part = unquote(part, errors="strict")
        else:
            raise ValueError("URL path is too deeply encoded")
        parts[index] = part
    return parts


def _notify(observer: Callable[[RequestAttempt], None] | None, event: RequestAttempt) -> None:
    if observer is None:
        return
    try:
        observer(event)
    except Exception:
        # Observability must not change application request semantics.
        return


def _base_url(value: str, *, allow_insecure_http: bool) -> str:
    if not isinstance(value, str) or len(value) > 8192 or value != value.strip() or _controls(value):
        raise ValueError("base_url is too long or contains whitespace/control characters")
    parsed = urlsplit(value)
    host = parsed.hostname or ""
    if not host or parsed.port == 0:
        raise ValueError("base_url must contain a valid host and port")
    try:
        loopback = host.lower() == "localhost" or ipaddress.ip_address(host).is_loopback
    except ValueError:
        loopback = host.lower() == "localhost"
    if parsed.scheme not in {"http", "https"} or not parsed.netloc or parsed.username or parsed.password or "\\" in value or "\0" in value:
        raise ValueError("base_url must be an absolute HTTP(S) URL without embedded credentials")
    if parsed.scheme == "http" and not (allow_insecure_http and loopback):
        raise ValueError("plain HTTP requires allow_insecure_http=True and a loopback host")
    if parsed.query or parsed.fragment:
        raise ValueError("base_url must not contain a query or fragment")
    path = "/".join(quote(part, safe="-._~") for part in _path_parts(parsed.path))
    return f"{parsed.scheme}://{parsed.netloc}/" + (path + "/" if path else "")


def _relative_path(value: str) -> str:
    if not isinstance(value, str) or len(value) > 8192 or value != value.strip() or _controls(value):
        raise ValueError("request path is too long or contains whitespace/control characters")
    parsed = urlsplit(value)
    if parsed.scheme or parsed.netloc or value.startswith("//") or "\\" in value or "\0" in value:
        raise ValueError("request path must be relative to the configured Forge server")
    if parsed.fragment:
        raise ValueError("request path must not contain a fragment")
    normalized = "/".join(quote(part, safe="-._~") for part in _path_parts(parsed.path))
    return normalized + (f"?{parsed.query}" if parsed.query else "")


def _segment(value: str) -> str:
    if (
        not isinstance(value, str)
        or not value
        or len(value) > 2048
        or value in {".", ".."}
        or _controls(value)
        or any(ch in value for ch in "/\\")
    ):
        raise ValueError("path segment is empty or unsafe")
    return quote(value, safe="-._~")


def _route(value: str) -> str:
    parts = value.split("/")
    if not parts or any(not part for part in parts):
        raise ValueError("route must contain non-empty path segments")
    return "/".join(_segment(part) for part in parts)


def _headers(
    api_key: str | None,
    request_id: str | None,
    idempotency_key: str | None,
    extra: Mapping[str, str] | None,
) -> dict[str, str]:
    if request_id is not None and (not isinstance(request_id, str) or not _REQUEST_ID.fullmatch(request_id)):
        raise ValueError("request_id must be a safe identifier of 1 to 64 characters")
    values = {"Accept": _JSON_ACCEPT, "Accept-Encoding": "identity", "X-Request-ID": request_id or str(uuid.uuid4())}
    if api_key:
        values["X-API-Key"] = api_key
    if idempotency_key:
        if not isinstance(idempotency_key, str) or len(idempotency_key) > 256 or _controls(idempotency_key):
            raise ValueError("idempotency_key is too long or contains control characters")
        values["Idempotency-Key"] = idempotency_key
    for name, value in (extra or {}).items():
        if (
            not isinstance(name, str)
            or not _HEADER_NAME.fullmatch(name)
            or not isinstance(value, str)
            or len(value) > 8192
            or _controls(value)
        ):
            raise ValueError("header names and values must be valid bounded HTTP fields")
        if name.lower() in {
            "host",
            "connection",
            "content-length",
            "transfer-encoding",
            "accept-encoding",
            "x-request-id",
            "x-api-key",
            "idempotency-key",
        }:
            raise ValueError("extra headers cannot replace transport, identity, or credential headers")
        values[name] = value
    return values


def _decode_response(response: httpx.Response, content: bytes, *, expect_json: bool) -> ForgeResponse[Any]:
    detail: Any
    valid_json = True
    try:
        decoded = json.loads(content) if content else None
    except (UnicodeDecodeError, json.JSONDecodeError, RecursionError):
        decoded = None
        valid_json = False
    if response.is_redirect:
        raise ForgeHTTPError(
            response.status_code,
            "Redirects are disabled to keep credentials on the configured origin",
            response.headers.get("X-Request-ID"),
            response.headers.get("Retry-After"),
        )
    if response.is_error:
        if isinstance(decoded, dict) and "detail" in decoded:
            detail = decoded["detail"]
        elif decoded is not None:
            detail = decoded
        else:
            detail = content.decode("utf-8", errors="replace")[:2048] or response.reason_phrase
        raise ForgeHTTPError(
            response.status_code,
            detail,
            response.headers.get("X-Request-ID"),
            response.headers.get("Retry-After"),
        )
    if expect_json and not valid_json and content:
        raise ForgeHTTPError(response.status_code, "Server returned non-JSON content", response.headers.get("X-Request-ID"))
    data = decoded if expect_json else content
    return ForgeResponse(
        data=data,
        status_code=response.status_code,
        request_id=response.headers.get("X-Request-ID"),
        idempotent_replay=response.headers.get("X-Forge-Idempotent-Replay", "").lower() == "true",
        cache_status=response.headers.get("X-Forge-Cache"),
    )


def _require_identity_encoding(response: httpx.Response) -> None:
    # httpx decodes before iter_bytes yields; checking decoded chunks alone
    # cannot bound a malicious compressed stream's intermediate allocation.
    if response.headers.get("Content-Encoding", "identity").strip().lower() not in {"", "identity"}:
        raise ForgeHTTPError(
            response.status_code, "Server ignored the required identity content encoding", response.headers.get("X-Request-ID")
        )


def _bounded_content(response: httpx.Response, max_response_bytes: int) -> bytes:
    _require_identity_encoding(response)
    raw_length = response.headers.get("Content-Length")
    if raw_length:
        try:
            if int(raw_length) > max_response_bytes:
                raise ForgeResponseTooLarge(f"Response exceeds max_response_bytes={max_response_bytes}")
        except ValueError:
            pass
    chunks = bytearray()
    total = 0
    for chunk in response.iter_bytes():
        total += len(chunk)
        if total > max_response_bytes:
            raise ForgeResponseTooLarge(f"Response exceeds max_response_bytes={max_response_bytes}")
        chunks.extend(chunk)
    return bytes(chunks)


async def _bounded_content_async(response: httpx.Response, max_response_bytes: int) -> bytes:
    _require_identity_encoding(response)
    raw_length = response.headers.get("Content-Length")
    if raw_length:
        try:
            if int(raw_length) > max_response_bytes:
                raise ForgeResponseTooLarge(f"Response exceeds max_response_bytes={max_response_bytes}")
        except ValueError:
            pass
    chunks = bytearray()
    total = 0
    async for chunk in response.aiter_bytes():
        total += len(chunk)
        if total > max_response_bytes:
            raise ForgeResponseTooLarge(f"Response exceeds max_response_bytes={max_response_bytes}")
        chunks.extend(chunk)
    return bytes(chunks)


class _AsyncFileStream(httpx.AsyncByteStream):
    """Keep httpx multipart's synchronous file reads off the event loop."""

    def __init__(self, stream: httpx.SyncByteStream):
        self._chunks = iter(stream)

    async def __aiter__(self) -> AsyncIterator[bytes]:
        while True:
            chunk = await run_io(next, self._chunks, None)
            if chunk is None:
                break
            yield chunk


@asynccontextmanager
async def _stream_async(client: httpx.AsyncClient, method: str, path: str, **kwargs: Any) -> AsyncIterator[httpx.Response]:
    if kwargs.get("files") is None:
        async with client.stream(method, path, **kwargs) as response:
            yield response
        return
    # Request assembly also seeks/fstats files to compute Content-Length.
    request = await run_io(client.build_request, method, path, **kwargs)
    request.stream = _AsyncFileStream(request.stream)
    response = await client.send(request, stream=True)
    try:
        yield response
    finally:
        await response.aclose()


class ForgeClient:
    """Synchronous, bounded client for one JSON API Forge server."""

    def __init__(
        self,
        base_url: str,
        *,
        api_key: str | None = None,
        timeout: float = 10.0,
        max_response_bytes: int = 8 * 1024 * 1024,
        allow_insecure_http: bool = False,
        transport: httpx.BaseTransport | None = None,
        retry_policy: RetryPolicy | None = None,
        observer: Callable[[RequestAttempt], None] | None = None,
    ):
        if api_key is not None and (
            not isinstance(api_key, str) or not api_key or len(api_key) > 4096 or _controls(api_key) or any(ch.isspace() for ch in api_key)
        ):
            raise ValueError("api_key must be non-empty, at most 4096 characters, and contain no whitespace or controls")
        if not math.isfinite(timeout) or not 0 < timeout <= 900:
            raise ValueError("timeout must be finite and between 0 and 900 seconds")
        if int(max_response_bytes) < 1024:
            raise ValueError("max_response_bytes must be at least 1024")
        self.api_key = api_key
        self.max_response_bytes = int(max_response_bytes)
        self.retry_policy = retry_policy
        self.observer = observer
        self._client = httpx.Client(
            base_url=_base_url(base_url, allow_insecure_http=allow_insecure_http),
            timeout=timeout,
            follow_redirects=False,
            trust_env=False,
            transport=transport,
        )

    def request(
        self,
        method: str,
        path: str,
        *,
        json_body: Any = None,
        files: Mapping[str, Any] | None = None,
        params: Mapping[str, Any] | None = None,
        headers: Mapping[str, str] | None = None,
        request_id: str | None = None,
        idempotency_key: str | None = None,
        expect_json: bool = True,
        max_response_bytes: int | None = None,
    ) -> ForgeResponse[Any]:
        normalized_method = method.upper()
        if not _HEADER_NAME.fullmatch(normalized_method):
            raise ValueError("method must be an HTTP token")
        normalized_path = _relative_path(path)
        observed_path = normalized_path.split("?", 1)[0]
        if json_body is not None and files is not None:
            raise ValueError("json_body and files cannot be sent together")
        response_limit = self.max_response_bytes if max_response_bytes is None else int(max_response_bytes)
        if response_limit < 1024:
            raise ValueError("max_response_bytes must be at least 1024")
        effective_request_id = request_id or str(uuid.uuid4())
        attempt = 0
        while True:
            attempt += 1
            started = time.monotonic()
            try:
                self._client.cookies.clear()
                try:
                    with self._client.stream(
                        normalized_method,
                        normalized_path,
                        json=json_body,
                        files=files,
                        params=params,
                        headers=_headers(self.api_key, effective_request_id, idempotency_key, headers),
                    ) as response:
                        content = _bounded_content(response, response_limit)
                finally:
                    self._client.cookies.clear()
                result = _decode_response(response, content, expect_json=expect_json)
            except ForgeResponseTooLarge as exc:
                _notify(
                    self.observer,
                    RequestAttempt(normalized_method, observed_path, attempt, time.monotonic() - started, None, None, str(exc)),
                )
                raise
            except ForgeHTTPError as exc:
                _notify(
                    self.observer,
                    RequestAttempt(
                        normalized_method,
                        observed_path,
                        attempt,
                        time.monotonic() - started,
                        exc.status_code,
                        exc.request_id,
                        str(exc),
                    ),
                )
                policy = self.retry_policy
                if (
                    policy is None
                    or files is not None
                    or attempt >= policy.max_attempts
                    or exc.status_code not in policy.retry_statuses
                    or not policy.permits(normalized_method, idempotency_key=idempotency_key)
                ):
                    raise
                time.sleep(policy.delay(attempt, retry_after=exc.retry_after))
                continue
            except httpx.HTTPError as exc:
                wrapped = ForgeTransportError(safe_error_text(exc))
                _notify(
                    self.observer,
                    RequestAttempt(normalized_method, observed_path, attempt, time.monotonic() - started, None, None, str(wrapped)),
                )
                policy = self.retry_policy
                if (
                    policy is None
                    or files is not None
                    or attempt >= policy.max_attempts
                    or not policy.permits(normalized_method, idempotency_key=idempotency_key)
                ):
                    raise wrapped from exc
                time.sleep(policy.delay(attempt))
                continue
            _notify(
                self.observer,
                RequestAttempt(
                    normalized_method,
                    observed_path,
                    attempt,
                    time.monotonic() - started,
                    result.status_code,
                    result.request_id,
                    None,
                ),
            )
            return result

    def health(self) -> ForgeResponse[JsonObject]:
        return self.request("GET", "health")

    def metadata(self, project: str) -> ForgeResponse[JsonObject]:
        return self.request("GET", f"api/{_segment(project)}/v1/meta")

    def list_items(self, project: str, resource: str, *, params: Mapping[str, Any] | None = None) -> ForgeResponse[JsonObject]:
        return self.request("GET", f"api/{_segment(project)}/v1/{_route(resource)}", params=params)

    def get_item(self, project: str, resource: str, item_id: str) -> ForgeResponse[JsonObject]:
        return self.request("GET", f"api/{_segment(project)}/v1/{_route(resource)}/{_segment(item_id)}")

    def create_item(self, project: str, resource: str, payload: Mapping[str, Any], *, idempotency_key: str | None = None):
        return self.request(
            "POST",
            f"api/{_segment(project)}/v1/{_route(resource)}",
            json_body=dict(payload),
            idempotency_key=idempotency_key,
        )

    def update_item(self, project: str, resource: str, item_id: str, payload: Mapping[str, Any], *, replace: bool = False):
        return self.request(
            "PUT" if replace else "PATCH",
            f"api/{_segment(project)}/v1/{_route(resource)}/{_segment(item_id)}",
            json_body=dict(payload),
        )

    def delete_item(self, project: str, resource: str, item_id: str):
        return self.request("DELETE", f"api/{_segment(project)}/v1/{_route(resource)}/{_segment(item_id)}")

    def call_operation(self, project: str, operation: str, payload: Mapping[str, Any], *, idempotency_key: str | None = None):
        return self.request(
            "POST",
            f"api/{_segment(project)}/v1/rpc/{_segment(operation)}",
            json_body=dict(payload),
            idempotency_key=idempotency_key,
        )

    def iter_items(
        self,
        project: str,
        resource: str,
        *,
        params: Mapping[str, Any] | None = None,
        page_size: int = 100,
        max_items: int = 10_000,
        pagination: Literal["offset", "cursor"] = "offset",
    ) -> Iterator[JsonObject]:
        pages = Pagination(params, page_size, max_items, pagination)
        while True:
            items, done = pages.consume(self.list_items(project, resource, params=pages.query()))
            yield from items
            if done:
                return

    def batch_create_items(self, project: str, resource: str, payloads: Sequence[Mapping[str, Any]]) -> ForgeResponse[JsonObject]:
        """Submit one atomic batch; POST is never automatically retried."""
        return self.request("POST", f"api/{_segment(project)}/v1/{_route(resource)}/_batch", json_body=batch_payload(payloads))

    def close(self) -> None:
        self._client.close()

    def __enter__(self) -> ForgeClient:
        return self

    def __exit__(self, *_args) -> None:
        self.close()


class AsyncForgeClient:
    """Asynchronous, bounded client for one JSON API Forge server."""

    def __init__(
        self,
        base_url: str,
        *,
        api_key: str | None = None,
        timeout: float = 10.0,
        max_response_bytes: int = 8 * 1024 * 1024,
        allow_insecure_http: bool = False,
        transport: httpx.AsyncBaseTransport | None = None,
        retry_policy: RetryPolicy | None = None,
        observer: Callable[[RequestAttempt], None] | None = None,
    ):
        if api_key is not None and (
            not isinstance(api_key, str) or not api_key or len(api_key) > 4096 or _controls(api_key) or any(ch.isspace() for ch in api_key)
        ):
            raise ValueError("api_key must be non-empty, at most 4096 characters, and contain no whitespace or controls")
        if not math.isfinite(timeout) or not 0 < timeout <= 900:
            raise ValueError("timeout must be finite and between 0 and 900 seconds")
        if int(max_response_bytes) < 1024:
            raise ValueError("max_response_bytes must be at least 1024")
        self.api_key = api_key
        self.max_response_bytes = int(max_response_bytes)
        self.retry_policy = retry_policy
        self.observer = observer
        self._client = httpx.AsyncClient(
            base_url=_base_url(base_url, allow_insecure_http=allow_insecure_http),
            timeout=timeout,
            follow_redirects=False,
            trust_env=False,
            transport=transport,
        )

    async def request(
        self,
        method: str,
        path: str,
        *,
        json_body: Any = None,
        files: Mapping[str, Any] | None = None,
        params: Mapping[str, Any] | None = None,
        headers: Mapping[str, str] | None = None,
        request_id: str | None = None,
        idempotency_key: str | None = None,
        expect_json: bool = True,
        max_response_bytes: int | None = None,
    ) -> ForgeResponse[Any]:
        normalized_method = method.upper()
        if not _HEADER_NAME.fullmatch(normalized_method):
            raise ValueError("method must be an HTTP token")
        normalized_path = _relative_path(path)
        observed_path = normalized_path.split("?", 1)[0]
        if json_body is not None and files is not None:
            raise ValueError("json_body and files cannot be sent together")
        response_limit = self.max_response_bytes if max_response_bytes is None else int(max_response_bytes)
        if response_limit < 1024:
            raise ValueError("max_response_bytes must be at least 1024")
        effective_request_id = request_id or str(uuid.uuid4())
        attempt = 0
        while True:
            attempt += 1
            started = time.monotonic()
            try:
                self._client.cookies.clear()
                try:
                    async with _stream_async(
                        self._client,
                        normalized_method,
                        normalized_path,
                        json=json_body,
                        files=files,
                        params=params,
                        headers=_headers(self.api_key, effective_request_id, idempotency_key, headers),
                    ) as response:
                        content = await _bounded_content_async(response, response_limit)
                finally:
                    self._client.cookies.clear()
                result = _decode_response(response, content, expect_json=expect_json)
            except ForgeResponseTooLarge as exc:
                _notify(
                    self.observer,
                    RequestAttempt(normalized_method, observed_path, attempt, time.monotonic() - started, None, None, str(exc)),
                )
                raise
            except ForgeHTTPError as exc:
                _notify(
                    self.observer,
                    RequestAttempt(
                        normalized_method,
                        observed_path,
                        attempt,
                        time.monotonic() - started,
                        exc.status_code,
                        exc.request_id,
                        str(exc),
                    ),
                )
                policy = self.retry_policy
                if (
                    policy is None
                    or files is not None
                    or attempt >= policy.max_attempts
                    or exc.status_code not in policy.retry_statuses
                    or not policy.permits(normalized_method, idempotency_key=idempotency_key)
                ):
                    raise
                await asyncio.sleep(policy.delay(attempt, retry_after=exc.retry_after))
                continue
            except httpx.HTTPError as exc:
                wrapped = ForgeTransportError(safe_error_text(exc))
                _notify(
                    self.observer,
                    RequestAttempt(normalized_method, observed_path, attempt, time.monotonic() - started, None, None, str(wrapped)),
                )
                policy = self.retry_policy
                if (
                    policy is None
                    or files is not None
                    or attempt >= policy.max_attempts
                    or not policy.permits(normalized_method, idempotency_key=idempotency_key)
                ):
                    raise wrapped from exc
                await asyncio.sleep(policy.delay(attempt))
                continue
            _notify(
                self.observer,
                RequestAttempt(
                    normalized_method,
                    observed_path,
                    attempt,
                    time.monotonic() - started,
                    result.status_code,
                    result.request_id,
                    None,
                ),
            )
            return result

    async def health(self) -> ForgeResponse[JsonObject]:
        return await self.request("GET", "health")

    async def metadata(self, project: str) -> ForgeResponse[JsonObject]:
        return await self.request("GET", f"api/{_segment(project)}/v1/meta")

    async def list_items(self, project: str, resource: str, *, params: Mapping[str, Any] | None = None):
        return await self.request("GET", f"api/{_segment(project)}/v1/{_route(resource)}", params=params)

    async def get_item(self, project: str, resource: str, item_id: str):
        return await self.request("GET", f"api/{_segment(project)}/v1/{_route(resource)}/{_segment(item_id)}")

    async def create_item(self, project: str, resource: str, payload: Mapping[str, Any], *, idempotency_key: str | None = None):
        return await self.request(
            "POST",
            f"api/{_segment(project)}/v1/{_route(resource)}",
            json_body=dict(payload),
            idempotency_key=idempotency_key,
        )

    async def update_item(self, project: str, resource: str, item_id: str, payload: Mapping[str, Any], *, replace: bool = False):
        return await self.request(
            "PUT" if replace else "PATCH",
            f"api/{_segment(project)}/v1/{_route(resource)}/{_segment(item_id)}",
            json_body=dict(payload),
        )

    async def delete_item(self, project: str, resource: str, item_id: str):
        return await self.request("DELETE", f"api/{_segment(project)}/v1/{_route(resource)}/{_segment(item_id)}")

    async def call_operation(self, project: str, operation: str, payload: Mapping[str, Any], *, idempotency_key: str | None = None):
        return await self.request(
            "POST",
            f"api/{_segment(project)}/v1/rpc/{_segment(operation)}",
            json_body=dict(payload),
            idempotency_key=idempotency_key,
        )

    async def iter_items(
        self,
        project: str,
        resource: str,
        *,
        params: Mapping[str, Any] | None = None,
        page_size: int = 100,
        max_items: int = 10_000,
        pagination: Literal["offset", "cursor"] = "offset",
    ) -> AsyncIterator[JsonObject]:
        pages = Pagination(params, page_size, max_items, pagination)
        while True:
            items, done = pages.consume(await self.list_items(project, resource, params=pages.query()))
            for item in items:
                yield item
            if done:
                return

    async def batch_create_items(self, project: str, resource: str, payloads: Sequence[Mapping[str, Any]]) -> ForgeResponse[JsonObject]:
        """Submit one atomic batch; POST is never automatically retried."""
        return await self.request("POST", f"api/{_segment(project)}/v1/{_route(resource)}/_batch", json_body=batch_payload(payloads))

    async def aclose(self) -> None:
        await self._client.aclose()

    async def __aenter__(self) -> AsyncForgeClient:
        return self

    async def __aexit__(self, *_args) -> None:
        await self.aclose()
