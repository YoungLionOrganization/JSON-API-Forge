from __future__ import annotations

import asyncio
import sys

import httpx
import pytest

from json_api_forge import AsyncForgeClient, ForgeClient, ForgeHTTPError


@pytest.mark.parametrize("content, expected", [(b"null", None), (b" null \n", None), (b"false", False), (b"0", 0), (b'""', "")])
def test_sync_response_accepts_valid_json_scalar(content, expected) -> None:
    with ForgeClient("https://forge.test", transport=httpx.MockTransport(lambda _: httpx.Response(200, content=content))) as client:
        assert client.request("GET", "operation").data == expected


@pytest.mark.asyncio
@pytest.mark.parametrize("content, expected", [(b"null", None), (b" null \n", None), (b"false", False), (b"0", 0), (b'""', "")])
async def test_async_response_accepts_valid_json_scalar(content, expected) -> None:
    async with AsyncForgeClient(
        "https://forge.test", transport=httpx.MockTransport(lambda _: httpx.Response(200, content=content))
    ) as client:
        assert (await client.request("GET", "operation")).data == expected


@pytest.mark.parametrize("content", [b"undefined", b"null suffix", b"\xff", b"{", b" \n"])
def test_malformed_json_remains_rejected(content) -> None:
    with ForgeClient("https://forge.test", transport=httpx.MockTransport(lambda _: httpx.Response(200, content=content))) as client:
        with pytest.raises(ForgeHTTPError, match="non-JSON"):
            client.request("GET", "operation")


@pytest.mark.parametrize("asynchronous", [False, True])
@pytest.mark.parametrize("content", [b"NaN", b"Infinity", b"-Infinity", b'{"items": [{"value": NaN}]}', b"1e999", b'{"value": -1e999}'])
def test_non_finite_response_numbers_are_rejected_and_observed(content, asynchronous) -> None:
    events = []
    transport = httpx.MockTransport(lambda _: httpx.Response(200, content=content, headers={"X-Request-ID": "invalid-number"}))

    async def run():
        async with AsyncForgeClient("https://forge.test", transport=transport, observer=events.append) as client:
            await client.request("GET", "operation")

    with pytest.raises(ForgeHTTPError, match="non-JSON") as caught:
        if asynchronous:
            asyncio.run(run())
        else:
            with ForgeClient("https://forge.test", transport=transport, observer=events.append) as client:
                client.request("GET", "operation")
    assert caught.value.status_code == 200
    assert caught.value.request_id == "invalid-number"
    assert len(events) == 1 and events[0].status_code == 200


@pytest.mark.parametrize("asynchronous", [False, True])
@pytest.mark.parametrize("content, expected", [(b"1.25", 1.25), (b"1.7976931348623157e308", 1.7976931348623157e308)])
def test_finite_response_numbers_preserve_supported_float_range(content, expected, asynchronous) -> None:
    transport = httpx.MockTransport(lambda _: httpx.Response(200, content=content))

    async def run():
        async with AsyncForgeClient("https://forge.test", transport=transport) as client:
            return (await client.request("GET", "operation")).data

    if asynchronous:
        assert asyncio.run(run()) == expected
    else:
        with ForgeClient("https://forge.test", transport=transport) as client:
            assert client.request("GET", "operation").data == expected


@pytest.mark.parametrize("asynchronous", [False, True])
@pytest.mark.parametrize("status", [200, 503])
def test_integer_decoder_policy_failures_keep_http_error_contract(status, asynchronous) -> None:
    maximum = getattr(sys, "get_int_max_str_digits", lambda: 0)()
    if not maximum:
        pytest.skip("This Python interpreter does not limit integer string conversion")
    events = []
    transport = httpx.MockTransport(
        lambda _: httpx.Response(status, content=b"9" * (maximum + 1), headers={"X-Request-ID": "bounded-integer"})
    )

    async def run():
        async with AsyncForgeClient("https://forge.test", transport=transport, observer=events.append) as client:
            await client.request("GET", "operation")

    with pytest.raises(ForgeHTTPError) as caught:
        if asynchronous:
            asyncio.run(run())
        else:
            with ForgeClient("https://forge.test", transport=transport, observer=events.append) as client:
                client.request("GET", "operation")
    assert caught.value.status_code == status
    assert caught.value.request_id == "bounded-integer"
    assert len(str(caught.value)) < 2300
    assert len(events) == 1 and events[0].status_code == status
