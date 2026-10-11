# Python SDK contract — v0.5.2

Package name: `json-api-forge-client`

Import name: `json_api_forge`

Python: 3.10–3.14
Wheel: pure Python, `py3-none-any`

The SDK and the Forge server are separate distributions. Install the server from `main`; install this client from `python-library`. The branch workflow verifies their integration by checking out both branches into separate directories.

## Public clients

- `ForgeClient` / `AsyncForgeClient`: health, metadata, CRUD, declared operations and a bounded general request API.
- `ForgeCluster` / `AsyncForgeCluster`: rendezvous or ordered routing, circuit breakers, endpoint health and bounded bulk execution.
- `EditorControlPlaneClient` / `AsyncEditorControlPlaneClient`: account/team administration, scoped editing, database browsing and collaboration.

All response objects expose the decoded data, HTTP status, server request ID, idempotent replay marker and cache status. Transport, HTTP, response-size, session and cluster failures use typed exceptions.

## Retry and routing rules

`RetryPolicy` uses capped exponential backoff and honors a bounded `Retry-After`. GET-like operations may retry according to policy. POST/PATCH requests require an explicit idempotency key before the SDK permits retry or cross-endpoint failover. One request ID is preserved across allowed attempts.

## Bounded collection traversal and atomic batches

Both clients support `iter_items(..., pagination="offset" | "cursor")`. The default remains offset; use cursor mode only for resources configured with `pagination_mode: cursor`. Filters are preserved, and `params={"cursor": saved_cursor}` resumes a cursor. Cursor mode rejects `offset` and `sort`. Offset mode accepts an explicit non-negative starting offset.

```python
async with AsyncForgeClient(url, api_key=key) as forge:
    async for task in forge.iter_items(
        "task-board", "tasks-feed", pagination="cursor", page_size=50, max_items=500,
        params={"status": "todo"},
    ):
        print(task["title"])
    result = await forge.batch_create_items("task-board", "tasks", [
        {"title": "Plan release"}, {"title": "Review release", "priority": 3},
    ])
```

Iteration fetches on demand, buffers at most one bounded response and stops at `max_items`. Stopping or cancelling async iteration does not start a new page. Invalid pages and repeated cursors raise `ForgeHTTPError` with the server request ID. A cursor is not a database snapshot; changes to its ordering field during traversal can change results.

`batch_create_items` sends one transaction to the resource's `/_batch` endpoint. It requires 1–5000 object payloads; the configured server `max_batch_size` may be lower. The entire batch succeeds or rolls back. The server batch endpoint does not implement idempotent replay, so the helper never automatically retries or splits it. If a network failure leaves the result uncertain, reconcile application identifiers before resubmitting.

Collection iterators and cluster bulk helpers are bounded. The default bulk input ceiling is 10,000 items; raise it only after evaluating memory and downstream capacity.

Multipart file uploads are never retried or failed over automatically, even with an idempotency key. Retry explicitly with a fresh body after checking the upload outcome. Cancelling async bulk work, including cancellation raised by an individual operation, cancels and waits for its sibling operations before returning. Completed server-side effects cannot be rolled back by cancellation.

## Control-plane security

- Remote HTTP is rejected; `allow_insecure_http=True` is restricted to loopback development endpoints.
- Embedded URL credentials, cross-host absolute paths, traversal, backslashes, fragments and control characters are rejected.
- Redirect following, ambient proxy variables and cookie persistence are disabled.
- Session, invitation and call credentials must match the server's exact v0.5.2 token formats.
- Login/setup credentials are not reused as application API keys.
- Adopted access tokens are removed from returned payloads and cleared on logout, close or HTTP 401.
- Response and attachment sizes are bounded.
- Uploads open regular non-symlink files with no-follow protection where supported, verify file identity and upload a bounded snapshot.
- Downloads fsync a private temporary file, atomically replace the requested name and fsync the parent directory on POSIX.
- Call tickets travel to the local browser client in the URL fragment; the browser presents the ticket to the same-origin call API; v0.5.2 call-client revision 2 uses HTTP signaling.

These controls reduce common client-side mistakes; they do not replace server authorization, TLS validation, secret storage or endpoint hardening.

## Optional YoungLion/DDM adapters

Both extras install the compatible YoungLion 0.1.x line lazily:

```bash
python -m pip install "json-api-forge-client[younglion]"
python -m pip install "json-api-forge-client[ddm]"
```

```python
from YoungLion import DDM
from json_api_forge.integrations import YoungLionForgeClient

with YoungLionForgeClient.connect("https://forge.example.com", api_key="...") as forge:
    result = forge.create_item("accounts", "profiles", DDM({"name": "Ada"}))
    print(result.data.to_dict())
```

## Release artifacts

The GitHub Action performs:

1. SDK-only branch ownership and manifest checks.
2. Ruff, compilation and Python 3.10–3.14 unit matrices on Linux, Windows and macOS x64/ARM64.
3. Clean PEP 517 installs across six Linux families, including musl and Rocky Linux 9/cPanel-family.
4. A real server/SDK contract test against `main`.
5. Branch-aware coverage gates.
6. Two wheel builds compared byte-for-byte and two sdist payloads compared structurally.
7. Wheel content, metadata, typing marker and out-of-tree installation checks.
8. A checksummed artifact bundle suitable for manual PyPI and GitHub Release publication.

The workflow deliberately has `contents: read` and never publishes a package or creates a release.
