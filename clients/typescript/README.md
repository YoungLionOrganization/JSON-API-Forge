# JSON API Forge TypeScript reference client

[![Version 0.5.2](https://img.shields.io/badge/version-0.5.2-D4A017)](../../VERSION)
[![Server CI](https://github.com/YoungLionOrganization/JSON-API-Forge/actions/workflows/ci.yml/badge.svg?branch=main)](https://github.com/YoungLionOrganization/JSON-API-Forge/actions/workflows/ci.yml?query=branch%3Amain)
[![Source available](https://img.shields.io/badge/license-source--available-555555)](../../LICENSE)

A small, typed `fetch` client for application CRUD and RPC endpoints. This private reference package lives on `main`; it is not published automatically to npm and does not implement the Editor account control plane. Python users can use the separate [Python SDK](https://github.com/YoungLionOrganization/JSON-API-Forge/tree/python-library).

## Check and compile

From this directory, with Node.js 22 and npm:

```bash
npm install
npm run typecheck
npm test
npx tsc --outDir dist --noEmit false --module NodeNext --moduleResolution NodeNext
```

`npm test` uses a separate `.test-dist` directory. The explicit compile command produces `dist/index.js`; import that file from a local JavaScript entry point:

```javascript
import { ForgeClient, ForgeAPIError } from "./dist/index.js";

const forge = new ForgeClient({
  baseUrl: "https://api.example.com/api/task-board/v1",
  apiKey: process.env.FORGE_API_KEY,
});

try {
  const tasks = await forge.list("tasks", { limit: 25 });
  console.log(tasks);
} catch (error) {
  if (error instanceof ForgeAPIError) {
    console.error(error.status, error.requestId);
  } else {
    throw error;
  }
}
```

Use the resource path configured by your project. `baseUrl` includes the application API prefix; paths passed to `request` remain within it. `get`, `create`, `update`, `delete` and `rpc` provide the corresponding helpers. `request<T>` and helper type parameters describe the expected result without replacing server-side schema validation.

## Transport and credentials

Remote endpoints require HTTPS; local development HTTP needs `allowHttpForLoopback: true`. Defaults are a 10-second timeout and a 4 MiB response limit, configurable through `timeoutMs` and `maxResponseBytes`. Redirects are rejected, paths are bounded to the configured base, and `AbortSignal` cancellation is supported. The client does not automatically retry requests.

Use either `apiKey` or `bearerToken`. Keep privileged application keys in a server-side secret store; a browser bundle cannot keep an embedded key secret. Custom `fetchImpl` is available for tests or an approved transport. Avoid logging full error payloads when they contain application data.

## Documentation and license

Read the [API semantics guide](../../docs/32-API-Semantics-and-HTTP-Contracts.md) and [server README](../../README.md). This reference client's original code uses [LicenseRef-JAF-SASH-1.1](../../LICENSE); see the [license FAQ](../../LICENSE-FAQ.md) and [third-party notices](../../THIRD_PARTY_NOTICES.md). Follow [CONTRIBUTING.md](../../CONTRIBUTING.md) for changes.
