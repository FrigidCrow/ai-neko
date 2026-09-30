"""Authenticated G5 control jobs and response-bound TTS through the real API."""

import asyncio
from uuid import uuid4

import pytest
from test_control_runtime import finish, send, target
from test_match_retrieval_runtime import CaptureProviders
from test_memory_backup_api import api_client

from ai_neko.config.paths import initialize_data_root
from ai_neko.media import VoiceService
from ai_neko.runtime import SessionRuntime


def test_actual_turn_target_job_response_ack_and_voice_contract(tmp_path, monkeypatch):
    async def run():
        runtime = SessionRuntime(initialize_data_root(tmp_path / "api"), CaptureProviders())
        calls = []

        async def synthesize(_self, **kwargs):
            calls.append(kwargs)
            return {"audio_base64": "U1lOVEhFVElD", "mime_type": "audio/wav"}

        monkeypatch.setattr(VoiceService, "synthesize", synthesize)
        try:
            sid = runtime.create_session()["id"]
            selected = await target(runtime)
            async with api_client(runtime) as client:
                response = await client.post(
                    f"/api/sessions/{sid}/turns",
                    json={
                        "text": "按这份攻略",
                        "input_origin": "voice",
                        "guide_target": selected,
                        "request_id": uuid4().hex,
                    },
                )
                assert response.status_code == 202
                batch, job = await finish(runtime, sid, response.json()["id"])
                base = f"/api/sessions/{sid}/control-jobs/{job['id']}"
                fetched = await client.get(base)
                assert (
                    fetched.status_code == 200
                    and fetched.json()["response_turn_id"] == job["response_turn_id"]
                )
                current = job["response_turn"]["context"]["match"]
                runtime.events(sid, job["response_turn_id"])
                binding = {key: current[key] for key in ("match_id", "expected_revision")}
                payload = {
                    "text": "已经采用所选攻略。",
                    "session_id": sid,
                    "match": binding,
                    "turn_id": job["response_turn_id"],
                }
                voice = await client.post("/api/voice/synthesize", json=payload)
                assert voice.status_code == 200 and calls == [{"text": payload["text"]}]
                cancelled = await client.post(base + "/cancel", json={})
                assert cancelled.status_code == 200 and cancelled.json()["confirmation_cancelled"]
                refused = await client.post("/api/voice/synthesize", json=payload)
                assert refused.status_code == 409 and len(calls) == 1
                other = runtime.create_session()["id"]
                assert (
                    await client.get(f"/api/sessions/{other}/control-jobs/{job['id']}")
                ).status_code == 404
                assert batch["control_job_id"] == job["id"]
        finally:
            await runtime.close()

    asyncio.run(run())


def test_cancel_during_response_tts_discards_noncooperative_late_audio(tmp_path, monkeypatch):
    async def run():
        runtime = SessionRuntime(initialize_data_root(tmp_path / "late-audio"), CaptureProviders())
        entered, release = asyncio.Event(), asyncio.Event()

        async def synthesize(_self, **_kwargs):
            entered.set()
            await release.wait()
            return {"audio_base64": "LATE_CONTROL_AUDIO"}

        monkeypatch.setattr(VoiceService, "synthesize", synthesize)
        try:
            sid = runtime.create_session()["id"]
            turn = await send(runtime, sid, "按这份攻略", selected=await target(runtime))
            _, job = await finish(runtime, sid, turn["id"])
            saved = job["response_turn"]["context"]["match"]
            runtime.events(sid, job["response_turn_id"])
            async with api_client(runtime) as client:
                pending = asyncio.create_task(
                    client.post(
                        "/api/voice/synthesize",
                        json={
                            "text": "操作成功",
                            "session_id": sid,
                            "match": {key: saved[key] for key in ("match_id", "expected_revision")},
                            "turn_id": job["response_turn_id"],
                        },
                    )
                )
                await asyncio.wait_for(entered.wait(), 2)
                await client.post(f"/api/sessions/{sid}/control-jobs/{job['id']}/cancel", json={})
                release.set()
                response = await pending
                assert response.status_code == 409 and "LATE_CONTROL_AUDIO" not in response.text
        finally:
            release.set()
            await runtime.close()

    asyncio.run(run())


@pytest.mark.parametrize("control_response", [False, True])
def test_no_match_guide_change_rejects_late_tts_using_persisted_guide_revision(
    tmp_path, monkeypatch, control_response
):
    async def run():
        runtime = SessionRuntime(initialize_data_root(tmp_path / "guide-tts"), CaptureProviders())
        entered, release = asyncio.Event(), asyncio.Event()

        async def synthesize(_self, **_kwargs):
            entered.set()
            try:
                await release.wait()
            except asyncio.CancelledError:
                return {"audio_base64": "LATE_OLD_GUIDE_AUDIO"}
            return {"audio_base64": "LATE_OLD_GUIDE_AUDIO"}

        monkeypatch.setattr(VoiceService, "synthesize", synthesize)
        try:
            sid = runtime.create_session()["id"]
            selected = await target(runtime)
            if control_response:
                turn = await send(runtime, sid, "按这份攻略", selected=selected)
                _, job = await finish(runtime, sid, turn["id"])
                tid = job["response_turn_id"]
            else:
                turn = await runtime.start_turn(sid, "你好")
                await finish(runtime, sid, turn["id"], job=False)
                tid = turn["id"]
            runtime.events(sid, tid)
            async with api_client(runtime) as client:
                pending = asyncio.create_task(
                    client.post(
                        "/api/voice/synthesize",
                        json={
                            "text": "原回复",
                            "session_id": sid,
                            "match": {"match_id": None, "expected_revision": 0},
                            "turn_id": tid,
                        },
                    )
                )
                await asyncio.wait_for(entered.wait(), 2)
                await runtime.guide_selection(
                    {
                        **selected,
                        "expected_revision": runtime.memory.guides.revision(),
                        "request_id": uuid4().hex,
                    }
                )
                release.set()
                response = await pending
                assert response.status_code == 409 and "LATE_OLD_GUIDE_AUDIO" not in response.text
                # There was no match revision change: the persisted guide
                # revision, not a live graph registry, rejected the old audio.
                assert runtime.matches.revision(sid) == 0
        finally:
            release.set()
            await runtime.close()

    asyncio.run(run())


@pytest.mark.parametrize("method,suffix", [("GET", ""), ("POST", "/cancel")])
def test_control_routes_enforce_auth_shape_and_scope_before_access(tmp_path, method, suffix):
    async def run():
        runtime = SessionRuntime(initialize_data_root(tmp_path / "bounds"), CaptureProviders())
        try:
            sid = runtime.create_session()["id"]
            base = f"/api/sessions/{sid}/control-jobs/{uuid4().hex}" + suffix
            async with api_client(runtime) as client:
                assert (
                    await client.request(
                        method, base, headers={"Authorization": ""}, content=b"bad"
                    )
                ).status_code == 401
                assert (
                    await client.request(
                        method, base, headers={"Origin": "https://example.com"}, content=b"bad"
                    )
                ).status_code == 403
                assert (
                    await client.request(
                        method, base + "?scope=other", json={} if method == "POST" else None
                    )
                ).status_code == 400
                if method == "POST":
                    assert (await client.post(base, json={"guide_id": "forged"})).status_code == 400
                response = await client.request(
                    method, base, **({"json": {}} if method == "POST" else {})
                )
                assert response.status_code == 404
        finally:
            await runtime.close()

    asyncio.run(run())
