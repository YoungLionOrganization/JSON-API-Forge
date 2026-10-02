from __future__ import annotations

import re
from collections.abc import Iterable
from dataclasses import asdict, dataclass

from .config import ForgeConfig, ProjectConfig
from .settings import settings

_PATH_PARAM = re.compile(r"\{[^{}]+\}")
_WEAK_MARKERS = {"change-me", "changeme", "secret", "password", "admin", "test", "example", "default"}


@dataclass(slots=True)
class Diagnostic:
    level: str
    code: str
    message: str
    project: str | None = None

    def to_dict(self) -> dict:
        return asdict(self)


def is_weak_secret(value: str | None, *, minimum_length: int = 32) -> bool:
    if not value:
        return True
    normalized = value.strip().lower()
    if len(value) < minimum_length or normalized in _WEAK_MARKERS:
        return True
    if any(normalized.startswith(marker + sep) for marker in _WEAK_MARKERS for sep in ("-", "_", ":")):
        return True
    compact = normalized.replace("-", "").replace("_", "")
    if compact and len(set(compact)) <= 4:
        return True
    return False


def normalize_route(path: str) -> str:
    path = "/" + path.strip("/")
    path = _PATH_PARAM.sub("{}", path)
    return re.sub(r"/{2,}", "/", path)


def _path_segments(path: str) -> list[str]:
    return [segment for segment in ("/" + path.strip("/")).split("/") if segment]


def route_shadows(earlier: str, later: str) -> bool:
    a, b = _path_segments(earlier), _path_segments(later)
    if len(a) != len(b):
        return False
    dynamic = False
    for left, right in zip(a, b, strict=True):
        if left.startswith("{") and left.endswith("}"):
            dynamic = True
            continue
        if left != right:
            return False
    return dynamic and earlier != later


