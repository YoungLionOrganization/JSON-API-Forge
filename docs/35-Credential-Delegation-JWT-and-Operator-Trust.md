# Credential Delegation, JWT and Operator Trust

Credential issuance is a privilege boundary. An API key may only delegate a subset of its roles/permissions/tenant/lifetime/rate budget unless it has the explicit high-trust `admin.credentials.delegate_any` capability. Issuing a credential for another subject requires impersonation authority.

Bootstrap is intentionally narrow: it exists to create the first durable administrative credential, not to become a permanent wildcard principal.

JWT can be local HS256 or external JWKS. Local project secrets are isolated per project when configured. External claim trust must be explicit. External key retrieval requires HTTPS and blocks private/link-local destinations by default; any insecure/private-network opt-in must be reviewed as an egress privilege. Process-wide operational telemetry uses `OPERATOR_TOKEN`, separate from project admin credentials.
