"""Qwen application integration with synthetic credentials/audio and no models."""

import asyncio
import json
from contextlib import asynccontextmanager

import httpx
import pytest
from test_runtime import Store
from test_voice import AUDIO, AUDIO_B64, FakeVault

from ai_neko.app.server import Connection, create_app
from ai_neko.config import credentials
from ai_neko.config.paths import initialize_data_root
from ai_neko.media.local_voice import LocalVoiceError
from ai_neko.media.qwen_voice import QwenVoiceError
from ai_neko.media.voice import DEFAULTS, VoiceError, VoiceService
from ai_neko.runtime import SessionRuntime


@pytest.fixture(autouse=True)
def isolated_credentials(monkeypatch):
    for name in ("AI_NEKO_ASR_API_KEY", "AI_NEKO_TTS_API_KEY"):
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setattr(credentials, "WindowsVault", FakeVault)


@pytest.fixture
def service(tmp_path):
    return VoiceService(initialize_data_root(tmp_path / "qwen-integration"))


@asynccontextmanager
async def api_client(tmp_path):
    runtime = SessionRuntime(initialize_data_root(tmp_path / "qwen-api"), Store())
    connection = Connection(43215, "d" * 43, "a" * 32)
    app = create_app(connection, lambda: None, runtime=runtime, providers=runtime.providers)
    try:
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app),
            base_url=connection.url,
            headers={"Authorization": "Bearer " + connection.token},
        ) as client:
            yield client, app.state.voice, runtime
    finally:
        await runtime.close()
        await app.state.voice.close()


def select_qwen(service):
    service.update({"voice_provider": "local", "local_tts_engine": "qwen"})


@pytest.mark.parametrize("shape", [5, 6, 7])
def test_released_configs_gain_kokoro_without_changing_fields_or_keys(service, shape):
    service.update({"asr_api_key": "synthetic-asr", "tts_api_key": "synthetic-tts"})
    original = {
        "asr_base_url": "https://asr.example.com/v1",
        "asr_model": "synthetic-asr-model",
        "tts_base_url": "https://tts.example.com/v1",
        "tts_model": "synthetic-tts-model",
        "tts_voice": "synthetic-voice",
    }
    if shape >= 6:
        original["voice_provider"] = "local"
    if shape == 7:
        original["local_tts_speaker_id"] = 57
    (service.paths.config / "voice.json").write_text(
        json.dumps({"app_id": "ai-neko", "schema_version": 1, "voice": original}),
        encoding="utf-8",
    )
    restored = VoiceService(service.paths)
    config = restored.public_config()
    assert all(config[name] == value for name, value in original.items())
    assert config["local_tts_engine"] == "kokoro"
    assert config["local_tts_speaker_id"] == (57 if shape == 7 else 46)
    assert config["voice_provider"] == ("local" if shape >= 6 else "openai")
    assert restored._credentials.get("asr") == "synthetic-asr"
    assert restored._credentials.get("tts") == "synthetic-tts"
    select_qwen(restored)
    persisted = json.loads((service.paths.config / "voice.json").read_text(encoding="utf-8"))
    assert persisted["voice"]["local_tts_engine"] == "qwen"
    assert set(persisted["voice"]) == set(DEFAULTS)
    assert 'synthetic-asr"' not in json.dumps(persisted)
    assert (
        VoiceService(service.paths).public_config()["local_tts_speaker_id"]
        == config["local_tts_speaker_id"]
    )


@pytest.mark.parametrize("value", [None, True, 0, "Qwen", "", "other", [], {}])
def test_engine_is_strict_and_bad_update_is_atomic(service, value):
    before = service.public_config()
    with pytest.raises(ValueError, match="invalid_voice_config"):
        service.update({"local_tts_engine": value, "tts_api_key": "must-not-be-written"})
    assert service.public_config() == before
    assert not (service.paths.config / "voice.json").exists()
    assert service._credentials.get("tts") is None


def test_unknown_partial_saved_shape_is_not_treated_as_a_released_config(service):
    config = {name: value for name, value in DEFAULTS.items() if name != "local_tts_speaker_id"}
    (service.paths.config / "voice.json").write_text(
        json.dumps({"app_id": "ai-neko", "schema_version": 1, "voice": config}),
        encoding="utf-8",
    )
    with pytest.raises(ValueError, match="invalid_voice_config"):
        VoiceService(service.paths)


def test_qwen_only_handles_tts_and_asr_keeps_sensevoice_without_cloud(service, monkeypatch):
    select_qwen(service)
    calls = []

    async def qwen(text):
        calls.append(("qwen", text))
        return AUDIO

    async def asr(wav):
        assert wav == AUDIO
        calls.append(("asr", None))
        return "synthetic transcript"

    async def forbidden(*args, **kwargs):
        raise AssertionError("Qwen selection must not invoke Kokoro or cloud TTS")

    monkeypatch.setattr(service.qwen, "synthesize", qwen)
    monkeypatch.setattr(service.local, "transcribe", asr)
    monkeypatch.setattr(service.local, "synthesize", forbidden)
    monkeypatch.setattr(service, "_request", forbidden)

    async def run():
        assert await service.synthesize("synthetic") == {
            "audio_base64": AUDIO_B64,
            "mime_type": "audio/wav",
        }
        assert await service.transcribe(AUDIO_B64, "audio/wav") == {"text": "synthetic transcript"}
        await service.close()

    asyncio.run(run())
    assert calls == [("qwen", "synthetic"), ("asr", None)]


