# JSON API Forge Editor v0.5.2

JSON API Forge Editor is a C++20/Qt 6 desktop workspace for authoring and operating Forge projects. It combines direct JSON editing, typed visual forms and an Unreal-inspired operation graph without introducing a second runtime language: the graph compiles to ordinary Forge configuration and the server still applies its normal schema, semantic and policy checks.

## Authoring modes

- **Code** provides line numbers, JSON highlighting, current-line focus and direct access to every supported local or remote document.
- **Visual** provides a searchable component library (SQL resources, RPC operations, static/HTTP/file data sources and realtime channels), a recursive document outline and a typed property inspector. Edit nested values, add or remove fields/items, duplicate components with unique names, reorder arrays and undo/redo up to 30 changes. Double-click an object or array value to edit its JSON with validation before saving.
- **Graph** provides a grid canvas with pan/zoom, marquee selection, draggable nodes, output-to-input Bézier wires, cycle/fan-in validation, automatic layout, fit-to-content and a live compiled-operation preview.

The graph palette includes Request, Authorization, SQL Query, SQL Mutation, Branch, Transform, Forge Operation, Python SDK, Event and Response nodes. Native plugins can register additional node types through Plugin API v2. Branch/Transform/Event and custom plugin nodes remain explicit design nodes until a compiler exists for their semantics; compile preview reports them instead of silently dropping behavior. Graphs are stored as bounded, schema-versioned `graphs/*.forgegraph.json` documents and target one direct `config/*.json` fragment.

## Project templates

Use **File → New from template…** after opening a local workspace. Creation is staged in a temporary directory and atomically published only after every file succeeds. Each template includes a manifest, database/security/resource/operation/event fragments, an editable graph and its own runbook.

| Template | Focus |
|---|---|
| Secure Operations Hub | Hardened CRUD, permissions, cache, events and analytics |
| SaaS Control Plane | Tenant subscription and lifecycle operations |
| Event-Driven Commerce | Order workflow and idempotent processing foundation |
| Analytics Read Model | Bounded aggregate/read-model API |
| Forge Plugin Registry | Forge-backed Editor plugin catalog metadata |
| Python Enterprise Gateway | Multi-region Python SDK integration foundation |
| Media Operations | Media metadata and operational lifecycle |
| Audited Ledger Workflow | Controlled transitions and audit-oriented records |

## Local and remote workspaces

The navigation sidebar animates together with the document area. The application title displays **v0.5.2**.

**Spaces & calls** stays available while the server loads and when access fails. Its banner explains missing collaboration support, server-disabled features and access errors; **Retry** reloads project access and features. With a current server and an authorized account, select a space (or create one with **New area**) to enable messages and calls. The message composer stays pinned, and **Shared files** expands the file list when needed. If the banner reports an older server, update the running server from `main`; upgrading only the Editor cannot add server endpoints.

Choose **File → Open local workspace…** and select a Forge repository root, its `app/` directory or one project directory. Local JSON/graph saves use `QSaveFile` atomic replacement. The Editor performs structural checks; `forge validate` remains the canonical merged-project and semantic validator.

For remote administration, enable the account control plane only on a private endpoint:

```dotenv
EDITOR_API_ENABLED=true
EDITOR_TOKEN=<one-time-founder-setup-secret-with-32+-characters>
EDITOR_SETUP_ENABLED=true
EDITOR_REQUIRE_HTTPS=true
EDITOR_ALLOWED_IPS=10.20.0.0/16
EDITOR_TRUSTED_PROXY_CIDRS=10.0.0.0/8
EDITOR_TRUSTED_HOSTS=forge-admin.example.com
EDITOR_ALLOWED_PROJECTS=Billing,InternalPortal
EDITOR_READ_ONLY=false
EDITOR_ALLOW_CREATE_PROJECTS=false
EDITOR_ALLOW_HOOKS=false
EDITOR_ALLOW_GRAPHS=true
EDITOR_DATABASE_BROWSER_ENABLED=true
EDITOR_COLLABORATION_ENABLED=true
EDITOR_CALLS_ENABLED=true
EDITOR_ATTACHMENT_DIR=/srv/json-api-forge/editor-attachments
EDITOR_CALL_ICE_SERVERS_JSON=[{"urls":["turns:turn.example.com:5349"],"username":"forge","credential":"replace-me"}]
```

Use the Editor's one-time founder setup page once, then set `EDITOR_SETUP_ENABLED=false`, remove `EDITOR_TOKEN` from the environment and restart. Workers join with single-use, expiring invitations and then sign in with their own account. Passwords and bearer sessions stay in memory; only non-secret server history/preferences are saved. Redirects, certificate failures and ambient desktop proxies are rejected. Plain HTTP requires a visible local-development opt-in and is restricted to loopback addresses. Keep the endpoint behind a VPN/private administration host and firewall. Python hook editing remains a separate, high-risk policy.

Open **Edit → Settings…** to tune bounded request/authentication/upload/download timeouts, safe GET retry/backoff, maximum JSON size, server history, discard/layout confirmations, right-drag panning and zoom sensitivity. Mutation requests are never retried automatically. Canceling an active request is explicit, and reconnect/session-expiry paths preserve unsaved documents whenever the server identity is unchanged.

Open **Integrations → Team Workspace** for the server-backed operational console:

