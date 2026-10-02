from __future__ import annotations

import hashlib
import json
import re
from datetime import datetime, timedelta, timezone
from time import monotonic
from typing import Any

from fastapi import HTTPException, Request
from sqlalchemy import (
    Column,
    DateTime,
    Index,
    Integer,
    MetaData,
    String,
    Table,
    Text,
    UniqueConstraint,
    delete,
    insert,
    inspect,
    select,
    text,
    update,
)
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncEngine

from .config import OperationConfig, SQLStatementConfig
from .security import Principal
from .validation import validate_json_schema

_DANGEROUS = re.compile(r"\b(DROP|ALTER|TRUNCATE|CREATE|GRANT|REVOKE|COPY|ATTACH|DETACH|VACUUM|PRAGMA|DO)\b", re.I)
_FIRST_WORD = re.compile(r"^\s*([A-Za-z]+)")

_operation_meta = MetaData()
operation_idempotency_table = Table(
    "_forge_v4_operation_idempotency",
    _operation_meta,
    Column("id", Integer, primary_key=True, autoincrement=True),
    Column("project_slug", String(80), nullable=False, index=True),
    Column("operation_name", String(120), nullable=False, index=True),
    Column("key_hash", String(64), nullable=False),
    Column("request_hash", String(64), nullable=False),
    Column("state", String(16), nullable=False, default="pending", index=True),
    Column("response_json", Text, nullable=True),
    Column("created_at", DateTime(timezone=True), nullable=False),
    Column("updated_at", DateTime(timezone=True), nullable=False),
    UniqueConstraint("project_slug", "operation_name", "key_hash", name="uq_forge_v4_operation_idempotency"),
)
Index(
    "ix_forge_v4_idempotency_retention",
    operation_idempotency_table.c.project_slug,
    operation_idempotency_table.c.operation_name,
    operation_idempotency_table.c.updated_at,
)
_idempotency_cleanup_after: dict[tuple[str, str], float] = {}


def _idempotency_cleanup_due(project_slug: str, operation: OperationConfig) -> bool:
    key = (project_slug, operation.name)
    now = monotonic()
    if now < _idempotency_cleanup_after.get(key, 0.0):
        return False
    _idempotency_cleanup_after[key] = now + min(600.0, max(60.0, operation.idempotency_ttl_seconds / 10.0))
    return True


async def init_operation_idempotency(engine: AsyncEngine, *, mode: str = "create") -> None:
    if mode == "create":
        async with engine.begin() as conn:
            await conn.run_sync(_operation_meta.create_all)
        return
    if mode != "validate":
        raise RuntimeError(f"Unsupported support schema mode: {mode}")
    async with engine.connect() as conn:
        exists = await conn.run_sync(lambda sync_conn: inspect(sync_conn).has_table(operation_idempotency_table.name))
    if not exists:
        raise RuntimeError(f"Missing idempotency support table {operation_idempotency_table.name!r}; run `forge migrate`.")


def _safe_sql(stmt: SQLStatementConfig, allow_ddl: bool) -> None:
    sql = stmt.sql.strip()
    if not sql:
        raise RuntimeError("Empty SQL statement")
    body = sql[:-1] if sql.endswith(";") else sql
    if ";" in body or "--" in body or "/*" in body or "*/" in body:
        raise RuntimeError("SQL operations must contain one parameterized statement and no SQL comments")
    if not allow_ddl and _DANGEROUS.search(body):
        raise RuntimeError("DDL/administrative SQL is disabled for declarative operations")
    match = _FIRST_WORD.search(body)
    verb = match.group(1).upper() if match else ""
    if stmt.mode in {"fetch_one", "fetch_all", "scalar"} and verb not in {"SELECT", "WITH", "SHOW", "EXPLAIN"}:
        raise RuntimeError(f"mode={stmt.mode} only accepts read statements; got {verb or 'unknown'}")
    if stmt.mode == "execute" and verb in {"DROP", "ALTER", "TRUNCATE", "GRANT", "REVOKE"} and not allow_ddl:
        raise RuntimeError(f"Administrative SQL verb {verb} is disabled")


def _dig(value: Any, path: str) -> Any:
    current = value
    if not path:
        return current
    for part in path.split("."):
        if isinstance(current, dict) and part in current:
            current = current[part]
        else:
            raise HTTPException(status_code=422, detail=f"Parameter source not found: {path}")
    return current


