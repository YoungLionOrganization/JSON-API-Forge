# Future Features

Potential future work should preserve the core rule: add primitives that broadly remove repetitive backend work without turning JSON configuration into an unreadable general-purpose language.

Candidates include production-grade object storage adapters, richer migration tooling, durable broker/outbox recipes, generated SDKs, stronger observability exports, deployment templates and additional declarative validation/authorization primitives.

New features should ship with typed config, JSON Schema, doctor diagnostics, unit/component tests, failure/concurrency tests where relevant, documentation and a clear non-goal statement. Unsupported features should not appear as valid configuration values merely because an interface might exist later.
