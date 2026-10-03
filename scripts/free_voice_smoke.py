"""Install and exercise free voice through the exact Windows package's HTTP API.

Downloads pinned public runtime/model resources into a disposable profile. The
fixed test sentence is synthesized locally, converted to PCM 16 kHz, and read
back by local ASR. No user microphone, paid API, or conversational model is used.
"""

from __future__ import annotations

import argparse
import base64
import hashlib
import io
import json
import math
import os
import platform
import re
import struct
import tempfile
import time
import tomllib
import wave
from datetime import datetime, timezone
from email.parser import Parser
from pathlib import Path
from typing import Any
from urllib.error import HTTPError
from urllib.request import ProxyHandler, Request, build_opener

try:
    from scripts.package_smoke import Probe, _NoRedirect, environment_info, require, sha256
except ModuleNotFoundError:
    from package_smoke import Probe, _NoRedirect, environment_info, require, sha256

STAGE = "windows-packaged-free-voice"
SENTENCE = "你好，我是小猫。今天我们一起学习，让生活更有趣。"
INSTALL_TIMEOUT = 900
INFERENCE_TIMEOUT = 120
MAX_RESPONSE = 12 * 1024 * 1024
MAX_AUDIO = 8 * 1024 * 1024
CASES = (
    "windows_x64_execution_environment",
    "archive_layout_and_safe_extraction",
    "authenticated_packaged_backend",
    "voice_routes_reject_missing_auth",
    "explicit_install_reaches_ready",
    "installed_model_and_runtime_provenance",
    "local_selection_preserves_cloud_configuration",
    "real_local_chinese_tts_without_chat_model",
    "real_local_asr_recognizes_synthesized_chinese",
    "restart_preserves_ready_resources",
    "graceful_stop_and_unchanged_archive",
)


class VoiceSmokeError(RuntimeError):
    """Only harness-owned diagnostic codes may reach the report."""


def wav_info(raw: bytes) -> tuple[dict[str, Any], tuple[int, ...]]:
    if not 44 <= len(raw) <= MAX_AUDIO:
        raise VoiceSmokeError("audio_size_invalid")
    try:
        with wave.open(io.BytesIO(raw), "rb") as audio:
            rate, frames = audio.getframerate(), audio.getnframes()
            if (
                audio.getnchannels() != 1
                or audio.getsampwidth() != 2
                or audio.getcomptype() != "NONE"
                or not 8000 <= rate <= 48000
                or not 0 < frames <= rate * 60
            ):
                raise VoiceSmokeError("audio_format_invalid")
            pcm = audio.readframes(frames)
            if len(pcm) != frames * 2:
                raise VoiceSmokeError("audio_truncated")
    except (wave.Error, EOFError) as exc:
        raise VoiceSmokeError("audio_wav_invalid") from exc
    samples = struct.unpack(f"<{frames}h", pcm)
    info = {
        "mime_type": "audio/wav",
        "channels": 1,
        "sample_rate": rate,
        "bits_per_sample": 16,
        "frames": frames,
        "duration_seconds": round(frames / rate, 4),
        "bytes": len(raw),
        "sha256": hashlib.sha256(raw).hexdigest(),
        "peak": max(abs(sample) for sample in samples),
        "rms": round(math.sqrt(sum(sample * sample for sample in samples) / frames), 4),
    }
    return info, samples


def asr_wav(raw: bytes) -> bytes:
    """Equivalent bounded PCM conversion to the browser's mono 16 kHz upload."""
    info, samples = wav_info(raw)
    if info["sample_rate"] == 16000:
        return raw
    ratio = info["sample_rate"] / 16000
    count = max(1, math.floor(len(samples) / ratio))
    pcm = bytearray(count * 2)
    for index in range(count):
        position = index * ratio
        if ratio >= 1:
            end = min(len(samples), (index + 1) * ratio)
            value = sum(
                samples[source] * (min(end, source + 1) - max(position, source))
                for source in range(math.floor(position), math.ceil(end))
            ) / (end - position)
        else:
            left, fraction = math.floor(position), position % 1
            value = (
                samples[left] * (1 - fraction) + samples[min(len(samples) - 1, left + 1)] * fraction
            )
        struct.pack_into("<h", pcm, index * 2, max(-32768, min(32767, round(value))))
    result = io.BytesIO()
    with wave.open(result, "wb") as target:
        target.setparams((1, 2, 16000, count, "NONE", "not compressed"))
        target.writeframes(pcm)
    return result.getvalue()


