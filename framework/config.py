from __future__ import annotations

import json
import os
import re
from pathlib import Path
from typing import Any, Literal
from urllib.parse import urlsplit

from dotenv import dotenv_values
from pydantic import BaseModel, ConfigDict, Field, ValidationError, model_validator

from .safe_regex import validate_safe_pattern, validate_schema_patterns
from .settings import settings

_ENV_PATTERN = re.compile(r"^\$env:([A-Z0-9_]+)(?::-(.*))?$")
_SLUG_PATTERN = re.compile(r"^[a-z0-9](?:[a-z0-9-]{0,62}[a-z0-9])?$")
_ROUTE_SEGMENT_PATTERN = re.compile(r"^[A-Za-z0-9._~{}:-]+$")
_HEADER_PATTERN = re.compile(r"^[!#$%&'*+.^_`|~0-9A-Za-z-]+$")
_RESERVED_APP_DIRECTORIES = frozenset({"config", "data", "hooks", "plugins", "schemas"})


def _contains_control(value: str) -> bool:
    return any(ord(character) < 0x20 or ord(character) == 0x7F for character in value)


def _validate_header_map(values: dict[str, str], *, label: str) -> None:
    if len(values) > 64:
        raise ValueError(f"{label} may contain at most 64 headers")
    for name, value in values.items():
        if not _HEADER_PATTERN.fullmatch(name) or not isinstance(value, str) or len(value) > 8192 or _contains_control(value):
            raise ValueError(f"{label} contains an invalid header name or value")


def _validate_http_url(value: str, *, allow_insecure_http: bool, label: str) -> None:
    if not value or len(value) > 4096 or "\\" in value or _contains_control(value) or any(character.isspace() for character in value):
        raise ValueError(f"{label} must be a bounded HTTP(S) URL without control characters")
    parsed = urlsplit(value)
    try:
        _ = parsed.port
    except ValueError as exc:
        raise ValueError(f"{label} contains an invalid port") from exc
    if parsed.scheme not in {"http", "https"} or not parsed.hostname or parsed.username or parsed.password or parsed.fragment:
        raise ValueError(f"{label} must be an absolute HTTP(S) URL without credentials or fragment")
    if parsed.scheme == "http" and not allow_insecure_http:
        raise ValueError(f"{label} requires HTTPS unless its explicit insecure-HTTP switch is enabled")


def _validate_origin(value: str, *, label: str) -> None:
    if value == "*":
        return
    _validate_http_url(value, allow_insecure_http=True, label=label)
    parsed = urlsplit(value)
    if parsed.path or parsed.query:
        raise ValueError(f"{label} must contain only scheme and authority, without path or query")


class ForgeModel(BaseModel):
    """Strict base model for the declarative configuration language.

    Unknown keys are rejected instead of being silently ignored. This catches
    misspellings in JSON before the application starts and makes generated JSON
    Schema substantially more useful in IDEs.
    """

    model_config = ConfigDict(extra="forbid", validate_assignment=True)


def _resolve_env(value: Any, dotenv: dict[str, str | None] | None = None) -> Any:
    if isinstance(value, str):
        match = _ENV_PATTERN.match(value)
        if match:
            name, default = match.groups()
            resolved = os.getenv(name)
            if resolved is None and dotenv is not None:
                resolved = dotenv.get(name)
            return resolved if resolved is not None else default
        return value
    if isinstance(value, list):
        return [_resolve_env(v, dotenv) for v in value]
    if isinstance(value, dict):
        return {k: _resolve_env(v, dotenv) for k, v in value.items()}
    return value


_APPEND_LIST_KEYS = {
    "resources",
    "mongo_resources",
    "operations",
    "data_sources",
    "dependencies",
    "custom_endpoints",
    "event_channels",
    "webhook_docs",
}


def _deep_merge(base: Any, extra: Any, *, key: str | None = None) -> Any:
    """Merge project fragments with security-aware list semantics.

    Declaration collections append across numbered fragments. Policy/configuration
    arrays (CORS origins, trusted hosts, IP allowlists, writable fields, etc.) are
    replaced by the later fragment. This makes it possible to intentionally clear
    or narrow a policy instead of accidentally retaining an earlier permissive list.
    """
    if isinstance(base, dict) and isinstance(extra, dict):
        out = dict(base)
        for child_key, value in extra.items():
            out[child_key] = _deep_merge(out[child_key], value, key=child_key) if child_key in out else value
        return out
    if isinstance(base, list) and isinstance(extra, list):
        return [*base, *extra] if key in _APPEND_LIST_KEYS else list(extra)
    return extra


class DatabaseConfig(ForgeModel):
    url: str
    echo: bool = False
    support_schema_mode: Literal["create", "validate"] = "create"
    pool_pre_ping: bool = True
    pool_size: int = Field(default=10, ge=1)
    max_overflow: int = Field(default=20, ge=0)
    pool_timeout: int = Field(default=30, ge=1)
    pool_recycle: int = Field(default=1800, ge=-1)
    isolation_level: str | None = None


