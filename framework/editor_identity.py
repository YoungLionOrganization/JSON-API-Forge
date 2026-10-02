from __future__ import annotations

import asyncio
import fnmatch
import hashlib
import hmac
import json
import re
import secrets
import uuid
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Any

from fastapi import HTTPException
from sqlalchemy import (
    Boolean,
    Column,
    DateTime,
    Integer,
    MetaData,
    String,
    Table,
    Text,
    and_,
    delete,
    insert,
    or_,
    select,
    update,
)
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncEngine

from .settings import Settings

_meta = MetaData()
_USERNAME = re.compile(r"^[a-zA-Z0-9](?:[a-zA-Z0-9._-]{1,30}[a-zA-Z0-9])?$")
_ROLE_NAME = re.compile(r"^[A-Za-z0-9](?:[A-Za-z0-9 _.-]{0,62}[A-Za-z0-9])?$")
_PROJECT_SCOPE = re.compile(r"^(?:\*|[A-Za-z0-9](?:[A-Za-z0-9 ._-]{0,62}[A-Za-z0-9])?)$")
_SAFE_PATTERN = re.compile(r"^[A-Za-z0-9*?_.:/ -]{1,160}$")
_INVITATION_TOKEN = re.compile(r"^jfi_[A-Za-z0-9_-]{40,80}$")
_CALL_TICKET = re.compile(r"^jfc_[A-Za-z0-9_-]{40,80}$")
_SESSION_TOKEN = re.compile(r"^jfe_session_[A-Za-z0-9_-]{40,100}$")

EDITOR_PERMISSIONS = frozenset(
    {
        "projects.read",
        "projects.create",
        "projects.validate",
        "documents.read",
        "documents.write",
        "documents.hooks.write",
        "documents.graphs.write",
        "databases.metadata.read",
        "databases.rows.read",
        "databases.undeclared.read",
        "profiles.read",
        "profiles.write.own",
        "members.read",
        "members.manage",
        "roles.read",
        "roles.manage",
        "invitations.manage",
        "areas.read",
        "areas.manage",
        "messages.read",
        "messages.write",
        "notes.read",
        "notes.write",
        "attachments.read",
        "attachments.write",
        "calls.join",
        "calls.start",
        "audit.read",
    }
)

editor_state = Table(
    "_forge_editor_state",
    _meta,
    Column("id", Integer, primary_key=True),
    Column("founder_user_id", String(36), nullable=True),
    Column("created_at", DateTime(timezone=True), nullable=False),
)
editor_users = Table(
    "_forge_editor_users",
    _meta,
    Column("id", String(36), primary_key=True),
    Column("username", String(32), nullable=False),
    Column("username_key", String(32), nullable=False, unique=True, index=True),
    Column("password_hash", Text, nullable=False),
    Column("display_name", String(80), nullable=False),
    Column("title", String(120), nullable=False, default=""),
    Column("bio", Text, nullable=False, default=""),
    Column("timezone", String(64), nullable=False, default="timezone.utc"),
    Column("status", String(160), nullable=False, default=""),
    Column("active", Boolean, nullable=False, default=True),
    Column("is_founder", Boolean, nullable=False, default=False),
    Column("created_at", DateTime(timezone=True), nullable=False),
    Column("updated_at", DateTime(timezone=True), nullable=False),
)
editor_roles = Table(
    "_forge_editor_roles",
    _meta,
    Column("id", String(36), primary_key=True),
    Column("name", String(64), nullable=False),
    Column("name_key", String(64), nullable=False, unique=True, index=True),
    Column("rank", Integer, nullable=False),
    Column("permissions", Text, nullable=False),
    Column("document_allow", Text, nullable=False),
    Column("document_deny", Text, nullable=False),
    Column("database_allow", Text, nullable=False),
    Column("system", Boolean, nullable=False, default=False),
    Column("created_at", DateTime(timezone=True), nullable=False),
    Column("updated_at", DateTime(timezone=True), nullable=False),
)
editor_memberships = Table(
    "_forge_editor_memberships",
    _meta,
    Column("id", String(36), primary_key=True),
    Column("user_id", String(36), nullable=False, index=True),
    Column("role_id", String(36), nullable=False, index=True),
    Column("project", String(64), nullable=False, index=True),
    Column("created_at", DateTime(timezone=True), nullable=False),
)
editor_sessions = Table(
    "_forge_editor_sessions",
    _meta,
    Column("token_hash", String(64), primary_key=True),
    Column("user_id", String(36), nullable=False, index=True),
    Column("user_agent_hash", String(64), nullable=False),
    Column("created_at", DateTime(timezone=True), nullable=False),
    Column("last_seen_at", DateTime(timezone=True), nullable=False),
    Column("expires_at", DateTime(timezone=True), nullable=False, index=True),
    Column("idle_expires_at", DateTime(timezone=True), nullable=False, index=True),
    Column("revoked", Boolean, nullable=False, default=False),
)
editor_login_attempts = Table(
    "_forge_editor_login_attempts",
    _meta,
    Column("attempt_key", String(64), primary_key=True),
    Column("failures", Integer, nullable=False),
    Column("window_started_at", DateTime(timezone=True), nullable=False),
    Column("locked_until", DateTime(timezone=True), nullable=True),
)
editor_invitations = Table(
    "_forge_editor_invitations",
    _meta,
    Column("token_hash", String(64), primary_key=True),
    Column("memberships", Text, nullable=False),
    Column("created_by", String(36), nullable=False),
    Column("created_at", DateTime(timezone=True), nullable=False),
    Column("expires_at", DateTime(timezone=True), nullable=False, index=True),
    Column("used_at", DateTime(timezone=True), nullable=True),
)
editor_areas = Table(
    "_forge_editor_areas",
    _meta,
    Column("id", String(36), primary_key=True),
    Column("project", String(64), nullable=False, index=True),
    Column("name", String(96), nullable=False),
    Column("description", String(500), nullable=False, default=""),
    Column("visibility", String(16), nullable=False),
    Column("minimum_rank", Integer, nullable=False, default=0),
    Column("allowed_role_ids", Text, nullable=False),
    Column("created_by", String(36), nullable=False),
    Column("created_at", DateTime(timezone=True), nullable=False),
)
editor_messages = Table(
    "_forge_editor_messages",
    _meta,
    Column("id", String(36), primary_key=True),
    Column("area_id", String(36), nullable=False, index=True),
    Column("author_id", String(36), nullable=False, index=True),
    Column("kind", String(16), nullable=False),
    Column("body", Text, nullable=False),
    Column("created_at", DateTime(timezone=True), nullable=False, index=True),
    Column("edited_at", DateTime(timezone=True), nullable=True),
)
editor_notes = Table(
    "_forge_editor_notes",
    _meta,
    Column("id", String(36), primary_key=True),
    Column("project", String(64), nullable=False, index=True),
    Column("area_id", String(36), nullable=True, index=True),
    Column("author_id", String(36), nullable=False, index=True),
    Column("title", String(160), nullable=False),
    Column("body", Text, nullable=False),
    Column("visibility", String(16), nullable=False),
    Column("minimum_rank", Integer, nullable=False, default=0),
    Column("allowed_role_ids", Text, nullable=False),
    Column("revision", Integer, nullable=False, default=1),
    Column("created_at", DateTime(timezone=True), nullable=False),
    Column("updated_at", DateTime(timezone=True), nullable=False, index=True),
)
editor_attachments = Table(
    "_forge_editor_attachments",
    _meta,
    Column("id", String(36), primary_key=True),
    Column("area_id", String(36), nullable=False, index=True),
    Column("uploader_id", String(36), nullable=False, index=True),
    Column("original_name", String(255), nullable=False),
    Column("stored_name", String(96), nullable=False, unique=True),
    Column("content_type", String(160), nullable=False),
    Column("size", Integer, nullable=False),
    Column("sha256", String(64), nullable=False),
    Column("created_at", DateTime(timezone=True), nullable=False),
)
editor_calls = Table(
    "_forge_editor_calls",
    _meta,
    Column("id", String(36), primary_key=True),
    Column("area_id", String(36), nullable=False, index=True),
    Column("created_by", String(36), nullable=False),
    Column("mode", String(16), nullable=False),
    Column("status", String(16), nullable=False),
    Column("created_at", DateTime(timezone=True), nullable=False),
    Column("expires_at", DateTime(timezone=True), nullable=False, index=True),
)
editor_call_tickets = Table(
    "_forge_editor_call_tickets",
    _meta,
    Column("token_hash", String(64), primary_key=True),
    Column("call_id", String(36), nullable=False, index=True),
    Column("user_id", String(36), nullable=False),
    Column("expires_at", DateTime(timezone=True), nullable=False),
    Column("used_at", DateTime(timezone=True), nullable=True),
)
editor_call_participants = Table(
    "_forge_editor_call_participants",
    _meta,
    Column("connection_id", String(36), primary_key=True),
    Column("call_id", String(36), nullable=False, index=True),
    Column("user_id", String(36), nullable=False, index=True),
    Column("display_name", String(80), nullable=False),
    Column("last_seen_at", DateTime(timezone=True), nullable=False, index=True),
)
editor_call_signals = Table(
    "_forge_editor_call_signals",
    _meta,
    Column("sequence", Integer, primary_key=True, autoincrement=True),
    Column("call_id", String(36), nullable=False, index=True),
    Column("sender_connection_id", String(36), nullable=False),
    Column("target_connection_id", String(36), nullable=True),
    Column("payload", Text, nullable=False),
    Column("created_at", DateTime(timezone=True), nullable=False),
    Column("expires_at", DateTime(timezone=True), nullable=False, index=True),
)
editor_audit = Table(
    "_forge_editor_audit",
    _meta,
    Column("id", Integer, primary_key=True, autoincrement=True),
    Column("created_at", DateTime(timezone=True), nullable=False, index=True),
    Column("actor_id", String(36), nullable=True, index=True),
    Column("action", String(80), nullable=False, index=True),
    Column("project", String(64), nullable=True, index=True),
    Column("target", String(255), nullable=False),
    Column("request_id", String(64), nullable=False),
    Column("detail", Text, nullable=False),
)


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _utc(value: datetime) -> datetime:
    return value.replace(tzinfo=timezone.utc) if value.tzinfo is None else value.astimezone(timezone.utc)