def _route_specs(project: ProjectConfig) -> Iterable[tuple[str, str, str, str]]:
    prefix = project.api_prefix.rstrip("/")

    def emit(method: str, suffix: str, owner: str):
        raw = re.sub(r"/{2,}", "/", f"{prefix}/{suffix.lstrip('/')}")
        return method.upper(), normalize_route(raw), owner, raw

    yield emit("GET", "meta", "system:meta")
    if project.docs_enabled:
        yield emit("GET", "_openapi.json", "system:openapi")
        yield emit("GET", "_docs", "system:docs")
        yield emit("GET", "_redoc", "system:redoc")
    for method, suffix, owner in [
        ("POST", "admin/api-keys", "admin:api-key-create"),
        ("GET", "admin/api-keys", "admin:api-key-list"),
        ("DELETE", "admin/api-keys/{key_id}", "admin:api-key-revoke"),
    ]:
        yield emit(method, suffix, owner)
    if project.security.jwt_enabled and project.security.jwt_provider == "local_hs256":
        yield emit("POST", "admin/jwt", "admin:jwt")
    for resource in project.resources:
        if not resource.enabled:
            continue
        base = resource.path.strip("/")
        if "list" in resource.allowed_actions:
            yield emit("GET", base, f"resource:{resource.path}:list")
            if resource.count_enabled:
                yield emit("GET", base + "/_count", f"resource:{resource.path}:count")
        if "read" in resource.allowed_actions:
            yield emit("GET", base + "/{item_id}", f"resource:{resource.path}:read")
        if "create" in resource.allowed_actions:
            yield emit("POST", base, f"resource:{resource.path}:create")
            if resource.batch_enabled:
                yield emit("POST", base + "/_batch", f"resource:{resource.path}:batch")
        if "update" in resource.allowed_actions:
            yield emit("PATCH", base + "/{item_id}", f"resource:{resource.path}:update")
            yield emit("PUT", base + "/{item_id}", f"resource:{resource.path}:replace")
        if "delete" in resource.allowed_actions:
            yield emit("DELETE", base + "/{item_id}", f"resource:{resource.path}:delete")
    for resource in project.mongo_resources:
        if not resource.enabled:
            continue
        base = resource.path.strip("/")
        if "list" in resource.allowed_actions:
            yield emit("GET", base, f"mongo:{resource.path}:list")
        if "read" in resource.allowed_actions:
            yield emit("GET", base + "/{item_id}", f"mongo:{resource.path}:read")
        if "create" in resource.allowed_actions:
            yield emit("POST", base, f"mongo:{resource.path}:create")
        if "update" in resource.allowed_actions:
            yield emit("PATCH", base + "/{item_id}", f"mongo:{resource.path}:update")
            yield emit("PUT", base + "/{item_id}", f"mongo:{resource.path}:replace")
        if "delete" in resource.allowed_actions:
            yield emit("DELETE", base + "/{item_id}", f"mongo:{resource.path}:delete")
    for op in project.operations:
        yield emit(op.method, op.path or f"rpc/{op.name}", f"operation:{op.name}")
    for source in project.data_sources:
        if not source.enabled:
            continue
        method = source.method if source.type == "http" else "GET"
        yield emit(method, source.path or f"data/{source.name}", f"data-source:{source.name}:read")
        if source.writable:
            yield emit("POST", source.path or f"data/{source.name}", f"data-source:{source.name}:create")
            yield emit("PATCH", (source.path or f"data/{source.name}") + "/{item_id}", f"data-source:{source.name}:update")
            yield emit("PUT", (source.path or f"data/{source.name}") + "/{item_id}", f"data-source:{source.name}:replace")
            yield emit("DELETE", (source.path or f"data/{source.name}") + "/{item_id}", f"data-source:{source.name}:delete")
    for index, endpoint in enumerate(project.custom_endpoints):
        yield emit(endpoint.method, endpoint.path, f"custom:{index}:{endpoint.handler}")
    for channel in project.event_channels:
        base = channel.path or f"events/{channel.name}"
        yield emit("POST", base, f"event:{channel.name}:publish")
        if channel.sse_enabled:
            yield emit("GET", base + "/stream", f"event:{channel.name}:sse")
        if channel.websocket_enabled:
            yield emit("WS", base + "/ws", f"event:{channel.name}:ws")
    if project.media.enabled:
        for method, suffix, owner in [
            ("POST", "media", "media:upload"),
            ("POST", "media/_batch", "media:batch"),
            ("GET", "media/{media_id}/meta", "media:meta"),
            ("POST", "media/{media_id}/signed-url", "media:signed-url"),
            ("GET", "media/{media_id}", "media:get"),
            ("DELETE", "media/{media_id}", "media:delete"),
        ]:
            yield emit(method, suffix, owner)