class CacheConfig(ForgeModel):
    enabled: bool = True
    backend: Literal["memory", "redis", "tiered"] = "memory"
    default_ttl_seconds: int = Field(default=30, ge=1)
    stale_ttl_seconds: int = Field(default=0, ge=0)
    max_entries: int = Field(default=10_000, ge=100)
    key_prefix: str = "forge"
    cache_lists: bool = True
    cache_reads: bool = True
    fail_open: bool = True


class ResourceCacheConfig(ForgeModel):
    enabled: bool | None = None
    ttl_seconds: int | None = Field(default=None, ge=1)
    list_ttl_seconds: int | None = Field(default=None, ge=1)
    read_ttl_seconds: int | None = Field(default=None, ge=1)
    stale_ttl_seconds: int | None = Field(default=None, ge=0)


class RateLimitConfig(ForgeModel):
    enabled: bool = True
    pre_auth_enabled: bool = True
    pre_auth_requests: int = Field(default=1200, ge=1)
    pre_auth_window_seconds: int = Field(default=60, ge=1)
    pre_auth_burst: int | None = Field(default=300, ge=1)
    requests: int = Field(default=120, ge=1)
    window_seconds: int = Field(default=60, ge=1)
    backend: Literal["memory", "redis"] = "memory"
    burst: int | None = Field(default=None, ge=1)
    route_requests: int | None = Field(default=None, ge=1)
    route_window_seconds: int | None = Field(default=None, ge=1)
    route_burst: int | None = Field(default=None, ge=1)
    memory_max_buckets: int = Field(default=50_000, ge=100)
    memory_idle_ttl_seconds: int = Field(default=600, ge=10)
    memory_cleanup_interval_seconds: int = Field(default=30, ge=1)
    fail_open: bool = False


class ProtectionConfig(ForgeModel):
    max_request_body_bytes: int = Field(default=8 * 1024 * 1024, ge=1024)
    trusted_proxy_cidrs: list[str] = Field(default_factory=list)
    max_concurrent_requests: int = Field(default=1000, ge=1)
    request_timeout_seconds: float = Field(default=30.0, gt=0)
    trusted_hosts: list[str] = Field(default_factory=lambda: ["*"])
    gzip_minimum_size: int = Field(default=1024, ge=0)
    reject_when_saturated: bool = True
    max_queue_wait_seconds: float = Field(default=1.0, ge=0)


class SecurityConfig(ForgeModel):
    api_key_header: str = "X-API-Key"
    bootstrap_enabled: bool = False
    bootstrap_admin_key: str | None = None
    bootstrap_one_time: bool = True
    jwt_enabled: bool = False
    jwt_provider: Literal["local_hs256", "jwks"] = "local_hs256"
    jwt_secret: str | None = None
    jwt_exp_minutes: int = Field(default=60, ge=1)
    jwt_jwks_url: str | None = None
    jwt_issuer: str | None = None
    jwt_audience: str | list[str] | None = None
    jwt_algorithms: list[str] = Field(default_factory=lambda: ["RS256", "ES256", "EdDSA"])
    jwt_subject_claim: str = "sub"
    jwt_roles_claim: str = "roles"
    jwt_permissions_claim: str = "permissions"
    jwt_tenant_claim: str = "tenant_id"
    jwt_project_claim: str = "project"
    jwt_require_project_claim: bool = True
    jwt_trust_roles_claim: bool = False
    jwt_trust_permissions_claim: bool = False
    jwt_trust_tenant_claim: bool = False
    jwks_cache_ttl_seconds: int = Field(default=600, ge=30)
    jwks_timeout_seconds: float = Field(default=5.0, gt=0, le=30)
    jwks_refresh_interval_seconds: float = Field(default=5.0, ge=0.1, le=60)
    jwks_max_response_bytes: int = Field(default=1024 * 1024, ge=4096, le=16 * 1024 * 1024)
    jwks_allow_private_networks: bool = False
    jwks_allow_insecure_http: bool = False
    allow_query_api_key: bool = False
    allow_websocket_query_api_key: bool = False
    require_https: bool = False
    allowed_ips: list[str] = Field(default_factory=list)
    denied_ips: list[str] = Field(default_factory=list)
    idempotency_header: str = "Idempotency-Key"
    api_key_cache_ttl_seconds: float = Field(default=2.0, ge=0.0, le=60.0)
    api_key_cache_max_entries: int = Field(default=10_000, ge=100, le=1_000_000)

    @model_validator(mode="after")
    def validate_jwt_provider(self):
        if self.jwt_enabled and self.jwt_provider == "jwks" and not self.jwt_jwks_url:
            raise ValueError("security.jwt_jwks_url is required when jwt_provider='jwks'")
        if not self.bootstrap_enabled and self.bootstrap_admin_key:
            raise ValueError("security.bootstrap_admin_key must be empty when bootstrap_enabled=false")
        if self.jwt_provider == "jwks":
            allowed = {"RS256", "RS384", "RS512", "PS256", "PS384", "PS512", "ES256", "ES384", "ES512", "EdDSA"}
            invalid = sorted(set(self.jwt_algorithms) - allowed)
            if invalid:
                raise ValueError(f"JWKS verification only accepts asymmetric signing algorithms; invalid={invalid}")
            if self.jwt_jwks_url:
                _validate_http_url(
                    self.jwt_jwks_url,
                    allow_insecure_http=self.jwks_allow_insecure_http,
                    label="security.jwt_jwks_url",
                )
        for field_name in ("api_key_header", "idempotency_header"):
            value = getattr(self, field_name)
            if not _HEADER_PATTERN.fullmatch(value):
                raise ValueError(f"security.{field_name} must be a valid HTTP header name")
        return self


