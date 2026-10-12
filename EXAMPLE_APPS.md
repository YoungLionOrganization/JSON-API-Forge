# Copy-ready example applications

This branch keeps complete Forge applications under `app/`; `main` intentionally contains none. Each directory is independent, uses its own slug/API prefix and can be copied unchanged into another JSON API Forge checkout.

The catalog contains 25 named projects. The original five demonstrate specialized surfaces; the other twenty are deliberately large domain systems generated from a reviewed, deterministic source catalog. Every generated system contains four SQL resources, three operations (analytics, idempotent transition and assignment), operator/auditor roles, cache/rate-limit/protection policy, realtime events and an Editor graph.

| Application | Focus |
|---|---|
| `TaskBoard` | Filtered/searchable SQL CRUD, soft deletion and scoped roles |
| `GuildLedger` | Discord-style accounts plus atomic, idempotent balance operations |
| `RealtimeSupport` | Ticket CRUD plus bounded WebSocket/SSE notifications |
| `MediaLibrary` | Private local media, quotas, MIME/extension policy and collection metadata |
| `PublicCatalog` | Explicitly public, read-only JSON-file data source |
| `CommerceCore` | Order orchestration, fulfilment ownership and monetary rollups |
| `IdentityHub` | Identity lifecycle, policy records and auditable state changes |
| `ProjectOps` | Portfolio work, assignments, workflow history and dashboards |
| `LearningCampus` | Enrollment operations and bounded progress reporting |
| `ClinicFlow` | Synthetic care-operations workflow (not a medical-record system) |
| `FleetControl` | Vehicle lifecycle, dispatch and maintenance operations |
| `HotelOperations` | Reservation workflow, service hand-offs and revenue totals |
| `RestaurantNetwork` | Multi-location preparation and service-order flow |
| `WarehouseGrid` | Inventory batches, allocation and quantity aggregation |
| `SubscriptionPlatform` | Tenant subscription lifecycle and usage operations |
| `CreatorStudio` | Content production, review and publishing workflow |
| `TournamentEngine` | Match lifecycle, officials, rules and live updates |
| `IoTControlCenter` | Device control metadata and remediation workflows |
| `LogisticsNetwork` | Shipment/hub orchestration and exception handling |
| `CivicPortal` | Private civic case workflow and aggregate reporting |
| `ResearchVault` | Dataset stewardship, access policies and provenance |
| `HiringPipeline` | Candidate workflow, recruiter ownership and audit events |
| `IncidentCommand` | Incident response, runbooks and realtime command updates |
| `FinanceOps` | Synthetic batch operations, scoped operators and totals |
| `EditorPluginRegistry` | Forge-backed Editor plugin metadata, permissions and SHA-256 review state |

Except for the explicitly public `PublicCatalog`, each project uses a generated `<SLUG>_BOOTSTRAP_ADMIN_KEY`. Run `forge init`; do not hand-author shared credentials.

## New in v0.5.2

TaskBoard adds a read-only `tasks-feed` cursor view ordered by nullable due date, plus atomic batches of up to 100 tasks. The original sortable task list is retained. The included [async SDK recipe](scripts/taskboard_client.py) browses on demand with an item limit and overall deadline; seeding is explicit.

The 20 generated domain apps now support primary-record batch imports, preserving defaults independently per row and rolling back the entire transaction on an invalid or conflicting record. CI exercises every app's batch success, defaults and rollback, along with TaskBoard's scoped credentials and async SDK flow. Server and SDK contract inputs are pinned to reviewed v0.5.2 commits.

## Run examples with the independently owned runtime

```bash
git clone --branch main --single-branch https://github.com/YoungLionOrganization/JSON-API-Forge.git JSON-API-Forge-main
git clone --branch exampleApps --single-branch https://github.com/YoungLionOrganization/JSON-API-Forge.git JSON-API-Forge-examples
cp -R JSON-API-Forge-examples/app/. JSON-API-Forge-main/app/
cd JSON-API-Forge-main
python -m pip install -e ".[dev]"
forge init                               # creates strong local bootstrap secrets
forge validate
forge doctor
forge dev
```

Each project exposes its own documentation root at `/api/<slug>/v1/_docs`.

## Copy one application into another checkout

From this branch:

```bash
./scripts/install-example.sh TaskBoard /path/to/JSON-API-Forge/app
# Windows PowerShell:
.\scripts\install-example.ps1 -Name TaskBoard -Destination C:\path\to\JSON-API-Forge\app
```

The scripts refuse to overwrite an existing target. A plain directory copy works too. In the destination checkout run `forge init`, `forge validate`, then `forge dev`. Never copy the generated root `.env` or runtime `data/`; create fresh secrets and databases per installation.

The generated applications and release ZIP are reproducible from the examples checkout:

```bash
python scripts/generate_example_catalog.py --check
python scripts/build-example-bundle.py JSON-API-Forge-exampleApps-v0.5.2.zip
```

`smoke-example-apps.py` is intentionally executed by CI from a separate `main` checkout after the examples have been copied into it; it is not a standalone server.

`EditorPluginRegistry` is the reference backing service for the Editor's plugin catalog. It stores metadata and reviewed package digests; the Editor intentionally does not auto-download, auto-install or auto-enable native code.

These are development/reference configurations. Before production, use Redis-backed shared rate limiting where needed, set concrete trusted hosts/CORS origins, enable HTTPS, move SQLite workloads to a supported production database and run `forge doctor --production`.
