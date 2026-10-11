import asyncio

import httpx
import pytest

import json_api_forge
from json_api_forge import AsyncForgeClient, ForgeClient, ForgeHTTPError, ForgeResponseTooLarge


def test_public_facade_and_secure_url_validation():
    assert json_api_forge.__version__ == "0.5.2"
    assert not hasattr(json_api_forge, "create_app")
    with pytest.raises(ValueError, match="plain HTTP"):
        ForgeClient("http://forge.test")
    with pytest.raises(ValueError, match="credentials"):
        ForgeClient("https://user:secret@forge.test")
    with pytest.raises(ValueError, match="loopback"):
        ForgeClient("http://forge.test", allow_insecure_http=True)
    with ForgeClient("http://127.0.0.1:8000", allow_insecure_http=True):
        pass
    with pytest.raises(ValueError, match="traversal"):
        ForgeClient("https://forge.test/base/../admin")
    with pytest.raises(ValueError, match="api_key"):
        ForgeClient("https://forge.test", api_key="bad\r\nkey")


def test_sync_client_routes_headers_errors_and_bounds():
    seen = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        if request.url.path.endswith("/denied"):
            return httpx.Response(403, json={"detail": "no"}, headers={"X-Request-ID": "server-request"})
        if request.url.path.endswith("/large"):
            return httpx.Response(200, content=b"x" * 2048)
        if request.url.path.endswith("/chunked"):
            return httpx.Response(200, stream=ChunkedResponse())
        if request.url.path.endswith("/redirect"):
            return httpx.Response(302, headers={"Location": "https://attacker.test/collect"})
        return httpx.Response(
            200,
            json={"path": request.url.path},
            headers={"X-Request-ID": "server-request", "X-Forge-Idempotent-Replay": "true", "X-Forge-Cache": "hit"},
        )

    with ForgeClient(
        "https://forge.test/base",
        api_key="secret",
        max_response_bytes=1024,
        transport=httpx.MockTransport(handler),
    ) as client:
        response = client.list_items("project one", "gaming/leaderboard", params={"limit": 5})
        assert response.data["path"] == "/base/api/project one/v1/gaming/leaderboard"
        assert b"project%20one" in seen[-1].url.raw_path
        assert response.idempotent_replay and response.cache_status == "hit"
        assert seen[-1].headers["X-API-Key"] == "secret"
        created = client.create_item("p", "notes", {"name": "x"}, idempotency_key="event-1")
        assert created.status_code == 200
        assert seen[-1].headers["Idempotency-Key"] == "event-1"
        with pytest.raises(ForgeHTTPError) as denied:
            client.request("GET", "denied")
        assert denied.value.status_code == 403 and denied.value.request_id == "server-request"
        with pytest.raises(ForgeResponseTooLarge):
            client.request("GET", "large")
        with pytest.raises(ForgeResponseTooLarge):
            client.request("GET", "chunked")
        with pytest.raises(ForgeHTTPError) as redirect:
            client.request("GET", "redirect")
        assert redirect.value.status_code == 302
        with pytest.raises(ValueError):
            client.request("GET", "https://attacker.test/")
        with pytest.raises(ValueError):
            client.request("GET", "../secret")
        with pytest.raises(ValueError):
            client.request("GET", "%2e%2e/secret")
        with pytest.raises(ValueError):
            client.get_item("p", "notes", "folder/item")


class ChunkedResponse(httpx.SyncByteStream):
    def __iter__(self):
        yield b"x" * 700
        yield b"y" * 700


def test_async_client_and_transport_error():
    async def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path.endswith("/offline"):
            raise httpx.ConnectError("offline", request=request)
        return httpx.Response(200, json={"ok": True}, headers={"X-Request-ID": "async-request"})

    async def run():
        async with AsyncForgeClient("https://forge.test", transport=httpx.MockTransport(handler)) as client:
            response = await client.health()
            assert response.data == {"ok": True} and response.request_id == "async-request"
            with pytest.raises(json_api_forge.ForgeTransportError):
                await client.request("GET", "offline")

    asyncio.run(run())
