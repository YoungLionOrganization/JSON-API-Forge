from __future__ import annotations

import asyncio

import httpx
import pytest

from json_api_forge import AsyncEditorControlPlaneClient, ForgeHTTPError, ForgeSessionError

OLD_SESSION = "jfe_session_" + "A" * 48
NEW_SESSION = "jfe_session_" + "B" * 48


@pytest.mark.asyncio
async def test_old_unauthorized_request_cannot_clear_new_session() -> None:
    started = asyncio.Event()
    release = asyncio.Event()

    async def handler(request):
        if request.headers.get("Authorization") == f"Bearer {OLD_SESSION}":
            started.set()
            await release.wait()
            return httpx.Response(401, json={"detail": "expired"})
        assert request.headers["Authorization"] == f"Bearer {NEW_SESSION}"
        return httpx.Response(200, json={"username": "new account"})

    async with AsyncEditorControlPlaneClient("https://forge.test", transport=httpx.MockTransport(handler)) as client:
        client.set_session_token(OLD_SESSION)
        previous = asyncio.create_task(client.profile())
        await started.wait()
        client.set_session_token(NEW_SESSION)
        release.set()
        with pytest.raises(ForgeHTTPError):
            await previous
        assert client.has_session
        assert (await client.profile()).data["username"] == "new account"


@pytest.mark.asyncio
@pytest.mark.parametrize("authentication", ["login", "setup", "register"])
@pytest.mark.parametrize("interruption", ["clear", "close", "new_session"])
async def test_late_authentication_cannot_restore_or_replace_session(authentication, interruption) -> None:
    started = asyncio.Event()
    release = asyncio.Event()

    async def handler(_request):
        started.set()
        await release.wait()
        return httpx.Response(200, json={"access_token": OLD_SESSION, "profile": {"username": "old"}})

    async with AsyncEditorControlPlaneClient("https://forge.test", transport=httpx.MockTransport(handler)) as client:
        if authentication == "login":
            operation = client.login("old", "password")
        elif authentication == "setup":
            operation = client.setup_founder("S" * 32, "old", "password", "Old")
        else:
            operation = client.register("jfi_" + "S" * 48, "old", "password", "Old")
        pending = asyncio.create_task(operation)
        await started.wait()
        if interruption == "clear":
            client.clear_session()
        elif interruption == "close":
            await client.aclose()
        else:
            client.set_session_token(NEW_SESSION)
        release.set()
        with pytest.raises(ForgeSessionError, match="changed"):
            await pending
        assert client.has_session == (interruption == "new_session")
        if client.has_session:
            assert client._headers(authenticated=True)["Authorization"] == f"Bearer {NEW_SESSION}"


@pytest.mark.asyncio
async def test_old_logout_cannot_clear_replacement_session() -> None:
    started = asyncio.Event()
    release = asyncio.Event()

    async def handler(_request):
        started.set()
        await release.wait()
        return httpx.Response(204)

    async with AsyncEditorControlPlaneClient("https://forge.test", transport=httpx.MockTransport(handler)) as client:
        client.set_session_token(OLD_SESSION)
        pending = asyncio.create_task(client.logout())
        await started.wait()
        client.set_session_token(NEW_SESSION)
        release.set()
        await pending
        assert client._headers(authenticated=True)["Authorization"] == f"Bearer {NEW_SESSION}"


@pytest.mark.asyncio
async def test_latest_login_wins_when_responses_arrive_out_of_order() -> None:
    started = asyncio.Event()
    release = asyncio.Event()

    async def handler(request):
        if b'"username":"old"' in request.content:
            started.set()
            await release.wait()
            return httpx.Response(200, json={"access_token": OLD_SESSION})
        return httpx.Response(200, json={"access_token": NEW_SESSION})

    async with AsyncEditorControlPlaneClient("https://forge.test", transport=httpx.MockTransport(handler)) as client:
        old_login = asyncio.create_task(client.login("old", "password"))
        await started.wait()
        await client.login("new", "password")
        release.set()
        with pytest.raises(ForgeSessionError, match="changed"):
            await old_login
        assert client._headers(authenticated=True)["Authorization"] == f"Bearer {NEW_SESSION}"


@pytest.mark.asyncio
async def test_current_unauthorized_request_clears_only_current_session() -> None:
    async with AsyncEditorControlPlaneClient(
        "https://forge.test", transport=httpx.MockTransport(lambda _: httpx.Response(401, json={"detail": "expired"}))
    ) as client:
        client.set_session_token(NEW_SESSION)
        with pytest.raises(ForgeHTTPError):
            await client.profile()
        assert not client.has_session
