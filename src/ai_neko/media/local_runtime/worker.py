"""ai-neko standalone offline voice child. JSON pipe protocol; never stores audio.

Run with the private managed Python using -I -B. This module intentionally has
no ai_neko imports and is also the auditable user-side runtime source.
"""

from __future__ import annotations

import base64
import ctypes
import io
import json
import os
import signal
import sys
import threading
import wave
from pathlib import Path

MAX_WIRE = 12 * 1024 * 1024
MAX_AUDIO = 8 * 1024 * 1024
ASR_NAME = "sherpa-onnx-sense-voice-zh-en-ja-ko-yue-int8-2024-07-17"
TTS_NAME = "kokoro-int8-multi-lang-v1_1"


def guard_parent(parent: int) -> None:
    if type(parent) is not int or parent <= 0 or parent == os.getpid():
        raise ValueError("invalid owner")
    if sys.platform == "linux":
        # Kernel enforcement also works while a native extension holds the GIL.
        libc = ctypes.CDLL(None, use_errno=True)
        if libc.prctl(1, signal.SIGKILL, 0, 0, 0) != 0 or os.getppid() != parent:
            raise ValueError("owner unavailable")
    elif sys.platform == "win32":
        from ctypes import wintypes

        kernel = ctypes.WinDLL("kernel32", use_last_error=True)
        kernel.OpenProcess.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
        kernel.OpenProcess.restype = wintypes.HANDLE
        kernel.WaitForSingleObject.argtypes = [wintypes.HANDLE, wintypes.DWORD]
        kernel.WaitForSingleObject.restype = wintypes.DWORD
        handle = kernel.OpenProcess(0x00100000, False, parent)
        if not handle or kernel.WaitForSingleObject(handle, 0) != 0x102:
            raise ValueError("owner unavailable")

        def watch_handle():
            kernel.WaitForSingleObject(handle, 0xFFFFFFFF)
            os._exit(91)

        threading.Thread(target=watch_handle, daemon=True, name="voice-owner-handle").start()
    elif os.getppid() != parent:
        raise ValueError("owner unavailable")

    def watch_pipe():
        # Parent retains this writer until inference is finished. Its death or
        # cleanup closes the kernel pipe; no polling or PID reuse is involved.
        os.read(sys.stdin.fileno(), 1)
        os._exit(91)

    threading.Thread(target=watch_pipe, daemon=True, name="voice-owner-pipe").start()


def deny_network(event, args):
    if event in {"socket.__new__", "socket.connect", "socket.getaddrinfo", "urllib.Request"}:
        raise RuntimeError("offline voice cannot access network")


def prepare_native_paths(models: Path) -> tuple[Path, Path]:
    """Use wide-character Python chdir, then ASCII-only native resource paths.

    Windows dependencies such as eSpeak and kaldifst still use narrow fopen /
    ifstream. Passing a UTF-8 absolute user directory is unsafe under a legacy
    Windows code page. This change is confined to this short-lived worker.
    """
    if not models.is_absolute() or not models.is_dir():
        raise ValueError("model directory unavailable")
    os.chdir(models.resolve(strict=True))
    return Path(ASR_NAME), Path(TTS_NAME)


