import asyncio
import base64
import io
import json
import wave
from email.parser import BytesParser
from email.policy import default

import httpx
import pytest

from ai_neko.config import credentials
from ai_neko.config.paths import initialize_data_root
from ai_neko.media import VoiceError, VoiceService
from ai_neko.media import voice as module
from ai_neko.tools import network


class FakeVault:
    values = {}

    def get(self, target):
        return self.values.get(target)

    def set(self, target, value):
        if value is None:
            self.values.pop(target, None)
        else:
            self.values[target] = value


def wav_bytes():
    output = io.BytesIO()
    with wave.open(output, "wb") as audio:
        audio.setnchannels(1)
        audio.setsampwidth(2)
        audio.setframerate(16_000)
        audio.writeframes(b"\x00\x00" * 1600)
    return output.getvalue()


AUDIO = wav_bytes()
AUDIO_B64 = base64.b64encode(AUDIO).decode("ascii")
MP3_CONTAINER = b"ID3\x04\x00\x00\x00\x00\x00\x00\xff\xfb\x90\x00" + b"\x00" * 64


class BytesStream(httpx.AsyncByteStream):
    def __init__(self, chunks):
        self.chunks = chunks
        self.closed = False

    async def __aiter__(self):
        for chunk in self.chunks:
            if isinstance(chunk, Exception):
                raise chunk
            yield chunk

    async def aclose(self):
        self.closed = True


@pytest.fixture
def service(tmp_path, monkeypatch):
    for key in ("AI_NEKO_ASR_API_KEY", "AI_NEKO_TTS_API_KEY", "OPENAI_API_KEY"):
        monkeypatch.delenv(key, raising=False)
    monkeypatch.setattr(credentials, "WindowsVault", FakeVault)
    return VoiceService(initialize_data_root(tmp_path / "voice-app"))


@pytest.fixture
def voice_http(monkeypatch):
    calls, pins, clients, streams = [], [], [], []

    def use(
        chunks=(b'{"text":"synthetic transcription"}',),
        status=200,
        content_type="application/json",
        headers=None,
        stream=None,
    ):
        async def pin(url, **kwargs):
            pins.append((url, kwargs))
            return (
                httpx.URL(url),
                {"Host": "synthetic.example"},
                {"sni_hostname": "synthetic.example"},
            )

        async def handle(request):
            await request.aread()
            calls.append(request)
            response_stream = stream or BytesStream(chunks)
            streams.append(response_stream)
            return httpx.Response(
                status,
                stream=response_stream,
                headers={"Content-Type": content_type, **(headers or {})},
            )

        def client():
            value = httpx.AsyncClient(transport=httpx.MockTransport(handle), trust_env=False)
            clients.append(value)
            return value

        monkeypatch.setattr(network, "pin_url", pin)
        monkeypatch.setattr(network, "client", client)
        return calls, pins, clients, streams

    return use


def configure(service):
    service.update(
        {
            "asr_model": "synthetic-asr",
            "tts_model": "synthetic-tts",
            "tts_voice": "synthetic-voice",
            "asr_api_key": "synthetic-asr-key",
            "tts_api_key": "synthetic-tts-key",
        }
    )


def transcribe(service):
    return asyncio.run(service.transcribe(AUDIO_B64, "audio/wav"))


def synthesize(service):
    return asyncio.run(service.synthesize("请先保留金币，看看下一轮。"))


def test_config_persists_redacted_and_keys_are_isolated(service, tmp_path, monkeypatch):
    monkeypatch.setenv("OPENAI_API_KEY", "foreign-key")
    assert not service.public_config()["asr_key_set"]
    configure(service)
    public = service.public_config()
    assert public["asr_key_set"] and public["tts_key_set"]
    assert "-key" not in json.dumps(public)
    saved = (service.paths.config / "voice.json").read_text()
    assert "api_key" not in saved and "-key" not in saved
    assert not list(service.paths.config.glob("*.tmp"))
    assert VoiceService(service.paths).public_config() == public
    other = VoiceService(initialize_data_root(tmp_path / "separate-voice-app"))
    assert not other.public_config()["asr_key_set"]
    assert service.update({"asr_api_key": ""})["asr_key_set"]
    assert not service.update({"clear_asr_api_key": True})["asr_key_set"]
    assert service.public_config()["tts_key_set"]


