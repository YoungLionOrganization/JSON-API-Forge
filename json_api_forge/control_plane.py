from __future__ import annotations

import os
import re
import stat
import tempfile
from collections.abc import AsyncIterator, Iterator, Mapping, Sequence
from contextlib import asynccontextmanager, contextmanager
from pathlib import Path
from typing import Any, BinaryIO
from urllib.parse import unquote, urlencode, urljoin, urlsplit, urlunsplit

import httpx

from ._io import run_io
from .client import AsyncForgeClient, ForgeClient, _base_url, _controls, _route, _segment
from .errors import ForgeHTTPError, ForgeSessionError
from .models import ForgeResponse, JsonObject

_PREFIX = "__forge/editor/v1"
_USER_AGENT = "json-api-forge-control-plane/0.5.2"
_DEFAULT_ATTACHMENT_LIMIT = 25 * 1024 * 1024
_SESSION_TOKEN = re.compile(r"jfe_session_[A-Za-z0-9_-]{40,100}\Z")
_INVITATION_TOKEN = re.compile(r"jfi_[A-Za-z0-9_-]{40,80}\Z")
_CALL_TICKET = re.compile(r"jfc_[A-Za-z0-9_-]{40,80}\Z")
_CALL_CLIENT_PATH = re.compile(r"/?__forge/editor/v1/call-client/[A-Za-z0-9][A-Za-z0-9_-]{0,127}\Z")


def _credential(
    value: str,
    *,
    name: str,
    minimum: int = 1,
    maximum: int = 512,
    pattern: re.Pattern[str] | None = None,
) -> str:
    value = value.strip()
    if not minimum <= len(value) <= maximum or _controls(value) or any(character.isspace() for character in value):
        raise ValueError(f"{name} is outside its length or control-character policy")
    try:
        value.encode("ascii")
    except UnicodeEncodeError as exc:
        raise ValueError(f"{name} must be ASCII") from exc
    if pattern is not None and pattern.fullmatch(value) is None:
        raise ValueError(f"{name} does not use the expected format")
    return value


def _profile_payload(username: str, password: str, display_name: str | None = None) -> JsonObject:
    if not 1 <= len(username) <= 64 or any(character in username for character in "\r\n\0"):
        raise ValueError("username is outside the supported policy")
    if not 1 <= len(password) <= 256 or "\0" in password:
        raise ValueError("password is outside the supported policy")
    payload: JsonObject = {"username": username, "password": password}
    if display_name is not None:
        if not 1 <= len(display_name.strip()) <= 80 or "\0" in display_name:
            raise ValueError("display_name is outside the supported policy")
        payload["display_name"] = display_name.strip()
    return payload


def _control_path(*segments: str) -> str:
    return "/".join([_PREFIX, *(_segment(segment) for segment in segments)])


def _document_path(project: str, document: str) -> str:
    return f"{_control_path('projects', project, 'documents')}/{_route(document)}"


def _copy_response(response: ForgeResponse[Any], data: Any) -> ForgeResponse[Any]:
    return ForgeResponse(
        data=data,
        status_code=response.status_code,
        request_id=response.request_id,
        idempotent_replay=response.idempotent_replay,
        cache_status=response.cache_status,
    )


def _object_response(response: ForgeResponse[Any]) -> ForgeResponse[JsonObject]:
    if not isinstance(response.data, dict):
        raise ForgeHTTPError(response.status_code, "Editor control plane returned a non-object response", response.request_id)
    return response


def _atomic_save(target: str | os.PathLike[str], content: bytes) -> Path:
    requested = Path(target)
    if requested.name in {"", ".", ".."}:
        raise ValueError("attachment destination must include a file name")
    parent = requested.parent.resolve(strict=True)
    if not parent.is_dir():
        raise ValueError("attachment destination parent must be an existing directory")
    destination = parent / requested.name
    try:
        current = destination.lstat()
    except FileNotFoundError:
        current = None
    if current is not None and stat.S_ISDIR(current.st_mode):
        raise ValueError("attachment destination cannot be a directory")
    descriptor, temporary_name = tempfile.mkstemp(prefix=f".{destination.name}.", dir=parent)
    temporary = Path(temporary_name)
    try:
        if os.name != "nt":
            os.fchmod(descriptor, 0o600)
        with os.fdopen(descriptor, "wb") as handle:
            handle.write(content)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, destination)
        if os.name != "nt":
            flags = os.O_RDONLY | getattr(os, "O_DIRECTORY", 0) | getattr(os, "O_CLOEXEC", 0)
            parent_descriptor = os.open(parent, flags)
            try:
                os.fsync(parent_descriptor)
            finally:
                os.close(parent_descriptor)
    finally:
        temporary.unlink(missing_ok=True)
    return destination


