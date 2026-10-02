# Installation

JSON API Forge requires Python 3.10–3.14 and Git. Python 3.13 is the recommended production baseline for v0.5.1.

## Clone and create an isolated environment

Linux/macOS:

```bash
git clone https://github.com/YoungLionOrganization/JSON-API-Forge.git
cd JSON-API-Forge
./scripts/install.sh
source .venv/bin/activate
forge new MyService --slug my-service
forge init
forge validate
forge dev
```

Windows PowerShell:

```powershell
git clone https://github.com/YoungLionOrganization/JSON-API-Forge.git
Set-Location JSON-API-Forge
.\scripts\install.ps1
.\.venv\Scripts\Activate.ps1
forge new MyService --slug my-service
forge init
forge validate
forge dev
```

Pass `--dev` (PowerShell: `-Dev`) to include tests and release tooling. The scripts never overwrite `.env` or application projects.

## Install a compiled release asset

Once the v0.5.1 workflow artifacts have been attached to the canonical GitHub Release, the same entry scripts detect the local OS, CPU architecture and Linux libc, download the exact asset over HTTPS, verify its published SHA-256 checksum, and refuse to overwrite an existing destination:

```bash
./scripts/install.sh --release --destination /opt/json-api-forge
```

```powershell
.\scripts\install.ps1 -Release -Destination 'C:\JSON-API-Forge'
```

The compiled package contains `forge` and `forge-server`. It does not contain a deployment's `.env`, application definitions or database data. Use the portable Python installation when no native artifact matches the host.

## Install directly from a Git ref

```bash
python -m pip install "json-api-forge @ git+https://github.com/YoungLionOrganization/JSON-API-Forge.git@main"
mkdir my-forge-server && cd my-forge-server
forge new MyService --slug my-service
forge init
forge validate
forge dev
```

Pin a release tag or commit instead of `main` for reproducible deployment.

## Production checks

```bash
forge init --production
forge doctor --production
forge migrate
forge validate
```

Set `INTERNAL_SCHEMA_MODE=validate` after migration. Run behind a TLS reverse proxy, configure each project's trusted proxy CIDRs/hosts, and never commit `.env`.

## Branches

- `main`: runtime and CLI; intentionally no example app.
- `python-library`: standalone typed Python SDK, tests and package workflow.
- `Editor`: C++20/Qt 6 desktop editor, assets, packaging and plugin SDK.
- `exampleApps`: copy-ready application directories validated against `main`.

Each branch contains only its owned source and workflow. See
[`BRANCHES.md`](BRANCHES.md) for the exact ownership contract.

The complete support/hosting matrix and hardened service templates are in [`docs/43-Platform-and-Hosting-Matrix.md`](docs/43-Platform-and-Hosting-Matrix.md).
