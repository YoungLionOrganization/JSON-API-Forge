# CLI and Developer Experience

The CLI is the primary operator/developer interface for initialization, project creation, validation, diagnostics, schema/OpenAPI generation, migration, routes and development serving.

`forge init` is safer than hand-copying a secret template. `forge doctor` explains invalid or risky choices beyond schema shape. `forge schema` keeps IDE/editor assistance aligned with typed models. `forge routes`/`openapi` let reviewers inspect the actual generated API before deployment.

CI should invoke the installed console entry point so packaging problems are caught, not only `python forge.py`. The repository retains `forge.py` as a convenient source-checkout entry point.
