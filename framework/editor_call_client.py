from __future__ import annotations

import html
import json
import secrets
from typing import Any


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
<html lang="en">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width,initial-scale=1">
  <title>JSON API Forge call</title>
  <style>
    :root{{--amber:#f2b84b;--amber2:#ffd071;--graphite:#202225;--panel:#2a2d31;--line:#41454b;--text:#f2f0ea;--muted:#aaa69d}}
    *{{box-sizing:border-box}} body{{margin:0;background:#202225;color:var(--text);font:14px Inter,Segoe UI,sans-serif}}
    header{{display:flex;align-items:center;gap:12px;padding:14px 18px;border-bottom:1px solid var(--line);background:#26282c}}
    .mark{{color:var(--amber);font-size:24px;font-weight:900}} .title{{font-weight:750}} .state{{margin-left:auto;color:var(--muted)}}
    main{{padding:18px}} #videos{{display:grid;grid-template-columns:repeat(auto-fit,minmax(260px,1fr));gap:14px}}
    .tile{{position:relative;min-height:190px;border:1px solid var(--line);border-radius:12px;overflow:hidden;background:#17181a}}
    video{{width:100%;height:100%;min-height:190px;object-fit:cover;background:#17181a}}
    .name{{position:absolute;left:10px;bottom:9px;padding:5px 8px;border-radius:7px;background:#111b;color:#fff}}
    footer{{position:fixed;left:0;right:0;bottom:0;display:flex;justify-content:center;gap:9px;padding:13px;background:#26282cee;border-top:1px solid var(--line)}}
    button{{min-width:110px;padding:10px 14px;border-radius:9px;border:1px solid var(--line);background:#32353a;color:var(--text);font-weight:700;cursor:pointer}}
    button:hover{{border-color:var(--amber)}} button.primary{{background:var(--amber);border-color:var(--amber2);color:#202225}} button.danger{{background:#603034;border-color:#8b454b}}
    #error{{display:none;margin:18px;padding:12px;border-radius:9px;background:#512d31;color:#ffd9dd}}
  </style>
</head>
<body>
  <header><div class="mark">{{F}}</div><div><div class="title">JSON API Forge secure call</div><div id="mode">Authenticated WebRTC workspace</div></div><div class="state" id="state">Waiting for secure authorization…</div></header>
  <div id="error"></div><main><div id="videos"></div></main>
  <footer><button id="mic">Mute microphone</button><button id="camera">Disable camera</button><button class="danger" id="leave">Leave</button></footer>
  <script nonce="{html.escape(nonce)}">
  (() => {{
    'use strict';
    const fragment=new URLSearchParams(location.hash.slice(1));
    const ticket=fragment.get('ticket')||'';
    history.replaceState(null,'',location.pathname);
    const state=document.getElementById('state'), error=document.getElementById('error'), videos=document.getElementById('videos');
    const peers=new Map(); let socket=null, localStream=null, iceServers=[], mode='audio';
    document.getElementById('mode').textContent=mode==='video'?'Video · peer-to-peer encrypted media':'Audio · peer-to-peer encrypted media';
    function fail(message){{error.textContent=message;error.style.display='block';state.textContent='Disconnected';}}
    function tile(id,label,stream,muted=false){{
      let box=document.getElementById('peer-'+id); if(box) return box.querySelector('video');
      box=document.createElement('div');box.className='tile';box.id='peer-'+id;
      const video=document.createElement('video');video.autoplay=true;video.playsInline=true;video.muted=muted;video.srcObject=stream;
      const name=document.createElement('div');name.className='name';name.textContent=label;
      box.append(video,name);videos.appendChild(box);return video;
    }}
    function send(value){{if(socket&&socket.readyState===WebSocket.OPEN) socket.send(JSON.stringify(value));}}
    async function peer(id, initiate=false){{
      if(peers.has(id)) return peers.get(id);
      const pc=new RTCPeerConnection({{iceServers}});peers.set(id,pc);
      localStream.getTracks().forEach(track=>pc.addTrack(track,localStream));
      pc.ontrack=e=>tile(id,'Team member',e.streams[0]);
      pc.onicecandidate=e=>{{if(e.candidate)send({{type:'ice',target:id,candidate:e.candidate.toJSON()}})}};
      pc.onconnectionstatechange=()=>{{if(['failed','closed','disconnected'].includes(pc.connectionState)){{document.getElementById('peer-'+id)?.remove();peers.delete(id)}}}};
      if(initiate){{const offer=await pc.createOffer();await pc.setLocalDescription(offer);send({{type:'offer',target:id,sdp:offer.sdp}})}}
      return pc;
    }}
    async function signal(message){{
      if(message.type==='peers'){{
        if(localStream)return;
        mode=message.mode==='video'?'video':'audio';iceServers=Array.isArray(message.ice_servers)?message.ice_servers:[];
        document.getElementById('mode').textContent=mode==='video'?'Video · peer-to-peer encrypted media':'Audio · peer-to-peer encrypted media';
        localStream=await navigator.mediaDevices.getUserMedia({{audio:true,video:mode==='video'}});tile('local','You',localStream,true);
        if(mode!=='video')document.getElementById('camera').style.display='none';
        for(const item of message.peers||[])await peer(item.connection_id,true);
        state.textContent='Connected · media stays between participants';return;
      }}
      if(message.type==='peer_joined'||message.type==='heartbeat')return;
      if(message.type==='peer_left'){{peers.get(message.sender)?.close();peers.delete(message.sender);document.getElementById('peer-'+message.sender)?.remove();return}}
      const pc=await peer(message.sender,false);
      if(message.type==='offer'){{await pc.setRemoteDescription({{type:'offer',sdp:message.sdp}});const answer=await pc.createAnswer();await pc.setLocalDescription(answer);send({{type:'answer',target:message.sender,sdp:answer.sdp}})}}
      else if(message.type==='answer')await pc.setRemoteDescription({{type:'answer',sdp:message.sdp}});
      else if(message.type==='ice'&&message.candidate)await pc.addIceCandidate(message.candidate);
    }}
    async function start(){{
      if(!ticket)throw new Error('This one-time call link is missing its ticket. Request a new link from the Editor.');
      const scheme=location.protocol==='https:'?'wss:':'ws:';
      socket=new WebSocket(
        scheme+'//'+location.host+location.pathname.replace('/call-client/','/ws/calls/'),
        ['forge-call-v1',ticket]
      );
      socket.onopen=()=>state.textContent='Authorizing one-time ticket…';
      socket.onmessage=event=>{{try{{signal(JSON.parse(event.data)).catch(e=>{{socket?.close();fail(e.message)}})}}catch(e){{socket?.close();fail('Invalid signaling message')}}}};
      socket.onerror=()=>fail('The secure signaling connection failed.');
      socket.onclose=()=>{{if(state.textContent.startsWith('Connected'))state.textContent='Call ended'}};
    }}
    document.getElementById('mic').onclick=e=>{{const t=localStream?.getAudioTracks()[0];if(t){{t.enabled=!t.enabled;e.target.textContent=t.enabled?'Mute microphone':'Unmute microphone'}}}};
    document.getElementById('camera').onclick=e=>{{const t=localStream?.getVideoTracks()[0];if(t){{t.enabled=!t.enabled;e.target.textContent=t.enabled?'Disable camera':'Enable camera'}}}};
    document.getElementById('leave').onclick=()=>{{socket?.close();localStream?.getTracks().forEach(t=>t.stop());peers.forEach(p=>p.close());location.replace('about:blank')}};
    start().catch(e=>fail(e.message||'Camera or microphone permission was denied.'));
  }})();
  </script>
</body></html>"""
    return document, nonce
