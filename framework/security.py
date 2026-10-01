from __future__ import annotations

import asyncio
import hashlib
import hmac
import secrets
import time
from collections import OrderedDict
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Any

import httpx
import jwt
from fastapi import HTTPException, Request, status
from sqlalchemy import Boolean, Column, DateTime, Integer, MetaData, String, Table, Text, insert, select, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncEngine

from .config import ProjectConfig
from .services.http_client import ResilientHTTPClient, ResponseTooLarge
from .settings import settings

_internal_meta = MetaData()
_JWKSCacheKey = tuple[str, bool, bool, int]
_jwks_cache: dict[_JWKSCacheKey, tuple[float, dict[str, Any]]] = {}
_jwks_locks: dict[_JWKSCacheKey, asyncio.Lock] = {}
_jwks_refreshed: dict[_JWKSCacheKey, float] = {}


def _claim(payload: dict[str, Any], dotted: str, default: Any = None) -> Any:
    current: Any = payload
    for part in dotted.split("."):
        if not isinstance(current, dict) or part not in current:
            return default
        current = current[part]
    return current


def _claim_set(value: Any) -> set[str]:
    if value is None:
        return set()
    if isinstance(value, str):
        return {value}
    if isinstance(value, (list, tuple, set)):
        return {str(x) for x in value if x is not None}
    return {str(value)}


def _jwks_cache_key(
    url: str,
    *,
    allow_private_networks: bool,
    allow_insecure_http: bool,
    max_response_bytes: int,
) -> _JWKSCacheKey:
    return (url, allow_private_networks, allow_insecure_http, max_response_bytes)


async def _get_jwks(url: str, *, ttl: int, timeout: float, **kwargs) -> dict[str, Any]:
    try:
        return await asyncio.wait_for(_fetch_jwks(url, ttl=ttl, timeout=timeout, **kwargs), timeout)
    except asyncio.TimeoutError as exc:
        raise HTTPException(status_code=503, detail="JWT signing keys are temporarily unavailable") from exc


async def _fetch_jwks(
    url: str,
    *,
    ttl: int,
    timeout: float,
    allow_private_networks: bool = False,
    allow_insecure_http: bool = False,
    max_response_bytes: int = 1024 * 1024,
    force_refresh: bool = False,
    refresh_interval: float = 5.0,
    client_pool: dict | None = None,
) -> dict[str, Any]:
    normalized_url = url.lower()
    if not normalized_url.startswith("https://") and not (allow_insecure_http and normalized_url.startswith("http://")):
        raise HTTPException(status_code=503, detail="JWT signing key endpoint is not permitted")
    cache_key = _jwks_cache_key(
        url,
        allow_private_networks=allow_private_networks,
        allow_insecure_http=allow_insecure_http,
        max_response_bytes=max_response_bytes,
    )
    now = time.monotonic()
    cached = _jwks_cache.get(cache_key)
    if cached and cached[0] > now and (not force_refresh or now - _jwks_refreshed.get(cache_key, 0) < refresh_interval):
        return cached[1]
    lock = _jwks_locks.setdefault(cache_key, asyncio.Lock())
    async with lock:
        cached = _jwks_cache.get(cache_key)
        now = time.monotonic()
        if cached and cached[0] > now and (not force_refresh or now - _jwks_refreshed.get(cache_key, 0) < refresh_interval):
            return cached[1]
        last_attempt = _jwks_refreshed.get(cache_key)
        if last_attempt is not None and now - last_attempt < refresh_interval:
            # Never serve expired keys, but also do not hammer a failing provider.
            raise HTTPException(status_code=503, detail="JWT signing keys are temporarily unavailable")
        client: ResilientHTTPClient | None = None
        # Bound refresh attempts even if the upstream is currently failing.
        _jwks_refreshed[cache_key] = now
        try:
            client = client_pool.get(cache_key) if client_pool is not None else None
            if client is None:
                client = ResilientHTTPClient(timeout=timeout, block_private_networks=not allow_private_networks)
                if client_pool is not None:
                    client_pool[cache_key] = client
            response = await client.request(
                "GET",
                url,
                headers={"Accept": "application/json"},
                retries=1,
                max_response_bytes=max_response_bytes,
                timeout=timeout,
                total_timeout=timeout,
            )
            data = response.json()
        except (httpx.HTTPError, ResponseTooLarge, RuntimeError, ValueError) as exc:
            raise HTTPException(status_code=503, detail="JWT signing keys are temporarily unavailable") from exc
        finally:
            if client is not None and client_pool is None:
                await client.close()
        if (
            not isinstance(data, dict)
            or not isinstance(data.get("keys"), list)
            or len(data["keys"]) > 128
            or any(not isinstance(key, dict) for key in data["keys"])
        ):
            raise HTTPException(status_code=503, detail="JWT signing key endpoint returned invalid JWKS")
        _jwks_refreshed[cache_key] = time.monotonic()
        _jwks_cache[cache_key] = (_jwks_refreshed[cache_key] + ttl, data)
        return data