def transcript_match(expected: str, actual: str) -> dict[str, Any]:
    """Report a single clean-synthesis roundtrip, not an ASR quality benchmark."""
    left = "".join(char.lower() for char in expected if char.isalnum())
    right = "".join(char.lower() for char in actual if char.isalnum())
    previous = list(range(len(right) + 1))
    for row, source in enumerate(left, 1):
        current = [row]
        for column, target in enumerate(right, 1):
            current.append(
                min(
                    current[-1] + 1, previous[column] + 1, previous[column - 1] + (source != target)
                )
            )
        previous = current
    error_rate = previous[-1] / max(1, len(left))
    return {
        "expected_normalized_characters": len(left),
        "recognized_normalized_characters": len(right),
        "edit_distance": previous[-1],
        "character_error_rate": round(error_rate, 4),
        "maximum_character_error_rate": 0.35,
        "passed": bool(left and right and error_rate <= 0.35),
        "scope": "one_fixed_synthesized_chinese_sentence_not_microphone_quality",
    }


class FreeVoiceProbe(Probe):
    def __init__(self, archive: Path, evidence: dict, wav_output: Path, previous_wav: str | None):
        super().__init__(archive, evidence)
        self.wav_output, self.previous_wav = wav_output, previous_wav
        self.data: Path | None = None
        self.audio: bytes | None = None

    def request(self, method, path, *, token=None, data=None, timeout=3):
        headers = {"Origin": self.connection["http_url"]}
        if token is not None:
            headers["Authorization"] = f"Bearer {token}"
        if data is not None:
            headers["Content-Type"] = "application/json"
        request = Request(
            self.connection["http_url"] + path,
            method=method,
            data=json.dumps(data).encode("utf-8") if data is not None else None,
            headers=headers,
        )
        opener = build_opener(ProxyHandler({}), _NoRedirect())
        try:
            response = opener.open(request, timeout=timeout)
        except HTTPError as error:
            response = error
        with response:
            body = response.read(MAX_RESPONSE + 1)
            if len(body) > MAX_RESPONSE:
                raise VoiceSmokeError("api_response_too_large")
            return response.status, body

    def api(self, method, path, data=None, *, timeout=10):
        status, body = self.request(
            method, path, token=self.connection["token"], data=data, timeout=timeout
        )
        try:
            value = json.loads(body)
        except (ValueError, UnicodeError):
            raise VoiceSmokeError("api_invalid_json") from None
        if not 200 <= status < 300:
            failure = {"route": path, "http_status": status}
            code = value.get("code") if isinstance(value, dict) else None
            if (
                isinstance(code, str)
                and re.fullmatch(r"[a-z][a-z0-9_]{0,79}", code)
                and code != self.connection.get("token")
            ):
                failure["code"] = code
            self.evidence["api_failure"] = failure
            raise VoiceSmokeError("api_request_failed")
        if not isinstance(value, dict):
            raise VoiceSmokeError("api_invalid_object")
        return value

    def check_voice_auth(self):
        for method, path in (
            ("GET", "/api/voice/local"),
            ("POST", "/api/voice/local/install"),
            ("POST", "/api/voice/local/cancel"),
            ("POST", "/api/voice/synthesize"),
            ("POST", "/api/voice/transcribe"),
        ):
            require(self.request(method, path)[0] in {401, 403})

    def install_voice(self):
        require(self.api("GET", "/api/voice/local")["state"] == "missing")
        started = time.monotonic()
        deadline = started + INSTALL_TIMEOUT
        status = self.api("POST", "/api/voice/local/install", {"confirm": True})
        record = self.evidence["installation"] = {
            "timeout_seconds": INSTALL_TIMEOUT,
            "explicit_confirmation": True,
            "transitions": [],
        }
        last = None
        while True:
            public = {
                key: status.get(key)
                for key in ("state", "stage", "downloaded_bytes", "total_bytes", "supported")
            }
            record["last_status"] = public
            signature = public["state"], public["stage"]
            if signature != last:
                record["transitions"].append(
                    {**public, "elapsed_seconds": round(time.monotonic() - started, 3)}
                )
                last = signature
            record["elapsed_seconds"] = round(time.monotonic() - started, 3)
            if public["state"] == "ready":
                return
            if public["state"] != "installing":
                raise VoiceSmokeError("local_voice_install_failed")
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                raise VoiceSmokeError("local_voice_install_timeout")
            time.sleep(min(1, remaining))
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                raise VoiceSmokeError("local_voice_install_timeout")
            status = self.api("GET", "/api/voice/local", timeout=min(10, remaining))

    def check_provenance(self):
        require(self.data is not None)
        root = self.data / "assets" / "voice"
        manifest = json.loads((root / "ready.json").read_text(encoding="utf-8"))
        require(set(manifest) == {"schema", "lock", "asr", "tts", "python"})
        require(manifest["schema"] == 1)
        for key in ("lock", "asr", "tts"):
            require(
                isinstance(manifest[key], str)
                and bool(re.fullmatch(r"[a-f0-9]{64}", manifest[key]))
            )
        require(manifest["lock"] == sha256(root / "runtime" / "uv.lock"))
        lock = tomllib.loads((root / "runtime" / "uv.lock").read_text(encoding="utf-8"))
        package_names = {"sherpa-onnx", "sherpa-onnx-core", "numpy"}
        locked = {
            item["name"]: item["version"]
            for item in lock["package"]
            if item["name"] in package_names
        }
        require(set(locked) == package_names)
        sites = [
            path
            for path in (
                root / "venv/Lib/site-packages",
                root / "venv/lib/python3.11/site-packages",
            )
            if path.is_dir()
        ]
        require(len(sites) == 1 and not sites[0].is_symlink())
        installed = {}
        for metadata in sites[0].glob("*.dist-info/METADATA"):
            require(not metadata.is_symlink() and metadata.stat().st_size < 1024 * 1024)
            parsed = Parser().parsestr(metadata.read_text(encoding="utf-8"))
            name = re.sub(r"[-_.]+", "-", (parsed.get("Name") or "").lower())
            if name in package_names:
                require(name not in installed)
                installed[name] = parsed.get("Version")
        require(installed == locked)
        downloads = []
        for archive in sorted((root / "downloads").iterdir()):
            require(archive.is_file() and not archive.is_symlink())
            downloads.append(
                {"name": archive.name, "bytes": archive.stat().st_size, "sha256": sha256(archive)}
            )
        require(len(downloads) == 3)
        require({manifest["asr"], manifest["tts"]}.issubset({item["sha256"] for item in downloads}))
        models = []
        for model in sorted((root / "models").glob("*/model.int8.onnx")):
            require(not model.is_symlink() and model.is_file())
            models.append(
                {
                    "name": model.parent.name,
                    "model_bytes": model.stat().st_size,
                    "model_sha256": sha256(model),
                }
            )
        require(len(models) == 2)
        self.evidence["installed_resources"] = {
            "manifest": manifest,
            "runtime_packages": [
                {"name": name, "version": locked[name]} for name in sorted(locked)
            ],
            "runtime_packages_source": "uv.lock_declarations",
            "installed_packages": [
                {"name": name, "version": installed[name]} for name in sorted(installed)
            ],
            "installed_packages_source": "private_venv_dist_info_METADATA",
            "installed_packages_match_lock": True,
            "download_archives": downloads,
            "models": models,
            "worker_sha256": sha256(root / "runtime" / "worker.py"),
            "model_files_sha256": sha256(root / "runtime" / "model_files.json"),
        }

    def select_local(self):
        before = self.api("GET", "/api/voice/config")
        require(not before["asr_key_set"] and not before["tts_key_set"])
        after = self.api("PUT", "/api/voice/config", {"voice_provider": "local"})
        require(after["voice_provider"] == "local" and after["local"]["state"] == "ready")
        for key, value in before.items():
            if key not in {"voice_provider", "local", "asr_ready", "tts_ready"}:
                require(after[key] == value)
        model = self.api("GET", "/api/config")
        # A fresh profile contains the default endpoint but no selected model
        # or key. Endpoint text alone does not constitute a configured model.
        require(not model.get("model_key_set") and not model.get("model"))
        self.evidence["configuration"] = {
            "voice_provider": "local",
            "cloud_voice_keys_set": False,
            "prior_cloud_voice_fields_preserved": True,
            "chat_model_configured": False,
        }

    def synthesize(self):
        started = time.monotonic()
        result = self.api(
            "POST", "/api/voice/synthesize", {"text": SENTENCE}, timeout=INFERENCE_TIMEOUT
        )
        elapsed = time.monotonic() - started
        require(result.get("mime_type") == "audio/wav")
        try:
            raw = base64.b64decode(result["audio_base64"], validate=True)
        except (ValueError, KeyError):
            raise VoiceSmokeError("tts_invalid_audio") from None
        info, _ = wav_info(raw)
        require(info["peak"] > 0 and info["rms"] > 0)
        if self.wav_output.is_symlink() or (
            self.wav_output.exists()
            and (self.previous_wav is None or sha256(self.wav_output) != self.previous_wav)
        ):
            raise VoiceSmokeError("audio_output_changed")
        self.evidence["synthesis"] = {
            "text": SENTENCE,
            "characters": len(SENTENCE),
            "elapsed_seconds": round(elapsed, 3),
            "timeout_seconds": INFERENCE_TIMEOUT,
            "audio": {**info, "file": self.wav_output.name},
        }
        self.wav_output.write_bytes(raw)
        self.audio = raw

    def transcribe(self):
        require(self.audio is not None)
        raw = asr_wav(self.audio)
        info, _ = wav_info(raw)
        started = time.monotonic()
        result = self.api(
            "POST",
            "/api/voice/transcribe",
            {"audio_base64": base64.b64encode(raw).decode("ascii"), "mime_type": "audio/wav"},
            timeout=INFERENCE_TIMEOUT,
        )
        text = result.get("text")
        require(isinstance(text, str) and 0 < len(text) <= 8000)
        match = transcript_match(SENTENCE, text)
        self.evidence["recognition"] = {
            "text": text,
            "characters": len(text),
            "elapsed_seconds": round(time.monotonic() - started, 3),
            "timeout_seconds": INFERENCE_TIMEOUT,
            "input_audio": info,
            "match": match,
        }
        require(match["passed"])

    def restart(self):
        require(self.data is not None and self.server is not None)
        old_pid = self.server.pid
        self.stop_server(self.data)
        self.start_server(self.data)
        require(self.server.pid != old_pid)
        require(self.api("GET", "/api/voice/local")["state"] == "ready")
        require(self.api("GET", "/api/voice/config")["voice_provider"] == "local")
        self.evidence["restart"] = {
            "first_pid": old_pid,
            "second_pid": self.server.pid,
            "ready_without_redownload": True,
        }

    def run(self, temporary):
        self.case(
            "windows_x64_execution_environment",
            lambda: require(
                os.name == "nt"
                and platform.machine().lower() in {"amd64", "x86_64"}
                and struct.calcsize("P") == 8
            ),
        )
        self.case("archive_layout_and_safe_extraction", lambda: self.prepare_archive(temporary))
        self.prepare_profile(temporary)
        self.data = temporary / "免费语音 合成数据"
        self.evidence["execution"] = {
            "backend_executable": str(self.exe),
            "working_directory": str(self.root),
            "data_root": str(self.data),
            "source_application_imports": False,
        }
        self.case("authenticated_packaged_backend", lambda: self.start_server(self.data))
        self.case("voice_routes_reject_missing_auth", self.check_voice_auth)
        self.case("explicit_install_reaches_ready", self.install_voice)
        self.case("installed_model_and_runtime_provenance", self.check_provenance)
        self.case("local_selection_preserves_cloud_configuration", self.select_local)
        self.case("real_local_chinese_tts_without_chat_model", self.synthesize)
        self.case("real_local_asr_recognizes_synthesized_chinese", self.transcribe)
        self.case("restart_preserves_ready_resources", self.restart)

        def finish():
            self.stop_server(self.data)
            require(sha256(self.archive) == self.evidence["archive_sha256"])

        self.case("graceful_stop_and_unchanged_archive", finish)

    def cleanup(self):
        if self.server is not None and self.server.poll() is None and self.connection:
            try:
                self.api("POST", "/api/voice/local/cancel", {}, timeout=15)
                if self.data is not None:
                    self.stop_server(self.data)
            except Exception:
                pass
        super().cleanup()


