# Editor plugins

[![Desktop builds](https://github.com/YoungLionOrganization/JSON-API-Forge/actions/workflows/editor-build.yml/badge.svg?branch=Editor)](https://github.com/YoungLionOrganization/JSON-API-Forge/actions/workflows/editor-build.yml?query=branch%3AEditor)
[![CodeQL](https://github.com/YoungLionOrganization/JSON-API-Forge/actions/workflows/editor-codeql.yml/badge.svg?branch=Editor)](https://github.com/YoungLionOrganization/JSON-API-Forge/actions/workflows/editor-codeql.yml?query=branch%3AEditor)
[![Version 0.5.2](https://img.shields.io/badge/version-0.5.2-D4A017)](../../VERSION)
[![Qt 6.4+](https://img.shields.io/badge/Qt-6.4%2B-41CD52?logo=qt)](../../QT-CREATOR-BUILD-GUIDE.md)
[![Source available](https://img.shields.io/badge/license-source--available-555555)](../../LICENSE)

JSON API Forge Editor supports reviewed native Qt plugins and a catalog served by JSON API Forge itself. Discovery never enables code: the operator must approve a plugin ID from **Plugins → Manage plugins…** after placing its library and manifest in this directory or the per-user application-data `plugins` directory.

## Import a plugin ZIP

Choose **Plugins → Import plugin from ZIP…** and select a package with exactly one manifest. The manifest and native library may be at the archive root, or every entry may sit below one portable package directory:

```text
vendor.plugin-name/
├── vendor.plugin-name.forgeplugin.json
└── PluginName.so
```

Use `.dll` on Windows and `.dylib` on macOS. Imports are staged atomically in the per-user application-data plugin directory. The importer accepts at most 256 entries, a 64 MiB archive, a 64 MiB individual file and 128 MiB extracted data. It also enforces a bounded compression ratio, portable/case-unique paths, a single shallow manifest, regular files/directories only, ZIP size and CRC checks, a matching native-library SHA-256, and Plugin API v2 compatibility.

Native plugins run with the Editor process's authority and are not sandboxed. Import only reviewed packages from a trusted publisher. A successful import is deliberately removed from the enabled-ID set; open **Manage plugins…** to review its identity, version, digest and permissions before enabling it.

## Native manifest contract

Name the manifest `NAME.forgeplugin.json` and keep the referenced library beside it:

```json
{
  "id": "vendor.plugin-name",
  "name": "Plugin Name",
  "version": "1.0.0",
  "apiVersion": 2,
  "library": "PluginName.so",
  "sha256": "0123456789abcdef0123456789abcdef0123456789abcdef0123456789abcdef",
  "permissions": [
    "workspace.read",
    "graph.nodes.register"
  ]
}
```

Use `.dll` on Windows and `.dylib` on macOS. `sha256` is the lowercase digest of the exact native library. The manifest and library may not be symlinks; the canonical library path must stay inside the manifest directory. IDs and permission names use lowercase dotted identifiers and the permissions list is capped at 32 entries.

The library implements `ForgeEditor::IEditorPlugin` from [IEditorPlugin.hpp](../include/forgeeditor/IEditorPlugin.hpp), declares `dev.jsonapiforge.EditorPlugin/2.0`, and returns `ForgeEditor::PluginApiVersion`. During `initialize`, the host supports:

```cpp
host->addPaletteComponent("Queue", "Resources", queueFragment);
host->addGraphNodeType("Validated Queue Publish", "vendor.queue.publish", defaultProperties);
host->addToolAction(action);
host->addDockWidget(Qt::RightDockWidgetArea, dock);
host->showStatusMessage("Queue tools ready");
```

Plugin-declared permissions are review metadata, not an operating-system sandbox. The Editor rechecks manifest/API/runtime identity and hashes the full binary before `QPluginLoader` executes it.

Plugin graph nodes are preserved in the graph document and rendered on the canvas, but v0.5.2 does not grant native plugins an implicit configuration compiler. Compile preview rejects a custom/design-only node with its type name instead of silently omitting it; plugins may provide a reviewed tool action that emits ordinary Forge fragments and then runs the normal validation workflow.

## Forge-backed catalog

The catalog browser calls a standard resource endpoint:

```text
GET /api/<catalog-project>/v1/<resource>?limit=100&offset=0
X-API-Key: <catalog-reader-key>
```

Each item must contain:

```json
{
  "plugin_id": "vendor.plugin-name",
  "name": "Plugin Name",
  "version": "1.0.0",
  "publisher": "Vendor",
  "download_url": "https://downloads.example/plugin-name-1.0.0.zip",
  "sha256": "0123456789abcdef0123456789abcdef0123456789abcdef0123456789abcdef",
  "permissions": ["workspace.read", "graph.nodes.register"],
  "enabled": true
}
```

The v0.5.2 Editor validates a maximum of 100 records/2 MiB, safe identity fields, unique plugin-version pairs, HTTPS package URLs, SHA-256 metadata and bounded permission arrays. It rejects TLS errors, redirects, URL credentials and non-loopback cleartext endpoints. The catalog UI copies the reviewed package URL but intentionally does not auto-download, install or enable native code. Downloaded packages must still be selected through the explicit ZIP import action. The `EditorPluginRegistry` project on the `exampleApps` branch is a ready Forge catalog backend.

For distribution, publish reproducible source, per-platform binaries, checksums and a cryptographic publisher signature through a trusted channel. SHA-256 proves that a reviewed file did not change; it does not prove who created it.

## Build and troubleshoot

Build against the same Qt version, compiler ABI, architecture and Plugin API as the target Editor. A Windows DLL cannot be loaded by a Linux Editor, and a matching API number does not make different C++ toolchains ABI-compatible. Follow the [Qt Creator guide](../../QT-CREATOR-BUILD-GUIDE.md) for kit selection.

| Import or load problem | Check |
|---|---|
| Digest mismatch | Recompute SHA-256 after the final native binary build; update the manifest before making the ZIP. |
| Unsafe archive path | Keep one shallow package directory; remove symlinks, traversal segments, duplicate case-insensitive names and unrelated nested archives. |
| Imported but inactive | Open **Manage plugins…** and explicitly enable the reviewed ID. |
| Load/ABI error | Match platform, CPU architecture, Qt kit, compiler and API v2; include dependencies permitted by their licenses. |
| Graph compile rejection | Replace design-only nodes with supported Forge operations or use a reviewed export tool. |

The code and examples in this repository follow [LICENSE](../../LICENSE). A separately authored plugin needs its own clear licensing/provenance record, and may not redistribute protected Editor code or branding beyond the permissions actually granted. See [third-party notices](../../THIRD_PARTY_NOTICES.md), [trademark policy](../../TRADEMARK_POLICY.md) and [AI contribution policy](../../AI_USAGE_POLICY.md).