async def _decode_jwks_token(token: str, project: ProjectConfig, *, client_pool: dict | None = None) -> dict[str, Any]:
    cfg = project.security
    try:
        header = jwt.get_unverified_header(token)
    except jwt.PyJWTError as exc:
        raise HTTPException(status_code=401, detail="Invalid bearer token header") from exc
    kid = header.get("kid")
    alg = header.get("alg")
    if not isinstance(kid, str) or not 1 <= len(kid) <= 256 or not isinstance(alg, str) or alg not in cfg.jwt_algorithms:
        raise HTTPException(status_code=401, detail="Bearer token uses an unsupported signing key or algorithm")
    jwks_args = {
        "ttl": cfg.jwks_cache_ttl_seconds,
        "timeout": cfg.jwks_timeout_seconds,
        "allow_private_networks": cfg.jwks_allow_private_networks,
        "allow_insecure_http": cfg.jwks_allow_insecure_http,
        "max_response_bytes": cfg.jwks_max_response_bytes,
        "refresh_interval": cfg.jwks_refresh_interval_seconds,
    }
    if client_pool is not None:
        jwks_args["client_pool"] = client_pool
    jwks = await _get_jwks(cfg.jwt_jwks_url or "", **jwks_args)
    key_data = next((key for key in jwks["keys"] if key.get("kid") == kid), None)
    if key_data is None:
        jwks = await _get_jwks(cfg.jwt_jwks_url or "", force_refresh=True, **jwks_args)
        key_data = next((key for key in jwks["keys"] if key.get("kid") == kid), None)
    if key_data is None:
        raise HTTPException(status_code=401, detail="Unknown JWT signing key")
    if key_data.get("alg") and key_data.get("alg") != alg:
        raise HTTPException(status_code=401, detail="JWT algorithm does not match signing key metadata")
    if key_data.get("use") and key_data.get("use") != "sig":
        raise HTTPException(status_code=401, detail="JWT key is not designated for signatures")
    if key_data.get("key_ops") and "verify" not in key_data.get("key_ops", []):
        raise HTTPException(status_code=401, detail="JWT signing key is not permitted for verification")
    try:
        pyjwk = jwt.PyJWK.from_dict(key_data)
        kwargs: dict[str, Any] = {"algorithms": [alg], "options": {"require": [cfg.jwt_subject_claim, "exp"]}}
        if cfg.jwt_issuer:
            kwargs["issuer"] = cfg.jwt_issuer
        if cfg.jwt_audience is not None:
            kwargs["audience"] = cfg.jwt_audience
        else:
            kwargs["options"] = {**kwargs["options"], "verify_aud": False}
        return jwt.decode(token, pyjwk.key, **kwargs)
    except jwt.PyJWTError as exc:
        raise HTTPException(status_code=401, detail="Invalid bearer token") from exc