def test_qwen_failure_is_bounded_and_has_no_fallback(service, monkeypatch):
    select_qwen(service)

    async def failed(text):
        raise QwenVoiceError("qwen_gpu_unavailable", "GPU unavailable; choose another engine.")

    async def forbidden(*args, **kwargs):
        raise AssertionError("No implicit fallback is allowed")

    monkeypatch.setattr(service.qwen, "synthesize", failed)
    monkeypatch.setattr(service.local, "synthesize", forbidden)
    monkeypatch.setattr(service, "_request", forbidden)
    with pytest.raises(VoiceError) as error:
        asyncio.run(service.synthesize("synthetic"))
    assert error.value.code == "qwen_gpu_unavailable"
    assert error.value.message == "GPU unavailable; choose another engine."


@pytest.mark.parametrize("payload", [b"not a wav stream", None, "not bytes"])
def test_invalid_qwen_output_does_not_escape_as_playable_audio(service, monkeypatch, payload):
    select_qwen(service)

    async def invalid(text):
        return payload

    monkeypatch.setattr(service.qwen, "synthesize", invalid)
    with pytest.raises(VoiceError):
        asyncio.run(service.synthesize("synthetic"))


def test_close_attempts_both_owned_services_even_after_local_failure(service, monkeypatch):
    calls = []

    async def local_close():
        calls.append("local")
        raise RuntimeError("synthetic cleanup error")

    async def qwen_close():
        calls.append("qwen")

    monkeypatch.setattr(service.local, "close", local_close)
    monkeypatch.setattr(service.qwen, "close", qwen_close)
    with pytest.raises(RuntimeError, match="synthetic cleanup error"):
        asyncio.run(service.close())
    assert calls == ["local", "qwen"]


def test_qwen_routes_require_auth_selection_and_empty_parameters(tmp_path, monkeypatch):
    async def run():
        async with api_client(tmp_path) as (client, voice, runtime):
            calls = []

            async def preload():
                calls.append("load")
                return {"state": "ready", "loaded": True}

            async def unload():
                calls.append("unload")

            monkeypatch.setattr(voice.qwen, "preload", preload)
            monkeypatch.setattr(voice.qwen, "unload", unload)
            for method, path in (
                ("GET", "/api/voice/qwen/status"),
                ("POST", "/api/voice/qwen/load"),
                ("POST", "/api/voice/qwen/unload"),
            ):
                payload = {} if method == "POST" else None
                response = await client.request(
                    method, path, json=payload, headers={"Authorization": "Bearer wrong"}
                )
                assert response.status_code == 401
                response = await client.request(method, path + "?path=C:/foreign", json=payload)
                assert response.status_code == 400
                response = await client.request(method, path, json={"path": "C:/foreign"})
                assert response.status_code == 400
            assert not calls
            assert (await client.post("/api/voice/qwen/load", json={})).status_code == 409
            select_qwen(voice)
            status = await client.get("/api/voice/qwen/status")
            assert status.status_code == 200
            assert {
                "state",
                "message",
                "supported",
                "loaded",
                "gpu_required",
            } <= status.json().keys()
            for payload in ([], None, {"python": "C:/foreign"}, {"confirm": True}):
                response = await client.post("/api/voice/qwen/load", json=payload)
                assert response.status_code in {400, 415}
            assert (await client.post("/api/voice/qwen/load", json={})).json()["loaded"]
            assert (await client.post("/api/voice/qwen/unload", json={})).status_code == 200
            assert calls == ["load", "unload"]
            await runtime.close()
            for method, path in (
                ("GET", "/api/voice/qwen/status"),
                ("POST", "/api/voice/qwen/load"),
                ("POST", "/api/voice/qwen/unload"),
            ):
                response = await client.request(method, path, json={} if method == "POST" else None)
                assert response.status_code == 409

    asyncio.run(run())


@pytest.mark.parametrize("action", ["switch", "unload", "exit"])
def test_deduplicated_preload_is_tracked_cancelled_and_drained(tmp_path, monkeypatch, action):
    async def run():
        async with api_client(tmp_path) as (client, voice, runtime):
            select_qwen(voice)
            started, cleaning, release = asyncio.Event(), asyncio.Event(), asyncio.Event()
            calls = []

            async def preload():
                calls.append("load")
                started.set()
                try:
                    await asyncio.Event().wait()
                finally:
                    cleaning.set()
                    await release.wait()

            async def unload():
                assert release.is_set()
                calls.append("unload")

            monkeypatch.setattr(voice.qwen, "preload", preload)
            monkeypatch.setattr(voice.qwen, "unload", unload)
            first = asyncio.create_task(client.post("/api/voice/qwen/load", json={}))
            await asyncio.wait_for(started.wait(), 2)
            second = asyncio.create_task(client.post("/api/voice/qwen/load", json={}))
            # Status completes after the duplicate has passed admission.
            await client.get("/api/voice/qwen/status")
            assert list(runtime.voice_tasks) == ["qwen-preload"]
            assert calls == ["load"]
            if action == "switch":
                operation = client.put("/api/voice/config", json={"local_tts_engine": "kokoro"})
            elif action == "unload":
                operation = client.post("/api/voice/qwen/unload", json={})
            else:
                operation = runtime.close()
            transition = asyncio.create_task(operation)
            await asyncio.wait_for(cleaning.wait(), 2)
            assert not transition.done()
            release.set()
            await asyncio.wait_for(transition, 2)
            assert (await first).status_code == 409
            assert (await second).status_code == 409
            assert "qwen-preload" not in runtime.voice_tasks
            assert calls == (["load"] if action == "exit" else ["load", "unload"])

    asyncio.run(run())


