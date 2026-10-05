"""Real two-participant WebRTC contract, enabled in the dedicated browser CI job.

Uses synthetic devices exclusively; no physical microphone or camera is opened.
Run with FORGE_BROWSER_TESTS=1 and an installed Playwright Chromium browser.
"""

from __future__ import annotations

import os
import socket
import subprocess
import threading
import time
from pathlib import Path

import httpx
import pytest
import uvicorn

from tests.test_v050_editor_collaboration import SETUP_TOKEN, _account_app

pytestmark = pytest.mark.skipif(os.environ.get("FORGE_BROWSER_TESTS") != "1", reason="Dedicated browser contract job only")


def until(page, condition, timeout=30000):
    # Poll via the automation protocol without injecting eval into the strict-CSP page.
    deadline = time.monotonic() + timeout / 1000
    while time.monotonic() < deadline:
        try:
            if page.evaluate(condition):
                return
        except Exception as reason:
            if "Execution context was destroyed" not in str(reason):
                raise
        page.wait_for_timeout(50)
    raise AssertionError(f"Call condition timed out: {condition}; UI: {page.locator('main').inner_text()}")


@pytest.fixture
def call_server(tmp_path):
    app, _ = _account_app(tmp_path)
    with socket.socket() as listener:
        listener.bind(("127.0.0.1", 0))
        listener.listen(128)
        server = uvicorn.Server(uvicorn.Config(app, log_level="warning", access_log=False, timeout_graceful_shutdown=5))
        thread = threading.Thread(target=lambda: server.run(sockets=[listener]), daemon=True)
        thread.start()
        deadline = time.monotonic() + 15
        while not server.started and thread.is_alive() and time.monotonic() < deadline:
            time.sleep(0.05)
        assert server.started, "Call API fixture failed to start"
        base = f"http://localhost:{listener.getsockname()[1]}/__forge/editor/v1"
        try:
            with httpx.Client(base_url=base, timeout=10, trust_env=False) as client:
                founder = client.post(
                    "/setup/founder",
                    headers={"X-Forge-Setup-Token": SETUP_TOKEN},
                    json={
                        "username": "founder",
                        "password": "Granite river orbits seven moons 42!",
                        "display_name": "Forge Founder",
                    },
                )
                assert founder.status_code == 201, founder.text
                client.headers["Authorization"] = "Bearer " + founder.json()["access_token"]
                area = client.post("/areas", json={"project": "Notes", "name": "Browser room", "visibility": "open"})
                assert area.status_code == 201, area.text
                yield client, base, area.json()["id"]
        finally:
            server.should_exit = True
            thread.join(timeout=10)
            assert not thread.is_alive(), "Call API fixture failed to stop"


@pytest.fixture
def browser():
    playwright = pytest.importorskip("playwright.sync_api")
    with playwright.sync_playwright() as runtime:
        options = {"headless": True, "args": ["--use-fake-device-for-media-stream", "--use-fake-ui-for-media-stream"]}
        executable = os.environ.get("FORGE_BROWSER_EXECUTABLE")
        if executable:
            options["executable_path"] = executable
        if os.environ.get("FORGE_AGENT_BROWSER"):
            options["args"].append("--remote-debugging-port=9337")
        instance = runtime.chromium.launch(**options)
        try:
            yield instance
        finally:
            instance.close()