audit_table = Table(
    "_forge_v2_audit",
    _internal_meta,
    Column("id", Integer, primary_key=True, autoincrement=True),
    Column("created_at", DateTime(timezone=True), nullable=False, index=True),
    Column("project_slug", String(80), nullable=False, index=True),
    Column("request_id", String(64), nullable=False, index=True),
    Column("principal_kind", String(32), nullable=False),
    Column("principal_subject", String(160), nullable=False),
    Column("method", String(12), nullable=False),
    Column("path", String(512), nullable=False),
    Column("status_code", Integer, nullable=False),
    Column("duration_ms", Integer, nullable=False),
)
api_keys_table = Table(
    "_forge_v2_api_keys",
    _internal_meta,
    Column("id", Integer, primary_key=True, autoincrement=True),
    Column("project_slug", String(80), nullable=False, index=True),
    Column("name", String(120), nullable=False),
    Column("prefix", String(16), nullable=False, index=True),
    Column("key_hash", String(64), nullable=False, unique=True),
    Column("roles", Text, nullable=False, default=""),
    Column("permissions", Text, nullable=False, default=""),
    Column("tenant_id", String(96), nullable=True, index=True),
    Column("rate_requests", Integer, nullable=True),
    Column("rate_window_seconds", Integer, nullable=True),
    Column("rate_burst", Integer, nullable=True),
    Column("enabled", Boolean, nullable=False, default=True),
    Column("expires_at", DateTime(timezone=True), nullable=True),
    Column("created_at", DateTime(timezone=True), nullable=False),
)
bootstrap_state_table = Table(
    "_forge_v4_bootstrap_state",
    _internal_meta,
    Column("project_slug", String(80), primary_key=True),
    Column("consumed_at", DateTime(timezone=True), nullable=True),
)
media_usage_table = Table(
    "_forge_v4_media_usage",
    _internal_meta,
    Column("project_slug", String(80), primary_key=True),
    Column("owner_subject", String(160), primary_key=True),
    Column("used_bytes", Integer, nullable=False, default=0),
    Column("updated_at", DateTime(timezone=True), nullable=False),
)
media_table = Table(
    "_forge_v2_media",
    _internal_meta,
    Column("id", String(64), primary_key=True),
    Column("project_slug", String(80), nullable=False, index=True),
    Column("storage_key", String(512), nullable=False, unique=True),
    Column("original_name", String(255), nullable=False),
    Column("content_type", String(160), nullable=False),
    Column("size", Integer, nullable=False),
    Column("sha256", String(64), nullable=False, index=True),
    Column("owner_subject", String(160), nullable=False, index=True),
    Column("created_at", DateTime(timezone=True), nullable=False, index=True),
)


@dataclass
class Principal:
    kind: str
    subject: str
    roles: set[str]
    permissions: set[str]
    tenant_id: str | None = None
    key_id: int | None = None
    rate_requests: int | None = None
    rate_window_seconds: int | None = None
    rate_burst: int | None = None
    expires_at: datetime | None = None


def hash_key(raw: str) -> str:
    return hashlib.sha256(raw.encode()).hexdigest()


def make_api_key() -> str:
    return "jf2_" + secrets.token_urlsafe(36)


async def init_security(engine: AsyncEngine, *, mode: str = "create") -> None:
    if mode not in {"create", "validate"}:
        raise RuntimeError(f"Unsupported internal schema mode: {mode}")
    if mode == "create":
        async with engine.begin() as conn:
            await conn.run_sync(_internal_meta.create_all)
        return
    async with engine.connect() as conn:

        def missing(sync_conn):
            from sqlalchemy import inspect

            inspector = inspect(sync_conn)
            return sorted(name for name in _internal_meta.tables if not inspector.has_table(name))

        missing_tables = await conn.run_sync(missing)
    if missing_tables:
        raise RuntimeError("Forge internal support schema is missing tables: " + ", ".join(missing_tables) + ". Run `forge migrate`.")


async def bootstrap_is_available(engine: AsyncEngine, project_slug: str) -> bool:
    async with engine.connect() as conn:
        row = (
            await conn.execute(select(bootstrap_state_table.c.consumed_at).where(bootstrap_state_table.c.project_slug == project_slug))
        ).first()
    return not row or row[0] is None


