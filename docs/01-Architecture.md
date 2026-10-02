# Architecture

Forge separates **declarative configuration**, **project runtime services**, **route assembly**, and **custom logic**.

`framework/config.py` owns strict typed configuration and merge validation. `framework/runtime.py` owns the lifecycle of SQL registries, Mongo registries, cache, rate limiting, event hubs, data-source managers and media stores. `framework/routers/project.py` turns one validated `ProjectConfig` into FastAPI routes. `framework/factory.py` owns the process application, middleware, health/readiness/metrics and shared internal metadata database.

This separation matters because a failure while opening project services must clean up resources already created; runtime startup is treated transactionally at process scope. Projects are looked up by their API prefix, including the longest matching prefix when prefixes overlap.

The architecture deliberately avoids a second “compiled configuration language.” JSON describes bounded primitives. Complex business rules should be a named SQL/RPC operation or Python hook rather than an opaque generic expression system.

Internal metadata (API keys, audit, media metadata, bootstrap state) lives in Forge's internal database. Business resources and operation idempotency live in the configured project/business databases. v0.4 idempotency deliberately keeps the idempotency ledger in the same database transaction as protected SQL side effects.

Source distribution remains one canonical framework; examples and clients are reference integrations, not alternate editions.
