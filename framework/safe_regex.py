from __future__ import annotations

from functools import lru_cache
from typing import Any

from pydantic_core import SchemaError, SchemaValidator, ValidationError, core_schema

_MAX_PATTERN_LENGTH = 512
_MAX_SCHEMA_DEPTH = 64
_MAX_SCHEMA_NODES = 20_000


@lru_cache(maxsize=512)
def _compiled(pattern: str) -> SchemaValidator:
    if not isinstance(pattern, str) or not pattern or len(pattern) > _MAX_PATTERN_LENGTH:
        raise ValueError(f"regular-expression patterns must contain 1-{_MAX_PATTERN_LENGTH} characters")
    if any(ord(character) < 0x20 or ord(character) == 0x7F for character in pattern):
        raise ValueError("regular-expression patterns may not contain control characters")
    try:
        # Pydantic Core uses Rust's linear-time regex engine. Unsupported
        # backreferences and look-around constructs fail closed at config load.
        return SchemaValidator(core_schema.str_schema(pattern=pattern))
    except SchemaError as exc:
        raise ValueError(f"pattern is invalid or uses a non-linear regex feature: {pattern!r}") from exc


def validate_safe_pattern(pattern: str) -> None:
    _compiled(pattern)


def safe_pattern_matches(pattern: str, value: str) -> bool:
    validator = _compiled(pattern)
    try:
        validator.validate_python(value)
    except ValidationError:
        return False
    return True


def validate_schema_patterns(schema: dict[str, Any] | None) -> None:
    if schema is None:
        return
    nodes = 0
    contains_pattern_properties = False
    contains_unevaluated_properties = False

    def walk(value: Any, depth: int) -> None:
        nonlocal contains_pattern_properties, contains_unevaluated_properties, nodes
        nodes += 1
        if nodes > _MAX_SCHEMA_NODES or depth > _MAX_SCHEMA_DEPTH:
            raise ValueError("JSON Schema is too large or deeply nested")
        if isinstance(value, dict):
            pattern = value.get("pattern")
            if pattern is not None:
                if not isinstance(pattern, str):
                    raise ValueError("JSON Schema pattern must be a string")
                validate_safe_pattern(pattern)
            pattern_properties = value.get("patternProperties")
            if pattern_properties is not None:
                contains_pattern_properties = True
                if not isinstance(pattern_properties, dict) or len(pattern_properties) > 256:
                    raise ValueError("JSON Schema patternProperties must be an object with at most 256 patterns")
                for property_pattern in pattern_properties:
                    validate_safe_pattern(property_pattern)
            if "unevaluatedProperties" in value:
                contains_unevaluated_properties = True
            for child in value.values():
                walk(child, depth + 1)
        elif isinstance(value, list):
            for child in value:
                walk(child, depth + 1)

    walk(schema, 0)
    if contains_pattern_properties and contains_unevaluated_properties:
        raise ValueError("patternProperties cannot be combined with unevaluatedProperties in one JSON Schema document")
