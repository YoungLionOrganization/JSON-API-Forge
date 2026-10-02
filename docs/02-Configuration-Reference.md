# Configuration Reference

Every project validates as `ProjectConfig`. Important top-level sections are `databases`, `mongo_databases`, `security`, `cache`, `rate_limit`, `protection`, `observability`, `realtime`, `roles`, `resources`, `mongo_resources`, `operations`, `data_sources`, `dependencies`, `custom_endpoints`, `event_channels`, `webhook_docs`, `media` and `features`.

Use `schemas/project.schema.json` for a complete merged project, `schemas/manifest.schema.json` for `app.json`/`manifest.json`, and `schemas/fragment.schema.json` for numbered partial fragments. VS Code associations are included in `.vscode/settings.json`.

Environment interpolation is supported by configuration loading. Prefer environment values for credentials/connection strings and keep `.env` untracked. Strict validation rejects unknown fields and invalid combinations instead of silently ignoring them.

Current framework default version metadata is `0.5.1`; application-specific `version` values remain their own product versions.

The generated schema is authoritative for field shapes. Run `forge schema` after changing typed config models and commit the generated schema change with the model change.
