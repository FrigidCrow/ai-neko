"""Standalone offline Qwen Base worker. No app, ASR, or VoiceDesign imports.

Only the explicitly registered Python executes this file. Audio/text travel in
bounded owner pipes and are never logged or persisted by this worker.
"""

from __future__ import annotations

import base64
import ctypes
import hashlib
import io
import json
import os
import runpy
import sys
import time
import wave
from pathlib import Path

MAX_WIRE = 12 * 1024 * 1024
MAX_AUDIO = 8 * 1024 * 1024
MAX_PROMPT = 4 * 1024 * 1024
MAX_NEW_TOKENS = 384
# Approved 24 kHz tokenizer decodes each codec frame into 1920 samples (12.5 Hz).
CODEC_FRAME_SAMPLES = 1920
CODEC_SAMPLE_RATE = 24000
PROMPT_SHA256 = "c39fe66d150c565e45adb36213bee8c417bbda054fde9d1ca87b871589cf8e5e"
MIN_RAM = 1536 * 1024**2
MIN_COMMIT = 8 * 1024**3
MIN_VRAM = 3 * 1024**3
_identity = None
_engine = None
_prompt = None
_torch = None
_np = None

# Reuse the app's existing owner HANDLE/pipe guards without importing ai_neko,
# an application environment, or any inference dependency into the host.
_shared = runpy.run_path(str(Path(__file__).parents[1] / "local_runtime" / "worker.py"))
guard_parent = _shared["guard_parent"]
deny_network = _shared["deny_network"]


class WorkerError(Exception):
    def __init__(self, code: str):
        self.code = code


class MemoryStatus(ctypes.Structure):
    _fields_ = [
        ("length", ctypes.c_ulong),
        ("load", ctypes.c_ulong),
        ("total_phys", ctypes.c_ulonglong),
        ("avail_phys", ctypes.c_ulonglong),
        ("total_page", ctypes.c_ulonglong),
        ("avail_page", ctypes.c_ulonglong),
        ("total_virtual", ctypes.c_ulonglong),
        ("avail_virtual", ctypes.c_ulonglong),
        ("avail_extended", ctypes.c_ulonglong),
    ]


def memory_snapshot() -> dict:
    if sys.platform != "win32":
        raise WorkerError("gpu_unavailable")
    info = MemoryStatus()
    info.length = ctypes.sizeof(info)
    kernel = ctypes.WinDLL("kernel32", use_last_error=True)
    kernel.GlobalMemoryStatusEx.argtypes = [ctypes.POINTER(MemoryStatus)]
    kernel.GlobalMemoryStatusEx.restype = ctypes.c_int
    if not kernel.GlobalMemoryStatusEx(ctypes.byref(info)):
        raise WorkerError("memory_pressure")
    return {"available_ram_bytes": info.avail_phys, "available_commit_bytes": info.avail_page}


def require_memory(*, cold: bool) -> dict:
    resources = memory_snapshot()
    if resources["available_ram_bytes"] < (MIN_RAM if cold else 256 * 1024**2) or resources[
        "available_commit_bytes"
    ] < (MIN_COMMIT if cold else 512 * 1024**2):
        raise WorkerError("memory_pressure")
    return resources


def resources() -> dict:
    return {
        **memory_snapshot(),
        "gpu_free_bytes": int(_torch.cuda.mem_get_info()[0]),
        "cuda_allocated_bytes": int(_torch.cuda.memory_allocated()),
        "cuda_reserved_bytes": int(_torch.cuda.memory_reserved()),
        "cuda_peak_allocated_bytes": int(_torch.cuda.max_memory_allocated()),
        "cuda_peak_reserved_bytes": int(_torch.cuda.max_memory_reserved()),
    }


