# Production Scaling

Scaling starts by separating state that is safely process-local from state that must coordinate across workers. SQL/Mongo databases are shared by design; memory cache/rate limits/event hubs are not.

Use Redis backends for distributed rate limiting, shared cache semantics and cross-instance best-effort pub/sub. Size database pools per process and account for total connections across replicas. Keep internal metadata DB capacity in the calculation because API-key lookup, audit and media metadata can otherwise become hidden bottlenecks.

Use a reverse proxy/load balancer that preserves WebSocket/SSE behavior and configure its CIDR as trusted before accepting forwarded headers. Keep independent proxy-header rewriting disabled in the official Uvicorn launch path so Forge's trust decision remains the source of truth.
