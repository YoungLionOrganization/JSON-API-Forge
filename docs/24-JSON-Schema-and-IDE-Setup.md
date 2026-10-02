# JSON Schema and IDE Setup

Forge generates Draft 2020-12 JSON Schemas from the strict Pydantic project model. `project.schema.json` describes a complete merged project. `manifest.schema.json` covers the base `app.json`/`manifest.json`, while `fragment.schema.json` covers numbered partial files; both omit the merged model's top-level required list without relaxing unknown-field checks.

VS Code associations map `app/*/app.json`, `manifest.json`, and project fragment paths to their matching schemas. Editors can therefore catch typos before runtime without falsely requiring fields supplied by another file. `forge validate` remains authoritative because it also resolves environment values, merges fragments and applies cross-field validators.

Run `forge schema` after model changes and verify `git diff --exit-code -- schemas` in CI. v0.5.1 schemas include current `0.5.1` defaults.
