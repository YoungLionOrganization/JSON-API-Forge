# Rate Limiting, Overload and High RPS

Rate limiting is token-bucket based. Principal-global identity avoids unbounded raw-path cardinality; optional route budgets use normalized route identity. Pre-auth IP budgets reduce credential-guessing/request floods before expensive auth work.

Memory limiter state is bounded and refuses to evict active quota state in a way that would reset a caller's budget; overflow identities share bounded policy buckets. Redis uses an atomic Lua token bucket with Redis server time for distributed workers.

Concurrency gates bound in-flight work. Depending on configuration they reject immediately or wait for a short bounded interval, returning 503 when saturated. Rate/concurrency limits protect capacity but cannot compensate for unindexed queries or undersized database pools.
