"""Qwen worker logic with tiny substitute packages; no real native imports."""

from __future__ import annotations

import builtins
import contextlib
import hashlib
import io
import json
import runpy
import struct
import sys
import wave
from types import SimpleNamespace

import pytest

from ai_neko.media.qwen_voice import RUNTIME_SOURCE


def worker():
    return runpy.run_path(str(RUNTIME_SOURCE / "worker.py"))["run"].__globals__


class Samples:
    ndim = 1

    def __init__(self, size=32, value=7):
        self.size, self.value = size, value

    def __mul__(self, other):
        return self

    def astype(self, dtype):
        return self

    def tobytes(self):
        return struct.pack("<h", self.value) * self.size


def mocked_libraries(monkeypatch):
    seen = {"loads": [], "prompts": [], "generations": [], "tensors": []}
    tensor = SimpleNamespace(numel=lambda: 48)
    cuda = SimpleNamespace(
        is_available=lambda: True,
        is_bf16_supported=lambda: True,
        mem_get_info=lambda: (6 * 1024**3, 8 * 1024**3),
        set_per_process_memory_fraction=lambda value: None,
        reset_peak_memory_stats=lambda: None,
        synchronize=lambda: None,
        memory_allocated=lambda: 1024,
        memory_reserved=lambda: 2048,
        max_memory_allocated=lambda: 4096,
        max_memory_reserved=lambda: 8192,
    )

    def prompt_load(path, **kwargs):
        seen["prompts"].append((path, kwargs))
        return {
            "items": [
                {
                    "ref_code": tensor,
                    "ref_spk_embedding": tensor,
                    "x_vector_only_mode": False,
                    "icl_mode": True,
                    "ref_text": "accepted synthetic reference",
                }
            ]
        }

    torch = SimpleNamespace(
        cuda=cuda,
        bfloat16="bfloat16",
        set_num_threads=lambda value: None,
        set_num_interop_threads=lambda value: None,
        load=prompt_load,
        is_tensor=lambda value: value is tensor,
        isfinite=lambda value: SimpleNamespace(all=lambda: True),
        inference_mode=contextlib.nullcontext,
        OutOfMemoryError=type("FakeOOM", (Exception,), {}),
    )
    numpy = SimpleNamespace(
        float32="float32",
        asarray=lambda value, dtype: value,
        isfinite=lambda value: SimpleNamespace(all=lambda: True),
        clip=lambda value, *args: value,
    )

    class Model:
        @classmethod
        def from_pretrained(cls, path, **kwargs):
            seen["loads"].append((path, kwargs))
            return cls()

        def generate_voice_clone(self, **kwargs):
            seen["generations"].append(kwargs)
            return [Samples()], 24000

    qwen = SimpleNamespace(Qwen3TTSModel=Model, VoiceClonePromptItem=lambda **kwargs: kwargs)
    monkeypatch.setitem(sys.modules, "torch", torch)
    monkeypatch.setitem(sys.modules, "numpy", numpy)
    monkeypatch.setitem(sys.modules, "qwen_tts", qwen)
    return seen, torch, numpy


def test_actual_run_loads_model_and_prompt_once_reuses_accepted_generation_parameters(
    tmp_path, monkeypatch
):
    runtime = worker()
    seen, _, _ = mocked_libraries(monkeypatch)
    monkeypatch.setitem(runtime, "prepare", lambda request: (tmp_path, tmp_path / "accepted.pt"))
    monkeypatch.setitem(
        runtime,
        "memory_snapshot",
        lambda: {
            "available_ram_bytes": 4 * 1024**3,
            "available_commit_bytes": 12 * 1024**3,
        },
    )
    first = runtime["run"]({"operation": "preload"})
    second = runtime["run"]({"operation": "tts", "text": " first synthetic "})
    third = runtime["run"]({"operation": "tts", "text": "second synthetic"})
    assert first["loaded"] and "audio" not in first
    assert first["timings"]["model_cold"] and not second["timings"]["model_cold"]
    assert not third["timings"]["model_cold"]
    assert len(seen["loads"]) == len(seen["prompts"]) == 1
    assert len(seen["generations"]) == 2
    assert seen["loads"][0][1] == {
        "device_map": "cuda:0",
        "dtype": "bfloat16",
        "attn_implementation": "sdpa",
        "low_cpu_mem_usage": True,
        "local_files_only": True,
    }
    assert seen["prompts"][0][1] == {"map_location": "cpu", "weights_only": True}
    one, two = seen["generations"]
    assert one["voice_clone_prompt"] is two["voice_clone_prompt"]
    assert one["text"] == "first synthetic" and two["text"] == "second synthetic"
    assert one["max_new_tokens"] == 384 and one["non_streaming_mode"] is True
    assert one["temperature"] == 0.9 and one["repetition_penalty"] == 1.05
    assert all(type(value) in (float, int, bool) for value in third["timings"].values())
    assert "synthetic" not in json.dumps(third["resources"])


