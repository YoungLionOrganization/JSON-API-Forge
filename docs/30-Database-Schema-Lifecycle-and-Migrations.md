# Database Schema Lifecycle and Migrations

Development may use create/auto-create behavior for convenience. Production should separate schema change from normal request-serving startup.

`forge migrate` creates required Forge support objects and configured auto-create resource tables. After migration, select validate mode so startup verifies required support structures instead of silently performing DDL. Application data migrations that transform existing business data remain operator/domain responsibility.

Back up before destructive schema changes, make migrations repeatable where possible, and exercise forward/rollback procedures against production-like databases.
