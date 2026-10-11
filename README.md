# JSON API Forge Python SDK v0.5.2

[![SDK builds](https://github.com/YoungLionOrganization/JSON-API-Forge/actions/workflows/python-library.yml/badge.svg?branch=python-library)](https://github.com/YoungLionOrganization/JSON-API-Forge/actions/workflows/python-library.yml?query=branch%3Apython-library)
[![CodeQL](https://github.com/YoungLionOrganization/JSON-API-Forge/actions/workflows/codeql.yml/badge.svg?branch=python-library)](https://github.com/YoungLionOrganization/JSON-API-Forge/actions/workflows/codeql.yml?query=branch%3Apython-library)
[![Version 0.5.2](https://img.shields.io/badge/version-0.5.2-D4A017)](VERSION)
[![Python 3.10–3.14](https://img.shields.io/badge/Python-3.10%E2%80%933.14-3776AB?logo=python&logoColor=white)](https://github.com/YoungLionOrganization/JSON-API-Forge/blob/main/pyproject.toml)
[![Source available](https://img.shields.io/badge/license-source--available-555555)](LICENSE)

This `python-library` branch is the independently packaged, pure-Python client SDK. It intentionally contains no Forge server runtime, Qt Editor, example projects, deployment files or compiled binaries.

## Choose a component

The four branches are separate products with their own source packages and workflows.

| Branch | Purpose | Start here |
|---|---|---|
| [`main`](https://github.com/YoungLionOrganization/JSON-API-Forge/tree/main) | FastAPI server, CLI, schemas and operational docs | [Server installation](https://github.com/YoungLionOrganization/JSON-API-Forge/blob/main/INSTALL.md) |
| [`Editor`](https://github.com/YoungLionOrganization/JSON-API-Forge/tree/Editor) | Native Qt desktop authoring and team workspace | [Editor guide](https://github.com/YoungLionOrganization/JSON-API-Forge/blob/Editor/EDITOR.md) |
| [`python-library`](https://github.com/YoungLionOrganization/JSON-API-Forge/tree/python-library) | Typed synchronous/asynchronous Python SDK | [SDK guide](https://github.com/YoungLionOrganization/JSON-API-Forge/blob/python-library/PYTHON_LIBRARY.md) |
| [`exampleApps`](https://github.com/YoungLionOrganization/JSON-API-Forge/tree/exampleApps) | 25 copy-ready reference applications | [Example catalog](https://github.com/YoungLionOrganization/JSON-API-Forge/blob/exampleApps/EXAMPLE_APPS.md) |

## Install

From the branch:

```bash
python -m pip install "json-api-forge-client @ git+https://github.com/YoungLionOrganization/JSON-API-Forge.git@python-library"
```

For a repeatable deployment, pin a reviewed commit SHA. The `Python SDK build` Action produces a universal `py3-none-any` wheel, source distribution, source ZIP and `SHA256SUMS`; it does not publish to PyPI automatically.

Python 3.10–3.14 is supported. The base package depends only on `httpx`.

## Application API client

```python
import os

from json_api_forge import ForgeClient

with ForgeClient("https://api.example.com", api_key=os.environ["FORGE_API_KEY"]) as forge:
    notes = forge.list_items("my-service", "notes", params={"limit": 25})
    created = forge.create_item(
        "my-service",
        "notes",
        {"name": "ship v0.5.2"},
        idempotency_key="command-0192",
    )
```

`AsyncForgeClient` provides the equivalent asyncio API:

```python
import asyncio
import os

from json_api_forge import AsyncForgeClient

async def main():
    async with AsyncForgeClient("https://api.example.com", api_key=os.environ["FORGE_API_KEY"]) as forge:
        return await forge.list_items("my-service", "notes", params={"limit": 25})

notes = asyncio.run(main())
```

Bounded retries are opt-in. The retry/failover policy treats GET, HEAD, OPTIONS, PUT and DELETE as idempotent HTTP methods. Other methods, including POST/PATCH, need an explicit idempotency key before replay is permitted. The server endpoint must actually support idempotency; adding a header alone does not make arbitrary side effects replay-safe.

## Editor control-plane client

```python
from getpass import getpass

from json_api_forge import EditorControlPlaneClient

with EditorControlPlaneClient("https://forge-admin.example.com") as control:
    profile = control.login("worker.name", getpass("Worker password: "))
    projects = control.projects()
    rows = control.database_rows("Billing", "primary", "invoices", limit=50)
```

The control-plane clients cover founder setup, invitation enrollment, profiles, roles, project/document access, validation, read-only database browsing, project areas, messages, notes, attachments, calls and audit records. Bearer sessions are held in a zeroable memory buffer and removed from returned authentication payloads.

Security defaults include HTTPS-only remote URLs, no ambient proxy inheritance, no redirects or retained cookies, strict same-origin paths, exact token formats, bounded response bodies, non-symlink attachment snapshots and atomic downloads. WebRTC call tickets are placed in a URL fragment for the local call client, never in an HTTP query. v0.5.2 uses the revision 2 HTTP signaling call client.

## Cluster and optional integrations

`ForgeCluster` and `AsyncForgeCluster` provide deterministic routing, circuit breakers, bounded bulk work and idempotency-gated failover across multiple Forge endpoints.

YoungLion/DDM adapters remain optional:

```bash
python -m pip install "json-api-forge-client[younglion] @ git+https://github.com/YoungLionOrganization/JSON-API-Forge.git@python-library"
# equivalent intent alias
python -m pip install "json-api-forge-client[ddm] @ git+https://github.com/YoungLionOrganization/JSON-API-Forge.git@python-library"
```

## Develop and verify

```bash
python -m venv .venv
source .venv/bin/activate  # PowerShell: .\.venv\Scripts\Activate.ps1
python -m pip install -e ".[dev]"
ruff format --check json_api_forge tests contract-tests scripts
ruff check json_api_forge tests contract-tests scripts
pytest -q tests
python -m build
python -m twine check dist/*
```

See [bounded cursor traversal and atomic batch creation](PYTHON_LIBRARY.md#bounded-collection-traversal-and-atomic-batches) for the new sync/async APIs.

CI tests Linux x64/ARM64, Windows x64/ARM64 and macOS Intel/Apple Silicon, plus clean installs on Debian, Arch, Fedora, Rocky Linux 9/cPanel-family, openSUSE and Alpine/musl. A separate contract job checks out `main` and exercises this SDK against the real v0.5.2 control plane without copying server code into this branch.

See [PYTHON_LIBRARY.md](PYTHON_LIBRARY.md) for the complete API and release contract.

## License and contribution

JSON API Forge uses the **source-available** [Self-Host License 1.1](LICENSE), identified by `LicenseRef-JAF-SASH-1.1`. Commercial self-hosting and private modification are permitted under its terms; redistribution and alternative distributions require the permission described there. See the [license FAQ](LICENSE-FAQ.md), [license history](LICENSE-HISTORY.md), [third-party notices](THIRD_PARTY_NOTICES.md), [trademark policy](TRADEMARK_POLICY.md) and [AI contribution policy](AI_USAGE_POLICY.md).

Official distribution is controlled by Cavanşir Qurbanzadə (`@Cavanshirpro`) or a lawful successor/assignee. For contributions, follow [CONTRIBUTING.md](CONTRIBUTING.md) and the [CLA](CONTRIBUTOR_LICENSE_AGREEMENT.md). Report vulnerabilities through [SECURITY.md](SECURITY.md); use [GitHub issues](https://github.com/YoungLionOrganization/JSON-API-Forge/issues) for reproducible bugs and feature proposals. Include the component, platform and version, with secrets removed from logs.

Responses use `Accept-Encoding: identity` so the response limit applies before decompression. Servers and proxies must honor this request; unsolicited compressed responses raise `ForgeHTTPError` before their body is read. The SDK reserves this transport header.
