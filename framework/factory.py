from __future__ import annotations

import asyncio
import inspect
import logging
import re
import time
import uuid
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI, HTTPException, Request
from fastapi.middleware.gzip import GZipMiddleware
from fastapi.responses import JSONResponse, Response
from fastapi.routing import APIRoute
from sqlalchemy.ext.asyncio import create_async_engine

from .audit import AuditWriter
from .config import DataSourceConfig, ForgeConfig, OperationConfig, ProjectConfig, ResourceConfig, load_config_resilient
from .doctor import ensure_no_errors, project_diagnostics
from .domain import expand_feature_packs
from .editor_api import EDITOR_PREFIX, register_editor_api
from .editor_identity import init_editor_identity
from .observability import measure_stage, metrics_payload, observe, observe_stage
from .protection import RequestBodyLimitMiddleware, RequestExecutionTimeoutMiddleware, client_ip, host_allowed, ip_allowed, request_is_https
from .readiness import check_runtime
from .routers import register_project_routes
from .runtime import ProjectRuntime, RuntimeManager
from .security import authenticate_request, has_permission, init_security
from .settings import settings

log = logging.getLogger("json_api_forge")
_REQUEST_ID = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:-]{0,63}$")


def _resource_namespace(project: ProjectConfig, resource: ResourceConfig) -> str:
    return f"{project.slug}:{resource.database}:{resource.table}"


def _operation_namespace(project: ProjectConfig, operation: OperationConfig) -> str:
    return f"{project.slug}:operation:{operation.name}"


def _data_namespace(project: ProjectConfig, source: DataSourceConfig) -> str:
    return f"{project.slug}:data:{source.name}"


async def _invoke_hook(handler, **kwargs):
    if inspect.iscoroutinefunction(handler):
        return await handler(**kwargs)
    result = await asyncio.to_thread(handler, **kwargs)
    if inspect.isawaitable(result):
        return await result
    return result


def _hide_internal_signature(func):
    signature = inspect.signature(func)
    public = [parameter for name, parameter in signature.parameters.items() if not name.startswith("_")]
    func.__signature__ = signature.replace(parameters=public)
    return func


def _hidden_route(route_decorator):
    def register(func):
        return route_decorator(_hide_internal_signature(func))

    return register


def _append_vary(response: Response, value: str) -> None:
    current = [item.strip() for item in response.headers.get("Vary", "").split(",") if item.strip()]
    if value.lower() not in {item.lower() for item in current}:
        current.append(value)
    if current:
        response.headers["Vary"] = ", ".join(current)


def _cors_origin_allowed(project: ProjectConfig, origin: str | None) -> bool:
    return bool(origin and ("*" in project.cors_origins or origin in project.cors_origins))


def _apply_cors_response(project: ProjectConfig, request: Request, response: Response) -> None:
    origin = request.headers.get("origin")
    if not _cors_origin_allowed(project, origin):
        return
    wildcard = "*" in project.cors_origins and not project.cors_allow_credentials
    response.headers["Access-Control-Allow-Origin"] = "*" if wildcard else str(origin)
    if not wildcard:
        _append_vary(response, "Origin")
    if project.cors_allow_credentials:
        response.headers["Access-Control-Allow-Credentials"] = "true"
    if project.cors_expose_headers:
        response.headers["Access-Control-Expose-Headers"] = ", ".join(project.cors_expose_headers)


def _internal_engine_kwargs(url: str) -> dict:
    kwargs = {"pool_pre_ping": settings.internal_pool_pre_ping}
    if not url.startswith("sqlite+"):
        kwargs.update(
            pool_size=settings.internal_pool_size,
            max_overflow=settings.internal_max_overflow,
            pool_timeout=settings.internal_pool_timeout,
            pool_recycle=settings.internal_pool_recycle,
        )
    return kwargs