async def consume_bootstrap(engine: AsyncEngine, project_slug: str) -> None:
    now = datetime.now(timezone.utc)
    async with engine.begin() as conn:
        row = (
            await conn.execute(select(bootstrap_state_table.c.project_slug).where(bootstrap_state_table.c.project_slug == project_slug))
        ).first()
        if row:
            await conn.execute(
                update(bootstrap_state_table).where(bootstrap_state_table.c.project_slug == project_slug).values(consumed_at=now)
            )
        else:
            await conn.execute(insert(bootstrap_state_table).values(project_slug=project_slug, consumed_at=now))


def _expand_role_permissions(project: ProjectConfig, roles: set[str]) -> set[str]:
    out = set()
    seen = set()

    def walk(role: str):
        if role in seen:
            return
        seen.add(role)
        spec = project.roles.get(role)
        if not spec:
            return
        out.update(spec.permissions)
        for parent in spec.inherits:
            walk(parent)

    for role in roles:
        walk(role)
    return out


def permission_matches(granted: str, required: str) -> bool:
    return granted == "*" or granted == required or (granted.endswith(".*") and required.startswith(granted[:-1]))


def has_permission(principal: Principal, required: str | None) -> bool:
    return True if not required else any(permission_matches(p, required) for p in principal.permissions)


_api_key_auth_cache: OrderedDict[tuple[str, str], tuple[float, dict[str, Any]]] = OrderedDict()


def clear_api_key_auth_cache(project_slug: str | None = None) -> None:
    if project_slug is None:
        _api_key_auth_cache.clear()
        return
    for key in [k for k in _api_key_auth_cache if k[0] == project_slug]:
        _api_key_auth_cache.pop(key, None)


def _cached_api_key_row(project: ProjectConfig, key_hash: str) -> dict[str, Any] | None:
    if project.security.api_key_cache_ttl_seconds <= 0:
        return None
    cache_key = (project.slug, key_hash)
    item = _api_key_auth_cache.get(cache_key)
    if item is None:
        return None
    expires, row = item
    if expires <= time.monotonic():
        _api_key_auth_cache.pop(cache_key, None)
        return None
    expires_at = row.get("expires_at")
    if expires_at:
        normalized = expires_at if expires_at.tzinfo else expires_at.replace(tzinfo=timezone.utc)
        if normalized <= datetime.now(timezone.utc):
            _api_key_auth_cache.pop(cache_key, None)
            return None
    _api_key_auth_cache.move_to_end(cache_key)
    return dict(row)


def _store_api_key_row(project: ProjectConfig, key_hash: str, row: dict[str, Any]) -> None:
    ttl = project.security.api_key_cache_ttl_seconds
    if ttl <= 0:
        return
    cache_key = (project.slug, key_hash)
    _api_key_auth_cache[cache_key] = (time.monotonic() + ttl, dict(row))
    _api_key_auth_cache.move_to_end(cache_key)
    while len(_api_key_auth_cache) > project.security.api_key_cache_max_entries:
        _api_key_auth_cache.popitem(last=False)


