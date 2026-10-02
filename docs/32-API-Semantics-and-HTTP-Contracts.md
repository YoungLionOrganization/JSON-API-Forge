# API Semantics and HTTP Contracts

Forge aims for stable conventional semantics: 401 for missing/invalid authentication when auth is required, 403 for authenticated principals lacking permission/policy, 404 for invisible/missing rows, 409 for transactional/idempotency conflicts, 413 for size/result bounds, 422 for validated input violations, 429 for rate limits and 503 for temporary saturation/dependency conditions.

PATCH is partial update; PUT is replacement of writable representation while server policy/identity fields are preserved. Pagination is deterministic and bounded.

Idempotent completed replays in v0.4.1 expose both `X-Forge-Idempotent-Replay: true` and `_idempotent_replay: true` for object responses. First executions do not carry that object marker.

OpenAPI public operations explicitly use `security: []`; private operations inherit project security schemes.
