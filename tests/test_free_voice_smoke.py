"""Harness protocol/safety tests; no model download or real inference occurs."""

from __future__ import annotations

import argparse
import base64
import importlib.util
import io
import json
import struct
import threading
import wave
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from types import SimpleNamespace

import pytest

SCRIPT = Path(__file__).resolve().parents[1] / "scripts" / "free_voice_smoke.py"
SPEC = importlib.util.spec_from_file_location("free_voice_smoke", SCRIPT)
assert SPEC is not None and SPEC.loader is not None
smoke = importlib.util.module_from_spec(SPEC)
with pytest.MonkeyPatch.context() as imports:
    imports.syspath_prepend(str(SCRIPT.parent))
    SPEC.loader.exec_module(smoke)


def wav(samples=(3, 6, 9), rate=24000, channels=1):
    result = io.BytesIO()
    with wave.open(result, "wb") as audio:
        audio.setparams((channels, 2, rate, len(samples) // channels, "NONE", "not compressed"))
        audio.writeframes(struct.pack(f"<{len(samples)}h", *samples))
    return result.getvalue()


def probe(tmp_path):
    return smoke.FreeVoiceProbe(tmp_path / "unused.zip", {}, tmp_path / "sample-speech.wav", None)


def test_pcm_conversion_has_real_wav_header_and_keeps_duration():
    raw = smoke.asr_wav(wav())
    info, samples = smoke.wav_info(raw)
    assert raw[:4] == b"RIFF" and raw[8:12] == b"WAVE"
    assert info["sample_rate"] == 16000
    assert info["bits_per_sample"] == 16 and info["channels"] == 1
    assert samples == (4, 8)
    unchanged = wav([123] * 16000, rate=16000)
    assert smoke.asr_wav(unchanged) == unchanged
    assert smoke.wav_info(unchanged)[0]["duration_seconds"] == 1


@pytest.mark.parametrize("raw", [b"not audio", wav([1, 2], channels=2), wav(rate=4000), wav()[:-1]])
def test_invalid_audio_never_becomes_an_asr_request(raw):
    with pytest.raises(smoke.VoiceSmokeError):
        smoke.asr_wav(raw)


def test_roundtrip_gate_reports_normalized_cer_and_rejects_gibberish():
    assert smoke.transcript_match("你好，小猫！", "你好小猫。")["passed"]
    assert not smoke.transcript_match(smoke.SENTENCE, "这是完全无关的内容")["passed"]
    assert not smoke.transcript_match(smoke.SENTENCE, "")["passed"]
    result = smoke.transcript_match("甲乙丙丁", "甲乙丙戊")
    assert result["character_error_rate"] == 0.25 and result["edit_distance"] == 1


def test_install_requires_explicit_confirmation_and_records_ready_transition(tmp_path, monkeypatch):
    item = probe(tmp_path)
    elapsed = [0]
    monkeypatch.setattr(
        smoke,
        "time",
        SimpleNamespace(
            monotonic=lambda: elapsed[0], sleep=lambda n: elapsed.__setitem__(0, elapsed[0] + n)
        ),
    )
    responses = iter(
        [
            {"state": "missing"},
            {"state": "installing", "stage": "runtime", "downloaded_bytes": 0, "total_bytes": 100},
            {"state": "installing", "stage": "tts", "downloaded_bytes": 90, "total_bytes": 100},
            {"state": "ready", "stage": "complete", "downloaded_bytes": 100, "total_bytes": 100},
        ]
    )
    calls = []

    def api(*args, **kwargs):
        calls.append((args, kwargs))
        return next(responses)

    item.api = api
    item.install_voice()
    assert calls[1][0] == ("POST", "/api/voice/local/install", {"confirm": True})
    assert [entry["stage"] for entry in item.evidence["installation"]["transitions"]] == [
        "runtime",
        "tts",
        "complete",
    ]
    assert item.evidence["installation"]["last_status"]["state"] == "ready"


@pytest.mark.parametrize("state", ["error", "installing"])
def test_install_failure_or_deadline_stays_failure(tmp_path, monkeypatch, state):
    item = probe(tmp_path)
    elapsed = [0]
    monkeypatch.setattr(smoke, "INSTALL_TIMEOUT", 2)
    monkeypatch.setattr(
        smoke,
        "time",
        SimpleNamespace(
            monotonic=lambda: elapsed[0], sleep=lambda n: elapsed.__setitem__(0, elapsed[0] + n)
        ),
    )
    count = [0]

    def api(*_args, **_kwargs):
        count[0] += 1
        return {
            "state": "missing" if count[0] == 1 else state,
            "stage": "runtime",
            "message": "do not copy raw status text",
        }

    item.api = api
    with pytest.raises(smoke.VoiceSmokeError, match="install_failed|install_timeout"):
        item.install_voice()
    assert "do not copy" not in json.dumps(item.evidence)
    assert elapsed[0] <= 2


def test_api_pipeline_has_auth_pcm_conversion_no_model_and_safe_errors(tmp_path):
    item = probe(tmp_path)
    token = "synthetic-separate-test-token-123456789"
    recorded = []
    configuration = {
        "voice_provider": "openai",
        "asr_base_url": "",
        "asr_model": "saved-asr",
        "tts_base_url": "",
        "tts_model": "saved-tts",
        "tts_voice": "saved-voice",
        "asr_key_set": False,
        "tts_key_set": False,
        "local": {"state": "ready"},
    }

    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *_args):
            pass

        def do_GET(self):
            self.respond()

        def do_POST(self):
            self.respond()

        def do_PUT(self):
            self.respond()

        def respond(self):
            raw = self.rfile.read(int(self.headers.get("Content-Length", "0")))
            body = json.loads(raw) if raw else None
            authenticated = self.headers.get("Authorization") == f"Bearer {token}"
            recorded.append((self.command, self.path, body, authenticated))
            status = 200
            if not authenticated:
                status, result = 401, {"detail": "denied"}
            elif self.path == "/api/voice/config":
                if self.command == "PUT":
                    assert body == {"voice_provider": "local"}
                    configuration.update(body)
                result = configuration
            elif self.path == "/api/config":
                result = {
                    "model": "",
                    "model_base_url": "https://api.openai.com/v1",
                    "model_key_set": False,
                }
            elif self.path == "/api/voice/synthesize":
                assert body == {"text": smoke.SENTENCE}
                result = {
                    "audio_base64": base64.b64encode(wav([100, -100, 200] * 2400)).decode(),
                    "mime_type": "audio/wav",
                }
            elif self.path == "/api/voice/transcribe":
                assert set(body) == {"audio_base64", "mime_type"}
                assert body["mime_type"] == "audio/wav"
                assert (
                    smoke.wav_info(base64.b64decode(body["audio_base64"]))[0]["sample_rate"]
                    == 16000
                )
                result = {"text": smoke.SENTENCE}
            else:
                status, result = (
                    400,
                    {
                        "code": "local_voice_failed",
                        "detail": token,
                        "request_headers": dict(self.headers),
                    },
                )
            encoded = json.dumps(result).encode()
            self.send_response(status)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(encoded)))
            self.end_headers()
            self.wfile.write(encoded)

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    item.connection = {"http_url": f"http://127.0.0.1:{server.server_port}", "token": token}
    try:
        item.check_voice_auth()
        item.select_local()
        item.synthesize()
        item.transcribe()
        assert item.wav_output.is_file()
        assert item.evidence["recognition"]["match"]["passed"]
        assert item.evidence["configuration"]["chat_model_configured"] is False
        assert all(auth for _, _, _, auth in recorded[5:])
        assert not any("install" in path and auth for _, path, _, auth in recorded)
        with pytest.raises(smoke.VoiceSmokeError, match="api_request_failed"):
            item.api("GET", "/synthetic-failure")
        assert item.evidence["api_failure"] == {
            "route": "/synthetic-failure",
            "http_status": 400,
            "code": "local_voice_failed",
        }
        assert token not in json.dumps(item.evidence)
        assert "request_headers" not in json.dumps(item.evidence)
    finally:
        server.shutdown()
        thread.join(timeout=5)
        server.server_close()