@pytest.mark.parametrize("start,finish", [("qwen", "kokoro"), ("kokoro", "qwen")])
def test_engine_switch_reaps_old_child_before_admitting_new_synthesis(
    tmp_path, monkeypatch, start, finish
):
    async def run():
        async with api_client(tmp_path) as (client, voice, runtime):
            voice.update({"voice_provider": "local", "local_tts_engine": start})
            started, unloading, release = asyncio.Event(), asyncio.Event(), asyncio.Event()
            calls = []
            old = voice.qwen if start == "qwen" else voice.local
            new = voice.qwen if finish == "qwen" else voice.local

            async def synth_old(*args, **kwargs):
                started.set()
                try:
                    await asyncio.Event().wait()
                finally:
                    calls.append("old-cancelled")

            async def unload():
                calls.append("unloading")
                unloading.set()
                await release.wait()
                calls.append("reaped")

            async def synth_new(*args, **kwargs):
                assert calls[-1] == "reaped"
                calls.append("new-synthesis")
                return AUDIO

            monkeypatch.setattr(old, "synthesize", synth_old)
            monkeypatch.setattr(old, "unload", unload)
            monkeypatch.setattr(new, "synthesize", synth_new)
            pending = asyncio.create_task(
                client.post("/api/voice/synthesize", json={"text": "old"})
            )
            await asyncio.wait_for(started.wait(), 2)
            switch = asyncio.create_task(
                client.put("/api/voice/config", json={"local_tts_engine": finish})
            )
            await asyncio.wait_for(unloading.wait(), 2)
            next_request = asyncio.create_task(
                client.post("/api/voice/synthesize", json={"text": "new"})
            )
            await client.get("/api/voice/config")
            assert not switch.done() and not next_request.done()
            release.set()
            assert (await asyncio.wait_for(switch, 2)).status_code == 200
            assert (await asyncio.wait_for(pending, 2)).status_code == 409
            assert (await asyncio.wait_for(next_request, 2)).json()["audio_base64"] == AUDIO_B64
            assert calls == ["old-cancelled", "unloading", "reaped", "new-synthesis"]

    asyncio.run(run())


def test_local_unload_blocks_admission_drains_and_remains_reusable(service, monkeypatch):
    async def run():
        local = service.local
        started, cleaning, release = asyncio.Event(), asyncio.Event(), asyncio.Event()
        calls = []

        async def worker(request):
            if request["text"] == "old":
                started.set()
                try:
                    await asyncio.Event().wait()
                finally:
                    cleaning.set()
                    await release.wait()
            return {"audio": AUDIO_B64}

        async def discard():
            calls.append("reaped")

        monkeypatch.setattr(local, "_worker", worker)
        monkeypatch.setattr(local, "_discard_inference_process", discard)
        pending = asyncio.create_task(local.synthesize("old"))
        await started.wait()
        unload = asyncio.create_task(local.unload())
        await asyncio.wait_for(cleaning.wait(), 2)
        with pytest.raises(LocalVoiceError):
            await local.synthesize("during unload")
        unload.cancel()
        await asyncio.sleep(0)
        assert not unload.done()
        release.set()
        with pytest.raises(asyncio.CancelledError):
            await unload
        assert pending.cancelled() and calls == ["reaped"]
        assert await local.synthesize("new") == AUDIO
        assert not local._closed
        await service.close()
        with pytest.raises(LocalVoiceError):
            await local.synthesize("after close")

    asyncio.run(run())


class ControlledPreload:
    """Model-free lifecycle with explicit inference and cleanup barriers."""

    def __init__(self, voice, monkeypatch):
        self.started = asyncio.Event()
        self.complete = asyncio.Event()
        self.cleaning = asyncio.Event()
        self.cleanup_complete = asyncio.Event()
        self.cleanup_complete.set()
        self.loaded = False
        self.loads = 0
        self.unloads = 0
        monkeypatch.setattr(voice.qwen, "preload", self.preload)
        monkeypatch.setattr(voice.qwen, "unload", self.unload)
        monkeypatch.setattr(voice.qwen, "status", self.status)

    def status(self):
        return {
            "state": "ready",
            "loaded": self.loaded,
            "supported": True,
            "gpu_required": True,
            "message": "synthetic",
        }

    async def preload(self):
        self.loads += 1
        self.started.set()
        try:
            await self.complete.wait()
            self.loaded = True
            return self.status()
        except asyncio.CancelledError:
            self.cleaning.set()
            await self.cleanup_complete.wait()
            self.loaded = False
            raise

    async def unload(self):
        self.unloads += 1
        self.loaded = False


async def delayed_qwen_body(payload, started, release):
    started.set()
    await release.wait()
    yield json.dumps(payload).encode()


def qwen_payload(identifier):
    return {} if identifier is None else {"request_id": identifier}


