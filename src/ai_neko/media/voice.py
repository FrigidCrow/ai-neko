"""Bounded OpenAI-compatible ASR/TTS with independent, redacted credentials.

Audio is request-scoped and never written to disk. This service performs no
conversation generation, playback, retries, or reads of another app's settings.
"""

from __future__ import annotations

import asyncio
import base64
import binascii
import json
import os
import tempfile
import threading
from pathlib import Path

import httpx

from ai_neko.config.credentials import Credentials
from ai_neko.config.paths import DataPaths, safe_child
from ai_neko.config.providers import endpoint
from ai_neko.tools import network

DEFAULTS = {
    "asr_base_url": "https://api.openai.com/v1",
    "asr_model": "",
    "tts_base_url": "https://api.openai.com/v1",
    "tts_model": "",
    "tts_voice": "alloy",
}
MAX_AUDIO_BYTES = 8 * 1024 * 1024
MAX_AUDIO_BASE64 = 4 * ((MAX_AUDIO_BYTES + 2) // 3)
MAX_TRANSCRIPT_CHARS = 8_000
MAX_TRANSCRIPT_RESPONSE = 128 * 1024
MAX_SPEECH_CHARS = 4_096
REQUEST_TIMEOUT = 60
_INPUT_TYPES = {
    "audio/webm": "webm",
    "audio/ogg": "ogg",
    "audio/wav": "wav",
    "audio/x-wav": "wav",
    "audio/wave": "wav",
    "audio/mpeg": "mp3",
    "audio/mp3": "mp3",
    "audio/mp4": "m4a",
    "audio/x-m4a": "m4a",
    "audio/flac": "flac",
    "audio/x-flac": "flac",
}


class VoiceError(RuntimeError):
    """Stable UI-safe error; upstream content and credentials are never included."""

    def __init__(self, code: str, message: str):
        self.code = code
        self.message = message
        super().__init__(message)


def _validate(config):
    if not isinstance(config, dict) or set(config) != set(DEFAULTS):
        raise ValueError("invalid_voice_config")
    result = {}
    try:
        for name in ("asr_base_url", "tts_base_url"):
            result[name] = endpoint(config[name], model=True)
        for name in ("asr_model", "tts_model", "tts_voice"):
            value = config[name]
            if (
                not isinstance(value, str)
                or len(value) > 200
                or any(
                    ord(char) < 32 or ord(char) == 127 or 0xD800 <= ord(char) <= 0xDFFF
                    for char in value
                )
            ):
                raise ValueError
            result[name] = value.strip()
    except (ValueError, TypeError):
        raise ValueError("invalid_voice_config") from None
    return result


def _looks_like_audio(data: bytes, extension: str) -> bool:
    """Validate container signatures, not decode untrusted media in the backend."""
    if len(data) < 12:
        return False
    if extension == "webm":
        return data.startswith(b"\x1a\x45\xdf\xa3")
    if extension == "ogg":
        return data.startswith(b"OggS")
    if extension == "wav":
        return data[:4] == b"RIFF" and data[8:12] == b"WAVE"
    if extension == "flac":
        return data.startswith(b"fLaC")
    if extension == "m4a":
        return data[4:8] == b"ftyp"
    if extension == "mp3":
        return data.startswith(b"ID3") or (data[0] == 0xFF and data[1] & 0xE0 == 0xE0)
    return False


def _decode_audio(audio_base64, mime_type):
    if not isinstance(mime_type, str) or len(mime_type) > 100:
        raise VoiceError("invalid_audio_type", "录音格式不受支持，请重新录音。")
    pieces = [part.strip().lower() for part in mime_type.split(";")]
    mime = pieces[0]
    # MediaRecorder adds the codec parameter in Chromium. No arbitrary MIME or
    # header fragments may reach the multipart Content-Type.
    if mime not in _INPUT_TYPES or any(
        piece not in {"codecs=opus", 'codecs="opus"'} for piece in pieces[1:]
    ):
        raise VoiceError("invalid_audio_type", "录音格式不受支持，请重新录音。")
    if not isinstance(audio_base64, str) or not audio_base64:
        raise VoiceError("invalid_audio", "没有可识别的录音，请重新录音。")
    if len(audio_base64) > MAX_AUDIO_BASE64:
        raise VoiceError("audio_too_large", "录音超过 8 MiB，请缩短后重试。")
    try:
        data = base64.b64decode(audio_base64, validate=True)
    except (ValueError, binascii.Error):
        raise VoiceError("invalid_audio", "录音数据无效，请重新录音。") from None
    if len(data) > MAX_AUDIO_BYTES:
        raise VoiceError("audio_too_large", "录音超过 8 MiB，请缩短后重试。")
    extension = _INPUT_TYPES[mime]
    if not _looks_like_audio(data, extension):
        raise VoiceError("invalid_audio", "录音数据与格式不符，请重新录音。")
    return data, mime, extension


class VoiceService:
    def __init__(self, paths: DataPaths):
        self.paths = paths
        self._lock = threading.RLock()
        self._credentials = Credentials(paths.root)
        self._config = dict(DEFAULTS)
        path = safe_child(paths.config, "voice.json")
        if path.exists():
            try:
                if path.stat().st_size > 16_384:
                    raise ValueError
                saved = json.loads(path.read_text(encoding="utf-8"))
                if (
                    not isinstance(saved, dict)
                    or set(saved) != {"app_id", "schema_version", "voice"}
                    or saved["app_id"] != "ai-neko"
                    or type(saved["schema_version"]) is not int
                    or saved["schema_version"] != 1
                ):
                    raise ValueError
                self._config = _validate(saved["voice"])
            except (OSError, ValueError, TypeError):
                raise ValueError("invalid_voice_config") from None

    def public_config(self) -> dict:
        with self._lock:
            return {
                **self._config,
                "asr_key_set": bool(self._credentials.get("asr")),
                "tts_key_set": bool(self._credentials.get("tts")),
                "credential_storage": self._credentials.storage,
            }

    def update(self, changes: dict) -> dict:
        allowed = set(DEFAULTS) | {
            "asr_api_key",
            "tts_api_key",
            "clear_asr_api_key",
            "clear_tts_api_key",
        }
        with self._lock:
            if not isinstance(changes, dict) or set(changes) - allowed:
                raise ValueError("invalid_voice_fields")
            updated = _validate(
                {
                    **self._config,
                    **{key: value for key, value in changes.items() if key in DEFAULTS},
                }
            )
            secrets = {}
            for kind in ("asr", "tts"):
                key = changes.get(kind + "_api_key", "")
                clear = changes.get("clear_" + kind + "_api_key", False)
                if (
                    not isinstance(key, str)
                    or len(key) > 2000
                    or any(ord(char) < 33 or ord(char) > 126 for char in key)
                    or type(clear) is not bool
                    or (clear and key)
                ):
                    raise ValueError("invalid_credential")
                if clear or key:
                    secrets[kind] = None if clear else key
            previous = {kind: self._credentials.get(kind) for kind in secrets}
            changed = []
            try:
                for kind, secret in secrets.items():
                    self._credentials.set(kind, secret)
                    changed.append(kind)
                self._save(updated)
            except (OSError, ValueError):
                for kind in reversed(changed):
                    self._credentials.set(kind, previous[kind])
                raise
            self._config = updated
            return self.public_config()

    def _save(self, config):
        destination = safe_child(self.paths.config, "voice.json")
        fd, temporary = tempfile.mkstemp(prefix=".voice-", suffix=".tmp", dir=self.paths.config)
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as handle:
                json.dump(
                    {"app_id": "ai-neko", "schema_version": 1, "voice": config},
                    handle,
                    ensure_ascii=False,
                    indent=2,
                )
                handle.write("\n")
                handle.flush()
                os.fsync(handle.fileno())
            Path(temporary).chmod(0o600)
            os.replace(temporary, destination)
        except OSError:
            raise ValueError("voice_config_write_failed") from None
        finally:
            Path(temporary).unlink(missing_ok=True)

    def _snapshot(self, kind):
        with self._lock:
            config = dict(self._config)
            if not config[kind + "_model"] or (kind == "tts" and not config["tts_voice"]):
                raise VoiceError(kind + "_not_configured", "请先设置语音服务的地址、模型和音色。")
            key = self._credentials.get(kind)
            if not key and not network.explicit_loopback(
                network.parse_url(config[kind + "_base_url"]).host
            ):
                raise VoiceError(kind + "_key_missing", "请先配置对应语音服务的 API Key。")
            return config, key

    async def _request(self, kind, config, key, path, limit, expected_types, **kwargs):
        try:
            async with asyncio.timeout(REQUEST_TIMEOUT):
                target, headers, extensions = await network.pin_url(
                    config[kind + "_base_url"] + path, allow_local=True
                )
                headers["Accept"] = ", ".join(expected_types)
                if key:
                    headers["Authorization"] = "Bearer " + key
                async with network.client() as client:
                    async with client.stream(
                        "POST", target, headers=headers, extensions=extensions, **kwargs
                    ) as response:
                        status = response.status_code
                        if status in {401, 403}:
                            raise VoiceError(
                                kind + "_authentication_failed",
                                "语音服务鉴权失败，请检查 API Key。",
                            )
                        if status == 429:
                            raise VoiceError(
                                kind + "_rate_limited", "语音服务请求受限，请稍后重试。"
                            )
                        if status != 200:
                            raise VoiceError(
                                kind + "_http_error", "语音服务返回错误，请检查配置后重试。"
                            )
                        mime = (
                            response.headers.get("content-type", "")
                            .split(";", 1)[0]
                            .strip()
                            .lower()
                        )
                        if mime not in expected_types:
                            raise VoiceError(kind + "_invalid_response", "语音服务返回格式不正确。")
                        body = await network.bounded_body(response, limit=limit)
                        if not body:
                            raise VoiceError(kind + "_invalid_response", "语音服务返回了空内容。")
                        return body
        except VoiceError:
            raise
        except (TimeoutError, httpx.TimeoutException):
            raise VoiceError(kind + "_timeout", "语音请求超时，请重试。") from None
        except network.NetworkPolicyError as exc:
            if str(exc) == "response_too_large":
                raise VoiceError(kind + "_output_limit", "语音服务响应超过大小限制。") from None
            if str(exc) == "unsupported_encoding":
                raise VoiceError(kind + "_invalid_response", "语音服务返回格式不正确。") from None
            raise VoiceError(
                kind + "_blocked_endpoint", "语音地址不可访问或不符合网络策略。"
            ) from None
        except httpx.HTTPError:
            raise VoiceError(kind + "_network_error", "无法连接语音服务，请检查网络。") from None

    async def transcribe(self, audio_base64: str, mime_type: str) -> dict:
        data, mime, extension = _decode_audio(audio_base64, mime_type)
        config, key = self._snapshot("asr")
        body = await self._request(
            "asr",
            config,
            key,
            "/audio/transcriptions",
            MAX_TRANSCRIPT_RESPONSE,
            ("application/json",),
            data={"model": config["asr_model"], "response_format": "json"},
            files={"file": ("recording." + extension, data, mime)},
        )
        try:
            result = json.loads(body)
            if not isinstance(result, dict) or result.get("error"):
                raise ValueError
            text = result["text"]
            if not isinstance(text, str) or any(
                (ord(char) < 32 and char not in "\n\r\t")
                or ord(char) == 127
                or 0xD800 <= ord(char) <= 0xDFFF
                for char in text
            ):
                raise ValueError
            if len(text) > MAX_TRANSCRIPT_CHARS:
                raise VoiceError("asr_output_limit", "识别文字过长，请缩短录音后重试。")
            if not text.strip():
                raise VoiceError("asr_empty_text", "没有识别到语音，请重新录音。")
            return {"text": text.strip()}
        except VoiceError:
            raise
        except (ValueError, KeyError, TypeError, UnicodeError):
            raise VoiceError("asr_invalid_response", "语音识别返回格式不正确。") from None

    async def synthesize(self, text: str) -> dict:
        if (
            not isinstance(text, str)
            or not text.strip()
            or any(
                (ord(char) < 32 and char not in "\n\r\t")
                or ord(char) == 127
                or 0xD800 <= ord(char) <= 0xDFFF
                for char in text
            )
        ):
            raise VoiceError("invalid_speech_text", "没有可朗读的文字。")
        if len(text) > MAX_SPEECH_CHARS:
            raise VoiceError("speech_text_too_long", "朗读文字过长，请按句发送。")
        config, key = self._snapshot("tts")
        body = await self._request(
            "tts",
            config,
            key,
            "/audio/speech",
            MAX_AUDIO_BYTES,
            ("audio/mpeg", "audio/mp3", "application/octet-stream"),
            json={
                "model": config["tts_model"],
                "voice": config["tts_voice"],
                "input": text,
                "response_format": "mp3",
            },
        )
        if not _looks_like_audio(body, "mp3"):
            raise VoiceError("tts_invalid_response", "语音服务未返回有效的 MP3 音频。")
        return {"audio_base64": base64.b64encode(body).decode("ascii"), "mime_type": "audio/mpeg"}
