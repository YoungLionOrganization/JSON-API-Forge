# JSON API Forge Python SDK v0.5.2

**Release candidate date:** 11 October 2026

**Status:** Alpha
**Distribution:** universal pure-Python wheel and source distribution

v0.5.2 adds lazy sync/async cursor traversal, resumable pagination, malformed-page/cycle detection and atomic heterogeneous batch creation. It preserves the existing CRUD, cluster and control-plane APIs.

The control-plane layer supports profiles, roles, scoped projects/documents/databases, project spaces, messages, notes, attachments, calls and audit. This release also tightens exact token validation, same-origin call handoff, symlink-safe bounded attachment snapshots and durable atomic downloads.

The `python-library` branch is SDK-only. Server code is owned by `main`; the Qt application is owned by `Editor`; examples are owned by `exampleApps`. CI enforces that boundary and runs a separate cross-branch contract test.

The Action uploads wheel, sdist, source ZIP and SHA-256 checksums for owner-controlled manual publication. It never pushes to PyPI or creates a GitHub Release.

JSON API Forge is source-available, not OSI open source. Official publication authority remains with Cavanşir Qurbanzadə (`@Cavanshirpro`) or a lawful successor/assignee.