@pytest.mark.parametrize("identifier", [None, "a" * 32])
def test_unload_rejects_load_already_reading_a_delayed_body(tmp_path, monkeypatch, identifier):
    async def run():
        async with api_client(tmp_path) as (client, voice, _runtime):
            select_qwen(voice)
            model = ControlledPreload(voice, monkeypatch)
            model.complete.set()
            started, release = asyncio.Event(), asyncio.Event()
            pending = asyncio.create_task(
                client.post(
                    "/api/voice/qwen/load",
                    content=delayed_qwen_body(qwen_payload(identifier), started, release),
                    headers={"Content-Type": "application/json"},
                )
            )
            await asyncio.wait_for(started.wait(), 2)
            stopped = await client.post("/api/voice/qwen/unload", json=qwen_payload(identifier))
            assert stopped.status_code == 200 and stopped.json()["loaded"] is False
            release.set()
            assert (await asyncio.wait_for(pending, 2)).status_code == 409
            assert model.loads == 0 and not model.loaded

    asyncio.run(run())


def test_named_cancel_before_load_arrives_blocks_that_id_and_unlabelled_downgrade(
    tmp_path, monkeypatch
):
    async def run():
        async with api_client(tmp_path) as (client, voice, _runtime):
            select_qwen(voice)
            model = ControlledPreload(voice, monkeypatch)
            model.complete.set()
            assert (
                await client.post("/api/voice/qwen/unload", json=qwen_payload("a" * 32))
            ).status_code == 200
            for payload in (qwen_payload("a" * 32), {}):
                assert (await client.post("/api/voice/qwen/load", json=payload)).status_code == 409
            assert model.loads == 0
            fresh = await client.post("/api/voice/qwen/load", json=qwen_payload("c" * 32))
            assert fresh.status_code == 200 and fresh.json()["loaded"]
            assert model.loads == 1

    asyncio.run(run())


@pytest.mark.parametrize("value", [None, True, 1, [], {}, "", "a" * 31, "A" * 32, "../runtime"])
def test_qwen_lifecycle_request_ids_are_strict(tmp_path, monkeypatch, value):
    async def run():
        async with api_client(tmp_path) as (client, voice, _runtime):
            select_qwen(voice)
            model = ControlledPreload(voice, monkeypatch)
            for operation in ("load", "unload"):
                response = await client.post(
                    "/api/voice/qwen/" + operation, json={"request_id": value}
                )
                assert response.status_code == 400
            assert model.loads == model.unloads == 0

    asyncio.run(run())


def test_named_duplicate_load_shares_one_task_and_cancel_waits_for_cleanup(tmp_path, monkeypatch):
    async def run():
        async with api_client(tmp_path) as (client, voice, runtime):
            select_qwen(voice)
            model = ControlledPreload(voice, monkeypatch)
            model.cleanup_complete.clear()
            payload = qwen_payload("a" * 32)
            first = asyncio.create_task(client.post("/api/voice/qwen/load", json=payload))
            await asyncio.wait_for(model.started.wait(), 2)
            second = asyncio.create_task(client.post("/api/voice/qwen/load", json=payload))
            await client.get("/api/voice/qwen/status")
            assert model.loads == 1 and list(runtime.voice_tasks) == ["qwen-preload"]
            stop = asyncio.create_task(client.post("/api/voice/qwen/unload", json=payload))
            await asyncio.wait_for(model.cleaning.wait(), 2)
            assert not stop.done() and model.unloads == 0
            model.cleanup_complete.set()
            response = await asyncio.wait_for(stop, 2)
            assert response.status_code == 200 and response.json()["loaded"] is False
            assert (await first).status_code == (await second).status_code == 409
            assert model.unloads == 1 and not runtime.voice_tasks
            assert (await client.post("/api/voice/qwen/load", json=payload)).status_code == 409

    asyncio.run(run())


def test_new_owner_adopts_shared_preload_and_old_cancel_cannot_kill_it(tmp_path, monkeypatch):
    async def run():
        async with api_client(tmp_path) as (client, voice, runtime):
            select_qwen(voice)
            model = ControlledPreload(voice, monkeypatch)
            first = asyncio.create_task(
                client.post("/api/voice/qwen/load", json=qwen_payload("a" * 32))
            )
            await asyncio.wait_for(model.started.wait(), 2)
            current = asyncio.create_task(
                client.post("/api/voice/qwen/load", json=qwen_payload("c" * 32))
            )
            await client.get("/api/voice/qwen/status")
            stopped = await client.post("/api/voice/qwen/unload", json=qwen_payload("a" * 32))
            assert stopped.status_code == 200
            assert not model.cleaning.is_set() and model.unloads == 0
            assert model.loads == 1 and len(runtime.voice_tasks) == 1
            model.complete.set()
            assert (await asyncio.wait_for(first, 2)).status_code == 409
            assert (await asyncio.wait_for(current, 2)).status_code == 200
            assert model.loaded
            assert (
                await client.post("/api/voice/qwen/load", json=qwen_payload("a" * 32))
            ).status_code == 409
            assert (
                await client.post("/api/voice/qwen/unload", json=qwen_payload("a" * 32))
            ).json()["loaded"]
            assert model.unloads == 0
            stopped = await client.post("/api/voice/qwen/unload", json=qwen_payload("c" * 32))
            assert stopped.json()["loaded"] is False and model.unloads == 1

    asyncio.run(run())