@pytest.mark.parametrize(
    "change",
    [
        {"asr_base_url": "http://cloud.example/v1"},
        {"tts_base_url": "https://10.0.0.1"},
        {"asr_base_url": "https://user:secret@example.com"},
        {"asr_base_url": "https://example.com?api_key=secret"},
        {"tts_base_url": "https://example.com#secret"},
        {"asr_model": "model\n"},
        {"tts_voice": "broken\ud800"},
        {"tts_voice": ["alloy"]},
        {"tts_api_key": "secret\r\n"},
        {"asr_api_key": "secret", "clear_asr_api_key": True},
        {"clear_tts_api_key": 1},
        {"unexpected": True},
    ],
)
def test_config_invalid_does_not_mutate(service, change):
    before = service.public_config()
    with pytest.raises(ValueError):
        service.update(change)
    assert service.public_config() == before
    assert not (service.paths.config / "voice.json").exists()


def test_config_failure_rolls_back_both_credentials(service, monkeypatch):
    configure(service)
    before = service.public_config()

    def fail(config):
        raise ValueError("voice_config_write_failed")

    monkeypatch.setattr(service, "_save", fail)
    with pytest.raises(ValueError, match="voice_config_write_failed"):
        service.update({"tts_model": "new", "asr_api_key": "new-asr", "tts_api_key": "new-tts"})
    assert service.public_config() == before
    assert service._credentials.get("asr") == "synthetic-asr-key"
    assert service._credentials.get("tts") == "synthetic-tts-key"


@pytest.mark.parametrize(
    "saved",
    [
        {"app_id": "foreign", "schema_version": 1, "voice": module.DEFAULTS},
        {"app_id": "ai-neko", "schema_version": True, "voice": module.DEFAULTS},
        {
            "app_id": "ai-neko",
            "schema_version": 1,
            "voice": {**module.DEFAULTS, "api_key": "secret"},
        },
    ],
)
def test_reject_foreign_or_secret_config(service, saved):
    (service.paths.config / "voice.json").write_text(json.dumps(saved))
    with pytest.raises(ValueError, match="invalid_voice_config"):
        VoiceService(service.paths)


def test_asr_multipart_contains_recording_and_independent_auth(service, voice_http):
    configure(service)
    calls, pins, clients, streams = voice_http(
        [json.dumps({"text": " 下一步应该怎么走？ "}, ensure_ascii=False).encode()]
    )
    assert transcribe(service) == {"text": "下一步应该怎么走？"}
    request = calls[0]
    assert str(request.url).endswith("/audio/transcriptions")
    assert request.headers["Authorization"] == "Bearer synthetic-asr-key"
    multipart = BytesParser(policy=default).parsebytes(
        b"Content-Type: " + request.headers["Content-Type"].encode() + b"\r\n\r\n" + request.content
    )
    parts = {
        part.get_param("name", header="content-disposition"): part
        for part in multipart.iter_parts()
    }
    assert parts["file"].get_filename() == "recording.wav"
    assert parts["file"].get_content_type() == "audio/wav"
    assert parts["file"].get_payload(decode=True) == AUDIO
    assert parts["model"].get_payload(decode=True) == b"synthetic-asr"
    assert parts["response_format"].get_payload(decode=True) == b"json"
    assert pins[0][1] == {"allow_local": True}
    assert clients[0].is_closed and streams[0].closed
    assert not list(service.paths.runtime.iterdir())


def test_browser_opus_codec_is_accepted_and_not_forwarded(service, voice_http):
    configure(service)
    calls, *_ = voice_http()
    data = base64.b64encode(b"\x1a\x45\xdf\xa3" + b"\x00" * 30).decode()
    asyncio.run(service.transcribe(data, "audio/webm;codecs=opus"))
    assert b'filename="recording.webm"' in calls[0].content
    assert b"Content-Type: audio/webm\r\n" in calls[0].content


@pytest.mark.parametrize("content_type", ["audio/mpeg", "audio/mp3", "application/octet-stream"])
def test_tts_binary_returns_mp3_type_without_disk_write(service, voice_http, content_type):
    configure(service)
    calls, _, clients, streams = voice_http(
        [MP3_CONTAINER[:13], MP3_CONTAINER[13:]], content_type=content_type
    )
    result = synthesize(service)
    assert base64.b64decode(result["audio_base64"]) == MP3_CONTAINER
    assert result["mime_type"] == "audio/mpeg"
    payload = json.loads(calls[0].content)
    assert payload == {
        "model": "synthetic-tts",
        "voice": "synthetic-voice",
        "input": "请先保留金币，看看下一轮。",
        "response_format": "mp3",
    }
    assert calls[0].headers["Authorization"] == "Bearer synthetic-tts-key"
    assert clients[0].is_closed and streams[0].closed
    assert not list(service.paths.runtime.iterdir())


