# Security Threat Model

Primary trust zones are: untrusted HTTP/WebSocket clients; trusted project configuration/source; internal metadata DB; project business DBs; optional Redis/Mongo; external JWKS/HTTP services; filesystem media; reverse proxy/operator plane.

Forge defends client boundaries with authentication, permission checks, tenant/owner filters, bounded inputs/results, safe parameter binding, rate/concurrency controls and explicit public opt-in. It defends proxy boundaries by trusting forwarded headers only from configured CIDRs. It defends credential delegation by preventing ordinary issuers from escalating descendants.

Not protected as attacker-safe: deployer-authored SQL text/hooks/config; a compromised host; stolen secrets; malicious dependencies; network destinations allowed by infrastructure; cross-system exactly-once semantics. Treat configuration review, dependency patching, secret storage, database permissions and network policy as part of the security model.