def prepare(request: dict):
    """Validate fixed identity/config/prompt before importing native packages."""
    global _identity
    model, prompt, site = (Path(request[name]) for name in ("model", "prompt", "site_packages"))
    identity = (str(model), str(prompt), str(site))
    if _identity is not None:
        if identity != _identity:
            raise WorkerError("binding_invalid")
        return model, prompt
    if not all(path.is_absolute() for path in (model, prompt, site)):
        raise WorkerError("binding_invalid")
    if not model.is_dir() or not prompt.is_file() or not site.is_dir():
        raise WorkerError("binding_invalid")
    config_path = model / "config.json"
    if config_path.stat().st_size > 128 * 1024:
        raise WorkerError("binding_invalid")
    config = json.loads(config_path.read_text(encoding="utf-8"))
    if (
        config.get("tts_model_type") != "base"
        or config.get("tts_model_size") != "0b6"
        or config.get("tokenizer_type") != "qwen3_tts_tokenizer_12hz"
    ):
        raise WorkerError("binding_invalid")
    if not 0 < prompt.stat().st_size <= MAX_PROMPT:
        raise WorkerError("binding_invalid")
    if hashlib.sha256(prompt.read_bytes()).hexdigest() != PROMPT_SHA256:
        raise WorkerError("binding_invalid")
    sys.addaudithook(deny_network)
    # No site.addsitedir: untrusted .pth executable hooks are never processed.
    sys.path.insert(0, str(site))
    _identity = identity
    return model, prompt


def load_model(model: Path, prompt_path: Path):
    global _engine, _prompt, _torch, _np
    require_memory(cold=True)
    import numpy as np
    import torch
    from qwen_tts import Qwen3TTSModel, VoiceClonePromptItem

    imported = time.perf_counter()
    _torch, _np = torch, np
    if not torch.cuda.is_available():
        raise WorkerError("gpu_unavailable")
    if torch.cuda.mem_get_info()[0] < MIN_VRAM:
        raise WorkerError("gpu_memory")
    if not torch.cuda.is_bf16_supported():
        raise WorkerError("gpu_unavailable")
    torch.set_num_threads(4)
    torch.set_num_interop_threads(2)
    torch.cuda.set_per_process_memory_fraction(0.65)
    torch.cuda.reset_peak_memory_stats()
    # Native imports themselves consume substantial commit; never treat a
    # pre-import check as proof that loading the model is still affordable.
    require_memory(cold=True)
    loading = time.perf_counter()
    _engine = Qwen3TTSModel.from_pretrained(
        str(model),
        device_map="cuda:0",
        dtype=torch.bfloat16,
        attn_implementation="sdpa",
        low_cpu_mem_usage=True,
        local_files_only=True,
    )
    # This approved artifact contains tensor-only data dictionaries, not arbitrary
    # Python model objects. Digest-check above plus weights_only guards both uses.
    payload = torch.load(prompt_path, map_location="cpu", weights_only=True)
    if (
        not isinstance(payload, dict)
        or set(payload) != {"items"}
        or not isinstance(payload["items"], list)
        or len(payload["items"]) != 1
    ):
        raise WorkerError("binding_invalid")
    required = {"ref_code", "ref_spk_embedding", "x_vector_only_mode", "icl_mode", "ref_text"}
    item = payload["items"][0]
    if (
        not isinstance(item, dict)
        or set(item) != required
        or item["x_vector_only_mode"] is not False
        or item["icl_mode"] is not True
        or not isinstance(item["ref_text"], str)
        or not 0 < len(item["ref_text"]) <= 2000
    ):
        raise WorkerError("binding_invalid")
    for name in ("ref_code", "ref_spk_embedding"):
        tensor = item[name]
        if (
            not torch.is_tensor(tensor)
            or not 0 < tensor.numel() < 1_000_000
            or not torch.isfinite(tensor).all()
        ):
            raise WorkerError("binding_invalid")
    _prompt = [VoiceClonePromptItem(**item)]
    torch.cuda.synchronize()
    require_memory(cold=False)
    return imported, loading


def encode(wavs, sample_rate: int) -> bytes:
    if not isinstance(wavs, list) or len(wavs) != 1:
        raise WorkerError("inference_failed")
    if type(sample_rate) is not int or not 8000 <= sample_rate <= 48000:
        raise WorkerError("inference_failed")
    samples = _np.asarray(wavs[0], dtype=_np.float32)
    if samples.ndim != 1 or not 0 < samples.size * 2 <= MAX_AUDIO - 44:
        raise WorkerError("output_limit")
    # The fixed, auditioned generation budget is 384 codec frames. Treat
    # near-cap output as uncertain coverage instead of silently returning a cut.
    cap_samples = MAX_NEW_TOKENS * CODEC_FRAME_SAMPLES * sample_rate / CODEC_SAMPLE_RATE
    if samples.size >= int(cap_samples * 0.98):
        raise WorkerError("output_limit")
    if not _np.isfinite(samples).all():
        raise WorkerError("inference_failed")
    pcm = (_np.clip(samples, -1, 1) * 32767).astype("<i2").tobytes()
    output = io.BytesIO()
    with wave.open(output, "wb") as wav:
        wav.setnchannels(1)
        wav.setsampwidth(2)
        wav.setframerate(sample_rate)
        wav.writeframes(pcm)
    return output.getvalue()


