"""Local/cloud routing and API ownership; no speech models or network downloads."""

import asyncio
import base64
import json

import httpx
import pytest
from test_runtime import Store
from test_voice import AUDIO, AUDIO_B64, MP3_CONTAINER, FakeVault

from ai_neko.app.server import Connection, create_app
from ai_neko.config import credentials
from ai_neko.config.paths import initialize_data_root
from ai_neko.media.voice import DEFAULTS, VoiceError, VoiceService
from ai_neko.runtime import SessionRuntime


@pytest.fixture(autouse=True)
def isolated_voice_credentials(monkeypatch):
    for name in ("AI_NEKO_ASR_API_KEY", "AI_NEKO_TTS_API_KEY"):
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setattr(credentials, "WindowsVault", FakeVault)


@pytest.fixture
def local_service(tmp_path):
    return VoiceService(initialize_data_root(tmp_path / "independent-local-voice"))


def test_old_five_field_config_remains_cloud_and_mode_does_not_erase_keys(local_service):
    service = local_service
    service.update({"asr_api_key": "synthetic-asr-key", "tts_api_key": "synthetic-tts-key"})
    original = {
        k: v
        for k, v in DEFAULTS.items()
        if k not in {"voice_provider", "local_tts_speaker_id", "local_tts_engine"}
    }
    (service.paths.config / "voice.json").write_text(
        json.dumps({"app_id": "ai-neko", "schema_version": 1, "voice": original})
    )
    restored = VoiceService(service.paths)
    assert restored.public_config()["voice_provider"] == "openai"
    restored.update({"voice_provider": "local"})
    assert restored._snapshot("asr")[1] is None
    assert restored._snapshot("tts")[1] is None
    assert restored._credentials.get("asr") == "synthetic-asr-key"
    assert restored._credentials.get("tts") == "synthetic-tts-key"
    assert VoiceService(service.paths).public_config()["voice_provider"] == "local"
    restored.update({"voice_provider": "openai"})
    assert restored.public_config()["asr_key_set"] and restored.public_config()["tts_key_set"]


@pytest.mark.parametrize("provider", [None, "free-cloud", True, [], {"local": True}])
def test_provider_is_strict_and_failure_preserves_config(local_service, provider):
    before = local_service.public_config()
    with pytest.raises(ValueError):
        local_service.update({"voice_provider": provider})
    assert local_service.public_config() == before


@pytest.mark.parametrize("speaker_id", [0, 3, 46, 57, 102])
def test_local_speaker_persists_and_reaches_only_local_synthesis(
    local_service, monkeypatch, speaker_id
):
    local_service.update({"voice_provider": "local", "local_tts_speaker_id": speaker_id})
    restored = VoiceService(local_service.paths)
    assert restored.public_config()["local_tts_speaker_id"] == speaker_id
    calls = []

    async def local(text, *, speaker_id):
        calls.append(speaker_id)
        return AUDIO

    async def forbidden(*args, **kwargs):
        raise AssertionError("Local speaker selection cannot use cloud speech")

    monkeypatch.setattr(restored.local, "synthesize", local)
    monkeypatch.setattr(restored, "_request", forbidden)
    assert asyncio.run(restored.synthesize("Synthetic text."))["mime_type"] == "audio/wav"
    assert calls == [speaker_id]


@pytest.mark.parametrize("speaker_id", [-1, 103, True, False, 46.0, "46", None, [], {}])
def test_local_speaker_rejects_non_integer_or_out_of_range_without_mutation(
    local_service, speaker_id
):
    before = local_service.public_config()
    with pytest.raises(ValueError, match="invalid_voice_config"):
        local_service.update({"local_tts_speaker_id": speaker_id})
    assert local_service.public_config() == before
    assert not (local_service.paths.config / "voice.json").exists()


@pytest.mark.parametrize("provider", ["local", "openai"])
def test_existing_six_field_config_gains_default_speaker_without_switching_provider(
    local_service, provider
):
    old = {
        key: value
        for key, value in DEFAULTS.items()
        if key not in {"local_tts_speaker_id", "local_tts_engine"}
    }
    old["voice_provider"] = provider
    (local_service.paths.config / "voice.json").write_text(
        json.dumps({"app_id": "ai-neko", "schema_version": 1, "voice": old})
    )
    config = VoiceService(local_service.paths).public_config()
    assert config["voice_provider"] == provider
    assert config["local_tts_speaker_id"] == 46


def test_local_routes_never_send_cloud_key_or_request(local_service, monkeypatch):
    service = local_service
    service.update({"voice_provider": "local", "asr_api_key": "old-key", "tts_api_key": "old-key"})
    calls = []

    async def cloud(*args, **kwargs):
        raise AssertionError("local mode must not make a cloud request")

    async def transcribe(wav):
        assert wav == AUDIO
        calls.append("asr")
        return "  你好，本地语音。  "

    async def synthesize(text, *, speaker_id):
        assert text == "你好。"
        assert speaker_id == 46
        calls.append("tts")
        return AUDIO

    monkeypatch.setattr(service, "_request", cloud)
    monkeypatch.setattr(service.local, "transcribe", transcribe)
    monkeypatch.setattr(service.local, "synthesize", synthesize)

    async def run():
        assert await service.transcribe(AUDIO_B64, "audio/wav") == {"text": "你好，本地语音。"}
        result = await service.synthesize("你好。")
        assert result == {"audio_base64": AUDIO_B64, "mime_type": "audio/wav"}
        with pytest.raises(VoiceError, match="PCM WAV"):
            await service.transcribe(base64.b64encode(MP3_CONTAINER).decode(), "audio/mpeg")
        assert calls == ["asr", "tts"]
        await service.close()

    asyncio.run(run())