class RoleConfig(ForgeModel):
    permissions: list[str] = Field(default_factory=list)
    inherits: list[str] = Field(default_factory=list)


class ColumnConfig(ForgeModel):
    type: str = "string"
    nullable: bool = True
    primary_key: bool = False
    unique: bool = False
    index: bool = False
    default: Any = None
    max_length: int | None = Field(default=None, ge=1)


class ResourceConfig(ForgeModel):
    database: str = "primary"
    table: str
    path: str
    enabled: bool = True
    auto_create: bool = False
    columns: dict[str, ColumnConfig] = Field(default_factory=dict)
    primary_key: str = "id"
    allowed_actions: list[str] = Field(default_factory=lambda: ["list", "read", "create", "update", "delete"])
    permissions: dict[str, str] = Field(default_factory=dict)
    readable_fields: list[str] | None = None
    writable_fields: list[str] | None = None
    hidden_fields: list[str] = Field(default_factory=list)
    default_limit: int = Field(default=50, ge=1)
    max_limit: int = Field(default=200, ge=1)
    allowed_filters: list[str] = Field(default_factory=list)
    filter_operators: list[Literal["eq", "ne", "gt", "gte", "lt", "lte", "in", "like", "ilike", "isnull"]] = Field(
        default_factory=lambda: ["eq"]
    )
    search_fields: list[str] = Field(default_factory=list)
    allowed_sort: list[str] = Field(default_factory=list)
    pagination_mode: Literal["offset", "cursor"] = "offset"
    cursor_field: str | None = None
    soft_delete_field: str | None = None
    tenant_field: str | None = None
    owner_field: str | None = None
    owner_actions: list[Literal["list", "read", "update", "delete"]] = Field(default_factory=list)
    owner_bypass_permission: str | None = None
    cache: ResourceCacheConfig = Field(default_factory=ResourceCacheConfig)
    create_schema: dict[str, Any] | None = None
    update_schema: dict[str, Any] | None = None
    batch_enabled: bool = False
    max_batch_size: int = Field(default=100, ge=1, le=5000)
    count_enabled: bool = True
    dependencies: list[str] = Field(default_factory=list)

    @model_validator(mode="after")
    def validate_schemas(self):
        validate_schema_patterns(self.create_schema)
        validate_schema_patterns(self.update_schema)
        return self


class MongoDatabaseConfig(ForgeModel):
    uri: str
    database: str
    max_pool_size: int = Field(default=100, ge=1)
    min_pool_size: int = Field(default=0, ge=0)
    server_selection_timeout_ms: int = Field(default=5000, ge=100)


class MongoResourceConfig(ForgeModel):
    database: str = "primary"
    collection: str
    path: str
    enabled: bool = True
    allowed_actions: list[str] = Field(default_factory=lambda: ["list", "read", "create", "update", "delete"])
    permissions: dict[str, str] = Field(default_factory=dict)
    writable_fields: list[str] | None = None
    hidden_fields: list[str] = Field(default_factory=list)
    allowed_filters: list[str] = Field(default_factory=list)
    filter_operators: list[Literal["eq", "ne", "gt", "gte", "lt", "lte", "in"]] = Field(default_factory=lambda: ["eq"])
    allowed_sort: list[str] = Field(default_factory=list)
    default_limit: int = Field(default=50, ge=1)
    max_limit: int = Field(default=200, ge=1)
    tenant_field: str | None = None
    owner_field: str | None = None
    owner_actions: list[Literal["list", "read", "update", "delete"]] = Field(default_factory=list)
    owner_bypass_permission: str | None = None
    soft_delete_field: str | None = None
    cache: ResourceCacheConfig = Field(default_factory=ResourceCacheConfig)
    create_schema: dict[str, Any] | None = None
    update_schema: dict[str, Any] | None = None
    dependencies: list[str] = Field(default_factory=list)

    @model_validator(mode="after")
    def validate_schemas(self):
        validate_schema_patterns(self.create_schema)
        validate_schema_patterns(self.update_schema)
        return self


class DependencySpec(ForgeModel):
    name: str
    callable: str
    use_cache: bool = True


