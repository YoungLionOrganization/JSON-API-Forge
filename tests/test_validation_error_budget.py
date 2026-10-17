import pytest
from fastapi import HTTPException

from framework import validation


def test_large_invalid_array_stops_collecting_after_diagnostic_budget(monkeypatch):
    original = validation.LinearDraft202012Validator
    yielded = 0

    class ObservedValidator:
        def __init__(self, schema):
            self.validator = original(schema)

        def iter_errors(self, instance):
            nonlocal yielded
            for error in self.validator.iter_errors(instance):
                yielded += 1
                yield error

    monkeypatch.setattr(validation, "LinearDraft202012Validator", ObservedValidator)
    with pytest.raises(HTTPException) as raised:
        validation.validate_json_schema(["invalid"] * 10_000, {"type": "array", "items": {"type": "integer"}})
    assert raised.value.status_code == 422
    assert len(raised.value.detail["errors"]) == 20
    assert yielded <= 21
    assert raised.value.detail["errors"][0]["path"] == "0"


def test_error_budget_does_not_skip_valid_values_or_late_invalid_value():
    schema = {"type": "array", "items": {"type": "integer"}}
    validation.validate_json_schema(list(range(4096)), schema)
    values = list(range(4096)) + ["invalid"]
    with pytest.raises(HTTPException) as raised:
        validation.validate_json_schema(values, schema)
    assert raised.value.detail["errors"][0]["path"] == "4096"