def run(request: dict) -> dict:
    sys.addaudithook(deny_network)
    # The parent runs the actual managed interpreter, not a Windows venv
    # launcher. Add precisely the app-owned locked wheels, never PYTHONPATH or
    # another project's environment, and do not execute arbitrary .pth files.
    models = Path(request["models"])
    suffix = "Lib/site-packages" if sys.platform == "win32" else "lib/python3.11/site-packages"
    expected = models.parent / "venv" / suffix
    packages = Path(request["site_packages"])
    if (
        not packages.is_absolute()
        or packages != expected
        or not packages.is_dir()
        or not packages.resolve().is_relative_to((models.parent / "venv").resolve())
    ):
        raise ValueError("private packages unavailable")
    sys.path.insert(0, str(packages))
    import numpy as np
    import sherpa_onnx

    # packages and models remain absolute for Python's Unicode-safe checks.
    # Every native path below is formed from these fixed ASCII model names.
    asr, tts = prepare_native_paths(models)
    operation = request.get("operation")
    if operation == "check":
        needed = json.loads(Path(__file__).with_name("model_files.json").read_text())
        if not all(
            (models / name).is_file() and (models / name).stat().st_size > 0 for name in needed
        ):
            raise ValueError("missing resources")
        return {"ok": True}
    if operation == "asr":
        raw = base64.b64decode(request["audio"], validate=True)
        if len(raw) > MAX_AUDIO:
            raise ValueError("audio limit")
        with wave.open(io.BytesIO(raw), "rb") as audio:
            if (
                audio.getnchannels() != 1
                or audio.getsampwidth() != 2
                or audio.getframerate() != 16000
                or not 0 < audio.getnframes() <= 960000
            ):
                raise ValueError("audio format")
            samples = (
                np.frombuffer(audio.readframes(audio.getnframes()), dtype="<i2").astype(np.float32)
                / 32768.0
            )
        recognizer = sherpa_onnx.OfflineRecognizer.from_sense_voice(
            model=str(asr / "model.int8.onnx"),
            tokens=str(asr / "tokens.txt"),
            num_threads=2,
            use_itn=True,
            debug=False,
            language="auto",
            provider="cpu",
        )
        stream = recognizer.create_stream()
        stream.accept_waveform(16000, samples)
        recognizer.decode_stream(stream)
        return {"ok": True, "text": stream.result.text[:8000]}
    if operation == "tts":
        text = request.get("text")
        if not isinstance(text, str) or not 0 < len(text) <= 600:
            raise ValueError("text limit")
        config = sherpa_onnx.OfflineTtsConfig(
            model=sherpa_onnx.OfflineTtsModelConfig(
                kokoro=sherpa_onnx.OfflineTtsKokoroModelConfig(
                    model=str(tts / "model.int8.onnx"),
                    voices=str(tts / "voices.bin"),
                    tokens=str(tts / "tokens.txt"),
                    data_dir=str(tts / "espeak-ng-data"),
                    dict_dir=str(tts / "dict"),
                    lang="zh",
                    lexicon=f"{tts / 'lexicon-us-en.txt'},{tts / 'lexicon-zh.txt'}",
                ),
                num_threads=2,
                debug=False,
                provider="cpu",
            ),
            max_num_sentences=1,
            rule_fsts=",".join(
                str(tts / name) for name in ("date-zh.fst", "number-zh.fst", "phone-zh.fst")
            ),
        )
        if not config.validate():
            raise ValueError("tts config")
        engine = sherpa_onnx.OfflineTts(config)
        generation = sherpa_onnx.GenerationConfig()
        generation.sid = 46  # Mandarin female voice in the pinned v1.1 model.
        generation.speed = 1.0
        audio = engine.generate(text, generation)
        if not 0 < len(audio.samples) * 2 <= MAX_AUDIO - 44:
            raise ValueError("output limit")
        if not np.isfinite(audio.samples).all():
            raise ValueError("nonfinite audio")
        pcm = (np.clip(audio.samples, -1, 1) * 32767).astype("<i2").tobytes()
        output = io.BytesIO()
        with wave.open(output, "wb") as result:
            result.setnchannels(1)
            result.setsampwidth(2)
            result.setframerate(audio.sample_rate)
            result.writeframes(pcm)
        return {"ok": True, "audio": base64.b64encode(output.getvalue()).decode("ascii")}
    raise ValueError("unsupported operation")


def main() -> None:
    # Preserve only the protocol descriptor. Native library diagnostics must
    # never leak text or audio into the parent output or persistent logs.
    output_fd = os.dup(sys.stdout.fileno())
    os.dup2(sys.stderr.fileno(), sys.stdout.fileno())
    try:
        wire = sys.stdin.buffer.readline(MAX_WIRE + 1)
        if len(wire) > MAX_WIRE or not wire.endswith(b"\n"):
            raise ValueError("request limit")
        request = json.loads(wire)
        if not isinstance(request, dict):
            raise ValueError("request format")
        guard_parent(request["parent"])
        result = run(request)
    except Exception:
        result = {"ok": False, "code": "inference_failed"}
    result["pid"] = os.getpid()
    wire = json.dumps(result, ensure_ascii=False).encode("utf-8")
    if len(wire) > MAX_WIRE:
        wire = b'{"ok":false,"code":"output_limit"}'
    with os.fdopen(output_fd, "wb", closefd=True) as output:
        output.write(wire)


if __name__ == "__main__":
    main()