def project_diagnostics(project: ProjectConfig, *, production: bool = False) -> list[Diagnostic]:
    out = []
    slug = project.slug
    seen = {}
    ordered_routes = list(_route_specs(project))
    for method, path, owner, _raw_path in ordered_routes:
        key = method, path
        previous = seen.get(key)
        if previous:
            out.append(Diagnostic("error", "route-collision", f"{method} {path} is generated by both {previous} and {owner}", slug))
        else:
            seen[key] = owner
    for index, (method, _path, owner, raw_path) in enumerate(ordered_routes):
        for later_method, _later_path, later_owner, later_raw in ordered_routes[index + 1 :]:
            if method == later_method and route_shadows(raw_path, later_raw):
                out.append(
                    Diagnostic(
                        "error",
                        "route-shadow",
                        f"{method} {raw_path} ({owner}) is registered before and can shadow {later_raw} ({later_owner})",
                        slug,
                    )
                )

    def duplicate(values: list[str], code: str, label: str):
        dupes = sorted({v for v in values if values.count(v) > 1})
        for value in dupes:
            out.append(Diagnostic("error", code, f"Duplicate {label}: {value}", slug))

    duplicate([x.name for x in project.operations], "duplicate-operation", "operation name")
    duplicate([x.name for x in project.data_sources], "duplicate-data-source", "data source name")
    duplicate([x.name for x in project.event_channels], "duplicate-event-channel", "event channel name")
    dependencies = {d.name for d in project.dependencies}
    for owner, names in [
        *[(f"resource:{r.path}", r.dependencies) for r in project.resources],
        *[(f"mongo:{r.path}", r.dependencies) for r in project.mongo_resources],
        *[(f"operation:{o.name}", o.dependencies) for o in project.operations],
        *[(f"data-source:{d.name}", d.dependencies) for d in project.data_sources],
        *[(f"custom:{c.path}", c.dependencies) for c in project.custom_endpoints],
    ]:
        for name in names:
            if name not in dependencies:
                out.append(Diagnostic("error", "unknown-dependency", f"{owner} references unknown dependency {name!r}", slug))
    sql_aliases = set(project.databases)
    for r in project.resources:
        if r.database not in sql_aliases:
            out.append(Diagnostic("error", "unknown-database", f"resource {r.path!r} references database {r.database!r}", slug))
    for o in project.operations:
        if o.database not in sql_aliases:
            out.append(Diagnostic("error", "unknown-database", f"operation {o.name!r} references database {o.database!r}", slug))
        if o.allow_ddl:
            out.append(
                Diagnostic(
                    "warning",
                    "ddl-enabled",
                    f"operation {o.name!r} enables declarative DDL; SQL regex checks are guardrails, not a sandbox",
                    slug,
                )
            )
        if any(statement.mode == "execute" for statement in o.statements) and not (o.invalidate_resources or o.invalidate_operations):
            out.append(
                Diagnostic(
                    "warning",
                    "mutation-without-cache-invalidation",
                    f"operation {o.name!r} mutates SQL but declares no resource/operation cache invalidation",
                    slug,
                )
            )
    mongo_aliases = set(project.mongo_databases)
    for r in project.mongo_resources:
        if r.database not in mongo_aliases:
            out.append(Diagnostic("error", "unknown-mongo-database", f"Mongo resource {r.path!r} references database {r.database!r}", slug))
    if production:
        if project.security.bootstrap_enabled:
            bootstrap = project.security.bootstrap_admin_key or settings.bootstrap_admin_key
            if is_weak_secret(bootstrap):
                out.append(
                    Diagnostic(
                        "error",
                        "weak-bootstrap-secret",
                        "Bootstrap is enabled but its admin key is missing, weak or default-like; run `forge init`/rotate it before production",
                        slug,
                    )
                )
        elif project.security.bootstrap_admin_key:
            out.append(
                Diagnostic("error", "bootstrap-disabled-with-key", "bootstrap_enabled=false but a bootstrap key is still configured", slug)
            )
        if project.security.jwt_enabled and project.security.jwt_provider == "local_hs256":
            jwt_secret = project.security.jwt_secret or settings.jwt_secret
            if is_weak_secret(jwt_secret, minimum_length=32):
                out.append(
                    Diagnostic(
                        "error",
                        "weak-jwt-secret",
                        "local_hs256 requires a strong project jwt_secret or JWT_SECRET fallback in production",
                        slug,
                    )
                )
            elif not project.security.jwt_secret:
                out.append(
                    Diagnostic(
                        "warning",
                        "shared-jwt-secret",
                        "local_hs256 uses the global JWT_SECRET fallback; prefer a project-scoped security.jwt_secret env reference for isolation",
                        slug,
                    )
                )
        if project.security.require_https is False:
            out.append(
                Diagnostic(
                    "warning",
                    "https-not-required",
                    "security.require_https=false; enforce TLS at the reverse proxy and configure trusted proxy handling correctly",
                    slug,
                )
            )
        if project.observability.metrics_enabled and is_weak_secret(settings.operator_token, minimum_length=32):
            out.append(
                Diagnostic(
                    "error",
                    "operator-token-missing",
                    "metrics are enabled but OPERATOR_TOKEN is missing or weak; process-wide telemetry must use a separate operator credential",
                    slug,
                )
            )
        if "*" in project.protection.trusted_hosts:
            out.append(Diagnostic("warning", "trusted-hosts-wildcard", "trusted_hosts contains '*' in production", slug))
        if "*" in project.cors_origins:
            out.append(Diagnostic("warning", "cors-wildcard", "cors_origins contains '*' in production", slug))
        if project.security.allow_query_api_key:
            out.append(
                Diagnostic("warning", "query-api-key", "API keys in query strings can leak through logs/history; prefer headers", slug)
            )
        if project.security.allow_websocket_query_api_key:
            out.append(
                Diagnostic(
                    "warning",
                    "websocket-query-api-key",
                    "WebSocket API keys in query strings can leak through proxy/browser logs; prefer headers or short-lived delegated credentials",
                    slug,
                )
            )
        if project.security.api_key_cache_ttl_seconds > 5:
            out.append(
                Diagnostic(
                    "warning",
                    "api-key-cache-revocation-window",
                    f"API-key auth cache TTL is {project.security.api_key_cache_ttl_seconds:g}s; other workers may accept a revoked key until their local cache entry expires",
                    slug,
                )
            )
        if project.docs_enabled:
            out.append(
                Diagnostic(
                    "warning",
                    "public-project-docs",
                    "project docs/OpenAPI are enabled; disable docs or protect them at the edge for sensitive production APIs",
                    slug,
                )
            )
        if (
            project.rate_limit.enabled
            and project.rate_limit.pre_auth_enabled
            and project.rate_limit.pre_auth_requests < project.rate_limit.requests
        ):
            out.append(
                Diagnostic(
                    "warning",
                    "preauth-stricter-than-principal",
                    "pre-auth IP budget is stricter than the normal principal budget and may throttle legitimate users behind NAT",
                    slug,
                )
            )
        if (
            project.security.jwt_provider == "jwks"
            and (
                project.security.jwt_trust_roles_claim
                or project.security.jwt_trust_permissions_claim
                or project.security.jwt_trust_tenant_claim
            )
            and not project.security.jwt_require_project_claim
        ):
            out.append(
                Diagnostic(
                    "warning",
                    "external-authz-without-project-binding",
                    "external JWT authorization/tenant claims are trusted while project claim binding is disabled",
                    slug,
                )
            )
        if project.security.jwt_provider == "jwks" and project.security.jwks_allow_insecure_http:
            out.append(
                Diagnostic(
                    "warning",
                    "insecure-jwks-egress",
                    "external JWT signing keys may be fetched over plaintext HTTP",
                    slug,
                )
            )
        if project.security.jwt_provider == "jwks" and project.security.jwks_allow_private_networks:
            out.append(
                Diagnostic(
                    "warning",
                    "private-network-jwks-egress",
                    "external JWT signing-key retrieval may reach private/link-local networks; enforce network-level egress controls",
                    slug,
                )
            )
        for source in project.data_sources:
            if source.type == "http" and source.allow_insecure_http:
                out.append(
                    Diagnostic("warning", "insecure-http-egress", f"data source {source.name!r} permits plaintext HTTP egress", slug)
                )
            if source.type == "http" and source.allow_private_networks:
                out.append(
                    Diagnostic(
                        "warning",
                        "private-network-egress",
                        f"data source {source.name!r} may reach private/link-local networks; enforce network-level egress controls",
                        slug,
                    )
                )
        needs_redis = (
            project.rate_limit.backend == "redis"
            or project.cache.backend in {"redis", "tiered"}
            or (project.realtime.backend == "redis" and bool(project.event_channels))
        )
        if needs_redis and not settings.redis_url:
            out.append(Diagnostic("error", "redis-url-missing", "A Redis-backed feature is enabled but REDIS_URL is empty", slug))
        if project.rate_limit.backend == "memory":
            out.append(
                Diagnostic(
                    "warning",
                    "memory-rate-limit",
                    "memory rate limiting is per-process; use Redis for multi-worker/global enforcement",
                    slug,
                )
            )
        if project.realtime.backend == "memory" and project.event_channels:
            out.append(
                Diagnostic(
                    "warning", "memory-realtime", "memory realtime does not cross workers; use Redis for multi-worker deployment", slug
                )
            )
        if project.media.enabled and project.media.backend == "local":
            out.append(
                Diagnostic(
                    "warning",
                    "local-media-storage",
                    "local media storage is host-local; use shared object storage before horizontally scaling",
                    slug,
                )
            )
        for alias, database in project.databases.items():
            if database.url.startswith("sqlite+"):
                out.append(
                    Diagnostic(
                        "warning",
                        "sqlite-production",
                        f"database {alias!r} uses SQLite; validate concurrency/durability requirements before production",
                        slug,
                    )
                )
            if database.support_schema_mode == "create":
                out.append(
                    Diagnostic(
                        "warning",
                        "runtime-schema-ddl",
                        f"database {alias!r} creates/updates Forge support schema at runtime; prefer forge migrate + support_schema_mode=validate in production",
                        slug,
                    )
                )
    return out