def _operator_authorized(request: Request) -> bool:
    token = settings.operator_token
    if not token:
        return False
    supplied = request.headers.get("X-Forge-Operator-Token")
    if not supplied:
        auth = request.headers.get("Authorization", "")
        if auth.lower().startswith("bearer "):
            supplied = auth.split(" ", 1)[1]
    if not supplied:
        return False
    import hmac

    return hmac.compare_digest(supplied, token)


def _check_route_integrity(routes) -> None:
    paths = set()
    operations = set()
    for route in routes:
        for method in getattr(route, "methods", {"WS"}):
            key = (method, re.sub(r"\{[^{}]+\}", "{}", route.path))
            if key in paths:
                raise ValueError("Duplicate generated route")
            paths.add(key)
        if isinstance(route, APIRoute) and route.include_in_schema:
            operation = route.operation_id or route.unique_id
            if operation in operations:
                raise ValueError("Duplicate generated operation ID")
            operations.add(operation)


def create_app(*, apps_dir: Path | str | None = None, _configuration=None, _reuse=None) -> FastAPI:
    logging.basicConfig(level=getattr(logging, settings.log_level.upper(), logging.INFO))
    resolved_apps_dir = Path(apps_dir) if apps_dir is not None else settings.apps_dir
    forge, config_issues = _configuration if _configuration is not None else load_config_resilient(resolved_apps_dir)
    production = settings.app_env.lower() == "production"
    diagnostics = ensure_no_errors(ForgeConfig(projects=[]), production=production)
    project_errors: dict[str, str] = {}
    for project in forge.projects:
        try:
            expand_feature_packs(project)
            checks = project_diagnostics(project, production=production)
            diagnostics.extend(checks)
            if any(check.level == "error" for check in checks):
                project_errors[project.slug] = "ConfigurationError"
        except Exception as exc:
            project_errors[project.slug] = type(exc).__name__
    for diagnostic in diagnostics:
        if diagnostic.level == "warning":
            log.warning("config warning code=%s project=%s message=%s", diagnostic.code, diagnostic.project, diagnostic.message)

    gzip_size = None
    metrics_path = None
    for project in forge.projects:
        if project.slug in project_errors:
            continue
        candidate_metrics = project.observability.metrics_path if project.observability.metrics_enabled else None
        if candidate_metrics and (
            candidate_metrics in {"/health", "/ready"}
            or candidate_metrics == "/__forge"
            or candidate_metrics.startswith("/__forge/")
            or any(candidate_metrics == p.api_prefix or candidate_metrics.startswith(p.api_prefix + "/") for p in forge.projects)
        ):
            project_errors[project.slug] = "ReservedServiceConflict"
            continue
        if (gzip_size is not None and gzip_size != project.protection.gzip_minimum_size) or (
            metrics_path is not None and candidate_metrics is not None and metrics_path != candidate_metrics
        ):
            project_errors[project.slug] = "SharedMiddlewareConflict"
            continue
        gzip_size = project.protection.gzip_minimum_size
        metrics_path = metrics_path or candidate_metrics

    reusable = {slug: runtime for slug, runtime in (_reuse or {}).items() if slug not in project_errors}
    runtime_manager = RuntimeManager(forge, reuse=reusable)
    runtimes = runtime_manager.runtimes
    for slug, error_type in project_errors.items():
        runtimes[slug].error_type = error_type
    for name, error_type in {**config_issues, **project_errors}.items():
        log.error("App configuration unavailable app=%s error_type=%s", name, error_type)
    internal_url = settings.internal_database_url
    if internal_url.startswith("sqlite+aiosqlite:///"):
        db_path = internal_url[len("sqlite+aiosqlite:///") :]
        if db_path and db_path != ":memory:":
            Path(db_path).parent.mkdir(parents=True, exist_ok=True)

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        app.state.internal_engine = None
        app.state.audit_writer = None
        app.state.jwks_clients = {}
        try:
            app.state.internal_engine = create_async_engine(internal_url, **_internal_engine_kwargs(internal_url))
            with measure_stage("global", "internal_schema_startup"):
                await init_security(app.state.internal_engine, mode=settings.internal_schema_mode)
            if settings.editor_api_enabled:
                with measure_stage("global", "editor_schema_startup"):
                    await init_editor_identity(app.state.internal_engine, mode=settings.internal_schema_mode)
            app.state.audit_writer = AuditWriter(app.state.internal_engine)
            await app.state.audit_writer.start()
            await runtime_manager.start()
            app.state.runtimes = runtimes
            yield
        finally:
            await asyncio.gather(*(client.close() for client in app.state.jwks_clients.values()), return_exceptions=True)
            app.state.jwks_clients.clear()
            try:
                await runtime_manager.close()
            except Exception:
                log.exception("Runtime cleanup failed")
            if app.state.audit_writer is not None:
                try:
                    await app.state.audit_writer.close()
                except Exception:
                    log.exception("Audit writer cleanup failed")
            if app.state.internal_engine is not None:
                try:
                    await app.state.internal_engine.dispose()
                except Exception:
                    log.exception("Internal database cleanup failed")

    app = FastAPI(
        title=forge.name,
        version=forge.version,
        description="Multi-project JSON-defined FastAPI backend runtime",
        default_response_class=JSONResponse,
        lifespan=lifespan,
        docs_url=None,
        redoc_url=None,
        openapi_url=None,
    )

    app.add_middleware(GZipMiddleware, minimum_size=gzip_size if gzip_size is not None else 1024)

    def body_limit_for_path(path: str) -> int | None:
        if path == EDITOR_PREFIX or path.startswith(EDITOR_PREFIX + "/"):
            if "/attachments" in path:
                return settings.editor_max_attachment_bytes + 1024 * 1024
            return settings.editor_max_document_bytes
        return runtime_manager.body_limit_for_path(path)

    app.add_middleware(RequestBodyLimitMiddleware, limit_for_path=body_limit_for_path)

    def timeout_for_path(path: str) -> float | None:
        runtime = runtime_manager.for_path(path)
        return runtime.config.protection.request_timeout_seconds if runtime else None

    app.add_middleware(RequestExecutionTimeoutMiddleware, timeout_for_path=timeout_for_path)

    def runtime_for_path(path: str) -> ProjectRuntime | None:
        return runtime_manager.for_path(path)

    async def _limiter_check(runtime: ProjectRuntime, identity: str, limit: int, window: int, burst: int | None) -> None:
        try:
            await runtime.limiter.check(identity, limit, window, burst)
        except HTTPException:
            raise
        except Exception as exc:
            if runtime.config.rate_limit.fail_open:
                log.warning("Rate limiter unavailable; fail_open=true project=%s error=%s", runtime.config.slug, type(exc).__name__)
                return
            raise HTTPException(status_code=503, detail="Rate limiter is temporarily unavailable", headers={"Retry-After": "1"}) from exc

    @app.middleware("http")
    async def protection_and_context(request: Request, call_next):
        raw_request_id = request.headers.get("X-Request-ID")
        request_id = raw_request_id if raw_request_id and _REQUEST_ID.fullmatch(raw_request_id) else str(uuid.uuid4())
        request.state.request_id = request_id
        request.state.principal = None
        request.state.project_slug = None
        runtime = runtime_for_path(request.url.path)
        start = time.perf_counter()
        response: Response | None = None
        status_code = 500
        try:
            if runtime:
                cfg = runtime.config
                request.state.project_slug = cfg.slug
                if not runtime.available:
                    raise HTTPException(status_code=503, detail="This app is temporarily unavailable", headers={"Retry-After": "1"})
                trusted_proxies = cfg.protection.trusted_proxy_cidrs

                if not host_allowed(request.headers.get("host"), cfg.protection.trusted_hosts):
                    raise HTTPException(status_code=400, detail="Host is not allowed for this project")

                origin = request.headers.get("origin")
                if request.method == "OPTIONS" and request.headers.get("access-control-request-method"):
                    if not _cors_origin_allowed(cfg, origin):
                        raise HTTPException(status_code=403, detail="CORS origin is not allowed for this project")
                    requested_method = request.headers.get("access-control-request-method", "").upper()
                    allowed_methods = {method.upper() for method in cfg.cors_methods}
                    if requested_method not in allowed_methods:
                        raise HTTPException(status_code=403, detail="CORS method is not allowed for this project")
                    requested_headers = {
                        value.strip().lower()
                        for value in request.headers.get("access-control-request-headers", "").split(",")
                        if value.strip()
                    }
                    allowed_headers = {value.lower() for value in cfg.cors_headers}
                    if "*" not in allowed_headers and not requested_headers.issubset(allowed_headers):
                        raise HTTPException(status_code=403, detail="CORS request headers are not allowed for this project")
                    response = Response(status_code=204)
                    _apply_cors_response(cfg, request, response)
                    response.headers["Access-Control-Allow-Methods"] = ", ".join(cfg.cors_methods)
                    response.headers["Access-Control-Allow-Headers"] = ", ".join(cfg.cors_headers)
                    response.headers["Access-Control-Max-Age"] = str(cfg.cors_max_age_seconds)
                    status_code = response.status_code
                    return response

                if cfg.security.require_https and not request_is_https(request, trusted_proxies):
                    raise HTTPException(status_code=400, detail="HTTPS is required")
                if not ip_allowed(request, cfg.security.allowed_ips, cfg.security.denied_ips, trusted_proxies):
                    raise HTTPException(status_code=403, detail="Client IP is not allowed")

                if cfg.rate_limit.enabled and cfg.rate_limit.pre_auth_enabled:
                    ip = client_ip(request, trusted_proxies)
                    await _limiter_check(
                        runtime,
                        f"{cfg.slug}:preauth:ip:{ip}",
                        cfg.rate_limit.pre_auth_requests,
                        cfg.rate_limit.pre_auth_window_seconds,
                        cfg.rate_limit.pre_auth_burst,
                    )

                gate_started = time.perf_counter()
                async with runtime.gate:
                    observe_stage(cfg.slug, "queue_wait", time.perf_counter() - gate_started)
                    response = await call_next(request)
            else:
                response = await call_next(request)
            status_code = response.status_code
            return response
        except HTTPException as exc:
            response = JSONResponse(status_code=exc.status_code, content={"detail": exc.detail}, headers=exc.headers or {})
            status_code = exc.status_code
            return response
        finally:
            elapsed_s = time.perf_counter() - start
            if response is not None:
                if runtime:
                    _apply_cors_response(runtime.config, request, response)
                response.headers["X-Request-ID"] = request_id
                response.headers["X-Content-Type-Options"] = "nosniff"
                response.headers["X-Frame-Options"] = "DENY"
                response.headers["Referrer-Policy"] = "strict-origin-when-cross-origin"
                response.headers.setdefault("Permissions-Policy", "camera=(), microphone=(), geolocation=()")
                if request.url.path == EDITOR_PREFIX or request.url.path.startswith(EDITOR_PREFIX + "/"):
                    response.headers.setdefault("Cache-Control", "no-store")
                    response.headers.setdefault("Cross-Origin-Resource-Policy", "same-origin")
                cache_status = getattr(request.state, "forge_cache", None)
                if cache_status:
                    response.headers["X-Forge-Cache"] = str(cache_status)
                if getattr(request.state, "forge_idempotent_replay", False):
                    response.headers["X-Forge-Idempotent-Replay"] = "true"
            project_slug = runtime.config.slug if runtime else "global"
            observe(project_slug, request.method, status_code, elapsed_s)
            if runtime and runtime.config.audit_enabled and hasattr(app.state, "audit_writer") and app.state.audit_writer is not None:
                p = getattr(request.state, "principal", None)
                app.state.audit_writer.submit(
                    project_slug=runtime.config.slug,
                    request_id=request_id,
                    principal_kind=getattr(p, "kind", "unresolved"),
                    principal_subject=getattr(p, "subject", "unresolved")[:160],
                    method=request.method[:12],
                    path=request.url.path[:512],
                    status_code=status_code,
                    duration_ms=int(elapsed_s * 1000),
                )
            log.info("%s %s %s %.1fms request_id=%s", request.method, request.url.path, status_code, elapsed_s * 1000, request_id)

    async def principal_for(request: Request, runtime: ProjectRuntime):
        cfg = runtime.config
        with measure_stage(cfg.slug, "authentication"):
            principal = await authenticate_request(request, cfg, app.state.internal_engine)
        request.state.principal = principal
        if cfg.rate_limit.enabled:
            trusted_proxies = cfg.protection.trusted_proxy_cidrs
            identity_subject = principal.subject if principal.kind != "anonymous" else f"ip:{client_ip(request, trusted_proxies)}"
            route = request.scope.get("route")
            route_template = getattr(route, "path", None) or request.url.path
            await _limiter_check(
                runtime,
                f"{cfg.slug}:{principal.kind}:{identity_subject}:global",
                principal.rate_requests or cfg.rate_limit.requests,
                principal.rate_window_seconds or cfg.rate_limit.window_seconds,
                principal.rate_burst or cfg.rate_limit.burst,
            )
            if cfg.rate_limit.route_requests:
                await _limiter_check(
                    runtime,
                    f"{cfg.slug}:{principal.kind}:{identity_subject}:route:{request.method}:{route_template}",
                    cfg.rate_limit.route_requests,
                    cfg.rate_limit.route_window_seconds or cfg.rate_limit.window_seconds,
                    cfg.rate_limit.route_burst,
                )
        return principal

    def require(principal, permission: str | None):
        if has_permission(principal, permission):
            return
        if principal.kind == "anonymous":
            raise HTTPException(status_code=401, detail="Authentication required")
        raise HTTPException(status_code=403, detail=f"Missing permission: {permission}")

    @_hidden_route(app.get("/health", tags=["system"], include_in_schema=False))
    async def health():
        return {"status": "ok"}

    readiness_slots = asyncio.Semaphore(settings.readiness_max_concurrency)

    @_hidden_route(app.get("/ready", tags=["system"], include_in_schema=False))
    async def ready(request: Request):
        detailed = _operator_authorized(request)
        checks = {}
        all_ok = bool(runtimes) and not config_issues
        results = await asyncio.gather(
            *(
                check_runtime(runtime, detailed=detailed, semaphore=readiness_slots, timeout=settings.readiness_timeout_seconds)
                for runtime in runtimes.values()
            )
        )
        for slug, (project_ok, result) in zip(runtimes, results, strict=True):
            all_ok = all_ok and project_ok
            if detailed:
                checks[slug] = result
        payload = {"status": "ready" if all_ok else "degraded"}
        if detailed:
            payload["projects"] = checks
            payload["configuration_errors"] = config_issues
        if not all_ok:
            return JSONResponse(status_code=503, content=payload)
        return payload

    if metrics_path is not None:

        @_hidden_route(app.get(metrics_path, include_in_schema=False))
        async def metrics(request: Request):
            if not _operator_authorized(request):
                raise HTTPException(status_code=401, detail="Operator authentication required")
            payload, content_type = metrics_payload()
            return Response(payload, media_type=content_type)

    for runtime in runtimes.values():
        if runtime.error_type:
            continue
        route_count = len(app.router.routes)
        try:
            register_project_routes(
                app=app,
                runtime=runtime,
                principal_for=principal_for,
                require=require,
                hide_internal_signature=_hide_internal_signature,
                hidden_route=_hidden_route,
                invoke_hook=_invoke_hook,
            )
            _check_route_integrity(app.router.routes)
        except Exception as exc:
            del app.router.routes[route_count:]
            error_type = type(exc).__name__
            # A candidate's route failure must not mutate a borrowed live runtime.
            runtimes[runtime.config.slug] = ProjectRuntime(config=runtime.config, error_type=error_type)
            log.error("App route registration failed project=%s error_type=%s", runtime.config.slug, error_type)
    register_editor_api(app, apps_dir=resolved_apps_dir, settings=settings, runtimes=runtimes)
    return app
