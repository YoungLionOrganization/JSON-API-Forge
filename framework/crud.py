from __future__ import annotations

import base64
import json
from datetime import date, datetime, timezone
from itertools import groupby
from typing import Any, Literal

from fastapi import HTTPException, Request
from sqlalchemy import DateTime, and_, delete, func, insert, or_, select, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.sql.schema import Table

from .config import ResourceConfig
from .security import Principal, has_permission
from .validation import validate_json_schema


def _visible(row: dict[str, Any], resource: ResourceConfig) -> dict[str, Any]:
    data = dict(row)
    for field in resource.hidden_fields:
        data.pop(field, None)
    if resource.readable_fields is not None:
        data = {k: v for k, v in data.items() if k in resource.readable_fields}
    return data


def _protected_write_fields(resource: ResourceConfig) -> set[str]:
    return {field for field in (resource.primary_key, resource.tenant_field, resource.owner_field, resource.soft_delete_field) if field}


def _allowed_write_fields(resource: ResourceConfig, table: Table) -> set[str]:
    allowed = set(table.c.keys())
    allowed -= _protected_write_fields(resource)
    if resource.writable_fields is not None:
        allowed &= set(resource.writable_fields)
    return allowed


def _write_payload(
    payload: dict[str, Any],
    resource: ResourceConfig,
    table: Table,
    *,
    schema=None,
    mode: Literal["create", "patch", "replace"] = "patch",
) -> dict[str, Any]:
    if not isinstance(payload, dict):
        raise HTTPException(status_code=422, detail="Resource payload must be a JSON object")
    validate_json_schema(payload, schema, label=f"resource:{resource.path}")
    allowed = _allowed_write_fields(resource, table)
    unknown = set(payload) - allowed
    if unknown:
        raise HTTPException(status_code=422, detail={"forbidden_or_unknown_fields": sorted(unknown)})
    data = {key: value for key, value in payload.items() if key in allowed}

    if mode == "replace":
        for name in sorted(allowed):
            if name in data:
                continue
            column = table.c[name]
            has_default = column.default is not None or column.server_default is not None
            if column.nullable:
                data[name] = None
            elif column.default is not None and column.default.is_scalar:
                data[name] = column.default.arg
            elif column.server_default is not None:
                data[name] = column.server_default.arg
            elif column.default is not None:
                raise HTTPException(status_code=422, detail=f"Replacement must supply field {name!r} with a dynamic default")
            elif not has_default:
                raise HTTPException(status_code=422, detail={"missing_required_fields": [name]})
    for name, value in list(data.items()):
        if isinstance(value, str) and isinstance(table.c[name].type, DateTime):
            data[name] = _coerce_for_column(table.c[name], value)
    return data


def _coerce_for_column(column, value: Any):
    if value is None:
        return None
    try:
        pytype = column.type.python_type
    except (NotImplementedError, AttributeError):
        return value
    if pytype is bool and isinstance(value, str):
        low = value.lower()
        if low in {"1", "true", "yes", "on"}:
            return True
        if low in {"0", "false", "no", "off"}:
            return False
        raise HTTPException(status_code=422, detail=f"Invalid value for field {column.name}")
    try:
        if pytype in {datetime, date}:
            if isinstance(value, pytype):
                return value
            if not isinstance(value, str):
                raise ValueError("Expected ISO date/time")
            parsed = pytype.fromisoformat(value.replace("Z", "+00:00"))
            if pytype is datetime:
                if parsed.tzinfo is None and getattr(column.type, "timezone", False):
                    parsed = parsed.replace(tzinfo=timezone.utc)
                elif parsed.tzinfo is not None and not getattr(column.type, "timezone", False):
                    parsed = parsed.astimezone(timezone.utc).replace(tzinfo=None)
            return parsed
        return pytype(value)
    except (TypeError, ValueError) as exc:
        raise HTTPException(status_code=422, detail=f"Invalid value for field {column.name}") from exc


def _tenant_clause(resource: ResourceConfig, table: Table, principal: Principal):
    if not resource.tenant_field:
        return None
    if resource.tenant_field not in table.c:
        raise RuntimeError(f"tenant_field {resource.tenant_field!r} is missing from table {table.name!r}")
    if not principal.tenant_id:
        raise HTTPException(status_code=403, detail="Tenant-bound resource requires tenant_id")
    return table.c[resource.tenant_field] == principal.tenant_id


