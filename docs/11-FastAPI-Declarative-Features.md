# FastAPI Declarative Features

Custom endpoints expose FastAPI behavior without writing a router for every endpoint: methods, path parameters, headers/query/cookies, JSON Schema input, dependencies, tags, summaries, descriptions, deprecation, OpenAPI extras and response kinds.

Response kinds include JSON, text, HTML, redirect, stream, file and empty. v0.4.1 uses FastAPI-compatible JSON encoding before `JSONResponse`, avoiding the deprecated `ORJSONResponse` path while preserving datetime/UUID/model serialization.

Explicit `public` endpoints receive an OpenAPI `security: []` override. Private endpoints remain under project authentication. Internal dependency/default parameters are hidden from generated public signatures/operation IDs.