@pytest.mark.parametrize("field", ["available_ram_bytes", "available_commit_bytes"])
def test_cold_preflight_failure_happens_before_any_native_import(tmp_path, monkeypatch, field):
    runtime = worker()
    resources = {"available_ram_bytes": 8 * 1024**3, "available_commit_bytes": 16 * 1024**3}
    resources[field] = 0
    monkeypatch.setitem(runtime, "memory_snapshot", lambda: resources)
    original_import = builtins.__import__
    seen = []

    def guarded(name, *args, **kwargs):
        if name in {"torch", "numpy", "qwen_tts"}:
            seen.append(name)
            raise AssertionError("native dependency imported before preflight")
        return original_import(name, *args, **kwargs)

    monkeypatch.setattr(builtins, "__import__", guarded)
    with pytest.raises(runtime["WorkerError"]) as error:
        runtime["load_model"](tmp_path, tmp_path / "prompt.pt")
    assert error.value.code == "memory_pressure" and not seen


@pytest.mark.parametrize(
    "fault,code",
    [
        ("no_cuda", "gpu_unavailable"),
        ("no_bf16", "gpu_unavailable"),
        ("vram", "gpu_memory"),
    ],
)
def test_gpu_preflight_never_falls_back_to_cpu(tmp_path, monkeypatch, fault, code):
    runtime = worker()
    seen, torch, _ = mocked_libraries(monkeypatch)
    monkeypatch.setitem(
        runtime,
        "memory_snapshot",
        lambda: {
            "available_ram_bytes": 4 * 1024**3,
            "available_commit_bytes": 12 * 1024**3,
        },
    )
    if fault == "no_cuda":
        torch.cuda.is_available = lambda: False
    elif fault == "no_bf16":
        torch.cuda.is_bf16_supported = lambda: False
    else:
        torch.cuda.mem_get_info = lambda: (1024**3, 8 * 1024**3)
    with pytest.raises(runtime["WorkerError"]) as error:
        runtime["load_model"](tmp_path, tmp_path / "prompt.pt")
    assert error.value.code == code and not seen["loads"]


def test_post_import_memory_check_prevents_loading_after_commit_drops(tmp_path, monkeypatch):
    runtime = worker()
    seen, _, _ = mocked_libraries(monkeypatch)
    snapshots = iter(
        [
            {"available_ram_bytes": 4 * 1024**3, "available_commit_bytes": 9 * 1024**3},
            {"available_ram_bytes": 3 * 1024**3, "available_commit_bytes": 6 * 1024**3},
        ]
    )
    monkeypatch.setitem(runtime, "memory_snapshot", lambda: next(snapshots))
    with pytest.raises(runtime["WorkerError"]) as error:
        runtime["load_model"](tmp_path, tmp_path / "prompt.pt")
    assert error.value.code == "memory_pressure" and not seen["loads"]