def _json(values: Any) -> str:
    return json.dumps(values, separators=(",", ":"), sort_keys=True)


def _list(raw: str | None) -> list[str]:
    if not raw:
        return []
    value = json.loads(raw)
    return [str(item) for item in value] if isinstance(value, list) else []


def _token_hash(raw: str) -> str:
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


def _agent_hash(raw: str) -> str:
    return hashlib.sha256(raw[:1024].encode("utf-8", errors="replace")).hexdigest()


def _password_digest(password: str, salt: bytes | None = None) -> str:
    salt = salt or secrets.token_bytes(16)
    digest = hashlib.scrypt(password.encode("utf-8"), salt=salt, n=2**14, r=8, p=1, dklen=32)
    return f"scrypt$16384$8$1${salt.hex()}${digest.hex()}"


def _password_matches(password: str, encoded: str) -> bool:
    try:
        algorithm, n, r, p, salt, expected = encoded.split("$", 5)
        if algorithm != "scrypt":
            return False
        actual = hashlib.scrypt(
            password.encode("utf-8"),
            salt=bytes.fromhex(salt),
            n=int(n),
            r=int(r),
            p=int(p),
            dklen=len(bytes.fromhex(expected)),
        )
        return hmac.compare_digest(actual, bytes.fromhex(expected))
    except (TypeError, ValueError):
        return False


def validate_password(password: str, username: str, minimum: int) -> None:
    if not minimum <= len(password) <= 256:
        raise HTTPException(status_code=422, detail=f"Password must contain {minimum}–256 characters")
    folded = password.casefold()
    weak = {"password", "password123", "123456789012", "qwerty123456", "jsonapiforge", "changeme1234"}
    if folded in weak or username.casefold() in folded or len(set(password)) < 6:
        raise HTTPException(status_code=422, detail="Password is too predictable")


def validate_username(username: str) -> str:
    value = username.strip()
    if _USERNAME.fullmatch(value) is None:
        raise HTTPException(status_code=422, detail="Username must be 3–32 safe characters")
    return value


def permission_matches(granted: str, required: str) -> bool:
    return granted == "*" or granted == required or (granted.endswith(".*") and required.startswith(granted[:-1]))


def _scope_pattern_within(pattern: str, parent_patterns: tuple[str, ...]) -> bool:
    """Return whether a delegated scope is contained by the caller's scopes."""
    # Wildcard-to-wildcard containment is deliberately exact. Otherwise a
    # child "*" character can itself satisfy a parent "?" wildcard.
    if "*" in pattern or "?" in pattern:
        return pattern in parent_patterns
    return any(fnmatch.fnmatchcase(pattern, parent) for parent in parent_patterns)


def _role_fingerprint(role: dict[str, Any]) -> str:
    governed = {
        "id": str(role["id"]),
        "rank": int(role["rank"]),
        "permissions": sorted({str(item) for item in role.get("permissions", [])}),
        "document_allow": sorted({str(item) for item in role.get("document_allow", [])}),
        "document_deny": sorted({str(item) for item in role.get("document_deny", [])}),
        "database_allow": sorted({str(item) for item in role.get("database_allow", [])}),
    }
    return hashlib.sha256(json.dumps(governed, sort_keys=True, separators=(",", ":")).encode()).hexdigest()


def _role_within_authority(role: dict[str, Any], caller: EditorAccess) -> bool:
    if caller.principal.is_founder:
        return True
    if int(role["rank"]) >= caller.rank:
        return False
    if any(not caller.permits(str(permission)) for permission in role.get("permissions", [])):
        return False
    if any(not _scope_pattern_within(str(pattern), caller.document_allow) for pattern in role.get("document_allow", [])):
        return False
    # Every delegated document role must preserve the caller's deny rules.
    # Exact inheritance is intentionally conservative because proving general
    # glob-language containment is error-prone and security-sensitive.
    if not set(caller.document_deny).issubset({str(pattern) for pattern in role.get("document_deny", [])}):
        return False
    return all(_scope_pattern_within(str(pattern), caller.database_allow) for pattern in role.get("database_allow", []))


@dataclass(frozen=True, slots=True)
class EditorPrincipal:
    user_id: str
    username: str
    display_name: str
    is_founder: bool
    session_hash: str | None
    legacy: bool = False


@dataclass(frozen=True, slots=True)
class EditorAccess:
    principal: EditorPrincipal
    permissions: frozenset[str]
    role_ids: frozenset[str]
    role_names: frozenset[str]
    rank: int
    document_allow: tuple[str, ...]
    document_deny: tuple[str, ...]
    database_allow: tuple[str, ...]

    def permits(self, permission: str) -> bool:
        return self.principal.is_founder or any(permission_matches(item, permission) for item in self.permissions)

    def permits_document(self, path: str) -> bool:
        if self.principal.is_founder:
            return True
        if any(fnmatch.fnmatchcase(path, pattern) for pattern in self.document_deny):
            return False
        return any(fnmatch.fnmatchcase(path, pattern) for pattern in self.document_allow)

    def permits_database(self, alias: str, table: str) -> bool:
        if self.principal.is_founder:
            return True
        key = f"{alias}:{table}"
        return any(fnmatch.fnmatchcase(key, pattern) for pattern in self.database_allow)