def resolve_value(spec: Any, *, body: Any, request: Request, principal: Principal) -> Any:
    if not isinstance(spec, str) or not spec.startswith("$"):
        return spec
    if spec.startswith("$$"):
        return spec[1:]
    if spec == "$principal.subject":
        return principal.subject
    if spec == "$principal.tenant_id":
        return principal.tenant_id
    if spec == "$principal.kind":
        return principal.kind
    if spec == "$request.id":
        return getattr(request.state, "request_id", None)
    source, _, path = spec[1:].partition(".")
    if source == "body":
        return _dig(body, path)
    if source == "param":
        values = getattr(request.state, "validated_parameters", {})
        if path not in values:
            raise HTTPException(status_code=422, detail=f"Validated parameter not found: {path}")
        return values[path]
    if source == "query":
        if path not in request.query_params:
            raise HTTPException(status_code=422, detail=f"Missing query parameter: {path}")
        return request.query_params[path]
    if source == "path":
        if path not in request.path_params:
            raise HTTPException(status_code=422, detail=f"Missing path parameter: {path}")
        return request.path_params[path]
    if source == "header":
        value = request.headers.get(path)
        if value is None:
            raise HTTPException(status_code=422, detail=f"Missing header: {path}")
        return value
    raise HTTPException(status_code=422, detail=f"Unknown parameter source: {spec}")


def _params(stmt: SQLStatementConfig, *, body: Any, request: Request, principal: Principal) -> dict[str, Any]:
    return {name: resolve_value(source, body=body, request=request, principal=principal) for name, source in stmt.params.items()}


def _jsonable(value: Any) -> Any:
    if isinstance(value, dict):
        return {k: _jsonable(v) for k, v in value.items()}
    if isinstance(value, list):
        return [_jsonable(v) for v in value]
    if hasattr(value, "isoformat"):
        return value.isoformat()
    return value


async def _execute_on_connection(conn, operation: OperationConfig, *, body: Any, request: Request, principal: Principal) -> dict[str, Any]:
    output = {}
    unnamed = []
    for statement in operation.statements:
        _safe_sql(statement, operation.allow_ddl)
        result = await conn.execute(text(statement.sql), _params(statement, body=body, request=request, principal=principal))
        if statement.mode == "execute":
            rowcount = max(result.rowcount or 0, 0)
            if statement.require_rowcount_min is not None and rowcount < statement.require_rowcount_min:
                raise HTTPException(
                    status_code=409,
                    detail={
                        "operation": operation.name,
                        "reason": "rowcount_below_minimum",
                        "minimum": statement.require_rowcount_min,
                        "actual": rowcount,
                    },
                )
            if statement.require_rowcount_max is not None and rowcount > statement.require_rowcount_max:
                raise HTTPException(
                    status_code=409,
                    detail={
                        "operation": operation.name,
                        "reason": "rowcount_above_maximum",
                        "maximum": statement.require_rowcount_max,
                        "actual": rowcount,
                    },
                )
            value = {"rowcount": rowcount}
        elif statement.mode == "fetch_one":
            row = result.mappings().first()
            value = _jsonable(dict(row)) if row else None
        elif statement.mode == "fetch_all":
            rows = result.mappings().fetchmany(statement.max_rows + 1)
            if len(rows) > statement.max_rows:
                raise HTTPException(status_code=413, detail=f"Operation result exceeds max_rows={statement.max_rows}")
            value = [_jsonable(dict(row)) for row in rows]
        else:
            value = _jsonable(result.scalar())
        if statement.result_name:
            output[statement.result_name] = value
        else:
            unnamed.append(value)
    if unnamed:
        output["results"] = unnamed
    return output


async def execute_operation(
    engine: AsyncEngine, operation: OperationConfig, *, body: Any, request: Request, principal: Principal
) -> dict[str, Any]:
    validate_json_schema(body, operation.input_schema, label=f"operation:{operation.name}")
    if operation.transaction:
        async with engine.begin() as conn:
            return await _execute_on_connection(conn, operation, body=body, request=request, principal=principal)
    async with engine.connect() as conn:
        return await _execute_on_connection(conn, operation, body=body, request=request, principal=principal)


def idempotency_digest(project_slug: str, operation_name: str, principal: Principal, raw_key: str) -> str:
    raw = f"{project_slug}\x00{operation_name}\x00{principal.kind}\x00{principal.subject}\x00{raw_key}".encode()
    return hashlib.sha256(raw).hexdigest()


def _referenced_headers(operation: OperationConfig) -> set[str]:
    headers = set()
    for statement in operation.statements:
        for source in statement.params.values():
            if isinstance(source, str) and source.startswith("$header."):
                headers.add(source[len("$header.") :].lower())
    return headers


def operation_input_context(*, body: Any, request: Request, principal: Principal, operation: OperationConfig) -> dict[str, Any]:
    return {
        "method": request.method.upper(),
        "body": body,
        "path": dict(sorted(request.path_params.items())),
        "query": sorted(request.query_params.multi_items()),
        "validated_parameters": getattr(request.state, "validated_parameters", {}),
        "tenant_id": principal.tenant_id,
        "headers": {name: request.headers.get(name) for name in sorted(_referenced_headers(operation))},
    }