@pytest.mark.parametrize(
    "call,code", [(transcribe, "asr_not_configured"), (synthesize, "tts_not_configured")]
)
def test_missing_configuration_fails_before_network(service, voice_http, call, code):
    calls, pins, *_ = voice_http()
    with pytest.raises(VoiceError) as captured:
        call(service)
    assert captured.value.code == code
    assert not calls and not pins


def test_cloud_requires_own_key_and_explicit_loopback_allows_keyless(service, voice_http):
    calls, pins, *_ = voice_http()
    service.update({"asr_model": "synthetic-asr"})
    with pytest.raises(VoiceError) as captured:
        transcribe(service)
    assert captured.value.code == "asr_key_missing"
    assert not pins
    service.update({"asr_base_url": "http://127.0.0.1:9912/v1"})
    assert transcribe(service)["text"]
    assert "Authorization" not in calls[0].headers


@pytest.mark.parametrize(
    "data,mime,code",
    [
        ("bad%%base64", "audio/wav", "invalid_audio"),
        (AUDIO_B64, "text/html", "invalid_audio_type"),
        (AUDIO_B64, "audio/wav;evil=header", "invalid_audio_type"),
        (AUDIO_B64, "audio/wav\r\nX-secret: value", "invalid_audio_type"),
        (AUDIO_B64, "audio/webm", "invalid_audio"),
        ("", "audio/wav", "invalid_audio"),
        (None, "audio/wav", "invalid_audio"),
    ],
)
def test_invalid_input_rejected_before_network(service, voice_http, data, mime, code):
    calls, pins, *_ = voice_http()
    with pytest.raises(VoiceError) as captured:
        asyncio.run(service.transcribe(data, mime))
    assert captured.value.code == code
    assert not calls and not pins


def test_request_limits_are_applied_before_network(service, voice_http, monkeypatch):
    calls, pins, *_ = voice_http()
    monkeypatch.setattr(module, "MAX_AUDIO_BYTES", 20)
    monkeypatch.setattr(module, "MAX_AUDIO_BASE64", 28)
    with pytest.raises(VoiceError) as captured:
        transcribe(service)
    assert captured.value.code == "audio_too_large"
    with pytest.raises(VoiceError) as captured:
        asyncio.run(service.synthesize("x" * 4097))
    assert captured.value.code == "speech_text_too_long"
    assert not calls and not pins


@pytest.mark.parametrize(
    "status,suffix",
    [
        (401, "authentication_failed"),
        (403, "authentication_failed"),
        (429, "rate_limited"),
        (500, "http_error"),
        (302, "http_error"),
    ],
)
@pytest.mark.parametrize("call,prefix", [(transcribe, "asr"), (synthesize, "tts")])
def test_http_failure_never_exposes_body_or_key(service, voice_http, status, suffix, call, prefix):
    configure(service)
    calls, _, clients, streams = voice_http(
        [b"synthetic-asr-key private-upstream-secret"], status=status
    )
    with pytest.raises(VoiceError) as captured:
        call(service)
    assert captured.value.code == prefix + "_" + suffix
    assert "synthetic" not in str(captured.value) and "secret" not in str(captured.value)
    assert len(calls) == 1 and clients[0].is_closed and streams[0].closed


@pytest.mark.parametrize(
    "body,code",
    [
        (b"bad-json secret", "asr_invalid_response"),
        (b'{"text":123}', "asr_invalid_response"),
        (b'{"text":"   "}', "asr_empty_text"),
        (b'{"text":"\\u0000"}', "asr_invalid_response"),
        (b'{"text":"\\ud800"}', "asr_invalid_response"),
        (b'{"text":"text","error":{"message":"secret"}}', "asr_invalid_response"),
        (json.dumps({"text": "x" * 8001}).encode(), "asr_output_limit"),
    ],
)
def test_asr_response_validation(service, voice_http, body, code):
    configure(service)
    voice_http([body])
    with pytest.raises(VoiceError) as captured:
        transcribe(service)
    assert captured.value.code == code


@pytest.mark.parametrize("text", [None, "", " \n\t ", "bad\ud800", "bad\x00", "bad\x7f"])
def test_invalid_speech_text_never_reaches_network(service, voice_http, text):
    calls, pins, *_ = voice_http()
    with pytest.raises(VoiceError) as captured:
        asyncio.run(service.synthesize(text))
    assert captured.value.code == "invalid_speech_text"
    assert not calls and not pins


def test_tts_decoded_audio_size_limit(service, voice_http, monkeypatch):
    configure(service)
    monkeypatch.setattr(module, "MAX_AUDIO_BYTES", 20)
    voice_http([MP3_CONTAINER], content_type="audio/mpeg")
    with pytest.raises(VoiceError) as captured:
        synthesize(service)
    assert captured.value.code == "tts_output_limit"