def test_prompt_hash_and_model_kind_checked_before_package_path_or_native_import(
    tmp_path, monkeypatch
):
    runtime = worker()
    model, site = tmp_path / "model", tmp_path / "site"
    model.mkdir()
    site.mkdir()
    prompt = tmp_path / "prompt.pt"
    prompt.write_bytes(b"synthetic approved prompt")
    config = {
        "tts_model_type": "base",
        "tts_model_size": "0b6",
        "tokenizer_type": "qwen3_tts_tokenizer_12hz",
    }
    (model / "config.json").write_text(json.dumps(config))
    request = {"model": str(model), "prompt": str(prompt), "site_packages": str(site)}
    original = list(sys.path)
    with pytest.raises(runtime["WorkerError"]) as error:
        runtime["prepare"](request)
    assert error.value.code == "binding_invalid" and sys.path == original
    monkeypatch.setitem(runtime, "PROMPT_SHA256", hashlib.sha256(prompt.read_bytes()).hexdigest())
    config["tts_model_type"] = "voice_design"
    (model / "config.json").write_text(json.dumps(config))
    with pytest.raises(runtime["WorkerError"]):
        runtime["prepare"](request)
    assert sys.path == original


@pytest.mark.parametrize("fault", ["items", "fields", "unsafe_load"])
def test_prompt_payload_rejects_unexpected_format(tmp_path, monkeypatch, fault):
    runtime = worker()
    _, torch, _ = mocked_libraries(monkeypatch)
    monkeypatch.setitem(
        runtime,
        "memory_snapshot",
        lambda: {
            "available_ram_bytes": 4 * 1024**3,
            "available_commit_bytes": 12 * 1024**3,
        },
    )
    if fault == "items":
        torch.load = lambda *args, **kwargs: {"items": []}
    elif fault == "fields":
        torch.load = lambda *args, **kwargs: {"items": [{"ref_code": object()}]}
    else:

        def safe_only(*args, **kwargs):
            assert kwargs["weights_only"] is True
            raise ValueError("synthetic untrusted pickle object")

        torch.load = safe_only
    with pytest.raises((runtime["WorkerError"], ValueError)):
        runtime["load_model"](tmp_path, tmp_path / "prompt.pt")
    assert runtime["_prompt"] is None


@pytest.mark.parametrize("frames", [383, 384])
def test_audio_encoding_rejects_near_token_cap_and_bounded_wav(tmp_path, monkeypatch, frames):
    runtime = worker()
    _, _, numpy = mocked_libraries(monkeypatch)
    monkeypatch.setitem(runtime, "_np", numpy)
    audio = runtime["encode"]([Samples()], 24000)
    with wave.open(io.BytesIO(audio), "rb") as wav:
        assert wav.getnchannels() == 1 and wav.getsampwidth() == 2 and wav.getframerate() == 24000
        assert wav.readframes(1) == b"\x07\x00"
    with pytest.raises(runtime["WorkerError"]) as error:
        runtime["encode"]([Samples(size=frames * 1920)], 24000)
    assert error.value.code == "output_limit"


def test_worker_error_codes_never_include_native_exception_text(monkeypatch):
    runtime = worker()
    _, torch, _ = mocked_libraries(monkeypatch)
    monkeypatch.setitem(runtime, "_torch", torch)
    assert runtime["error_code"](MemoryError("private text")) == "resource_exhausted"
    assert runtime["error_code"](torch.OutOfMemoryError("private text")) == "resource_exhausted"
    assert runtime["error_code"](RuntimeError("private traceback/path/text")) == "inference_failed"


def test_real_offline_audit_hook_denies_python_network_without_loading_native():
    runtime = worker()
    for event in ("socket.__new__", "socket.connect", "socket.getaddrinfo", "urllib.Request"):
        with pytest.raises(RuntimeError, match="offline"):
            runtime["deny_network"](event, ())


def test_identity_change_rejected_for_cached_engine(monkeypatch):
    runtime = worker()
    monkeypatch.setitem(runtime, "_identity", ("bound-model", "bound-prompt", "bound-site"))
    with pytest.raises(runtime["WorkerError"]) as error:
        runtime["prepare"](
            {"model": "changed", "prompt": "bound-prompt", "site_packages": "bound-site"}
        )
    assert error.value.code == "binding_invalid"
