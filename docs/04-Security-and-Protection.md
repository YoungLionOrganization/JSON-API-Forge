# Security and Request Protection

Request protection includes trusted host validation, IP allow/deny rules, trusted proxy CIDRs, HTTPS requirements, pre-auth and principal rate limits, request timeouts, concurrency saturation handling and streaming body-size enforcement.

Forwarded IP/protocol headers are ignored unless the immediate peer is inside a configured trusted proxy CIDR. This prevents arbitrary clients from spoofing `X-Forwarded-For` or `X-Forwarded-Proto` into authorization/protection decisions.

The ASGI body limiter counts received chunks and does not trust `Content-Length` alone. A misleading or absent length therefore cannot bypass the configured body limit.

In-memory concurrency/rate-limit state is worker-local; use Redis for distributed rate limiting. A bounded overflow policy prevents high-cardinality identities from forcing unbounded in-memory bucket growth.