- edit the signed-in member profile;
- create lower-ranked roles from explicit permissions and document/database patterns;
- replace worker memberships, project scopes and active status in one server transaction;
- use open or restricted project spaces, chat, scoped notes and policy-filtered file sharing;
- browse only runtime-declared database resources and only the fields allowed by Forge policy;
- start audio/video rooms through one-time WebRTC tickets and inspect the append-only security audit.

Qt WebEngine embeds the call surface with an off-the-record profile when available. Builds without it open the same-origin one-time URL in the system browser. Production calls need a configured TURN service, and WebSocket-capable native ASGI hosting; conventional Passenger/cPanel HTTP hosting does not provide the call transport.

Remote graph support is deliberately metadata-only. The server checks document size, exact fields, node/edge identities, target path, unique input fan-in and acyclic execution. Compiled JSON must still pass normal Forge validation before deployment.

## Python SDK integration

Open **Integrations → Python SDK panel** to generate secret-safe snippets for:

- synchronous and asynchronous clients;
- bounded pagination and retry policy;
- multi-region rendezvous routing, failover and circuit breaking;
- YoungLion-native payloads;
- DDM conversion adapters.

The panel reads the API key from a named environment variable, invokes Python with fixed arguments rather than a shell, can verify the installed SDK and can run a bounded health check. Install the SDK from the `python-library` branch (or later from PyPI as `json-api-forge-client`); optional `younglion` and `ddm` extras remain lazy.

## Plugins and Forge catalog

Local native plugins can contribute palette components, graph node types, actions and dock widgets. A manifest must use API v2, declare a lowercase SHA-256 digest and list its requested permissions. The Editor constrains libraries to approved plugin directories, rejects symlinks and identity/API mismatches, verifies the binary digest before loading, and requires explicit per-ID enablement.

**Plugins → Import plugin from ZIP…** stages one reviewed package in the per-user plugin directory. The importer rejects traversal/absolute/control-character paths, symlinks, duplicate/case-colliding paths, archive bombs, excessive entries/sizes, bad ZIP CRCs, invalid manifests, digest mismatches and incompatible native libraries. Import never enables or loads the plugin; review it separately in **Manage plugins…**.

**Plugins → Browse Forge plugin catalog…** reads release metadata from a normal JSON API Forge resource (`/api/<project>/v1/<resource>`). This makes Forge itself the catalog/control plane: the response is bounded and every record's ID, version, HTTPS package URL, digest and permissions are validated. The Editor does not silently download or execute catalog code; an operator reviews the record and copies the package URL before installing the verified native bundle. See `editor/plugins/README.md` for the contracts.

Native plugins execute with the desktop user's privileges. Hash verification detects package changes but is not publisher identity; use signed releases and a trusted distribution channel.

## Build locally

Prerequisites are CMake 3.24+, a C++20 compiler, Qt 6.4+ with Core/Gui/Widgets/Network/Test using public APIs and the bundled bounded ZIP decoder, preferably WebEngineWidgets for embedded calls, and Ninja.

```bash
cmake -S . -B build/editor -G Ninja \
  -DCMAKE_BUILD_TYPE=Release \
  -DBUILD_TESTING=ON \
  -DFORGE_EDITOR_WARNINGS_AS_ERRORS=ON
cmake --build build/editor --parallel
ctest --test-dir build/editor --output-on-failure
cmake --install build/editor --prefix build/stage
QT_QPA_PLATFORM=offscreen build/editor/editor/JSON-API-Forge-Editor \
  --graph-preview --screenshot build/editor-graph-preview.png
```

The original background logo remains the application/window/platform icon. The transparent, text-free Forge mark is used inside the Amber Gold + Graphite Gray interface. Linux installs include a desktop entry and hicolor icon.

For a complete Qt Creator kit and preset walkthrough, local portable ZIP steps,
and the same Qt Installer Framework setup command used by CI, see
[QT-CREATOR-BUILD-GUIDE.md](QT-CREATOR-BUILD-GUIDE.md).

## CI artifacts

The `Editor build` workflow builds and tests six native targets with warnings-as-errors:

- Linux x64 and ARM64 (`ubuntu-24.04`, `ubuntu-24.04-arm`)
- Windows x64 and ARM64 (`windows-2022`, `windows-11-arm`)
- macOS x64 and ARM64 (`macos-15-intel`, `macos-15`)

Every job stages a multi-file application and renders a packaged-app screenshot. Linux bundles the Qt runtime and produces ZIP + Qt Installer Framework setup RUN, Windows runs `windeployqt` and produces ZIP + Qt IFW setup EXE, and macOS runs `macdeployqt` and produces ZIP + Qt IFW setup DMG. The workflow downloads official IFW 4.8.1 tools for the matching platform, verifies their pinned SHA-256 values before execution, and makes every installer present/install the project `LICENSE`. Every archive/installer receives a SHA-256 sidecar. A final job verifies all twelve deliverables and publishes one all-platform bundle. Workflows never create a release or push generated binaries.

## Sending messages and joining calls

The placeholder names the selected space. Enter sends a nonempty message;
Shift+Enter adds a line. The input grows for wrapped text and preserves the
next draft while a previous send is pending. Failed sends keep the draft.
The separate hint shows send state and the character count near the limit.

Calls require the current main server advertising `call_client_revision: 2`.
An older page showing “Waiting for secure authorization…” comes from the
server, so updating just the desktop application is insufficient. Update main,
restart its running process/container/service and request a new call ticket.
A current page reports WebSocket, expired-ticket and script startup problems.