def test_delayed_old_unload_cannot_release_a_new_successful_preload(tmp_path, monkeypatch):
    async def run():
        async with api_client(tmp_path) as (client, voice, _runtime):
            select_qwen(voice)
            model = ControlledPreload(voice, monkeypatch)
            model.complete.set()
            assert (
                await client.post("/api/voice/qwen/load", json=qwen_payload("a" * 32))
            ).status_code == 200
            started, release = asyncio.Event(), asyncio.Event()
            old_stop = asyncio.create_task(
                client.post(
                    "/api/voice/qwen/unload",
                    content=delayed_qwen_body(qwen_payload("a" * 32), started, release),
                    headers={"Content-Type": "application/json"},
                )
            )
            await asyncio.wait_for(started.wait(), 2)
            assert (
                await client.post("/api/voice/qwen/load", json=qwen_payload("c" * 32))
            ).status_code == 200
            release.set()
            assert (await asyncio.wait_for(old_stop, 2)).json()["loaded"]
            assert model.loads == 2 and model.unloads == 0 and model.loaded

    asyncio.run(run())


def test_named_retry_is_idempotent_after_success_and_after_idle_unload(tmp_path, monkeypatch):
    async def run():
        async with api_client(tmp_path) as (client, voice, _runtime):
            select_qwen(voice)
            model = ControlledPreload(voice, monkeypatch)
            model.complete.set()
            payload = qwen_payload("a" * 32)
            assert (await client.post("/api/voice/qwen/load", json=payload)).json()["loaded"]
            assert (await client.post("/api/voice/qwen/load", json=payload)).json()["loaded"]
            assert model.loads == 1
            model.loaded = False  # Native idle timeout is owned by QwenVoice.
            retry = await client.post("/api/voice/qwen/load", json=payload)
            assert retry.status_code == 200 and retry.json()["loaded"] is False
            assert model.loads == 1
            assert (await client.post("/api/voice/qwen/load", json=qwen_payload("c" * 32))).json()[
                "loaded"
            ]
            assert model.loads == 2
            assert (await client.post("/api/voice/qwen/unload", json={})).json()["loaded"] is False
            assert (
                await client.post("/api/voice/qwen/load", json=qwen_payload("c" * 32))
            ).status_code == 409

    asyncio.run(run())


@pytest.mark.parametrize("identifier", [None, "a" * 32])
def test_load_admitted_during_unload_cannot_restart_after_gate_opens(
    tmp_path, monkeypatch, identifier
):
    async def run():
        async with api_client(tmp_path) as (client, voice, _runtime):
            select_qwen(voice)
            model = ControlledPreload(voice, monkeypatch)
            model.cleanup_complete.clear()
            first = asyncio.create_task(
                client.post("/api/voice/qwen/load", json=qwen_payload(identifier))
            )
            await asyncio.wait_for(model.started.wait(), 2)
            stopping = asyncio.create_task(
                client.post("/api/voice/qwen/unload", json=qwen_payload(identifier))
            )
            await asyncio.wait_for(model.cleaning.wait(), 2)
            arrived, send = asyncio.Event(), asyncio.Event()
            send.set()
            waiting = asyncio.create_task(
                client.post(
                    "/api/voice/qwen/load",
                    content=delayed_qwen_body(
                        qwen_payload(None if identifier is None else "c" * 32), arrived, send
                    ),
                    headers={"Content-Type": "application/json"},
                )
            )
            await asyncio.wait_for(arrived.wait(), 2)
            model.cleanup_complete.set()
            assert (await asyncio.wait_for(stopping, 2)).json()["loaded"] is False
            assert (await first).status_code == (await waiting).status_code == 409
            assert model.loads == 1 and model.unloads == 1

    asyncio.run(run())


@pytest.mark.parametrize("delayed", [False, True])
def test_config_switch_revokes_old_preload_even_after_qwen_is_selected_again(
    tmp_path, monkeypatch, delayed
):
    async def run():
        async with api_client(tmp_path) as (client, voice, _runtime):
            select_qwen(voice)
            model = ControlledPreload(voice, monkeypatch)
            model.complete.set()
            payload = qwen_payload("a" * 32)
            pending = None
            started, release = asyncio.Event(), asyncio.Event()
            if delayed:
                pending = asyncio.create_task(
                    client.post(
                        "/api/voice/qwen/load",
                        content=delayed_qwen_body(payload, started, release),
                        headers={"Content-Type": "application/json"},
                    )
                )
                await asyncio.wait_for(started.wait(), 2)
            else:
                assert (await client.post("/api/voice/qwen/load", json=payload)).status_code == 200
            for engine in ("kokoro", "qwen"):
                assert (
                    await client.put("/api/voice/config", json={"local_tts_engine": engine})
                ).status_code == 200
            if delayed:
                release.set()
                response = await asyncio.wait_for(pending, 2)
            else:
                response = await client.post("/api/voice/qwen/load", json=payload)
            assert response.status_code == 409
            assert model.loads == (0 if delayed else 1) and not model.loaded

    asyncio.run(run())