def _owner_clause(resource: ResourceConfig, table: Table, principal: Principal, action: str):
    if not resource.owner_field or action not in resource.owner_actions:
        return None
    if resource.owner_field not in table.c:
        raise RuntimeError(f"owner_field {resource.owner_field!r} is missing from table {table.name!r}")
    if resource.owner_bypass_permission and has_permission(principal, resource.owner_bypass_permission):
        return None
    if principal.kind == "anonymous":
        raise HTTPException(status_code=401, detail="Owner-bound resource requires authentication")
    return table.c[resource.owner_field] == principal.subject


def _base_clauses(resource: ResourceConfig, table: Table, principal: Principal, *, action: str = "list") -> list[Any]:
    clauses: list[Any] = []
    tenant_clause = _tenant_clause(resource, table, principal)
    if tenant_clause is not None:
        clauses.append(tenant_clause)
    owner_clause = _owner_clause(resource, table, principal, action)
    if owner_clause is not None:
        clauses.append(owner_clause)
    if resource.soft_delete_field:
        if resource.soft_delete_field not in table.c:
            raise RuntimeError(f"soft_delete_field {resource.soft_delete_field!r} missing from {table.name!r}")
        clauses.append(table.c[resource.soft_delete_field].is_(None))
    return clauses


def _parse_int(value: str | None, default: int, *, minimum: int = 0) -> int:
    if value is None:
        return default
    try:
        parsed = int(value)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail="Pagination values must be integers") from exc
    if parsed < minimum:
        raise HTTPException(status_code=400, detail=f"Pagination value must be >= {minimum}")
    return parsed


def _filter_clause(column, op: str, raw: str):
    if op == "isnull":
        return column.is_(None) if raw.lower() in {"1", "true", "yes"} else column.is_not(None)
    if op == "in":
        values = [_coerce_for_column(column, x) for x in raw.split(",") if x != ""]
        return column.in_(values)
    value = _coerce_for_column(column, raw)
    if op == "eq":
        return column == value
    if op == "ne":
        return column != value
    if op == "gt":
        return column > value
    if op == "gte":
        return column >= value
    if op == "lt":
        return column < value
    if op == "lte":
        return column <= value
    if op == "like":
        return column.like(str(raw))
    if op == "ilike":
        return column.ilike(str(raw))
    raise HTTPException(status_code=400, detail=f"Unsupported filter operator: {op}")


def build_filter_clauses(request: Request, table: Table, resource: ResourceConfig) -> list[Any]:
    clauses: list[Any] = []
    allowed = set(resource.allowed_filters)
    ops = set(resource.filter_operators)
    reserved = {"limit", "offset", "cursor", "sort", "q"}
    for key, raw in request.query_params.items():
        if key in reserved:
            continue
        if "__" in key:
            field, op = key.rsplit("__", 1)
        else:
            field, op = key, "eq"
        if field not in allowed or field not in table.c or op not in ops:
            continue
        clauses.append(_filter_clause(table.c[field], op, raw))
    q = request.query_params.get("q")
    if q and resource.search_fields:
        searchable = [table.c[name].ilike(f"%{q}%") for name in resource.search_fields if name in table.c]
        if searchable:
            clauses.append(or_(*searchable))
    return clauses


def _encode_cursor(cursor_value: Any, pk_value: Any) -> str:
    raw = json.dumps([cursor_value, pk_value], separators=(",", ":"), default=str).encode("utf-8")
    return "v1." + base64.urlsafe_b64encode(raw).decode("ascii").rstrip("=")


def _decode_cursor(raw: str) -> tuple[Any, Any]:
    if not raw.startswith("v1."):
        raise HTTPException(status_code=400, detail="Cursor format is invalid or from an unsupported Forge version")
    encoded = raw[3:]
    encoded += "=" * (-len(encoded) % 4)
    try:
        value = json.loads(base64.urlsafe_b64decode(encoded.encode("ascii")))
    except Exception as exc:
        raise HTTPException(status_code=400, detail="Cursor is invalid") from exc
    if not isinstance(value, list) or len(value) != 2:
        raise HTTPException(status_code=400, detail="Cursor is invalid")
    return value[0], value[1]


