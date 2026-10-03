# Editor changelog

## Editor fixes — 2026-10-03 (0.5.1)

- Restored permission-aware team startup, project-scoped member capabilities and profile editing; added a native integration test against the canonical server.
- Moved Disconnect to the persistent workspace header, reclaimed splitter space immediately when hiding navigation, and kept the document workspace visible with the team dock on compact windows.
- Preserved chat and note drafts across failed requests, delayed acknowledgements and area changes; filtered stale project/area responses and avoided overlapping message polling.
- Added restricted-note reader rank, accurate create-only note labels, camera/microphone permission prompts and safe call-window cleanup.
- Preserved edits when reselecting Visual/Graph modes or opening another document, and kept visual property types intact.
- Added native GUI regression tests to every platform build and the Linux sanitizer job, plus a pinned canonical-server contract test on Linux x64.

## [0.5.1] — 2026-09-06

- Fixed graph wire use-after-free during scene rebuilds and cancelled stale drag interactions; added a real mouse-event regression test.
- Rejected non-finite zoom settings and non-printable plugin display names.
- Waited explicitly for the Windows Qt IFW tool installer and corrected the macOS setup launch path to the actual CMake bundle name.
- Added Linux/macOS portable staging instructions to the Qt Creator guide.

- Added persistent Network and Editor/Advanced preferences with bounded request, authentication, upload/download timeout, safe-GET retry, backoff, response-size, graph-pan and zoom controls.
- Preserved unsaved documents across same-server reauthentication/session expiry, added save-race and revision-conflict guards, and reconciled founder setup after timeout/conflict responses.
- Added detailed timeout, DNS, refusal, reset, TLS, cancellation, authentication, conflict, validation, rate and server error reporting.
- Added right-button graph panning with click-vs-drag separation and configurable sensitivity while preserving middle-button panning.
- Added explicit native-plugin ZIP import with traversal, symlink, case collision, archive bomb, size, CRC, manifest, SHA-256 and API compatibility defenses. Imported plugins stay disabled pending separate review.
- Replaced legacy DEB/NSIS/native-DMG packaging with six portable ZIPs and six license-presenting Qt Installer Framework setup packages, using SHA-256-pinned official IFW tools.
- Added Qt private-header dependencies, warnings-as-errors, sanitizer tests and expanded core regression coverage.

## [0.5.0] — 2026-08-25

- Added the Amber Gold + Graphite Gray interface, full application icon and
  transparent text-free in-app brand mark.
- Expanded local/remote code, typed visual and node-graph authoring with
  bounded documents, cycle/fan-in checks and compiled Forge previews.
- Added founder setup, worker login, profiles, ranked/scoped roles,
  invitations and policy-aware member administration.
- Added open/restricted project areas, chat, notes, bounded attachment
  sharing, read-only database browsing, audit inspection and one-time-ticket
  audio/video rooms.
- Hardened URL, redirect, proxy, TLS, credential, call-ticket, attachment and
  plugin boundaries. Authorization remains server-side and deny-by-default.
- Added Plugin API v2 digest/permission checks and a metadata-only Forge
  plugin catalog.
- Added six warnings-as-errors native build targets. Each produces a
  multi-file portable ZIP and a real DEB, NSIS or DMG installer with SHA-256
  verification.

This branch is the Editor distribution only. Server, Python SDK and example
application source belong to their dedicated branches/packages.