def request_fingerprint(*, body: Any, request: Request, principal: Principal, operation: OperationConfig) -> str:
    return hashlib.sha256(
        json.dumps(
            operation_input_context(body=body, request=request, principal=principal, operation=operation),
            sort_keys=True,
            separators=(",", ":"),
            ensure_ascii=False,
            default=str,
        ).encode()
    ).hexdigest()


async def execute_idempotent_operation(
    engine: AsyncEngine, *, project_slug: str, operation: OperationConfig, principal: Principal, raw_key: str, body: Any, request: Request
) -> tuple[dict[str, Any], bool]:
    if not operation.transaction:
        raise RuntimeError("idempotent operation requires transaction=true")
    validate_json_schema(body, operation.input_schema, label=f"operation:{operation.name}")
    digest = idempotency_digest(project_slug, operation.name, principal, raw_key)
    fingerprint = request_fingerprint(body=body, request=request, principal=principal, operation=operation)
    now = datetime.now(timezone.utc)
    cutoff = now - timedelta(seconds=operation.idempotency_ttl_seconds)
    try:
        async with engine.begin() as conn:
            if _idempotency_cleanup_due(project_slug, operation):
                expired_ids = list(
                    (
                        await conn.execute(
                            select(operation_idempotency_table.c.id)
                            .where(
                                (operation_idempotency_table.c.project_slug == project_slug)
                                & (operation_idempotency_table.c.operation_name == operation.name)
                                & (operation_idempotency_table.c.updated_at < cutoff)
                            )
                            .order_by(operation_idempotency_table.c.updated_at, operation_idempotency_table.c.id)
                            .limit(operation.idempotency_cleanup_batch_size)
                        )
                    )
                    .scalars()
                    .all()
                )
                if expired_ids:
                    await conn.execute(delete(operation_idempotency_table).where(operation_idempotency_table.c.id.in_(expired_ids)))
                if len(expired_ids) >= operation.idempotency_cleanup_batch_size:
                    _idempotency_cleanup_after[(project_slug, operation.name)] = monotonic() + 1.0
            await conn.execute(
                delete(operation_idempotency_table).where(
                    (operation_idempotency_table.c.project_slug == project_slug)
                    & (operation_idempotency_table.c.operation_name == operation.name)
                    & (operation_idempotency_table.c.key_hash == digest)
                    & (operation_idempotency_table.c.updated_at < cutoff)
                )
            )
            await conn.execute(
                insert(operation_idempotency_table).values(
                    project_slug=project_slug,
                    operation_name=operation.name,
                    key_hash=digest,
                    request_hash=fingerprint,
                    state="pending",
                    response_json=None,
                    created_at=now,
                    updated_at=now,
                )
            )
            result = await _execute_on_connection(conn, operation, body=body, request=request, principal=principal)
            payload = json.dumps(result, separators=(",", ":"), ensure_ascii=False, default=str)
            if len(payload.encode()) > operation.idempotency_max_response_bytes:
                raise HTTPException(
                    status_code=413,
                    detail=f"Idempotent replay response exceeds idempotency_max_response_bytes={operation.idempotency_max_response_bytes}",
                )
            await conn.execute(
                update(operation_idempotency_table)
                .where(
                    (operation_idempotency_table.c.project_slug == project_slug)
                    & (operation_idempotency_table.c.operation_name == operation.name)
                    & (operation_idempotency_table.c.key_hash == digest)
                )
                .values(state="complete", response_json=payload, updated_at=datetime.now(timezone.utc))
            )
        return result, False
    except IntegrityError as exc:
        async with engine.connect() as conn:
            row = (
                (
                    await conn.execute(
                        select(operation_idempotency_table).where(
                            (operation_idempotency_table.c.project_slug == project_slug)
                            & (operation_idempotency_table.c.operation_name == operation.name)
                            & (operation_idempotency_table.c.key_hash == digest)
                        )
                    )
                )
                .mappings()
                .first()
            )
        if not row:
            raise
        if row["request_hash"] != fingerprint:
            raise HTTPException(status_code=409, detail="Idempotency key was already used with a different request payload") from exc
        if row["state"] == "complete" and row["response_json"] is not None:
            return json.loads(row["response_json"]), True
        raise HTTPException(
            status_code=409, detail="An identical idempotent operation is still in progress", headers={"Retry-After": "1"}
        ) from exc


async def claim_idempotency(*args, **kwargs):
    raise RuntimeError("v0.4 removed split-phase idempotency; use execute_idempotent_operation")


async def complete_idempotency(*args, **kwargs):
    raise RuntimeError("v0.4 removed split-phase idempotency; use execute_idempotent_operation")


async def release_idempotency(*args, **kwargs):
    return None
