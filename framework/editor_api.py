from __future__ import annotations

import asyncio
import hashlib
import hmac
import json
import logging
import mimetypes
import os
import re
import shutil
import tempfile
import time
import uuid
from datetime import datetime
from pathlib import Path, PurePosixPath

from dotenv import dotenv_values
from fastapi import APIRouter, FastAPI, HTTPException, Request, Response, UploadFile, WebSocket, WebSocketDisconnect, status
from fastapi.responses import FileResponse, HTMLResponse
from filelock import FileLock
from filelock import Timeout as FileLockTimeout
from pydantic import BaseModel, ConfigDict, Field, field_validator

from .config import _load_project_dir
from .doctor import is_weak_secret
from .editor_call_client import call_client_page, parse_ice_servers
from .editor_database import browse_rows, database_catalog
from .editor_identity import EDITOR_PERMISSIONS, EditorAccess, EditorIdentityStore, EditorPrincipal
from .protection import client_ip, host_allowed, ip_allowed, request_is_https
from .runtime import ProjectRuntime
from .settings import Settings

log = logging.getLogger("json_api_forge.editor")

EDITOR_PREFIX = "/__forge/editor/v1"
_PROJECT_NAME = re.compile(r"^[A-Za-z0-9](?:[A-Za-z0-9 ._-]{0,62}[A-Za-z0-9])?$")
_PROJECT_SLUG = re.compile(r"^[a-z0-9](?:[a-z0-9-]{0,62}[a-z0-9])?$")
_RESERVED = frozenset({"config", "data", "hooks", "plugins", "schemas"})
_GRAPH_FILE = re.compile(r"^[a-z0-9](?:[a-z0-9._-]{0,94}[a-z0-9])?\.forgegraph\.json$")
_GRAPH_TOKEN = re.compile(r"^[A-Za-z0-9](?:[A-Za-z0-9._:-]{0,126}[A-Za-z0-9])?$")
_GRAPH_TYPE = re.compile(r"^[a-z][a-z0-9]*(?:[._-][a-z0-9]+){1,7}$")
_SAFE_REQUEST_ID = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:-]{0,63}$")
_CALL_TICKET = re.compile(r"^jfc_[A-Za-z0-9_-]{40,80}$")
_CALL_PROTOCOL = "forge-call-v1"


class EditorModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


class DocumentWrite(EditorModel):
    content: str
    expected_sha256: str = Field(min_length=3, max_length=64)

    @field_validator("expected_sha256")
    @classmethod
    def expected_revision_is_exact(cls, value: str) -> str:
        if value != "new" and re.fullmatch(r"[0-9a-f]{64}", value) is None:
            raise ValueError("expected_sha256 must be 'new' or a lowercase SHA-256 digest")
        return value


class ProjectCreate(EditorModel):
    name: str
    slug: str


class FounderCreate(EditorModel):
    username: str = Field(min_length=3, max_length=32)
    password: str = Field(min_length=1, max_length=256)
    display_name: str = Field(min_length=1, max_length=80)


class LoginRequest(EditorModel):
    username: str = Field(min_length=1, max_length=64)
    password: str = Field(min_length=1, max_length=256)


class InvitationRegistration(FounderCreate):
    invitation: str = Field(min_length=32, max_length=512)


class ProfileUpdate(EditorModel):
    display_name: str | None = Field(default=None, max_length=80)
    title: str | None = Field(default=None, max_length=120)
    bio: str | None = Field(default=None, max_length=2000)
    timezone: str | None = Field(default=None, max_length=64)
    status: str | None = Field(default=None, max_length=160)


class RoleWrite(EditorModel):
    name: str = Field(min_length=1, max_length=64)
    rank: int = Field(ge=1, le=999)
    permissions: list[str] = Field(max_length=100)
    document_allow: list[str] = Field(default_factory=list, max_length=100)
    document_deny: list[str] = Field(default_factory=list, max_length=100)
    database_allow: list[str] = Field(default_factory=list, max_length=100)


class InvitationCreate(EditorModel):
    memberships: list[dict[str, str]] = Field(min_length=1, max_length=32)
    expires_hours: int = Field(default=24, ge=1, le=168)


class MemberUpdate(EditorModel):
    memberships: list[dict[str, str]] = Field(max_length=32)
    active: bool = True


class AreaCreate(EditorModel):
    project: str
    name: str = Field(min_length=1, max_length=96)
    description: str = Field(default="", max_length=500)
    visibility: str = "open"
    minimum_rank: int = Field(default=0, ge=0, le=999)
    allowed_role_ids: list[str] = Field(default_factory=list, max_length=100)


class MessageCreate(EditorModel):
    body: str = Field(min_length=1)
    kind: str = "message"


class NoteCreate(EditorModel):
    project: str
    area_id: str | None = None
    title: str = Field(min_length=1, max_length=160)
    body: str = ""
    visibility: str = "open"
    minimum_rank: int = Field(default=0, ge=0, le=999)
    allowed_role_ids: list[str] = Field(default_factory=list, max_length=100)


class CallCreate(EditorModel):
    area_id: str
    mode: str


class _ActionLimiter:
    def __init__(self) -> None:
        self._lock = asyncio.Lock()
        self._buckets: dict[str, tuple[float, int]] = {}

    async def check(self, key: str, *, limit: int, window_seconds: int) -> None:
        now = time.monotonic()
        async with self._lock:
            started, count = self._buckets.get(key, (now, 0))
            if now - started >= window_seconds:
                started, count = now, 0
            if count >= limit:
                retry = max(1, int(window_seconds - (now - started)))
                raise HTTPException(status_code=429, detail="Editor action rate limit exceeded", headers={"Retry-After": str(retry)})
            self._buckets[key] = (started, count + 1)
            if len(self._buckets) > 10_000:
                cutoff = now - max(window_seconds, 300)
                self._buckets = {name: value for name, value in self._buckets.items() if value[0] >= cutoff}


def _csv(value: str) -> list[str]:
    return [part.strip() for part in value.split(",") if part.strip()]