_BUILTIN_ROLES: tuple[dict[str, Any], ...] = (
    {"id": "00000000-0000-0000-0000-000000000001", "name": "Founder", "rank": 1000, "permissions": ["*"], "docs": ["*"], "db": ["*"]},
    {"id": "00000000-0000-0000-0000-000000000002", "name": "Administrator", "rank": 800, "permissions": ["*"], "docs": ["*"], "db": ["*"]},
    {
        "id": "00000000-0000-0000-0000-000000000003",
        "name": "Developer",
        "rank": 600,
        "permissions": [
            "projects.read",
            "projects.validate",
            "documents.read",
            "documents.write",
            "documents.graphs.write",
            "databases.metadata.read",
            "databases.rows.read",
            "profiles.read",
            "profiles.write.own",
            "members.read",
            "areas.read",
            "messages.read",
            "messages.write",
            "notes.read",
            "notes.write",
            "attachments.read",
            "attachments.write",
            "calls.join",
            "calls.start",
        ],
        "docs": ["app.json", "config/*.json", "graphs/*.forgegraph.json"],
        "db": ["*"],
    },
    {
        "id": "00000000-0000-0000-0000-000000000004",
        "name": "Analyst",
        "rank": 400,
        "permissions": [
            "projects.read",
            "documents.read",
            "databases.metadata.read",
            "databases.rows.read",
            "profiles.read",
            "profiles.write.own",
            "members.read",
            "areas.read",
            "messages.read",
            "messages.write",
            "notes.read",
            "notes.write",
            "attachments.read",
            "calls.join",
        ],
        "docs": ["*"],
        "db": ["*"],
    },
    {
        "id": "00000000-0000-0000-0000-000000000005",
        "name": "Collaborator",
        "rank": 200,
        "permissions": [
            "projects.read",
            "documents.read",
            "profiles.read",
            "profiles.write.own",
            "members.read",
            "areas.read",
            "messages.read",
            "messages.write",
            "notes.read",
            "notes.write",
            "attachments.read",
            "attachments.write",
            "calls.join",
        ],
        "docs": ["*"],
        "db": [],
    },
    {
        "id": "00000000-0000-0000-0000-000000000006",
        "name": "Viewer",
        "rank": 100,
        "permissions": [
            "projects.read",
            "documents.read",
            "databases.metadata.read",
            "profiles.read",
            "members.read",
            "areas.read",
            "messages.read",
            "notes.read",
            "attachments.read",
        ],
        "docs": ["*"],
        "db": ["*"],
    },
)


async def init_editor_identity(engine: AsyncEngine, *, mode: str = "create") -> None:
    if mode not in {"create", "validate"}:
        raise RuntimeError(f"Unsupported internal schema mode: {mode}")
    if mode == "validate":
        async with engine.connect() as connection:

            def missing(sync_connection):
                from sqlalchemy import inspect

                inspector = inspect(sync_connection)
                return sorted(name for name in _meta.tables if not inspector.has_table(name))

            absent = await connection.run_sync(missing)
        if absent:
            raise RuntimeError("Forge Editor support schema is missing tables: " + ", ".join(absent) + ". Run `forge migrate`.")
        return
    async with engine.begin() as connection:
        await connection.run_sync(_meta.create_all)
        now = _now()
        state = (await connection.execute(select(editor_state.c.id).where(editor_state.c.id == 1))).first()
        if not state:
            await connection.execute(insert(editor_state).values(id=1, founder_user_id=None, created_at=now))
        for role in _BUILTIN_ROLES:
            exists = (await connection.execute(select(editor_roles.c.id).where(editor_roles.c.id == role["id"]))).first()
            if exists:
                continue
            await connection.execute(
                insert(editor_roles).values(
                    id=role["id"],
                    name=role["name"],
                    name_key=role["name"].casefold(),
                    rank=role["rank"],
                    permissions=_json(role["permissions"]),
                    document_allow=_json(role["docs"]),
                    document_deny="[]",
                    database_allow=_json(role["db"]),
                    system=True,
                    created_at=now,
                    updated_at=now,
                )
            )