@contextmanager
def _open_attachment(source: str | os.PathLike[str], max_bytes: int) -> Iterator[tuple[str, BinaryIO]]:
    """Open and snapshot a regular file without following a path-swap symlink."""

    path = Path(source)
    if not path.name or len(path.name) > 255 or any(character in path.name for character in '\r\n\0";'):
        raise ValueError("attachment source has an unsafe file name")
    try:
        before = path.lstat()
    except OSError as exc:
        raise ValueError("attachment source is unavailable") from exc
    if stat.S_ISLNK(before.st_mode) or not stat.S_ISREG(before.st_mode):
        raise ValueError("attachment source must be a regular non-symlink file")

    flags = os.O_RDONLY | getattr(os, "O_BINARY", 0) | getattr(os, "O_CLOEXEC", 0)
    flags |= getattr(os, "O_NOFOLLOW", 0)
    try:
        descriptor = os.open(path, flags)
    except OSError as exc:
        raise ValueError("attachment source could not be opened safely") from exc

    snapshot: BinaryIO | None = None
    try:
        opened = os.fstat(descriptor)
        if not stat.S_ISREG(opened.st_mode) or (before.st_dev, before.st_ino) != (opened.st_dev, opened.st_ino):
            raise ValueError("attachment source changed while it was being opened")
        if not 0 <= opened.st_size <= max_bytes:
            raise ValueError("attachment source exceeds max_attachment_bytes")
        snapshot = tempfile.SpooledTemporaryFile(max_size=min(max_bytes, 1024 * 1024), mode="w+b")
        with os.fdopen(descriptor, "rb", closefd=True) as handle:
            descriptor = -1
            remaining = max_bytes + 1
            while remaining:
                chunk = handle.read(min(1024 * 1024, remaining))
                if not chunk:
                    break
                snapshot.write(chunk)
                remaining -= len(chunk)
            after = os.fstat(handle.fileno())
        if snapshot.tell() > max_bytes:
            raise ValueError("attachment source exceeds max_attachment_bytes")
        identity_before = (
            opened.st_dev,
            opened.st_ino,
            opened.st_size,
            opened.st_mtime_ns,
            opened.st_ctime_ns,
        )
        identity_after = (
            after.st_dev,
            after.st_ino,
            after.st_size,
            after.st_mtime_ns,
            after.st_ctime_ns,
        )
        if identity_before != identity_after or snapshot.tell() != opened.st_size:
            raise ValueError("attachment source changed while it was being read")
        snapshot.seek(0)
        yield path.name, snapshot
    finally:
        if descriptor >= 0:
            os.close(descriptor)
        if snapshot is not None:
            snapshot.close()


@asynccontextmanager
async def _open_attachment_async(source: str | os.PathLike[str], max_bytes: int) -> AsyncIterator[tuple[str, BinaryIO]]:
    attachment = _open_attachment(source, max_bytes)
    try:
        yield await run_io(attachment.__enter__)
    finally:
        # run_io drains an interrupted snapshot/read before its file closes.
        await run_io(attachment.__exit__, None, None, None)