class RequestParameterSpec(ForgeModel):
    name: str
    location: Literal["query", "header", "cookie", "path"] = "query"
    type: Literal["string", "integer", "number", "boolean"] = "string"
    required: bool = False
    default: Any = None
    description: str | None = None
    enum: list[Any] | None = None
    minimum: float | None = None
    maximum: float | None = None
    min_length: int | None = Field(default=None, ge=0)
    max_length: int | None = Field(default=None, ge=0)
    pattern: str | None = None

    @model_validator(mode="after")
    def validate_parameter(self):
        if not self.name or len(self.name) > 128 or _contains_control(self.name):
            raise ValueError("request parameter names must contain 1-128 characters without controls")
        if self.location == "header" and not _HEADER_PATTERN.fullmatch(self.name):
            raise ValueError("header parameter name must be a valid HTTP header name")
        if self.pattern:
            if self.type != "string":
                raise ValueError("pattern is only supported for string request parameters")
            validate_safe_pattern(self.pattern)
            if self.max_length is None:
                object.__setattr__(self, "max_length", 16_384)
        if self.max_length is not None and self.max_length > 16_384:
            raise ValueError("request parameter max_length may not exceed 16384")
        return self


class ResponseSpec(ForgeModel):
    kind: Literal["json", "text", "html", "redirect", "stream", "file", "empty"] = "json"
    media_type: str | None = None
    headers: dict[str, str] = Field(default_factory=dict)
    filename: str | None = None
    status_code: int | None = None

    @model_validator(mode="after")
    def validate_response_metadata(self):
        _validate_header_map(self.headers, label="response.headers")
        for label, value in (("response.media_type", self.media_type), ("response.filename", self.filename)):
            if value is not None and (not value or len(value) > 1024 or _contains_control(value)):
                raise ValueError(f"{label} must be bounded and contain no control characters")
        if self.status_code is not None and not 100 <= self.status_code <= 599:
            raise ValueError("response.status_code must be between 100 and 599")
        return self


class CustomEndpointConfig(ForgeModel):
    path: str
    method: str = "GET"
    public: bool = False
    permission: str | None = None
    handler: str
    summary: str | None = None
    description: str | None = None
    tags: list[str] = Field(default_factory=list)
    deprecated: bool = False
    include_in_schema: bool = True
    input_schema: dict[str, Any] | None = None
    input_mode: Literal["json", "form", "text", "bytes", "none"] = "json"
    parameters: list[RequestParameterSpec] = Field(default_factory=list)
    dependencies: list[str] = Field(default_factory=list)
    background_hooks: list[str] = Field(default_factory=list)
    response: ResponseSpec = Field(default_factory=ResponseSpec)
    openapi_extra: dict[str, Any] | None = None

    @model_validator(mode="after")
    def secure_by_default(self):
        if not self.public and not self.permission:
            raise ValueError("custom endpoint is private by default; set permission or explicit public=true")
        if self.public and self.permission:
            raise ValueError("custom endpoint cannot set both public=true and permission")
        validate_schema_patterns(self.input_schema)
        return self


class SQLStatementConfig(ForgeModel):
    sql: str
    mode: Literal["execute", "fetch_one", "fetch_all", "scalar"] = "execute"
    params: dict[str, Any] = Field(default_factory=dict)
    result_name: str | None = None
    max_rows: int = Field(default=1000, ge=1, le=100_000)
    require_rowcount_min: int | None = Field(default=None, ge=0)
    require_rowcount_max: int | None = Field(default=None, ge=0)


class OperationCacheConfig(ForgeModel):
    enabled: bool = False
    ttl_seconds: int = Field(default=15, ge=1)
    stale_ttl_seconds: int | None = Field(default=None, ge=0)
    vary_by_principal: bool = True