async def authenticate_request(request: Request, project: ProjectConfig, engine: AsyncEngine) -> Principal:
    raw_key = request.headers.get(project.security.api_key_header)
    if not raw_key and (
        project.security.allow_query_api_key
        or (getattr(request, "scope", {}).get("type") == "websocket" and project.security.allow_websocket_query_api_key)
    ):
        raw_key = request.query_params.get("api_key")
    if raw_key is not None and (not raw_key or len(raw_key) > 512):
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid API key")
    bootstrap = (project.security.bootstrap_admin_key or settings.bootstrap_admin_key) if project.security.bootstrap_enabled else None
    if raw_key and bootstrap and hmac.compare_digest(raw_key, bootstrap):
        if project.security.bootstrap_one_time and not await bootstrap_is_available(engine, project.slug):
            raise HTTPException(status_code=401, detail="Bootstrap credential has already been consumed")
        return Principal("bootstrap", f"bootstrap:{project.slug}", set(), {"admin.keys.create", "admin.credentials.delegate_any"})
    if raw_key:
        key_hash = hash_key(raw_key)
        row = _cached_api_key_row(project, key_hash)
        if row is None:
            async with engine.connect() as conn:
                loaded = (
                    (
                        await conn.execute(
                            select(api_keys_table).where(
                                (api_keys_table.c.key_hash == key_hash) & (api_keys_table.c.project_slug == project.slug)
                            )
                        )
                    )
                    .mappings()
                    .first()
                )
            row = dict(loaded) if loaded else None
            if row and row["enabled"]:
                expires_at = row.get("expires_at")
                normalized = expires_at if not expires_at or expires_at.tzinfo else expires_at.replace(tzinfo=timezone.utc)
                if not normalized or normalized > datetime.now(timezone.utc):
                    _store_api_key_row(project, key_hash, row)
        if not row or not row["enabled"]:
            raise HTTPException(status_code=401, detail="Invalid API key")
        expires_at = row["expires_at"]
        if expires_at:
            if expires_at.tzinfo is None:
                expires_at = expires_at.replace(tzinfo=timezone.utc)
            if expires_at < datetime.now(timezone.utc):
                raise HTTPException(status_code=401, detail="Expired API key")
        roles = {x for x in row["roles"].split(",") if x}
        direct = {x for x in row["permissions"].split(",") if x}
        return Principal(
            "api_key",
            f"api-key:{row['id']}:{row['name']}",
            roles,
            direct | _expand_role_permissions(project, roles),
            row["tenant_id"],
            row["id"],
            row["rate_requests"],
            row["rate_window_seconds"],
            row["rate_burst"],
            expires_at,
        )
    auth = request.headers.get("Authorization", "")
    if project.security.jwt_enabled and auth.lower().startswith("bearer "):
        token = auth.split(" ", 1)[1]
        if not token or len(token) > 8192:
            raise HTTPException(status_code=401, detail="Invalid bearer token")
        cfg = project.security
        if cfg.jwt_provider == "jwks":
            pool = getattr(request.scope.get("app", None).state, "jwks_clients", None) if request.scope.get("app") is not None else None
            payload = await _decode_jwks_token(token, project, client_pool=pool)
        else:
            try:
                secret = cfg.jwt_secret or settings.jwt_secret
                if not secret:
                    raise HTTPException(status_code=503, detail="Local JWT verification is not configured")
                payload = jwt.decode(token, secret, algorithms=["HS256"], options={"require": ["exp", cfg.jwt_subject_claim]})
            except jwt.PyJWTError as exc:
                raise HTTPException(status_code=401, detail="Invalid bearer token") from exc
        project_claim = _claim(payload, cfg.jwt_project_claim)
        if cfg.jwt_require_project_claim and project_claim != project.slug:
            raise HTTPException(status_code=401, detail="Bearer token is missing the required project claim")
        if project_claim not in (None, project.slug):
            raise HTTPException(status_code=401, detail="Token is not valid for this project")
        if cfg.jwt_provider == "jwks":
            roles = _claim_set(_claim(payload, cfg.jwt_roles_claim)) if cfg.jwt_trust_roles_claim else set()
            direct = _claim_set(_claim(payload, cfg.jwt_permissions_claim)) if cfg.jwt_trust_permissions_claim else set()
        else:
            roles = _claim_set(_claim(payload, cfg.jwt_roles_claim))
            direct = _claim_set(_claim(payload, cfg.jwt_permissions_claim))
        subject = _claim(payload, cfg.jwt_subject_claim)
        if subject is None:
            raise HTTPException(status_code=401, detail="Bearer token has no subject")
        tenant = _claim(payload, cfg.jwt_tenant_claim)
        if cfg.jwt_provider == "jwks" and not cfg.jwt_trust_tenant_claim:
            tenant = None
        return Principal(
            "jwt", str(subject), roles, direct | _expand_role_permissions(project, roles), str(tenant) if tenant is not None else None
        )
    return Principal("anonymous", "anonymous", set(), set())


