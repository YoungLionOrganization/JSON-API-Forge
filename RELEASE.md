# JSON API Forge Editor v0.5.2

**Release date:** 4 October 2026

**Status:** Alpha desktop workspace

**License:** JSON API Forge Source-Available Self-Host License 1.1

v0.5.2 hardens the native C++20/Qt 6 workspace for local and policy-controlled
remote Forge administration while preserving the v0.5.0 document contract.

This release adds animated navigation, visible Spaces & calls availability and
retry controls, a pinned chat composer, and an expanded Visual workspace with
search, nested typed editing, structured JSON dialogs, duplication, reordering
and undo/redo. The window title and executable metadata display v0.5.2.

The release includes code, typed visual and graph editing; worker profiles;
ranked/scoped access; project areas; messaging, notes and attachments;
read-only database browsing; security audit visibility; and short-lived
audio/video call tickets. The Editor never substitutes UI visibility for
server authorization.

This maintenance line adds persistent bounded network/editor settings,
detailed connection diagnostics, same-server reauthentication without losing
unsaved work, founder/setup reconciliation, guarded save-conflict handling,
right-drag graph panning and explicit native-plugin import from ZIP. ZIP
imports reject traversal, symlinks, collisions, oversized/high-ratio archives,
bad CRC/digests and incompatible Plugin API versions; imported code remains
disabled until separately reviewed and enabled.

The `Editor build` workflow produces six portable, multi-file packages and six
installers:

| Target | Portable package | Installer |
|---|---|---|
| Linux x64 / ARM64 | ZIP | Qt IFW RUN |
| Windows x64 / ARM64 | ZIP | Qt IFW EXE |
| macOS Intel / ARM64 | ZIP | Qt IFW DMG |

Every deliverable has a SHA-256 sidecar, and the combined job verifies all
checksums before creating the all-platform artifact. Signing, notarization and
GitHub Release publication remain explicit Project Owner steps.

The server-side v0.5.2 control plane from `main` is required for remote team,
database and collaboration features. Keep that endpoint private, HTTPS-only
outside loopback, and configured with least-privilege project/document/
database policies.
