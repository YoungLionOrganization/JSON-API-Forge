# Changelog

## v0.5.2 (in development)

Editor capabilities and profile requests now accept an optional project scope. Authenticated project-only members can discover projects before selecting one, then load their effective project permissions without inheriting server-wide account administration privileges. Regression coverage checks allowed and denied scopes.

Cache policy isolation, atomic heterogeneous batches, ISO datetimes and nullable cursors, media cleanup and signed-token validation, non-finite parameter rejection, portable BIGINT declarations, bounded DNS/JWKS/readiness waits, app-scoped JWKS clients, reflected-table reuse and stage latency metrics. Manual server validate/draft/publish workflow verifies exact commit/run-attempt provenance, latest CI/CodeQL/platform gates, complete checksums and remote digests. Latency follow-up cancels the actual ASGI endpoint before reusing admission slots, isolates timed-out DNS work from file/hook executors, lazily creates HTTP clients, limits auto-create checks to the current table and adds optional HTTP source total deadlines. See `release/notes/v0.5.2.md` for compatibility details and `docs/performance/v0.5.2-latency-analysis.md` for measured results and remaining limits.


## [0.5.1] — 2026-09-06

### Security and correctness
- Isolated app manifest, route and startup failures so healthy apps remain available; readiness reports degraded apps without exposing exception contents.
- Added automatic development reload for app configuration, hooks and new directories while excluding runtime data writes.
- Confined file data operations with descriptor-relative traversal where available, bounded reads, atomic replacement and non-overwriting media publication.
- Precomputed app prefix lookup and retained strict CLI validation for deployment.
- Routed external JWKS retrieval through the DNS-rebinding-resistant egress transport, blocked private/link-local targets by default, disabled redirects and ambient proxies, bounded JWKS responses and isolated caches by egress policy.
- Replaced request and JSON Schema pattern matching with Pydantic Core's linear-time regex engine; unsupported look-around/backreference constructs fail closed and pattern-bearing inputs receive explicit bounds.
- Tightened CORS origins, response/data-source headers, outbound URLs and WebRTC ICE metadata against ambiguous URLs and control-character/header injection.
- Fixed the WebRTC call client's temporal-dead-zone startup failure.
- Hardened the TypeScript client against non-finite response limits, raw/encoded control characters, malformed HTTP methods and oversized credentials/paths.

### Delivery
- Refreshed manifest generation for both Git checkouts and extracted ZIPs, including new files and tracked deletions while excluding build output and secrets.
- Advanced runtime, schema, TypeScript and workflow artifact metadata to v0.5.1.
- Expanded CodeQL coverage to the TypeScript reference client and retained immutable action references, dependency audits, manifests and multi-platform build gates.

## [0.5.0] — 2026-08-18

### Visual authoring and control plane
- Replaced the remotely reusable shared Editor credential with one-time founder setup, invitation enrollment, scrypt password hashes, expiring/revocable sessions and persistent login throttling; legacy-token compatibility is explicitly disabled in production.
- Added immutable built-in ranks plus bounded custom roles, project/document/database scopes, protected founder authority and security audit records.
- Added worker profiles, open/restricted project spaces, team messages, private/scoped notes, bounded attachments and one-time-ticket WebRTC audio/video signaling.
- Added metadata-aware, policy-filtered, read-only database browsing; raw SQL and undeclared support tables remain unavailable by default.
- Added configurable, validated STUN/TURN policy and a switch to disable founder setup/remove its secret after first use.
- Added bounded, schema-versioned `graphs/*.forgegraph.json` documents behind an independent Editor policy, including exact-field, path, fan-in and cycle validation.
- Added the Editor branch's Unreal-inspired node/wire authoring model, compiled Forge operation preview, eight atomic project templates and modern Graphite/amber Qt styling.
- Upgraded the native Editor plugin contract to API v2 with binary SHA-256 verification, declared permissions and plugin-provided graph nodes.
- Added a bounded Forge-backed plugin catalog contract; metadata is validated without silently downloading, installing or enabling native code.

### Python integration
- Added safe sync/async retries with stable request IDs, bounded pagination and attempt observers.
- Added multi-region sync/async clusters with rendezvous routing, circuit breakers, bounded bulk execution and idempotency-gated write failover.
- Added lazy YoungLion/DDM adapters and package extras named `[younglion]` and `[ddm]`.

