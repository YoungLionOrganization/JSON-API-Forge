from __future__ import annotations

import math
from typing import Any

from fastapi import HTTPException, Request
from jsonschema import Draft202012Validator
from jsonschema.exceptions import ValidationError
from jsonschema.validators import extend

from .config import RequestParameterSpec
from .safe_regex import safe_pattern_matches


def _pattern(validator, pattern, instance, schema):
    if validator.is_type(instance, "string"):
        try:
            matches = safe_pattern_matches(pattern, instance)
        except ValueError as exc:
            yield ValidationError(str(exc))
            return
        if not matches:
            yield ValidationError(f"{instance!r} does not match {pattern!r}")


def _pattern_properties(validator, patterns, instance, schema):
    if not validator.is_type(instance, "object"):
        return
    for pattern, subschema in patterns.items():
        for key, value in instance.items():
            try:
                matches = safe_pattern_matches(pattern, key)
            except ValueError as exc:
                yield ValidationError(str(exc))
                return
            if matches:
                yield from validator.descend(value, subschema, path=key, schema_path=pattern)


def _additional_properties(validator, additional, instance, schema):
    if not validator.is_type(instance, "object"):
        return
    properties = schema.get("properties", {})
    patterns = schema.get("patternProperties", {})
    extras = []
    for key in instance:
        if key in properties:
            continue
        try:
            matched = any(safe_pattern_matches(pattern, key) for pattern in patterns)
        except ValueError as exc:
            yield ValidationError(str(exc))
            return
        if not matched:
            extras.append(key)
    if validator.is_type(additional, "object"):
        for key in extras:
            yield from validator.descend(instance[key], additional, path=key)
    elif not additional and extras:
        rendered = ", ".join(repr(key) for key in sorted(extras, key=str))
        yield ValidationError(f"Additional properties are not allowed ({rendered} unexpected)")


LinearDraft202012Validator = extend(
    Draft202012Validator,
    {
        "pattern": _pattern,
        "patternProperties": _pattern_properties,
        "additionalProperties": _additional_properties,
    },
)


def validate_json_schema(instance: Any, schema: dict[str, Any] | None, *, label: str = "request") -> None:
    if not schema:
        return
    validator = LinearDraft202012Validator(schema)
    errors = sorted(validator.iter_errors(instance), key=lambda e: list(e.absolute_path))
    if not errors:
        return
    details = []
    for error in errors[:20]:
        path = ".".join(str(x) for x in error.absolute_path) or "$"
        details.append({"path": path, "message": error.message})
    raise HTTPException(status_code=422, detail={"schema": label, "errors": details})


def _raw_parameter(request: Request, spec: RequestParameterSpec):
    if spec.location == "query":
        return request.query_params.get(spec.name)
    if spec.location == "header":
        return request.headers.get(spec.name)
    if spec.location == "cookie":
        return request.cookies.get(spec.name)
    return request.path_params.get(spec.name)


def _coerce_parameter(raw: str, spec: RequestParameterSpec):
    try:
        if spec.type == "integer":
            return int(raw)
        if spec.type == "number":
            value = float(raw)
            if not math.isfinite(value):
                raise ValueError("Number must be finite")
            return value
        if spec.type == "boolean":
            low = str(raw).lower()
            if low in {"true", "1", "yes", "on"}:
                return True
            if low in {"false", "0", "no", "off"}:
                return False
            raise ValueError
        return str(raw)
    except (TypeError, ValueError) as exc:
        raise HTTPException(status_code=422, detail=f"Parameter {spec.name!r} must be {spec.type}") from exc


def validate_request_parameters(request: Request, specs: list[RequestParameterSpec]) -> dict[str, Any]:
    values: dict[str, Any] = {}
    for spec in specs:
        raw = _raw_parameter(request, spec)
        if raw is None:
            if spec.required and spec.default is None:
                raise HTTPException(status_code=422, detail=f"Missing required {spec.location} parameter: {spec.name}")
            values[spec.name] = spec.default
            continue
        value = _coerce_parameter(raw, spec)
        if spec.enum is not None and value not in spec.enum:
            raise HTTPException(status_code=422, detail=f"Parameter {spec.name!r} must be one of {spec.enum}")
        if isinstance(value, (int, float)):
            if spec.minimum is not None and value < spec.minimum:
                raise HTTPException(status_code=422, detail=f"Parameter {spec.name!r} is below minimum")
            if spec.maximum is not None and value > spec.maximum:
                raise HTTPException(status_code=422, detail=f"Parameter {spec.name!r} is above maximum")
        if isinstance(value, str):
            if spec.min_length is not None and len(value) < spec.min_length:
                raise HTTPException(status_code=422, detail=f"Parameter {spec.name!r} is too short")
            if spec.max_length is not None and len(value) > spec.max_length:
                raise HTTPException(status_code=422, detail=f"Parameter {spec.name!r} is too long")
            if spec.pattern and not safe_pattern_matches(spec.pattern, value):
                raise HTTPException(status_code=422, detail=f"Parameter {spec.name!r} does not match pattern")
        values[spec.name] = value
    request.state.validated_parameters = values
    return values


def openapi_parameters(specs: list[RequestParameterSpec]) -> list[dict[str, Any]]:
    out = []
    for spec in specs:
        schema: dict[str, Any] = {"type": spec.type}
        if spec.enum is not None:
            schema["enum"] = spec.enum
        if spec.minimum is not None:
            schema["minimum"] = spec.minimum
        if spec.maximum is not None:
            schema["maximum"] = spec.maximum
        if spec.min_length is not None:
            schema["minLength"] = spec.min_length
        if spec.max_length is not None:
            schema["maxLength"] = spec.max_length
        if spec.pattern:
            schema["pattern"] = spec.pattern
        if spec.default is not None:
            schema["default"] = spec.default
        out.append(
            {
                "name": spec.name,
                "in": spec.location,
                "required": True if spec.location == "path" else spec.required,
                "description": spec.description,
                "schema": schema,
            }
        )
    return out
