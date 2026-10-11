import asyncio
import json

import httpx
import pytest

from json_api_forge import AsyncForgeClient, ForgeClient, ForgeHTTPError, RetryPolicy


def traverse(handler, *, asynchronous=False, **kwargs):
    if asynchronous:

        async def run():
            async with AsyncForgeClient("https://forge.test", transport=httpx.MockTransport(handler)) as client:
                return [item async for item in client.iter_items("p", "tasks", **kwargs)]

        return asyncio.run(run())
    with ForgeClient("https://forge.test", transport=httpx.MockTransport(handler)) as client:
        return list(client.iter_items("p", "tasks", **kwargs))


@pytest.mark.parametrize("asynchronous", [False, True])
@pytest.mark.parametrize("mode", ["cursor", "offset"])
def test_bounded_lazy_traversal_preserves_filters(asynchronous, mode):
    seen = []
    params = {"status": "todo"}

    def handler(request):
        seen.append(dict(request.url.params))
        offset = int(request.url.params.get("offset", request.url.params.get("cursor", "0")))
        limit = int(request.url.params["limit"])
        items = [{"id": n} for n in range(offset, min(offset + limit, 8))]
        return httpx.Response(200, json={"items": items, "has_more": True, "next_cursor": str(offset + len(items))})

    items = traverse(handler, asynchronous=asynchronous, params=params, page_size=2, max_items=5, pagination=mode)
    assert [item["id"] for item in items] == list(range(5))
    assert [query["limit"] for query in seen] == ["2", "2", "1"]
    assert all(query["status"] == "todo" for query in seen)
    assert all(("offset" in query) == (mode == "offset") for query in seen)
    assert params == {"status": "todo"}


@pytest.mark.parametrize("asynchronous", [False, True])
def test_cursor_short_page_is_not_final_and_can_resume(asynchronous):
    pages = iter(
        [
            {"items": [{"id": 2}], "has_more": True, "next_cursor": "opaque&+="},
            {"items": [{"id": 3}], "has_more": False, "next_cursor": None},
        ]
    )
    seen = []

    def handler(request):
        seen.append(request.url.params.get("cursor"))
        return httpx.Response(200, json=next(pages))

    assert traverse(handler, asynchronous=asynchronous, pagination="cursor", params={"cursor": "start"}) == [{"id": 2}, {"id": 3}]
    assert seen == ["start", "opaque&+="]


@pytest.mark.parametrize(
    "payload",
    [
        {},
        {"items": [1]},
        {"items": [{}, {}]},
        {"items": [], "has_more": True, "next_cursor": "a"},
        {"items": [{}], "has_more": 1, "next_cursor": "a"},
        {"items": [{}], "has_more": True, "next_cursor": "start"},
        {"items": [{}], "has_more": True, "next_cursor": "bad\n"},
        {"items": [{}], "has_more": False, "next_cursor": "a"},
    ],
)
@pytest.mark.parametrize("asynchronous", [False, True])
def test_malformed_cursor_pages_fail_before_yielding(payload, asynchronous):
    with pytest.raises(ForgeHTTPError) as error:
        traverse(
            lambda _: httpx.Response(200, json=payload, headers={"X-Request-ID": "bad-page"}),
            asynchronous=asynchronous,
            pagination="cursor",
            params={"cursor": "start"},
            page_size=1,
        )
    assert error.value.request_id == "bad-page"


def test_cursor_cycles_are_rejected():
    pages = iter([{"items": [{}], "has_more": True, "next_cursor": value} for value in ["a", "b", "a"]])
    with pytest.raises(ForgeHTTPError, match="advance"):
        traverse(lambda _: httpx.Response(200, json=next(pages)), pagination="cursor")


@pytest.mark.parametrize(
    "options",
    [
        {"page_size": True},
        {"max_items": 0},
        {"pagination": "unknown"},
        {"pagination": "cursor", "params": {"sort": "id"}},
        {"pagination": "cursor", "params": {"offset": 0}},
        {"params": {"cursor": "start"}},
        {"params": {"offset": -1}},
        {"pagination": "cursor", "params": {"cursor": None}},
    ],
)
def test_bad_pagination_options_do_not_send_requests(options):
    def handler(_):
        pytest.fail("invalid options reached the server")

    with pytest.raises(ValueError):
        traverse(handler, **options)


