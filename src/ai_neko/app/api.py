"""Authenticated local UI API. No provider secrets appear in error responses."""

from __future__ import annotations

import json
import secrets
from pathlib import Path

from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import FileResponse, JSONResponse


async def body(request: Request) -> dict:
    if request.headers.get("content-type", "").split(";")[0] != "application/json":
        raise HTTPException(415, "JSON required")
    data = bytearray()
    async for chunk in request.stream():
        data.extend(chunk)
        if len(data) > 65536:
            raise HTTPException(413, "request too large")
    try:
        value = json.loads(data)
    except (ValueError, UnicodeError):
        raise HTTPException(400, "invalid JSON") from None
    if not isinstance(value, dict):
        raise HTTPException(400, "JSON object required")
    return value


def install_api(app: FastAPI, connection, authorize, runtime, providers, bootstrap_code=None):
    from ai_neko.providers import ProviderError
    from ai_neko.runtime import RuntimeAccessError, RuntimeConflictError, RuntimeInputError

    pending_bootstrap = bootstrap_code
    web_root = Path(__file__).resolve().parents[1] / "web"

    @app.middleware("http")
    async def security_headers(request, call_next):
        response = await call_next(request)
        response.headers["Cache-Control"] = "no-store"
        response.headers["Referrer-Policy"] = "no-referrer"
        response.headers["X-Content-Type-Options"] = "nosniff"
        response.headers["Content-Security-Policy"] = (
            "default-src 'none'; script-src 'self'; style-src 'self'; "
            "connect-src 'self'; img-src 'self' data:; font-src 'self'; "
            "base-uri 'none'; form-action 'none'; frame-ancestors 'none'"
        )
        return response

    @app.exception_handler(ProviderError)
    async def provider_error(_request, exc):
        return JSONResponse({"detail": str(exc), "code": exc.code}, status_code=400)

    @app.exception_handler(RuntimeAccessError)
    async def missing(_request, _exc):
        return JSONResponse({"detail": "会话或回合不存在。"}, status_code=404)

    @app.exception_handler(RuntimeConflictError)
    async def conflict(_request, _exc):
        return JSONResponse({"detail": "当前会话已有进行中的回合。"}, status_code=409)

    @app.exception_handler(RuntimeInputError)
    async def invalid(_request, _exc):
        return JSONResponse({"detail": "请检查输入和回合参数。"}, status_code=400)

    def auth(request: Request):
        authorize(request)
        origin = request.headers.get("origin")
        if origin is not None and origin != connection.url:
            raise HTTPException(403, "untrusted origin")

    @app.get("/")
    async def index():
        return FileResponse(web_root / "index.html", media_type="text/html")

    @app.get("/static/{name}")
    async def asset(name: str):
        media = {"app.js": "text/javascript", "style.css": "text/css"}
        if name not in media:
            raise HTTPException(404)
        return FileResponse(web_root / name, media_type=media[name])

    @app.post("/api/bootstrap")
    async def bootstrap(request: Request):
        nonlocal pending_bootstrap
        if request.headers.get("origin") != connection.url:
            raise HTTPException(403, "untrusted origin")
        value = await body(request)
        code = value.get("code")
        if (
            pending_bootstrap is None
            or not isinstance(code, str)
            or not secrets.compare_digest(code.encode(), pending_bootstrap.encode())
        ):
            raise HTTPException(401, "启动链接已失效，请重新打开应用。")
        pending_bootstrap = None
        return {"token": connection.token}

    @app.get("/api/config")
    async def get_config(request: Request):
        auth(request)
        try:
            return providers.public_config()
        except ValueError:
            raise HTTPException(400, "无法读取应用凭据，请检查系统凭据存储。") from None

    @app.put("/api/config")
    async def put_config(request: Request):
        auth(request)
        value = await body(request)
        try:
            return providers.update(value)
        except ValueError:
            raise HTTPException(400, "配置未保存，请检查地址、模型名与凭据格式。") from None

    @app.get("/api/sessions")
    async def sessions(request: Request):
        auth(request)
        return {"sessions": runtime.list_sessions()}

    @app.post("/api/sessions", status_code=201)
    async def create_session(request: Request):
        auth(request)
        return runtime.create_session()

    @app.get("/api/sessions/{session_id}")
    async def get_session(request: Request, session_id: str):
        auth(request)
        return runtime.get_session(session_id)

    @app.post("/api/sessions/{session_id}/turns", status_code=202)
    async def start(request: Request, session_id: str):
        auth(request)
        value = await body(request)
        if set(value) - {"text", "guide", "request_id"}:
            raise HTTPException(400, "unknown turn fields")
        return await runtime.start_turn(
            session_id,
            value.get("text"),
            guide=value.get("guide", False),
            request_id=value.get("request_id"),
        )

    @app.get("/api/sessions/{session_id}/turns/{turn_id}/events")
    async def events(request: Request, session_id: str, turn_id: str):
        auth(request)
        raw = request.query_params.get("after", "0")
        if not raw.isascii() or not raw.isdigit() or len(raw) > 9:
            raise HTTPException(400, "invalid event cursor")
        return runtime.events(session_id, turn_id, int(raw))

    @app.post("/api/sessions/{session_id}/turns/{turn_id}/ack")
    async def ack(request: Request, session_id: str, turn_id: str):
        auth(request)
        value = await body(request)
        return runtime.ack(session_id, turn_id, value.get("sequence"))

    @app.post("/api/sessions/{session_id}/turns/{turn_id}/cancel")
    async def cancel(request: Request, session_id: str, turn_id: str):
        auth(request)
        return await runtime.cancel_turn(session_id, turn_id)
