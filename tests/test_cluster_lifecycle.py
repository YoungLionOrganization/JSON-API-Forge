from __future__ import annotations

import asyncio

import pytest

from json_api_forge import AsyncForgeCluster, CircuitBreakerPolicy, ForgeCluster, ForgeEndpoint

ENDPOINTS = [ForgeEndpoint("first", "https://first.test"), ForgeEndpoint("second", "https://second.test")]


def test_sync_close_failure_still_closes_every_client() -> None:
    closed = []

    class Client:
        def __init__(self, name):
            self.name = name

        def close(self):
            closed.append(self.name)
            if self.name == "first":
                raise OSError("first close failed")

    cluster = ForgeCluster(ENDPOINTS, client_factory=lambda endpoint: Client(endpoint.name))
    with pytest.raises(OSError, match="first close failed"):
        cluster.close()
    assert closed == ["first", "second"]


@pytest.mark.asyncio
async def test_async_close_failure_waits_for_every_client() -> None:
    started = asyncio.Event()
    release = asyncio.Event()
    completed = asyncio.Event()
    workers = []

    class Client:
        def __init__(self, name):
            self.name = name

        async def aclose(self):
            workers.append(asyncio.current_task())
            if self.name == "first":
                raise OSError("first close failed")
            started.set()
            await release.wait()
            completed.set()

    cluster = AsyncForgeCluster(ENDPOINTS, client_factory=lambda endpoint: Client(endpoint.name))
    task = asyncio.create_task(cluster.aclose())
    try:
        await started.wait()
        await asyncio.sleep(0)
        await asyncio.sleep(0)
        assert not task.done(), "Close returned while another client was still closing"
        release.set()
        with pytest.raises(OSError, match="first close failed"):
            await task
        assert completed.is_set()
    finally:
        release.set()
        await asyncio.gather(task, *workers, return_exceptions=True)


@pytest.mark.asyncio
async def test_cancelled_cluster_close_finishes_cleanup_before_returning() -> None:
    started = asyncio.Event()
    release = asyncio.Event()
    completed = asyncio.Event()

    class Client:
        async def aclose(self):
            started.set()
            await release.wait()
            completed.set()

    cluster = AsyncForgeCluster(ENDPOINTS[:1], client_factory=lambda _: Client())
    task = asyncio.create_task(cluster.aclose())
    try:
        await started.wait()
        task.cancel()
        await asyncio.sleep(0)
        task.cancel()
        await asyncio.sleep(0)
        assert not task.done()
        release.set()
        with pytest.raises(asyncio.CancelledError):
            await task
        assert completed.is_set()
    finally:
        release.set()
        await asyncio.gather(task, return_exceptions=True)


@pytest.mark.asyncio
async def test_cancelled_health_child_cancels_and_drains_sibling_probe() -> None:
    started = asyncio.Event()
    stopped = asyncio.Event()
    probes = []

    class Client:
        def __init__(self, name):
            self.name = name

        async def health(self):
            probes.append(asyncio.current_task())
            if self.name == "first":
                await started.wait()
                raise asyncio.CancelledError
            started.set()
            try:
                await asyncio.Event().wait()
            finally:
                stopped.set()

        async def aclose(self):
            pass

    async with AsyncForgeCluster(ENDPOINTS, client_factory=lambda endpoint: Client(endpoint.name)) as cluster:
        try:
            with pytest.raises(asyncio.CancelledError):
                await cluster.health_all()
            assert stopped.is_set(), "Health returned with an active orphaned probe"
            assert all(task.done() for task in probes)
        finally:
            for task in probes:
                task.cancel()
            await asyncio.gather(*probes, return_exceptions=True)


@pytest.mark.parametrize("maximum", [True, False, 1.5, float("nan"), float("inf"), "2", 0, 100001])
@pytest.mark.parametrize("asynchronous", [False, True])
def test_bulk_maximum_rejects_non_integer_before_consuming_work(maximum, asynchronous) -> None:
    def work():
        raise AssertionError("Invalid maximum consumed input")
        yield

    if asynchronous:

        async def run():
            async with AsyncForgeCluster(ENDPOINTS[:1]) as cluster:
                with pytest.raises(ValueError, match="max_items"):
                    await cluster.bulk(work(), max_items=maximum)

        asyncio.run(run())
    else:
        with ForgeCluster(ENDPOINTS[:1]) as cluster:
            with pytest.raises(ValueError, match="max_items"):
                cluster.bulk_create("project", "records", work(), max_items=maximum)


@pytest.mark.parametrize("workers", [True, False, 1.5, float("nan"), float("inf"), "2", 0, 65])
def test_sync_bulk_rejects_invalid_workers_before_consuming_work(workers) -> None:
    def work():
        raise AssertionError("Invalid worker count consumed input")
        yield

    with ForgeCluster(ENDPOINTS[:1]) as cluster:
        with pytest.raises(ValueError, match="max_workers"):
            cluster.bulk_create("project", "records", work(), max_workers=workers)


@pytest.mark.parametrize("value", [True, False, 1.5, float("nan"), float("inf"), "2", 0, 101])
@pytest.mark.parametrize("policy", ["weight", "failure_threshold"])
def test_integer_cluster_policies_reject_invalid_types(value, policy) -> None:
    with pytest.raises(ValueError, match=policy):
        if policy == "weight":
            ForgeEndpoint("first", "https://first.test", weight=value)
        else:
            CircuitBreakerPolicy(failure_threshold=value)


@pytest.mark.asyncio
async def test_repeated_caller_cancellation_does_not_interrupt_bulk_cleanup() -> None:
    started = asyncio.Event()
    cleanup_started = asyncio.Event()
    release_cleanup = asyncio.Event()
    completed = asyncio.Event()

    async def slow_mutation():
        started.set()
        try:
            await asyncio.Event().wait()
        finally:
            cleanup_started.set()
            await release_cleanup.wait()
            completed.set()

    async def cancelled_item():
        await started.wait()
        raise asyncio.CancelledError

    async with AsyncForgeCluster(ENDPOINTS[:1]) as cluster:
        pending = asyncio.create_task(cluster.bulk([cancelled_item, slow_mutation], concurrency=2))
        try:
            await cleanup_started.wait()
            pending.cancel()
            await asyncio.sleep(0)
            pending.cancel()
            await asyncio.sleep(0)
            assert not pending.done()
            release_cleanup.set()
            with pytest.raises(asyncio.CancelledError):
                await pending
            assert completed.is_set()
        finally:
            release_cleanup.set()
            await asyncio.gather(pending, return_exceptions=True)
