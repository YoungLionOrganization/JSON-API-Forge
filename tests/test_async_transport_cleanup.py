from __future__ import annotations

import asyncio
import io

import httpx
import pytest

from json_api_forge import AsyncForgeClient, ForgeTransportError


class SlowClosingStream(httpx.AsyncByteStream):
    def __init__(self, outcome: str):
        self.outcome = outcome
        self.read_started = asyncio.Event()
        self.close_started = asyncio.Event()
        self.release_close = asyncio.Event()
        self.closed = False
        self.close_calls = 0

    async def __aiter__(self):
        self.read_started.set()
        if self.outcome == "cancel_read":
            await asyncio.Event().wait()
        if self.outcome == "read_error":
            raise httpx.ReadError("response interrupted")
        yield b'{"ok":true}'

    async def aclose(self):
        self.close_calls += 1
        self.close_started.set()
        await self.release_close.wait()
        self.closed = True


@pytest.mark.asyncio
@pytest.mark.parametrize("multipart", [False, True])
@pytest.mark.parametrize("outcome", ["success", "read_error", "cancel_read"])
async def test_response_close_is_drained_before_repeated_cancellation_returns(multipart, outcome):
    stream = SlowClosingStream(outcome)

    async def handle(request):
        await request.aread()
        return httpx.Response(200, stream=stream)

    async with AsyncForgeClient("https://forge.test", transport=httpx.MockTransport(handle)) as client:
        options = {"files": {"upload": ("sample.txt", io.BytesIO(b"sample"))}} if multipart else {}
        task = asyncio.create_task(client.request("POST", "upload", **options))
        try:
            await asyncio.wait_for(stream.read_started.wait(), 5)
            if outcome == "cancel_read":
                task.cancel()
            await asyncio.wait_for(stream.close_started.wait(), 5)
            task.cancel()
            await asyncio.sleep(0)
            task.cancel()
            await asyncio.sleep(0)
            assert not task.done(), "Request returned before its response connection was closed"
            stream.release_close.set()
            with pytest.raises(asyncio.CancelledError):
                await task
            assert stream.closed
            assert stream.close_calls == 1
        finally:
            stream.release_close.set()
            await asyncio.gather(task, return_exceptions=True)


@pytest.mark.asyncio
@pytest.mark.parametrize("multipart", [False, True])
async def test_response_read_failure_preserves_error_after_cleanup(multipart):
    stream = SlowClosingStream("read_error")
    stream.release_close.set()
    transport = httpx.MockTransport(lambda _: httpx.Response(200, stream=stream))
    async with AsyncForgeClient("https://forge.test", transport=transport) as client:
        options = {"files": {"upload": ("sample.txt", io.BytesIO(b"sample"))}} if multipart else {}
        with pytest.raises(ForgeTransportError, match="response interrupted"):
            await client.request("POST", "upload", **options)
    assert stream.closed
    assert stream.close_calls == 1


@pytest.mark.asyncio
async def test_single_client_close_finishes_transport_cleanup_before_returning_cancellation():
    started = asyncio.Event()
    release = asyncio.Event()
    completed = asyncio.Event()

    class Transport(httpx.AsyncBaseTransport):
        async def handle_async_request(self, request):
            return httpx.Response(200, json={})

        async def aclose(self):
            started.set()
            await release.wait()
            completed.set()

    client = AsyncForgeClient("https://forge.test", transport=Transport())
    task = asyncio.create_task(client.aclose())
    try:
        await asyncio.wait_for(started.wait(), 5)
        task.cancel()
        await asyncio.sleep(0)
        task.cancel()
        await asyncio.sleep(0)
        assert not task.done(), "Client returned before its HTTP transport was closed"
        release.set()
        with pytest.raises(asyncio.CancelledError):
            await task
        assert completed.is_set()
    finally:
        release.set()
        await asyncio.gather(task, return_exceptions=True)
