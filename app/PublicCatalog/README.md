# PublicCatalog

[![Forge 0.5.2](https://img.shields.io/badge/Forge-0.5.2-D4A017)](../../VERSION)
[![Example checks](https://github.com/YoungLionOrganization/JSON-API-Forge/actions/workflows/example-apps.yml/badge.svg?branch=exampleApps)](https://github.com/YoungLionOrganization/JSON-API-Forge/actions/workflows/example-apps.yml?query=branch%3AexampleApps)
[![Source available](https://img.shields.io/badge/license-source--available-555555)](../../LICENSE)

An explicitly public, read-only JSON data source with allowlisted filtering and sorting. Writes remain disabled.

## Install and run

Install the [main runtime](https://github.com/YoungLionOrganization/JSON-API-Forge/blob/main/INSTALL.md) at v0.5.2. From the exampleApps checkout:

```bash
bash scripts/install-example.sh PublicCatalog /path/to/JSON-API-Forge-main/app
```

On Windows use `scripts/install-example.ps1 -Name PublicCatalog -Destination C:\path\to\JSON-API-Forge-main\app`. Activate the main server's Python environment and run these commands **from its root**:

```bash
forge init
forge validate
forge doctor
forge dev
```

API prefix: `/api/public-catalog/v1`. Open `http://127.0.0.1:8000/api/public-catalog/v1/_docs` for the actual request schemas and endpoints.

This application is explicitly public and does not require a catalog API key. Other projects on the same server keep their own authorization policy.

## Try the example

Request `GET /api/public-catalog/v1/content/catalog`. Edit the copied `data/catalog.json` and request it again to inspect file-backed content updates. This is also exercised by the branch smoke tests.

Use `app.json` and the numbered `config/` files as the source of truth for permissions, limits and database/resource names. After adapting them, run `forge validate` and `forge doctor` again. The development reloader applies configuration changes automatically; use a deployment process with explicit migrations and backups in production.

## Boundaries and license

Publish only data intended for unauthenticated access. Never put credentials, private customer records or private file paths in the catalog.

This is a reference application. Review its policies and deployment dependencies before using real data. See the [full catalog](https://github.com/YoungLionOrganization/JSON-API-Forge/blob/exampleApps/EXAMPLE_APPS.md), [LICENSE](../../LICENSE), [license FAQ](../../LICENSE-FAQ.md) and [third-party notices](../../THIRD_PARTY_NOTICES.md). Original example code is covered by `LicenseRef-JAF-SASH-1.1`.
