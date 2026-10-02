from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any

from fastapi import HTTPException

from .config import MongoResourceConfig, ProjectConfig
from .security import has_permission
from .validation import validate_json_schema

log = logging.getLogger("json_api_forge.mongo")


@dataclass
class MongoRegistry:
    clients: dict[str, Any]
    databases: dict[str, Any]

    async def dispose(self) -> None:
        errors = []
        for client in self.clients.values():
            try:
                result = client.close()
                if hasattr(result, "__await__"):
                    await result
            except Exception as exc:
                errors.append(exc)
        if errors:
            raise RuntimeError(f"Failed to close {len(errors)} Mongo client(s)") from errors[0]


async def build_mongo_registry(project: ProjectConfig) -> MongoRegistry | None:
    if not project.mongo_databases:
        return None
    try:
        from pymongo import AsyncMongoClient
    except ImportError as exc:
        raise RuntimeError("MongoDB is configured but pymongo with AsyncMongoClient is not installed") from exc
    clients = {}
    databases = {}
    try:
        for alias, cfg in project.mongo_databases.items():
            client = AsyncMongoClient(
                cfg.uri,
                maxPoolSize=cfg.max_pool_size,
                minPoolSize=cfg.min_pool_size,
                serverSelectionTimeoutMS=cfg.server_selection_timeout_ms,
            )
            clients[alias] = client
            databases[alias] = client[cfg.database]
        for resource in project.mongo_resources:
            if resource.enabled and resource.database not in databases:
                raise RuntimeError(f"Unknown Mongo database alias {resource.database!r} in resource {resource.path!r}")
            protected = {x for x in (resource.tenant_field, resource.owner_field, resource.soft_delete_field) if x}
            if resource.writable_fields is not None and protected & set(resource.writable_fields):
                raise RuntimeError(f"Mongo resource {resource.path!r} exposes policy fields as writable")
            if resource.owner_actions and not resource.owner_field:
                raise RuntimeError(f"Mongo resource {resource.path!r} defines owner_actions without owner_field")
        return MongoRegistry(clients, databases)
    except Exception:
        for client in clients.values():
            try:
                result = client.close()
                if hasattr(result, "__await__"):
                    await result
            except Exception:
                log.exception("Failed to close Mongo client during registry rollback")
        raise


def _visible(doc: dict[str, Any], resource: MongoResourceConfig) -> dict[str, Any]:
    out = dict(doc)
    if "_id" in out:
        out["_id"] = str(out["_id"])
    for field in resource.hidden_fields:
        out.pop(field, None)
    return out


def _protected(resource):
    return {field for field in ("_id", resource.tenant_field, resource.owner_field, resource.soft_delete_field) if field}


def _write_payload(payload, resource, schema, *, replace=False):
    if not isinstance(payload, dict):
        raise HTTPException(status_code=422, detail="Mongo payload must be a JSON object")
    validate_json_schema(payload, schema, label=f"mongo:{resource.path}")
    allowed = set(resource.writable_fields) if resource.writable_fields is not None else set(payload)
    allowed -= _protected(resource)
    unknown = set(payload) - allowed
    if unknown:
        raise HTTPException(status_code=422, detail={"forbidden_or_unknown_fields": sorted(unknown)})
    return {k: v for k, v in payload.items() if k in allowed}


def _base_filter(resource, principal, *, action="list"):
    filt = {}
    if resource.tenant_field:
        if not principal.tenant_id:
            raise HTTPException(status_code=403, detail="Tenant-bound Mongo resource requires tenant_id")
        filt[resource.tenant_field] = principal.tenant_id
    if (
        resource.owner_field
        and action in resource.owner_actions
        and not (resource.owner_bypass_permission and has_permission(principal, resource.owner_bypass_permission))
    ):
        if principal.kind == "anonymous":
            raise HTTPException(status_code=401, detail="Owner-bound Mongo resource requires authentication")
        filt[resource.owner_field] = principal.subject
    if resource.soft_delete_field:
        filt[resource.soft_delete_field] = None
    return filt


def _id_filter(item_id):
    try:
        from bson import ObjectId

        if ObjectId.is_valid(item_id):
            return ObjectId(item_id)
    except ImportError:
        pass
    return item_id


