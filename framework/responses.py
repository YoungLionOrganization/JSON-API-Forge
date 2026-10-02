from __future__ import annotations

from pathlib import Path
from typing import Any

from fastapi.encoders import jsonable_encoder
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse, PlainTextResponse, RedirectResponse, Response, StreamingResponse

from .config import ResponseSpec


def render_response(spec: ResponseSpec, result: Any):
    status = spec.status_code or 200
    headers = spec.headers or None
    if spec.kind == "json":
        return JSONResponse(jsonable_encoder(result), status_code=status, headers=headers)
    if spec.kind == "text":
        return PlainTextResponse(str(result), status_code=status, headers=headers, media_type=spec.media_type)
    if spec.kind == "html":
        return HTMLResponse(str(result), status_code=status, headers=headers)
    if spec.kind == "redirect":
        url = result.get("url") if isinstance(result, dict) else str(result)
        return RedirectResponse(url=url, status_code=spec.status_code or 307, headers=headers)
    if spec.kind == "stream":
        return StreamingResponse(result, status_code=status, headers=headers, media_type=spec.media_type or "application/octet-stream")
    if spec.kind == "file":
        path = result.get("path") if isinstance(result, dict) else result
        return FileResponse(Path(path), status_code=status, headers=headers, media_type=spec.media_type, filename=spec.filename)
    if spec.kind == "empty":
        return Response(status_code=spec.status_code or 204, headers=headers)
    return JSONResponse(jsonable_encoder(result), status_code=status, headers=headers)
