from __future__ import annotations

import logging
from dataclasses import dataclass
from functools import partial
from pathlib import Path
from typing import Any

from sqlalchemy import JSON, BigInteger, Boolean, Column, DateTime, Float, Integer, MetaData, String, Table, Text
from sqlalchemy.ext.asyncio import AsyncEngine, create_async_engine

from .config import ColumnConfig, ProjectConfig, ResourceConfig
from .observability import measure_stage
from .operations import init_operation_idempotency

log = logging.getLogger("json_api_forge.db")


@dataclass
class DatabaseRegistry:
    engines: dict[str, AsyncEngine]
    metadata: dict[str, MetaData]
    tables: dict[tuple[str, str], Table]

    async def dispose(self) -> None:
        errors: list[Exception] = []
        for engine in self.engines.values():
            try:
                await engine.dispose()
            except Exception as exc:
                errors.append(exc)
        if errors:
            raise RuntimeError(f"Failed to dispose {len(errors)} database engine(s)") from errors[0]


def _column_type(spec: ColumnConfig):
    t = spec.type.lower()
    if t == "bigint":
        return BigInteger().with_variant(Integer(), "sqlite")
    if t in {"int", "integer"}:
        return Integer
    if t in {"float", "number", "double"}:
        return Float
    if t in {"bool", "boolean"}:
        return Boolean
    if t in {"text"}:
        return Text
    if t in {"datetime", "timestamp"}:
        return DateTime(timezone=True)
    if t in {"json", "object", "array"}:
        return JSON
    return String(spec.max_length or 255)


def _ensure_sqlite_dir(url: str) -> None:
    prefix = "sqlite+aiosqlite:///"
    if url.startswith(prefix):
        path = url[len(prefix) :]
        if path and path != ":memory:":
            Path(path).parent.mkdir(parents=True, exist_ok=True)


def _engine_kwargs(db) -> dict[str, Any]:
    kwargs: dict[str, Any] = {"echo": db.echo, "pool_pre_ping": db.pool_pre_ping}
    if db.isolation_level:
        kwargs["isolation_level"] = db.isolation_level
    if not db.url.startswith("sqlite"):
        kwargs.update(pool_size=db.pool_size, max_overflow=db.max_overflow, pool_timeout=db.pool_timeout, pool_recycle=db.pool_recycle)
    return kwargs


def build_declared_table(metadata: MetaData, resource: ResourceConfig) -> Table:
    cols: list[Column[Any]] = []
    for name, spec in resource.columns.items():
        kwargs: dict[str, Any] = {"nullable": spec.nullable, "primary_key": spec.primary_key, "unique": spec.unique, "index": spec.index}
        if spec.default is not None:
            kwargs["default"] = spec.default
        if spec.primary_key and spec.type.lower() in {"int", "integer", "bigint"}:
            kwargs["autoincrement"] = True
        cols.append(Column(name, _column_type(spec), **kwargs))
    if not cols:
        raise RuntimeError(f"Resource {resource.path!r} uses auto_create but has no columns")
    return Table(resource.table, metadata, *cols, extend_existing=True)


async def _reflect_table(engine: AsyncEngine, metadata: MetaData, table_name: str) -> Table:
    def sync_reflect(conn):
        metadata.reflect(bind=conn, only=[table_name])

    try:
        async with engine.connect() as conn:
            await conn.run_sync(sync_reflect)
    except Exception as exc:
        raise RuntimeError(f"Could not reflect database table {table_name!r}") from exc
    return metadata.tables[table_name]


def validate_resource_contract(resource: ResourceConfig, table: Table) -> None:
    fields = set(table.c.keys())
    references = {
        "primary_key": [resource.primary_key],
        "allowed_filters": resource.allowed_filters,
        "search_fields": resource.search_fields,
        "allowed_sort": resource.allowed_sort,
        "hidden_fields": resource.hidden_fields,
        "readable_fields": resource.readable_fields or [],
        "writable_fields": resource.writable_fields or [],
    }
    for name, value in (
        ("tenant_field", resource.tenant_field),
        ("owner_field", resource.owner_field),
        ("soft_delete_field", resource.soft_delete_field),
        ("cursor_field", resource.cursor_field),
    ):
        if value:
            references[name] = [value]
    missing = {name: sorted(set(values) - fields) for name, values in references.items() if set(values) - fields}
    if missing:
        raise RuntimeError(f"Resource {resource.path!r} references missing database columns: {missing}")
    protected = {
        value for value in (resource.primary_key, resource.tenant_field, resource.owner_field, resource.soft_delete_field) if value
    }
    if resource.writable_fields is not None:
        unsafe = protected & set(resource.writable_fields)
        if unsafe:
            raise RuntimeError(f"Resource {resource.path!r} exposes protected policy fields as writable: {sorted(unsafe)}")
    if resource.owner_actions and not resource.owner_field:
        raise RuntimeError(f"Resource {resource.path!r} defines owner_actions without owner_field")
    if resource.default_limit > resource.max_limit:
        raise RuntimeError(f"Resource {resource.path!r} default_limit may not exceed max_limit")


async def build_registry(project: ProjectConfig) -> DatabaseRegistry:
    engines = {}
    metadata_map = {}
    tables = {}
    try:
        for alias, db in project.databases.items():
            _ensure_sqlite_dir(db.url)
            engines[alias] = create_async_engine(db.url, **_engine_kwargs(db))
            metadata_map[alias] = MetaData()
        idempotent_aliases = {op.database for op in project.operations if op.idempotency}
        for alias in idempotent_aliases:
            if alias not in engines:
                raise RuntimeError(f"Unknown database alias {alias!r} in idempotent operation")
            await init_operation_idempotency(engines[alias], mode=project.databases[alias].support_schema_mode)
        for resource in project.resources:
            if not resource.enabled:
                continue
            if resource.database not in engines:
                raise RuntimeError(f"Unknown database alias {resource.database!r} in resource {resource.path!r}")
            engine = engines[resource.database]
            metadata = metadata_map[resource.database]
            db_cfg = project.databases[resource.database]
            if resource.auto_create and db_cfg.support_schema_mode == "create":
                table = build_declared_table(metadata, resource)
                with measure_stage(project.slug, "sql_schema_setup"):
                    async with engine.begin() as conn:
                        # Checking all accumulated tables for each resource causes
                        # quadratic startup round trips to a remote database.
                        await conn.run_sync(partial(metadata.create_all, tables=[table]))
            elif (resource.database, resource.table) in tables:
                table = tables[(resource.database, resource.table)]
            else:
                with measure_stage(project.slug, "sql_reflection"):
                    table = await _reflect_table(engine, metadata, resource.table)
            validate_resource_contract(resource, table)
            tables[(resource.database, resource.table)] = table
        return DatabaseRegistry(engines=engines, metadata=metadata_map, tables=tables)
    except Exception:
        for engine in engines.values():
            try:
                await engine.dispose()
            except Exception:
                log.exception("Failed to dispose database engine during registry rollback")
        raise