def _query_filter(request, resource, principal):
    filt = _base_filter(resource, principal, action="list")
    op_map = {"ne": "$ne", "gt": "$gt", "gte": "$gte", "lt": "$lt", "lte": "$lte", "in": "$in"}
    reserved = {"limit", "offset", "sort"}
    for key, value in request.query_params.items():
        if key in reserved:
            continue
        field, op = key.rsplit("__", 1) if "__" in key else (key, "eq")
        if field not in resource.allowed_filters or op not in resource.filter_operators:
            continue
        if op == "eq":
            filt[field] = value
        elif op == "in":
            filt[field] = {"$in": [x for x in value.split(",") if x]}
        else:
            existing = filt.get(field)
            existing = existing if isinstance(existing, dict) else {}
            existing[op_map[op]] = value
            filt[field] = existing
    return filt


def _positive_int(request, name, default, *, minimum):
    raw = request.query_params.get(name)
    if raw is None:
        return default
    try:
        value = int(raw)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=f"{name} must be an integer") from exc
    if value < minimum:
        raise HTTPException(status_code=400, detail=f"{name} must be >= {minimum}")
    return value


async def list_documents(request, database, resource, principal):
    limit = min(_positive_int(request, "limit", resource.default_limit, minimum=1), resource.max_limit)
    offset = _positive_int(request, "offset", 0, minimum=0)
    cursor = database[resource.collection].find(_query_filter(request, resource, principal)).skip(offset).limit(limit)
    sort = request.query_params.get("sort")
    if sort:
        desc = sort.startswith("-")
        field = sort[1:] if desc else sort
        if field not in resource.allowed_sort:
            raise HTTPException(status_code=400, detail="Sort field is not allowed")
        cursor = cursor.sort([(field, -1 if desc else 1), ("_id", 1)])
    else:
        cursor = cursor.sort("_id", 1)
    rows = await cursor.to_list(length=limit)
    return {"items": [_visible(row, resource) for row in rows], "limit": limit, "offset": offset}


async def count_documents(request, database, resource, principal):
    return {"count": int(await database[resource.collection].count_documents(_query_filter(request, resource, principal)))}


async def get_document(database, resource, principal, item_id):
    row = await database[resource.collection].find_one({"_id": _id_filter(item_id), **_base_filter(resource, principal, action="read")})
    if row is None:
        raise HTTPException(status_code=404, detail="Not found")
    return _visible(row, resource)


async def create_document(database, resource, principal, payload):
    data = _write_payload(payload, resource, resource.create_schema)
    if resource.tenant_field:
        if not principal.tenant_id:
            raise HTTPException(status_code=403, detail="Tenant-bound Mongo resource requires tenant_id")
        data[resource.tenant_field] = principal.tenant_id
    if resource.owner_field:
        if principal.kind == "anonymous":
            raise HTTPException(status_code=401, detail="Owner-bound Mongo resource requires authentication")
        data[resource.owner_field] = principal.subject
    result = await database[resource.collection].insert_one(data)
    return await get_document(database, resource, principal, str(result.inserted_id))


async def update_document(database, resource, principal, item_id, payload, *, replace=False):
    data = _write_payload(payload, resource, resource.update_schema, replace=replace)
    if not data and not replace:
        raise HTTPException(status_code=422, detail="No writable fields supplied")
    filt = {"_id": _id_filter(item_id), **_base_filter(resource, principal, action="update")}
    collection = database[resource.collection]
    if replace:
        existing = await collection.find_one(filt)
        if existing is None:
            raise HTTPException(status_code=404, detail="Not found")
        replacement = {"_id": existing["_id"], **data}
        for f in (resource.tenant_field, resource.owner_field, resource.soft_delete_field):
            if f and f in existing:
                replacement[f] = existing[f]
        matched = (await collection.replace_one(filt, replacement)).matched_count
    else:
        matched = (await collection.update_one(filt, {"$set": data})).matched_count
    if not matched:
        raise HTTPException(status_code=404, detail="Not found")
    return await get_document(database, resource, principal, item_id)


async def delete_document(database, resource, principal, item_id):
    filt = {"_id": _id_filter(item_id), **_base_filter(resource, principal, action="delete")}
    if resource.soft_delete_field:
        count = (
            await database[resource.collection].update_one(filt, {"$set": {resource.soft_delete_field: datetime.now(timezone.utc)}})
        ).matched_count
    else:
        count = (await database[resource.collection].delete_one(filt)).deleted_count
    if not count:
        raise HTTPException(status_code=404, detail="Not found")
    return {"deleted": True}
