# Outbound HTTP and Egress Security

Outbound HTTP is a server/operator capability. Destination rules, HTTP-vs-HTTPS policy, private-network access and mutation retries must be reviewed as security configuration.

The resilient client disables redirects, ignores ambient proxies, resolves and validates every returned address before connecting, and can enforce maximum response bytes while streaming. Permanent 4xx responses are not retried. GET/HEAD/OPTIONS/PUT/DELETE can use bounded retries; POST/PATCH retries require explicit opt-in because retrying a mutation without upstream idempotency can duplicate effects.

HTTP data sources and external JWKS retrieval use this boundary. Both require explicit switches for plaintext HTTP or private/link-local targets. JWKS cache entries include the egress policy and response-size bound so a permissive fetch cannot be reused by a stricter configuration.

Application checks do not replace firewall, VPC, proxy or DNS egress controls. Restrict infrastructure access to metadata endpoints, internal admin services and private networks unless explicitly needed.