def test_shutdown_rejects_a_load_whose_body_finishes_after_close(tmp_path, monkeypatch):
    async def run():
        async with api_client(tmp_path) as (client, voice, runtime):
            select_qwen(voice)
            model = ControlledPreload(voice, monkeypatch)
            started, release = asyncio.Event(), asyncio.Event()
            pending = asyncio.create_task(
                client.post(
                    "/api/voice/qwen/load",
                    content=delayed_qwen_body(qwen_payload("a" * 32), started, release),
                    headers={"Content-Type": "application/json"},
                )
            )
            await asyncio.wait_for(started.wait(), 2)
            await runtime.close()
            release.set()
            assert (await asyncio.wait_for(pending, 2)).status_code == 409
            assert model.loads == 0

    asyncio.run(run())


def test_preload_and_speech_cancellation_ids_are_independent(tmp_path, monkeypatch):
    async def run():
        async with api_client(tmp_path) as (client, voice, _runtime):
            select_qwen(voice)
            model = ControlledPreload(voice, monkeypatch)
            model.complete.set()

            async def synthesize(text):
                return {"audio_base64": AUDIO_B64, "mime_type": "audio/wav"}

            monkeypatch.setattr(voice, "synthesize", synthesize)
            assert (
                await client.post("/api/voice/qwen/unload", json=qwen_payload("a" * 32))
            ).status_code == 200
            assert (
                await client.post(
                    "/api/voice/synthesize", json={"request_id": "a" * 32, "text": "synthetic"}
                )
            ).status_code == 200
            assert (
                await client.post("/api/voice/cancel", json=qwen_payload("b" * 32))
            ).status_code == 200
            assert (
                await client.post("/api/voice/qwen/load", json=qwen_payload("b" * 32))
            ).status_code == 200

    asyncio.run(run())


def test_preload_cancellation_capacity_is_bounded_and_reserves_active_owner(tmp_path, monkeypatch):
    from types import SimpleNamespace

    from ai_neko.app import api

    async def run():
        async with api_client(tmp_path) as (client, voice, _runtime):
            select_qwen(voice)
            model = ControlledPreload(voice, monkeypatch)
            model.complete.set()
            now = [100.0]
            monkeypatch.setattr(api, "time", SimpleNamespace(monotonic=lambda: now[0]))
            active = qwen_payload("a" * 32)
            assert (await client.post("/api/voice/qwen/load", json=active)).status_code == 200
            for number in range(1023):
                response = await client.post(
                    "/api/voice/qwen/unload", json=qwen_payload(f"{number:032x}")
                )
                assert response.status_code == 200
            assert (
                await client.post("/api/voice/qwen/unload", json=qwen_payload("b" * 32))
            ).status_code == 429
            stopped = await client.post("/api/voice/qwen/unload", json=active)
            assert stopped.status_code == 200 and stopped.json()["loaded"] is False
            assert (
                await client.post("/api/voice/qwen/load", json=qwen_payload("c" * 32))
            ).status_code == 429
            now[0] += 181
            assert (
                await client.post("/api/voice/qwen/load", json=qwen_payload("c" * 32))
            ).status_code == 200

    asyncio.run(run())


@pytest.mark.parametrize("new_owner", [False, True])
def test_failed_release_can_retry_same_id_without_touching_new_owner(
    tmp_path, monkeypatch, new_owner
):
    async def run():
        async with api_client(tmp_path) as (client, voice, _runtime):
            select_qwen(voice)
            model = ControlledPreload(voice, monkeypatch)
            model.complete.set()
            attempts = []

            async def unload():
                attempts.append("release")
                if len(attempts) == 1:
                    raise QwenVoiceError("qwen_release_failed", "Synthetic cleanup failed.")
                model.loaded = False

            monkeypatch.setattr(voice.qwen, "unload", unload)
            old = qwen_payload("a" * 32)
            assert (await client.post("/api/voice/qwen/load", json=old)).json()["loaded"]
            failed = await client.post("/api/voice/qwen/unload", json=old)
            assert failed.status_code == 400 and failed.json()["code"] == "qwen_release_failed"
            assert model.loaded and len(attempts) == 1
            assert (await client.post("/api/voice/qwen/load", json=old)).status_code == 409
            if new_owner:
                current = qwen_payload("c" * 32)
                assert (await client.post("/api/voice/qwen/load", json=current)).json()["loaded"]
                retry = await client.post("/api/voice/qwen/unload", json=old)
                assert retry.status_code == 200 and retry.json()["loaded"]
                assert len(attempts) == 1 and model.loaded
                retry = await client.post("/api/voice/qwen/unload", json=current)
            else:
                retry = await client.post("/api/voice/qwen/unload", json=old)
            assert retry.status_code == 200 and retry.json()["loaded"] is False
            assert len(attempts) == 2 and not model.loaded

    asyncio.run(run())


@pytest.mark.parametrize(
    "change,fail", [({"local_tts_speaker_id": 3}, False), ({"local_tts_engine": "kokoro"}, True)]
)
def test_config_change_retains_release_owner_until_actual_qwen_cleanup(
    tmp_path, monkeypatch, change, fail
):
    async def run():
        async with api_client(tmp_path) as (client, voice, _runtime):
            select_qwen(voice)
            model = ControlledPreload(voice, monkeypatch)
            model.complete.set()
            attempts = []

            async def unload():
                attempts.append("release")
                if fail and len(attempts) == 1:
                    raise QwenVoiceError("qwen_release_failed", "Synthetic cleanup failed.")
                model.loaded = False

            monkeypatch.setattr(voice.qwen, "unload", unload)
            old = qwen_payload("a" * 32)
            assert (await client.post("/api/voice/qwen/load", json=old)).json()["loaded"]
            changed = await client.put("/api/voice/config", json=change)
            assert changed.status_code == (400 if fail else 200)
            assert model.loaded
            retry = await client.post("/api/voice/qwen/unload", json=old)
            assert retry.status_code == 200 and retry.json()["loaded"] is False
            assert len(attempts) == (2 if fail else 1)

    asyncio.run(run())