class OperationConfig(ForgeModel):
    name: str
    path: str | None = None
    method: Literal["GET", "POST", "PUT", "PATCH", "DELETE"] = "POST"
    database: str = "primary"
    public: bool = False
    permission: str | None = None
    transaction: bool = True
    input_schema: dict[str, Any] | None = None
    parameters: list[RequestParameterSpec] = Field(default_factory=list)
    statements: list[SQLStatementConfig] = Field(default_factory=list)
    allow_ddl: bool = False
    idempotency: bool = False
    idempotency_ttl_seconds: int = Field(default=86400, ge=60, le=31_536_000)
    idempotency_cleanup_batch_size: int = Field(default=1000, ge=10, le=50_000)
    idempotency_max_response_bytes: int = Field(default=262_144, ge=1024, le=16_777_216)
    cache: OperationCacheConfig = Field(default_factory=OperationCacheConfig)
    invalidate_resources: list[str] = Field(default_factory=list)
    invalidate_operations: list[str] = Field(default_factory=list)
    summary: str | None = None
    description: str | None = None
    tags: list[str] = Field(default_factory=list)
    deprecated: bool = False
    background_hooks: list[str] = Field(default_factory=list)
    dependencies: list[str] = Field(default_factory=list)

    @model_validator(mode="after")
    def finalize_path(self):
        if self.path is None:
            self.path = f"rpc/{self.name}"
        if not self.statements:
            raise ValueError("operation requires at least one SQL statement")
        if not self.public and not self.permission:
            raise ValueError("operation is private by default; set permission or explicit public=true")
        if self.public and self.permission:
            raise ValueError("operation cannot set both public=true and permission")
        if self.idempotency and not self.transaction:
            raise ValueError("idempotent operations require transaction=true for atomic side effects")
        if self.idempotency and self.cache.enabled:
            raise ValueError("idempotent operations cannot enable response cache; replay is handled by the idempotency ledger")
        if self.cache.enabled and self.method != "GET":
            raise ValueError("operation response cache is only supported for GET operations")
        if self.cache.enabled and any(statement.mode == "execute" for statement in self.statements):
            raise ValueError("operation response cache is read-only and cannot be used with execute statements")
        if not self.transaction and any(statement.mode == "execute" for statement in self.statements):
            raise ValueError("transaction=false is read-only; execute statements require transaction=true")
        if not self.transaction:
            for statement in self.statements:
                verb = statement.sql.lstrip().split(None, 1)[0].upper() if statement.sql.strip() else ""
                if verb not in {"SELECT", "SHOW", "EXPLAIN"}:
                    raise ValueError("transaction=false only accepts explicit SELECT/SHOW/EXPLAIN statements")
        refs = " ".join(str(v) for st in self.statements for v in st.params.values())
        if self.idempotency and "$request.id" in refs:
            raise ValueError("idempotent operation cannot bind $request.id because retries receive a different request ID")
        if self.cache.enabled and not self.cache.vary_by_principal and "$principal." in refs:
            raise ValueError("cached operation references principal context but cache.vary_by_principal=false")
        if self.cache.enabled and "$request.id" in refs:
            raise ValueError("cached operation cannot depend on $request.id")
        validate_schema_patterns(self.input_schema)
        return self


class DataSourceConfig(ForgeModel):
    name: str
    enabled: bool = True
    path: str | None = None
    type: Literal["json_file", "yaml_file", "csv_file", "static", "http"]
    public: bool = False
    public_write: bool = False
    permission: str | None = None
    read_permission: str | None = None
    write_permission: str | None = None
    parameters: list[RequestParameterSpec] = Field(default_factory=list)
    file: str | None = None
    data: Any = None
    url: str | None = None
    method: Literal["GET", "POST", "PUT", "PATCH", "DELETE"] = "GET"
    headers: dict[str, str] = Field(default_factory=dict)
    timeout_seconds: float = Field(default=10.0, gt=0)
    total_timeout_seconds: float | None = Field(default=None, gt=0)
    max_response_bytes: int = Field(default=2 * 1024 * 1024, ge=1024, le=100 * 1024 * 1024)
    allow_insecure_http: bool = False
    allow_private_networks: bool = False
    retries: int = Field(default=2, ge=0, le=10)
    retry_non_idempotent: bool = False
    writable: bool = False
    file_lock_timeout_seconds: float = Field(default=5.0, gt=0, le=60)
    id_field: str = "id"
    max_items: int = Field(default=10_000, ge=1)
    allowed_filters: list[str] = Field(default_factory=list)
    allowed_sort: list[str] = Field(default_factory=list)
    cache_ttl_seconds: int = Field(default=15, ge=0)
    stale_ttl_seconds: int | None = Field(default=None, ge=0)
    forward_query: bool = True
    forward_body: bool = True
    dependencies: list[str] = Field(default_factory=list)

    @model_validator(mode="after")
    def finalize(self):
        if self.path is None:
            self.path = f"data/{self.name}"
        if self.type in {"json_file", "yaml_file", "csv_file"} and not self.file:
            raise ValueError(f"{self.type} data source requires file")
        if self.type == "http" and not self.url:
            raise ValueError("http data source requires url")
        if self.type == "http" and self.url:
            _validate_http_url(self.url, allow_insecure_http=self.allow_insecure_http, label="data source url")
            _validate_header_map(self.headers, label="data source headers")
        if not self.public and not (self.permission or self.read_permission):
            raise ValueError("data source is private by default; set permission/read_permission or explicit public=true")
        if self.public and (self.permission or self.read_permission):
            raise ValueError("public data source must not also define read permission")
        if self.public_write and not self.writable:
            raise ValueError("public_write=true requires writable=true")
        if self.public_write and (self.write_permission or self.permission):
            raise ValueError("public writable data source must not also define write_permission/permission")
        if self.writable and not self.public_write and not (self.write_permission or self.permission):
            raise ValueError(
                "writable data source mutations are private by default; set write_permission/permission or explicit public_write=true"
            )
        return self