def run(request: dict) -> dict:
    started = time.perf_counter()
    operation = request.get("operation")
    if operation not in {"tts", "preload"}:
        raise WorkerError("inference_failed")
    if operation == "tts":
        text = request.get("text")
        if not isinstance(text, str) or not 0 < len(text.strip()) <= 600:
            raise WorkerError("inference_failed")
    model, prompt = prepare(request)
    prepared = time.perf_counter()
    cold = _engine is None
    if cold:
        imported, loading = load_model(model, prompt)
    else:
        require_memory(cold=False)
        imported = loading = time.perf_counter()
    loaded = time.perf_counter()
    result = {"ok": True, "loaded": True}
    if operation == "tts":
        with _torch.inference_mode():
            wavs, sample_rate = _engine.generate_voice_clone(
                text=text.strip(),
                language="Chinese",
                voice_clone_prompt=_prompt,
                do_sample=True,
                top_k=50,
                top_p=1.0,
                temperature=0.9,
                repetition_penalty=1.05,
                max_new_tokens=MAX_NEW_TOKENS,
                non_streaming_mode=True,
            )
        _torch.cuda.synchronize()
        inferred = time.perf_counter()
        result["audio"] = base64.b64encode(encode(wavs, sample_rate)).decode("ascii")
    else:
        inferred = loaded
    result["resources"] = resources()
    result["timings"] = {
        "model_cold": cold,
        "prepare_ms": (prepared - started) * 1000,
        "import_ms": (imported - prepared) * 1000,
        "model_load_ms": (loaded - loading) * 1000,
        "inference_ms": (inferred - loaded) * 1000,
        "encode_ms": (time.perf_counter() - inferred) * 1000,
        "worker_total_ms": (time.perf_counter() - started) * 1000,
    }
    return result


def error_code(error: Exception) -> str:
    if isinstance(error, WorkerError):
        return error.code
    if isinstance(error, MemoryError):
        return "resource_exhausted"
    if isinstance(error, OSError) and getattr(error, "winerror", None) in {8, 14, 1455}:
        return "resource_exhausted"
    if _torch is not None and isinstance(error, _torch.OutOfMemoryError):
        return "resource_exhausted"
    return "inference_failed"


def main() -> None:
    output_fd = os.dup(sys.stdout.fileno())
    os.dup2(sys.stderr.fileno(), sys.stdout.fileno())
    close_guard = None
    sequence = 0
    owner = None
    try:
        with os.fdopen(output_fd, "wb", closefd=True) as output:
            while True:
                request = {}
                try:
                    wire = sys.stdin.buffer.readline(MAX_WIRE + 1)
                    if not wire:
                        break
                    if len(wire) > MAX_WIRE or not wire.endswith(b"\n"):
                        raise ValueError("request limit")
                    request = json.loads(wire)
                    if not isinstance(request, dict):
                        raise ValueError("request format")
                    if close_guard is None:
                        owner = request["parent"]
                        close_guard = guard_parent(owner, persistent=True)
                    elif request.get("parent") != owner:
                        raise ValueError("owner changed")
                    sequence += 1
                    if (
                        type(request.get("request_id")) is not int
                        or request["request_id"] != sequence
                    ):
                        raise ValueError("request sequence")
                    result = run(request)
                except Exception as error:
                    result = {"ok": False, "code": error_code(error)}
                result.update(pid=os.getpid(), request_id=sequence)
                wire = json.dumps(result, ensure_ascii=False).encode("utf-8")
                if len(wire) >= MAX_WIRE:
                    break
                output.write(wire + b"\n")
                output.flush()
                if result.get("ok") is not True:
                    break
                del request, result, wire
    finally:
        if close_guard is not None:
            close_guard()


if __name__ == "__main__":
    main()
