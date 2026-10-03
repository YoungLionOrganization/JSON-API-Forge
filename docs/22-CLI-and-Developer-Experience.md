# CLI and Developer Experience

The CLI is the primary operator/developer interface for initialization, project creation, validation, diagnostics, schema/OpenAPI generation, migration, routes and development serving.

`forge init` is safer than hand-copying a secret template. `forge doctor` explains invalid or risky choices beyond schema shape. `forge schema` keeps IDE/editor assistance aligned with typed models. `forge routes`/`openapi` let reviewers inspect the actual generated API before deployment.

CI should invoke the installed console entry point so packaging problems are caught, not only `python forge.py`. The repository retains `forge.py` as a convenient source-checkout entry point.

`forge init` creates `.env` through an exclusive atomic publication, or atomically
rotates managed secrets with `--force` while preserving other settings. Symbolic
and hard-linked destinations are rejected. On POSIX, the temporary file and
published file have mode 0600 from the first byte. Windows inherits the parent
directory's ACL; keep the deployment directory private to the Forge account.

`forge secrets --output generated.txt --count 2` creates a secret file without
overwriting an existing destination or printing the credentials. With no output
option, secrets are shown only to an interactive terminal. Redirected output
requires the explicit `--stdout` option; avoid sending it to CI logs or retained
shell transcripts. `--count` accepts 1 through 100.
