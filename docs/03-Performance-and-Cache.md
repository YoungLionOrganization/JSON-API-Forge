# Performance and Cache

Forge uses async database drivers and connection pools, pooled outbound HTTP, optional Redis, bounded in-memory state and cache generation namespaces.

Resource reads/lists can be cached. Mutation paths invalidate the resource namespace; operations may invalidate named resources or other operation namespaces. Cache keys include server-controlled context such as tenant/owner where configured so authorization boundaries do not collapse into one shared cached result.

Memory cache is process-local. Redis is required when cache coordination must cross workers/hosts. Cache single-flight reduces stampedes, but it is not a distributed transaction mechanism. Stale-response windows are explicit configuration.

Performance tuning should start with database indexes and query shape, then pool sizing, then cache. Do not hide slow unbounded SQL behind a cache. Use `scripts/load_test.py` only as a small diagnostic probe; use a distributed load tool for real capacity testing.
