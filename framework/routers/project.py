from __future__ import annotations

import asyncio
import copy
import hashlib
import json
import logging
from datetime import datetime, timedelta, timezone
from typing import Any

from fastapi import BackgroundTasks, Body, Depends, File, HTTPException, Request, UploadFile, WebSocket, WebSocketDisconnect
from fastapi.openapi.docs import get_redoc_html, get_swagger_ui_html
from fastapi.responses import FileResponse, JSONResponse, StreamingResponse

from ..api_models import APIKeyCreate, JWTCreate
from ..config import DataSourceConfig, OperationConfig, ResourceConfig
from ..crud import batch_create_rows, count_rows, create_row, delete_row, get_row, list_rows, update_row
from ..events import sse_encode
from ..hooks import import_callable
from ..media import delete_media, get_media_meta, make_signed_media_token, media_api_meta, save_media, verify_signed_media_token
from ..mongo import count_documents, create_document, delete_document, get_document, list_documents, update_document
from ..operations import execute_idempotent_operation, execute_operation, operation_input_context
from ..protection import client_ip, host_allowed, ip_allowed, request_is_https
from ..responses import render_response
from ..security import (
    authenticate_request,
    create_api_key,
    ensure_credential_delegation,
    get_api_key_metadata,
    has_permission,
    issue_jwt,
    list_api_keys,
    revoke_api_key,
)
from ..validation import openapi_parameters, validate_json_schema, validate_request_parameters

log = logging.getLogger("json_api_forge.router")


def _resource_namespace(project, resource: ResourceConfig) -> str:
    return f"{project.slug}:{resource.database}:{resource.table}"


def _cache_variant(resource, principal) -> str:
    """Keep shared table invalidation while isolating response/access policies."""
    policy = {
        "resource": resource.model_dump(mode="json"),
        "owner_bypass": bool(resource.owner_bypass_permission and has_permission(principal, resource.owner_bypass_permission)),
    }
    return hashlib.sha256(json.dumps(policy, sort_keys=True, separators=(",", ":")).encode()).hexdigest()


def _operation_namespace(project, operation: OperationConfig) -> str:
    return f"{project.slug}:operation:{operation.name}"


def _data_namespace(project, source: DataSourceConfig) -> str:
    return f"{project.slug}:data:{source.name}"