def test_outputs_reject_unrelated_files_and_allow_only_owned_audio(tmp_path):
    parser = argparse.ArgumentParser()
    output, archive = tmp_path / "voice.json", tmp_path / "package.zip"
    output.write_text('{"stage": "unrelated"}')
    with pytest.raises(SystemExit):
        smoke.validate_output(output, archive, parser)
    output.write_text(json.dumps({"stage": smoke.STAGE}))
    audio = output.with_name("voice-speech.wav")
    audio.write_bytes(wav())
    with pytest.raises(SystemExit):
        smoke.validate_output(output, archive, parser)
    digest = smoke.sha256(audio)
    output.write_text(
        json.dumps(
            {"stage": smoke.STAGE, "synthesis": {"audio": {"file": audio.name, "sha256": digest}}}
        )
    )
    assert smoke.validate_output(output, archive, parser) == (audio, digest)
    audio.write_bytes(b"changed after old run")
    with pytest.raises(SystemExit):
        smoke.validate_output(output, archive, parser)


def test_audio_output_cannot_alias_package_archive(tmp_path):
    with pytest.raises(SystemExit):
        smoke.validate_output(
            tmp_path / "voice.json", tmp_path / "voice-speech.wav", argparse.ArgumentParser()
        )


def test_main_failure_never_promotes_checks_or_prints_sensitive_exception(
    tmp_path, monkeypatch, capsys
):
    archive, output = tmp_path / "synthetic.zip", tmp_path / "voice.json"
    archive.write_bytes(b"synthetic")

    def fail(_self, _directory):
        raise RuntimeError("secret-token-should-not-appear")

    monkeypatch.setattr(smoke.FreeVoiceProbe, "run", fail)
    assert smoke.main(["--archive", str(archive), "--output", str(output)]) == 1
    report = json.loads(output.read_text())
    assert report["status"] == "FAILED"
    assert report["scope"]["real_voice_inference"] == "NOT_VERIFIED"
    assert all(case["outcome"] == "NOT_RUN" for case in report["tests"]["cases"])
    assert "secret-token" not in output.read_text() + capsys.readouterr().out


