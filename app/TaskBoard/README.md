# TaskBoard

[![Forge 0.5.2](https://img.shields.io/badge/Forge-0.5.2-D4A017)](../../VERSION)
[![Example checks](https://github.com/YoungLionOrganization/JSON-API-Forge/actions/workflows/example-apps.yml/badge.svg?branch=exampleApps)](https://github.com/YoungLionOrganization/JSON-API-Forge/actions/workflows/example-apps.yml?query=branch%3AexampleApps)
[![Source available](https://img.shields.io/badge/license-source--available-555555)](../../LICENSE)

SQL CRUD reference with filtering, search, soft deletion, input schemas and a scoped `task_user` role.

## Install and run

Install the [main runtime](https://github.com/YoungLionOrganization/JSON-API-Forge/blob/main/INSTALL.md) at v0.5.2. From the exampleApps checkout:

```bash
bash scripts/install-example.sh TaskBoard /path/to/JSON-API-Forge-main/app
```

On Windows use `scripts/install-example.ps1 -Name TaskBoard -Destination C:\path\to\JSON-API-Forge-main\app`. Activate the main server's Python environment and run these commands **from its root**:

```bash
forge init
forge validate
forge doctor
forge dev
```

API prefix: `/api/task-board/v1`. Open `http://127.0.0.1:8000/api/task-board/v1/_docs` for the actual request schemas and endpoints.

After `forge init`, use `TASK_BOARD_BOOTSTRAP_ADMIN_KEY` to provision the role-specific API keys. Keep the generated `.env` private and retire bootstrap access as documented by the server.

## Try the example

`tasks` exposes the task CRUD contract. Try bounded list filters, create a task, update it, and check soft-deletion behavior with the generated API documentation.

Use `app.json` and the numbered `config/` files as the source of truth for permissions, limits and database/resource names. After adapting them, run `forge validate` and `forge doctor` again. The development reloader applies configuration changes automatically; use a deployment process with explicit migrations and backups in production.

## v0.5.2 batch creation and async task feed

`POST /api/task-board/v1/tasks/_batch` accepts up to 100 tasks in one atomic transaction. Omitted fields keep their configured defaults, even when neighboring rows supply those fields. Invalid rows reject the whole batch. This endpoint does not implement idempotent replay; reconcile task state before resubmitting an uncertain request.

`GET /api/task-board/v1/tasks-feed` is a read-only cursor view of the same tasks, using the same `tasks.list` / `tasks.read` permissions and soft-delete policy. It orders missing due dates first, then due date and task ID. Use `next_cursor` while `has_more` is true; do not supply `offset` or `sort`. The original `tasks` list still supports offset and custom sort.

Install the v0.5.2 Python SDK and run `scripts/taskboard_client.py` from the examples bundle:

```bash
python -m pip install "json-api-forge-client @ git+https://github.com/YoungLionOrganization/JSON-API-Forge.git@python-library"
# Set FORGE_API_KEY in your environment to a task_user credential.
python scripts/taskboard_client.py --local-http --status todo --limit 100
# Explicitly create two example tasks, then browse the feed:
python scripts/taskboard_client.py --local-http --seed
```

The default command only reads. It fetches pages on demand, stops at the requested limit, closes its client and has a 60-second overall deadline. Use an HTTPS URL for a remote server. Repeating `--seed` creates additional tasks.

## Boundaries and license

Use a scoped `task_user` credential for normal work; reserve administrative permissions for provisioning.

This is a reference application. Review its policies and deployment dependencies before using real data. See the [full catalog](https://github.com/YoungLionOrganization/JSON-API-Forge/blob/exampleApps/EXAMPLE_APPS.md), [LICENSE](../../LICENSE), [license FAQ](../../LICENSE-FAQ.md) and [third-party notices](../../THIRD_PARTY_NOTICES.md). Original example code is covered by `LicenseRef-JAF-SASH-1.1`.
