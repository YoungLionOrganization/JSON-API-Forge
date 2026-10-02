# Production Checklist

Before production: run `forge validate`, `forge doctor --production`, `forge migrate`, schema drift checks, full tests and a package/container build. Supply real secrets outside Git and use `INTERNAL_SCHEMA_MODE=validate` after migration.

Review: TLS and proxy trust; CORS/hosts; public endpoints; API key/JWT delegation; database pools; tenant/owner policies; rate/concurrency budgets; request size; egress destinations; media quotas; readiness/operator token; audit capacity; backup/restore; log redaction; database indexes and migration rollback.

Prefer native ASGI. If multiple workers/hosts need shared rate limiting/cache/realtime, configure Redis rather than memory. If media must be shared across hosts, local filesystem is insufficient.

Do not tag an official release while required canonical CI is red.