def _digest(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _allowed_project(settings: Settings, name: str) -> bool:
    configured = set(_csv(settings.editor_allowed_projects))
    return not configured or name in configured


def _project_name(name: str) -> str:
    if not _PROJECT_NAME.fullmatch(name) or name.casefold() in _RESERVED or name in {".", ".."}:
        raise HTTPException(status_code=404, detail="Project not found")
    return name


def _document_path(project_dir: Path, raw: str, *, allow_hooks: bool, allow_graphs: bool) -> tuple[Path, str]:
    if not raw or "\\" in raw or "\0" in raw:
        raise HTTPException(status_code=400, detail="Invalid document path")
    relative = PurePosixPath(raw)
    if relative.is_absolute() or any(part in {"", ".", ".."} for part in relative.parts):
        raise HTTPException(status_code=400, detail="Invalid document path")
    normalized = relative.as_posix()
    allowed = normalized == "app.json" or (len(relative.parts) == 2 and relative.parts[0] == "config" and relative.suffix == ".json")
    if allow_graphs:
        allowed = allowed or (
            len(relative.parts) == 2 and relative.parts[0] == "graphs" and _GRAPH_FILE.fullmatch(relative.name) is not None
        )
    if allow_hooks:
        allowed = allowed or (
            len(relative.parts) == 2 and relative.parts[0] == "hooks" and relative.suffix == ".py" and not relative.name.startswith(".")
        )
    if not allowed:
        raise HTTPException(status_code=403, detail="Document type is not allowed by the editor policy")
    target = project_dir.joinpath(*relative.parts)
    try:
        target.resolve(strict=False).relative_to(project_dir.resolve())
    except ValueError as exc:
        raise HTTPException(status_code=400, detail="Invalid document path") from exc
    return target, normalized


def _graph_error(message: str) -> None:
    raise HTTPException(status_code=422, detail=f"Invalid Forge graph: {message}")


def _validate_graph_document(value: object) -> None:
    if not isinstance(value, dict):
        _graph_error("root must be an object")
    allowed_root = {"$schema", "schema_version", "target_document", "nodes", "edges", "metadata"}
    unknown_root = set(value) - allowed_root
    if unknown_root:
        _graph_error(f"unknown root fields: {', '.join(sorted(unknown_root))}")
    if value.get("schema_version") != 1:
        _graph_error("schema_version must be 1")
    target = value.get("target_document")
    if not isinstance(target, str):
        _graph_error("target_document must be a config/*.json path")
    target_path = PurePosixPath(target)
    if (
        target_path.is_absolute()
        or len(target_path.parts) != 2
        or target_path.parts[0] != "config"
        or target_path.suffix != ".json"
        or any(part in {"", ".", ".."} for part in target_path.parts)
        or "\\" in target
    ):
        _graph_error("target_document must be a direct config/*.json path")
    nodes = value.get("nodes")
    edges = value.get("edges")
    if not isinstance(nodes, list) or len(nodes) > 500:
        _graph_error("nodes must be an array with at most 500 entries")
    if not isinstance(edges, list) or len(edges) > 2000:
        _graph_error("edges must be an array with at most 2000 entries")

    node_ids: set[str] = set()
    adjacency: dict[str, set[str]] = {}
    for index, node in enumerate(nodes):
        if not isinstance(node, dict):
            _graph_error(f"node {index} must be an object")
        unknown = set(node) - {"id", "type", "title", "x", "y", "properties"}
        if unknown:
            _graph_error(f"node {index} has unknown fields: {', '.join(sorted(unknown))}")
        node_id = node.get("id")
        node_type = node.get("type")
        if not isinstance(node_id, str) or _GRAPH_TOKEN.fullmatch(node_id) is None:
            _graph_error(f"node {index} has an unsafe id")
        if node_id in node_ids:
            _graph_error(f"duplicate node id: {node_id}")
        if not isinstance(node_type, str) or _GRAPH_TYPE.fullmatch(node_type) is None:
            _graph_error(f"node {node_id} has an unsafe type")
        title = node.get("title", "")
        if not isinstance(title, str) or len(title) > 160 or any(character in title for character in "\r\n\0"):
            _graph_error(f"node {node_id} has an invalid title")
        for coordinate in ("x", "y"):
            raw_coordinate = node.get(coordinate, 0)
            if not isinstance(raw_coordinate, (int, float)) or isinstance(raw_coordinate, bool) or abs(raw_coordinate) > 1_000_000:
                _graph_error(f"node {node_id} has an invalid {coordinate} coordinate")
        if not isinstance(node.get("properties", {}), dict):
            _graph_error(f"node {node_id} properties must be an object")
        node_ids.add(node_id)
        adjacency[node_id] = set()

    edge_ids: set[str] = set()
    incoming: set[tuple[str, str]] = set()
    pairs: set[tuple[str, str, str, str]] = set()
    for index, edge in enumerate(edges):
        if not isinstance(edge, dict):
            _graph_error(f"edge {index} must be an object")
        unknown = set(edge) - {"id", "from_node", "from_port", "to_node", "to_port"}
        if unknown:
            _graph_error(f"edge {index} has unknown fields: {', '.join(sorted(unknown))}")
        fields = {name: edge.get(name) for name in ("id", "from_node", "from_port", "to_node", "to_port")}
        if any(not isinstance(item, str) or _GRAPH_TOKEN.fullmatch(item) is None for item in fields.values()):
            _graph_error(f"edge {index} contains an unsafe id or port")
        edge_id = fields["id"]
        from_node = fields["from_node"]
        to_node = fields["to_node"]
        if edge_id in edge_ids:
            _graph_error(f"duplicate edge id: {edge_id}")
        if from_node not in node_ids or to_node not in node_ids:
            _graph_error(f"edge {edge_id} references a missing node")
        if from_node == to_node:
            _graph_error(f"edge {edge_id} may not connect a node to itself")
        incoming_key = (to_node, fields["to_port"])
        if incoming_key in incoming:
            _graph_error(f"input {to_node}:{fields['to_port']} has more than one connection")
        pair = (from_node, fields["from_port"], to_node, fields["to_port"])
        if pair in pairs:
            _graph_error(f"edge {edge_id} duplicates an existing connection")
        edge_ids.add(edge_id)
        incoming.add(incoming_key)
        pairs.add(pair)
        adjacency[from_node].add(to_node)

    indegree = {node_id: 0 for node_id in node_ids}
    for targets in adjacency.values():
        for target_node in targets:
            indegree[target_node] += 1
    ready = [node_id for node_id, degree in indegree.items() if degree == 0]
    visited = 0
    while ready:
        current = ready.pop()
        visited += 1
        for target_node in adjacency[current]:
            indegree[target_node] -= 1
            if indegree[target_node] == 0:
                ready.append(target_node)
    if visited != len(node_ids):
        _graph_error("execution connections must form an acyclic graph")


class EditorControlPlane:
    def __init__(self, apps_dir: Path, settings: Settings, runtimes: dict[str, ProjectRuntime] | None = None):
        self.apps_dir = apps_dir.resolve()
        self.settings = settings
        self.runtimes = runtimes if runtimes is not None else {}
        self._write_lock = asyncio.Lock()
        self._lock_dir = self.apps_dir.parent / ".forge-editor-locks"

    def _project_dir(self, name: str, *, must_exist: bool = True) -> Path:
        name = _project_name(name)
        if not _allowed_project(self.settings, name):
            raise HTTPException(status_code=404, detail="Project not found")
        project = self.apps_dir / name
        if must_exist and (not project.is_dir() or project.is_symlink()):
            raise HTTPException(status_code=404, detail="Project not found")
        try:
            project.resolve(strict=False).relative_to(self.apps_dir)
        except ValueError as exc:
            raise HTTPException(status_code=404, detail="Project not found") from exc
        return project

    async def authorize_network(self, request: Request | WebSocket) -> None:
        trusted = _csv(self.settings.editor_trusted_proxy_cidrs)
        if self.settings.editor_require_https and not request_is_https(request, trusted):
            raise HTTPException(status_code=400, detail="HTTPS is required for the editor API")
        if not ip_allowed(request, _csv(self.settings.editor_allowed_ips), [], trusted):
            raise HTTPException(status_code=403, detail="Client IP is not allowed for the editor API")
        if not host_allowed(request.headers.get("host"), _csv(self.settings.editor_trusted_hosts)):
            raise HTTPException(status_code=400, detail="Host is not allowed for the editor API")

    def legacy_principal(self, request: Request) -> EditorPrincipal | None:
        if not self.settings.editor_legacy_token_enabled:
            return None
        supplied = request.headers.get("X-Forge-Editor-Token", "")
        if supplied and len(supplied) <= 512 and hmac.compare_digest(supplied, self.settings.editor_token):
            return EditorPrincipal("legacy-operator", "legacy-operator", "Legacy operator", True, None, legacy=True)
        return None

    def runtime_for_project(self, project_name: str) -> ProjectRuntime:
        project = self._project_dir(project_name)
        try:
            manifest = json.loads((project / "app.json").read_text(encoding="utf-8"))
        except (OSError, ValueError) as exc:
            raise HTTPException(status_code=422, detail="Project manifest could not be read") from exc
        runtime = self.runtimes.get(str(manifest.get("slug", "")))
        if runtime is None:
            raise HTTPException(status_code=503, detail="Project runtime is not ready")
        return runtime

    def _assert_no_symlinks(self, project: Path) -> None:
        for directory in (project, project / "config", project / "hooks", project / "graphs"):
            if directory.exists() and directory.is_symlink():
                raise HTTPException(status_code=409, detail="Editor projects may not contain symlinked control directories")
        candidates = [project / "app.json"]
        for directory, pattern in ((project / "config", "*.json"), (project / "hooks", "*.py"), (project / "graphs", "*.forgegraph.json")):
            if directory.is_dir():
                candidates.extend(directory.glob(pattern))
        if any(path.is_symlink() for path in candidates):
            raise HTTPException(status_code=409, detail="Editor projects may not contain symlinked documents")

    def _stage_project(self, project: Path, destination: Path) -> None:
        self._assert_no_symlinks(project)
        destination.mkdir(parents=True)
        manifest = project / "app.json"
        if not manifest.is_file():
            raise HTTPException(status_code=404, detail="Project manifest not found")
        shutil.copyfile(manifest, destination / "app.json", follow_symlinks=False)
        for folder, pattern in (("config", "*.json"), ("hooks", "*.py"), ("graphs", "*.forgegraph.json")):
            source_dir = project / folder
            target_dir = destination / folder
            target_dir.mkdir()
            if not source_dir.is_dir():
                continue
            for source in source_dir.glob(pattern):
                if not source.is_file() or source.is_symlink():
                    continue
                shutil.copyfile(source, target_dir / source.name, follow_symlinks=False)

    async def _acquire_project_lock(self, project: Path) -> FileLock:
        self._lock_dir.mkdir(mode=0o700, parents=True, exist_ok=True)
        name = hashlib.sha256(str(project).encode("utf-8")).hexdigest() + ".lock"
        lock = FileLock(self._lock_dir / name, timeout=10)
        try:
            await asyncio.to_thread(lock.acquire)
        except FileLockTimeout as exc:
            raise HTTPException(status_code=503, detail="Project is busy; retry the save", headers={"Retry-After": "1"}) from exc
        return lock

    def list_projects(self) -> list[dict]:
        if not self.apps_dir.exists():
            return []
        projects = []
        for path in sorted(self.apps_dir.iterdir(), key=lambda value: value.name.casefold()):
            if not path.is_dir() or path.is_symlink() or not _allowed_project(self.settings, path.name):
                continue
            if path.name.casefold() in _RESERVED or path.name.startswith((".", "_")):
                continue
            manifest = path / "app.json"
            if not manifest.is_file():
                continue
            try:
                raw = json.loads(manifest.read_text(encoding="utf-8"))
            except (OSError, ValueError):
                raw = {}
            projects.append({"directory": path.name, "name": raw.get("name", path.name), "slug": raw.get("slug", "")})
        return projects

    def list_documents(self, project_name: str) -> list[dict]:
        project = self._project_dir(project_name)
        self._assert_no_symlinks(project)
        candidates = [project / "app.json", *sorted((project / "config").glob("*.json"))]
        if self.settings.editor_allow_hooks:
            candidates.extend(sorted((project / "hooks").glob("*.py")))
        if self.settings.editor_allow_graphs:
            candidates.extend(sorted((project / "graphs").glob("*.forgegraph.json")))
        documents = []
        for path in candidates:
            if not path.is_file() or path.is_symlink():
                continue
            data = path.read_bytes()
            documents.append(
                {
                    "path": path.relative_to(project).as_posix(),
                    "size": len(data),
                    "sha256": _digest(data),
                    "editable": not self.settings.editor_read_only,
                }
            )
        return documents

    def read_document(self, project_name: str, raw_path: str) -> tuple[str, str]:
        project = self._project_dir(project_name)
        self._assert_no_symlinks(project)
        path, _ = _document_path(
            project,
            raw_path,
            allow_hooks=self.settings.editor_allow_hooks,
            allow_graphs=self.settings.editor_allow_graphs,
        )
        if not path.is_file() or path.is_symlink():
            raise HTTPException(status_code=404, detail="Document not found")
        size = path.stat(follow_symlinks=False).st_size
        if size > self.settings.editor_max_document_bytes:
            raise HTTPException(status_code=413, detail="Document exceeds the editor policy limit")
        data = path.read_bytes()
        try:
            return data.decode("utf-8"), _digest(data)
        except UnicodeDecodeError as exc:
            raise HTTPException(status_code=415, detail="Editor documents must be UTF-8 text") from exc

    def validate_project(self, project_name: str) -> dict:
        project = self._project_dir(project_name)
        configured = _load_project_dir(project, dotenv=dotenv_values(self.apps_dir.parent / ".env"))
        return {
            "valid": True,
            "slug": configured.slug,
            "resources": len(configured.resources),
            "operations": len(configured.operations),
            "data_sources": len(configured.data_sources),
        }

    async def write_document(self, project_name: str, raw_path: str, payload: DocumentWrite) -> str:
        if self.settings.editor_read_only:
            raise HTTPException(status_code=403, detail="Editor API is read-only")
        data = payload.content.encode("utf-8")
        if len(data) > self.settings.editor_max_document_bytes:
            raise HTTPException(status_code=413, detail="Document exceeds the editor policy limit")
        project = self._project_dir(project_name)
        target, normalized = _document_path(
            project,
            raw_path,
            allow_hooks=self.settings.editor_allow_hooks,
            allow_graphs=self.settings.editor_allow_graphs,
        )
        if normalized.endswith(".json"):
            try:
                parsed = json.loads(payload.content)
            except json.JSONDecodeError as exc:
                raise HTTPException(status_code=422, detail=f"Invalid JSON: {exc.msg} at line {exc.lineno}, column {exc.colno}") from exc
            if not isinstance(parsed, dict):
                raise HTTPException(status_code=422, detail="JSON document root must be an object")
            if normalized.startswith("graphs/"):
                _validate_graph_document(parsed)

        async with self._write_lock:
            lock = await self._acquire_project_lock(project)
            try:
                self._assert_no_symlinks(project)
                exists = target.is_file() and not target.is_symlink()
                if exists:
                    current = target.read_bytes()
                    if payload.expected_sha256 != _digest(current):
                        raise HTTPException(status_code=409, detail="Document changed on the server; reload before saving")
                elif payload.expected_sha256 != "new":
                    raise HTTPException(status_code=409, detail="Document no longer exists; reload before saving")
                elif target.exists():
                    raise HTTPException(status_code=409, detail="Document target is not a regular file")

                with tempfile.TemporaryDirectory(prefix="forge-editor-") as temp_root:
                    staged = Path(temp_root) / project.name
                    self._stage_project(project, staged)
                    staged_target, _ = _document_path(
                        staged,
                        normalized,
                        allow_hooks=self.settings.editor_allow_hooks,
                        allow_graphs=self.settings.editor_allow_graphs,
                    )
                    staged_target.parent.mkdir(parents=True, exist_ok=True)
                    staged_target.write_bytes(data)
                    try:
                        _load_project_dir(staged, dotenv=dotenv_values(self.apps_dir.parent / ".env"))
                    except RuntimeError as exc:
                        raise HTTPException(status_code=422, detail=str(exc)) from exc

                target.parent.mkdir(parents=True, exist_ok=True)
                fd, temporary_name = tempfile.mkstemp(prefix=f".{target.name}.", dir=target.parent)
                temporary = Path(temporary_name)
                try:
                    with os.fdopen(fd, "wb") as handle:
                        handle.write(data)
                        handle.flush()
                        os.fsync(handle.fileno())
                    os.replace(temporary, target)
                    if hasattr(os, "O_DIRECTORY"):
                        directory_fd = os.open(target.parent, os.O_RDONLY | os.O_DIRECTORY)
                        try:
                            os.fsync(directory_fd)
                        finally:
                            os.close(directory_fd)
                finally:
                    temporary.unlink(missing_ok=True)
            finally:
                await asyncio.to_thread(lock.release)
        digest = _digest(data)
        log.info("editor write project=%s document=%s client_policy=authorized sha256=%s", project.name, normalized, digest[:12])
        return digest

    async def create_project(self, payload: ProjectCreate) -> dict:
        if self.settings.editor_read_only or not self.settings.editor_allow_create_projects:
            raise HTTPException(status_code=403, detail="Project creation is disabled by the editor policy")
        if not _PROJECT_NAME.fullmatch(payload.name) or payload.name.casefold() in _RESERVED:
            raise HTTPException(status_code=422, detail="Invalid project directory name")
        if not _PROJECT_SLUG.fullmatch(payload.slug):
            raise HTTPException(status_code=422, detail="Invalid project slug")
        if not _allowed_project(self.settings, payload.name):
            raise HTTPException(status_code=403, detail="Project is outside the editor allowlist")
        target = self._project_dir(payload.name, must_exist=False)
        async with self._write_lock:
            if target.exists():
                raise HTTPException(status_code=409, detail="Project already exists")
            self.apps_dir.mkdir(parents=True, exist_ok=True)
            temporary = Path(tempfile.mkdtemp(prefix=f".{payload.name}.", dir=self.apps_dir))
            try:
                (temporary / "config").mkdir()
                (temporary / "hooks").mkdir()
                (temporary / "graphs").mkdir()
                (temporary / "app.json").write_text(
                    json.dumps(
                        {
                            "$schema": "../../schemas/manifest.schema.json",
                            "slug": payload.slug,
                            "name": payload.name,
                            "version": "1.0.0",
                            "api_prefix": f"/api/{payload.slug}/v1",
                        },
                        indent=2,
                    )
                    + "\n",
                    encoding="utf-8",
                )
                (temporary / "config" / "10-databases.json").write_text(
                    json.dumps(
                        {
                            "$schema": "../../../schemas/fragment.schema.json",
                            "databases": {
                                "primary": {
                                    "url": f"$env:{payload.slug.upper().replace('-', '_')}_DATABASE_URL:-sqlite+aiosqlite:///./data/{payload.slug}.db"
                                }
                            },
                        },
                        indent=2,
                    )
                    + "\n",
                    encoding="utf-8",
                )
                _load_project_dir(temporary, dotenv=dotenv_values(self.apps_dir.parent / ".env"))
                os.replace(temporary, target)
            except BaseException:
                shutil.rmtree(temporary, ignore_errors=True)
                raise
        log.info("editor project created project=%s slug=%s", payload.name, payload.slug)
        return {"directory": payload.name, "name": payload.name, "slug": payload.slug}


def register_editor_api(
    app: FastAPI,
    *,
    apps_dir: Path,
    settings: Settings,
    runtimes: dict[str, ProjectRuntime] | None = None,
) -> None:
    if not settings.editor_api_enabled:
        return
    if (settings.editor_setup_enabled or settings.editor_legacy_token_enabled) and is_weak_secret(settings.editor_token, minimum_length=32):
        raise RuntimeError("EDITOR_API_ENABLED=true requires a strong, independent EDITOR_TOKEN of at least 32 characters")
    if settings.app_env.casefold() == "production" and not settings.editor_require_https:
        raise RuntimeError("Production editor API requires EDITOR_REQUIRE_HTTPS=true")
    if settings.app_env.casefold() == "production" and settings.editor_legacy_token_enabled:
        raise RuntimeError("Production editor API does not allow EDITOR_LEGACY_TOKEN_ENABLED=true")
    if settings.editor_calls_enabled and not settings.editor_collaboration_enabled:
        raise RuntimeError("EDITOR_CALLS_ENABLED requires EDITOR_COLLABORATION_ENABLED")
    ice_servers = parse_ice_servers(settings.editor_call_ice_servers_json) if settings.editor_calls_enabled else []

    control = EditorControlPlane(apps_dir, settings, runtimes)
    router = APIRouter(prefix=EDITOR_PREFIX, dependencies=[])
    action_limiter = _ActionLimiter()

    def store_for(connection: Request | WebSocket) -> EditorIdentityStore:
        engine = getattr(connection.app.state, "internal_engine", None)
        if engine is None:
            raise HTTPException(status_code=503, detail="Editor identity store is not ready")
        return EditorIdentityStore(engine, settings)

    async def principal_for(request: Request) -> EditorPrincipal:
        await control.authorize_network(request)
        legacy = control.legacy_principal(request)
        if legacy is not None:
            return legacy
        authorization = request.headers.get("authorization", "")
        scheme, separator, token = authorization.partition(" ")
        if separator != " " or scheme.casefold() != "bearer" or not token:
            raise HTTPException(status_code=401, detail="Editor authentication required")
        return await store_for(request).authenticate(token, request.headers.get("user-agent", ""))

    async def access_for_principal(request: Request, principal: EditorPrincipal, permission: str, project: str = "*") -> EditorAccess:
        if principal.legacy:
            access = EditorAccess(principal, frozenset({"*"}), frozenset(), frozenset({"Founder"}), 1000, ("*",), (), ("*",))
        else:
            access = await store_for(request).access(principal, project)
        if not access.permits(permission):
            raise HTTPException(status_code=403, detail=f"Missing Editor permission: {permission}")
        return access

    async def access_for(request: Request, permission: str, project: str = "*") -> EditorAccess:
        return await access_for_principal(request, await principal_for(request), permission, project)

    def request_id(request: Request) -> str:
        candidate = str(getattr(request.state, "request_id", ""))
        return candidate if _SAFE_REQUEST_ID.fullmatch(candidate) else str(uuid.uuid4())

    async def throttle(access: EditorAccess, action: str, *, limit: int, window: int = 60) -> None:
        await action_limiter.check(f"{access.principal.user_id}:{action}", limit=limit, window_seconds=window)

    @router.get("/setup/status")
    async def setup_status(request: Request, response: Response):
        await control.authorize_network(request)
        response.headers["Cache-Control"] = "no-store"
        initialized = await store_for(request).initialized()
        return {
            "initialized": initialized,
            "setup_enabled": settings.editor_setup_enabled and not initialized,
            "account_authentication": True,
            "api_version": 2,
        }

    @router.post("/setup/founder", status_code=status.HTTP_201_CREATED)
    async def setup_founder(payload: FounderCreate, request: Request, response: Response):
        await control.authorize_network(request)
        if not settings.editor_setup_enabled:
            raise HTTPException(status_code=404, detail="Founder setup is disabled")
        supplied = request.headers.get("X-Forge-Setup-Token", "")
        if not supplied or len(supplied) > 512 or not hmac.compare_digest(supplied, settings.editor_token):
            raise HTTPException(status_code=401, detail="Valid one-time setup token required")
        store = store_for(request)
        principal, token = await store.bootstrap_founder(
            username=payload.username,
            password=payload.password,
            display_name=payload.display_name,
            user_agent=request.headers.get("user-agent", ""),
        )
        await store.audit(principal, "founder.bootstrap", project=None, target=principal.user_id, request_id=request_id(request))
        response.headers["Cache-Control"] = "no-store"
        return {
            "access_token": token,
            "token_type": "Bearer",
            "expires_in": settings.editor_session_ttl_seconds,
            "profile": await store.profile(principal.user_id),
        }

    @router.post("/auth/login")
    async def login(payload: LoginRequest, request: Request, response: Response):
        await control.authorize_network(request)
        store = store_for(request)
        principal, token = await store.login(
            username=payload.username,
            password=payload.password,
            client_ip=client_ip(request, _csv(settings.editor_trusted_proxy_cidrs)),
            user_agent=request.headers.get("user-agent", ""),
        )
        await store.audit(principal, "auth.login", project=None, target=principal.user_id, request_id=request_id(request))
        response.headers["Cache-Control"] = "no-store"
        return {
            "access_token": token,
            "token_type": "Bearer",
            "expires_in": settings.editor_session_ttl_seconds,
            "profile": await store.profile(principal.user_id),
        }

    @router.post("/auth/register", status_code=status.HTTP_201_CREATED)
    async def register(payload: InvitationRegistration, request: Request, response: Response):
        await control.authorize_network(request)
        effective_ip = client_ip(request, _csv(settings.editor_trusted_proxy_cidrs))
        await action_limiter.check(f"register:{effective_ip}", limit=10, window_seconds=60)
        store = store_for(request)
        principal, token = await store.accept_invitation(
            invitation=payload.invitation,
            username=payload.username,
            password=payload.password,
            display_name=payload.display_name,
            user_agent=request.headers.get("user-agent", ""),
        )
        await store.audit(principal, "member.register", project=None, target=principal.user_id, request_id=request_id(request))
        response.headers["Cache-Control"] = "no-store"
        return {
            "access_token": token,
            "token_type": "Bearer",
            "expires_in": settings.editor_session_ttl_seconds,
            "profile": await store.profile(principal.user_id),
        }

    @router.post("/auth/logout", status_code=status.HTTP_204_NO_CONTENT)
    async def logout(request: Request):
        principal = await principal_for(request)
        if not principal.legacy:
            await store_for(request).logout(principal)
        return Response(status_code=status.HTTP_204_NO_CONTENT)

    @router.get("/capabilities")
    async def capabilities(request: Request):
        access = await access_for(request, "projects.read")
        return {
            "api_version": 2,
            "read_only": settings.editor_read_only,
            "allow_create_projects": settings.editor_allow_create_projects and access.permits("projects.create"),
            "allow_hooks": settings.editor_allow_hooks and access.permits("documents.hooks.write"),
            "allow_graphs": settings.editor_allow_graphs and access.permits("documents.graphs.write"),
            "database_browser": settings.editor_database_browser_enabled and access.permits("databases.metadata.read"),
            "collaboration": settings.editor_collaboration_enabled and access.permits("areas.read"),
            "calls": settings.editor_calls_enabled and access.permits("calls.join"),
            "graph_schema_version": 1,
            "max_document_bytes": settings.editor_max_document_bytes,
            "max_attachment_bytes": settings.editor_max_attachment_bytes,
            "authentication": "Bearer session",
            "optimistic_concurrency": "sha256",
            "cross_process_locking": True,
            "permissions": sorted(access.permissions),
            "permission_catalog": sorted(EDITOR_PERMISSIONS) if access.permits("roles.manage") else [],
            "roles": sorted(access.role_names),
            "rank": access.rank,
        }

    @router.get("/me")
    async def me(request: Request):
        access = await access_for(request, "profiles.read")
        if access.principal.legacy:
            return {
                "id": access.principal.user_id,
                "username": access.principal.username,
                "display_name": access.principal.display_name,
                "is_founder": True,
                "legacy": True,
            }
        return await store_for(request).profile(access.principal.user_id)

    @router.patch("/me")
    async def update_me(payload: ProfileUpdate, request: Request):
        access = await access_for(request, "profiles.write.own")
        profile = await store_for(request).update_profile(access.principal, payload.model_dump(exclude_none=True))
        await store_for(request).audit(
            access.principal, "profile.update", project=None, target=access.principal.user_id, request_id=request_id(request)
        )
        return profile

    @router.get("/roles")
    async def roles(request: Request):
        await access_for(request, "roles.read")
        return {"roles": await store_for(request).list_roles()}

    @router.post("/roles", status_code=status.HTTP_201_CREATED)
    async def create_role(payload: RoleWrite, request: Request):
        access = await access_for(request, "roles.manage")
        await throttle(access, "roles.write", limit=30)
        role = await store_for(request).save_role(access, payload.model_dump())
        await store_for(request).audit(
            access.principal,
            "role.create",
            project=None,
            target=role["id"],
            request_id=request_id(request),
            detail={"name": role["name"], "rank": role["rank"]},
        )
        return role

    @router.put("/roles/{role_id}")
    async def update_role(role_id: str, payload: RoleWrite, request: Request):
        access = await access_for(request, "roles.manage")
        await throttle(access, "roles.write", limit=30)
        role = await store_for(request).save_role(access, payload.model_dump(), role_id=role_id)
        await store_for(request).audit(
            access.principal,
            "role.update",
            project=None,
            target=role_id,
            request_id=request_id(request),
            detail={"name": role["name"], "rank": role["rank"]},
        )
        return role

    @router.get("/members")
    async def members(request: Request):
        await access_for(request, "members.read")
        return {"members": await store_for(request).list_members()}

    @router.put("/members/{user_id}", status_code=status.HTTP_204_NO_CONTENT)
    async def update_member(user_id: str, payload: MemberUpdate, request: Request):
        access = await access_for(request, "members.manage")
        await throttle(access, "members.write", limit=30)
        await store_for(request).replace_memberships(access, user_id, payload.memberships, active=payload.active)
        await store_for(request).audit(
            access.principal,
            "member.update",
            project=None,
            target=user_id,
            request_id=request_id(request),
            detail={"active": payload.active},
        )
        return Response(status_code=status.HTTP_204_NO_CONTENT)

    @router.post("/invitations", status_code=status.HTTP_201_CREATED)
    async def invitation(payload: InvitationCreate, request: Request, response: Response):
        access = await access_for(request, "invitations.manage")
        await throttle(access, "invitations.write", limit=30)
        token = await store_for(request).create_invitation(access, payload.memberships, payload.expires_hours)
        await store_for(request).audit(
            access.principal,
            "invitation.create",
            project=None,
            target="invitation",
            request_id=request_id(request),
            detail={"memberships": payload.memberships, "expires_hours": payload.expires_hours},
        )
        response.headers["Cache-Control"] = "no-store"
        return {"invitation": token, "expires_hours": payload.expires_hours}

    @router.get("/projects")
    async def projects(request: Request):
        principal = await principal_for(request)
        store = store_for(request) if not principal.legacy else None
        visible = []
        for project in control.list_projects():
            access = (
                EditorAccess(principal, frozenset({"*"}), frozenset(), frozenset({"Founder"}), 1000, ("*",), (), ("*",))
                if principal.legacy
                else await store.access(principal, project["directory"])
            )
            if access.permits("projects.read"):
                visible.append(project)
        return {"projects": visible}

    @router.post("/projects", status_code=status.HTTP_201_CREATED)
    async def create_project(payload: ProjectCreate, request: Request):
        access = await access_for(request, "projects.create")
        await throttle(access, "projects.create", limit=20)
        created = await control.create_project(payload)
        if not access.principal.legacy:
            await store_for(request).audit(
                access.principal, "project.create", project=created["directory"], target=created["slug"], request_id=request_id(request)
            )
        return created

    @router.get("/projects/{project_name}/documents")
    async def documents(project_name: str, request: Request):
        access = await access_for(request, "documents.read", project_name)
        documents = control.list_documents(project_name)
        for document in documents:
            document["editable"] = bool(
                document["editable"] and access.permits("documents.write") and access.permits_document(document["path"])
            )
        return {"documents": [item for item in documents if access.permits_document(item["path"])]}

    @router.get("/projects/{project_name}/documents/{document_path:path}")
    async def read_document(project_name: str, document_path: str, request: Request, response: Response):
        access = await access_for(request, "documents.read", project_name)
        if not access.permits_document(document_path):
            raise HTTPException(status_code=404, detail="Document not found")
        content, digest = control.read_document(project_name, document_path)
        response.headers["ETag"] = f'"{digest}"'
        return {"path": document_path, "content": content, "sha256": digest}

    @router.put("/projects/{project_name}/documents/{document_path:path}")
    async def write_document(project_name: str, document_path: str, payload: DocumentWrite, request: Request, response: Response):
        access = await access_for(request, "documents.write", project_name)
        await throttle(access, "documents.write", limit=120)
        if not access.permits_document(document_path):
            raise HTTPException(status_code=403, detail="Role policy does not allow this document")
        if document_path.startswith("hooks/") and not access.permits("documents.hooks.write"):
            raise HTTPException(status_code=403, detail="Missing Editor permission: documents.hooks.write")
        if document_path.startswith("graphs/") and not access.permits("documents.graphs.write"):
            raise HTTPException(status_code=403, detail="Missing Editor permission: documents.graphs.write")
        digest = await control.write_document(project_name, document_path, payload)
        if not access.principal.legacy:
            await store_for(request).audit(
                access.principal,
                "document.write",
                project=project_name,
                target=document_path,
                request_id=request_id(request),
                detail={"sha256": digest},
            )
        response.headers["ETag"] = f'"{digest}"'
        return {"path": document_path, "sha256": digest, "valid": True}

    @router.post("/projects/{project_name}/validate")
    async def validate_project(project_name: str, request: Request):
        access = await access_for(request, "projects.validate", project_name)
        try:
            return control.validate_project(project_name)
        except RuntimeError as exc:
            if not access.principal.is_founder and "*" not in access.document_allow:
                raise HTTPException(status_code=422, detail="Project validation failed in a restricted document") from exc
            raise HTTPException(status_code=422, detail=str(exc)) from exc

    @router.get("/projects/{project_name}/databases")
    async def databases(project_name: str, request: Request):
        if not settings.editor_database_browser_enabled:
            raise HTTPException(status_code=404, detail="Database browser is disabled")
        access = await access_for(request, "databases.metadata.read", project_name)
        expose = settings.editor_database_expose_undeclared and access.permits("databases.undeclared.read")
        return database_catalog(control.runtime_for_project(project_name), access, expose_undeclared=expose)

    @router.get("/projects/{project_name}/databases/{alias}/tables/{table_name}/rows")
    async def database_rows(project_name: str, alias: str, table_name: str, request: Request, limit: int = 100, offset: int = 0):
        if not settings.editor_database_browser_enabled:
            raise HTTPException(status_code=404, detail="Database browser is disabled")
        access = await access_for(request, "databases.rows.read", project_name)
        await throttle(access, "databases.rows.read", limit=180)
        if not 1 <= limit <= settings.editor_database_row_limit or not 0 <= offset <= 100_000:
            raise HTTPException(status_code=422, detail="Database pagination is outside the server policy")
        payload = await browse_rows(
            control.runtime_for_project(project_name),
            access,
            alias=alias,
            table_name=table_name,
            limit=limit,
            offset=offset,
            expose_undeclared=settings.editor_database_expose_undeclared,
        )
        if not access.principal.legacy:
            await store_for(request).audit(
                access.principal,
                "database.rows.read",
                project=project_name,
                target=f"{alias}:{table_name}",
                request_id=request_id(request),
                detail={"limit": limit, "offset": offset},
            )
        return payload

    @router.get("/areas")
    async def areas(request: Request, project: str = "*"):
        if not settings.editor_collaboration_enabled:
            raise HTTPException(status_code=404, detail="Collaboration is disabled")
        access = await access_for(request, "areas.read", project)
        return {"areas": await store_for(request).list_areas(access, project)}

    @router.post("/areas", status_code=status.HTTP_201_CREATED)
    async def create_area(payload: AreaCreate, request: Request):
        access = await access_for(request, "areas.manage", payload.project)
        await throttle(access, "areas.write", limit=30)
        area = await store_for(request).create_area(access, payload.model_dump())
        await store_for(request).audit(
            access.principal,
            "area.create",
            project=payload.project,
            target=area["id"],
            request_id=request_id(request),
            detail={"visibility": area["visibility"]},
        )
        return area

    @router.get("/areas/{area_id}/messages")
    async def messages(area_id: str, request: Request, limit: int = 100, before: datetime | None = None):
        principal = await principal_for(request)
        project = await store_for(request).area_project(area_id)
        access = await access_for_principal(request, principal, "messages.read", project)
        if not 1 <= limit <= 200:
            raise HTTPException(status_code=422, detail="Message limit must be between 1 and 200")
        return {"messages": await store_for(request).list_messages(access, area_id, before=before, limit=limit)}

    @router.post("/areas/{area_id}/messages", status_code=status.HTTP_201_CREATED)
    async def post_message(area_id: str, payload: MessageCreate, request: Request):
        principal = await principal_for(request)
        project = await store_for(request).area_project(area_id)
        access = await access_for_principal(request, principal, "messages.write", project)
        await throttle(access, "messages.write", limit=60)
        if payload.kind == "announcement" and not access.permits("areas.manage"):
            raise HTTPException(status_code=403, detail="Announcements require areas.manage")
        message = await store_for(request).post_message(access, area_id, payload.body, payload.kind)
        await store_for(request).audit(
            access.principal,
            "message.create",
            project=None,
            target=area_id,
            request_id=request_id(request),
            detail={"message_id": message["id"], "kind": payload.kind},
        )
        return message

    @router.get("/notes")
    async def notes(request: Request, project: str = "*"):
        access = await access_for(request, "notes.read", project)
        return {"notes": await store_for(request).list_notes(access, project)}

    @router.post("/notes", status_code=status.HTTP_201_CREATED)
    async def create_note(payload: NoteCreate, request: Request):
        access = await access_for(request, "notes.write", payload.project)
        await throttle(access, "notes.write", limit=30)
        note = await store_for(request).create_note(access, payload.model_dump())
        await store_for(request).audit(
            access.principal,
            "note.create",
            project=payload.project,
            target=note["id"],
            request_id=request_id(request),
            detail={"visibility": payload.visibility},
        )
        return note

    @router.post("/areas/{area_id}/attachments", status_code=status.HTTP_201_CREATED)
    async def upload_attachment(area_id: str, request: Request, upload: UploadFile):
        principal = await principal_for(request)
        project = await store_for(request).area_project(area_id)
        access = await access_for_principal(request, principal, "attachments.write", project)
        await throttle(access, "attachments.write", limit=10)
        await store_for(request).visible_area(access, area_id)
        raw_name = upload.filename or ""
        if (
            not raw_name
            or raw_name != Path(raw_name).name
            or "\\" in raw_name
            or any(ord(character) < 32 for character in raw_name)
            or len(raw_name) > 255
        ):
            raise HTTPException(status_code=422, detail="Attachment filename is invalid")
        root = settings.editor_attachment_dir
        if not root.is_absolute():
            root = control.apps_dir.parent / root
        root.mkdir(mode=0o700, parents=True, exist_ok=True)
        if root.is_symlink() or not root.is_dir():
            raise HTTPException(status_code=503, detail="Attachment storage is not a safe directory")
        root = root.resolve()
        stored_name = f"{uuid.uuid4()}.blob"
        target = root / stored_name
        fd, temporary_name = tempfile.mkstemp(prefix=".upload-", dir=root)
        temporary = Path(temporary_name)
        digest = hashlib.sha256()
        size = 0
        try:
            with os.fdopen(fd, "wb") as handle:
                while chunk := await upload.read(1024 * 1024):
                    size += len(chunk)
                    if size > settings.editor_max_attachment_bytes:
                        raise HTTPException(status_code=413, detail="Attachment exceeds the server policy limit")
                    digest.update(chunk)
                    handle.write(chunk)
                handle.flush()
                os.fsync(handle.fileno())
            os.replace(temporary, target)
            content_type = (upload.content_type or mimetypes.guess_type(raw_name)[0] or "application/octet-stream")[:160]
            if any(character in content_type for character in "\r\n\0"):
                content_type = "application/octet-stream"
            try:
                metadata = await store_for(request).register_attachment(
                    access,
                    area_id=area_id,
                    original_name=raw_name,
                    stored_name=stored_name,
                    content_type=content_type,
                    size=size,
                    sha256=digest.hexdigest(),
                )
            except BaseException:
                target.unlink(missing_ok=True)
                raise
        finally:
            temporary.unlink(missing_ok=True)
            await upload.close()
        await store_for(request).audit(
            access.principal,
            "attachment.create",
            project=None,
            target=metadata["id"],
            request_id=request_id(request),
            detail={"size": size, "sha256": digest.hexdigest()},
        )
        return metadata

    @router.get("/areas/{area_id}/attachments")
    async def attachments(area_id: str, request: Request, limit: int = 100):
        principal = await principal_for(request)
        project = await store_for(request).area_project(area_id)
        access = await access_for_principal(request, principal, "attachments.read", project)
        if not 1 <= limit <= 200:
            raise HTTPException(status_code=422, detail="Attachment limit must be between 1 and 200")
        return {"attachments": await store_for(request).list_attachments(access, area_id, limit=limit)}

    @router.get("/attachments/{attachment_id}", response_class=FileResponse)
    async def download_attachment(attachment_id: str, request: Request):
        principal = await principal_for(request)
        project = await store_for(request).attachment_project(attachment_id)
        access = await access_for_principal(request, principal, "attachments.read", project)
        metadata = await store_for(request).attachment(access, attachment_id)
        root = settings.editor_attachment_dir
        if not root.is_absolute():
            root = control.apps_dir.parent / root
        if root.is_symlink() or not root.is_dir():
            raise HTTPException(status_code=404, detail="Attachment file not found")
        target = root / metadata["stored_name"]
        try:
            target.resolve(strict=True).relative_to(root.resolve(strict=True))
        except (OSError, ValueError) as exc:
            raise HTTPException(status_code=404, detail="Attachment file not found") from exc
        if not target.is_file() or target.is_symlink() or target.stat().st_size != metadata["size"]:
            raise HTTPException(status_code=404, detail="Attachment file not found")
        return FileResponse(
            target,
            media_type=metadata["content_type"],
            filename=metadata["original_name"],
            headers={"Cache-Control": "private, no-store", "X-Content-Type-Options": "nosniff"},
        )

    @router.post("/calls", status_code=status.HTTP_201_CREATED)
    async def create_call(payload: CallCreate, request: Request):
        if not settings.editor_calls_enabled:
            raise HTTPException(status_code=404, detail="Calls are disabled")
        principal = await principal_for(request)
        project = await store_for(request).area_project(payload.area_id)
        access = await access_for_principal(request, principal, "calls.start", project)
        await throttle(access, "calls.start", limit=20)
        call = await store_for(request).create_call(access, payload.area_id, payload.mode)
        await store_for(request).audit(
            access.principal, "call.create", project=None, target=call["id"], request_id=request_id(request), detail={"mode": payload.mode}
        )
        return call

    @router.post("/calls/{call_id}/ticket")
    async def create_call_ticket(call_id: str, request: Request, response: Response):
        if not settings.editor_calls_enabled:
            raise HTTPException(status_code=404, detail="Calls are disabled")
        principal = await principal_for(request)
        call = await store_for(request).call(call_id)
        access = await access_for_principal(request, principal, "calls.join", call["project"])
        await throttle(access, "calls.ticket", limit=30)
        ticket = await store_for(request).call_ticket(access, call_id)
        response.headers["Cache-Control"] = "no-store"
        return {
            "ticket": ticket,
            "call_client_path": f"{EDITOR_PREFIX}/call-client/{call_id}",
            "expires_in": settings.editor_call_ticket_ttl_seconds,
        }

    @router.get("/call-client/{call_id}", response_class=HTMLResponse, include_in_schema=False)
    async def call_client(call_id: str, request: Request):
        await control.authorize_network(request)
        document, nonce = call_client_page()
        return HTMLResponse(
            document,
            headers={
                "Cache-Control": "no-store",
                "Content-Security-Policy": f"default-src 'none'; script-src 'nonce-{nonce}'; style-src 'unsafe-inline'; connect-src 'self' wss: ws:; media-src 'self' blob:; img-src 'self' data:; base-uri 'none'; frame-ancestors 'self'",
                "Permissions-Policy": "camera=(self), microphone=(self), geolocation=()",
                "Referrer-Policy": "no-referrer",
            },
        )

    @router.websocket("/ws/calls/{call_id}")
    async def call_socket(websocket: WebSocket, call_id: str):
        connection_id = ""
        store: EditorIdentityStore | None = None
        try:
            await control.authorize_network(websocket)
            offered_protocols = [item.strip() for item in websocket.headers.get("sec-websocket-protocol", "").split(",") if item.strip()]
            if (
                "ticket" in websocket.query_params
                or len(offered_protocols) != 2
                or offered_protocols[0] != _CALL_PROTOCOL
                or _CALL_TICKET.fullmatch(offered_protocols[1]) is None
            ):
                raise HTTPException(status_code=401, detail="Call ticket is missing or invalid")
            store = store_for(websocket)
            principal, connection_id = await store.consume_call_ticket(call_id, offered_protocols[1])
            call = await store.call(call_id)
            area_access = await store.access(principal, call["project"])
            if not area_access.permits("calls.join"):
                raise HTTPException(status_code=403, detail="Missing Editor permission: calls.join")
            await store.visible_area(area_access, call["area_id"])
        except HTTPException:
            await websocket.close(code=1008)
            return

        await websocket.accept(subprotocol=_CALL_PROTOCOL)
        peers = await store.join_call(call_id, principal, connection_id)
        sequence = await store.current_signal_sequence(call_id)
        await websocket.send_json(
            {
                "type": "peers",
                "peers": peers,
                "connection_id": connection_id,
                "mode": call["mode"],
                "ice_servers": ice_servers,
            }
        )
        await store.signal_call(call_id, connection_id, None, {"type": "peer_joined", "display_name": principal.display_name})
        last_heartbeat = time.monotonic()
        signal_window_started = time.monotonic()
        signals_in_window = 0
        try:
            while True:
                try:
                    packet = await asyncio.wait_for(websocket.receive(), timeout=0.2)
                except asyncio.TimeoutError:
                    packet = None
                if packet is not None and packet["type"] == "websocket.disconnect":
                    raise WebSocketDisconnect(packet.get("code", 1000))
                if packet is not None and packet["type"] != "websocket.receive":
                    await websocket.close(code=1008)
                    break
                if packet is not None and packet.get("text") is None:
                    await websocket.close(code=1003)
                    break
                raw_message = packet.get("text") if packet is not None else None
                if raw_message is not None and len(raw_message.encode("utf-8")) > 65_536:
                    await websocket.close(code=1009)
                    break
                if raw_message is not None:
                    try:
                        incoming = json.loads(raw_message)
                    except json.JSONDecodeError:
                        await websocket.close(code=1008)
                        break
                else:
                    incoming = None
                if incoming is not None:
                    now_monotonic = time.monotonic()
                    if now_monotonic - signal_window_started >= 10:
                        signal_window_started = now_monotonic
                        signals_in_window = 0
                    signals_in_window += 1
                    if signals_in_window > 120:
                        await websocket.close(code=1008)
                        break
                    if not isinstance(incoming, dict):
                        await websocket.close(code=1008)
                        break
                    signal_type = incoming.get("type")
                    allowed_fields = {
                        "offer": {"type", "target", "sdp"},
                        "answer": {"type", "target", "sdp"},
                        "ice": {"type", "target", "candidate"},
                        "heartbeat": {"type"},
                        "hangup": {"type"},
                    }
                    if signal_type not in allowed_fields or set(incoming) - allowed_fields[signal_type]:
                        await websocket.close(code=1008)
                        break
                    if signal_type in {"offer", "answer", "ice"}:
                        target = incoming.get("target")
                        try:
                            uuid.UUID(str(target))
                        except ValueError:
                            await websocket.close(code=1008)
                            break
                        if signal_type in {"offer", "answer"} and (
                            not isinstance(incoming.get("sdp"), str) or len(incoming["sdp"]) > 32_768
                        ):
                            await websocket.close(code=1009)
                            break
                        if signal_type == "ice" and (
                            not isinstance(incoming.get("candidate"), dict) or len(json.dumps(incoming["candidate"])) > 4096
                        ):
                            await websocket.close(code=1009)
                            break
                        await store.signal_call(call_id, connection_id, str(target), incoming)
                    elif signal_type == "heartbeat":
                        await store.heartbeat_call(connection_id)
                    else:
                        await store.signal_call(call_id, connection_id, None, {"type": "peer_left"})
                        await store.leave_call(connection_id)
                        connection_id = ""
                        await websocket.close(code=1000)
                        break
                signals = await store.call_signals(call_id, connection_id, sequence)
                for signal in signals:
                    sequence = max(sequence, int(signal["sequence"]))
                    await websocket.send_json(signal)
                if time.monotonic() - last_heartbeat >= 15:
                    await store.heartbeat_call(connection_id)
                    last_heartbeat = time.monotonic()
        except WebSocketDisconnect:
            pass
        finally:
            if store is not None and connection_id:
                try:
                    await store.signal_call(call_id, connection_id, None, {"type": "peer_left"})
                    await store.leave_call(connection_id)
                except Exception:
                    log.exception("Failed to clean up Editor call participant")

    @router.get("/audit")
    async def audit(request: Request, project: str | None = None, limit: int = 100):
        await access_for(request, "audit.read", project or "*")
        if not 1 <= limit <= 500:
            raise HTTPException(status_code=422, detail="Audit limit must be between 1 and 500")
        return {"events": await store_for(request).list_audit(project=project, limit=limit)}

    app.include_router(router)
    log.warning(
        "Remote Editor control plane enabled prefix=%s allowed_ips=%s accounts=true legacy=%s hooks=%s databases=%s collaboration=%s calls=%s",
        EDITOR_PREFIX,
        settings.editor_allowed_ips,
        settings.editor_legacy_token_enabled,
        settings.editor_allow_hooks,
        settings.editor_database_browser_enabled,
        settings.editor_collaboration_enabled,
        settings.editor_calls_enabled,
    )