class EventChannelConfig(ForgeModel):
    name: str
    path: str | None = None
    public_publish: bool = False
    public_subscribe: bool = False
    publish_permission: str | None = None
    subscribe_permission: str | None = None
    websocket_enabled: bool = True
    sse_enabled: bool = True
    max_message_bytes: int = Field(default=64 * 1024, ge=128, le=1024 * 1024)
    queue_size: int = Field(default=256, ge=1, le=10000)
    max_websocket_connections: int = Field(default=1000, ge=1, le=100_000)
    max_sse_connections: int = Field(default=1000, ge=1, le=100_000)
    allowed_origins: list[str] = Field(default_factory=list)
    heartbeat_seconds: int = Field(default=15, ge=1)
    websocket_message_requests: int | None = Field(default=None, ge=1)
    websocket_message_window_seconds: int | None = Field(default=None, ge=1)
    websocket_message_burst: int | None = Field(default=None, ge=1)

    @model_validator(mode="after")
    def finalize(self):
        if self.path is None:
            self.path = f"events/{self.name}"
        if not self.public_publish and not self.publish_permission:
            raise ValueError("event publishing is private by default; set publish_permission or public_publish=true")
        if not self.public_subscribe and not self.subscribe_permission:
            raise ValueError("event subscription is private by default; set subscribe_permission or public_subscribe=true")
        if self.public_publish and self.publish_permission:
            raise ValueError("event channel cannot set both public_publish=true and publish_permission")
        if self.public_subscribe and self.subscribe_permission:
            raise ValueError("event channel cannot set both public_subscribe=true and subscribe_permission")
        for origin in self.allowed_origins:
            _validate_origin(origin, label="event channel allowed origin")
        return self


class WebhookDocConfig(ForgeModel):
    name: str
    method: Literal["POST", "PUT", "PATCH"] = "POST"
    summary: str | None = None
    description: str | None = None
    payload_schema: dict[str, Any] | None = None

    @model_validator(mode="after")
    def validate_payload_schema(self):
        validate_schema_patterns(self.payload_schema)
        return self


class ObservabilityConfig(ForgeModel):
    metrics_enabled: bool = True
    metrics_path: str = "/metrics"


class RealtimeConfig(ForgeModel):
    backend: Literal["memory", "redis"] = "memory"
    redis_prefix: str = "forge:events"


class MediaConfig(ForgeModel):
    enabled: bool = False
    backend: Literal["local"] = "local"
    local_directory: str = "./data/media"
    max_upload_bytes: int = Field(default=25 * 1024 * 1024, ge=1, le=1024 * 1024 * 1024)
    max_batch_files: int = Field(default=8, ge=1, le=100)
    max_owner_bytes: int | None = Field(default=None, ge=1)
    allowed_mime_types: list[str] = Field(
        default_factory=lambda: [
            "image/jpeg",
            "image/png",
            "image/webp",
            "image/gif",
            "video/mp4",
            "audio/mpeg",
            "audio/ogg",
            "application/pdf",
        ]
    )
    allowed_extensions: list[str] = Field(default_factory=list)
    public: bool = False
    upload_permission: str = "media.upload"
    read_permission: str = "media.read"
    delete_permission: str = "media.delete"
    admin_permission: str = "media.admin"
    owner_delete_only: bool = False
    deduplicate: bool = True
    deduplicate_scope: Literal["owner", "project"] = "owner"
    signed_urls_enabled: bool = False
    signing_secret: str | None = None
    signed_url_ttl_seconds: int = Field(default=300, ge=1, le=86400)
    post_upload_hooks: list[str] = Field(default_factory=list)

    @model_validator(mode="after")
    def validate_signing(self):
        if self.enabled and self.signed_urls_enabled and not self.signing_secret:
            raise ValueError("media.signing_secret is required when signed_urls_enabled=true")
        return self


class MessagingPackConfig(ForgeModel):
    enabled: bool = False
    table_prefix: str = "msg_"
    database: str = "primary"
    tenant_field: str | None = None


class SocialPackConfig(ForgeModel):
    enabled: bool = False
    table_prefix: str = "social_"
    database: str = "primary"
    tenant_field: str | None = None


class GamingPackConfig(ForgeModel):
    enabled: bool = False
    table_prefix: str = "game_"
    database: str = "primary"
    tenant_field: str | None = None


class FeaturePacksConfig(ForgeModel):
    messaging: MessagingPackConfig = Field(default_factory=MessagingPackConfig)
    social: SocialPackConfig = Field(default_factory=SocialPackConfig)
    gaming: GamingPackConfig = Field(default_factory=GamingPackConfig)