class _ControlPlaneBase:
    def _initialize_control_plane(self, base_url: str, *, allow_insecure_http: bool, max_attachment_bytes: int) -> None:
        if not 1024 <= int(max_attachment_bytes) <= 512 * 1024 * 1024:
            raise ValueError("max_attachment_bytes must be between 1 KiB and 512 MiB")
        self._base_url = _base_url(base_url, allow_insecure_http=allow_insecure_http)
        self.max_attachment_bytes = int(max_attachment_bytes)
        self._session = bytearray()

    @property
    def has_session(self) -> bool:
        return bool(self._session)

    def set_session_token(self, token: str) -> None:
        encoded = _credential(token, name="session token", pattern=_SESSION_TOKEN).encode("ascii")
        self.clear_session()
        self._session.extend(encoded)

    def clear_session(self) -> None:
        for index in range(len(self._session)):
            self._session[index] = 0
        self._session.clear()

    def _headers(self, *, authenticated: bool, extra: Mapping[str, str] | None = None) -> dict[str, str]:
        values = {
            "Cache-Control": "no-store",
            "Pragma": "no-cache",
            "User-Agent": _USER_AGENT,
        }
        if authenticated:
            if not self.has_session:
                raise ForgeSessionError("Sign in to the Editor control plane first")
            values["Authorization"] = f"Bearer {self._session.decode('ascii')}"
        for name, value in (extra or {}).items():
            if any(character in name or character in value for character in "\r\n\0"):
                raise ValueError("control-plane headers cannot contain control characters")
            values[name] = value
        return values

    def _adopt_authentication(self, response: ForgeResponse[Any]) -> ForgeResponse[JsonObject]:
        verified = _object_response(response)
        payload = dict(verified.data)
        token = payload.pop("access_token", None)
        if not isinstance(token, str):
            self.clear_session()
            raise ForgeSessionError("Editor authentication response did not contain a valid session")
        self.set_session_token(token)
        return _copy_response(verified, payload)

    def call_client_url(self, call_client_path: str, ticket: str) -> str:
        _credential(ticket, name="call ticket", pattern=_CALL_TICKET)
        parsed = urlsplit(call_client_path)
        if (
            parsed.scheme
            or parsed.netloc
            or parsed.query
            or parsed.fragment
            or "\\" in call_client_path
            or any(character in call_client_path for character in "\r\n\0")
            or unquote(parsed.path) != parsed.path
            or _CALL_CLIENT_PATH.fullmatch(parsed.path) is None
        ):
            raise ValueError("call_client_path must be the safe same-origin Forge call path")
        target = urlsplit(urljoin(self._base_url, parsed.path.lstrip("/")))
        return urlunsplit((target.scheme, target.netloc, target.path, "", urlencode({"ticket": ticket})))


