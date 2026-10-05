"""Browser-side call state machine. No credentials are persisted or logged."""

CALL_BOOTSTRAP_SCRIPT = r"""
(function () {
  setTimeout(function () {
    if (window.forgeCallClientStarted) return;
    document.getElementById('state').textContent = 'Call client could not start';
    document.getElementById('detail').textContent = 'Update the main server and reopen this call in a current browser.';
    var box = document.getElementById('error');
    box.textContent = 'The call script did not start. Check browser compatibility and the server Content-Security-Policy.';
    box.hidden = false;
  }, 12000);
})();
"""

CALL_SCRIPT = r"""
(() => {
  'use strict';
  window.forgeCallClientStarted = true;
  const $ = id => document.getElementById(id);
  const ticket = new URLSearchParams(location.hash.slice(1)).get('ticket') || '';
  history.replaceState(null, '', location.pathname);
  const peers = new Map(), names = new Map(), pending = [];
  let socket, stream, hello, mode = 'audio', ended = false, joining = false;
  let chain = Promise.resolve(), authorizationTimer;
  function phase(title, detail) { $('state').textContent = title; $('detail').textContent = detail; }
  function error(message) { $('error').textContent = message; $('error').hidden = false; }
  function send(value) { if (socket?.readyState === WebSocket.OPEN) socket.send(JSON.stringify(value)); }
  function removePeer(id) {
    const entry = peers.get(id); peers.delete(id); entry?.pc.close();
    document.getElementById('peer-' + id)?.remove(); updateCount();
  }
  function updateCount() { $('count').textContent = `${1 + peers.size} participant(s)`; }
  function cleanup() {
    clearTimeout(authorizationTimer);
    stream?.getTracks().forEach(track => track.stop()); stream = null;
    for (const id of Array.from(peers.keys())) removePeer(id);
    pending.length = 0;
  }
  function leave() {
    if (ended) return;
    ended = true; send({type:'hangup'}); cleanup(); socket?.close();
    phase('Call ended', 'Your microphone and camera are off. You can close this window.');
    $('join').hidden = true; $('mic').disabled = true; $('camera').disabled = true;
  }
  window.forgeCallLeave = leave;
  function connectionFailed(message) {
    if (ended) return;
    error(message); leave();
    phase('Unable to connect', 'Return to the Editor and request a new call after resolving the issue below.');
  }
  window.addEventListener('pagehide', leave);
  window.addEventListener('hashchange', () => {
    if (new URLSearchParams(location.hash.slice(1)).has('ticket')) { leave(); location.reload(); }
  });
  function tile(id, label, media, muted = false) {
    let box = document.getElementById('peer-' + id);
    if (box) {
      box.querySelector('video').srcObject = media;
      box.classList.toggle('has-video', media.getVideoTracks().length > 0);
      return;
    }
    box = document.createElement('section'); box.id = 'peer-' + id; box.className = 'tile';
    const avatar = document.createElement('div'); avatar.className = 'avatar'; avatar.textContent = label.slice(0, 1).toUpperCase();
    const name = document.createElement('div'); name.className = 'name'; name.textContent = label;
    const video = document.createElement('video'); video.autoplay = true; video.playsInline = true; video.muted = muted; video.srcObject = media;
    if (media.getVideoTracks().length) box.classList.add('has-video');
    video.play().catch(() => { $('sound').hidden = false; });
    box.append(avatar, video, name); $('videos').appendChild(box);
  }
  async function peer(id, initiate = false) {
    if (peers.has(id)) return peers.get(id);
    if (!stream) throw new Error('Join with your microphone before connecting media.');
    const pc = new RTCPeerConnection({iceServers:hello.ice_servers || []});
    const entry = {pc, ice:[]}; peers.set(id, entry); updateCount();
    stream.getTracks().forEach(track => pc.addTrack(track, stream));
    pc.ontrack = event => tile(id, names.get(id) || 'Team member', event.streams[0] || new MediaStream([event.track]));
    pc.onicecandidate = event => { if (event.candidate) send({type:'ice', target:id, candidate:event.candidate.toJSON()}); };
    pc.onconnectionstatechange = () => {
      if (!peers.has(id)) return;
      if (pc.connectionState === 'connected') phase('In call', 'Your media connection is ready. Use the controls below to mute or leave.');
      if (pc.connectionState === 'failed') { error('A participant could not connect. If you are on different networks, ask the server administrator to verify the TURN relay.'); removePeer(id); }
      else if (pc.connectionState === 'closed') removePeer(id);
    };
    if (initiate) {
      const offer = await pc.createOffer(); await pc.setLocalDescription(offer);
      send({type:'offer', target:id, sdp:pc.localDescription.sdp});
    }
    return entry;
  }
  async function flushIce(entry) {
    if (!entry.pc.remoteDescription) return;
    for (const candidate of entry.ice.splice(0)) await entry.pc.addIceCandidate(candidate);
  }
  async function signal(message) {
    if (ended) return;
    if (message.type === 'peers') {
      if (hello) return;
      clearTimeout(authorizationTimer); hello = message;
      mode = message.mode === 'video' ? 'video' : 'audio';
      for (const item of message.peers || []) names.set(item.connection_id, item.display_name || 'Team member');
      $('mode').textContent = mode === 'video' ? 'Video room' : 'Voice room';
      $('camera').hidden = mode !== 'video';
      $('join').hidden = false; $('join').disabled = false;
      $('join').textContent = mode === 'video' ? 'Join with microphone & camera' : 'Join with microphone';
      phase('Ready to join', 'Choose Join to grant microphone access. Other members can join this room from the Editor.');
      return;
    }
    if (message.type === 'heartbeat') return;
    if (message.type === 'peer_joined') {
      names.set(message.sender, message.display_name || 'Team member');
      // Stable offer ownership also covers concurrent joins with empty peer snapshots.
      if (stream && hello.connection_id < message.sender) await peer(message.sender, true);
      return;
    }
    if (message.type === 'peer_left') {
      names.delete(message.sender); removePeer(message.sender);
      for (let index = pending.length - 1; index >= 0; index--) {
        if (pending[index].sender === message.sender) pending.splice(index, 1);
      }
      return;
    }
    if (!['offer','answer','ice'].includes(message.type)) return;
    if (!stream) {
      if (pending.length >= 256) throw new Error('Too many queued call signals. Reopen the call from the Editor.');
      pending.push(message); return;
    }
    const entry = await peer(message.sender); const pc = entry.pc;
    if (message.type === 'offer') {
      await pc.setRemoteDescription({type:'offer', sdp:message.sdp}); await flushIce(entry);
      const answer = await pc.createAnswer(); await pc.setLocalDescription(answer);
      send({type:'answer', target:message.sender, sdp:pc.localDescription.sdp});
    } else if (message.type === 'answer') {
      await pc.setRemoteDescription({type:'answer', sdp:message.sdp}); await flushIce(entry);
    } else if (message.candidate) {
      if (pc.remoteDescription) await pc.addIceCandidate(message.candidate);
      else if (entry.ice.length < 128) entry.ice.push(message.candidate);
      else throw new Error('Too many ICE candidates. Reopen the call.');
    }
  }
  function enqueue(message) {
    // Preserve signaling order while permissions, SDP and ICE promises resolve.
    chain = chain.then(() => signal(message)).catch(reason => { error(reason.message || 'Media negotiation failed.'); });
    return chain;
  }
  async function join() {
    if (joining || stream || ended || !hello) return;
    joining = true; $('join').disabled = true; $('error').hidden = true;
    phase('Microphone / camera access', 'Allow the permission prompt. If it is hidden, check your browser or Windows privacy settings.');
    let expired = false, timer;
    try {
      if (!window.isSecureContext || !navigator.mediaDevices?.getUserMedia) throw new Error('Microphone access needs HTTPS or localhost. Open the server through HTTPS and request a new call link.');
      const request = navigator.mediaDevices.getUserMedia({audio:true, video:mode === 'video' ? {width:{ideal:1280}, height:{ideal:720}} : false});
      request.then(value => { if (expired || ended) value.getTracks().forEach(track => track.stop()); }, () => {});
      stream = await Promise.race([request, new Promise((_, reject) => { timer = setTimeout(() => { expired = true; reject(new Error('Permission is still pending. Allow microphone/camera access, then choose Join again.')); }, 30000); })]);
      if (ended) { cleanup(); return; }
      clearTimeout(timer); tile('local', 'You', stream, true);
      $('join').hidden = true; $('mic').disabled = false; $('camera').disabled = mode !== 'video';
      phase('In call', peers.size ? 'Connecting to participants…' : 'Waiting for others. They can choose Join call in this space.');
      for (const id of names.keys()) {
        if (hello.connection_id >= id || pending.some(packet => packet.sender === id && packet.type === 'offer')) continue;
        await peer(id, true);
      }
      for (const packet of pending.splice(0)) await enqueue(packet);
    } catch (reason) {
      expired = true; stream?.getTracks().forEach(track => track.stop()); stream = null;
      for (const id of Array.from(peers.keys())) removePeer(id);
      document.getElementById('peer-local')?.remove();
      $('join').hidden = false; $('mic').disabled = true; $('camera').disabled = true;
      phase('Unable to join', 'Resolve the issue below and choose Join again.');
      const messages = {NotAllowedError:'Microphone/camera permission was denied. Allow access in browser and Windows privacy settings.', NotFoundError:'No microphone or camera was found. Connect a device and retry.', NotReadableError:'Another application may be using the device. Close it and retry.'};
      error(messages[reason.name] || reason.message || 'Could not start media.');
    } finally { clearTimeout(timer); joining = false; if (!ended) $('join').disabled = false; }
  }
  $('join').onclick = join;
  $('mic').onclick = () => {
    if (!stream) return;
    const tracks = stream.getAudioTracks(), enabled = !tracks[0]?.enabled;
    tracks.forEach(track => { track.enabled = enabled; });
    $('mic').textContent = enabled ? 'Mute microphone' : 'Unmute microphone';
    $('mic').setAttribute('aria-pressed', String(!enabled));
  };
  $('camera').onclick = () => {
    if (!stream) return;
    const tracks = stream.getVideoTracks(), enabled = !tracks[0]?.enabled;
    tracks.forEach(track => { track.enabled = enabled; });
    $('camera').textContent = enabled ? 'Disable camera' : 'Enable camera';
    document.getElementById('peer-local')?.classList.toggle('has-video', enabled);
  };
  $('leave').onclick = leave;
  $('sound').onclick = () => { document.querySelectorAll('video').forEach(video => { video.play().catch(() => error('Audio playback is blocked. Check your browser sound settings.')); }); $('sound').hidden = true; };
  if (!ticket) { phase('Call link unavailable', 'Return to the Editor and request a new call.'); error('The one-time authorization ticket is missing.'); return; }
  authorizationTimer = setTimeout(() => { if (!hello && !ended) connectionFailed('The server did not authorize this call within 15 seconds. Check the connection and reopen the call from the Editor.'); }, 15000);
  try {
    const scheme = location.protocol === 'https:' ? 'wss:' : 'ws:';
    socket = new WebSocket(scheme + '//' + location.host + location.pathname.replace('/call-client/', '/ws/calls/'), ['forge-call-v1', ticket]);
    socket.onopen = () => phase('Checking call access', 'The server is validating your one-time ticket…');
    socket.onmessage = event => { try { enqueue(JSON.parse(event.data)); } catch { error('The server returned an invalid call signal.'); leave(); } };
    socket.onerror = () => connectionFailed('Could not reach the call connection. Verify the server and reverse-proxy WebSocket configuration.');
    socket.onclose = event => { if (!ended) connectionFailed(event.code === 1008 ? 'Call authorization was denied or the ticket expired. Request a new call link.' : 'The call connection closed. Reopen the call to reconnect.'); };
  } catch (reason) { connectionFailed(reason.message || 'Call connection failed.'); }
})();
"""
