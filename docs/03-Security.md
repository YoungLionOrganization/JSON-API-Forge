# Security Overview

Forge security is deny-by-default around declarative endpoint types. API keys are project scoped; JWT is opt-in; bootstrap is explicit; public access requires explicit configuration.

Do not treat the config repository as untrusted code. SQL text, hook paths, egress destinations and many security settings are server/operator configuration and must be reviewed like source code.

Use one-time bootstrap to mint the first durable credential, then stop using bootstrap. Delegated credentials are constrained so ordinary admins cannot mint broader roles, permissions, tenants, expiration or rate budgets than they hold. A separate explicit high-trust permission is required for unrestricted delegation/impersonation.

Production deployments should use `forge doctor --production`, strong secrets, TLS, trusted proxy CIDRs, narrow allowed hosts/origins, and external network controls around databases and egress.
