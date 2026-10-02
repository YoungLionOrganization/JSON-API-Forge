# Operational Failure Modes

Expected failure classes include unavailable databases, Redis/Mongo outages, pool exhaustion, malformed/oversized requests, saturated concurrency gates, cache backend failure, egress timeout/oversized responses, media quota/storage failure, stale external JWKS, invalid migrations and shutdown cleanup errors.

Prefer explicit errors over silent partial behavior. Transactional SQL must roll back on guard failure. Runtime startup cleans up already-opened project services when a later service fails. External retries are limited and method-aware. Readiness should expose dependency failure to operators without exposing sensitive details to ordinary clients.

Test failure paths in CI and rehearse database/media backup restoration separately from unit tests.