@pytest.mark.parametrize(
    "site_relative", ["venv/Lib/site-packages", "venv/lib/python3.11/site-packages"]
)
def test_provenance_reads_real_installed_metadata_and_archive_bytes(tmp_path, site_relative):
    item = probe(tmp_path)
    item.data = tmp_path / "data"
    root = item.data / "assets/voice"
    runtime = root / "runtime"
    runtime.mkdir(parents=True)
    versions = {"numpy": "2.4.4", "sherpa-onnx": "1.13.8", "sherpa-onnx-core": "1.13.8"}
    (runtime / "uv.lock").write_text(
        "\n".join(
            f'[[package]]\nname = "{name}"\nversion = "{version}"\n'
            for name, version in versions.items()
        )
    )
    (runtime / "worker.py").write_text("# synthetic worker source\n")
    (runtime / "model_files.json").write_text("{}\n")
    sites = root / site_relative
    for name, version in versions.items():
        info = sites / f"{name.replace('-', '_')}-{version}.dist-info"
        info.mkdir(parents=True)
        (info / "METADATA").write_text(f"Metadata-Version: 2.1\nName: {name}\nVersion: {version}\n")
    downloads = root / "downloads"
    downloads.mkdir()
    for name in ("uv.zip", "asr.tar.bz2", "tts.tar.bz2"):
        (downloads / name).write_bytes(name.encode())
    manifest = {
        "schema": 1,
        "lock": smoke.sha256(runtime / "uv.lock"),
        "asr": smoke.sha256(downloads / "asr.tar.bz2"),
        "tts": smoke.sha256(downloads / "tts.tar.bz2"),
        "python": "3.11.15",
    }
    (root / "ready.json").write_text(json.dumps(manifest))
    for name in ("synthetic-asr", "synthetic-tts"):
        directory = root / "models" / name
        directory.mkdir(parents=True)
        (directory / "model.int8.onnx").write_bytes(name.encode())
    item.check_provenance()
    actual = item.evidence["installed_resources"]
    assert actual["installed_packages_match_lock"] is True
    assert actual["installed_packages"] == actual["runtime_packages"]
    assert len(actual["download_archives"]) == 3
    assert actual["model_files_sha256"] == smoke.sha256(runtime / "model_files.json")
    (sites / "sherpa_onnx_core-1.13.8.dist-info/METADATA").write_text(
        "Name: sherpa-onnx-core\nVersion: 0.0.0\n"
    )
    with pytest.raises(AssertionError):
        item.check_provenance()
