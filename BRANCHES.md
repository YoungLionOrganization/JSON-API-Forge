# Branch ownership for v0.5.1

The four release branches are components, not copies of one monorepo tree.
Their workflows fail early when files owned by another component appear.

| Branch | Owned content | Not owned |
|---|---|---|
| `main` | Forge runtime, CLI, schemas, TypeScript reference client, deployment templates and server docs | Qt Editor, Python SDK package, generated example applications |
| `Editor` | C++20/Qt Editor, visual assets, plugin SDK, installer/portable packaging and Editor docs | Forge server runtime, Python SDK, example applications |
| `python-library` | `json-api-forge-client` package, SDK tests, contract test and package workflow | Forge server runtime, Qt Editor, example applications |
| `exampleApps` | Generator, 25 generated applications, smoke tests and example documentation | Forge runtime implementation, Qt Editor, Python SDK |

Cross-component workflows explicitly check out `main` into a separate
temporary directory when they need the canonical runtime. Do not merge the
full tree of one release branch into another.