@pytest.mark.parametrize("mode", ["audio", "video"])
@pytest.mark.parametrize("empty_snapshot", [False, True], ids=["normal-join", "concurrent-join"])
def test_two_participant_media_and_controls(call_server, browser, mode, empty_snapshot, monkeypatch):
    if empty_snapshot:
        from framework.editor_identity import EditorIdentityStore

        original = EditorIdentityStore.join_call

        async def concurrent_snapshot(store, *args):
            # Reproduce two simultaneous PostgreSQL transactions observing no peers.
            await original(store, *args)
            return []

        async def concurrent_cursor(store, *args):
            # Both transactions read their signal cursor before either join commits.
            return 0

        monkeypatch.setattr(EditorIdentityStore, "join_call", concurrent_snapshot)
        monkeypatch.setattr(EditorIdentityStore, "current_signal_sequence", concurrent_cursor)
    client, base, area = call_server
    response = client.post("/calls", json={"area_id": area, "mode": mode})
    assert response.status_code == 201
    call = response.json()["id"]
    contexts = [browser.new_context(viewport={"width": 1100, "height": 760}) for _ in range(2)]
    pages = [context.new_page() for context in contexts]
    errors = []
    for page in pages:
        page.on("pageerror", lambda reason: errors.append(str(reason)))
        page.add_init_script("""window.__pcs=[]; const NativePC=window.RTCPeerConnection;
            window.RTCPeerConnection=class extends NativePC { constructor(...args){ super(...args); window.__pcs.push(this); } };""")
        ticket = client.post(f"/calls/{call}/ticket").json()["ticket"]
        page.goto(f"{base}/call-client/{call}#ticket={ticket}")
        until(page, "document.getElementById('state').textContent === 'Ready to join'")
        assert page.evaluate("location.hash") == ""
    assert client.get(f"/areas/{area}/calls").json()["calls"][0]["participants"] == 2
    # The newcomer offers before the first participant grants media permission.
    # The first page must queue that offer and its ICE candidates without losing them.
    pages[1].locator("#join").click()
    pages[1].wait_for_selector("#peer-local")
    pages[0].locator("#join").click()
    for page in pages:
        until(page, "window.__pcs.some(pc => pc.connectionState === 'connected')", timeout=20000)
        until(
            page,
            """async () => {
            for (const pc of window.__pcs) for (const report of (await pc.getStats()).values())
                if (report.type === 'inbound-rtp' && report.bytesReceived > 0) return true;
            return false;
        }""",
            timeout=20000,
        )
        assert page.locator("#videos .tile").count() == 2
        assert page.locator("#error").is_hidden(), page.locator("#error").text_content()
        if mode == "video":
            if page.locator("#sound").is_visible():
                page.locator("#sound").click()
            until(
                page,
                """async () => {
                for (const pc of window.__pcs) for (const report of (await pc.getStats()).values())
                    if (report.type === 'inbound-rtp' && report.kind === 'video' && report.framesDecoded > 0) return true;
                return false;
            }""",
                timeout=20000,
            )
            until(
                page,
                "document.querySelectorAll('#videos .has-video').length === 2 && Array.from(document.querySelectorAll('#videos video')).every(v=>v.readyState>=2)",
            )
    pages[0].locator("#mic").click()
    assert pages[0].locator("#mic").get_attribute("aria-pressed") == "true"
    assert pages[0].evaluate("document.querySelector('#peer-local video').srcObject.getAudioTracks().every(t=>!t.enabled)")
    pages[0].locator("#mic").click()
    if mode == "video":
        pages[0].locator("#camera").click()
        assert pages[0].evaluate("document.querySelector('#peer-local video').srcObject.getVideoTracks().every(t=>!t.enabled)")
        pages[0].locator("#camera").click()
    else:
        assert pages[0].locator("#camera").is_hidden()
    snapshots = os.environ.get("FORGE_BROWSER_SCREENSHOT_DIR")
    if snapshots:
        folder = Path(snapshots)
        folder.mkdir(parents=True, exist_ok=True)
        pages[0].screenshot(path=str(folder / f"call-{mode}-desktop.png"))
        pages[0].set_viewport_size({"width": 390, "height": 844})
        assert pages[0].evaluate("document.documentElement.scrollWidth <= innerWidth")
        pages[0].screenshot(path=str(folder / f"call-{mode}-mobile.png"))
    if os.environ.get("FORGE_AGENT_BROWSER"):
        result = subprocess.run(
            [os.environ["FORGE_AGENT_BROWSER"], "--session", "forge-call-review", "--cdp", "9337", "snapshot", "-i"],
            capture_output=True,
            text=True,
            timeout=60,
        )
        assert result.returncode == 0, result.stderr
        assert "Leave call" in result.stdout
        print(result.stdout)
    pages[0].locator("#leave").click()
    assert pages[0].locator("#state").text_content() == "Call ended"
    assert pages[0].evaluate("document.querySelector('#peer-local video').srcObject.getTracks().every(t=>t.readyState==='ended')")
    until(pages[1], "document.querySelectorAll('#videos .tile').length === 1")
    assert not errors, errors
    for context in contexts:
        context.close()


