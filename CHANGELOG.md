# Changelog

## Unreleased maintenance

- Preserve input validation, row-count guards, audit insertion and cache invalidation in all 20 domain transition graphs, matching their JSON RPC contracts.
- Fix unreadable transition audit rows by declaring status payloads as text; keep assignment details as nullable JSON metadata rather than inserting unencoded step strings.
- Require an explicit nullable assignment deadline and verify failed assignments/transitions leave no orphan workflow or audit rows.
- Fix Git Bash drive-path hangs, reject trailing-slash link destinations, preserve late targets, and treat PowerShell bracket paths literally.
- Clarify shared operator access and assignment metadata, without claiming per-owner isolation or dual-control approval.

These changes do not replace the frozen v0.5.2 release assets. Compile the updated graphs with the matching maintenance Editor, whose compiler preserves row-count guards and invalidation arrays.

## 0.5.2 — 2026-10-11

- Add TaskBoard atomic batches and a permission-preserving read-only cursor feed with nullable due dates.
- Add a bounded asynchronous SDK recipe, included in the deterministic delivery bundle.
- Enable bounded primary-record batches in the 20 generated domain applications.
- Verify heterogeneous defaults, batch rollback, cursor completeness and SDK cancellation against pinned v0.5.2 sources.
- Fix Windows source identity checks in the ZIP builder and run delivery tests on both Windows and Linux.
- Refresh compatibility guides, catalog generation and v0.5.2 workflow assets.

## 0.5.1 — 2026-09-06

- Advance the independent examples distribution, installers, generated catalog and workflow assets to v0.5.1.
- Stream bounded example files into deterministic ZIPs and reject symlinks, case collisions, oversized files and source changes during packaging.
- Use unique temporary archives and reject output inside source apps; clamp timestamps to the ZIP format's supported range.
- Stage one-app installs and reject existing targets, dangling links and symlink/junction parents.
- Add archive regression tests to Actions and make source manifests work identically in Git and extracted source packages.

## 0.5.0 — 2026-08-25

- Split `exampleApps` into an example-only distribution with no copied server, SDK or Editor source.
- Added 25 named, copy-ready projects covering CRUD, roles, SQL/RPC, idempotency, realtime, media, public data and Editor graph metadata.
- Added 20 deterministic large-domain systems with four resources, three operations and scoped workflow policies each.
- Added `EditorPluginRegistry` for bounded reviewed plugin metadata without automatic native-code execution.
- Added safe Bash and PowerShell one-project installers.
- Added a deterministic bounded ZIP builder with internal and external SHA-256 inventories.
- Added Python 3.11–3.14 smoke tests that copy the examples into a separately checked-out `main` runtime.
- Added native Linux/Windows installer tests, branch-ownership enforcement, manifest verification and CodeQL.
