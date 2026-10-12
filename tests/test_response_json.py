from __future__ import annotations

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