def test_permission_denial_and_missing_ticket_are_visible(call_server, browser):
    client, base, area = call_server
    call = client.post("/calls", json={"area_id": area, "mode": "audio"}).json()["id"]
    page = browser.new_page()
    page.goto(f"{base}/call-client/{call}")
    assert page.locator("#state").text_content() == "Call link unavailable"
    assert page.locator("#error").is_visible()
    page.add_init_script("navigator.mediaDevices.getUserMedia=()=>Promise.reject(new DOMException('denied','NotAllowedError'))")
    ticket = client.post(f"/calls/{call}/ticket").json()["ticket"]
    page.goto(f"{base}/call-client/{call}#ticket={ticket}")
    until(page, "document.getElementById('state').textContent === 'Ready to join'")
    page.locator("#join").click()
    until(page, "document.getElementById('state').textContent === 'Unable to join'")
    assert "permission was denied" in page.locator("#error").text_content()
    assert page.locator("#join").is_enabled()
    assert page.locator("#mic").is_disabled()
    page.close()


def test_authorization_timeout_does_not_wait_forever(call_server, browser):
    client, base, area = call_server
    call = client.post("/calls", json={"area_id": area, "mode": "audio"}).json()["id"]
    page = browser.new_page()
    page.clock.install()
    page.add_init_script("""window.WebSocket=class { static OPEN=1; readyState=0; close(){this.readyState=3;} };""")
    ticket = client.post(f"/calls/{call}/ticket").json()["ticket"]
    page.goto(f"{base}/call-client/{call}#ticket={ticket}")
    page.clock.fast_forward(16000)
    assert page.locator("#state").text_content() == "Unable to connect"
    assert "15 seconds" in page.locator("#error").text_content()
    assert page.locator("#join").is_hidden()
    page.close()


def test_broken_call_script_has_independent_fallback(call_server, browser, monkeypatch):
    import framework.editor_call_client as call_client

    monkeypatch.setattr(call_client, "CALL_SCRIPT", "function broken(")
    _, base, _ = call_server
    page = browser.new_page()
    page.clock.install()
    page.goto(f"{base}/call-client/test")
    page.clock.fast_forward(13000)
    assert page.locator("#state").text_content() == "Call client could not start"
    assert "Content-Security-Policy" in page.locator("#error").text_content()
    page.close()


def test_consumed_ticket_shows_connection_error(call_server, browser):
    client, base, area = call_server
    assert client.get("/capabilities").json()["call_client_revision"] == 2
    call = client.post("/calls", json={"area_id": area, "mode": "audio"}).json()["id"]
    ticket = client.post(f"/calls/{call}/ticket").json()["ticket"]
    first, second = browser.new_page(), browser.new_page()
    first.goto(f"{base}/call-client/{call}#ticket={ticket}")
    until(first, "document.getElementById('state').textContent === 'Ready to join'")
    second.goto(f"{base}/call-client/{call}#ticket={ticket}")
    until(second, "document.getElementById('state').textContent === 'Unable to connect'")
    assert second.locator("#error").is_visible()
    assert second.locator("#join").is_hidden()
    first.close()
    second.close()


def test_late_socket_error_does_not_override_user_leave(call_server, browser):
    client, base, area = call_server
    call = client.post("/calls", json={"area_id": area, "mode": "audio"}).json()["id"]
    page = browser.new_page()
    page.add_init_script("""window.WebSocket=class { static OPEN=1; readyState=0;
        constructor(){window.__socket=this;} close(){this.readyState=3;} };""")
    ticket = client.post(f"/calls/{call}/ticket").json()["ticket"]
    page.goto(f"{base}/call-client/{call}#ticket={ticket}")
    page.locator("#leave").click()
    page.evaluate("window.__socket.onerror()")
    assert page.locator("#state").text_content() == "Call ended"
    assert page.locator("#error").is_hidden()
    page.close()
