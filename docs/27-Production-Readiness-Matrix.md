# Production Readiness Matrix

| Area | v0.5.1 status | Production action |
|---|---|---|
| HTTP CRUD/RPC | Mature alpha primitive | load/security test your schema |
| PostgreSQL | Live CI target | explicit migrations/backups |
| Redis | Live CI target | required for distributed memory semantics |
| MongoDB | Live CI target | review tenant/owner fields/indexes |
| Local media | Implemented | use only with suitable filesystem topology |
| Object storage | Not implemented | provide external architecture |
| SSE/WebSocket | Best-effort | use native ASGI and capacity tests |
| Idempotency | Same-SQL-DB transactional | outbox/provider idempotency for external effects |
| Audit | Operational log | not an immutable compliance ledger |
| Passenger | Compatibility path | prefer ASGI for realtime/high load |

Production readiness is deployment-specific; a green framework CI is necessary but not sufficient for your application.