### Examples and delivery
- Expanded `exampleApps` to 25 named systems with deterministic generation and schema, CRUD, RPC, idempotency, realtime and graph smoke coverage.
- Added six native Editor build targets covering Linux, Windows and macOS on x64 and ARM64, plus checksummed per-platform and combined artifacts.
- Added checksum-verified standalone server installers and build jobs for Linux glibc/musl, Windows, macOS, OCI images, universal Python packages and cPanel/Passenger source bundles without changing the 0.5.0 version line.

## [0.4.2] — 2026-08-18

### Security and correctness
- Hardened project discovery, path/header validation, API-key expiry caching, delegated-rate containment and WebSocket connection accounting.
- Disabled ambient environment proxy inheritance for controlled outbound HTTP and JWKS retrieval.
- Added a disabled-by-default editor control plane with an independent credential, TLS/IP/project policies, validated atomic writes and SHA-256 conflict detection.

### Distribution and developer experience
- Removed all bundled examples from `main`; examples now live only on the `exampleApps` branch.
- Fixed `forge new` schema references and traversal handling, generated schemas when installed outside a source checkout, and made `forge dev` work after a Git installation.
- Added reproducible install helpers and branch-specific Python library, Qt editor and example-app build workflows.

## [0.4.1] — 2026-08-10

### Correctness
- Removed legacy `app/config` and root `app/hooks` artifacts so project discovery is deterministic.
- Fixed idempotent HTTP replay metadata and prevented replayed operations from re-running background hooks.
- Replaced deprecated FastAPI ORJSON response-class usage while preserving JSON-compatible encoding.
- Updated framework/project version defaults and regenerated JSON Schemas.
- Corrected the MongoDB example so tenant policy fields remain server controlled.

### Security / dependencies
- Raised `pydantic-settings` to `>=2.14.2`.
- Raised tested minimums for `orjson`, SQLAlchemy and `asyncpg` to versions already exercised by the GitHub environment.

### CI / release integrity
- Updated `actions/checkout` to v7 and TypeScript to 7.0.2.
- Added manifest verification as a CI gate and regenerated the manifest from the final release tree.
- Retained Python 3.11–3.14, live-service, TypeScript, Docker and CodeQL gates.

## [0.4.0] — 2026-08-08

### Security / reliability
- Removed usable default secrets from onboarding and added `forge init` plus production doctor checks.
- Added one-time bootstrap administration support.
- Made operations, custom endpoints, data sources and event directions private by default with explicit public opt-ins.
- Reworked SQL-operation idempotency around canonical request fingerprints and same-business-transaction persistence.
- Reworked rate limiting around bounded principal-global and normalized-route token buckets, with WebSocket message budgets.
- Added ASGI streaming request-body size enforcement and stronger overload behavior.
- Added route-collision, dependency, database-alias, secret and production-default diagnostics.
- Isolated local JWT signing per project, disabled JWT by default, and retained the global JWT secret only as a compatibility fallback with production diagnostics.
- Hardened media metadata boundaries, owner-scoped deduplication defaults and streamed-file cleanup when metadata persistence fails.
- Made outbound retry policy method-aware so non-idempotent POST/PATCH retries require explicit opt-in.
- Added Redis subscription-readiness synchronization and bounded/reclaimed cache single-flight lock state.
- Added row ownership policies for SQL/Mongo resources; protected tenant/owner/soft-delete fields are server controlled.
- Added credential delegation/impersonation containment and narrowed bootstrap to first-credential capabilities instead of general `*` authority.
- Added trusted-proxy boundaries, operator telemetry auth, deterministic pagination, explicit migrations, bounded realtime/media/egress behavior and additional production diagnostics.

### Architecture / DX
- Introduced project runtime management and separated project routing from the application factory.
- Added strict typed configuration (`extra=forbid`) and generated JSON Schemas with VS Code associations.
- Added expanded CLI, packaging, Docker/Compose examples and TypeScript reference client.

### Testing / CI
- Expanded critical-path unit/component/integration coverage and high-risk module gates.
- Added Python matrix, live PostgreSQL/Redis/MongoDB jobs, TypeScript typecheck, container build and tag release gate.

## [0.3.0] — 2026-08-07
- Added declarative SQL/RPC, data sources/API gateway, realtime, MongoDB/JWKS, distributed cache controls and client examples.

## [0.2.0] — 2026-08-07
- Added multi-project fragments, caches, rate limiting, media, feature packs and production scaling controls.

## [0.1.0] — 2026-08-07
- Initial configuration-driven FastAPI backend foundation.