def register_project_routes(*, app, runtime, principal_for, require, hide_internal_signature, hidden_route, invoke_hook) -> None:
    project = runtime.config
    prefix = project.api_prefix.rstrip("/")
    dependency_specs = {d.name: d for d in project.dependencies}

    def add_route(path: str, endpoint, *, methods: list[str], name: str, **kwargs):
        clean = hide_internal_signature(endpoint)
        method_list = [m.upper() for m in methods]
        for method in method_list:
            route_name = f"{name}_{method.lower()}" if len(method_list) > 1 else name
            app.add_api_route(path, clean, methods=[method], name=route_name, **kwargs)

    def fastapi_dependencies(names: list[str]):
        deps = []
        for name in names:
            spec = dependency_specs.get(name)
            if not spec:
                raise RuntimeError(f"Project {project.slug}: unknown dependency {name!r}")
            deps.append(Depends(import_callable(spec.callable), use_cache=spec.use_cache))
        return deps

    @hidden_route(app.get(f"{prefix}/meta", tags=[f"{project.slug}:system"], name=f"{project.slug}_meta"))
    async def meta(request: Request, _rt=runtime, _project=project):
        principal = await principal_for(request, _rt)
        require(principal, "system.meta.read")
        return {
            "project": _project.slug,
            "name": _project.name,
            "version": _project.version,
            "resources": [{"path": r.path, "actions": r.allowed_actions, "backend": "sql"} for r in _project.resources if r.enabled]
            + [{"path": r.path, "actions": r.allowed_actions, "backend": "mongodb"} for r in _project.mongo_resources if r.enabled],
            "operations": [{"name": o.name, "path": o.path, "method": o.method} for o in _project.operations],
            "data_sources": [{"name": d.name, "path": d.path, "type": d.type} for d in _project.data_sources],
            "event_channels": [e.name for e in _project.event_channels],
            "features": {
                "media": _project.media.enabled,
                "messaging": _project.features.messaging.enabled,
                "social": _project.features.social.enabled,
                "gaming": _project.features.gaming.enabled,
            },
            "principal": {"kind": principal.kind, "subject": principal.subject, "roles": sorted(principal.roles)},
        }

    if project.docs_enabled:

        @hidden_route(app.get(f"{prefix}/_openapi.json", include_in_schema=False, name=f"{project.slug}_project_openapi"))
        async def project_openapi(_prefix=prefix, _project=project):
            schema = copy.deepcopy(app.openapi())
            schema["info"]["title"] = f"{_project.name} API"
            schema["info"]["version"] = _project.version
            schema["paths"] = {
                path: value
                for path, value in schema.get("paths", {}).items()
                if path.startswith(_prefix + "/") and not path.endswith("/_openapi.json")
            }
            schemes = schema.setdefault("components", {}).setdefault("securitySchemes", {})
            schemes["ForgeApiKey"] = {
                "type": "apiKey",
                "in": "header",
                "name": _project.security.api_key_header,
                "description": "Project-scoped Forge API key. Prefer a narrow key per bot/plugin/service.",
            }
            if _project.security.jwt_enabled:
                schemes["ForgeBearer"] = {"type": "http", "scheme": "bearer", "bearerFormat": "JWT"}
            schema["security"] = [{"ForgeApiKey": []}] + ([{"ForgeBearer": []}] if _project.security.jwt_enabled else [])
            if "webhooks" in schema:
                schema["webhooks"] = {name: value for name, value in schema["webhooks"].items() if name.startswith(_project.slug + ".")}
            return schema

        @hidden_route(app.get(f"{prefix}/_docs", include_in_schema=False, name=f"{project.slug}_project_docs"))
        async def project_docs(request: Request, _prefix=prefix, _project=project):
            root = request.scope.get("root_path", "").rstrip("/")
            return get_swagger_ui_html(openapi_url=f"{root}{_prefix}/_openapi.json", title=f"{_project.name} - Swagger UI")

        @hidden_route(app.get(f"{prefix}/_redoc", include_in_schema=False, name=f"{project.slug}_project_redoc"))
        async def project_redoc(request: Request, _prefix=prefix, _project=project):
            root = request.scope.get("root_path", "").rstrip("/")
            return get_redoc_html(openapi_url=f"{root}{_prefix}/_openapi.json", title=f"{_project.name} - ReDoc")

    @hidden_route(app.post(f"{prefix}/admin/api-keys", tags=[f"{project.slug}:admin"], name=f"{project.slug}_create_key"))
    async def admin_create_key(request: Request, body: APIKeyCreate, _rt=runtime, _project=project):
        principal = await principal_for(request, _rt)
        require(principal, "admin.keys.create")
        ensure_credential_delegation(
            _project,
            principal,
            roles=body.roles,
            permissions=body.permissions,
            tenant_id=body.tenant_id,
            expires_at=body.expires_at,
            rate_requests=body.rate_requests,
            rate_window_seconds=body.rate_window_seconds,
            rate_burst=body.rate_burst,
        )
        one_time = principal.kind == "bootstrap" and _project.security.bootstrap_one_time
        created = await create_api_key(
            app.state.internal_engine,
            project_slug=_project.slug,
            name=body.name,
            roles=body.roles,
            permissions=body.permissions,
            expires_at=body.expires_at,
            tenant_id=body.tenant_id,
            rate_requests=body.rate_requests,
            rate_window_seconds=body.rate_window_seconds,
            rate_burst=body.rate_burst,
            consume_bootstrap_once=one_time,
        )
        if one_time:
            created["bootstrap_consumed"] = True
        return created

    @hidden_route(app.get(f"{prefix}/admin/api-keys", tags=[f"{project.slug}:admin"], name=f"{project.slug}_list_keys"))
    async def admin_list_keys(request: Request, _rt=runtime, _project=project):
        principal = await principal_for(request, _rt)
        require(principal, "admin.keys.list")
        return {"items": await list_api_keys(app.state.internal_engine, _project.slug)}

    @hidden_route(app.delete(f"{prefix}/admin/api-keys/{{key_id}}", tags=[f"{project.slug}:admin"], name=f"{project.slug}_revoke_key"))
    async def admin_revoke_key(key_id: int, request: Request, _rt=runtime, _project=project):
        principal = await principal_for(request, _rt)
        require(principal, "admin.keys.revoke")
        target = await get_api_key_metadata(app.state.internal_engine, _project.slug, key_id)
        if target is None:
            return {"revoked": False}
        ensure_credential_delegation(
            _project,
            principal,
            roles=target["roles"],
            permissions=target["permissions"],
            tenant_id=target["tenant_id"],
            expires_at=target["expires_at"],
            rate_requests=target["rate_requests"],
            rate_window_seconds=target["rate_window_seconds"],
            rate_burst=target["rate_burst"],
        )
        return {"revoked": await revoke_api_key(app.state.internal_engine, _project.slug, key_id)}

    if project.security.jwt_enabled and project.security.jwt_provider == "local_hs256":

        @hidden_route(app.post(f"{prefix}/admin/jwt", tags=[f"{project.slug}:admin"], name=f"{project.slug}_issue_jwt"))
        async def admin_issue_jwt(request: Request, body: JWTCreate, _rt=runtime, _project=project):
            principal = await principal_for(request, _rt)
            require(principal, "admin.jwt.issue")
            exp_minutes = body.exp_minutes or _project.security.jwt_exp_minutes
            expires_at = datetime.now(timezone.utc) + timedelta(minutes=exp_minutes)
            ensure_credential_delegation(
                _project,
                principal,
                roles=body.roles,
                permissions=body.permissions,
                tenant_id=body.tenant_id,
                expires_at=expires_at,
                subject=body.subject,
            )
            return {
                "token": issue_jwt(
                    body.subject,
                    _project.slug,
                    body.roles,
                    body.permissions,
                    exp_minutes,
                    body.tenant_id,
                    secret=_project.security.jwt_secret,
                ),
                "token_type": "bearer",
            }

    def register_resource(resource: ResourceConfig):
        base = f"{prefix}/{resource.path.strip('/')}"
        tag = f"{project.slug}:{resource.path.strip('/').replace('/', '-')}"
        table_key = (resource.database, resource.table)
        namespace = _resource_namespace(project, resource)

        def cache_enabled(kind):
            return (
                runtime.cache is not None
                and resource.cache.enabled is not False
                and (project.cache.cache_lists if kind == "list" else project.cache.cache_reads)
            )

        def ttl(kind):
            if kind == "list" and resource.cache.list_ttl_seconds is not None:
                return resource.cache.list_ttl_seconds
            if kind == "read" and resource.cache.read_ttl_seconds is not None:
                return resource.cache.read_ttl_seconds
            return resource.cache.ttl_seconds or project.cache.default_ttl_seconds

        if "list" in resource.allowed_actions:

            async def list_handler(request: Request, _r=resource, _tk=table_key, _rt=runtime, _ns=namespace):
                p = await principal_for(request, _rt)
                require(p, _r.permissions.get("list", f"{_r.path}.list"))
                table, engine = _rt.registry.tables[_tk], _rt.registry.engines[_r.database]
                if cache_enabled("list"):
                    key = await _rt.cache.make_key(
                        _ns,
                        {
                            "kind": "list",
                            "variant": _cache_variant(_r, p),
                            "query": sorted(request.query_params.multi_items()),
                            "tenant": p.tenant_id,
                            "owner": p.subject if _r.owner_field and "list" in _r.owner_actions else None,
                        },
                    )

                    async def loader():
                        return await list_rows(request, engine, table, _r, p)

                    value, hit = await _rt.cache.get_or_set_json(
                        key,
                        ttl("list"),
                        loader,
                        _r.cache.stale_ttl_seconds if _r.cache.stale_ttl_seconds is not None else project.cache.stale_ttl_seconds,
                    )
                    request.state.forge_cache = "hit" if hit else "miss"
                    return value
                return await list_rows(request, engine, table, _r, p)

            add_route(
                base,
                list_handler,
                methods=["GET"],
                tags=[tag],
                name=f"{project.slug}_list_{resource.path}",
                dependencies=fastapi_dependencies(resource.dependencies),
            )
        if resource.count_enabled and "list" in resource.allowed_actions:

            async def count_handler(request: Request, _r=resource, _tk=table_key, _rt=runtime):
                p = await principal_for(request, _rt)
                require(p, _r.permissions.get("list", f"{_r.path}.list"))
                return await count_rows(request, _rt.registry.engines[_r.database], _rt.registry.tables[_tk], _r, p)

            add_route(
                base + "/_count",
                count_handler,
                methods=["GET"],
                tags=[tag],
                name=f"{project.slug}_count_{resource.path}",
                dependencies=fastapi_dependencies(resource.dependencies),
            )
        if "read" in resource.allowed_actions:

            async def read_handler(item_id: str, request: Request, _r=resource, _tk=table_key, _rt=runtime, _ns=namespace):
                p = await principal_for(request, _rt)
                require(p, _r.permissions.get("read", f"{_r.path}.read"))
                table, engine = _rt.registry.tables[_tk], _rt.registry.engines[_r.database]
                if cache_enabled("read"):
                    key = await _rt.cache.make_key(
                        _ns,
                        {
                            "kind": "read",
                            "variant": _cache_variant(_r, p),
                            "id": item_id,
                            "tenant": p.tenant_id,
                            "owner": p.subject if _r.owner_field and "read" in _r.owner_actions else None,
                        },
                    )

                    async def loader():
                        return await get_row(engine, table, _r, p, item_id)

                    value, hit = await _rt.cache.get_or_set_json(
                        key,
                        ttl("read"),
                        loader,
                        _r.cache.stale_ttl_seconds if _r.cache.stale_ttl_seconds is not None else project.cache.stale_ttl_seconds,
                    )
                    request.state.forge_cache = "hit" if hit else "miss"
                    return value
                return await get_row(engine, table, _r, p, item_id)

            add_route(
                base + "/{item_id}",
                read_handler,
                methods=["GET"],
                tags=[tag],
                name=f"{project.slug}_read_{resource.path}",
                dependencies=fastapi_dependencies(resource.dependencies),
            )
        if "create" in resource.allowed_actions:

            async def create_handler(
                request: Request, payload: dict[str, Any] = Body(...), _r=resource, _tk=table_key, _rt=runtime, _ns=namespace
            ):
                p = await principal_for(request, _rt)
                require(p, _r.permissions.get("create", f"{_r.path}.create"))
                value = await create_row(_rt.registry.engines[_r.database], _rt.registry.tables[_tk], _r, p, payload)
                await _rt.cache.invalidate_namespace(_ns) if _rt.cache else asyncio.sleep(0)
                return value

            add_route(
                base,
                create_handler,
                methods=["POST"],
                tags=[tag],
                name=f"{project.slug}_create_{resource.path}",
                dependencies=fastapi_dependencies(resource.dependencies),
            )
            if resource.batch_enabled:

                async def batch_create_handler(
                    request: Request, payload: list[dict[str, Any]] = Body(...), _r=resource, _tk=table_key, _rt=runtime, _ns=namespace
                ):
                    p = await principal_for(request, _rt)
                    require(p, _r.permissions.get("create", f"{_r.path}.create"))
                    value = await batch_create_rows(_rt.registry.engines[_r.database], _rt.registry.tables[_tk], _r, p, payload)
                    await _rt.cache.invalidate_namespace(_ns) if _rt.cache else asyncio.sleep(0)
                    return value

                add_route(
                    base + "/_batch",
                    batch_create_handler,
                    methods=["POST"],
                    tags=[tag],
                    name=f"{project.slug}_batch_create_{resource.path}",
                    dependencies=fastapi_dependencies(resource.dependencies),
                )
        if "update" in resource.allowed_actions:

            async def patch_handler(
                item_id: str, request: Request, payload: dict[str, Any] = Body(...), _r=resource, _tk=table_key, _rt=runtime, _ns=namespace
            ):
                p = await principal_for(request, _rt)
                require(p, _r.permissions.get("update", f"{_r.path}.update"))
                value = await update_row(
                    _rt.registry.engines[_r.database], _rt.registry.tables[_tk], _r, p, item_id, payload, replace=False
                )
                await _rt.cache.invalidate_namespace(_ns) if _rt.cache else asyncio.sleep(0)
                return value

            async def put_handler(
                item_id: str, request: Request, payload: dict[str, Any] = Body(...), _r=resource, _tk=table_key, _rt=runtime, _ns=namespace
            ):
                p = await principal_for(request, _rt)
                require(p, _r.permissions.get("update", f"{_r.path}.update"))
                value = await update_row(_rt.registry.engines[_r.database], _rt.registry.tables[_tk], _r, p, item_id, payload, replace=True)
                await _rt.cache.invalidate_namespace(_ns) if _rt.cache else asyncio.sleep(0)
                return value

            add_route(
                base + "/{item_id}",
                patch_handler,
                methods=["PATCH"],
                tags=[tag],
                name=f"{project.slug}_patch_{resource.path}",
                dependencies=fastapi_dependencies(resource.dependencies),
            )
            add_route(
                base + "/{item_id}",
                put_handler,
                methods=["PUT"],
                tags=[tag],
                name=f"{project.slug}_put_{resource.path}",
                dependencies=fastapi_dependencies(resource.dependencies),
            )
        if "delete" in resource.allowed_actions:

            async def delete_handler(item_id: str, request: Request, _r=resource, _tk=table_key, _rt=runtime, _ns=namespace):
                p = await principal_for(request, _rt)
                require(p, _r.permissions.get("delete", f"{_r.path}.delete"))
                value = await delete_row(_rt.registry.engines[_r.database], _rt.registry.tables[_tk], _r, p, item_id)
                await _rt.cache.invalidate_namespace(_ns) if _rt.cache else asyncio.sleep(0)
                return value

            add_route(
                base + "/{item_id}",
                delete_handler,
                methods=["DELETE"],
                tags=[tag],
                name=f"{project.slug}_delete_{resource.path}",
                dependencies=fastapi_dependencies(resource.dependencies),
            )

    for resource in project.resources:
        if resource.enabled:
            register_resource(resource)

    def register_mongo_resource(resource):
        base = f"{prefix}/{resource.path.strip('/')}"
        tag = f"{project.slug}:mongo:{resource.path.strip('/').replace('/', '-')}"
        namespace = f"{project.slug}:mongo:{resource.database}:{resource.collection}"

        def cache_enabled(kind):
            return (
                runtime.cache is not None
                and resource.cache.enabled is not False
                and (project.cache.cache_lists if kind == "list" else project.cache.cache_reads)
            )

        def ttl(kind):
            if kind == "list" and resource.cache.list_ttl_seconds is not None:
                return resource.cache.list_ttl_seconds
            if kind == "read" and resource.cache.read_ttl_seconds is not None:
                return resource.cache.read_ttl_seconds
            return resource.cache.ttl_seconds or project.cache.default_ttl_seconds

        if "list" in resource.allowed_actions:

            async def mongo_list(request: Request, _r=resource, _rt=runtime, _ns=namespace):
                p = await principal_for(request, _rt)
                require(p, _r.permissions.get("list", f"{_r.path}.list"))
                if _rt.mongo_registry is None or _r.database not in _rt.mongo_registry.databases:
                    raise HTTPException(status_code=503, detail="MongoDB runtime is unavailable")
                db = _rt.mongo_registry.databases[_r.database]
                if cache_enabled("list"):
                    key = await _rt.cache.make_key(
                        _ns,
                        {
                            "kind": "list",
                            "variant": _cache_variant(_r, p),
                            "query": sorted(request.query_params.multi_items()),
                            "tenant": p.tenant_id,
                            "owner": p.subject if _r.owner_field and "list" in _r.owner_actions else None,
                        },
                    )

                    async def loader():
                        return await list_documents(request, db, _r, p)

                    value, hit = await _rt.cache.get_or_set_json(
                        key,
                        ttl("list"),
                        loader,
                        _r.cache.stale_ttl_seconds if _r.cache.stale_ttl_seconds is not None else project.cache.stale_ttl_seconds,
                    )
                    request.state.forge_cache = "hit" if hit else "miss"
                    return value
                return await list_documents(request, db, _r, p)

            add_route(
                base,
                mongo_list,
                methods=["GET"],
                tags=[tag],
                name=f"{project.slug}_mongo_list_{resource.path}",
                dependencies=fastapi_dependencies(resource.dependencies),
            )

            async def mongo_count(request: Request, _r=resource, _rt=runtime):
                p = await principal_for(request, _rt)
                require(p, _r.permissions.get("list", f"{_r.path}.list"))
                return (
                    await count_documents(request, _rt.mongo_registry.databases[_r.database], _r, p)
                    if _rt.mongo_registry and _r.database in _rt.mongo_registry.databases
                    else (_ for _ in ()).throw(HTTPException(status_code=503, detail="MongoDB runtime is unavailable"))
                )

            add_route(
                base + "/_count",
                mongo_count,
                methods=["GET"],
                tags=[tag],
                name=f"{project.slug}_mongo_count_{resource.path}",
                dependencies=fastapi_dependencies(resource.dependencies),
            )
        if "read" in resource.allowed_actions:

            async def mongo_read(item_id: str, request: Request, _r=resource, _rt=runtime, _ns=namespace):
                p = await principal_for(request, _rt)
                require(p, _r.permissions.get("read", f"{_r.path}.read"))
                if _rt.mongo_registry is None or _r.database not in _rt.mongo_registry.databases:
                    raise HTTPException(status_code=503, detail="MongoDB runtime is unavailable")
                db = _rt.mongo_registry.databases[_r.database]
                if cache_enabled("read"):
                    key = await _rt.cache.make_key(
                        _ns,
                        {
                            "kind": "read",
                            "variant": _cache_variant(_r, p),
                            "id": item_id,
                            "tenant": p.tenant_id,
                            "owner": p.subject if _r.owner_field and "read" in _r.owner_actions else None,
                        },
                    )

                    async def loader():
                        return await get_document(db, _r, p, item_id)

                    value, hit = await _rt.cache.get_or_set_json(
                        key,
                        ttl("read"),
                        loader,
                        _r.cache.stale_ttl_seconds if _r.cache.stale_ttl_seconds is not None else project.cache.stale_ttl_seconds,
                    )
                    request.state.forge_cache = "hit" if hit else "miss"
                    return value
                return await get_document(db, _r, p, item_id)

            add_route(
                base + "/{item_id}",
                mongo_read,
                methods=["GET"],
                tags=[tag],
                name=f"{project.slug}_mongo_read_{resource.path}",
                dependencies=fastapi_dependencies(resource.dependencies),
            )
        if "create" in resource.allowed_actions:

            async def mongo_create(request: Request, payload: dict[str, Any] = Body(...), _r=resource, _rt=runtime, _ns=namespace):
                p = await principal_for(request, _rt)
                require(p, _r.permissions.get("create", f"{_r.path}.create"))
                if _rt.mongo_registry is None or _r.database not in _rt.mongo_registry.databases:
                    raise HTTPException(status_code=503, detail="MongoDB runtime is unavailable")
                value = await create_document(_rt.mongo_registry.databases[_r.database], _r, p, payload)
                if _rt.cache:
                    await _rt.cache.invalidate_namespace(_ns)
                return value

            add_route(
                base,
                mongo_create,
                methods=["POST"],
                tags=[tag],
                name=f"{project.slug}_mongo_create_{resource.path}",
                dependencies=fastapi_dependencies(resource.dependencies),
            )
        if "update" in resource.allowed_actions:

            async def mongo_patch(
                item_id: str, request: Request, payload: dict[str, Any] = Body(...), _r=resource, _rt=runtime, _ns=namespace
            ):
                p = await principal_for(request, _rt)
                require(p, _r.permissions.get("update", f"{_r.path}.update"))
                if _rt.mongo_registry is None or _r.database not in _rt.mongo_registry.databases:
                    raise HTTPException(status_code=503, detail="MongoDB runtime is unavailable")
                value = await update_document(_rt.mongo_registry.databases[_r.database], _r, p, item_id, payload, replace=False)
                if _rt.cache:
                    await _rt.cache.invalidate_namespace(_ns)
                return value

            async def mongo_put(
                item_id: str, request: Request, payload: dict[str, Any] = Body(...), _r=resource, _rt=runtime, _ns=namespace
            ):
                p = await principal_for(request, _rt)
                require(p, _r.permissions.get("update", f"{_r.path}.update"))
                if _rt.mongo_registry is None or _r.database not in _rt.mongo_registry.databases:
                    raise HTTPException(status_code=503, detail="MongoDB runtime is unavailable")
                value = await update_document(_rt.mongo_registry.databases[_r.database], _r, p, item_id, payload, replace=True)
                if _rt.cache:
                    await _rt.cache.invalidate_namespace(_ns)
                return value

            add_route(
                base + "/{item_id}",
                mongo_patch,
                methods=["PATCH"],
                tags=[tag],
                name=f"{project.slug}_mongo_patch_{resource.path}",
                dependencies=fastapi_dependencies(resource.dependencies),
            )
            add_route(
                base + "/{item_id}",
                mongo_put,
                methods=["PUT"],
                tags=[tag],
                name=f"{project.slug}_mongo_put_{resource.path}",
                dependencies=fastapi_dependencies(resource.dependencies),
            )
        if "delete" in resource.allowed_actions:

            async def mongo_delete(item_id: str, request: Request, _r=resource, _rt=runtime, _ns=namespace):
                p = await principal_for(request, _rt)
                require(p, _r.permissions.get("delete", f"{_r.path}.delete"))
                if _rt.mongo_registry is None or _r.database not in _rt.mongo_registry.databases:
                    raise HTTPException(status_code=503, detail="MongoDB runtime is unavailable")
                value = await delete_document(_rt.mongo_registry.databases[_r.database], _r, p, item_id)
                if _rt.cache:
                    await _rt.cache.invalidate_namespace(_ns)
                return value

            add_route(
                base + "/{item_id}",
                mongo_delete,
                methods=["DELETE"],
                tags=[tag],
                name=f"{project.slug}_mongo_delete_{resource.path}",
                dependencies=fastapi_dependencies(resource.dependencies),
            )

    for resource in project.mongo_resources:
        if resource.enabled:
            register_mongo_resource(resource)

    for index, operation in enumerate(project.operations):
        namespace = _operation_namespace(project, operation)
        openapi_extra = {}
        if operation.input_schema:
            openapi_extra["requestBody"] = {"content": {"application/json": {"schema": operation.input_schema}}}
        if operation.parameters:
            openapi_extra["parameters"] = openapi_parameters(operation.parameters)
        if operation.public:
            openapi_extra["security"] = []

        async def operation_handler(
            request: Request,
            background_tasks: BackgroundTasks,
            payload: Any = Body(default=None),
            _op=operation,
            _rt=runtime,
            _ns=namespace,
        ):
            p = await principal_for(request, _rt)
            require(p, _op.permission)
            validate_request_parameters(request, _op.parameters)
            if _op.database not in _rt.registry.engines:
                raise HTTPException(status_code=500, detail=f"Unknown operation database: {_op.database}")
            idem_key = request.headers.get(project.security.idempotency_header) if _op.idempotency else None
            if _op.idempotency and not idem_key:
                raise HTTPException(status_code=400, detail=f"Missing {project.security.idempotency_header} header")
            if idem_key is not None and (not idem_key.strip() or len(idem_key) > 200):
                raise HTTPException(status_code=400, detail="Idempotency-Key must be 1..200 characters")
            replayed = False
            if _op.idempotency:
                result, replayed = await execute_idempotent_operation(
                    _rt.registry.engines[_op.database],
                    project_slug=project.slug,
                    operation=_op,
                    principal=p,
                    raw_key=idem_key or "",
                    body=payload,
                    request=request,
                )
            else:

                async def loader():
                    return await execute_operation(_rt.registry.engines[_op.database], _op, body=payload, request=request, principal=p)

                if _op.cache.enabled and _rt.cache:
                    context = operation_input_context(body=payload, request=request, principal=p, operation=_op)
                    if _op.cache.vary_by_principal:
                        context["principal"] = p.subject
                    key = await _rt.cache.make_key(_ns, context)
                    result, hit = await _rt.cache.get_or_set_json(
                        key,
                        _op.cache.ttl_seconds,
                        loader,
                        _op.cache.stale_ttl_seconds if _op.cache.stale_ttl_seconds is not None else project.cache.stale_ttl_seconds,
                    )
                    request.state.forge_cache = "hit" if hit else "miss"
                else:
                    result = await loader()
            if not replayed:
                for path in _op.invalidate_resources:
                    target = next((r for r in project.resources if r.path == path), None)
                    if target and _rt.cache:
                        await _rt.cache.invalidate_namespace(_resource_namespace(project, target))
                for operation_name in _op.invalidate_operations:
                    target_op = next((o for o in project.operations if o.name == operation_name), None)
                    if target_op and _rt.cache:
                        await _rt.cache.invalidate_namespace(_operation_namespace(project, target_op))
                for hook_path in _op.background_hooks:
                    background_tasks.add_task(
                        invoke_hook,
                        import_callable(hook_path),
                        request=request,
                        payload=payload,
                        principal=p,
                        result=result,
                        app=app,
                        project=project,
                    )
            else:
                request.state.forge_idempotent_replay = True
                if isinstance(result, dict):
                    result = {**result, "_idempotent_replay": True}
            return result

        add_route(
            f"{prefix}/{operation.path.strip('/')}",
            operation_handler,
            methods=[operation.method],
            tags=operation.tags or [f"{project.slug}:rpc"],
            name=f"{project.slug}_operation_{index}_{operation.name}",
            summary=operation.summary,
            description=operation.description,
            deprecated=operation.deprecated,
            openapi_extra=openapi_extra or None,
            dependencies=fastapi_dependencies(operation.dependencies),
        )

    for index, source in enumerate(project.data_sources):
        if not source.enabled:
            continue
        base = f"{prefix}/{source.path.strip('/')}"
        namespace = _data_namespace(project, source)
        method = source.method if source.type == "http" else "GET"

        async def data_read(request: Request, payload: Any = Body(default=None), _s=source, _rt=runtime, _ns=namespace, _method=method):
            p = await principal_for(request, _rt)
            require(p, _s.read_permission or _s.permission)
            validate_request_parameters(request, _s.parameters)

            async def loader():
                return await _rt.data_sources.read(_s, request, payload)

            if _s.cache_ttl_seconds > 0 and _rt.cache and _method == "GET":
                key = await _rt.cache.make_key(
                    _ns, {"query": sorted(request.query_params.multi_items()), "body": payload, "principal": p.subject}
                )
                result, hit = await _rt.cache.get_or_set_json(
                    key,
                    _s.cache_ttl_seconds,
                    loader,
                    _s.stale_ttl_seconds if _s.stale_ttl_seconds is not None else project.cache.stale_ttl_seconds,
                )
                request.state.forge_cache = "hit" if hit else "miss"
                return result
            return await loader()

        data_openapi = {"parameters": openapi_parameters(source.parameters)} if source.parameters else {}
        if source.public:
            data_openapi["security"] = []
        add_route(
            base,
            data_read,
            methods=[method],
            tags=[f"{project.slug}:data"],
            name=f"{project.slug}_data_{index}_{source.name}",
            dependencies=fastapi_dependencies(source.dependencies),
            openapi_extra=data_openapi or None,
        )
        if source.writable:

            async def data_create(request: Request, payload: dict[str, Any] = Body(...), _s=source, _rt=runtime, _ns=namespace):
                p = await principal_for(request, _rt)
                require(p, _s.write_permission or _s.permission) if not _s.public_write else None
                value = await _rt.data_sources.create(_s, payload)
                await _rt.cache.invalidate_namespace(_ns) if _rt.cache else asyncio.sleep(0)
                return value

            async def data_patch(
                item_id: str, request: Request, payload: dict[str, Any] = Body(...), _s=source, _rt=runtime, _ns=namespace
            ):
                p = await principal_for(request, _rt)
                require(p, _s.write_permission or _s.permission) if not _s.public_write else None
                value = await _rt.data_sources.update(_s, item_id, payload, replace=False)
                await _rt.cache.invalidate_namespace(_ns) if _rt.cache else asyncio.sleep(0)
                return value

            async def data_put(item_id: str, request: Request, payload: dict[str, Any] = Body(...), _s=source, _rt=runtime, _ns=namespace):
                p = await principal_for(request, _rt)
                require(p, _s.write_permission or _s.permission) if not _s.public_write else None
                value = await _rt.data_sources.update(_s, item_id, payload, replace=True)
                await _rt.cache.invalidate_namespace(_ns) if _rt.cache else asyncio.sleep(0)
                return value

            async def data_delete(item_id: str, request: Request, _s=source, _rt=runtime, _ns=namespace):
                p = await principal_for(request, _rt)
                require(p, _s.write_permission or _s.permission) if not _s.public_write else None
                value = await _rt.data_sources.delete(_s, item_id)
                await _rt.cache.invalidate_namespace(_ns) if _rt.cache else asyncio.sleep(0)
                return value

            write_openapi = {"security": []} if source.public_write else None
            add_route(
                base,
                data_create,
                methods=["POST"],
                tags=[f"{project.slug}:data"],
                name=f"{project.slug}_data_create_{index}",
                dependencies=fastapi_dependencies(source.dependencies),
                openapi_extra=write_openapi,
            )
            add_route(
                base + "/{item_id}",
                data_patch,
                methods=["PATCH"],
                tags=[f"{project.slug}:data"],
                name=f"{project.slug}_data_patch_{index}",
                dependencies=fastapi_dependencies(source.dependencies),
                openapi_extra=write_openapi,
            )
            add_route(
                base + "/{item_id}",
                data_put,
                methods=["PUT"],
                tags=[f"{project.slug}:data"],
                name=f"{project.slug}_data_put_{index}",
                dependencies=fastapi_dependencies(source.dependencies),
                openapi_extra=write_openapi,
            )
            add_route(
                base + "/{item_id}",
                data_delete,
                methods=["DELETE"],
                tags=[f"{project.slug}:data"],
                name=f"{project.slug}_data_delete_{index}",
                dependencies=fastapi_dependencies(source.dependencies),
                openapi_extra=write_openapi,
            )

    for index, endpoint in enumerate(project.custom_endpoints):
        handler = import_callable(endpoint.handler)
        openapi_extra = dict(endpoint.openapi_extra or {})
        if endpoint.parameters:
            openapi_extra["parameters"] = openapi_parameters(endpoint.parameters)
        if endpoint.public:
            openapi_extra["security"] = []
        if endpoint.input_mode != "none":
            media_map = {
                "json": "application/json",
                "form": "application/x-www-form-urlencoded",
                "text": "text/plain",
                "bytes": "application/octet-stream",
            }
            body_schema = endpoint.input_schema or (
                {"type": "string", "format": "binary"}
                if endpoint.input_mode == "bytes"
                else {"type": "string"}
                if endpoint.input_mode == "text"
                else {"type": "object"}
            )
            openapi_extra.setdefault("requestBody", {"content": {media_map[endpoint.input_mode]: {"schema": body_schema}}})

        async def custom_handler(request: Request, background_tasks: BackgroundTasks, _e=endpoint, _h=handler, _rt=runtime):
            p = await principal_for(request, _rt)
            require(p, _e.permission)
            validate_request_parameters(request, _e.parameters)
            if _e.input_mode == "none":
                payload = None
            elif _e.input_mode == "json":
                raw = await request.body()
                if not raw:
                    payload = None
                else:
                    try:
                        import orjson

                        payload = orjson.loads(raw)
                    except Exception as exc:
                        raise HTTPException(status_code=400, detail="Invalid JSON request body") from exc
            elif _e.input_mode == "form":
                form = await request.form()
                payload = dict(form)
            elif _e.input_mode == "text":
                payload = (await request.body()).decode("utf-8")
            else:
                payload = await request.body()
            if _e.input_mode in {"json", "form"}:
                validate_json_schema(payload, _e.input_schema, label=f"endpoint:{_e.path}")
            kwargs = {"request": request, "payload": payload, "principal": p, "app": app, "project": project}
            result = await invoke_hook(_h, **kwargs)
            for hook_path in _e.background_hooks:
                background_tasks.add_task(invoke_hook, import_callable(hook_path), **kwargs, result=result)
            return render_response(_e.response, result)

        add_route(
            f"{prefix}/{endpoint.path.strip('/')}",
            custom_handler,
            methods=[endpoint.method.upper()],
            summary=endpoint.summary,
            description=endpoint.description,
            tags=endpoint.tags or [f"{project.slug}:custom"],
            name=f"{project.slug}_custom_{index}",
            deprecated=endpoint.deprecated,
            include_in_schema=endpoint.include_in_schema,
            dependencies=fastapi_dependencies(endpoint.dependencies),
            openapi_extra=openapi_extra or None,
        )

    for index, channel in enumerate(project.event_channels):
        base = f"{prefix}/{channel.path.strip('/')}"

        async def publish_event(request: Request, payload: Any = Body(...), _c=channel, _rt=runtime):
            p = await principal_for(request, _rt)
            require(p, _c.publish_permission)
            import orjson

            if len(orjson.dumps(payload)) > _c.max_message_bytes:
                raise HTTPException(status_code=413, detail=f"Event exceeds max_message_bytes={_c.max_message_bytes}")
            fanout_hint = await _rt.event_hub.publish(
                _c.name, {"channel": _c.name, "event": payload, "publisher": p.subject, "request_id": request.state.request_id}
            )
            return {"published": True, "fanout_hint": fanout_hint, "delivery": "best-effort"}

        add_route(
            base,
            publish_event,
            methods=["POST"],
            tags=[f"{project.slug}:events"],
            name=f"{project.slug}_event_publish_{index}",
            openapi_extra={"security": []} if channel.public_publish else None,
        )
        if channel.sse_enabled:

            async def event_stream(request: Request, _c=channel, _rt=runtime):
                p = await principal_for(request, _rt)
                require(p, _c.subscribe_permission)

                async def stream():
                    subscription = _rt.event_hub.subscribe(_c.name, _c.queue_size, max_subscribers=_c.max_sse_connections)
                    try:
                        while True:
                            try:
                                event = await asyncio.wait_for(anext(subscription), timeout=_c.heartbeat_seconds)
                                yield sse_encode(event)
                            except asyncio.TimeoutError:
                                yield b": heartbeat\n\n"
                            if await request.is_disconnected():
                                break
                    finally:
                        await subscription.aclose()

                return StreamingResponse(
                    stream(), media_type="text/event-stream", headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"}
                )

            add_route(
                base + "/stream",
                event_stream,
                methods=["GET"],
                tags=[f"{project.slug}:events"],
                name=f"{project.slug}_event_sse_{index}",
                openapi_extra={"security": []} if channel.public_subscribe else None,
            )
        if channel.websocket_enabled:

            async def websocket_endpoint(websocket: WebSocket, _c=channel, _rt=runtime, _project=project):
                try:
                    trusted = _project.protection.trusted_proxy_cidrs
                    if not host_allowed(websocket.headers.get("host"), _project.protection.trusted_hosts):
                        raise HTTPException(status_code=400, detail="Host is not allowed for this project")
                    if _project.security.require_https and not request_is_https(websocket, trusted):
                        raise HTTPException(status_code=400, detail="HTTPS is required")
                    if not ip_allowed(websocket, _project.security.allowed_ips, _project.security.denied_ips, trusted):
                        raise HTTPException(status_code=403, detail="Client IP is not allowed")
                    origin = websocket.headers.get("origin")
                    if _c.allowed_origins and "*" not in _c.allowed_origins and origin not in _c.allowed_origins:
                        raise HTTPException(status_code=403, detail="WebSocket origin is not allowed")
                    effective_ip = client_ip(websocket, trusted)
                    if _project.rate_limit.enabled and _project.rate_limit.pre_auth_enabled:
                        await _rt.limiter.check(
                            f"{_project.slug}:preauth:ip:{effective_ip}",
                            _project.rate_limit.pre_auth_requests,
                            _project.rate_limit.pre_auth_window_seconds,
                            _project.rate_limit.pre_auth_burst,
                        )
                    p = await authenticate_request(websocket, _project, app.state.internal_engine)
                    require(p, _c.subscribe_permission)
                    ws_subject = p.subject if p.kind != "anonymous" else f"ip:{effective_ip}"
                    if _project.rate_limit.enabled:
                        await _rt.limiter.check(
                            f"{_project.slug}:{p.kind}:{ws_subject}:global",
                            p.rate_requests or _project.rate_limit.requests,
                            p.rate_window_seconds or _project.rate_limit.window_seconds,
                            p.rate_burst or _project.rate_limit.burst,
                        )
                    await _rt.event_hub.connect_ws(
                        _c.name, websocket, queue_size=_c.queue_size, max_connections=_c.max_websocket_connections
                    )
                    while True:
                        message = await websocket.receive_text()
                        if len(message.encode()) > _c.max_message_bytes:
                            await websocket.close(code=1009)
                            break
                        require(p, _c.publish_permission)
                        if _project.rate_limit.enabled:
                            await _rt.limiter.check(
                                f"{_project.slug}:{p.kind}:{ws_subject}:ws-message:{_c.name}",
                                _c.websocket_message_requests or p.rate_requests or _project.rate_limit.requests,
                                _c.websocket_message_window_seconds or p.rate_window_seconds or _project.rate_limit.window_seconds,
                                _c.websocket_message_burst or p.rate_burst or _project.rate_limit.burst,
                            )
                        await _rt.event_hub.publish(_c.name, {"channel": _c.name, "event": message, "publisher": p.subject})
                except WebSocketDisconnect:
                    pass
                except HTTPException:
                    try:
                        await websocket.close(code=1008)
                    except Exception:
                        log.warning("Failed to close policy-rejected WebSocket", exc_info=True)
                finally:
                    await _rt.event_hub.disconnect_ws(_c.name, websocket)

            app.add_api_websocket_route(base + "/ws", hide_internal_signature(websocket_endpoint), name=f"{project.slug}_event_ws_{index}")

    if project.media.enabled:

        @hidden_route(app.post(f"{prefix}/media", tags=[f"{project.slug}:media"], name=f"{project.slug}_media_upload"))
        async def media_upload(
            request: Request, background_tasks: BackgroundTasks, file: UploadFile = File(...), _rt=runtime, _project=project
        ):
            p = await principal_for(request, _rt)
            require(p, _project.media.upload_permission)
            result = await save_media(
                engine=app.state.internal_engine,
                store=_rt.media_store,
                project_slug=_project.slug,
                config=_project.media,
                upload=file,
                owner_subject=p.subject,
            )
            for hook_path in _project.media.post_upload_hooks:
                background_tasks.add_task(invoke_hook, import_callable(hook_path), media=result, principal=p, app=app, project=_project)
            return media_api_meta(result)

        @hidden_route(app.post(f"{prefix}/media/_batch", tags=[f"{project.slug}:media"], name=f"{project.slug}_media_batch_upload"))
        async def media_batch_upload(
            request: Request, background_tasks: BackgroundTasks, files: list[UploadFile] = File(...), _rt=runtime, _project=project
        ):
            p = await principal_for(request, _rt)
            require(p, _project.media.upload_permission)
            if not files or len(files) > _project.media.max_batch_files:
                raise HTTPException(status_code=422, detail=f"Batch media count must be 1..{_project.media.max_batch_files}")
            items = []
            succeeded = 0
            for file in files:
                try:
                    item = await save_media(
                        engine=app.state.internal_engine,
                        store=_rt.media_store,
                        project_slug=_project.slug,
                        config=_project.media,
                        upload=file,
                        owner_subject=p.subject,
                    )
                    items.append({"ok": True, "media": media_api_meta(item)})
                    succeeded += 1
                    for hook_path in _project.media.post_upload_hooks:
                        background_tasks.add_task(
                            invoke_hook, import_callable(hook_path), media=item, principal=p, app=app, project=_project
                        )
                except HTTPException as exc:
                    items.append({"ok": False, "filename": file.filename, "status": exc.status_code, "detail": exc.detail})
                except Exception:
                    log.exception("Batch media upload failed project=%s filename=%s", _project.slug, file.filename)
                    items.append({"ok": False, "filename": file.filename, "status": 500, "detail": "Upload failed"})
            payload = {"items": items, "count": len(items), "succeeded": succeeded, "failed": len(items) - succeeded}
            return JSONResponse(payload, status_code=200 if succeeded == len(items) else 207)

        @hidden_route(app.get(f"{prefix}/media/{{media_id}}/meta", tags=[f"{project.slug}:media"], name=f"{project.slug}_media_meta"))
        async def media_meta(media_id: str, request: Request, _rt=runtime, _project=project):
            p = await principal_for(request, _rt)
            require(p, _project.media.read_permission) if not _project.media.public else None
            meta = await get_media_meta(app.state.internal_engine, _project.slug, media_id)
            return media_api_meta(
                meta, include_owner=p.subject == meta["owner_subject"] or has_permission(p, _project.media.admin_permission)
            )

        @hidden_route(
            app.post(f"{prefix}/media/{{media_id}}/signed-url", tags=[f"{project.slug}:media"], name=f"{project.slug}_media_signed_url")
        )
        async def media_signed_url(media_id: str, request: Request, _rt=runtime, _project=project):
            p = await principal_for(request, _rt)
            require(p, _project.media.read_permission)
            if not _project.media.signed_urls_enabled:
                raise HTTPException(status_code=404, detail="Signed media URLs are disabled")
            await get_media_meta(app.state.internal_engine, _project.slug, media_id)
            token = make_signed_media_token(
                _project.slug, media_id, _project.media.signed_url_ttl_seconds, secret=_project.media.signing_secret or ""
            )
            return {
                "path": f"{_project.api_prefix.rstrip('/')}/media/{media_id}?token={token}",
                "expires_in": _project.media.signed_url_ttl_seconds,
            }

        @hidden_route(app.get(f"{prefix}/media/{{media_id}}", tags=[f"{project.slug}:media"], name=f"{project.slug}_media_get"))
        async def media_get(media_id: str, request: Request, token: str | None = None, _rt=runtime, _project=project):
            p = await principal_for(request, _rt)
            signed = _project.media.signed_urls_enabled and verify_signed_media_token(
                _project.slug, media_id, token, secret=_project.media.signing_secret or ""
            )
            if not _project.media.public and not signed:
                require(p, _project.media.read_permission)
            meta = await get_media_meta(app.state.internal_engine, _project.slug, media_id)
            path = _rt.media_store.path_for(meta["storage_key"])
            if not path.exists():
                raise HTTPException(status_code=404, detail="Media object is missing from storage")
            return FileResponse(path, media_type=meta["content_type"], filename=meta["original_name"])

        @hidden_route(app.delete(f"{prefix}/media/{{media_id}}", tags=[f"{project.slug}:media"], name=f"{project.slug}_media_delete"))
        async def media_delete_route(media_id: str, request: Request, _rt=runtime, _project=project):
            p = await principal_for(request, _rt)
            require(p, _project.media.delete_permission)
            if _project.media.owner_delete_only:
                meta = await get_media_meta(app.state.internal_engine, _project.slug, media_id)
                if meta["owner_subject"] != p.subject and not has_permission(p, _project.media.admin_permission):
                    raise HTTPException(status_code=403, detail="Only the media owner may delete this object")
            return {"deleted": await delete_media(app.state.internal_engine, _rt.media_store, _project.slug, media_id)}

    for index, webhook in enumerate(project.webhook_docs):

        async def documented_webhook(payload: Any = Body(...)):
            return None

        extra = {"requestBody": {"content": {"application/json": {"schema": webhook.payload_schema}}}} if webhook.payload_schema else None
        app.webhooks.add_api_route(
            f"{project.slug}.{webhook.name}",
            documented_webhook,
            methods=[webhook.method],
            summary=webhook.summary,
            description=webhook.description,
            name=f"{project.slug}_webhook_doc_{index}",
            openapi_extra=extra,
        )
