from __future__ import annotations

import asyncio
import csv
import io
import ipaddress
import json
import os
import secrets
import socket
import stat
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit

import yaml
from fastapi import HTTPException, Request

from .config import DataSourceConfig, ProjectConfig
from .file_safety import check_leaf, confined_parent, relative_parts
from .services.http_client import ResilientHTTPClient, ResponseTooLarge, blocked_network_address


class DataSourceManager:
    def __init__(self, project: ProjectConfig):
        self.project = project
        self.root = Path(project.project_dir).resolve()
        self._locks: dict[str, asyncio.Lock] = {}
        self._http = None
        self._private_http = None

    @property
    def http(self):
        if self._http is None:
            self._http = ResilientHTTPClient()
        return self._http

    @http.setter
    def http(self, value):
        self._http = value

    @property
    def private_http(self):
        if self._private_http is None:
            self._private_http = ResilientHTTPClient(block_private_networks=False)
        return self._private_http

    @private_http.setter
    def private_http(self, value):
        self._private_http = value

    def _lock(self, name: str) -> asyncio.Lock:
        return self._locks.setdefault(name, asyncio.Lock())

    def _file_path(self, source: DataSourceConfig) -> Path:
        return self.root.joinpath(*relative_parts(source.file or ""))

    def _read_file_sync(self, source: DataSourceConfig) -> Any:
        with confined_parent(self.root, source.file or "") as (parent, name, parent_fd):
            flags = os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0) | getattr(os, "O_BINARY", 0) | getattr(os, "O_NONBLOCK", 0)
            fd = os.open(name, flags, dir_fd=parent_fd) if parent_fd is not None else os.open(parent / name, flags)
            with os.fdopen(fd, "rb") as handle:
                info = os.fstat(handle.fileno())
                if not stat.S_ISREG(info.st_mode):
                    raise HTTPException(400, "Data source must be a regular file")
                if info.st_size > source.max_response_bytes:
                    raise HTTPException(413, "Data source file exceeds max_response_bytes")
                raw = handle.read(source.max_response_bytes + 1)
        if len(raw) > source.max_response_bytes:
            raise HTTPException(413, "Data source file exceeds max_response_bytes")
        content = raw.decode("utf-8")
        if source.type == "json_file":
            return json.loads(content)
        if source.type == "yaml_file":
            return yaml.safe_load(content)
        if source.type == "csv_file":
            from itertools import islice

            return list(islice(csv.DictReader(io.StringIO(content, newline="")), source.max_items))
        raise RuntimeError("Unsupported file source")

    def _write_file_sync(self, source: DataSourceConfig, value: Any) -> None:
        if source.type == "json_file":
            encoded = json.dumps(value, indent=2, ensure_ascii=False, default=str)
        elif source.type == "yaml_file":
            encoded = yaml.safe_dump(value, allow_unicode=True, sort_keys=False)
        else:
            raise HTTPException(status_code=405, detail="CSV data sources are read-only")
        if len(encoded.encode("utf-8")) > source.max_response_bytes:
            raise HTTPException(status_code=413, detail=f"Mutated data source exceeds max_response_bytes={source.max_response_bytes}")
        with confined_parent(self.root, source.file or "", create=True) as (parent, name, parent_fd):
            info = check_leaf(parent, name, parent_fd)
            mode = stat.S_IMODE(info.st_mode) & 0o777 if info else 0o600
            temporary = f".forge-{secrets.token_hex(16)}.tmp"
            target = temporary if parent_fd is not None else parent / temporary
            fd = os.open(target, os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_BINARY", 0), mode, dir_fd=parent_fd)
            try:
                with os.fdopen(fd, "wb") as handle:
                    handle.write(encoded.encode("utf-8"))
                    handle.flush()
                    os.fsync(handle.fileno())
                check_leaf(parent, name, parent_fd)
                if parent_fd is not None:
                    os.replace(temporary, name, src_dir_fd=parent_fd, dst_dir_fd=parent_fd)
                    os.fsync(parent_fd)
                else:
                    os.replace(parent / temporary, parent / name)
            finally:
                try:
                    os.unlink(target, dir_fd=parent_fd)
                except FileNotFoundError:
                    pass

    @staticmethod
    def _blocked_address(address: str) -> bool:
        return blocked_network_address(address)

    async def _validate_http_target(self, source: DataSourceConfig, *, resolve: bool = True) -> None:
        parsed = urlsplit(source.url or "")
        if parsed.scheme not in {"http", "https"} or not parsed.hostname:
            raise HTTPException(status_code=502, detail="HTTP data source URL is invalid")
        if parsed.username or parsed.password:
            raise HTTPException(status_code=502, detail="HTTP data source URLs may not embed credentials")
        if parsed.scheme == "http" and not source.allow_insecure_http:
            raise HTTPException(status_code=502, detail="Plain HTTP egress is disabled for this data source")
        if source.allow_private_networks or not resolve:
            return
        host = parsed.hostname
        try:
            addresses = [str(ipaddress.ip_address(host))]
        except ValueError:
            try:
                infos = await asyncio.to_thread(
                    socket.getaddrinfo, host, parsed.port or (443 if parsed.scheme == "https" else 80), type=socket.SOCK_STREAM
                )
            except OSError as exc:
                raise HTTPException(status_code=502, detail="HTTP data source hostname could not be resolved") from exc
            addresses = sorted({info[4][0] for info in infos})
        try:
            if any(self._blocked_address(address) for address in addresses):
                raise HTTPException(status_code=502, detail="HTTP data source resolved to a private or non-routable address")
        except ValueError as exc:
            raise HTTPException(status_code=502, detail="HTTP data source resolved to an invalid address") from exc

    async def read(self, source: DataSourceConfig, request: Request, payload: Any = None) -> Any:
        if source.type == "static":
            data = source.data
        elif source.type in {"json_file", "yaml_file", "csv_file"}:
            data = await asyncio.to_thread(self._read_file_sync, source)
        elif source.type == "http":
            # The transport validates and dials the same DNS answers. A second
            # preflight resolution adds latency without strengthening that boundary.
            await self._validate_http_target(source, resolve=False)
            kwargs: dict[str, Any] = {
                "headers": source.headers,
                "retries": source.retries,
                "timeout": source.timeout_seconds,
                "retry_non_idempotent": source.retry_non_idempotent,
                "max_response_bytes": source.max_response_bytes,
            }
            if source.total_timeout_seconds is not None:
                kwargs["total_timeout"] = source.total_timeout_seconds
            if source.forward_query:
                kwargs["params"] = list(request.query_params.multi_items())
            if source.forward_body and payload is not None:
                kwargs["json"] = payload
            try:
                client = self.private_http if source.allow_private_networks else self.http
                response = await client.request(source.method, source.url or "", **kwargs)
            except ResponseTooLarge as exc:
                raise HTTPException(status_code=502, detail=str(exc)) from exc
            ctype = response.headers.get("content-type", "")
            if "json" in ctype:
                try:
                    return response.json()
                except ValueError as exc:
                    raise HTTPException(status_code=502, detail="Upstream advertised JSON but returned invalid JSON") from exc
            return {"status_code": response.status_code, "text": response.text}
        else:
            raise RuntimeError(f"Unsupported data source type {source.type}")
        return self._query_collection(data, source, request)

    def _query_collection(self, data: Any, source: DataSourceConfig, request: Request) -> Any:
        if not isinstance(data, list):
            return data
        rows = data[: source.max_items]
        reserved = {"limit", "offset", "sort"}
        for key, value in request.query_params.items():
            if key in reserved:
                continue
            if key not in source.allowed_filters:
                raise HTTPException(status_code=400, detail=f"Data source filter is not allowed: {key}")
            rows = [row for row in rows if isinstance(row, dict) and str(row.get(key)) == value]
        sort = request.query_params.get("sort")
        if sort:
            reverse = sort.startswith("-")
            field = sort[1:] if reverse else sort
            if field not in source.allowed_sort:
                raise HTTPException(status_code=400, detail=f"Data source sort is not allowed: {field}")
            rows = sorted(
                rows, key=lambda r: (r.get(field) is None, r.get(field)) if isinstance(r, dict) else (True, None), reverse=reverse
            )
        try:
            limit = int(request.query_params.get("limit", "100"))
            offset = int(request.query_params.get("offset", "0"))
        except ValueError as exc:
            raise HTTPException(status_code=400, detail="limit/offset must be integers") from exc
        if limit < 1 or offset < 0:
            raise HTTPException(status_code=400, detail="limit must be >= 1 and offset must be >= 0")
        limit = min(limit, source.max_items)
        return {"items": rows[offset : offset + limit], "limit": limit, "offset": offset, "total": len(rows)}

    def _mutate_file_sync(self, source: DataSourceConfig, action: str, *, payload: Any = None, item_id: str | None = None) -> Any:
        from filelock import FileLock, Timeout

        path = self._file_path(source)
        lock = FileLock(str(path) + ".forge.lock", timeout=source.file_lock_timeout_seconds)
        try:
            with lock:
                data = self._read_file_sync(source)
                if not isinstance(data, list):
                    raise HTTPException(status_code=422, detail="Writable file data source must contain a list of objects")
                if action == "create":
                    if not isinstance(payload, dict):
                        raise HTTPException(status_code=422, detail="Writable file create payload must be an object")
                    item = dict(payload)
                    if source.id_field not in item:
                        numeric = [x.get(source.id_field) for x in data if isinstance(x, dict) and isinstance(x.get(source.id_field), int)]
                        item[source.id_field] = (max(numeric) + 1) if numeric else 1
                    if any(isinstance(x, dict) and str(x.get(source.id_field)) == str(item[source.id_field]) for x in data):
                        raise HTTPException(status_code=409, detail="Duplicate data-source id")
                    data.append(item)
                    self._write_file_sync(source, data)
                    return item
                if action in {"update", "replace"}:
                    if not isinstance(payload, dict):
                        raise HTTPException(status_code=422, detail="Writable file update payload must be an object")
                    if source.id_field in payload:
                        raise HTTPException(status_code=422, detail=f"Field {source.id_field!r} is immutable")
                    for idx, row in enumerate(data):
                        if isinstance(row, dict) and str(row.get(source.id_field)) == str(item_id):
                            immutable_id = row.get(source.id_field)
                            updated = {**row, **payload} if action == "update" else dict(payload)
                            updated[source.id_field] = immutable_id
                            data[idx] = updated
                            self._write_file_sync(source, data)
                            return updated
                    raise HTTPException(status_code=404, detail="Data source item not found")
                if action == "delete":
                    new_data = [row for row in data if not (isinstance(row, dict) and str(row.get(source.id_field)) == str(item_id))]
                    if len(new_data) == len(data):
                        raise HTTPException(status_code=404, detail="Data source item not found")
                    self._write_file_sync(source, new_data)
                    return {"deleted": True}
                raise RuntimeError(f"Unknown file mutation action: {action}")
        except Timeout as exc:
            raise HTTPException(status_code=503, detail="Data source file is busy", headers={"Retry-After": "1"}) from exc

    async def create(self, source: DataSourceConfig, payload: Any) -> Any:
        if not source.writable or source.type not in {"json_file", "yaml_file"}:
            raise HTTPException(status_code=405, detail="Data source is read-only")
        async with self._lock(source.name):
            return await asyncio.to_thread(self._mutate_file_sync, source, "create", payload=payload)

    async def update(self, source: DataSourceConfig, item_id: str, payload: dict[str, Any], *, replace: bool = False) -> Any:
        if not source.writable or source.type not in {"json_file", "yaml_file"}:
            raise HTTPException(status_code=405, detail="Data source is read-only")
        async with self._lock(source.name):
            return await asyncio.to_thread(
                self._mutate_file_sync, source, "replace" if replace else "update", payload=payload, item_id=item_id
            )

    async def delete(self, source: DataSourceConfig, item_id: str) -> dict[str, bool]:
        if not source.writable or source.type not in {"json_file", "yaml_file"}:
            raise HTTPException(status_code=405, detail="Data source is read-only")
        async with self._lock(source.name):
            return await asyncio.to_thread(self._mutate_file_sync, source, "delete", item_id=item_id)

    async def close(self) -> None:
        for client in (self._http, self._private_http):
            if client is not None:
                await client.close()