class EditorControlPlaneClient(_ControlPlaneBase):
    """Synchronous account and team client for the v0.5.2 Editor control plane.

    Sessions are kept in a zeroable in-memory buffer, never persisted by this client, and
    removed from returned authentication payloads to reduce accidental logging.
    """

    def __init__(
        self,
        base_url: str,
        *,
        timeout: float = 15.0,
        max_response_bytes: int = 8 * 1024 * 1024,
        max_attachment_bytes: int = _DEFAULT_ATTACHMENT_LIMIT,
        allow_insecure_http: bool = False,
        transport: httpx.BaseTransport | None = None,
    ):
        self._initialize_control_plane(
            base_url,
            allow_insecure_http=allow_insecure_http,
            max_attachment_bytes=max_attachment_bytes,
        )
        self._transport = ForgeClient(
            self._base_url,
            timeout=timeout,
            max_response_bytes=max_response_bytes,
            allow_insecure_http=allow_insecure_http,
            transport=transport,
        )

    def _request(
        self,
        method: str,
        path: str,
        *,
        json_body: Any = None,
        files: Mapping[str, Any] | None = None,
        params: Mapping[str, Any] | None = None,
        authenticated: bool = True,
        extra_headers: Mapping[str, str] | None = None,
        expect_json: bool = True,
        max_response_bytes: int | None = None,
    ) -> ForgeResponse[Any]:
        try:
            return self._transport.request(
                method,
                path,
                json_body=json_body,
                files=files,
                params=params,
                headers=self._headers(authenticated=authenticated, extra=extra_headers),
                expect_json=expect_json,
                max_response_bytes=max_response_bytes,
            )
        except ForgeHTTPError as exc:
            if authenticated and exc.status_code == 401:
                self.clear_session()
            raise

    def setup_status(self) -> ForgeResponse[JsonObject]:
        return _object_response(self._request("GET", _control_path("setup", "status"), authenticated=False))

    def setup_founder(self, setup_token: str, username: str, password: str, display_name: str) -> ForgeResponse[JsonObject]:
        self.clear_session()
        token = _credential(setup_token, name="setup token", minimum=32)
        response = self._request(
            "POST",
            _control_path("setup", "founder"),
            json_body=_profile_payload(username, password, display_name),
            authenticated=False,
            extra_headers={"X-Forge-Setup-Token": token},
        )
        return self._adopt_authentication(response)

    def login(self, username: str, password: str) -> ForgeResponse[JsonObject]:
        self.clear_session()
        return self._adopt_authentication(
            self._request(
                "POST",
                _control_path("auth", "login"),
                json_body=_profile_payload(username, password),
                authenticated=False,
            )
        )

    def register(self, invitation: str, username: str, password: str, display_name: str) -> ForgeResponse[JsonObject]:
        self.clear_session()
        payload = _profile_payload(username, password, display_name)
        payload["invitation"] = _credential(invitation, name="invitation", pattern=_INVITATION_TOKEN)
        return self._adopt_authentication(self._request("POST", _control_path("auth", "register"), json_body=payload, authenticated=False))

    def logout(self) -> ForgeResponse[Any]:
        try:
            return self._request("POST", _control_path("auth", "logout"), expect_json=False)
        finally:
            self.clear_session()

    def capabilities(self) -> ForgeResponse[JsonObject]:
        return _object_response(self._request("GET", _control_path("capabilities")))

    def profile(self) -> ForgeResponse[JsonObject]:
        return _object_response(self._request("GET", _control_path("me")))

    def update_profile(self, values: Mapping[str, str]) -> ForgeResponse[JsonObject]:
        return _object_response(self._request("PATCH", _control_path("me"), json_body=dict(values)))

    def projects(self) -> ForgeResponse[JsonObject]:
        return _object_response(self._request("GET", _control_path("projects")))

    def create_project(self, name: str, slug: str) -> ForgeResponse[JsonObject]:
        return _object_response(self._request("POST", _control_path("projects"), json_body={"name": name, "slug": slug}))

    def documents(self, project: str) -> ForgeResponse[JsonObject]:
        return _object_response(self._request("GET", _control_path("projects", project, "documents")))

    def document(self, project: str, document: str) -> ForgeResponse[JsonObject]:
        return _object_response(self._request("GET", _document_path(project, document)))

    def save_document(self, project: str, document: str, content: str, expected_sha256: str) -> ForgeResponse[JsonObject]:
        return _object_response(
            self._request(
                "PUT",
                _document_path(project, document),
                json_body={"content": content, "expected_sha256": expected_sha256},
            )
        )

    def validate_project(self, project: str) -> ForgeResponse[JsonObject]:
        return _object_response(self._request("POST", _control_path("projects", project, "validate")))

    def roles(self) -> ForgeResponse[JsonObject]:
        return _object_response(self._request("GET", _control_path("roles")))

    def create_role(self, values: Mapping[str, Any]) -> ForgeResponse[JsonObject]:
        return _object_response(self._request("POST", _control_path("roles"), json_body=dict(values)))

    def update_role(self, role_id: str, values: Mapping[str, Any]) -> ForgeResponse[JsonObject]:
        return _object_response(self._request("PUT", _control_path("roles", role_id), json_body=dict(values)))

    def members(self) -> ForgeResponse[JsonObject]:
        return _object_response(self._request("GET", _control_path("members")))

    def update_member(
        self,
        user_id: str,
        memberships: Sequence[Mapping[str, str]],
        *,
        active: bool = True,
    ) -> ForgeResponse[Any]:
        return self._request(
            "PUT",
            _control_path("members", user_id),
            json_body={"memberships": [dict(value) for value in memberships], "active": active},
            expect_json=False,
        )

    def create_invitation(
        self,
        memberships: Sequence[Mapping[str, str]],
        *,
        expires_hours: int = 24,
    ) -> ForgeResponse[JsonObject]:
        return _object_response(
            self._request(
                "POST",
                _control_path("invitations"),
                json_body={"memberships": [dict(value) for value in memberships], "expires_hours": expires_hours},
            )
        )

    def areas(self, project: str = "*") -> ForgeResponse[JsonObject]:
        return _object_response(self._request("GET", _control_path("areas"), params={"project": project}))

    def create_area(self, values: Mapping[str, Any]) -> ForgeResponse[JsonObject]:
        return _object_response(self._request("POST", _control_path("areas"), json_body=dict(values)))

    def messages(self, area_id: str, *, limit: int = 100) -> ForgeResponse[JsonObject]:
        return _object_response(self._request("GET", _control_path("areas", area_id, "messages"), params={"limit": limit}))

    def post_message(self, area_id: str, body: str, *, announcement: bool = False) -> ForgeResponse[JsonObject]:
        return _object_response(
            self._request(
                "POST",
                _control_path("areas", area_id, "messages"),
                json_body={"body": body, "kind": "announcement" if announcement else "message"},
            )
        )

    def notes(self, project: str = "*") -> ForgeResponse[JsonObject]:
        return _object_response(self._request("GET", _control_path("notes"), params={"project": project}))

    def create_note(self, values: Mapping[str, Any]) -> ForgeResponse[JsonObject]:
        return _object_response(self._request("POST", _control_path("notes"), json_body=dict(values)))

    def database_catalog(self, project: str) -> ForgeResponse[JsonObject]:
        return _object_response(self._request("GET", _control_path("projects", project, "databases")))

    def database_rows(
        self,
        project: str,
        alias: str,
        table: str,
        *,
        limit: int = 100,
        offset: int = 0,
    ) -> ForgeResponse[JsonObject]:
        return _object_response(
            self._request(
                "GET",
                _control_path("projects", project, "databases", alias, "tables", table, "rows"),
                params={"limit": limit, "offset": offset},
            )
        )

    def attachments(self, area_id: str, *, limit: int = 100) -> ForgeResponse[JsonObject]:
        return _object_response(self._request("GET", _control_path("areas", area_id, "attachments"), params={"limit": limit}))

    def upload_attachment(self, area_id: str, source: str | os.PathLike[str]) -> ForgeResponse[JsonObject]:
        with _open_attachment(source, self.max_attachment_bytes) as (name, handle):
            response = self._request(
                "POST",
                _control_path("areas", area_id, "attachments"),
                files={"upload": (name, handle, "application/octet-stream")},
            )
        return _object_response(response)

    def download_attachment(
        self,
        attachment_id: str,
        target: str | os.PathLike[str],
    ) -> ForgeResponse[Path]:
        response = self._request(
            "GET",
            _control_path("attachments", attachment_id),
            expect_json=False,
            max_response_bytes=self.max_attachment_bytes,
        )
        if not isinstance(response.data, bytes):
            raise ForgeHTTPError(response.status_code, "Attachment response was not binary", response.request_id)
        return _copy_response(response, _atomic_save(target, response.data))

    def start_call(self, area_id: str, *, mode: str = "video") -> ForgeResponse[JsonObject]:
        return _object_response(self._request("POST", _control_path("calls"), json_body={"area_id": area_id, "mode": mode}))

    def call_ticket(self, call_id: str) -> ForgeResponse[JsonObject]:
        return _object_response(self._request("POST", _control_path("calls", call_id, "ticket")))

    def audit(self, *, project: str | None = None, limit: int = 100) -> ForgeResponse[JsonObject]:
        params: dict[str, Any] = {"limit": limit}
        if project is not None:
            params["project"] = project
        return _object_response(self._request("GET", _control_path("audit"), params=params))

    def close(self) -> None:
        self.clear_session()
        self._transport.close()

    def __enter__(self) -> EditorControlPlaneClient:
        return self

    def __exit__(self, *_args: Any) -> None:
        self.close()


