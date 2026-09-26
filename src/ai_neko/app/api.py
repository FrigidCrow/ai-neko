"""Authenticated local UI API. No provider secrets appear in error responses."""

from __future__ import annotations

import asyncio
import json
import re
import secrets
import time
from pathlib import Path
from uuid import uuid4

from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import FileResponse, JSONResponse


async def body(request: Request, limit: int = 65536) -> dict:
    if request.headers.get("content-type", "").split(";")[0] != "application/json":
        raise HTTPException(415, "JSON required")
    data = bytearray()
    async for chunk in request.stream():
        data.extend(chunk)
        if len(data) > limit:
            raise HTTPException(413, "request too large")
    try:
        value = json.loads(data)
    except (ValueError, UnicodeError):
        raise HTTPException(400, "invalid JSON") from None
    if not isinstance(value, dict):
        raise HTTPException(400, "JSON object required")
    return value


def install_api(app: FastAPI, connection, authorize, runtime, providers, bootstrap_code=None):
    from ai_neko.media import VoiceError, VoiceService
    from ai_neko.memory import MemoryAccessError, MemoryConflictError, MemoryInputError
    from ai_neko.providers import ProviderError
    from ai_neko.runtime import RuntimeAccessError, RuntimeConflictError, RuntimeInputError

    pending_bootstrap = bootstrap_code
    web_root = Path(__file__).resolve().parents[1] / "web"
    voice = VoiceService(runtime.paths)

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

    @app.exception_handler(VoiceError)
    async def voice_error(_request, exc):
        return JSONResponse({"detail": str(exc), "code": exc.code}, status_code=400)

    @app.exception_handler(MemoryInputError)
    async def memory_input(_request, _exc):
        return JSONResponse({"detail": "请检查人格或记忆字段。"}, status_code=400)

    @app.exception_handler(MemoryConflictError)
    async def memory_conflict(_request, _exc):
        return JSONResponse({"detail": "记忆已更新，请刷新后重试。"}, status_code=409)

    @app.exception_handler(MemoryAccessError)
    async def memory_missing(_request, _exc):
        return JSONResponse({"detail": "记忆不存在。"}, status_code=404)

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
        value = await body(request, 4 * 1024 * 1024 + 65536)
        if set(value) - {"text", "guide", "request_id", "image"}:
            raise HTTPException(400, "unknown turn fields")
        return await runtime.start_turn(
            session_id,
            value.get("text"),
            guide=value.get("guide", False),
            request_id=value.get("request_id"),
            image=value.get("image"),
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

    @app.post("/api/sessions/{session_id}/turns/{turn_id}/audio")
    async def audio_ack(request: Request, session_id: str, turn_id: str):
        auth(request)
        value = await body(request)
        if set(value) != {"segment_id", "state"}:
            raise HTTPException(400, "invalid audio acknowledgement")
        return runtime.audio_ack(session_id, turn_id, value["segment_id"], value["state"])

    @app.get("/api/persona")
    async def persona(request: Request):
        auth(request)
        return runtime.memory.get_persona()

    @app.put("/api/persona")
    async def update_persona(request: Request):
        auth(request)
        value = await body(request)
        expected = value.pop("version", None)
        return runtime.memory.update_persona(value, expected_version=expected)

    @app.get("/api/memories")
    async def memories(request: Request):
        auth(request)
        return {"memories": runtime.memory.list_facts(), "revision": runtime.memory.revision()}

    @app.post("/api/memories", status_code=201)
    async def remember(request: Request):
        auth(request)
        value = await body(request)
        if set(value) - {"content", "kind"}:
            raise HTTPException(400, "unknown memory fields")
        return runtime.memory.remember(
            value.get("content"),
            source_id="manual:" + uuid4().hex,
            source_text=value.get("content"),
            kind=value.get("kind", "fact"),
        )

    @app.get("/api/memories/{fact_id}/sources")
    async def memory_sources(request: Request, fact_id: str):
        auth(request)
        return {"sources": runtime.memory.sources(fact_id)}

    @app.put("/api/memories/{fact_id}")
    async def correct(request: Request, fact_id: str):
        auth(request)
        value = await body(request)
        if set(value) != {"content"}:
            raise HTTPException(400, "unknown memory fields")
        return await runtime.correct_memory(fact_id, value["content"])

    @app.delete("/api/memories/{fact_id}")
    async def forget(request: Request, fact_id: str):
        auth(request)
        return await runtime.forget_memory(fact_id)

    @app.get("/api/memory/config")
    async def memory_config(request: Request):
        auth(request)
        return dict(runtime.memory_preferences.value)

    @app.put("/api/memory/config")
    async def set_memory_config(request: Request):
        auth(request)
        value = await body(request)
        try:
            return await runtime.update_memory_preferences(value)
        except ValueError:
            raise HTTPException(400, "invalid memory configuration") from None

    @app.get("/api/voice/config")
    async def voice_config(request: Request):
        auth(request)
        return voice.public_config()

    @app.put("/api/voice/config")
    async def set_voice_config(request: Request):
        auth(request)
        try:
            return voice.update(await body(request))
        except ValueError:
            raise HTTPException(400, "语音配置未保存，请检查字段。") from None

    async def voice_request(request: Request, kind: str):
        auth(request)
        if runtime._closing:
            raise HTTPException(409, "应用正在关闭。")
        value = await body(request, 12 * 1024 * 1024 if kind == "transcribe" else 65536)
        identifier = value.pop("request_id", uuid4().hex)
        if not isinstance(identifier, str) or not re.fullmatch(r"[a-f0-9]{32}", identifier):
            raise HTTPException(400, "invalid request id")
        required = {"audio_base64", "mime_type"} if kind == "transcribe" else {"text"}
        if set(value) != required:
            raise HTTPException(400, "invalid voice fields")
        if runtime._closing or runtime._closed or runtime._memory_mutating:
            raise HTTPException(409, "应用正在关闭或更新记忆，请稍后重试。")
        if runtime.voice_cancelled.get(identifier, 0) > time.monotonic():
            raise HTTPException(409, "语音请求已停止。")
        if identifier in runtime.voice_tasks or len(runtime.voice_tasks) >= 4:
            raise HTTPException(409, "语音请求仍在处理中。")
        task = asyncio.create_task(getattr(voice, kind)(**value))
        runtime.voice_tasks[identifier] = task
        try:
            return await task
        except asyncio.CancelledError:
            raise HTTPException(409, "语音请求已停止。") from None
        finally:
            runtime.voice_tasks.pop(identifier, None)

    @app.post("/api/voice/transcribe")
    async def transcribe(request: Request):
        return await voice_request(request, "transcribe")

    @app.post("/api/voice/synthesize")
    async def synthesize(request: Request):
        return await voice_request(request, "synthesize")

    @app.post("/api/voice/cancel")
    async def stop_voice(request: Request):
        auth(request)
        value = await body(request)
        identifier = value.get("request_id")
        if (
            set(value) != {"request_id"}
            or not isinstance(identifier, str)
            or not re.fullmatch(r"[a-f0-9]{32}", identifier)
        ):
            raise HTTPException(400, "invalid request id")
        now = time.monotonic()
        runtime.voice_cancelled = {
            key: expiry for key, expiry in runtime.voice_cancelled.items() if expiry > now
        }
        if len(runtime.voice_cancelled) >= 1024 and identifier not in runtime.voice_cancelled:
            raise HTTPException(429, "请稍后重试。")
        runtime.voice_cancelled[identifier] = now + 180
        task = runtime.voice_tasks.get(identifier)
        if task is not None:
            task.cancel()
        return {"cancelled": task is not None}