class EditorIdentityStore:
    def __init__(self, engine: AsyncEngine, settings: Settings):
        self.engine = engine
        self.settings = settings

    async def initialized(self) -> bool:
        async with self.engine.connect() as connection:
            value = await connection.scalar(select(editor_state.c.founder_user_id).where(editor_state.c.id == 1))
        return bool(value)

    async def bootstrap_founder(self, *, username: str, password: str, display_name: str, user_agent: str) -> tuple[EditorPrincipal, str]:
        if await self.initialized():
            raise HTTPException(status_code=409, detail="Founder account has already been configured")
        username = validate_username(username)
        validate_password(password, username, self.settings.editor_password_min_length)
        display_name = display_name.strip()
        if not 1 <= len(display_name) <= 80:
            raise HTTPException(status_code=422, detail="Display name must contain 1–80 characters")
        password_hash = await asyncio.to_thread(_password_digest, password)
        now = _now()
        user_id = str(uuid.uuid4())
        try:
            async with self.engine.begin() as connection:
                claimed = await connection.execute(
                    update(editor_state)
                    .where(and_(editor_state.c.id == 1, editor_state.c.founder_user_id.is_(None)))
                    .values(founder_user_id=user_id)
                )
                if claimed.rowcount != 1:
                    raise HTTPException(status_code=409, detail="Founder account has already been configured")
                await connection.execute(
                    insert(editor_users).values(
                        id=user_id,
                        username=username,
                        username_key=username.casefold(),
                        password_hash=password_hash,
                        display_name=display_name,
                        title="Founder",
                        bio="",
                        timezone="timezone.utc",
                        status="",
                        active=True,
                        is_founder=True,
                        created_at=now,
                        updated_at=now,
                    )
                )
                await connection.execute(
                    insert(editor_memberships).values(
                        id=str(uuid.uuid4()), user_id=user_id, role_id=_BUILTIN_ROLES[0]["id"], project="*", created_at=now
                    )
                )
        except IntegrityError as exc:
            raise HTTPException(status_code=409, detail="Username is already in use") from exc
        principal = EditorPrincipal(user_id, username, display_name, True, None)
        token = await self._new_session(principal, user_agent)
        return principal, token

    def _attempt_key(self, username: str, client_ip: str) -> str:
        return _token_hash(f"{username.casefold()}\0{client_ip}")

    async def _assert_login_available(self, attempt_key: str) -> None:
        now = _now()
        async with self.engine.connect() as connection:
            row = (
                (await connection.execute(select(editor_login_attempts).where(editor_login_attempts.c.attempt_key == attempt_key)))
                .mappings()
                .first()
            )
        if row and row["locked_until"] and _utc(row["locked_until"]) > now:
            retry = max(1, int((_utc(row["locked_until"]) - now).total_seconds()))
            raise HTTPException(status_code=429, detail="Too many sign-in attempts", headers={"Retry-After": str(retry)})

    async def _record_login_failure(self, attempt_key: str) -> None:
        now = _now()
        window = timedelta(seconds=self.settings.editor_login_window_seconds)
        async with self.engine.begin() as connection:
            row = (
                (await connection.execute(select(editor_login_attempts).where(editor_login_attempts.c.attempt_key == attempt_key)))
                .mappings()
                .first()
            )
            failures = 1 if not row or now - _utc(row["window_started_at"]) > window else int(row["failures"]) + 1
            started = now if not row or now - _utc(row["window_started_at"]) > window else row["window_started_at"]
            locked = (
                now + timedelta(seconds=self.settings.editor_login_lock_seconds)
                if failures >= self.settings.editor_login_max_attempts
                else None
            )
            values = dict(failures=failures, window_started_at=started, locked_until=locked)
            if row:
                await connection.execute(
                    update(editor_login_attempts).where(editor_login_attempts.c.attempt_key == attempt_key).values(**values)
                )
            else:
                await connection.execute(insert(editor_login_attempts).values(attempt_key=attempt_key, **values))

    async def login(self, *, username: str, password: str, client_ip: str, user_agent: str) -> tuple[EditorPrincipal, str]:
        username = username.strip()
        if len(password) > 256:
            raise HTTPException(status_code=401, detail="Invalid username or password")
        attempt_key = self._attempt_key(username, client_ip)
        await self._assert_login_available(attempt_key)
        async with self.engine.connect() as connection:
            row = (
                (await connection.execute(select(editor_users).where(editor_users.c.username_key == username.casefold())))
                .mappings()
                .first()
            )
        valid = bool(row and row["active"] and await asyncio.to_thread(_password_matches, password, row["password_hash"]))
        if not valid:
            await self._record_login_failure(attempt_key)
            await asyncio.sleep(0.08)
            raise HTTPException(status_code=401, detail="Invalid username or password")
        async with self.engine.begin() as connection:
            await connection.execute(delete(editor_login_attempts).where(editor_login_attempts.c.attempt_key == attempt_key))
        principal = EditorPrincipal(row["id"], row["username"], row["display_name"], bool(row["is_founder"]), None)
        token = await self._new_session(principal, user_agent)
        return principal, token

    async def _new_session(self, principal: EditorPrincipal, user_agent: str) -> str:
        token = "jfe_session_" + secrets.token_urlsafe(48)
        digest = _token_hash(token)
        now = _now()
        async with self.engine.begin() as connection:
            await connection.execute(
                delete(editor_sessions).where(
                    or_(editor_sessions.c.expires_at <= now, editor_sessions.c.idle_expires_at <= now, editor_sessions.c.revoked.is_(True))
                )
            )
            await connection.execute(
                insert(editor_sessions).values(
                    token_hash=digest,
                    user_id=principal.user_id,
                    user_agent_hash=_agent_hash(user_agent),
                    created_at=now,
                    last_seen_at=now,
                    expires_at=now + timedelta(seconds=self.settings.editor_session_ttl_seconds),
                    idle_expires_at=now + timedelta(seconds=self.settings.editor_session_idle_seconds),
                    revoked=False,
                )
            )
        return token

    async def authenticate(self, token: str, user_agent: str) -> EditorPrincipal:
        if _SESSION_TOKEN.fullmatch(token) is None:
            raise HTTPException(status_code=401, detail="Editor authentication required")
        digest = _token_hash(token)
        now = _now()
        statement = (
            select(editor_sessions, editor_users)
            .join(editor_users, editor_users.c.id == editor_sessions.c.user_id)
            .where(editor_sessions.c.token_hash == digest)
        )
        async with self.engine.connect() as connection:
            row = (await connection.execute(statement)).mappings().first()
        if (
            not row
            or row["revoked"]
            or not row["active"]
            or _utc(row["expires_at"]) <= now
            or _utc(row["idle_expires_at"]) <= now
            or (self.settings.editor_session_bind_user_agent and not hmac.compare_digest(row["user_agent_hash"], _agent_hash(user_agent)))
        ):
            raise HTTPException(status_code=401, detail="Editor session is invalid or expired")
        if (now - _utc(row["last_seen_at"])).total_seconds() >= 60:
            async with self.engine.begin() as connection:
                await connection.execute(
                    update(editor_sessions)
                    .where(editor_sessions.c.token_hash == digest)
                    .values(last_seen_at=now, idle_expires_at=now + timedelta(seconds=self.settings.editor_session_idle_seconds))
                )
        return EditorPrincipal(row["user_id"], row["username"], row["display_name"], bool(row["is_founder"]), digest)

    async def logout(self, principal: EditorPrincipal) -> None:
        if not principal.session_hash:
            return
        async with self.engine.begin() as connection:
            await connection.execute(
                update(editor_sessions).where(editor_sessions.c.token_hash == principal.session_hash).values(revoked=True)
            )

    async def access(self, principal: EditorPrincipal, project: str = "*") -> EditorAccess:
        async with self.engine.connect() as connection:
            return await self._access_with_connection(connection, principal, project)

    async def _access_with_connection(self, connection, principal: EditorPrincipal, project: str = "*") -> EditorAccess:
        if principal.legacy or principal.is_founder:
            return EditorAccess(principal, frozenset({"*"}), frozenset(), frozenset({"Founder"}), 1000, ("*",), (), ("*",))
        statement = (
            select(editor_roles)
            .join(editor_memberships, editor_memberships.c.role_id == editor_roles.c.id)
            .where(and_(editor_memberships.c.user_id == principal.user_id, editor_memberships.c.project.in_(["*", project])))
        )
        rows = (await connection.execute(statement)).mappings().all()
        permissions: set[str] = set()
        role_ids: set[str] = set()
        role_names: set[str] = set()
        allow: set[str] = set()
        deny: set[str] = set()
        databases: set[str] = set()
        rank = 0
        for row in rows:
            permissions.update(_list(row["permissions"]))
            role_ids.add(row["id"])
            role_names.add(row["name"])
            allow.update(_list(row["document_allow"]))
            deny.update(_list(row["document_deny"]))
            databases.update(_list(row["database_allow"]))
            rank = max(rank, int(row["rank"]))
        return EditorAccess(
            principal,
            frozenset(permissions),
            frozenset(role_ids),
            frozenset(role_names),
            rank,
            tuple(sorted(allow)),
            tuple(sorted(deny)),
            tuple(sorted(databases)),
        )

    async def profile(self, user_id: str) -> dict[str, Any]:
        async with self.engine.connect() as connection:
            row = (await connection.execute(select(editor_users).where(editor_users.c.id == user_id))).mappings().first()
        if not row:
            raise HTTPException(status_code=404, detail="Member not found")
        return {
            key: row[key]
            for key in (
                "id",
                "username",
                "display_name",
                "title",
                "bio",
                "timezone",
                "status",
                "active",
                "is_founder",
                "created_at",
                "updated_at",
            )
        }

    async def update_profile(self, principal: EditorPrincipal, values: dict[str, str]) -> dict[str, Any]:
        limits = {"display_name": 80, "title": 120, "bio": 2000, "timezone": 64, "status": 160}
        cleaned: dict[str, str] = {}
        for field, maximum in limits.items():
            if field in values:
                value = values[field].strip()
                if field == "display_name" and not value:
                    raise HTTPException(status_code=422, detail="Display name cannot be empty")
                if len(value) > maximum or any(character in value for character in "\0"):
                    raise HTTPException(status_code=422, detail=f"Invalid profile field: {field}")
                cleaned[field] = value
        cleaned["updated_at"] = _now()
        async with self.engine.begin() as connection:
            await connection.execute(update(editor_users).where(editor_users.c.id == principal.user_id).values(**cleaned))
        return await self.profile(principal.user_id)

    async def list_roles(self) -> list[dict[str, Any]]:
        async with self.engine.connect() as connection:
            rows = (
                (await connection.execute(select(editor_roles).order_by(editor_roles.c.rank.desc(), editor_roles.c.name))).mappings().all()
            )
        return [
            {
                "id": row["id"],
                "name": row["name"],
                "rank": row["rank"],
                "permissions": _list(row["permissions"]),
                "document_allow": _list(row["document_allow"]),
                "document_deny": _list(row["document_deny"]),
                "database_allow": _list(row["database_allow"]),
                "system": row["system"],
            }
            for row in rows
        ]

    def _validated_role_values(self, values: dict[str, Any], caller: EditorAccess) -> dict[str, Any]:
        name = str(values.get("name", "")).strip()
        rank = int(values.get("rank", 0))
        permissions = sorted({str(item) for item in values.get("permissions", [])})
        allow = sorted({str(item) for item in values.get("document_allow", [])})
        deny = sorted({str(item) for item in values.get("document_deny", [])})
        databases = sorted({str(item) for item in values.get("database_allow", [])})
        if _ROLE_NAME.fullmatch(name) is None or not 1 <= rank < caller.rank:
            raise HTTPException(status_code=422, detail="Role name or rank is invalid for the caller")
        if any(item not in EDITOR_PERMISSIONS and item != "*" for item in permissions):
            raise HTTPException(status_code=422, detail="Role contains an unknown permission")
        for pattern in [*allow, *deny, *databases]:
            if _SAFE_PATTERN.fullmatch(pattern) is None or ".." in pattern or "\\" in pattern:
                raise HTTPException(status_code=422, detail="Role contains an unsafe scope pattern")
        candidate = {
            "id": "candidate",
            "rank": rank,
            "permissions": permissions,
            "document_allow": allow,
            "document_deny": deny,
            "database_allow": databases,
        }
        if not _role_within_authority(candidate, caller):
            raise HTTPException(status_code=403, detail="A role cannot widen the caller's authority")
        return {
            "name": name,
            "name_key": name.casefold(),
            "rank": rank,
            "permissions": _json(permissions),
            "document_allow": _json(allow),
            "document_deny": _json(deny),
            "database_allow": _json(databases),
            "updated_at": _now(),
        }

    async def save_role(self, caller: EditorAccess, values: dict[str, Any], role_id: str | None = None) -> dict[str, Any]:
        prepared = self._validated_role_values(values, caller)
        now = _now()
        try:
            async with self.engine.begin() as connection:
                if role_id:
                    current = (
                        (await connection.execute(select(editor_roles).where(editor_roles.c.id == role_id).with_for_update()))
                        .mappings()
                        .first()
                    )
                    if not current:
                        raise HTTPException(status_code=404, detail="Role not found")
                    current_role = {
                        "id": current["id"],
                        "rank": current["rank"],
                        "permissions": _list(current["permissions"]),
                        "document_allow": _list(current["document_allow"]),
                        "document_deny": _list(current["document_deny"]),
                        "database_allow": _list(current["database_allow"]),
                    }
                    if current["system"] or not _role_within_authority(current_role, caller):
                        raise HTTPException(status_code=403, detail="This role cannot be modified by the caller")
                    await connection.execute(update(editor_roles).where(editor_roles.c.id == role_id).values(**prepared))
                    # Invitations are authority snapshots. Revoke every pending
                    # snapshot after a custom role changes.
                    await connection.execute(update(editor_invitations).where(editor_invitations.c.used_at.is_(None)).values(used_at=now))
                else:
                    role_id = str(uuid.uuid4())
                    await connection.execute(insert(editor_roles).values(id=role_id, system=False, created_at=now, **prepared))
        except IntegrityError as exc:
            raise HTTPException(status_code=409, detail="Role name is already in use") from exc
        roles = await self.list_roles()
        return next(item for item in roles if item["id"] == role_id)

    async def create_invitation(self, caller: EditorAccess, memberships: list[dict[str, str]], expires_hours: int) -> str:
        if not 1 <= expires_hours <= 168 or not memberships or len(memberships) > 32:
            raise HTTPException(status_code=422, detail="Invitation membership or expiry is invalid")
        role_map = {item["id"]: item for item in await self.list_roles()}
        normalized: list[dict[str, str]] = []
        for membership in memberships:
            role_id = membership.get("role_id", "")
            project = membership.get("project", "")
            role = role_map.get(role_id)
            if not role or not _role_within_authority(role, caller) or _PROJECT_SCOPE.fullmatch(project) is None:
                raise HTTPException(status_code=403, detail="Invitation attempts to assign an unauthorized role or project")
            normalized.append({"role_id": role_id, "project": project, "role_fingerprint": _role_fingerprint(role)})
        normalized = list({(item["role_id"], item["project"]): item for item in normalized}.values())
        raw = "jfi_" + secrets.token_urlsafe(40)
        now = _now()
        async with self.engine.begin() as connection:
            await connection.execute(
                delete(editor_invitations).where(or_(editor_invitations.c.expires_at <= now, editor_invitations.c.used_at.is_not(None)))
            )
            await connection.execute(
                insert(editor_invitations).values(
                    token_hash=_token_hash(raw),
                    memberships=_json(normalized),
                    created_by=caller.principal.user_id,
                    created_at=now,
                    expires_at=now + timedelta(hours=expires_hours),
                    used_at=None,
                )
            )
        return raw

    async def accept_invitation(
        self, *, invitation: str, username: str, password: str, display_name: str, user_agent: str
    ) -> tuple[EditorPrincipal, str]:
        if _INVITATION_TOKEN.fullmatch(invitation) is None:
            raise HTTPException(status_code=401, detail="Invitation is invalid or expired")
        username = validate_username(username)
        validate_password(password, username, self.settings.editor_password_min_length)
        display_name = display_name.strip()
        if not 1 <= len(display_name) <= 80:
            raise HTTPException(status_code=422, detail="Display name must contain 1–80 characters")
        digest = _token_hash(invitation)
        now = _now()
        # Reject bad credentials before the expensive password KDF, then
        # revalidate under the write transaction for single-use semantics.
        async with self.engine.connect() as connection:
            preliminary = (
                (await connection.execute(select(editor_invitations).where(editor_invitations.c.token_hash == digest))).mappings().first()
            )
        if not preliminary or preliminary["used_at"] or _utc(preliminary["expires_at"]) <= now:
            raise HTTPException(status_code=401, detail="Invitation is invalid or expired")
        password_hash = await asyncio.to_thread(_password_digest, password)
        user_id = str(uuid.uuid4())
        try:
            async with self.engine.begin() as connection:
                invitation_row = (
                    (
                        await connection.execute(
                            select(editor_invitations).where(editor_invitations.c.token_hash == digest).with_for_update()
                        )
                    )
                    .mappings()
                    .first()
                )
                if not invitation_row or invitation_row["used_at"] or _utc(invitation_row["expires_at"]) <= now:
                    raise HTTPException(status_code=401, detail="Invitation is invalid or expired")
                creator = (
                    (
                        await connection.execute(
                            select(editor_users).where(editor_users.c.id == invitation_row["created_by"]).with_for_update()
                        )
                    )
                    .mappings()
                    .first()
                )
                if not creator or not creator["active"]:
                    raise HTTPException(status_code=401, detail="Invitation is invalid or expired")
                creator_principal = EditorPrincipal(
                    creator["id"], creator["username"], creator["display_name"], bool(creator["is_founder"]), None
                )
                creator_access = await self._access_with_connection(connection, creator_principal, "*")
                if not creator_access.permits("invitations.manage"):
                    raise HTTPException(status_code=401, detail="Invitation is invalid or expired")
                try:
                    pending_memberships = json.loads(invitation_row["memberships"])
                except (TypeError, json.JSONDecodeError) as exc:
                    raise HTTPException(status_code=401, detail="Invitation is invalid or expired") from exc
                if not isinstance(pending_memberships, list) or not pending_memberships:
                    raise HTTPException(status_code=401, detail="Invitation is invalid or expired")
                role_ids = {item.get("role_id") for item in pending_memberships if isinstance(item, dict)}
                role_rows = (
                    (await connection.execute(select(editor_roles).where(editor_roles.c.id.in_(role_ids)).with_for_update()))
                    .mappings()
                    .all()
                )
                role_map = {
                    row["id"]: {
                        "id": row["id"],
                        "rank": row["rank"],
                        "permissions": _list(row["permissions"]),
                        "document_allow": _list(row["document_allow"]),
                        "document_deny": _list(row["document_deny"]),
                        "database_allow": _list(row["database_allow"]),
                    }
                    for row in role_rows
                }
                for membership in pending_memberships:
                    if not isinstance(membership, dict) or set(membership) != {"role_id", "project", "role_fingerprint"}:
                        raise HTTPException(status_code=401, detail="Invitation is invalid or expired")
                    role = role_map.get(membership["role_id"])
                    if (
                        not role
                        or _PROJECT_SCOPE.fullmatch(str(membership["project"])) is None
                        or not hmac.compare_digest(str(membership["role_fingerprint"]), _role_fingerprint(role))
                        or not _role_within_authority(role, creator_access)
                    ):
                        raise HTTPException(status_code=401, detail="Invitation is invalid or expired")
                consumed = await connection.execute(
                    update(editor_invitations)
                    .where(and_(editor_invitations.c.token_hash == digest, editor_invitations.c.used_at.is_(None)))
                    .values(used_at=now)
                )
                if consumed.rowcount != 1:
                    raise HTTPException(status_code=409, detail="Invitation has already been used")
                await connection.execute(
                    insert(editor_users).values(
                        id=user_id,
                        username=username,
                        username_key=username.casefold(),
                        password_hash=password_hash,
                        display_name=display_name,
                        title="",
                        bio="",
                        timezone="timezone.utc",
                        status="",
                        active=True,
                        is_founder=False,
                        created_at=now,
                        updated_at=now,
                    )
                )
                for membership in pending_memberships:
                    await connection.execute(
                        insert(editor_memberships).values(
                            id=str(uuid.uuid4()),
                            user_id=user_id,
                            role_id=membership["role_id"],
                            project=membership["project"],
                            created_at=now,
                        )
                    )
        except IntegrityError as exc:
            raise HTTPException(status_code=409, detail="Username is already in use") from exc
        principal = EditorPrincipal(user_id, username, display_name, False, None)
        return principal, await self._new_session(principal, user_agent)

    async def list_members(self) -> list[dict[str, Any]]:
        statement = select(editor_users).order_by(editor_users.c.is_founder.desc(), editor_users.c.display_name)
        async with self.engine.connect() as connection:
            users = (await connection.execute(statement)).mappings().all()
            memberships = (
                (
                    await connection.execute(
                        select(editor_memberships, editor_roles.c.name, editor_roles.c.rank).join(
                            editor_roles, editor_roles.c.id == editor_memberships.c.role_id
                        )
                    )
                )
                .mappings()
                .all()
            )
        by_user: dict[str, list[dict[str, Any]]] = {}
        for row in memberships:
            by_user.setdefault(row["user_id"], []).append(
                {"role_id": row["role_id"], "role": row["name"], "rank": row["rank"], "project": row["project"]}
            )
        return [
            {
                "id": row["id"],
                "username": row["username"],
                "display_name": row["display_name"],
                "title": row["title"],
                "status": row["status"],
                "active": row["active"],
                "is_founder": row["is_founder"],
                "memberships": by_user.get(row["id"], []),
            }
            for row in users
        ]

    async def replace_memberships(self, caller: EditorAccess, user_id: str, memberships: list[dict[str, str]], *, active: bool) -> None:
        if len(memberships) > 64:
            raise HTTPException(status_code=422, detail="A member cannot have more than 64 scoped memberships")
        requested: list[tuple[str, str]] = []
        for item in memberships:
            role_id = item.get("role_id", "")
            project = item.get("project", "")
            if not role_id or _PROJECT_SCOPE.fullmatch(project) is None:
                raise HTTPException(status_code=403, detail="Membership is outside the caller's authority")
            requested.append((role_id, project))
        requested = list(dict.fromkeys(requested))
        now = _now()
        async with self.engine.begin() as connection:
            target = (
                (await connection.execute(select(editor_users).where(editor_users.c.id == user_id).with_for_update())).mappings().first()
            )
            if not target:
                raise HTTPException(status_code=404, detail="Member not found")
            if target["is_founder"]:
                raise HTTPException(status_code=403, detail="The founder account cannot be disabled or reassigned")

            role_ids = {role_id for role_id, _project in requested}
            role_rows = (
                (await connection.execute(select(editor_roles).where(editor_roles.c.id.in_(role_ids)).with_for_update())).mappings().all()
            )
            role_map = {
                row["id"]: {
                    "id": row["id"],
                    "rank": row["rank"],
                    "permissions": _list(row["permissions"]),
                    "document_allow": _list(row["document_allow"]),
                    "document_deny": _list(row["document_deny"]),
                    "database_allow": _list(row["database_allow"]),
                }
                for row in role_rows
            }
            normalized: list[dict[str, str]] = []
            target_rank = 0
            for role_id, project in requested:
                role = role_map.get(role_id)
                if not role or not _role_within_authority(role, caller):
                    raise HTTPException(status_code=403, detail="Membership is outside the caller's authority")
                target_rank = max(target_rank, int(role["rank"]))
                normalized.append({"role_id": role_id, "project": project})

            current_roles = (
                (
                    await connection.execute(
                        select(editor_roles.c.rank)
                        .join(editor_memberships, editor_memberships.c.role_id == editor_roles.c.id)
                        .where(editor_memberships.c.user_id == user_id)
                        .with_for_update()
                    )
                )
                .scalars()
                .all()
            )
            if max((int(rank) for rank in current_roles), default=0) >= caller.rank or target_rank >= caller.rank:
                raise HTTPException(status_code=403, detail="Member rank is outside the caller's authority")

            await connection.execute(delete(editor_memberships).where(editor_memberships.c.user_id == user_id))
            for item in normalized:
                await connection.execute(
                    insert(editor_memberships).values(
                        id=str(uuid.uuid4()), user_id=user_id, role_id=item["role_id"], project=item["project"], created_at=now
                    )
                )
            await connection.execute(update(editor_users).where(editor_users.c.id == user_id).values(active=active, updated_at=now))
            await connection.execute(
                update(editor_invitations)
                .where(and_(editor_invitations.c.created_by == user_id, editor_invitations.c.used_at.is_(None)))
                .values(used_at=now)
            )
            if not active:
                await connection.execute(update(editor_sessions).where(editor_sessions.c.user_id == user_id).values(revoked=True))

    async def visible_area(self, access: EditorAccess, area_id: str) -> dict[str, Any]:
        async with self.engine.connect() as connection:
            row = (await connection.execute(select(editor_areas).where(editor_areas.c.id == area_id))).mappings().first()
        if not row:
            raise HTTPException(status_code=404, detail="Project area not found")
        allowed = _list(row["allowed_role_ids"])
        if row["visibility"] != "open" and access.rank < int(row["minimum_rank"]) and not access.role_ids.intersection(allowed):
            raise HTTPException(status_code=404, detail="Project area not found")
        return {**dict(row), "allowed_role_ids": allowed}

    async def area_project(self, area_id: str) -> str:
        async with self.engine.connect() as connection:
            project = await connection.scalar(select(editor_areas.c.project).where(editor_areas.c.id == area_id))
        if project is None:
            raise HTTPException(status_code=404, detail="Project area not found")
        return str(project)

    async def list_areas(self, access: EditorAccess, project: str) -> list[dict[str, Any]]:
        async with self.engine.connect() as connection:
            rows = (
                (
                    await connection.execute(
                        select(editor_areas).where(editor_areas.c.project.in_(["*", project])).order_by(editor_areas.c.name)
                    )
                )
                .mappings()
                .all()
            )
        result = []
        for row in rows:
            allowed = _list(row["allowed_role_ids"])
            if row["visibility"] == "open" or access.rank >= int(row["minimum_rank"]) or access.role_ids.intersection(allowed):
                result.append({**dict(row), "allowed_role_ids": allowed})
        return result

    async def create_area(self, access: EditorAccess, values: dict[str, Any]) -> dict[str, Any]:
        name = str(values.get("name", "")).strip()
        project = str(values.get("project", "")).strip()
        visibility = str(values.get("visibility", "open"))
        minimum_rank = int(values.get("minimum_rank", 0))
        role_ids = sorted({str(item) for item in values.get("allowed_role_ids", [])})
        if not 1 <= len(name) <= 96 or _PROJECT_SCOPE.fullmatch(project) is None or visibility not in {"open", "restricted"}:
            raise HTTPException(status_code=422, detail="Project area settings are invalid")
        if minimum_rank < 0 or minimum_rank >= access.rank:
            raise HTTPException(status_code=422, detail="Area minimum rank must be below the caller")
        area_id = str(uuid.uuid4())
        now = _now()
        async with self.engine.begin() as connection:
            await connection.execute(
                insert(editor_areas).values(
                    id=area_id,
                    project=project,
                    name=name,
                    description=str(values.get("description", "")).strip()[:500],
                    visibility=visibility,
                    minimum_rank=minimum_rank,
                    allowed_role_ids=_json(role_ids),
                    created_by=access.principal.user_id,
                    created_at=now,
                )
            )
        return await self.visible_area(access, area_id)

    async def list_messages(self, access: EditorAccess, area_id: str, *, before: datetime | None, limit: int) -> list[dict[str, Any]]:
        await self.visible_area(access, area_id)
        statement = (
            select(editor_messages, editor_users.c.display_name, editor_users.c.username)
            .join(editor_users, editor_users.c.id == editor_messages.c.author_id)
            .where(editor_messages.c.area_id == area_id)
            .order_by(editor_messages.c.created_at.desc())
            .limit(limit)
        )
        if before:
            statement = statement.where(editor_messages.c.created_at < before)
        async with self.engine.connect() as connection:
            rows = (await connection.execute(statement)).mappings().all()
        return [dict(row) for row in reversed(rows)]

    async def post_message(self, access: EditorAccess, area_id: str, body: str, kind: str = "message") -> dict[str, Any]:
        await self.visible_area(access, area_id)
        body = body.strip()
        if kind not in {"message", "announcement"} or not body or len(body) > self.settings.editor_max_message_chars or "\0" in body:
            raise HTTPException(status_code=422, detail="Message is empty, too large or invalid")
        message_id = str(uuid.uuid4())
        now = _now()
        async with self.engine.begin() as connection:
            await connection.execute(
                insert(editor_messages).values(
                    id=message_id, area_id=area_id, author_id=access.principal.user_id, kind=kind, body=body, created_at=now, edited_at=None
                )
            )
        return {
            "id": message_id,
            "area_id": area_id,
            "author_id": access.principal.user_id,
            "display_name": access.principal.display_name,
            "username": access.principal.username,
            "kind": kind,
            "body": body,
            "created_at": now,
            "edited_at": None,
        }

    async def list_notes(self, access: EditorAccess, project: str) -> list[dict[str, Any]]:
        statement = (
            select(editor_notes, editor_users.c.display_name)
            .join(editor_users, editor_users.c.id == editor_notes.c.author_id)
            .where(editor_notes.c.project.in_(["*", project]))
            .order_by(editor_notes.c.updated_at.desc())
        )
        async with self.engine.connect() as connection:
            rows = (await connection.execute(statement)).mappings().all()
        result = []
        for row in rows:
            roles = _list(row["allowed_role_ids"])
            visible = row["visibility"] == "open" or row["author_id"] == access.principal.user_id
            if row["visibility"] == "restricted":
                visible = visible or access.rank >= int(row["minimum_rank"]) or bool(access.role_ids.intersection(roles))
            if visible:
                result.append({**dict(row), "allowed_role_ids": roles})
        return result

    async def create_note(self, access: EditorAccess, values: dict[str, Any]) -> dict[str, Any]:
        project = str(values.get("project", "")).strip()
        title = str(values.get("title", "")).strip()
        body = str(values.get("body", ""))
        visibility = str(values.get("visibility", "open"))
        minimum_rank = int(values.get("minimum_rank", 0))
        roles = sorted({str(item) for item in values.get("allowed_role_ids", [])})
        if (
            _PROJECT_SCOPE.fullmatch(project) is None
            or not 1 <= len(title) <= 160
            or len(body) > self.settings.editor_max_note_chars
            or visibility not in {"open", "restricted", "private"}
            or minimum_rank < 0
        ):
            raise HTTPException(status_code=422, detail="Note settings are invalid")
        area_id = values.get("area_id")
        if area_id:
            area = await self.visible_area(access, str(area_id))
            if area["project"] not in {"*", project}:
                raise HTTPException(status_code=422, detail="Note and project area belong to different projects")
        note_id = str(uuid.uuid4())
        now = _now()
        async with self.engine.begin() as connection:
            await connection.execute(
                insert(editor_notes).values(
                    id=note_id,
                    project=project,
                    area_id=area_id,
                    author_id=access.principal.user_id,
                    title=title,
                    body=body,
                    visibility=visibility,
                    minimum_rank=minimum_rank,
                    allowed_role_ids=_json(roles),
                    revision=1,
                    created_at=now,
                    updated_at=now,
                )
            )
        return next(item for item in await self.list_notes(access, project) if item["id"] == note_id)

    async def register_attachment(
        self, access: EditorAccess, *, area_id: str, original_name: str, stored_name: str, content_type: str, size: int, sha256: str
    ) -> dict[str, Any]:
        await self.visible_area(access, area_id)
        attachment_id = str(uuid.uuid4())
        now = _now()
        async with self.engine.begin() as connection:
            await connection.execute(
                insert(editor_attachments).values(
                    id=attachment_id,
                    area_id=area_id,
                    uploader_id=access.principal.user_id,
                    original_name=original_name,
                    stored_name=stored_name,
                    content_type=content_type,
                    size=size,
                    sha256=sha256,
                    created_at=now,
                )
            )
        return {
            "id": attachment_id,
            "area_id": area_id,
            "original_name": original_name,
            "content_type": content_type,
            "size": size,
            "sha256": sha256,
            "created_at": now,
        }

    async def list_attachments(self, access: EditorAccess, area_id: str, *, limit: int) -> list[dict[str, Any]]:
        await self.visible_area(access, area_id)
        statement = (
            select(editor_attachments, editor_users.c.display_name, editor_users.c.username)
            .join(editor_users, editor_users.c.id == editor_attachments.c.uploader_id)
            .where(editor_attachments.c.area_id == area_id)
            .order_by(editor_attachments.c.created_at.desc())
            .limit(limit)
        )
        async with self.engine.connect() as connection:
            rows = (await connection.execute(statement)).mappings().all()
        return [
            {
                key: row[key]
                for key in (
                    "id",
                    "area_id",
                    "uploader_id",
                    "display_name",
                    "username",
                    "original_name",
                    "content_type",
                    "size",
                    "sha256",
                    "created_at",
                )
            }
            for row in rows
        ]

    async def attachment(self, access: EditorAccess, attachment_id: str) -> dict[str, Any]:
        async with self.engine.connect() as connection:
            row = (await connection.execute(select(editor_attachments).where(editor_attachments.c.id == attachment_id))).mappings().first()
        if not row:
            raise HTTPException(status_code=404, detail="Attachment not found")
        await self.visible_area(access, row["area_id"])
        return dict(row)

    async def attachment_project(self, attachment_id: str) -> str:
        statement = (
            select(editor_areas.c.project)
            .join(editor_attachments, editor_attachments.c.area_id == editor_areas.c.id)
            .where(editor_attachments.c.id == attachment_id)
        )
        async with self.engine.connect() as connection:
            project = await connection.scalar(statement)
        if project is None:
            raise HTTPException(status_code=404, detail="Attachment not found")
        return str(project)

    async def create_call(self, access: EditorAccess, area_id: str, mode: str) -> dict[str, Any]:
        await self.visible_area(access, area_id)
        if mode not in {"audio", "video"}:
            raise HTTPException(status_code=422, detail="Call mode must be audio or video")
        call_id = str(uuid.uuid4())
        now = _now()
        async with self.engine.begin() as connection:
            await connection.execute(
                insert(editor_calls).values(
                    id=call_id,
                    area_id=area_id,
                    created_by=access.principal.user_id,
                    mode=mode,
                    status="open",
                    created_at=now,
                    expires_at=now + timedelta(hours=8),
                )
            )
        return {
            "id": call_id,
            "area_id": area_id,
            "mode": mode,
            "status": "open",
            "created_by": access.principal.user_id,
            "created_at": now,
            "expires_at": now + timedelta(hours=8),
        }

    async def call_ticket(self, access: EditorAccess, call_id: str) -> str:
        async with self.engine.connect() as connection:
            call = (await connection.execute(select(editor_calls).where(editor_calls.c.id == call_id))).mappings().first()
        if not call or call["status"] != "open" or _utc(call["expires_at"]) <= _now():
            raise HTTPException(status_code=404, detail="Call not found")
        await self.visible_area(access, call["area_id"])
        raw = "jfc_" + secrets.token_urlsafe(40)
        async with self.engine.begin() as connection:
            await connection.execute(
                insert(editor_call_tickets).values(
                    token_hash=_token_hash(raw),
                    call_id=call_id,
                    user_id=access.principal.user_id,
                    expires_at=_now() + timedelta(seconds=self.settings.editor_call_ticket_ttl_seconds),
                    used_at=None,
                )
            )
        return raw

    async def call(self, call_id: str) -> dict[str, Any]:
        async with self.engine.connect() as connection:
            row = (
                (
                    await connection.execute(
                        select(editor_calls, editor_areas.c.project.label("project"))
                        .join(editor_areas, editor_areas.c.id == editor_calls.c.area_id)
                        .where(editor_calls.c.id == call_id)
                    )
                )
                .mappings()
                .first()
            )
        if not row or row["status"] != "open" or _utc(row["expires_at"]) <= _now():
            raise HTTPException(status_code=404, detail="Call not found")
        return dict(row)

    async def consume_call_ticket(self, call_id: str, raw: str) -> tuple[EditorPrincipal, str]:
        if _CALL_TICKET.fullmatch(raw) is None:
            raise HTTPException(status_code=401, detail="Call ticket is invalid or expired")
        now = _now()
        digest = _token_hash(raw)
        async with self.engine.begin() as connection:
            row = (
                (
                    await connection.execute(
                        select(editor_call_tickets).where(
                            and_(editor_call_tickets.c.token_hash == digest, editor_call_tickets.c.call_id == call_id)
                        )
                    )
                )
                .mappings()
                .first()
            )
            if not row or row["used_at"] or _utc(row["expires_at"]) <= now:
                raise HTTPException(status_code=401, detail="Call ticket is invalid or expired")
            consumed = await connection.execute(
                update(editor_call_tickets)
                .where(and_(editor_call_tickets.c.token_hash == digest, editor_call_tickets.c.used_at.is_(None)))
                .values(used_at=now)
            )
            if consumed.rowcount != 1:
                raise HTTPException(status_code=401, detail="Call ticket was already consumed")
            user = (await connection.execute(select(editor_users).where(editor_users.c.id == row["user_id"]))).mappings().first()
        if not user or not user["active"]:
            raise HTTPException(status_code=401, detail="Call member is not active")
        return EditorPrincipal(user["id"], user["username"], user["display_name"], bool(user["is_founder"]), None), str(uuid.uuid4())

    async def join_call(self, call_id: str, principal: EditorPrincipal, connection_id: str) -> list[dict[str, str]]:
        now = _now()
        stale = now - timedelta(seconds=45)
        async with self.engine.begin() as connection:
            await connection.execute(delete(editor_call_participants).where(editor_call_participants.c.last_seen_at < stale))
            peers = (
                (await connection.execute(select(editor_call_participants).where(editor_call_participants.c.call_id == call_id)))
                .mappings()
                .all()
            )
            await connection.execute(
                insert(editor_call_participants).values(
                    connection_id=connection_id,
                    call_id=call_id,
                    user_id=principal.user_id,
                    display_name=principal.display_name,
                    last_seen_at=now,
                )
            )
        return [{"connection_id": row["connection_id"], "display_name": row["display_name"]} for row in peers]

    async def heartbeat_call(self, connection_id: str) -> None:
        async with self.engine.begin() as connection:
            await connection.execute(
                update(editor_call_participants)
                .where(editor_call_participants.c.connection_id == connection_id)
                .values(last_seen_at=_now())
            )

    async def leave_call(self, connection_id: str) -> None:
        async with self.engine.begin() as connection:
            await connection.execute(delete(editor_call_participants).where(editor_call_participants.c.connection_id == connection_id))

    async def signal_call(self, call_id: str, sender: str, target: str | None, payload: dict[str, Any]) -> int:
        now = _now()
        async with self.engine.begin() as connection:
            await connection.execute(delete(editor_call_signals).where(editor_call_signals.c.expires_at <= now))
            result = await connection.execute(
                insert(editor_call_signals).values(
                    call_id=call_id,
                    sender_connection_id=sender,
                    target_connection_id=target,
                    payload=_json(payload),
                    created_at=now,
                    expires_at=now + timedelta(seconds=self.settings.editor_call_signal_ttl_seconds),
                )
            )
        return int(result.inserted_primary_key[0])

    async def current_signal_sequence(self, call_id: str) -> int:
        from sqlalchemy import func

        async with self.engine.connect() as connection:
            value = await connection.scalar(
                select(func.max(editor_call_signals.c.sequence)).where(editor_call_signals.c.call_id == call_id)
            )
        return int(value or 0)

    async def call_signals(self, call_id: str, connection_id: str, after: int) -> list[dict[str, Any]]:
        now = _now()
        statement = (
            select(editor_call_signals)
            .where(
                and_(
                    editor_call_signals.c.call_id == call_id,
                    editor_call_signals.c.sequence > after,
                    editor_call_signals.c.sender_connection_id != connection_id,
                    or_(editor_call_signals.c.target_connection_id.is_(None), editor_call_signals.c.target_connection_id == connection_id),
                    editor_call_signals.c.expires_at > now,
                )
            )
            .order_by(editor_call_signals.c.sequence)
            .limit(100)
        )
        async with self.engine.connect() as connection:
            rows = (await connection.execute(statement)).mappings().all()
        return [{"sequence": row["sequence"], "sender": row["sender_connection_id"], **json.loads(row["payload"])} for row in rows]

    async def audit(
        self,
        principal: EditorPrincipal | None,
        action: str,
        *,
        project: str | None,
        target: str,
        request_id: str,
        detail: dict[str, Any] | None = None,
    ) -> None:
        async with self.engine.begin() as connection:
            await connection.execute(
                insert(editor_audit).values(
                    created_at=_now(),
                    actor_id=principal.user_id if principal else None,
                    action=action[:80],
                    project=project,
                    target=target[:255],
                    request_id=request_id[:64],
                    detail=_json(detail or {}),
                )
            )

    async def list_audit(self, *, project: str | None, limit: int) -> list[dict[str, Any]]:
        statement = select(editor_audit).order_by(editor_audit.c.created_at.desc()).limit(limit)
        if project:
            statement = statement.where(editor_audit.c.project == project)
        async with self.engine.connect() as connection:
            rows = (await connection.execute(statement)).mappings().all()
        return [{**dict(row), "detail": json.loads(row["detail"])} for row in rows]
