# Security Policy

## Supported versions

| Version | Security support |
|---|---|
| 0.5.x | ✅ Current supported line |
| 0.4.x | ⚠️ Security fixes only; upgrade recommended |
| 0.3.x | ⚠️ Superseded; upgrade recommended |
| 0.2.x | ❌ Historical |
| 0.1.x | ❌ Historical |

Historical releases remain available for reproducibility and comparison, but security fixes target the current supported release line unless the Project Owner states otherwise.

## Reporting a vulnerability
Do **not** publish exploit details, credentials, private keys or sensitive deployment data in a public issue. Prefer GitHub Private Vulnerability Reporting on the canonical repository. If it is unavailable, request a private contact channel without including exploit details.

Include the affected version, deployment model, impact, reproduction conditions and mitigation if known. No guaranteed response-time or support SLA is provided.

## Deployment responsibility
Exposed `.env` files, weak keys, unsafe hooks, deployer-authored arbitrary SQL and insecure proxy/TLS settings can create vulnerabilities outside the framework defaults. Run `forge doctor --production`, protect secrets, use narrow credentials and keep dependencies current.
