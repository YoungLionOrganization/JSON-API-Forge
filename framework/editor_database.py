from __future__ import annotations

import base64
from datetime import date, datetime, time
from decimal import Decimal
from typing import Any

from fastapi import HTTPException
from sqlalchemy import Table, select

from .editor_identity import EditorAccess
from .runtime import ProjectRuntime


def _resource_for(runtime: ProjectRuntime, alias: str, table_name: str):
    return next(
        (
            resource
            for resource in runtime.config.resources
            if resource.enabled and resource.database == alias and resource.table == table_name
        ),
        None,
    )


def _column_names(runtime: ProjectRuntime, alias: str, table: Table, *, include_undeclared: bool) -> list[str]:
    resource = _resource_for(runtime, alias, table.name)
    if resource is None:
        return list(table.c.keys()) if include_undeclared else []
    readable = set(resource.readable_fields or table.c.keys())
    readable.difference_update(resource.hidden_fields)
    return [column.name for column in table.c if column.name in readable]


def database_catalog(runtime: ProjectRuntime, access: EditorAccess, *, expose_undeclared: bool) -> dict[str, Any]:
    if runtime.registry is None:
        raise HTTPException(status_code=503, detail="Project databases are not ready")
    aliases: list[dict[str, Any]] = []
    declared = {(resource.database, resource.table) for resource in runtime.config.resources if resource.enabled}
    for alias in sorted(runtime.registry.engines):
        tables: list[dict[str, Any]] = []
        for (table_alias, table_name), table in sorted(runtime.registry.tables.items()):
            if table_alias != alias or not access.permits_database(alias, table_name):
                continue
            is_declared = (alias, table_name) in declared
            if not is_declared and not expose_undeclared:
                continue
            resource = _resource_for(runtime, alias, table_name)
            columns = []
            readable = set(_column_names(runtime, alias, table, include_undeclared=expose_undeclared))
            for column in table.c:
                columns.append(
                    {
                        "name": column.name,
                        "type": str(column.type),
                        "nullable": bool(column.nullable),
                        "primary_key": bool(column.primary_key),
                        "readable": column.name in readable,
                    }
                )
            tables.append(
                {
                    "name": table_name,
                    "declared_resource": bool(resource),
                    "resource_path": resource.path if resource else None,
                    "row_browsing": bool(readable),
                    "columns": columns,
                }
            )
        aliases.append({"alias": alias, "tables": tables})
    return {"project": runtime.config.slug, "databases": aliases, "raw_sql": False}


def _json_value(value: Any, *, depth: int = 0) -> Any:
    if depth > 6:
        return "<nested value truncated>"
    if value is None or isinstance(value, (bool, int, float)):
        return value
    if isinstance(value, str):
        return value if len(value) <= 10_000 else value[:10_000] + "…"
    if isinstance(value, (datetime, date, time)):
        return value.isoformat()
    if isinstance(value, Decimal):
        return str(value)
    if isinstance(value, bytes):
        if len(value) > 4096:
            return {"binary": True, "size": len(value), "preview": None}
        return {"binary": True, "size": len(value), "preview": base64.b64encode(value).decode("ascii")}
    if isinstance(value, dict):
        items = list(value.items())[:200]
        result = {str(key)[:256]: _json_value(item, depth=depth + 1) for key, item in items}
        if len(value) > len(items):
            result["<truncated>"] = len(value) - len(items)
        return result
    if isinstance(value, (list, tuple)):
        items = list(value)[:500]
        result = [_json_value(item, depth=depth + 1) for item in items]
        if len(value) > len(items):
            result.append(f"<{len(value) - len(items)} items truncated>")
        return result
    return str(value)[:10_000]


async def browse_rows(
    runtime: ProjectRuntime,
    access: EditorAccess,
    *,
    alias: str,
    table_name: str,
    limit: int,
    offset: int,
    expose_undeclared: bool,
) -> dict[str, Any]:
    if runtime.registry is None:
        raise HTTPException(status_code=503, detail="Project databases are not ready")
    table = runtime.registry.tables.get((alias, table_name))
    if table is None or not access.permits_database(alias, table_name):
        raise HTTPException(status_code=404, detail="Database table not found")
    resource = _resource_for(runtime, alias, table_name)
    if resource is None and not (expose_undeclared and access.permits("databases.undeclared.read")):
        raise HTTPException(status_code=404, detail="Database table not found")
    names = _column_names(runtime, alias, table, include_undeclared=expose_undeclared)
    if not names:
        raise HTTPException(status_code=403, detail="No columns are readable under the resource policy")
    columns = [table.c[name] for name in names]
    statement = select(*columns)
    order_column = None
    if resource and resource.primary_key in table.c:
        order_column = table.c[resource.primary_key]
    else:
        order_column = next(iter(table.primary_key.columns), None)
    if order_column is not None:
        statement = statement.order_by(order_column)
    statement = statement.offset(offset).limit(limit)
    try:
        async with runtime.registry.engines[alias].connect() as connection:
            result = await connection.execute(statement)
            rows = result.mappings().all()
    except Exception as exc:
        raise HTTPException(status_code=503, detail="Database rows could not be read") from exc
    return {
        "alias": alias,
        "table": table_name,
        "columns": names,
        "offset": offset,
        "limit": limit,
        "rows": [{name: _json_value(row[name]) for name in names} for row in rows],
        "next_offset": offset + len(rows) if len(rows) == limit else None,
        "read_only": True,
    }
