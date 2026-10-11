# Changelog

## 0.5.2 — 2026-10-11

- Add lazy synchronous and asynchronous cursor/offset traversal with resumable cursors, strict page validation, cycle detection, item bounds and cancellation propagation.
- Add atomic batch-create helpers for heterogeneous rows; never retry an unkeyed batch POST.
- Preserve explicit starting offsets and caller filters without mutating parameters.
- Verify v0.5.2 collaboration, Unicode attachments and call handoff against a pinned real server.
- Separate SDK release tags from server tags and update package/artifact identity to v0.5.2.

## 0.5.1 — 2026-09-06

- Reject repeatedly encoded traversal, URL separators, malformed encoding, unsafe HTTP headers and non-finite timeouts/retries.
- Redact common credentials from printable error diagnostics and omit query strings from request observers while still sending them to the server.
- Reduce response buffering overhead with a bounded bytearray for both synchronous and asynchronous clients.
- Update package, control-plane user agent, examples and workflow artifacts to v0.5.1; add security regression coverage and retain the real-main contract test.
- Make source manifests consistent in Git and extracted ZIPs; retain updated immutable CodeQL references and compatible Twine bounds.

## 0.5.0 — 2026-08-25

- Split the Python SDK into the independent `json-api-forge-client` pure-Python distribution.
- Added typed synchronous/asynchronous CRUD, operation and general request clients.
- Added capped retries, request-attempt observability and bounded pagination.
- Added multi-endpoint routing, circuit breakers, endpoint health and bounded bulk execution.
- Required idempotency keys before retrying or failing over writes.
- Added optional YoungLion/DDM adapters without expanding the base dependency set.
- Added synchronous/asynchronous Editor control-plane clients for accounts, roles, projects, documents, databases, collaboration, attachments, calls and audit.
- Hardened URLs, redirects, ambient proxy/cookie behavior, token formats, response bounds, upload snapshots and atomic downloads.
- Added SDK-only ownership gates, Python/platform matrices, Linux distribution installs, CodeQL, coverage, reproducible-package checks and a real contract test against `main`.
