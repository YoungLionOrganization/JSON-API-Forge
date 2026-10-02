from __future__ import annotations

from pathlib import Path

import pytest
from fastapi import HTTPException
from sqlalchemy import Boolean, Column, Integer, MetaData, String, Table
from starlette.requests import Request

from framework.config import ColumnConfig, DatabaseConfig, ResourceConfig
from framework.crud import (
    _base_clauses,
    _coerce_for_column,
    _filter_clause,
    _parse_int,
    _tenant_clause,
    _visible,
    _write_payload,
    build_filter_clauses,
)
from framework.db import _column_type, _engine_kwargs, _ensure_sqlite_dir, build_declared_table
from framework.security import Principal


def req(query: bytes = b"") -> Request:
    return Request(
        {
            "type": "http",
            "method": "GET",
            "path": "/items",
            "headers": [],
            "query_string": query,
            "server": ("test", 80),
            "client": ("127.0.0.1", 1),
            "scheme": "http",
        }
    )


def resource(**kwargs) -> ResourceConfig:
    base = {
        "path": "items",
        "table": "items",
        "database": "primary",
        "primary_key": "id",
        "permissions": {
            "list": "items.list",
            "read": "items.read",
            "create": "items.create",
            "update": "items.update",
            "delete": "items.delete",
        },
        "allowed_filters": ["id", "name", "active"],
        "filter_operators": ["eq", "ne", "gt", "gte", "lt", "lte", "like", "ilike", "in", "isnull"],
        "allowed_sort": ["id", "name"],
        "search_fields": ["name"],
    }
    base.update(kwargs)
    return ResourceConfig.model_validate(base)


def table() -> Table:
    return Table(
        "items",
        MetaData(),
        Column("id", Integer, primary_key=True),
        Column("name", String),
        Column("active", Boolean),
        Column("tenant_id", String),
        Column("deleted_at", String),
    )


def principal(tenant_id="tenant-a") -> Principal:
    return Principal(kind="api_key", subject="k1", roles={"user"}, permissions={"*"}, tenant_id=tenant_id)


def test_crud_visibility_write_coercion_and_pagination_helpers():
    t = table()
    r = resource(hidden_fields=["active"], readable_fields=["id", "name", "active"], writable_fields=["name", "active"])
    assert _visible({"id": 1, "name": "A", "active": True, "tenant_id": "x"}, r) == {"id": 1, "name": "A"}
    assert _write_payload({"name": "B", "active": False}, r, t) == {"name": "B", "active": False}
    with pytest.raises(HTTPException):
        _write_payload({"id": 5, "name": "B"}, r, t)
    assert (
        _coerce_for_column(t.c.id, "42") == 42
        and _coerce_for_column(t.c.active, "yes") is True
        and _coerce_for_column(t.c.active, "off") is False
    )
    with pytest.raises(HTTPException):
        _coerce_for_column(t.c.id, "not-an-int")
    assert _parse_int(None, 5) == 5
    with pytest.raises(HTTPException):
        _parse_int("-3", 5, minimum=1)
    with pytest.raises(HTTPException):
        _parse_int("x", 5)


def test_crud_tenant_soft_delete_and_filter_building():
    t = table()
    r = resource(tenant_field="tenant_id", soft_delete_field="deleted_at")
    assert _tenant_clause(r, t, principal()) is not None and len(_base_clauses(r, t, principal())) == 2
    with pytest.raises(HTTPException):
        _tenant_clause(r, t, principal(None))
    clauses = build_filter_clauses(req(b"id__gte=2&name__ilike=a%25&active=true&q=test&unknown=x&limit=5"), t, r)
    assert len(clauses) == 4
    for op in ("eq", "ne", "gt", "gte", "lt", "lte", "like", "ilike", "in", "isnull"):
        assert (
            _filter_clause(
                t.c.id if op not in {"like", "ilike"} else t.c.name, op, "1,2" if op == "in" else ("true" if op == "isnull" else "1")
            )
            is not None
        )
    with pytest.raises(HTTPException):
        _filter_clause(t.c.id, "wat", "1")
    with pytest.raises(RuntimeError):
        _tenant_clause(resource(tenant_field="does_not_exist"), t, principal())
    with pytest.raises(RuntimeError):
        _base_clauses(resource(soft_delete_field="missing"), t, principal())


def test_db_declared_table_types_engine_kwargs_and_sqlite_directory(tmp_path: Path):
    for kind in ("integer", "float", "boolean", "text", "datetime", "json", "string"):
        assert _column_type(ColumnConfig(type=kind)) is not None
    r = resource(
        auto_create=True,
        columns={
            "id": {"type": "integer", "primary_key": True, "nullable": False},
            "name": {"type": "string", "max_length": 64, "nullable": False, "index": True},
            "meta": {"type": "json", "nullable": True},
        },
    )
    built = build_declared_table(MetaData(), r)
    assert list(built.c.keys()) == ["id", "name", "meta"] and built.c.id.autoincrement is True
    with pytest.raises(RuntimeError):
        build_declared_table(MetaData(), resource(auto_create=True, columns={}))
    assert _engine_kwargs(DatabaseConfig(url="sqlite+aiosqlite:///x.db", echo=True, pool_pre_ping=True)) == {
        "echo": True,
        "pool_pre_ping": True,
    }
    kwargs = _engine_kwargs(
        DatabaseConfig(
            url="postgresql+asyncpg://u:p@localhost/db",
            pool_size=7,
            max_overflow=9,
            pool_timeout=4,
            pool_recycle=99,
            isolation_level="SERIALIZABLE",
        )
    )
    assert kwargs["pool_size"] == 7 and kwargs["max_overflow"] == 9 and kwargs["isolation_level"] == "SERIALIZABLE"
    nested = tmp_path / "a/b/db.sqlite"
    _ensure_sqlite_dir(f"sqlite+aiosqlite:///{nested}")
    assert nested.parent.is_dir()
