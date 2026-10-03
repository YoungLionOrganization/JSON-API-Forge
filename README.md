# JSON API Forge Editor v0.5.2

[![Desktop builds](https://github.com/YoungLionOrganization/JSON-API-Forge/actions/workflows/editor-build.yml/badge.svg?branch=Editor)](https://github.com/YoungLionOrganization/JSON-API-Forge/actions/workflows/editor-build.yml?query=branch%3AEditor)
[![CodeQL](https://github.com/YoungLionOrganization/JSON-API-Forge/actions/workflows/editor-codeql.yml/badge.svg?branch=Editor)](https://github.com/YoungLionOrganization/JSON-API-Forge/actions/workflows/editor-codeql.yml?query=branch%3AEditor)
[![Version 0.5.2](https://img.shields.io/badge/version-0.5.2-D4A017)](VERSION)
[![Qt 6.4+](https://img.shields.io/badge/Qt-6.4%2B-41CD52?logo=qt)](QT-CREATOR-BUILD-GUIDE.md)
[![Source available](https://img.shields.io/badge/license-source--available-555555)](LICENSE)

<p align="center"><img src="editor/resources/logo.png" width="220" alt="JSON API Forge Editor logo"></p>

JSON API Forge Editor is the native C++20/Qt 6 desktop workspace for JSON API
Forge. It combines code editing, typed visual forms, a node graph, remote
database browsing and an account-based team workspace in the Amber Gold +
Graphite Gray design system.

This branch intentionally contains only the Editor. The canonical server and
remote control-plane implementation live on `main`; the Python SDK and
example applications have their own release branches.

## Choose a component

The four branches are separate products with their own source packages and workflows.

| Branch | Purpose | Start here |
|---|---|---|
| [`main`](https://github.com/YoungLionOrganization/JSON-API-Forge/tree/main) | FastAPI server, CLI, schemas and operational docs | [Server installation](https://github.com/YoungLionOrganization/JSON-API-Forge/blob/main/INSTALL.md) |
| [`Editor`](https://github.com/YoungLionOrganization/JSON-API-Forge/tree/Editor) | Native Qt desktop authoring and team workspace | [Editor guide](https://github.com/YoungLionOrganization/JSON-API-Forge/blob/Editor/EDITOR.md) |
| [`python-library`](https://github.com/YoungLionOrganization/JSON-API-Forge/tree/python-library) | Typed synchronous/asynchronous Python SDK | [SDK guide](https://github.com/YoungLionOrganization/JSON-API-Forge/blob/python-library/PYTHON_LIBRARY.md) |
| [`exampleApps`](https://github.com/YoungLionOrganization/JSON-API-Forge/tree/exampleApps) | 25 copy-ready reference applications | [Example catalog](https://github.com/YoungLionOrganization/JSON-API-Forge/blob/exampleApps/EXAMPLE_APPS.md) |

## What v0.5.2 includes

- Animated navigation that expands the document workspace smoothly.
- Spaces & calls with visible availability, retry controls and a pinned chat composer.
- A searchable Visual library, nested document outline, typed/JSON properties, duplication, array ordering and undo/redo.

- local and remote project/document editing with optimistic SHA-256 revisions;
- visual resource, operation, database and event-channel editing;
- bounded, schema-versioned operation graphs with cycle and fan-in checks;
- founder setup, worker sign-in, invitations, ranked roles and scoped access;
- profiles, open/restricted project areas, chat, notes and file sharing;
- policy-filtered, read-only Forge database browsing;
- audio/video room launch through short-lived call tickets;
- digest-verified Plugin API v2, a bounded Forge plugin catalog and explicit secure ZIP import;
- persistent network/editor preferences, bounded retry policy and separate request/upload/download timeouts;
- right-drag graph panning, guarded unsaved-document/session recovery and detailed connection errors;
- Python SDK snippet generation without putting API keys in generated source.

The desktop client does not treat hidden UI controls as authorization. The
server rechecks every project, document, database, membership and
collaboration request.

## First session

1. Start the Editor and open a local Forge project, or connect to a server with its Editor control plane enabled.
2. For remote access, use founder setup or a worker account/invitation. Application API keys are separate from Editor account sessions.
3. Edit code, forms or graphs, then validate and review the generated configuration before saving it to the server.
4. To add a native plugin, choose **Plugins → Import plugin from ZIP…**, then review it under **Manage plugins…** before enabling it. The [plugin guide](editor/plugins/README.md) covers the package format and platform compatibility.

## Branding

The full background logo remains the application/window/platform icon. The
transparent, text-free mark in
`editor/resources/brand-mark-transparent.png` is used inside the interface
where a compact logo is appropriate.

## Build locally

Prerequisites: CMake 3.24+, a C++20 compiler, Qt 6.4+ Core/Gui/Widgets/Network
and Test (public Qt APIs; the bounded ZIP decoder is bundled),
Ninja, and optionally Qt WebEngineWidgets. Use one Qt kit throughout; the ZIP
implementation uses GuiPrivate before Qt 6.6 and CorePrivate from Qt 6.6.
On Ubuntu, the Qt IFW tools also need `libxcb-cursor0`.

```bash
cmake -S . -B build/editor -G Ninja \
  -DCMAKE_BUILD_TYPE=Release \
  -DBUILD_TESTING=ON \
  -DFORGE_EDITOR_WARNINGS_AS_ERRORS=ON
cmake --build build/editor --parallel
ctest --test-dir build/editor --output-on-failure
cmake --install build/editor --prefix build/stage
```

See [EDITOR.md](EDITOR.md) for the complete authoring, remote-team, plugin and
security model. For a click-by-click Qt Creator setup, matching kits, presets,
portable packaging and Qt Installer Framework steps, see
[QT-CREATOR-BUILD-GUIDE.md](QT-CREATOR-BUILD-GUIDE.md).

## GitHub Actions artifacts

Open [Editor build Actions](https://github.com/YoungLionOrganization/JSON-API-Forge/actions/workflows/editor-build.yml?query=branch%3AEditor), select a successful run, and download its release-assets artifact. Extract the Actions download before selecting files for a GitHub Release.

The Editor workflow tests Linux, Windows and macOS on x64 and ARM64. Every
platform produces a multi-file portable ZIP plus a real installer:

| Platform | Portable | Installer |
|---|---|---|
| Linux x64 / ARM64 | `.zip` with bundled Qt runtime | Qt Installer Framework `-setup.run` |
| Windows x64 / ARM64 | `.zip` deployed by `windeployqt` | Qt Installer Framework `-setup.exe` |
| macOS Intel / ARM64 | `.zip` deployed by `macdeployqt` | Qt Installer Framework `-setup.dmg` |

Each file receives a SHA-256 sidecar. The final job verifies every checksum
and publishes one all-platform ZIP for download from the Actions run. The
workflow never creates a GitHub Release automatically, so the Project Owner
can review and attach the artifacts to v0.5.2.

All setup packages are built with Qt's own Installer Framework. They present
the repository license before installation and install the license alongside
the Editor. Supporting legal notices are included in the staged payload. The macOS ARM64 application is native; its Qt IFW setup wrapper
uses Qt's x64 compatibility tool and therefore requires Rosetta during setup.

## License and contribution

JSON API Forge uses the **source-available** [Self-Host License 1.1](LICENSE), identified by `LicenseRef-JAF-SASH-1.1`. Commercial self-hosting and private modification are permitted under its terms; redistribution and alternative distributions require the permission described there. See the [license FAQ](LICENSE-FAQ.md), [license history](LICENSE-HISTORY.md), [third-party notices](THIRD_PARTY_NOTICES.md), [trademark policy](TRADEMARK_POLICY.md) and [AI contribution policy](AI_USAGE_POLICY.md).

Official distribution is controlled by Cavanşir Qurbanzadə (`@Cavanshirpro`) or a lawful successor/assignee. For contributions, follow [CONTRIBUTING.md](CONTRIBUTING.md) and the [CLA](CONTRIBUTOR_LICENSE_AGREEMENT.md). Report vulnerabilities through [SECURITY.md](SECURITY.md); use [GitHub issues](https://github.com/YoungLionOrganization/JSON-API-Forge/issues) for reproducible bugs and feature proposals. Include the component, platform and version, with secrets removed from logs.
