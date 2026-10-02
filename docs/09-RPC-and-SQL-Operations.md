# RPC and SQL Operations

Operations are named, permissioned, predeclared SQL workflows. Each statement has fixed SQL text, a mode (`execute`, `fetch_one`, `fetch_all`, `scalar`), bound parameter sources, optional result name, result limits and row-count guards.

Parameter sources can refer to validated body fields, path/query/header parameters, the authenticated principal and request ID. `$` references are resolved as values. SQL string construction from user input is not a supported pattern.

Transactional operations run all statements in one DB transaction. Idempotent operations require transactions and store the request fingerprint/result in the same business transaction. Reusing a key with a different canonical input returns conflict. A completed identical request returns the stored result with replay metadata; replay does not re-trigger invalidations/background hooks.
