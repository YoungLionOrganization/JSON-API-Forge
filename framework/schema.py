from __future__ import annotations

import copy
import json
from pathlib import Path

from .config import ProjectConfig


def _with_schema_metadata(schema: dict, *, title: str, description: str) -> dict:
    out = copy.deepcopy(schema)
    out["$schema"] = "https://json-schema.org/draft/2020-12/schema"
    out["title"] = title
    out["description"] = description
    properties = out.setdefault("properties", {})
    properties["$schema"] = {
        "type": "string",
        "description": "Optional editor-only JSON Schema reference. Ignored by the runtime.",
    }
    return out


def project_schema() -> dict:
    schema = ProjectConfig.model_json_schema(mode="validation")
    return _with_schema_metadata(
        schema,
        title="JSON API Forge project configuration",
        description="Complete merged configuration for one app/NAME project.",
    )


def fragment_schema() -> dict:
    schema = project_schema()
    schema["title"] = "JSON API Forge configuration fragment"
    schema["description"] = (
        "Partial project configuration. Files below app/NAME/config/*.json are merged alphabetically before strict typed validation."
    )
    schema.pop("required", None)
    return schema


def manifest_schema() -> dict:
    schema = project_schema()
    schema["title"] = "JSON API Forge project manifest"
    schema["description"] = (
        "Base app/NAME/app.json or manifest.json document. Numbered config fragments provide the remaining fields before strict validation."
    )
    schema.pop("required", None)
    return schema


def write_schemas(directory: Path) -> list[Path]:
    directory.mkdir(parents=True, exist_ok=True)
    outputs = {
        directory / "project.schema.json": project_schema(),
        directory / "manifest.schema.json": manifest_schema(),
        directory / "fragment.schema.json": fragment_schema(),
    }
    for path, value in outputs.items():
        path.write_text(json.dumps(value, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    return list(outputs)