class AsyncEditorControlPlaneClient(_ControlPlaneBase):
    """Asynchronous account and team client for the v0.5.2 Editor control plane."""

    def __init__(
        self,
        base_url: str,
        *,
        timeout: float = 15.0,
        max_response_bytes: int = 8 * 1024 * 1024,
        max_attachment_bytes: int = _DEFAULT_ATTACHMENT_LIMIT,
        allow_insecure_http: bool = False,
        transport: httpx.AsyncBaseTransport | None = None,
    ):
        self._initialize_control_plane(
            base_url,
            allow_insecure_http=allow_insecure_http,
            max_attachment_bytes=max_attachment_bytes,
        )
        self._transport = AsyncForgeClient(
            self._base_url,
            timeout=timeout,
            max_response_bytes=max_response_bytes,
            allow_insecure_http=allow_insecure_http,
            transport=transport,
        )

    async def _request(
        self,
        method: str,
        path: str,
        *,
        json_body: Any = None,
        files: Mapping[str, Any] | None = None,
        params: Mapping[str, Any] | None = None,
        authenticated: bool = True,
        extra_headers: Mapping[str, str] | None = None,
        expect_json: bool = True,
        max_response_bytes: int | None = None,
    ) -> ForgeResponse[Any]:
        try:
            return await self._transport.request(
                method,
                path,
                json_body=json_body,
                files=files,
                params=params,
                headers=self._headers(authenticated=authenticated, extra=extra_headers),
                expect_json=expect_json,
                max_response_bytes=max_response_bytes,
            )
        except ForgeHTTPError as exc:
            if authenticated and exc.status_code == 401:
                self.clear_session()
            raise

    async def setup_status(self) -> ForgeResponse[JsonObject]:
        return _object_response(await self._request("GET", _control_path("setup", "status"), authenticated=False))

    async def setup_founder(
        self,
        setup_token: str,
        username: str,
        password: str,
        display_name: str,
    ) -> ForgeResponse[JsonObject]:
        self.clear_session()
        token = _credential(setup_token, name="setup token", minimum=32)
        response = await self._request(
            "POST",
            _control_path("setup", "founder"),
            json_body=_profile_payload(username, password, display_name),
            authenticated=False,
            extra_headers={"X-Forge-Setup-Token": token},
        )
        return self._adopt_authentication(response)

    async def login(self, username: str, password: str) -> ForgeResponse[JsonObject]:
        self.clear_session()
        response = await self._request(
            "POST",
            _control_path("auth", "login"),
            json_body=_profile_payload(username, password),
            authenticated=False,
        )
        return self._adopt_authentication(response)

    async def register(
        self,
        invitation: str,
        username: str,
        password: str,
        display_name: str,
    ) -> ForgeResponse[JsonObject]:
        self.clear_session()
        payload = _profile_payload(username, password, display_name)
        payload["invitation"] = _credential(invitation, name="invitation", pattern=_INVITATION_TOKEN)
        response = await self._request("POST", _control_path("auth", "register"), json_body=payload, authenticated=False)
        return self._adopt_authentication(response)

    async def logout(self) -> ForgeResponse[Any]:
        try:
            return await self._request("POST", _control_path("auth", "logout"), expect_json=False)
        finally:
            self.clear_session()

    async def capabilities(self) -> ForgeResponse[JsonObject]:
        return _object_response(await self._request("GET", _control_path("capabilities")))

    async def profile(self) -> ForgeResponse[JsonObject]:
        return _object_response(await self._request("GET", _control_path("me")))

    async def update_profile(self, values: Mapping[str, str]) -> ForgeResponse[JsonObject]:
        return _object_response(await self._request("PATCH", _control_path("me"), json_body=dict(values)))

    async def projects(self) -> ForgeResponse[JsonObject]:
        return _object_response(await self._request("GET", _control_path("projects")))

    async def create_project(self, name: str, slug: str) -> ForgeResponse[JsonObject]:
        return _object_response(await self._request("POST", _control_path("projects"), json_body={"name": name, "slug": slug}))

    async def documents(self, project: str) -> ForgeResponse[JsonObject]:
        return _object_response(await self._request("GET", _control_path("projects", project, "documents")))

    async def document(self, project: str, document: str) -> ForgeResponse[JsonObject]:
        return _object_response(await self._request("GET", _document_path(project, document)))

    async def save_document(
        self,
        project: str,
        document: str,
        content: str,
        expected_sha256: str,
    ) -> ForgeResponse[JsonObject]:
        return _object_response(
            await self._request(
                "PUT",
                _document_path(project, document),
                json_body={"content": content, "expected_sha256": expected_sha256},
            )
        )

    async def validate_project(self, project: str) -> ForgeResponse[JsonObject]:
        return _object_response(await self._request("POST", _control_path("projects", project, "validate")))

    async def roles(self) -> ForgeResponse[JsonObject]:
        return _object_response(await self._request("GET", _control_path("roles")))

    async def create_role(self, values: Mapping[str, Any]) -> ForgeResponse[JsonObject]:
        return _object_response(await self._request("POST", _control_path("roles"), json_body=dict(values)))

    async def update_role(self, role_id: str, values: Mapping[str, Any]) -> ForgeResponse[JsonObject]:
        return _object_response(await self._request("PUT", _control_path("roles", role_id), json_body=dict(values)))

    async def members(self) -> ForgeResponse[JsonObject]:
        return _object_response(await self._request("GET", _control_path("members")))

    async def update_member(
        self,
        user_id: str,
        memberships: Sequence[Mapping[str, str]],
        *,
        active: bool = True,
    ) -> ForgeResponse[Any]:
        return await self._request(
            "PUT",
            _control_path("members", user_id),
            json_body={"memberships": [dict(value) for value in memberships], "active": active},
            expect_json=False,
        )

    async def create_invitation(
        self,
        memberships: Sequence[Mapping[str, str]],
        *,
        expires_hours: int = 24,
    ) -> ForgeResponse[JsonObject]:
        return _object_response(
            await self._request(
                "POST",
                _control_path("invitations"),
                json_body={"memberships": [dict(value) for value in memberships], "expires_hours": expires_hours},
            )
        )

    async def areas(self, project: str = "*") -> ForgeResponse[JsonObject]:
        return _object_response(await self._request("GET", _control_path("areas"), params={"project": project}))

    async def create_area(self, values: Mapping[str, Any]) -> ForgeResponse[JsonObject]:
        return _object_response(await self._request("POST", _control_path("areas"), json_body=dict(values)))

    async def messages(self, area_id: str, *, limit: int = 100) -> ForgeResponse[JsonObject]:
        return _object_response(await self._request("GET", _control_path("areas", area_id, "messages"), params={"limit": limit}))

    async def post_message(
        self,
        area_id: str,
        body: str,
        *,
        announcement: bool = False,
    ) -> ForgeResponse[JsonObject]:
        return _object_response(
            await self._request(
                "POST",
                _control_path("areas", area_id, "messages"),
                json_body={"body": body, "kind": "announcement" if announcement else "message"},
            )
        )

    async def notes(self, project: str = "*") -> ForgeResponse[JsonObject]:
        return _object_response(await self._request("GET", _control_path("notes"), params={"project": project}))

    async def create_note(self, values: Mapping[str, Any]) -> ForgeResponse[JsonObject]:
        return _object_response(await self._request("POST", _control_path("notes"), json_body=dict(values)))

    async def database_catalog(self, project: str) -> ForgeResponse[JsonObject]:
        return _object_response(await self._request("GET", _control_path("projects", project, "databases")))

    async def database_rows(
        self,
        project: str,
        alias: str,
        table: str,
        *,
        limit: int = 100,
        offset: int = 0,
    ) -> ForgeResponse[JsonObject]:
        return _object_response(
            await self._request(
                "GET",
                _control_path("projects", project, "databases", alias, "tables", table, "rows"),
                params={"limit": limit, "offset": offset},
            )
        )

    async def attachments(self, area_id: str, *, limit: int = 100) -> ForgeResponse[JsonObject]:
        return _object_response(await self._request("GET", _control_path("areas", area_id, "attachments"), params={"limit": limit}))

    async def upload_attachment(self, area_id: str, source: str | os.PathLike[str]) -> ForgeResponse[JsonObject]:
        async with _open_attachment_async(source, self.max_attachment_bytes) as (name, handle):
            response = await self._request(
                "POST",
                _control_path("areas", area_id, "attachments"),
                files={"upload": (name, handle, "application/octet-stream")},
            )
        return _object_response(response)

    async def download_attachment(
        self,
        attachment_id: str,
        target: str | os.PathLike[str],
    ) -> ForgeResponse[Path]:
        response = await self._request(
            "GET",
            _control_path("attachments", attachment_id),
            expect_json=False,
            max_response_bytes=self.max_attachment_bytes,
        )
        if not isinstance(response.data, bytes):
            raise ForgeHTTPError(response.status_code, "Attachment response was not binary", response.request_id)
        return _copy_response(response, await run_io(_atomic_save, target, response.data))

    async def start_call(self, area_id: str, *, mode: str = "video") -> ForgeResponse[JsonObject]:
        return _object_response(await self._request("POST", _control_path("calls"), json_body={"area_id": area_id, "mode": mode}))

    async def call_ticket(self, call_id: str) -> ForgeResponse[JsonObject]:
        return _object_response(await self._request("POST", _control_path("calls", call_id, "ticket")))

    async def audit(self, *, project: str | None = None, limit: int = 100) -> ForgeResponse[JsonObject]:
        params: dict[str, Any] = {"limit": limit}
        if project is not None:
            params["project"] = project
        return _object_response(await self._request("GET", _control_path("audit"), params=params))

    async def aclose(self) -> None:
        self.clear_session()
        await self._transport.aclose()

    async def __aenter__(self) -> AsyncEditorControlPlaneClient:
        return self

    async def __aexit__(self, *_args: Any) -> None:
        await self.aclose()