@pytest.mark.parametrize(
    "mime,body",
    [
        ("text/html", MP3_CONTAINER),
        ("audio/mpeg", b"<html>error-body</html>"),
        ("audio/wav", AUDIO),
        ("application/octet-stream", b""),
    ],
)
def test_tts_rejects_wrong_format_or_non_audio(service, voice_http, mime, body):
    configure(service)
    voice_http([body], content_type=mime)
    with pytest.raises(VoiceError) as captured:
        synthesize(service)
    assert captured.value.code == "tts_invalid_response"


@pytest.mark.parametrize(
    "headers,chunks,code",
    [
        ({"Content-Length": str(module.MAX_TRANSCRIPT_RESPONSE + 1)}, (), "asr_output_limit"),
        ({}, [b"x" * (module.MAX_TRANSCRIPT_RESPONSE + 1)], "asr_output_limit"),
        ({"Content-Encoding": "gzip"}, [b"compressed"], "asr_invalid_response"),
        ({}, [httpx.ReadError("secret")], "asr_network_error"),
        ({}, [httpx.ReadTimeout("secret")], "asr_timeout"),
    ],
)
def test_response_limits_and_transport_failures(service, voice_http, headers, chunks, code):
    configure(service)
    _, _, clients, streams = voice_http(chunks, headers=headers)
    with pytest.raises(VoiceError) as captured:
        transcribe(service)
    assert captured.value.code == code and "secret" not in str(captured.value)
    assert clients[0].is_closed and streams[0].closed


def test_dns_policy_blocks_before_any_request(service, monkeypatch):
    configure(service)

    async def reject(*args, **kwargs):
        raise network.NetworkPolicyError("blocked_address")

    monkeypatch.setattr(network, "pin_url", reject)
    with pytest.raises(VoiceError) as captured:
        transcribe(service)
    assert captured.value.code == "asr_blocked_endpoint"


def test_cancel_during_response_closes_stream_and_client(service, voice_http):
    configure(service)

    async def run():
        started = asyncio.Event()

        class WaitingStream(BytesStream):
            async def __aiter__(self):
                started.set()
                await asyncio.Event().wait()
                yield b"never"

        stream = WaitingStream([])
        _, _, clients, _ = voice_http(stream=stream)
        task = asyncio.create_task(service.transcribe(AUDIO_B64, "audio/wav"))
        await asyncio.wait_for(started.wait(), 1)
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task
        assert stream.closed and clients[0].is_closed

    asyncio.run(run())


def test_absolute_timeout_includes_dns_and_cancellation_propagates(service, monkeypatch):
    configure(service)

    async def slow(*args, **kwargs):
        await asyncio.sleep(1)

    monkeypatch.setattr(module, "REQUEST_TIMEOUT", 0.01)
    monkeypatch.setattr(network, "pin_url", slow)
    with pytest.raises(VoiceError) as captured:
        transcribe(service)
    assert captured.value.code == "asr_timeout"

    async def cancel(*args, **kwargs):
        raise asyncio.CancelledError

    monkeypatch.setattr(network, "pin_url", cancel)
    with pytest.raises(asyncio.CancelledError):
        synthesize(service)


def test_real_local_http_asr_wire_without_external_service(service):
    async def run():
        received = []

        async def handle(reader, writer):
            try:
                header = await reader.readuntil(b"\r\n\r\n")
                length = next(
                    int(line.split(b":", 1)[1])
                    for line in header.split(b"\r\n")
                    if line.lower().startswith(b"content-length:")
                )
                body = await reader.readexactly(length)
                received.append((header, body))
                response = json.dumps({"text": "合成本地录音"}, ensure_ascii=False).encode()
                writer.write(
                    b"HTTP/1.1 200 OK\r\nContent-Type: application/json\r\nContent-Length: "
                    + str(len(response)).encode()
                    + b"\r\nConnection: close\r\n\r\n"
                    + response
                )
                await writer.drain()
            finally:
                writer.close()
                await writer.wait_closed()

        server = await asyncio.start_server(handle, "127.0.0.1", 0)
        async with server:
            port = server.sockets[0].getsockname()[1]
            service.update(
                {"asr_base_url": f"http://127.0.0.1:{port}/v1", "asr_model": "synthetic-asr"}
            )
            result = await service.transcribe(AUDIO_B64, "audio/wav")
        assert result == {"text": "合成本地录音"}
        assert received[0][0].startswith(b"POST /v1/audio/transcriptions HTTP/1.1")
        assert AUDIO in received[0][1]

    asyncio.run(run())