@pytest.mark.parametrize(
    "worker_code",
    [
        "memory_pressure",
        "gpu_unavailable",
        "gpu_memory",
        "resource_exhausted",
        "binding_invalid",
        "output_limit",
        "inference_failed",
        "unknown_private_failure",
    ],
)
def test_preload_failure_http_preserves_safe_reason_and_never_exposes_worker_details(
    tmp_path, monkeypatch, worker_code
):
    from test_qwen_voice import prepared

    from ai_neko.media import qwen_voice

    async def run():
        async with api_client(tmp_path) as (client, voice, runtime):
            select_qwen(voice)
            worker_root = tmp_path / "synthetic-error-worker"
            worker_root.mkdir()
            voice.qwen = prepared(worker_root, monkeypatch)
            script = "\n".join(
                [
                    "import json,os,sys",
                    "for line in sys.stdin:",
                    "    request=json.loads(line)",
                    "    print(json.dumps({'ok':False,'pid':os.getpid(),",
                    "        'request_id':request['request_id'],",
                    f"        'code':{worker_code!r},",
                    "        'message':'RAW_PRIVATE_MESSAGE','traceback':'RAW_PRIVATE_TRACE',",
                    "        'path':'C:/RAW_PRIVATE_PATH','api_key':'RAW_PRIVATE_KEY'}),flush=True)",
                ]
            )
            (qwen_voice.RUNTIME_SOURCE / "worker.py").write_text(script, encoding="utf-8")
            children = []
            original_start = voice.qwen._start_process

            async def start(binding):
                process = await original_start(binding)
                children.append(process)
                return process

            monkeypatch.setattr(voice.qwen, "_start_process", start)
            before = (await client.get("/api/voice/config")).json()
            assert before["qwen"]["preflight"] == {
                "available_ram_mib": 1536,
                "available_commit_mib": 8192,
                "available_vram_mib": 3072,
            }
            payload = {"request_id": "f" * 32}
            response = await client.post("/api/voice/qwen/load", json=payload)
            safe_code = (
                worker_code if worker_code in qwen_voice.ERROR_MESSAGES else "inference_failed"
            )
            expected = {
                "detail": qwen_voice.ERROR_MESSAGES[safe_code],
                "code": "qwen_" + safe_code,
            }
            # The first failed POST contains the cause without a separate status
            # refresh. Only fixed host text crosses the authenticated UI API.
            assert response.status_code == 400 and response.json() == expected
            assert "RAW_PRIVATE" not in response.text
            assert "unknown_private_failure" not in response.text
            assert str(tmp_path) not in response.text
            assert len(children) == 1 and children[0].returncode is not None
            assert not voice.qwen._processes and not voice.qwen.status()["loaded"]
            assert not runtime.voice_tasks
            retry = await client.post("/api/voice/qwen/load", json=payload)
            assert retry.status_code == 400 and retry.json() == expected
            assert len(children) == 1, "a repeated request ID must not restart a failed worker"
            assert voice.qwen.status()["message"] == expected["detail"]

    asyncio.run(run())


def synthetic_local_and_qwen(voice, tmp_path, monkeypatch):
    from test_qwen_voice import prepared as prepare_qwen
    from test_voice_persistent import prepared as prepare_local

    local_root, qwen_root = tmp_path / "local-child", tmp_path / "qwen-child"
    local_root.mkdir()
    qwen_root.mkdir()
    voice.local = prepare_local(local_root, monkeypatch)
    voice.qwen = prepare_qwen(qwen_root, monkeypatch)


async def start_idle_local_and_qwen(client, voice):
    select_qwen(voice)
    response = await client.post(
        "/api/voice/transcribe", json={"audio_base64": AUDIO_B64, "mime_type": "audio/wav"}
    )
    assert response.status_code == 200 and response.json() == {"text": "synthetic response"}
    response = await client.post("/api/voice/qwen/load", json=qwen_payload("a" * 32))
    assert response.status_code == 200 and response.json()["loaded"]
    children = {"local": voice.local._inference_process, "qwen": voice.qwen._inference_process}
    assert all(child is not None and child.returncode is None for child in children.values())
    assert not voice.local._requests and not voice.qwen._requests
    return children


def test_switch_to_cloud_reaps_idle_asr_and_qwen_children_and_allows_local_reuse(
    tmp_path, monkeypatch
):
    async def run():
        async with api_client(tmp_path) as (client, voice, runtime):
            synthetic_local_and_qwen(voice, tmp_path, monkeypatch)
            children = await start_idle_local_and_qwen(client, voice)
            assert not runtime.voice_tasks
            changed = await client.put("/api/voice/config", json={"voice_provider": "openai"})
            assert changed.status_code == 200
            assert changed.json()["voice_provider"] == "openai"
            assert changed.json()["qwen"]["loaded"] is False
            for name, child in children.items():
                assert child.returncode is not None and child.stdin.is_closing()
                engine = getattr(voice, name)
                assert engine._inference_process is None and not engine._processes
                assert not engine._closed
            assert (
                await client.put("/api/voice/config", json={"voice_provider": "local"})
            ).status_code == 200
            assert (
                await client.post("/api/voice/qwen/load", json=qwen_payload("a" * 32))
            ).status_code == 409
            assert (await client.post("/api/voice/qwen/load", json=qwen_payload("c" * 32))).json()[
                "loaded"
            ]
            asr = await client.post(
                "/api/voice/transcribe", json={"audio_base64": AUDIO_B64, "mime_type": "audio/wav"}
            )
            assert asr.status_code == 200
            assert voice.local._inference_process.pid != children["local"].pid
            assert voice.qwen._inference_process.pid != children["qwen"].pid

    asyncio.run(run())