async def _consume_bootstrap_in_transaction(conn, project_slug: str) -> None:
    now = datetime.now(timezone.utc)
    row = (
        await conn.execute(
            select(bootstrap_state_table.c.consumed_at).where(bootstrap_state_table.c.project_slug == project_slug).with_for_update()
        )
    ).first()
    if row:
        if row[0] is not None:
            raise HTTPException(status_code=401, detail="Bootstrap credential has already been consumed")
        await conn.execute(
            update(bootstrap_state_table)
            .where((bootstrap_state_table.c.project_slug == project_slug) & (bootstrap_state_table.c.consumed_at.is_(None)))
            .values(consumed_at=now)
        )
    else:
        await conn.execute(insert(bootstrap_state_table).values(project_slug=project_slug, consumed_at=now))


def _permission_is_delegable(parent: Principal, requested: str) -> bool:
    return any(permission_matches(granted, requested) for granted in parent.permissions)


def ensure_credential_delegation(
    project: ProjectConfig,
    parent: Principal,
    *,
    roles: list[str],
    permissions: list[str],
    tenant_id: str | None,
    expires_at: datetime | None = None,
    rate_requests: int | None = None,
    rate_window_seconds: int | None = None,
    rate_burst: int | None = None,
    subject: str | None = None,
) -> None:
    unknown_roles = sorted(set(roles) - set(project.roles))
    if unknown_roles:
        raise HTTPException(status_code=422, detail=f"Unknown roles: {', '.join(unknown_roles)}")
    if has_permission(parent, "admin.credentials.delegate_any"):
        return
    requested_roles = set(roles)
    if not requested_roles.issubset(parent.roles):
        raise HTTPException(status_code=403, detail="Cannot delegate roles not held by the caller")
    effective = set(permissions) | _expand_role_permissions(project, requested_roles)
    if any(not _permission_is_delegable(parent, p) for p in effective):
        raise HTTPException(status_code=403, detail="Cannot delegate permissions beyond the caller")
    if tenant_id != parent.tenant_id:
        raise HTTPException(status_code=403, detail="Cannot delegate a different tenant")
    if parent.expires_at:
        if expires_at is None:
            raise HTTPException(status_code=403, detail="Cannot delegate a non-expiring credential from an expiring caller")
        pexp = parent.expires_at if parent.expires_at.tzinfo else parent.expires_at.replace(tzinfo=timezone.utc)
        cexp = expires_at if expires_at.tzinfo else expires_at.replace(tzinfo=timezone.utc)
        if cexp > pexp:
            raise HTTPException(status_code=403, detail="Cannot delegate a credential that outlives the caller")
    pr = parent.rate_requests or project.rate_limit.requests
    pw = parent.rate_window_seconds or project.rate_limit.window_seconds
    pb = parent.rate_burst or project.rate_limit.burst or pr
    cr = rate_requests or project.rate_limit.requests
    cw = rate_window_seconds or project.rate_limit.window_seconds
    cb = rate_burst or project.rate_limit.burst or cr
    # Compare sustained request rates without floating-point rounding. A child
    # with 99 requests/second is more permissive than a parent with 100/minute,
    # even though the child's raw request count is smaller.
    if cr * pw > pr * cw:
        raise HTTPException(status_code=403, detail="Cannot delegate a higher sustained request rate")
    if cb > pb:
        raise HTTPException(status_code=403, detail="Cannot delegate a larger rate_burst budget")
    if subject is not None and subject != parent.subject and not has_permission(parent, "admin.credentials.impersonate"):
        raise HTTPException(status_code=403, detail="Cannot issue a credential for another subject")