async def list_rows(request: Request, engine, table: Table, resource: ResourceConfig, principal: Principal):
    limit = min(_parse_int(request.query_params.get("limit"), resource.default_limit, minimum=1), resource.max_limit)
    clauses = [*_base_clauses(resource, table, principal, action="list"), *build_filter_clauses(request, table, resource)]
    stmt = select(table)
    if clauses:
        stmt = stmt.where(and_(*clauses))

    if resource.pagination_mode == "cursor":
        if "offset" in request.query_params or "sort" in request.query_params:
            raise HTTPException(status_code=400, detail="cursor pagination does not accept offset or sort")
        cursor_name = resource.cursor_field or resource.primary_key
        if cursor_name not in table.c:
            raise RuntimeError(f"cursor_field {cursor_name!r} missing from {table.name!r}")
        if resource.primary_key not in table.c:
            raise RuntimeError(f"primary_key {resource.primary_key!r} missing from {table.name!r}")
        cursor_col = table.c[cursor_name]
        pk_col = table.c[resource.primary_key]
        cursor = request.query_params.get("cursor")
        if cursor is not None:
            cursor_value_raw, pk_value_raw = _decode_cursor(cursor)
            cursor_value = _coerce_for_column(cursor_col, cursor_value_raw)
            pk_value = _coerce_for_column(pk_col, pk_value_raw)
            if pk_value is None:
                raise HTTPException(status_code=400, detail="Cursor primary key may not be null")
            if cursor_name == resource.primary_key:
                stmt = stmt.where(pk_col > pk_value)
            else:
                # NULLs are ordered first consistently across supported SQL engines.
                if cursor_value is None:
                    stmt = stmt.where(or_(cursor_col.is_not(None), and_(cursor_col.is_(None), pk_col > pk_value)))
                else:
                    stmt = stmt.where(or_(cursor_col > cursor_value, and_(cursor_col == cursor_value, pk_col > pk_value)))
        if cursor_name == resource.primary_key:
            stmt = stmt.order_by(pk_col.asc())
        else:
            stmt = stmt.order_by(cursor_col.is_not(None).asc(), cursor_col.asc(), pk_col.asc())
        stmt = stmt.limit(limit + 1)
        async with engine.connect() as conn:
            raw_rows = (await conn.execute(stmt)).mappings().all()
        has_more = len(raw_rows) > limit
        rows = raw_rows[:limit]
        next_cursor = None
        if has_more and rows:
            last = rows[-1]
            next_cursor = _encode_cursor(last[cursor_name], last[resource.primary_key])
        return {"items": [_visible(dict(row), resource) for row in rows], "limit": limit, "next_cursor": next_cursor, "has_more": has_more}

    if "cursor" in request.query_params:
        raise HTTPException(status_code=400, detail="offset pagination does not accept cursor")
    offset = _parse_int(request.query_params.get("offset"), 0, minimum=0)
    sort = request.query_params.get("sort")
    if sort:
        descending = sort.startswith("-")
        name = sort[1:] if descending else sort
        if name not in resource.allowed_sort or name not in table.c:
            raise HTTPException(status_code=400, detail="Sort field is not allowed")
        stmt = stmt.order_by(table.c[name].desc() if descending else table.c[name].asc(), table.c[resource.primary_key].asc())
    else:
        stmt = stmt.order_by(table.c[resource.primary_key].asc())
    stmt = stmt.offset(offset).limit(limit)
    async with engine.connect() as conn:
        rows = (await conn.execute(stmt)).mappings().all()
    return {"items": [_visible(dict(row), resource) for row in rows], "limit": limit, "offset": offset}


async def count_rows(request: Request, engine, table: Table, resource: ResourceConfig, principal: Principal):
    clauses = [*_base_clauses(resource, table, principal, action="list"), *build_filter_clauses(request, table, resource)]
    stmt = select(func.count()).select_from(table)
    if clauses:
        stmt = stmt.where(and_(*clauses))
    async with engine.connect() as conn:
        total = (await conn.execute(stmt)).scalar_one()
    return {"count": int(total)}