def forge_diagnostics(forge: ForgeConfig, *, production: bool = False) -> list[Diagnostic]:
    diagnostics = []
    if settings.editor_api_enabled:
        if (settings.editor_setup_enabled or settings.editor_legacy_token_enabled) and is_weak_secret(
            settings.editor_token, minimum_length=32
        ):
            diagnostics.append(
                Diagnostic(
                    "error",
                    "weak-editor-setup-token",
                    "Founder setup or legacy token mode requires a strong, independent EDITOR_TOKEN",
                )
            )
        if not settings.editor_setup_enabled and not settings.editor_legacy_token_enabled and settings.editor_token:
            diagnostics.append(
                Diagnostic(
                    "warning",
                    "editor-disabled-setup-token-present",
                    "Founder setup is disabled but EDITOR_TOKEN is still present; remove the unused secret",
                )
            )
        if not settings.editor_allowed_ips:
            diagnostics.append(
                Diagnostic(
                    "warning",
                    "editor-ip-policy-open",
                    "EDITOR_ALLOWED_IPS is empty; restrict the control plane at both the application and firewall/VPN layers",
                )
            )
        if "*" in {item.strip() for item in settings.editor_trusted_hosts.split(",")}:
            diagnostics.append(Diagnostic("warning", "editor-host-policy-open", "EDITOR_TRUSTED_HOSTS contains '*'"))
        if settings.editor_allow_hooks:
            diagnostics.append(
                Diagnostic(
                    "warning",
                    "editor-remote-code-editing",
                    "Remote Python hook editing is enabled; grant documents.hooks.write only to fully trusted operators",
                )
            )
        if production and not settings.editor_require_https:
            diagnostics.append(Diagnostic("error", "editor-https-disabled", "Production Editor control plane requires HTTPS"))
        if production and settings.editor_legacy_token_enabled:
            diagnostics.append(
                Diagnostic("error", "editor-legacy-token", "Production Editor control plane cannot use the shared legacy token mode")
            )
    for project in forge.projects:
        diagnostics.extend(project_diagnostics(project, production=production))
    return diagnostics


def ensure_no_errors(forge: ForgeConfig, *, production: bool = False) -> list[Diagnostic]:
    diagnostics = forge_diagnostics(forge, production=production)
    errors = [d for d in diagnostics if d.level == "error"]
    if errors:
        detail = "\n".join(f"[{d.code}] {d.project or 'global'}: {d.message}" for d in errors)
        raise RuntimeError(f"JSON API Forge configuration diagnostics failed:\n{detail}")
    return diagnostics
