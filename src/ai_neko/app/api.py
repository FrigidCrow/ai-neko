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
    from ai_neko.memory.guides import (
        GuideAccessError,
        GuideCapacityError,
        GuideConflictError,
        GuideInputError,
    )
    from ai_neko.providers import ProviderError
    from ai_neko.runtime import RuntimeAccessError, RuntimeConflictError, RuntimeInputError
    from ai_neko.tools.network import NetworkPolicyError, parse_url

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

    @app.exception_handler(GuideInputError)
    async def guide_input(_request, _exc):
        return JSONResponse({"detail": "请检查攻略字段或操作参数。"}, status_code=400)

    @app.exception_handler(GuideAccessError)
    async def guide_missing(_request, _exc):
        return JSONResponse({"detail": "攻略、版本或备份不存在。"}, status_code=404)

    @app.exception_handler(GuideConflictError)
    async def guide_conflict(_request, _exc):
        return JSONResponse({"detail": "攻略状态已变化，请刷新后重试。"}, status_code=409)

    @app.exception_handler(GuideCapacityError)
    async def guide_capacity(_request, _exc):
        return JSONResponse({"detail": "攻略存储容量不足，请清理后重试。"}, status_code=507)

    @app.exception_handler(RuntimeAccessError)
    async def missing(_request, _exc):
        return JSONResponse({"detail": "会话、回合或对局不存在。"}, status_code=404)

    @app.exception_handler(RuntimeConflictError)
    async def conflict(_request, _exc):
        return JSONResponse(
            {"detail": "当前会话或对局状态不允许此操作，请刷新后重试。"}, status_code=409
        )

    @app.exception_handler(RuntimeInputError)
    async def invalid(_request, _exc):
        return JSONResponse({"detail": "请检查输入、回合或对局参数。"}, status_code=400)

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

    def match_identifier(value, *, match=False, nullable=False):
        if nullable and value is None:
            return
        pattern = r"match-[a-f0-9]{32}" if match else r"[a-f0-9]{32}"
        if not isinstance(value, str) or not re.fullmatch(pattern, value):
            raise RuntimeInputError("invalid_match_identifier")

    def match_revision(value):
        if type(value) is not int or value < 0:
            raise RuntimeInputError("invalid_match_revision")

    def match_fields(value, required, optional=()):
        if not required <= set(value) or set(value) - required - set(optional):
            raise RuntimeInputError("invalid_match_fields")

    def match_binding(value):
        if not isinstance(value, dict):
            raise RuntimeInputError("invalid_match_binding")
        match_fields(value, {"match_id", "expected_revision"})
        match_identifier(value["match_id"], match=True, nullable=True)
        match_revision(value["expected_revision"])

    def match_query(request):
        if request.query_params:
            raise RuntimeInputError("invalid_match_query")

    def match_text(value, maximum, *, required=False, nullable=False):
        if nullable and value is None:
            return
        if (
            not isinstance(value, str)
            or len(value) > maximum
            or (required and not value.strip())
            or any(ord(char) < 32 or ord(char) == 127 for char in value)
            or any(0xD800 <= ord(char) <= 0xDFFF for char in value)
        ):
            raise RuntimeInputError("invalid_match_text")

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
        if set(value) - {
            "text",
            "guide",
            "request_id",
            "image",
            "match",
            "input_origin",
            "review_match_id",
            "guide_target",
        }:
            raise HTTPException(400, "unknown turn fields")
        additional = {}
        if "guide_target" in value:
            from ai_neko.runtime.controls import validate_guide_target

            additional["guide_target"] = validate_guide_target(value["guide_target"])
        if "match" in value:
            match_binding(value["match"])
            additional["match"] = value["match"]
        if "input_origin" in value:
            if not isinstance(value["input_origin"], str) or value["input_origin"] not in {
                "text",
                "voice",
            }:
                raise RuntimeInputError("invalid_input_origin")
            additional["input_origin"] = value["input_origin"]
        if "review_match_id" in value:
            match_identifier(value["review_match_id"], match=True, nullable=True)
            additional["review_match_id"] = value["review_match_id"]
        return await runtime.start_turn(
            session_id,
            value.get("text"),
            guide=value.get("guide", False),
            request_id=value.get("request_id"),
            image=value.get("image"),
            **additional,
        )

    @app.get("/api/sessions/{session_id}/control-jobs/{job_id}")
    async def control_job(request: Request, session_id: str, job_id: str):
        auth(request)
        match_query(request)
        match_identifier(session_id)
        match_identifier(job_id)
        return runtime.control_job(session_id, job_id)

    @app.post("/api/sessions/{session_id}/control-jobs/{job_id}/cancel")
    async def cancel_control_job(request: Request, session_id: str, job_id: str):
        auth(request)
        match_query(request)
        match_identifier(session_id)
        match_identifier(job_id)
        if await body(request):
            raise RuntimeInputError("操作取消不接受额外参数。")
        return await runtime.cancel_control_job(session_id, job_id)

    @app.get("/api/sessions/{session_id}/matches")
    async def match_catalog(request: Request, session_id: str):
        auth(request)
        match_query(request)
        match_identifier(session_id)
        return await runtime.match_catalog(session_id)

    @app.get("/api/sessions/{session_id}/matches/{match_id}")
    async def match_detail(request: Request, session_id: str, match_id: str):
        auth(request)
        match_query(request)
        match_identifier(session_id)
        match_identifier(match_id, match=True)
        return await runtime.match_detail(session_id, match_id)

    async def match_mutation(request, session_id, action, match_id=None):
        auth(request)
        match_query(request)
        match_identifier(session_id)
        if match_id is not None:
            match_identifier(match_id, match=True)
        value = await body(request)
        required = {"request_id", "expected_revision"}
        optional = set()
        if action in {"start", "new"}:
            required |= {"game", "platform", "mode"}
            optional = {"game_version", "goal"}
        elif action == "update":
            optional = {"goal", "game_version"}
        elif action == "observe":
            required |= {"turn_id", "text"}
        match_fields(value, required, optional)
        if action == "update" and not {"goal", "game_version"}.intersection(value):
            raise RuntimeInputError("empty_match_update")
        match_identifier(value["request_id"])
        match_revision(value["expected_revision"])
        if action in {"start", "new"}:
            for key in ("game", "platform", "mode"):
                match_text(value[key], 200, required=True)
        if "game_version" in value:
            match_text(value["game_version"], 200, nullable=True)
        if "goal" in value:
            match_text(value["goal"], 2000)
        if action == "observe":
            match_identifier(value["turn_id"])
            match_text(value["text"], 2000, required=True)
        if match_id is not None:
            value = {**value, "match_id": match_id}
        return await runtime.match_control(session_id, action, value)

    @app.post("/api/sessions/{session_id}/matches", status_code=201)
    async def start_match(request: Request, session_id: str):
        return await match_mutation(request, session_id, "start")

    @app.post("/api/sessions/{session_id}/matches/{match_id}/new", status_code=201)
    async def new_match(request: Request, session_id: str, match_id: str):
        return await match_mutation(request, session_id, "new", match_id)

    @app.post("/api/sessions/{session_id}/matches/{match_id}/end")
    async def end_match(request: Request, session_id: str, match_id: str):
        return await match_mutation(request, session_id, "end", match_id)

    @app.post("/api/sessions/{session_id}/matches/{match_id}/update")
    async def update_match(request: Request, session_id: str, match_id: str):
        return await match_mutation(request, session_id, "update", match_id)

    @app.post("/api/sessions/{session_id}/matches/{match_id}/observations")
    async def observe_match(request: Request, session_id: str, match_id: str):
        return await match_mutation(request, session_id, "observe", match_id)

    @app.post("/api/sessions/{session_id}/matches/{match_id}/close-observation")
    async def close_match_observation(request: Request, session_id: str, match_id: str):
        return await match_mutation(request, session_id, "close_observation", match_id)

    @app.get("/api/sessions/{session_id}/turns/{turn_id}/events")
    async def events(request: Request, session_id: str, turn_id: str):
        auth(request)
        raw = request.query_params.get("after", "0")
        if not raw.isascii() or not raw.isdigit() or len(raw) > 9:
            raise HTTPException(400, "invalid event cursor")
        return runtime.events(session_id, turn_id, int(raw))

    @app.post("/api/sessions/{session_id}/requests/{request_id}/cancel")
    async def cancel_request(request: Request, session_id: str, request_id: str):
        auth(request)
        if await body(request):
            raise HTTPException(400, "unknown request cancellation fields")
        return await runtime.cancel_request(session_id, request_id)

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
        if not {"segment_id", "state"} <= set(value) or set(value) - {
            "segment_id",
            "state",
            "text_start",
            "text_end",
        }:
            raise HTTPException(400, "invalid audio acknowledgement")
        if ("text_start" in value) != ("text_end" in value):
            raise HTTPException(400, "incomplete audio range")
        if "text_start" in value and (
            type(value["text_start"]) is not int or type(value["text_end"]) is not int
        ):
            raise HTTPException(400, "invalid audio range")
        return runtime.audio_ack(
            session_id,
            turn_id,
            value["segment_id"],
            value["state"],
            text_start=value.get("text_start"),
            text_end=value.get("text_end"),
        )

    @app.get("/api/persona")
    async def persona(request: Request):
        auth(request)
        return await runtime.memory_api(runtime.memory.get_persona)

    @app.put("/api/persona")
    async def update_persona(request: Request):
        auth(request)
        value = await body(request)
        expected = value.pop("version", None)
        return await runtime.memory_api(
            runtime.memory.update_persona, value, expected_version=expected, mutation=True
        )

    @app.get("/api/memories")
    async def memories(request: Request):
        auth(request)
        return await runtime.memory_api(
            lambda: {"memories": runtime.memory.list_facts(), "revision": runtime.memory.revision()}
        )

    @app.post("/api/memories", status_code=201)
    async def remember(request: Request):
        auth(request)
        value = await body(request)
        if set(value) - {"content", "kind"}:
            raise HTTPException(400, "unknown memory fields")
        return await runtime.memory_api(
            runtime.memory.remember,
            value.get("content"),
            mutation=True,
            source_id="manual:" + uuid4().hex,
            source_text=value.get("content"),
            kind=value.get("kind", "fact"),
        )

    @app.get("/api/memories/{fact_id}/sources")
    async def memory_sources(request: Request, fact_id: str):
        auth(request)
        return {"sources": await runtime.memory_api(runtime.memory.sources, fact_id)}

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

    @app.get("/api/memory/backups")
    async def memory_backups(request: Request):
        auth(request)
        return await runtime.memory_backups()

    @app.post("/api/memory/backups", status_code=201)
    async def backup_memory(request: Request):
        auth(request)
        if await body(request):
            raise HTTPException(400, "unknown backup fields")
        return await runtime.backup_memory()

    @app.post("/api/memory/backups/{backup_id}/restore")
    async def restore_memory(request: Request, backup_id: str):
        auth(request)
        value = await body(request)
        if (
            set(value) != {"confirm", "expected_revision"}
            or value["confirm"] is not True
            or type(value["expected_revision"]) is not int
            or value["expected_revision"] < 0
        ):
            raise HTTPException(400, "explicit restore confirmation and revision required")
        return await runtime.restore_memory(backup_id, value["expected_revision"])

    @app.delete("/api/memory/backups/{backup_id}")
    async def delete_memory_backup(request: Request, backup_id: str):
        auth(request)
        return await runtime.delete_memory_backup(backup_id)

    @app.put("/api/memory/config")
    async def set_memory_config(request: Request):
        auth(request)
        value = await body(request)
        try:
            return await runtime.update_memory_preferences(value)
        except ValueError:
            raise HTTPException(400, "invalid memory configuration") from None

    def guide_identifier(value, kind: str):
        patterns = {
            "guide": r"guide-[a-f0-9]{32}",
            "revision": r"revision-[a-f0-9]{32}",
            "request": r"[a-f0-9]{32}",
            "backup": r"guides-[a-f0-9]{32}\.sqlite",
        }
        if not isinstance(value, str) or not re.fullmatch(patterns[kind], value):
            raise GuideInputError("invalid_identifier")

    def guide_query(request: Request, *, detail: bool = False):
        pairs = list(request.query_params.multi_items())
        if detail and len(pairs) == 1 and pairs[0][0] == "revision_id":
            guide_identifier(pairs[0][1], "revision")
            return pairs[0][1]
        if pairs:
            raise GuideInputError("invalid_query")
        return None

    def guide_fields(value: dict, required: set, optional: set | None = None):
        if not required <= set(value) or set(value) - required - (optional or set()):
            raise GuideInputError("invalid_fields")

    def guide_controls(value: dict):
        guide_identifier(value.get("request_id"), "request")
        revision = value.get("expected_revision")
        if type(revision) is not int or revision < 0:
            raise GuideInputError("invalid_expected_revision")

    def guide_text(value, maximum: int, *, required: bool = False, nullable: bool = False):
        if nullable and value is None:
            return
        if (
            not isinstance(value, str)
            or len(value) > maximum
            or (required and not value.strip())
            or any(ord(char) < 32 or ord(char) == 127 for char in value)
            or any(0xD800 <= ord(char) <= 0xDFFF for char in value)
        ):
            raise GuideInputError("invalid_text")

    guide_control_fields = {"request_id", "expected_revision"}

    @app.get("/api/guides")
    async def guides(request: Request):
        auth(request)
        guide_query(request)
        return await runtime.guide_catalog()

    @app.get("/api/guides/{guide_id}")
    async def guide_detail(request: Request, guide_id: str):
        auth(request)
        guide_identifier(guide_id, "guide")
        revision_id = guide_query(request, detail=True)
        return await runtime.guide_detail(guide_id, revision_id=revision_id)

    @app.put("/api/guide-selection")
    async def guide_selection(request: Request):
        auth(request)
        guide_query(request)
        value = await body(request)
        guide_fields(
            value, guide_control_fields | {"game", "platform", "mode", "guide_id", "revision_id"}
        )
        guide_controls(value)
        for field in ("game", "platform", "mode"):
            guide_text(value[field], 200, required=True)
        if value["guide_id"] is not None or value["revision_id"] is not None:
            guide_identifier(value["guide_id"], "guide")
            guide_identifier(value["revision_id"], "revision")
        return await runtime.guide_selection(value)

    @app.post("/api/guides", status_code=202)
    async def guide_import(request: Request):
        auth(request)
        guide_query(request)
        value = await body(request)
        guide_fields(
            value,
            guide_control_fields | {"url"},
            {"game", "platform", "mode", "game_version", "version_basis"},
        )
        guide_controls(value)
        guide_text(value["url"], 4096, required=True)
        try:
            parse_url(value["url"])
        except NetworkPolicyError:
            raise GuideInputError("invalid_url") from None
        for field in ("game", "platform", "mode", "game_version", "version_basis"):
            if field in value:
                guide_text(
                    value[field],
                    2000 if field == "version_basis" else 200,
                    nullable=field in {"game_version", "version_basis"},
                )
        return await runtime.guide_fetch(value)

    @app.post("/api/guides/{guide_id}/refresh", status_code=202)
    async def guide_refresh(request: Request, guide_id: str):
        auth(request)
        guide_query(request)
        guide_identifier(guide_id, "guide")
        value = await body(request)
        guide_fields(value, guide_control_fields)
        guide_controls(value)
        return await runtime.guide_fetch(value, guide_id=guide_id)

    @app.delete("/api/guides/{guide_id}")
    async def guide_delete(request: Request, guide_id: str):
        auth(request)
        guide_query(request)
        guide_identifier(guide_id, "guide")
        value = await body(request)
        guide_fields(value, guide_control_fields | {"confirm"})
        guide_controls(value)
        if value["confirm"] is not True:
            raise GuideInputError("confirmation_required")
        return await runtime.guide_delete(guide_id, value)

    @app.get("/api/guide-operations/{request_id}")
    async def guide_operation(request: Request, request_id: str):
        auth(request)
        guide_query(request)
        guide_identifier(request_id, "request")
        return await runtime.guide_operation(request_id)

    @app.post("/api/guide-operations/{request_id}/cancel")
    async def guide_cancel(request: Request, request_id: str):
        auth(request)
        guide_query(request)
        guide_identifier(request_id, "request")
        if await body(request):
            raise GuideInputError("invalid_cancel_fields")
        return await runtime.cancel_guide_operation(request_id)

    @app.get("/api/guide-backups")
    async def guide_backups(request: Request):
        auth(request)
        guide_query(request)
        return await runtime.guide_backups()

    @app.post("/api/guide-backups", status_code=201)
    async def backup_guides(request: Request):
        auth(request)
        guide_query(request)
        value = await body(request)
        guide_fields(value, {"request_id"})
        guide_identifier(value["request_id"], "request")
        return await runtime.backup_guides(value)

    @app.post("/api/guide-backups/{backup_id}/restore")
    async def restore_guides(request: Request, backup_id: str):
        auth(request)
        guide_query(request)
        guide_identifier(backup_id, "backup")
        value = await body(request)
        guide_fields(value, guide_control_fields | {"confirm"})
        guide_controls(value)
        if value["confirm"] is not True:
            raise GuideInputError("confirmation_required")
        return await runtime.restore_guides(backup_id, value)

    @app.delete("/api/guide-backups/{backup_id}")
    async def delete_guide_backup(request: Request, backup_id: str):
        auth(request)
        guide_query(request)
        guide_identifier(backup_id, "backup")
        async for chunk in request.stream():
            if chunk:
                raise GuideInputError("unexpected_body")
        return await runtime.delete_guide_backup(backup_id)

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
        has_session, has_match = "session_id" in value, "match" in value
        if has_session != has_match:
            raise RuntimeInputError("incomplete_voice_match_binding")
        session_id = value.pop("session_id", None)
        binding = value.pop("match", None)
        has_turn = "turn_id" in value
        turn_id = value.pop("turn_id", None)
        if has_turn:
            if not has_session or kind != "synthesize":
                raise RuntimeInputError("incomplete_voice_turn_binding")
            match_identifier(turn_id)
        if has_session:
            match_identifier(session_id)
            match_binding(binding)
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
        if has_session:
            runtime.validate_match_request(session_id, binding)
            if turn_id is not None:
                runtime.validate_voice_turn(session_id, turn_id)
        task = asyncio.create_task(getattr(voice, kind)(**value))
        runtime.voice_tasks[identifier] = task
        try:
            result = await task
            if has_session:
                runtime.validate_match_request(session_id, binding)
                if turn_id is not None:
                    runtime.validate_voice_turn(session_id, turn_id)
            return result
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