class ProjectConfig(ForgeModel):
    slug: str
    name: str
    version: str = "0.5.3"
    enabled: bool = True
    api_prefix: str | None = None
    docs_enabled: bool = True
    audit_enabled: bool = True
    cors_origins: list[str] = Field(default_factory=lambda: ["*"])
    cors_methods: list[str] = Field(default_factory=lambda: ["GET", "POST", "PUT", "PATCH", "DELETE", "OPTIONS"])
    cors_headers: list[str] = Field(
        default_factory=lambda: ["Authorization", "Content-Type", "X-API-Key", "Idempotency-Key", "X-Request-ID"]
    )
    cors_expose_headers: list[str] = Field(default_factory=lambda: ["X-Request-ID", "X-Forge-Cache"])
    cors_allow_credentials: bool = False
    cors_max_age_seconds: int = Field(default=600, ge=0, le=86400)
    databases: dict[str, DatabaseConfig]
    mongo_databases: dict[str, MongoDatabaseConfig] = Field(default_factory=dict)
    security: SecurityConfig = Field(default_factory=SecurityConfig)
    cache: CacheConfig = Field(default_factory=CacheConfig)
    rate_limit: RateLimitConfig = Field(default_factory=RateLimitConfig)
    protection: ProtectionConfig = Field(default_factory=ProtectionConfig)
    observability: ObservabilityConfig = Field(default_factory=ObservabilityConfig)
    realtime: RealtimeConfig = Field(default_factory=RealtimeConfig)
    roles: dict[str, RoleConfig] = Field(default_factory=dict)
    resources: list[ResourceConfig] = Field(default_factory=list)
    mongo_resources: list[MongoResourceConfig] = Field(default_factory=list)
    operations: list[OperationConfig] = Field(default_factory=list)
    data_sources: list[DataSourceConfig] = Field(default_factory=list)
    dependencies: list[DependencySpec] = Field(default_factory=list)
    custom_endpoints: list[CustomEndpointConfig] = Field(default_factory=list)
    event_channels: list[EventChannelConfig] = Field(default_factory=list)
    webhook_docs: list[WebhookDocConfig] = Field(default_factory=list)
    media: MediaConfig = Field(default_factory=MediaConfig)
    features: FeaturePacksConfig = Field(default_factory=FeaturePacksConfig)
    project_dir: str = Field(default="", exclude=True)

    @model_validator(mode="after")
    def finalize_prefix(self):
        if not _SLUG_PATTERN.fullmatch(self.slug):
            raise ValueError("slug must be 1-64 lowercase letters, digits or internal hyphens")
        if not self.name.strip() or len(self.name) > 120 or any(ch in self.name for ch in "\r\n\0"):
            raise ValueError("name must be non-empty, at most 120 characters and contain no control line breaks")
        prefix = self.api_prefix or f"/api/{self.slug}/v1"
        if not prefix.startswith("/"):
            prefix = "/" + prefix
        object.__setattr__(self, "api_prefix", prefix.rstrip("/") or "/")
        if self.api_prefix == "/" or any(
            self.api_prefix == reserved or self.api_prefix.startswith(reserved + "/") for reserved in ("/__forge", "/health", "/ready")
        ):
            raise ValueError("api_prefix overlaps a reserved Forge service namespace")
        if "\\" in self.api_prefix or "?" in self.api_prefix or "#" in self.api_prefix or ".." in self.api_prefix:
            raise ValueError("api_prefix must be a normalized URL path without traversal, query or fragment syntax")
        if any(not _ROUTE_SEGMENT_PATTERN.fullmatch(segment) for segment in self.api_prefix.split("/") if segment):
            raise ValueError("api_prefix contains an invalid URL path segment")
        if self.cors_allow_credentials and "*" in self.cors_origins:
            raise ValueError("cors_allow_credentials=true cannot be combined with wildcard origin")
        for origin in self.cors_origins:
            _validate_origin(origin, label="CORS origin")
        if len(self.cors_methods) > 32 or any(
            method != "*" and (not method or len(method) > 32 or not _HEADER_PATTERN.fullmatch(method)) for method in self.cors_methods
        ):
            raise ValueError("cors_methods must contain at most 32 valid HTTP method tokens")
        for label, values in (("cors_headers", self.cors_headers), ("cors_expose_headers", self.cors_expose_headers)):
            if len(values) > 64 or any(value != "*" and not _HEADER_PATTERN.fullmatch(value) for value in values):
                raise ValueError(f"{label} must contain at most 64 valid HTTP header names")
        return self


class ForgeConfig(ForgeModel):
    name: str = "JSON API Forge"
    version: str = "0.5.3"
    projects: list[ProjectConfig]


def _load_json(path: Path) -> dict[str, Any]:
    try:
        if path.is_symlink() or not path.is_file():
            raise RuntimeError("Configuration must be a regular non-link JSON file")
        with path.open("rb") as stream:
            data = stream.read(2 * 1024 * 1024 + 1)
        if len(data) > 2 * 1024 * 1024:
            raise RuntimeError("Configuration JSON exceeds 2 MiB")
        value = json.loads(data.decode("utf-8"))
    except json.JSONDecodeError as exc:
        raise RuntimeError(f"Invalid JSON in {path}: {exc}") from exc
    if not isinstance(value, dict):
        raise RuntimeError(f"Invalid JSON in {path}: the document root must be an object")
    return value