def test_local_failure_is_reported_without_cloud_fallback(local_service, monkeypatch):
    from ai_neko.media.local_voice import LocalVoiceError

    local_service.update({"voice_provider": "local"})

    async def missing(*args, **kwargs):
        raise LocalVoiceError("local_models_missing", "请先下载免费语音资源。")

    monkeypatch.setattr(local_service.local, "synthesize", missing)
    with pytest.raises(VoiceError) as error:
        asyncio.run(local_service.synthesize("你好。"))
    assert error.value.code == "local_models_missing"


def test_install_api_requires_auth_confirmation_and_cancels_only_owned_install(tmp_path):
    async def run():
        runtime = SessionRuntime(initialize_data_root(tmp_path / "local-api"), Store())
        connection = Connection(43210, "a" * 43, "f" * 32)
        app = create_app(connection, lambda: None, runtime=runtime, providers=runtime.providers)
        local = app.state.voice.local
        calls = []
        local.start_install = lambda: calls.append("install") or {"state": "installing"}

        async def cancel():
            calls.append("cancel")
            return {"state": "missing"}

        local.cancel_install = cancel
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url=connection.url
        ) as client:
            headers = {"Authorization": "Bearer " + connection.token}
            for method, path, payload in (
                ("GET", "/api/voice/local", None),
                ("POST", "/api/voice/local/install", {"confirm": True}),
                ("POST", "/api/voice/local/cancel", {}),
            ):
                assert (await client.request(method, path, json=payload)).status_code == 401
            assert not calls
            for payload in ({}, {"confirm": 1}, {"confirm": True, "url": "https://other.invalid"}):
                assert (
                    await client.post("/api/voice/local/install", headers=headers, json=payload)
                ).status_code == 400
            assert not calls
            response = await client.post(
                "/api/voice/local/install", headers=headers, json={"confirm": True}
            )
            assert response.status_code == 200 and response.json()["state"] == "installing"
            assert (
                await client.post(
                    "/api/voice/local/cancel", headers=headers, json={"path": "other"}
                )
            ).status_code == 400
            assert (
                await client.post("/api/voice/local/cancel", headers=headers, json={})
            ).status_code == 200
            assert calls == ["install", "cancel"]
            await runtime.close()
            assert (
                await client.post(
                    "/api/voice/local/install", headers=headers, json={"confirm": True}
                )
            ).status_code == 409
        await app.state.voice.close()

    asyncio.run(run())


@pytest.mark.parametrize("change", [{"voice_provider": "local"}, {"local_tts_speaker_id": 3}])
def test_provider_or_local_speaker_change_waits_for_existing_voice_cancellation(tmp_path, change):
    async def run():
        runtime = SessionRuntime(initialize_data_root(tmp_path / "local-switch"), Store())
        connection = Connection(43211, "b" * 43, "e" * 32)
        app = create_app(connection, lambda: None, runtime=runtime, providers=runtime.providers)
        if "local_tts_speaker_id" in change:
            app.state.voice.update({"voice_provider": "local"})
        started, cancelled = asyncio.Event(), asyncio.Event()

        async def pending():
            started.set()
            try:
                await asyncio.Event().wait()
            finally:
                cancelled.set()

        task = asyncio.create_task(pending())
        runtime.voice_tasks["a" * 32] = task
        await started.wait()
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url=connection.url
        ) as client:
            response = await client.put(
                "/api/voice/config",
                headers={"Authorization": "Bearer " + connection.token},
                json=change,
            )
            assert response.status_code == 200
            assert cancelled.is_set() and task.done()
            assert response.json()["voice_provider"] == "local"
        await runtime.close()
        await app.state.voice.close()

    asyncio.run(run())


def test_cancel_api_waits_until_worker_cleanup_finishes(tmp_path):
    async def run():
        runtime = SessionRuntime(initialize_data_root(tmp_path / "cancel-worker"), Store())
        connection = Connection(43212, "c" * 43, "d" * 32)
        app = create_app(connection, lambda: None, runtime=runtime, providers=runtime.providers)
        started, cleaning, release = asyncio.Event(), asyncio.Event(), asyncio.Event()

        async def inference():
            started.set()
            try:
                await asyncio.Event().wait()
            finally:
                cleaning.set()
                await release.wait()

        worker = asyncio.create_task(inference())
        runtime.voice_tasks["a" * 32] = worker
        await started.wait()
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url=connection.url
        ) as client:
            request = asyncio.create_task(
                client.post(
                    "/api/voice/cancel",
                    headers={"Authorization": "Bearer " + connection.token},
                    json={"request_id": "a" * 32},
                )
            )
            await asyncio.wait_for(cleaning.wait(), 1)
            assert not request.done(), "a stopped response must wait for actual worker cleanup"
            release.set()
            response = await asyncio.wait_for(request, 1)
            assert response.status_code == 200 and response.json() == {"cancelled": True}
            assert worker.done()
        await runtime.close()
        await app.state.voice.close()

    asyncio.run(run())
