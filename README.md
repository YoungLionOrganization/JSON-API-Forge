# JSON API Forge example applications v0.5.2

[![Examples](https://github.com/YoungLionOrganization/JSON-API-Forge/actions/workflows/example-apps.yml/badge.svg?branch=exampleApps)](https://github.com/YoungLionOrganization/JSON-API-Forge/actions/workflows/example-apps.yml?query=branch%3AexampleApps)
[![CodeQL](https://github.com/YoungLionOrganization/JSON-API-Forge/actions/workflows/codeql.yml/badge.svg?branch=exampleApps)](https://github.com/YoungLionOrganization/JSON-API-Forge/actions/workflows/codeql.yml?query=branch%3AexampleApps)
[![Version 0.5.2](https://img.shields.io/badge/version-0.5.2-D4A017)](VERSION)
[![Python 3.10–3.14](https://img.shields.io/badge/Python-3.10%E2%80%933.14-3776AB?logo=python&logoColor=white)](https://github.com/YoungLionOrganization/JSON-API-Forge/blob/main/pyproject.toml)
[![Source available](https://img.shields.io/badge/license-source--available-555555)](LICENSE)

This `exampleApps` branch contains only the 25 copy-ready Forge projects and the small tools that generate, install, smoke-test and package them. It intentionally contains no server runtime, Python SDK or Qt Editor source.

## Use an example

First obtain a v0.5.2 `main` checkout. Then copy one project from this branch into its `app/` directory:

```bash
bash scripts/install-example.sh TaskBoard /path/to/JSON-API-Forge-main/app
```

Windows PowerShell:

```powershell
.\scripts\install-example.ps1 -Name TaskBoard -Destination C:\path\to\JSON-API-Forge-main\app
```

In the `main` checkout, create new local secrets and validate before starting:

```bash
forge init
forge validate
forge doctor
forge dev
```

The installers reject unsafe names, unknown projects and existing destinations. Do not copy a server `.env`, database, media or log directory between deployments.

## Pick a starting point

| Goal | Example |
|---|---|
| First SQL CRUD API | [TaskBoard](app/TaskBoard/README.md) |
| Atomic, replay-safe transfers | [GuildLedger](app/GuildLedger/README.md) |
| Public data without credentials | [PublicCatalog](app/PublicCatalog/README.md) |
| Ticket events over SSE/WebSocket | [RealtimeSupport](app/RealtimeSupport/README.md) |
| Private file uploads | [MediaLibrary](app/MediaLibrary/README.md) |
| Editor plugin catalog | [EditorPluginRegistry](app/EditorPluginRegistry/README.md) |
| Domain workflows and graph authoring | The 20 generated applications in the [catalog](EXAMPLE_APPS.md) |

The [main server](https://github.com/YoungLionOrganization/JSON-API-Forge/tree/main), [desktop Editor](https://github.com/YoungLionOrganization/JSON-API-Forge/tree/Editor) and [Python SDK](https://github.com/YoungLionOrganization/JSON-API-Forge/tree/python-library) have independent packages. Example apps are installed into the main server; importing an Editor plugin ZIP is a different operation.

## What is included

The collection covers CRUD, scoped roles, soft deletion, SQL/RPC, transactional idempotency, realtime events, media, public file data, operational workflows and Editor graph metadata. `EditorPluginRegistry` demonstrates reviewed plugin metadata and SHA-256 records; it never installs or executes native code.

See [EXAMPLE_APPS.md](EXAMPLE_APPS.md) for the full catalog and production caveats.

## Verification and release artifact

The branch workflow checks out `main` separately, installs its real runtime, copies all 25 projects into a clean `main/app/`, and runs validation plus schema/CRUD/RPC/idempotency/realtime/media smoke scenarios on Python 3.10–3.14. Bash and PowerShell copy installers run on native Linux and Windows workers.

`scripts/build-example-bundle.py` produces a bounded, symlink-free, byte-for-byte deterministic ZIP containing `app/`, the catalog and README, legal/provenance documents, the two copy installers and an internal `SHA256SUMS`. GitHub Actions uploads the ZIP and its external checksum; it never publishes a Release automatically.

### Contribute catalog changes

Update `scripts/generate_example_catalog.py` for the 20 generated apps, then run:

```bash
python scripts/generate_example_catalog.py
python scripts/generate_example_catalog.py --check
python -m unittest discover -s scripts -p 'test_*.py' -v
python scripts/build-example-bundle.py /tmp/forge-examples.zip
```

Choose a path outside `app/` for the ZIP (`$env:TEMP` on Windows). The five introductory examples are maintained directly. To run integration smoke tests, install the server's development dependencies, copy examples into a disposable main checkout, initialize it, and run this branch's `scripts/smoke-example-apps.py` from that server root.

## License and contribution

JSON API Forge uses the **source-available** [Self-Host License 1.1](LICENSE), identified by `LicenseRef-JAF-SASH-1.1`. Commercial self-hosting and private modification are permitted under its terms; redistribution and alternative distributions require the permission described there. See the [license FAQ](LICENSE-FAQ.md), [license history](LICENSE-HISTORY.md), [third-party notices](THIRD_PARTY_NOTICES.md), [trademark policy](TRADEMARK_POLICY.md) and [AI contribution policy](AI_USAGE_POLICY.md).

Official distribution is controlled by Cavanşir Qurbanzadə (`@Cavanshirpro`) or a lawful successor/assignee. For contributions, follow [CONTRIBUTING.md](CONTRIBUTING.md) and the [CLA](CONTRIBUTOR_LICENSE_AGREEMENT.md). Report vulnerabilities through [SECURITY.md](SECURITY.md); use [GitHub issues](https://github.com/YoungLionOrganization/JSON-API-Forge/issues) for reproducible bugs and feature proposals. Include the component, platform and version, with secrets removed from logs.

See [v0.5.2 improvements](EXAMPLE_APPS.md#new-in-v052) for atomic imports, TaskBoard cursor feeds and the runnable async SDK recipe.
