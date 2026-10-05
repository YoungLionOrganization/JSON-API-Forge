from __future__ import annotations

import html
import json
import secrets
from typing import Any

from .editor_call_script import CALL_BOOTSTRAP_SCRIPT, CALL_SCRIPT


def parse_ice_servers(raw: str) -> list[dict[str, Any]]:
    """Parse a bounded WebRTC ICE configuration without accepting arbitrary URLs."""
    try:
        value = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise RuntimeError("EDITOR_CALL_ICE_SERVERS_JSON must be valid JSON") from exc
    if not isinstance(value, list) or not 1 <= len(value) <= 8:
        raise RuntimeError("EDITOR_CALL_ICE_SERVERS_JSON must contain 1-8 server objects")
    result: list[dict[str, Any]] = []
    for item in value:
        if not isinstance(item, dict) or set(item) - {"urls", "username", "credential"}:
            raise RuntimeError("Each ICE server may contain only urls, username and credential")
        urls = item.get("urls")
        candidates = [urls] if isinstance(urls, str) else urls
        if not isinstance(candidates, list) or not 1 <= len(candidates) <= 8 or not all(isinstance(url, str) for url in candidates):
            raise RuntimeError("Each ICE server requires one or more URL strings")
        if any(
            not url
            or len(url) > 512
            or not url.lower().startswith(("stun:", "turn:", "turns:"))
            or not url.split(":", 1)[1]
            or "\\" in url
            or any(character.isspace() or ord(character) == 0x7F for character in url)
            for url in candidates
        ):
            raise RuntimeError("ICE URLs must use stun:, turn: or turns: and be at most 512 characters")
        server: dict[str, Any] = {"urls": urls}
        for field in ("username", "credential"):
            field_value = item.get(field)
            if field_value is not None:
                if (
                    not isinstance(field_value, str)
                    or len(field_value) > 1024
                    or any(ord(character) < 0x20 or ord(character) == 0x7F for character in field_value)
                ):
                    raise RuntimeError(f"ICE {field} must be a string of at most 1024 characters")
                server[field] = field_value
        result.append(server)
    return result


def call_client_page() -> tuple[str, str]:
    """Return the self-contained WebRTC client and its per-response CSP nonce."""
    nonce = secrets.token_urlsafe(18)
    document = f"""<!doctype html>
<html lang="en" data-forge-call-client="2"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><title>Forge call</title>
<style>
:root{{--amber:#f2b84b;--text:#eceef2;--muted:#a6a9b2}}*{{box-sizing:border-box}}[hidden]{{display:none!important}}
body{{margin:0;background:#202225;color:var(--text);font:15px 'Segoe UI',sans-serif}}header{{display:flex;justify-content:space-between;align-items:center;padding:18px 24px;background:#282b30;border-bottom:1px solid #41444b}}h1{{font-size:18px;margin:0}}#mode,#count{{color:var(--muted);font-size:13px}}main{{max-width:1200px;margin:auto;padding:28px 24px 130px}}.status{{background:#292d34;border:1px solid #444950;border-radius:14px;padding:24px;margin-bottom:20px;text-align:center}}#state{{margin:0 0 10px;font-size:25px;color:var(--amber)}}#detail{{color:var(--muted);line-height:1.6;margin:0 auto 18px;max-width:680px}}#error{{margin:12px 0;padding:14px;background:#502b32;border-radius:9px;color:#ffdbe1;line-height:1.5}}#videos{{display:grid;grid-template-columns:repeat(auto-fit,minmax(220px,1fr));gap:16px}}.tile{{position:relative;min-height:230px;background:#30323b;border:1px solid #464956;border-radius:14px;overflow:hidden;display:flex;align-items:center;justify-content:center}}.avatar{{display:grid;place-items:center;width:88px;height:88px;border-radius:50%;background:#746040;color:#fff0cc;font-size:38px;font-weight:bold}}video{{position:absolute;width:1px;height:1px;opacity:0}}.has-video video{{width:100%;height:100%;object-fit:cover;opacity:1}}.name{{position:absolute;bottom:12px;left:12px;padding:5px 9px;border-radius:7px;background:#17181add}}footer{{position:fixed;bottom:0;left:0;right:0;padding:18px;background:#25272def;border-top:1px solid #454953;display:flex;justify-content:center;gap:10px;flex-wrap:wrap}}button{{padding:12px 18px;border:1px solid #505560;border-radius:9px;background:#363a43;color:var(--text);font:600 14px 'Segoe UI',sans-serif;cursor:pointer}}button:hover{{border-color:var(--amber)}}button:focus-visible{{outline:2px solid var(--amber)}}button:disabled{{opacity:.5;cursor:default}}.primary{{background:var(--amber);color:#212225;border-color:var(--amber)}}.danger{{background:#a83946;border-color:#bf5260}}@media(max-width:540px){{main{{padding:15px 12px 145px}}header{{padding:14px}}#state{{font-size:21px}}.status{{padding:18px 12px}}footer{{padding:12px}}button{{padding:10px 12px}}}}
</style></head><body><header><div><h1>Forge team call</h1><span id="mode">Private voice / video room</span></div><span id="count">Preparing your room</span></header><main><noscript>JavaScript is disabled. Enable it or open this call in a current browser.</noscript><section class="status" aria-live="polite"><h2 id="state">Connecting to your call</h2><p id="detail">Checking your connection and call access…</p><button id="join" class="primary" hidden disabled>Join with microphone</button><div id="error" role="alert" hidden></div></section><div id="videos"></div></main><footer><button id="mic" disabled>Mute microphone</button><button id="camera" hidden disabled>Disable camera</button><button id="sound" hidden>Enable sound</button><button id="leave" class="danger">Leave call</button></footer><script nonce="{html.escape(nonce)}">{CALL_BOOTSTRAP_SCRIPT}</script><script nonce="{html.escape(nonce)}">{CALL_SCRIPT}</script></body></html>"""
    return document, nonce