async def get_row(engine, table: Table, resource: ResourceConfig, principal: Principal, item_id: Any):
    pk = table.c[resource.primary_key]
    clauses = [pk == _coerce_for_column(pk, item_id), *_base_clauses(resource, table, principal, action="read")]
    async with engine.connect() as conn:
        row = (await conn.execute(select(table).where(and_(*clauses)))).mappings().first()
    if not row:
        raise HTTPException(status_code=404, detail="Not found")
    return _visible(dict(row), resource)


def _inject_policy_fields(data: dict[str, Any], resource: ResourceConfig, principal: Principal) -> None:
    if resource.tenant_field:
        if not principal.tenant_id:
            raise HTTPException(status_code=403, detail="Tenant-bound resource requires tenant_id")
        data[resource.tenant_field] = principal.tenant_id
    if resource.owner_field:
        if principal.kind == "anonymous":
            raise HTTPException(status_code=401, detail="Owner-bound resource requires authentication")
        data[resource.owner_field] = principal.subject


async def create_row(engine, table: Table, resource: ResourceConfig, principal: Principal, payload: dict[str, Any]):
    data = _write_payload(payload, resource, table, schema=resource.create_schema, mode="create")
    _inject_policy_fields(data, resource, principal)
    try:
        async with engine.begin() as conn:
            result = await conn.execute(insert(table).values(**data))
            pk_val = result.inserted_primary_key[0] if result.inserted_primary_key else None
    except IntegrityError as exc:
        raise HTTPException(status_code=409, detail="Resource conflicts with an existing row or database constraint") from exc
    if pk_val is not None:
        return await get_row(engine, table, resource, principal, pk_val)
    return {"created": True}


async def batch_create_rows(engine, table: Table, resource: ResourceConfig, principal: Principal, payloads: list[dict[str, Any]]):
    if not resource.batch_enabled:
        raise HTTPException(status_code=405, detail="Batch API disabled for this resource")
    if not payloads or len(payloads) > resource.max_batch_size:
        raise HTTPException(status_code=422, detail=f"Batch size must be 1..{resource.max_batch_size}")
    values = []
    for payload in payloads:
        data = _write_payload(payload, resource, table, schema=resource.create_schema, mode="create")
        _inject_policy_fields(data, resource, principal)
        values.append(data)
    try:
        async with engine.begin() as conn:
            # SQLAlchemy executemany derives columns from the first dictionary.
            # Preserve each row's fields and defaults in the same atomic transaction.
            for _fields, group in groupby(values, key=lambda data: tuple(sorted(data))):
                await conn.execute(insert(table), list(group))
    except IntegrityError as exc:
        raise HTTPException(status_code=409, detail="Batch conflicts with an existing row or database constraint") from exc
    return {"created": len(values), "rowcount": len(values)}


async def update_row(
    engine,
    table: Table,
    resource: ResourceConfig,
    principal: Principal,
    item_id: Any,
    payload: dict[str, Any],
    *,
    replace: bool = False,
):
    data = _write_payload(payload, resource, table, schema=resource.update_schema, mode="replace" if replace else "patch")
    if not data:
        raise HTTPException(status_code=422, detail="No writable fields supplied")
    pk = table.c[resource.primary_key]
    clauses = [pk == _coerce_for_column(pk, item_id), *_base_clauses(resource, table, principal, action="update")]
    try:
        async with engine.begin() as conn:
            result = await conn.execute(update(table).where(and_(*clauses)).values(**data))
    except IntegrityError as exc:
        raise HTTPException(status_code=409, detail="Update conflicts with an existing row or database constraint") from exc
    if not result.rowcount:
        raise HTTPException(status_code=404, detail="Not found")
    return await get_row(engine, table, resource, principal, item_id)


async def delete_row(engine, table: Table, resource: ResourceConfig, principal: Principal, item_id: Any):
    pk = table.c[resource.primary_key]
    clauses = [pk == _coerce_for_column(pk, item_id), *_base_clauses(resource, table, principal, action="delete")]
    async with engine.begin() as conn:
        if resource.soft_delete_field:
            from datetime import datetime

            result = await conn.execute(
                update(table).where(and_(*clauses)).values({resource.soft_delete_field: datetime.now(timezone.utc)})
            )
        else:
            result = await conn.execute(delete(table).where(and_(*clauses)))
    if not result.rowcount:
        raise HTTPException(status_code=404, detail="Not found")
    return {"deleted": True}
