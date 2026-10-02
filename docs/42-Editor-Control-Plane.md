# Editor control plane and team workspace

The Qt Editor connects to the separately protected management surface at `/__forge/editor/v1`. The surface is absent unless `EDITOR_API_ENABLED=true`. Application API keys, project bootstrap keys, JWTs and `OPERATOR_TOKEN` cannot authenticate it.

## Secure first-time setup

```bash
forge init --editor --production
forge migrate
```

`EDITOR_TOKEN` is a one-time founder-setup secret. The founder creates an account through `POST /setup/founder`; after that transaction commits, the same setup secret cannot create another founder. Immediately set `EDITOR_SETUP_ENABLED=false`, remove `EDITOR_TOKEN` from the process environment and restart. Worker access uses short-lived `Authorization: Bearer jfe_session_…` sessions. Session tokens are stored only as SHA-256 hashes, have absolute and idle expiry, are bound to the client user-agent by default, and are revoked when a member is disabled. The desktop Editor keeps the session in memory and never writes a password or session token to its settings file.

The pre-v0.5 shared `X-Forge-Editor-Token` behavior exists only behind `EDITOR_LEGACY_TOKEN_ENABLED=true` for local migration tests. It is rejected when `APP_ENV=production`.

At minimum, configure:

```dotenv
EDITOR_API_ENABLED=true
EDITOR_TOKEN=<random one-time setup secret, 32+ characters>
EDITOR_SETUP_ENABLED=true
EDITOR_REQUIRE_HTTPS=true
EDITOR_ALLOWED_IPS=10.20.0.0/16,2001:db8:1234::/48
EDITOR_TRUSTED_PROXY_CIDRS=10.0.0.0/8
EDITOR_TRUSTED_HOSTS=forge-admin.example.com
EDITOR_LEGACY_TOKEN_ENABLED=false
EDITOR_ALLOW_CREATE_PROJECTS=false
EDITOR_ALLOW_HOOKS=false
EDITOR_ALLOW_GRAPHS=true
EDITOR_ALLOWED_PROJECTS=Billing API,Internal Portal
EDITOR_CALL_ICE_SERVERS_JSON=[{"urls":"turns:turn.example.com:5349","username":"short-lived-user","credential":"short-lived-secret"}]
```

Forwarded protocol and client-IP headers are honored only when the direct peer belongs to `EDITOR_TRUSTED_PROXY_CIDRS`. The Host header must match `EDITOR_TRUSTED_HOSTS`. The default IP and Host policies allow loopback only. Production startup refuses a weak secret while setup is enabled, disabled HTTPS policy, or legacy shared-token mode. Once setup is disabled, `forge doctor` warns if the now-unused setup secret is still present.

## Ranks, roles and founder restrictions

The support schema seeds immutable `Founder`, `Administrator`, `Developer`, `Analyst`, `Collaborator` and `Viewer` roles. The founder can create lower custom roles with:

- an integer rank;
- explicit permissions;
- document allow/deny globs;
- database alias/table allow globs;
- global or project-specific memberships.

A role manager cannot create a role at or above their own rank, grant a permission they do not hold, or widen their own document/database scope. Delegated wildcard scopes must exactly match a wildcard the manager already holds; literal child paths may be contained by a parent glob. Every delegated role must also preserve all document-deny patterns inherited by the manager. This prevents glob characters or an omitted deny rule from widening delegated authority.

The founder account cannot be disabled, deleted or reassigned through the remote API. New workers receive exact-format, single-use, expiring invitation tokens and choose their own passwords; an administrator never needs to know a worker password. Each invitation records a fingerprint of every assigned role. Updating a custom role or changing/disabling its issuing member revokes pending invitations, and registration rechecks that the active issuer still has global invitation authority before doing password hashing.

Every project, document and database request resolves access again from current memberships. A UI control being disabled is never treated as authorization. `hooks/*.py` additionally requires both the server-wide `EDITOR_ALLOW_HOOKS=true` policy and `documents.hooks.write`; graph writes similarly require the graph policy and permission.

## Database explorer

The Editor database browser does not accept SQL. It exposes metadata and bounded, read-only `SELECT` pagination for declared Forge resources. Resource `hidden_fields` are never returned and `readable_fields` remains authoritative. Support/undeclared tables stay invisible unless `EDITOR_DATABASE_EXPOSE_UNDECLARED=true` **and** the role has `databases.undeclared.read`. Responses cap row counts, offsets, cell sizes, nested JSON depth and binary previews.

## Team profiles, areas, notes and files

Authenticated members have server-side profiles and can use:

- open project areas visible to every authorized project member;
- restricted areas gated by rank and/or role;
- bounded messages and announcements;
- open, restricted or private notes;
- attachment uploads stored outside application configuration with random storage names and SHA-256 metadata;
- an Editor audit stream for sensitive management actions.

Visible-area members can list attachment metadata, but the random server storage name is never exposed. Attachments are always downloaded through the authorized endpoint with attachment disposition and `nosniff`. The configured attachment root may not be a symlink. Do not place it under a public web root.

## Voice and video

Calls use a self-contained WebRTC client. Media is DTLS-SRTP peer-to-peer; Forge stores no audio or video. The server provides only short-lived, single-use call tickets and bounded SDP/ICE signaling. Tickets arrive at the page in the URL fragment, which is not sent with the HTTP request, and the page immediately removes that fragment from browser history. The WebSocket then carries the exact-format ticket in its `Sec-WebSocket-Protocol` offer alongside `forge-call-v1`; query-string tickets are rejected so access logs and proxy request targets cannot capture them. A nonce-based Content Security Policy and a call-only camera/microphone Permissions Policy protect the client.

Call signaling is database-backed so participants connected to different application workers can exchange signals. Raw signaling frames are capped before JSON parsing, non-text/invalid frames are policy-closed, and Uvicorn applies a 1 MiB protocol ceiling. `EDITOR_CALL_ICE_SERVERS_JSON` accepts only a bounded list of `stun:`, `turn:` and `turns:` URLs with optional string credentials. For reliable internet calls, deploy an organization-controlled TURN service, prefer short-lived credentials, and keep the management hostname on HTTPS/WSS. Passenger/WSGI hosting cannot provide WebSockets; calls require a native ASGI process or a separate ASGI management deployment.

## Concurrent and hostile edits

Documents are limited to `app.json`, direct `config/*.json`, separately enabled `graphs/*.forgegraph.json`, and separately enabled `hooks/*.py`. `.env`, secrets, arbitrary source paths, traversal segments and symlinks are rejected. A save must provide the exact current SHA-256 revision. The server then:

1. takes an in-process lock and a cross-process file lock;
2. rejects symlinked control directories/documents;
3. copies only allowlisted control files into a staging directory;
4. validates the complete staged project;
5. writes and `fsync`s a temporary file;
6. atomically replaces the live document and `fsync`s its directory.

Invalid JSON or project configuration never overwrites the live document. File locks cover workers sharing one filesystem; clustered hosts still need shared configuration storage or a single designated management writer.

## Deployment boundary

Expose the prefix on a private administration hostname or VPN, terminate TLS at a trusted reverse proxy, keep the application IP allowlist **and** an edge firewall policy, and run `forge doctor --production`. Never publish the management prefix directly to the internet with an empty IP policy. Keep remote hook editing off unless every holder of that permission is trusted to execute code as the Forge process account.
