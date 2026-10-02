# Multi-Project Configuration

Each project is a direct child of the configured apps directory and must contain its own project manifest (`app.json`). Numbered fragments belong under that project's `config/` directory. This makes ownership and discovery unambiguous.

Example:
```text
app/
├── App1/
│   ├── app.json
│   ├── config/10-databases.json
│   └── hooks/
└── App2/
    └── app.json
```
Fragments are merged alphabetically. Split by responsibility rather than by arbitrary size: databases/security/performance/resources/domain operations/data-events is a good default.

Project slugs and API prefixes must not create route ambiguity. `forge doctor` performs cross-project diagnostics, including collisions. Runtime services are project scoped even though the FastAPI process and internal metadata engine are shared.

v0.4.1 removes the accidental legacy root `app/config` pseudo-project and root `app/hooks`. Put all runtime project material under an explicit project directory.
