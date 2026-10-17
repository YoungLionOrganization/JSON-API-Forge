import json
import sqlite3
import warnings
from contextlib import closing

import pytest
from fastapi import HTTPException
from fastapi.testclient import TestClient
from sqlalchemy import Column, Float, Numeric

from framework.crud import _coerce_for_column
from framework.factory import create_app
from framework.settings import settings
from framework.validation import validate_json_schema


@pytest.fixture(params=[True, False, "decimal"], ids=["numeric-schema", "no-schema", "sqlite-decimal"])
def numeric_app(tmp_path, monkeypatch, request):
    project = tmp_path / "app/Numeric"
    project.mkdir(parents=True)
    database = tmp_path / "business.db"
    schema = {"type": "object", "properties": {"value": {"type": "number", "minimum": 0}}} if request.param is True else None
    config = {
        "slug": "numeric",
        "name": "Numeric",
        "audit_enabled": False,
        "databases": {"primary": {"url": f"sqlite+aiosqlite:///{database}"}},
        "security": {"bootstrap_enabled": True, "bootstrap_admin_key": "B" * 48, "jwt_enabled": False},
        "cache": {"enabled": False},
        "rate_limit": {"enabled": False},
        "resources": [
            {
                "database": "primary",
                "table": "numbers",
                "path": "numbers",
                "auto_create": True,
                "batch_enabled": True,
                "columns": {
                    "id": {"type": "integer", "primary_key": True, "nullable": False},
                    "value": {"type": "float", "nullable": True},
                },
                "writable_fields": ["value"],
                "create_schema": schema,
                "update_schema": schema,
            }
        ],
        "operations": [
            {
                "name": "append-number",
                "path": "rpc/append-number",
                "permission": "*",
                "transaction": True,
                "input_schema": schema,
                "statements": [{"sql": "INSERT INTO numbers (value) VALUES (:value)", "params": {"value": "$body.value"}}],
            }
        ],
    }
    if request.param == "decimal":
        with closing(sqlite3.connect(database)) as connection:
            connection.execute("CREATE TABLE numbers(id INTEGER PRIMARY KEY, value DECIMAL(1000,2))")
            connection.commit()
        config["resources"][0]["auto_create"] = False
        config["resources"][0].pop("columns")
    (project / "app.json").write_text(json.dumps(config), encoding="utf-8")
    for name, value in {
        "apps_dir": tmp_path / "app",
        "internal_database_url": f"sqlite+aiosqlite:///{tmp_path / 'internal.db'}",
        "jwt_secret": "J" * 64,
        "bootstrap_admin_key": "",
        "app_env": "development",
        "redis_url": None,
    }.items():
        monkeypatch.setattr(settings, name, value)
    with TestClient(create_app(), raise_server_exceptions=False) as client:
        response = client.post(
            "/api/numeric/v1/admin/api-keys", headers={"X-API-Key": "B" * 48}, json={"permissions": ["*"], "name": "test"}
        )
        assert response.status_code == 200, response.text
        client.headers.update({"X-API-Key": response.json()["api_key"], "Content-Type": "application/json"})
        yield client, database


def test_nonfinite_payloads_never_commit_any_resource_or_rpc_write(numeric_app):
    client, database = numeric_app
    base = "/api/numeric/v1/"
    response = client.post(base + "numbers", json={"value": 1.25})
    assert response.status_code == 200, response.text
    row_id = response.json()["id"]
    for token in ("NaN", "Infinity", "-Infinity", "1e999", '"NaN"', '"Infinity"', '"1e999"'):
        body = '{"value":' + token + "}"
        for method, endpoint, payload in [
            ("POST", "numbers", body),
            ("PATCH", f"numbers/{row_id}", body),
            ("PUT", f"numbers/{row_id}", body),
            ("POST", "numbers/_batch", '[{"value":2.5},' + body + "]"),
            ("POST", "rpc/append-number", body),
        ]:
            if endpoint == "rpc/append-number" and token.startswith('"'):
                continue  # A schema-free SQL RPC may legitimately bind text.
            response = client.request(method, base + endpoint, content=payload)
            assert response.status_code == 422, (method, endpoint, token, response.text)
            with closing(sqlite3.connect(database)) as connection:
                assert connection.execute("SELECT value FROM numbers").fetchall() == [(1.25,)]


def test_cache_disabled_successful_mutations_do_not_leak_coroutines(numeric_app):
    client, _ = numeric_app
    base = "/api/numeric/v1/numbers"
    with warnings.catch_warnings(record=True) as caught:
        warnings.simplefilter("always", RuntimeWarning)
        response = client.post(base, json={"value": 1.25})
        assert response.status_code == 200, response.text
        row_id = response.json()["id"]
        assert client.patch(f"{base}/{row_id}", json={"value": 2.5}).status_code == 200
        assert client.put(f"{base}/{row_id}", json={"value": 3.5}).status_code == 200
        assert client.post(base + "/_batch", json=[{"value": 4.5}]).status_code == 200
        assert client.delete(f"{base}/{row_id}").status_code == 200
    assert not [warning for warning in caught if issubclass(warning.category, RuntimeWarning)]


@pytest.mark.parametrize("schema", [None, {}, {"type": "object"}])
def test_nested_nonfinite_json_is_rejected_without_requiring_numeric_schema(schema):
    with pytest.raises(HTTPException) as raised:
        validate_json_schema({"nested": [1, {"amount": float("nan")}]}, schema)
    assert raised.value.status_code == 422
    assert raised.value.detail["errors"][0]["path"] == "nested.1.amount"


@pytest.mark.parametrize("value", ["NaN", "Infinity", "-Infinity", "1e999", 10**400])
def test_float_column_conversion_rejects_nonfinite_or_overflowing_values(value):
    with pytest.raises(HTTPException) as raised:
        _coerce_for_column(Column("value", Float), value)
    assert raised.value.status_code == 422


@pytest.mark.parametrize("value", ["NaN", "Infinity", "-Infinity"])
def test_decimal_column_conversion_rejects_nonfinite_values(value):
    with pytest.raises(HTTPException) as raised:
        _coerce_for_column(Column("value", Numeric), value)
    assert raised.value.status_code == 422


@pytest.mark.parametrize("column_type", [Float, Numeric])
def test_numeric_column_conversion_keeps_finite_strings_and_null(column_type):
    column = Column("value", column_type)
    assert _coerce_for_column(column, "1.25") == 1.25
    assert _coerce_for_column(column, None) is None


def test_numeric_range_validation_uses_actual_database_bind_policy():
    from decimal import Decimal

    from sqlalchemy.dialects.postgresql.asyncpg import PGDialect_asyncpg
    from sqlalchemy.dialects.sqlite import dialect as SQLiteDialect

    column = Column("value", Numeric(1000, 2))
    with pytest.raises(HTTPException) as raised:
        _coerce_for_column(column, "1e999", dialect=SQLiteDialect())
    assert raised.value.status_code == 422
    # Native Decimal binding must not inherit SQLite's float conversion limit.
    assert _coerce_for_column(column, "1e999", dialect=PGDialect_asyncpg()) == Decimal("1e999")