@pytest.mark.parametrize("failed_engine", ["local", "qwen"])
def test_failed_cloud_switch_attempts_both_releases_and_retries_without_cancelling_cloud_work(
    tmp_path, monkeypatch, failed_engine
):
    async def run():
        async with api_client(tmp_path) as (client, voice, runtime):
            synthetic_local_and_qwen(voice, tmp_path, monkeypatch)
            children = await start_idle_local_and_qwen(client, voice)
            attempts = {"local": 0, "qwen": 0}

            def controlled_unload(name, original):
                async def unload():
                    attempts[name] += 1
                    if name == failed_engine and attempts[name] == 1:
                        raise LocalVoiceError(
                            "synthetic_release_failed", "Synthetic cleanup failed."
                        )
                    await original()

                return unload

            for name in attempts:
                engine = getattr(voice, name)
                monkeypatch.setattr(engine, "unload", controlled_unload(name, engine.unload))
            changed = await client.put("/api/voice/config", json={"voice_provider": "openai"})
            assert changed.status_code == 400
            assert changed.json()["code"] == "synthetic_release_failed"
            assert voice.public_config()["voice_provider"] == "openai"
            assert attempts == {"local": 1, "qwen": 1}
            other = "qwen" if failed_engine == "local" else "local"
            assert children[failed_engine].returncode is None
            assert children[other].returncode is not None and children[other].stdin.is_closing()
            started, complete = asyncio.Event(), asyncio.Event()

            async def cloud_synthesis(text):
                assert voice.public_config()["voice_provider"] == "openai"
                started.set()
                await complete.wait()
                return {"audio_base64": AUDIO_B64, "mime_type": "audio/wav"}

            monkeypatch.setattr(voice, "synthesize", cloud_synthesis)
            cloud = asyncio.create_task(
                client.post(
                    "/api/voice/synthesize",
                    json={"text": "synthetic cloud", "request_id": "d" * 32},
                )
            )
            try:
                await asyncio.wait_for(started.wait(), 2)
                retry = await asyncio.wait_for(
                    client.put("/api/voice/config", json={"voice_provider": "openai"}), 5
                )
                assert retry.status_code == 200 and retry.json()["qwen"]["loaded"] is False
                assert attempts == {"local": 2, "qwen": 2}
                assert all(child.returncode is not None for child in children.values())
                assert not runtime.voice_tasks["d" * 32].done() and not cloud.done()
                # Once cleanup succeeds, an ordinary cloud settings save must
                # neither retry unload nor disturb an existing cloud request.
                saved = await client.put("/api/voice/config", json={"tts_model": "synthetic"})
                assert saved.status_code == 200 and attempts == {"local": 2, "qwen": 2}
                assert not runtime.voice_tasks["d" * 32].done()
            finally:
                complete.set()
            assert (await asyncio.wait_for(cloud, 2)).status_code == 200

    asyncio.run(run())


@pytest.mark.parametrize("failed_engine", ["local", "qwen"])
def test_returning_to_local_supersedes_failed_cloud_cleanup_without_harming_new_qwen_owner(
    tmp_path, monkeypatch, failed_engine
):
    async def run():
        async with api_client(tmp_path) as (client, voice, _runtime):
            synthetic_local_and_qwen(voice, tmp_path, monkeypatch)
            await start_idle_local_and_qwen(client, voice)
            attempts = []
            engine = getattr(voice, failed_engine)
            original = engine.unload

            async def fail_once():
                attempts.append("release")
                if len(attempts) == 1:
                    raise LocalVoiceError("synthetic_release_failed", "Synthetic cleanup failed.")
                await original()

            monkeypatch.setattr(engine, "unload", fail_once)
            assert (
                await client.put("/api/voice/config", json={"voice_provider": "openai"})
            ).status_code == 400
            assert (
                await client.put("/api/voice/config", json={"voice_provider": "local"})
            ).status_code == 200
            assert (await client.post("/api/voice/qwen/load", json=qwen_payload("c" * 32))).json()[
                "loaded"
            ]
            current = voice.qwen._inference_process
            stale_release = await client.post("/api/voice/qwen/unload", json=qwen_payload("a" * 32))
            assert stale_release.status_code == 200 and stale_release.json()["loaded"]
            saved = await client.put("/api/voice/config", json={"voice_provider": "local"})
            assert saved.status_code == 200 and saved.json()["qwen"]["loaded"]
            assert voice.qwen._inference_process is current and current.returncode is None
            assert attempts == ["release"]
            assert (
                await client.post("/api/voice/qwen/unload", json=qwen_payload("c" * 32))
            ).json()["loaded"] is False
            assert current.returncode is not None

    asyncio.run(run())
