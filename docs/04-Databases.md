# Databases

Forge uses SQLAlchemy async engines. SQLite is useful for development/small deployments; PostgreSQL is the primary integration target; MySQL/MariaDB is supported through asyncmy configuration.

Each SQL resource names a configured database alias and table. Columns declare types, nullability, keys, uniqueness and indexes. Generic CRUD uses bound values and declarative allowlists for writable/filter/sort fields.

`auto_create` is convenient during development but is not a substitute for a controlled production migration process. v0.4 adds explicit `forge migrate` plus schema create/validate modes. In production, migrate explicitly, then prefer validation rather than routine runtime DDL.

The internal metadata database has separate pool settings from project databases. Operation idempotency support belongs to the operation's business database so the ledger and protected side effects can commit/rollback atomically.