@pytest.mark.parametrize("asynchronous", [False, True])
def test_early_stop_does_not_prefetch(asynchronous):
    seen = []

    def handler(request):
        seen.append(request)
        return httpx.Response(200, json={"items": [{"id": 1}], "has_more": True, "next_cursor": "a"})

    if asynchronous:

        async def run():
            async with AsyncForgeClient("https://forge.test", transport=httpx.MockTransport(handler)) as client:
                iterator = client.iter_items("p", "tasks", pagination="cursor")
                assert await anext(iterator) == {"id": 1}
                await iterator.aclose()

        asyncio.run(run())
    else:
        with ForgeClient("https://forge.test", transport=httpx.MockTransport(handler)) as client:
            iterator = client.iter_items("p", "tasks", pagination="cursor")
            assert next(iterator) == {"id": 1}
            iterator.close()
    assert len(seen) == 1


@pytest.mark.parametrize("asynchronous", [False, True])
def test_atomic_batch_body_and_no_unsafe_replay(asynchronous):
    seen = []

    def handler(request):
        seen.append(request)
        assert json.loads(request.content) == [{"title": "a"}, {"title": "b", "priority": 3}]
        assert request.url.path == "/api/p/v1/tasks/_batch"
        return httpx.Response(503, json={"detail": "busy"})

    async def run():
        async with AsyncForgeClient("https://forge.test", transport=httpx.MockTransport(handler), retry_policy=RetryPolicy()) as client:
            await client.batch_create_items("p", "tasks", [{"title": "a"}, {"title": "b", "priority": 3}])

    with pytest.raises(ForgeHTTPError):
        if asynchronous:
            asyncio.run(run())
        else:
            with ForgeClient("https://forge.test", transport=httpx.MockTransport(handler), retry_policy=RetryPolicy()) as client:
                client.batch_create_items("p", "tasks", [{"title": "a"}, {"title": "b", "priority": 3}])
    assert len(seen) == 1


@pytest.mark.parametrize("payloads", [[], [None], "bad", [{}] * 5001, iter([{}])])
def test_invalid_batches_are_rejected(payloads):
    with ForgeClient("https://forge.test") as client, pytest.raises(ValueError):
        client.batch_create_items("p", "tasks", payloads)


def test_starting_offset_and_empty_final_cursor():
    def handler(request):
        assert request.url.params["offset"] == "4"
        return httpx.Response(200, json={"items": [{"id": 4}]})

    assert traverse(handler, params={"offset": 4}) == [{"id": 4}]
    assert traverse(lambda _: httpx.Response(200, json={"items": [], "has_more": False, "next_cursor": None}), pagination="cursor") == []


def test_async_traversal_cancellation_propagates():
    async def run():
        started = asyncio.Event()

        async def handler(_):
            started.set()
            await asyncio.Future()

        async with AsyncForgeClient("https://forge.test", transport=httpx.MockTransport(handler)) as client:

            async def collect():
                return [item async for item in client.iter_items("p", "tasks")]

            task = asyncio.create_task(collect())
            await started.wait()
            task.cancel()
            with pytest.raises(asyncio.CancelledError):
                await task

    asyncio.run(run())


@pytest.mark.parametrize("asynchronous", [False, True])
def test_server_page_size_cap_does_not_truncate_offset_results(asynchronous):
    def handler(request):
        offset = int(request.url.params["offset"])
        limit = min(2, int(request.url.params["limit"]))
        return httpx.Response(200, json={"items": [{"id": n} for n in range(offset, min(offset + limit, 5))], "limit": limit})

    assert traverse(handler, asynchronous=asynchronous, page_size=100) == [{"id": n} for n in range(5)]


@pytest.mark.parametrize("limit", [0, True, "1", 101])
def test_invalid_effective_page_limit_fails(limit):
    with pytest.raises(ForgeHTTPError, match="effective limit"):
        traverse(lambda _: httpx.Response(200, json={"items": [], "limit": limit}))
