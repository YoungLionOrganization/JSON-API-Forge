from __future__ import annotations

import asyncio
import io

import httpx
import pytest

from json_api_forge import AsyncForgeClient, AsyncForgeCluster, ForgeClient, ForgeCluster, ForgeEndpoint, ForgeHTTPError


@pytest.mark.parametrize("asynchronous", [False, True])
@pytest.mark.parametrize("method,idempotency_key", [("PUT", None), ("POST", "upload-once")])
def test_cluster_never_replays_multipart_uploads(asynchronous, method, idempotency_key):
    calls = []
    endpoints = [ForgeEndpoint("primary", "https://primary.test"), ForgeEndpoint("secondary", "https://secondary.test")]

    def factory(endpoint):
        def handler(request):
            calls.append(endpoint.name)
            assert b"attachment content" in request.content
            return httpx.Response(503 if endpoint.name == "primary" else 201, json={"endpoint": endpoint.name})

        client = AsyncForgeClient if asynchronous else ForgeClient
        return client(endpoint.base_url, transport=httpx.MockTransport(handler))

    if asynchronous:

        async def run():
            async with AsyncForgeCluster(endpoints, client_factory=factory) as cluster:
                with pytest.raises(ForgeHTTPError):
                    await cluster.request(
                        method,
                        "upload",
                        files={"upload": ("file.txt", io.BytesIO(b"attachment content"))},
                        idempotency_key=idempotency_key,
                    )

        asyncio.run(run())
    else:
        with ForgeCluster(endpoints, client_factory=factory) as cluster:
            with pytest.raises(ForgeHTTPError):
                cluster.request(
                    method,
                    "upload",
                    files={"upload": ("file.txt", io.BytesIO(b"attachment content"))},
                    idempotency_key=idempotency_key,
                )
    assert calls == ["primary"]


@pytest.mark.asyncio
async def test_cancelled_bulk_item_stops_other_running_operations_before_returning():
    started = asyncio.Event()
    stopped = asyncio.Event()
    workers = []

    async def slow_mutation():
        workers.append(asyncio.current_task())
        started.set()
        try:
            await asyncio.Event().wait()
        finally:
            stopped.set()

    async def cancel_item():
        await started.wait()
        raise asyncio.CancelledError

    async with AsyncForgeCluster([ForgeEndpoint("only", "https://forge.test")]) as cluster:
        try:
            with pytest.raises(asyncio.CancelledError):
                await cluster.bulk([cancel_item, slow_mutation], concurrency=2)
            assert stopped.is_set(), "Bulk returned with an unowned operation still running"
            assert all(task.done() for task in workers)
        finally:
            for task in workers:
                task.cancel()
            await asyncio.gather(*workers, return_exceptions=True)


@pytest.mark.asyncio
async def test_bulk_caller_cancellation_does_not_start_queued_operations():
    started = asyncio.Event()
    active = calls = 0

    async def mutation():
        nonlocal active, calls
        active += 1
        calls += 1
        if active == 2:
            started.set()
        try:
            await asyncio.Event().wait()
        finally:
            active -= 1

    async with AsyncForgeCluster([ForgeEndpoint("only", "https://forge.test")]) as cluster:
        task = asyncio.create_task(cluster.bulk([mutation] * 20, concurrency=2))
        try:
            await asyncio.wait_for(started.wait(), 1)
            task.cancel()
            with pytest.raises(asyncio.CancelledError):
                await task
            assert active == 0 and calls == 2
        finally:
            task.cancel()
            await asyncio.gather(task, return_exceptions=True)