def validate_output(output: Path, archive: Path, parser) -> tuple[Path, str | None]:
    root = Path(__file__).resolve().parents[1]
    if (
        output.suffix != ".json"
        or output.name in {"connection.json", "build-info.json"}
        or output == archive
        or output.is_symlink()
        or any(
            root / name in output.parents for name in ("src", "tests", "scripts", ".git", ".venv")
        )
    ):
        parser.error("Use a dedicated JSON evidence file outside application sources")
    existing = {}
    if output.exists():
        try:
            existing = json.loads(output.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            parser.error("Refusing to overwrite unrelated evidence")
        if not isinstance(existing, dict) or existing.get("stage") != STAGE:
            parser.error("Refusing to overwrite unrelated evidence")
    audio = output.with_name(output.stem + "-speech.wav")
    if audio == archive:
        parser.error("Audio evidence must not overwrite the package archive")
    previous = existing.get("synthesis", {}).get("audio", {})
    if audio.is_symlink() or (
        audio.exists()
        and (previous.get("file") != audio.name or previous.get("sha256") != sha256(audio))
    ):
        parser.error("Refusing to overwrite unrelated audio")
    return audio, previous.get("sha256")


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--archive", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args(argv)
    archive = args.archive.expanduser().resolve()
    if args.output.expanduser().is_symlink():
        parser.error("Evidence output must not be a symlink")
    output = args.output.expanduser().resolve()
    audio, previous = validate_output(output, archive, parser)
    output.parent.mkdir(parents=True, exist_ok=True)
    started = time.monotonic()
    evidence = {
        "schema_version": 1,
        "app_id": "ai-neko",
        "stage": STAGE,
        "status": "FAILED",
        "started_at_utc": datetime.now(timezone.utc).isoformat(),
        "archive": str(archive),
        "archive_sha256": None,
        "executable_sha256": None,
        "source_commit": None,
        "environment": environment_info(),
        "tests": {
            "kind": "real_local_voice_with_fixed_synthetic_text",
            "cases": [{"name": name, "outcome": "NOT_RUN"} for name in CASES],
        },
        "scope": {
            "actual_user_microphone_captures": 0,
            "paid_api_calls": 0,
            "conversational_model_calls": 0,
            "real_voice_inference": "NOT_VERIFIED",
            "network_during_installation": "public_pinned_runtime_and_models",
            "speaker_playback": "NOT_TESTED",
            "windows_11_device_acceptance": "NOT_TESTED",
        },
    }
    probe = FreeVoiceProbe(archive, evidence, audio, previous)
    try:
        evidence["archive_sha256"] = sha256(archive)
        with tempfile.TemporaryDirectory(prefix="ai-neko 免费语音 验证 ") as temporary:
            try:
                probe.run(Path(temporary).resolve())
            finally:
                probe.cleanup()
        evidence["status"] = "PASS"
        evidence["scope"]["real_voice_inference"] = "PASS"
    except (Exception, KeyboardInterrupt) as error:
        evidence["failure"] = {"stage": probe.current_stage, "error_class": type(error).__name__}
        if isinstance(error, VoiceSmokeError):
            evidence["failure"]["code"] = str(error)
    evidence["finished_at_utc"] = datetime.now(timezone.utc).isoformat()
    evidence["duration_seconds"] = round(time.monotonic() - started, 3)
    evidence["failure_detail_policy"] = (
        "No raw application logs, stdout, stderr, tokens, request headers or tracebacks."
    )
    output.write_text(json.dumps(evidence, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    passed = sum(item["outcome"] == "PASS" for item in evidence["tests"]["cases"])
    print(f"Windows packaged free voice {evidence['status']}: {passed}/{len(CASES)} checks passed")
    if "failure" in evidence:
        print(f"Stage: {evidence['failure']['stage']}; error: {evidence['failure']['error_class']}")
    print(f"Evidence: {output}")
    return 0 if evidence["status"] == "PASS" else 1


if __name__ == "__main__":
    raise SystemExit(main())