def _load_project_dir(project_dir: Path, *, dotenv: dict[str, str | None] | None = None) -> ProjectConfig:
    if project_dir.is_symlink():
        raise RuntimeError("App directories may not be symbolic links")
    manifest = project_dir / "app.json"
    if not manifest.exists():
        manifest = project_dir / "manifest.json"
    if not manifest.exists():
        raise RuntimeError(f"Project {project_dir.name!r} has no app.json or manifest.json")

    raw: dict[str, Any] = _load_json(manifest)
    config_dir = project_dir / "config"
    if config_dir.is_symlink():
        raise RuntimeError("Configuration directories may not be symbolic links")
    if config_dir.exists():
        fragments = sorted(config_dir.glob("*.json"))
        if len(fragments) > 256:
            raise RuntimeError("An app may have at most 256 JSON configuration fragments")
        for fragment in fragments:
            raw = _deep_merge(raw, _load_json(fragment))

    raw.pop("$schema", None)
    raw.setdefault("slug", project_dir.name.lower())
    raw.setdefault("name", project_dir.name)
    raw["project_dir"] = str(project_dir.resolve())
    raw = _resolve_env(raw, dotenv)
    try:
        return ProjectConfig.model_validate(raw)
    except ValidationError as exc:
        errors = []
        for error in exc.errors(include_input=False, include_context=False, include_url=False):
            location = ".".join(str(part) for part in error.get("loc", ())) or "<root>"
            errors.append(f"{location}: {error.get('msg', 'invalid value')}")
        raise RuntimeError(f"Invalid project configuration in {project_dir}: " + "; ".join(errors)) from exc


def load_config_resilient(apps_dir: Path) -> tuple[ForgeConfig, dict[str, str]]:
    """Load independent apps; CLI validation remains strict and diagnostics omit secrets."""
    apps_dir = Path(apps_dir)
    if not apps_dir.is_dir():
        raise RuntimeError(f"Apps directory does not exist: {apps_dir}")
    projects: list[ProjectConfig] = []
    issues: dict[str, str] = {}
    root_dotenv = dotenv_values(apps_dir.resolve().parent / ".env")
    for directory in sorted(apps_dir.iterdir()):
        if not directory.is_dir() or directory.name.startswith(("_", ".")) or directory.name.casefold() in _RESERVED_APP_DIRECTORIES:
            continue
        if not any((directory / name).exists() for name in ("app.json", "manifest.json")):
            continue
        try:
            project = _load_project_dir(directory, dotenv=root_dotenv)
            if not project.enabled:
                continue
            projects.append(project)
        except Exception as exc:
            issues[directory.name] = type(exc).__name__
    # Quarantine every participant in a collision. Directory sort order must
    # never decide who owns an authentication boundary.
    conflicts: set[str] = set()
    for index, project in enumerate(projects):
        prefix = project.api_prefix.rstrip("/")
        for other in projects[index + 1 :]:
            target = other.api_prefix.rstrip("/")
            if project.slug == other.slug or prefix == target or prefix.startswith(target + "/") or target.startswith(prefix + "/"):
                conflicts.update((project.project_dir, other.project_dir))
    for directory in conflicts:
        issues[Path(directory).name] = "RouteOwnershipConflict"
    projects = [project for project in projects if project.project_dir not in conflicts]
    return ForgeConfig(projects=projects), issues


def load_config(apps_dir: Path | None = None) -> ForgeConfig:
    apps_dir = Path(apps_dir or settings.apps_dir)
    if not apps_dir.exists():
        raise RuntimeError(f"Apps directory does not exist: {apps_dir}")
    projects: list[ProjectConfig] = []
    root_dotenv = dotenv_values(apps_dir.resolve().parent / ".env")
    for project_dir in sorted(
        p
        for p in apps_dir.iterdir()
        if p.is_dir() and not p.name.startswith(("_", ".")) and p.name.casefold() not in _RESERVED_APP_DIRECTORIES
    ):
        manifest = project_dir / "app.json"
        fallback = project_dir / "manifest.json"
        if manifest.exists() or fallback.exists():
            project = _load_project_dir(project_dir, dotenv=root_dotenv)
            if project.enabled:
                projects.append(project)
    if not projects:
        raise RuntimeError(f"No enabled projects found below {apps_dir}")
    slugs = [p.slug for p in projects]
    if len(slugs) != len(set(slugs)):
        raise RuntimeError("Project slugs must be unique")
    prefixes = [p.api_prefix for p in projects]
    if len(prefixes) != len(set(prefixes)):
        raise RuntimeError("Project api_prefix values must be unique")
    normalized = sorted((p.api_prefix.rstrip("/"), p.slug) for p in projects)
    for index, (prefix, slug) in enumerate(normalized):
        for other, other_slug in normalized[index + 1 :]:
            if other.startswith(prefix + "/") or prefix.startswith(other + "/"):
                raise RuntimeError(f"Project api_prefix values may not overlap: {slug}={prefix!r}, {other_slug}={other!r}")
    return ForgeConfig(projects=projects)
