from __future__ import annotations

import asyncio
import gzip

import httpx
import pytest

from framework.services.http_client import ResilientHTTPClient, ResponseTooLarge


class UnreadableBody(httpx.AsyncByteStream):
    def __init__(self):
        self.closed = False

    async def __aiter__(self):
        raise AssertionError("Encoded body must be rejected before a decoder reads it")
        yield b""

    async def aclose(self):
        self.closed = True


@pytest.mark.asyncio
@pytest.mark.parametrize("encoding", ["gzip", "br", "deflate", "gzip, br", " GZip "])
async def test_bounded_http_rejects_encoded_body_without_reading_it(encoding):
    body = UnreadableBody()
    attempts = []

    async def handler(request):
        attempts.append(request)
        return httpx.Response(200, headers={"Content-Encoding": encoding}, stream=body)

    client = ResilientHTTPClient()
    await client.client.aclose()
    client.client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    try:
        with pytest.raises(ResponseTooLarge, match="identity content encoding"):
            await client.request("GET", "https://upstream.test/data", max_response_bytes=1024, retries=2)
        assert body.closed and len(attempts) == 1
    finally:
        await client.close()


@pytest.mark.asyncio
async def test_identity_negotiation_preserves_caller_headers_without_mutating_them():
    headers = {"accept-encoding": "gzip", "X-Trace": "kept"}

    async def handler(request):
        assert request.headers["accept-encoding"] == "identity"
        assert request.headers["x-trace"] == "kept"
        return httpx.Response(200, json={"ok": True}, headers={"Content-Encoding": "identity"})

    client = ResilientHTTPClient()
    await client.client.aclose()
    client.client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    try:
        assert (await client.request("GET", "https://upstream.test/data", headers=headers, max_response_bytes=1024)).json() == {"ok": True}
        assert headers == {"accept-encoding": "gzip", "X-Trace": "kept"}
    finally:
        await client.close()


@pytest.mark.asyncio
async def test_unbounded_explicit_client_call_preserves_existing_compressed_response_support():
    class Body(httpx.AsyncByteStream):
        async def __aiter__(self):
            yield gzip.compress(b'{"ok":true}')

    async def handler(request):
        return httpx.Response(200, headers={"Content-Encoding": "gzip"}, stream=Body())

    client = ResilientHTTPClient()
    await client.client.aclose()
    client.client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    try:
        assert (await client.request("GET", "https://upstream.test/data")).json() == {"ok": True}
    finally:
        await client.close()


@pytest.mark.parametrize("raw", ["nan", "NaN", "inf", "-inf", "invalid"])
def test_non_finite_retry_after_uses_bounded_backoff(raw):
    response = httpx.Response(503, headers={"Retry-After": raw})
    assert ResilientHTTPClient._retry_delay(0, response) == 0.25


@pytest.mark.asyncio
async def test_non_finite_retry_after_does_not_abort_a_retry(monkeypatch):
    calls = []
    delays = []

    async def handler(request):
        calls.append(request)
        return httpx.Response(503 if len(calls) == 1 else 200, json={"ok": True}, headers={"Retry-After": "nan"})

    async def sleep(delay):
        delays.append(delay)

    monkeypatch.setattr(asyncio, "sleep", sleep)
    client = ResilientHTTPClient()
    await client.client.aclose()
    client.client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    try:
        assert (await client.request("GET", "https://upstream.test/data", max_response_bytes=1024)).json() == {"ok": True}
        assert len(calls) == 2 and delays == [0.25]
    finally:
        await client.close()


@pytest.mark.asyncio
async def test_recovered_retry_closes_the_circuit_for_the_next_request():
    attempts = []

    async def handler(request):
        attempts.append(request)
        return httpx.Response(503 if len(attempts) == 1 else 200, json={"ok": True}, headers={"Retry-After": "0"})

    client = ResilientHTTPClient(failure_threshold=1)
    await client.client.aclose()
    client.client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    try:
        assert (await client.request("GET", "https://upstream.test/data", retries=1)).status_code == 200
        assert (await client.request("GET", "https://upstream.test/data", retries=0)).status_code == 200
        assert len(attempts) == 3
    finally:
        await client.close()