async def create_api_key(
    engine: AsyncEngine,
    *,
    project_slug: str,
    name: str,
    roles: list[str],
    permissions: list[str],
    expires_at: datetime | None = None,
    tenant_id: str | None = None,
    rate_requests: int | None = None,
    rate_window_seconds: int | None = None,
    rate_burst: int | None = None,
    consume_bootstrap_once: bool = False,
) -> dict[str, Any]:
    raw = make_api_key()
    values = dict(
        project_slug=project_slug,
        name=name,
        prefix=raw[:12],
        key_hash=hash_key(raw),
        roles=",".join(sorted(set(roles))),
        permissions=",".join(sorted(set(permissions))),
        tenant_id=tenant_id,
        rate_requests=rate_requests,
        rate_window_seconds=rate_window_seconds,
        rate_burst=rate_burst,
        enabled=True,
        expires_at=expires_at,
        created_at=datetime.now(timezone.utc),
    )
    try:
        async with engine.begin() as conn:
            if consume_bootstrap_once:
                await _consume_bootstrap_in_transaction(conn, project_slug)
            result = await conn.execute(insert(api_keys_table).values(**values))
            key_id = result.inserted_primary_key[0]
    except IntegrityError as exc:
        if consume_bootstrap_once:
            raise HTTPException(status_code=401, detail="Bootstrap credential has already been consumed") from exc
        raise
    clear_api_key_auth_cache(project_slug)
    return {
        "id": key_id,
        "api_key": raw,
        "project": project_slug,
        "name": name,
        "roles": roles,
        "permissions": permissions,
        "tenant_id": tenant_id,
        "rate_requests": rate_requests,
        "rate_window_seconds": rate_window_seconds,
        "rate_burst": rate_burst,
        "expires_at": expires_at,
    }


def _api_key_metadata(row: Any) -> dict[str, Any]:
    return {
        "id": row["id"],
        "name": row["name"],
        "prefix": row["prefix"],
        "roles": [x for x in row["roles"].split(",") if x],
        "permissions": [x for x in row["permissions"].split(",") if x],
        "tenant_id": row["tenant_id"],
        "enabled": row["enabled"],
        "rate_requests": row["rate_requests"],
        "rate_window_seconds": row["rate_window_seconds"],
        "rate_burst": row["rate_burst"],
        "expires_at": row["expires_at"],
        "created_at": row["created_at"],
    }


async def get_api_key_metadata(engine, project_slug, key_id):
    async with engine.connect() as conn:
        row = (
            (
                await conn.execute(
                    select(api_keys_table).where((api_keys_table.c.id == key_id) & (api_keys_table.c.project_slug == project_slug))
                )
            )
            .mappings()
            .first()
        )
    return _api_key_metadata(row) if row else None


async def revoke_api_key(engine, project_slug, key_id):
    async with engine.begin() as conn:
        result = await conn.execute(
            update(api_keys_table)
            .where((api_keys_table.c.id == key_id) & (api_keys_table.c.project_slug == project_slug))
            .values(enabled=False)
        )
    if result.rowcount:
        clear_api_key_auth_cache(project_slug)
    return bool(result.rowcount)


async def list_api_keys(engine, project_slug):
    async with engine.connect() as conn:
        rows = (
            (
                await conn.execute(
                    select(api_keys_table).where(api_keys_table.c.project_slug == project_slug).order_by(api_keys_table.c.id.desc())
                )
            )
            .mappings()
            .all()
        )
    return [_api_key_metadata(row) for row in rows]


def issue_jwt(
    subject: str,
    project_slug: str,
    roles: list[str],
    permissions: list[str],
    exp_minutes: int,
    tenant_id: str | None = None,
    *,
    secret: str | None = None,
) -> str:
    signing_secret = secret or settings.jwt_secret
    if not signing_secret:
        raise RuntimeError("Local JWT signing secret is not configured")
    now = datetime.now(timezone.utc)
    payload = {
        "sub": subject,
        "project": project_slug,
        "roles": roles,
        "permissions": permissions,
        "iat": now,
        "exp": now + timedelta(minutes=exp_minutes),
    }
    if tenant_id is not None:
        payload["tenant_id"] = tenant_id
    return jwt.encode(payload, signing_secret, algorithm="HS256")
