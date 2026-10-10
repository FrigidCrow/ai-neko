"""Optional registered Qwen TTS, isolated from the application interpreter.

Registration is an explicit local CLI action, never a web-supplied executable.
The worker is lazy, bounded, cancellable, and owns its CUDA context until reaped.
"""

from __future__ import annotations

import asyncio
import contextlib
import json
import math
import os
import sys
import sysconfig
import time
from pathlib import Path

from ai_neko.config.paths import DataPaths, safe_child
from ai_neko.media.local_voice import (
    LocalVoice,
    LocalVoiceError,
    _environment,
    _reap_spawn_result,
    _spawn_external,
)
from ai_neko.media.qwen_runtime.binding import load_binding, owned_root

MAX_AUDIO = 8 * 1024 * 1024
MAX_WIRE = 12 * 1024 * 1024
MAX_TEXT = 600
INFERENCE_TIMEOUT = 120
RUNTIME_SOURCE = Path(__file__).with_name("qwen_runtime")
TIMING_FIELDS = {
    "import_ms",
    "prepare_ms",
    "model_load_ms",
    "inference_ms",
    "encode_ms",
    "worker_total_ms",
}
RESOURCE_FIELDS = {
    "available_ram_bytes",
    "available_commit_bytes",
    "gpu_free_bytes",
    "cuda_allocated_bytes",
    "cuda_reserved_bytes",
    "cuda_peak_allocated_bytes",
    "cuda_peak_reserved_bytes",
}
ERROR_MESSAGES = {
    "memory_pressure": "可用内存或系统提交空间不足，Qwen 未继续加载；请释放资源后重试。",
    "gpu_unavailable": "Qwen 需要可用的 NVIDIA CUDA GPU；当前无法使用，不会转为云端。",
    "gpu_memory": "可用显存不足，Qwen 未加载；请释放显存后重试。",
    "resource_exhausted": "Qwen 的内存或显存不足，已卸载模型；请释放资源后重试。",
    "binding_invalid": "Qwen 登记资源已变化，请重新运行本地资源登记脚本。",
    "output_limit": "Qwen 生成音频超过限制，请缩短文本后重试。",
    "inference_failed": "Qwen 本地语音运行失败，模型已卸载，请重试。",
}


class QwenVoiceError(LocalVoiceError):
    """Safe, stable UI error; raw native errors never cross the process pipe."""


def _supported() -> bool:
    return sys.platform == "win32" and sysconfig.get_platform() == "win-amd64"


def _worker_environment(root: Path) -> dict[str, str]:
    env = _environment(root)
    env.update(
        {
            "HF_HOME": str(root / "cache" / "huggingface"),
            "HF_HUB_OFFLINE": "1",
            "TRANSFORMERS_OFFLINE": "1",
            "HF_HUB_DISABLE_TELEMETRY": "1",
            "TOKENIZERS_PARALLELISM": "false",
            "NUMBA_CACHE_DIR": str(root / "cache" / "numba"),
            "TORCH_HOME": str(root / "cache" / "torch"),
            "CUDA_CACHE_PATH": str(root / "cache" / "cuda"),
        }
    )
    return env


class QwenVoice:
    def __init__(self, paths: DataPaths):
        self.paths = paths
        self.root = paths.assets / "qwen"
        self._inference_lock = asyncio.Lock()
        self._inference_process: asyncio.subprocess.Process | None = None
        self._processes: set[asyncio.subprocess.Process] = set()
        self._requests: set[asyncio.Task] = set()
        self._idle_task: asyncio.Task | None = None
        self._shutdown_task: asyncio.Task | None = None
        self._sequence = 0
        self._worker_identity: str | None = None
        self._loaded = False
        self._closed = False
        self._unloading = False
        self._last_error = ""
        self._last_diagnostics: dict = {}

    def status(self) -> dict:
        supported = _supported()
        loaded = bool(
            self._loaded
            and self._inference_process is not None
            and self._inference_process.returncode is None
        )
        result = {
            "state": "missing",
            "message": "请运行本地 Qwen 资源登记脚本，复用已安装的 GPU 环境。",
            "supported": supported,
            "loaded": loaded,
            "gpu_required": True,
            "packaged_runtime": False,
            "engine": "qwen3-base-0.6b",
            "preflight": {
                "available_ram_mib": 1536,
                "available_commit_mib": 8192,
                "available_vram_mib": 3072,
            },
        }
        if not supported:
            result.update(state="error", message="Qwen 本地语音当前只支持 Windows x64 CUDA 环境。")
        elif self._closed:
            result.update(state="error", message="Qwen 语音服务已关闭。", loaded=False)
        else:
            try:
                binding = load_binding(self.paths)
            except (OSError, ValueError, RuntimeError):
                if self.root.exists():
                    result.update(state="error", message=ERROR_MESSAGES["binding_invalid"])
            else:
                result.update(
                    state="error" if self._last_error else "ready",
                    message=self._last_error
                    or (
                        "Qwen 模型已加载。" if loaded else "Qwen 已登记，首次朗读时加载 GPU 模型。"
                    ),
                    idle_unload_seconds=binding.get("idle_unload_seconds", 300),
                )
        return result

    def diagnostics(self) -> dict:
        """Bounded numeric timing/resource snapshot; never input, audio, or paths."""
        return dict(self._last_diagnostics)

    def _binding(self) -> dict:
        if self._closed or self._unloading:
            raise QwenVoiceError("qwen_closed", "Qwen 语音服务已关闭或正在卸载。")
        if not _supported():
            raise QwenVoiceError("qwen_unsupported", "Qwen 本地语音需要 Windows x64 CUDA 环境。")
        try:
            return load_binding(self.paths)
        except (OSError, ValueError, RuntimeError):
            raise QwenVoiceError("qwen_not_ready", ERROR_MESSAGES["binding_invalid"]) from None

    def _cancel_idle(self) -> None:
        task, self._idle_task = self._idle_task, None
        if task is not None and task is not asyncio.current_task():
            task.cancel()

    def _schedule_idle(self, seconds: int) -> None:
        self._cancel_idle()
        if self._closed or self._unloading:
            return

        async def expire():
            try:
                await asyncio.sleep(seconds)
                async with self._inference_lock:
                    await self._discard_process()
            except asyncio.CancelledError:
                pass
            finally:
                if self._idle_task is asyncio.current_task():
                    self._idle_task = None

        self._idle_task = asyncio.create_task(expire(), name="qwen-idle-unload")

    async def _discard_process(self) -> None:
        process, self._inference_process = self._inference_process, None
        self._loaded, self._sequence, self._worker_identity = False, 0, None
        if process is not None:
            try:
                await _reap_spawn_result(process)
            finally:
                self._processes.discard(process)

    async def _start_process(self, binding: dict):
        root = owned_root(self.paths)
        for name in ("home", "temp", "cache"):
            safe_child(root, name).mkdir(exist_ok=True, mode=0o700)
        for name in ("huggingface", "numba", "torch", "cuda"):
            safe_child(root / "cache", name).mkdir(exist_ok=True, mode=0o700)
        spawn = asyncio.create_task(
            _spawn_external(
                binding["python"],
                "-I",
                "-B",
                str(RUNTIME_SOURCE / "worker.py"),
                cwd=str(root),
                env=_worker_environment(root),
                stdin=asyncio.subprocess.PIPE,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.DEVNULL,
                limit=MAX_WIRE + 1,
                creationflags=0x08000000 if sys.platform == "win32" else 0,
            )
        )
        try:
            process = await asyncio.shield(spawn)
        except asyncio.CancelledError:
            while not spawn.done():
                with contextlib.suppress(asyncio.CancelledError):
                    await asyncio.shield(spawn)
            if not spawn.cancelled() and spawn.exception() is None:
                await _reap_spawn_result(spawn.result())
            raise
        self._inference_process = process
        self._processes.add(process)
        return process

    async def _exchange(self, request: dict, binding: dict) -> dict:
        started = time.perf_counter()
        try:
            identity = json.dumps(binding, sort_keys=True)
            process = self._inference_process
            if process is not None and (
                process.returncode is not None or self._worker_identity != identity
            ):
                await self._discard_process()
                process = None
            reused = process is not None
            spawning = time.perf_counter()
            if process is None:
                process = await self._start_process(binding)
                self._worker_identity = identity
            spawn_ms = 0.0 if reused else (time.perf_counter() - spawning) * 1000
            self._sequence += 1
            payload = {
                **request,
                "parent": os.getpid(),
                "request_id": self._sequence,
                "model": binding["model"],
                "prompt": binding["prompt"],
                "site_packages": binding["site_packages"],
            }
            wire = json.dumps(payload, ensure_ascii=False).encode("utf-8") + b"\n"
            if len(wire) > MAX_WIRE:
                raise ValueError("request size")
            process.stdin.write(wire)
            await process.stdin.drain()
            wire = await process.stdout.readuntil(b"\n")
            if len(wire) > MAX_WIRE:
                raise ValueError("response size")
            result = json.loads(wire)
            if (
                not isinstance(result, dict)
                or type(result.get("pid")) is not int
                or result["pid"] != process.pid
                or type(result.get("request_id")) is not int
                or result["request_id"] != self._sequence
            ):
                raise ValueError("response identity")
            if result.get("ok") is not True:
                code = result.get("code")
                if not isinstance(code, str) or code not in ERROR_MESSAGES:
                    code = "inference_failed"
                raise QwenVoiceError("qwen_" + code, ERROR_MESSAGES[code])
            timing, resources = result.get("timings"), result.get("resources")
            if (
                not isinstance(timing, dict)
                or set(timing) != TIMING_FIELDS | {"model_cold"}
                or type(timing.get("model_cold")) is not bool
                or not self._numeric(timing, TIMING_FIELDS, 600000)
                or not isinstance(resources, dict)
                or set(resources) != RESOURCE_FIELDS
                or not self._numeric(resources, RESOURCE_FIELDS, 2**60)
            ):
                raise ValueError("diagnostic protocol")
            if request["operation"] == "tts":
                self._audio(result)
            elif result.get("loaded") is not True:
                raise ValueError("load response")
            self._loaded = True
            self._last_diagnostics = {
                **timing,
                **resources,
                "worker_reused": reused,
                "worker_pid": process.pid,
                "spawn_ms": spawn_ms,
                "roundtrip_ms": (time.perf_counter() - started) * 1000,
            }
            return result
        except BaseException as error:
            await self._discard_process()
            if isinstance(error, asyncio.CancelledError):
                raise
            if isinstance(error, QwenVoiceError):
                raise
            if not isinstance(error, Exception):
                raise
            raise QwenVoiceError(
                "qwen_inference_failed", ERROR_MESSAGES["inference_failed"]
            ) from None

    @staticmethod
    def _numeric(value: dict, names: set, maximum: int) -> bool:
        return all(
            type(value[name]) in (int, float)
            and math.isfinite(value[name])
            and 0 <= value[name] <= maximum
            for name in names
        )

    @staticmethod
    def _audio(value: dict) -> bytes:
        try:
            return LocalVoice._synthesized_audio(value)
        except LocalVoiceError:
            raise QwenVoiceError("qwen_response", "Qwen 返回的音频无效，模型已卸载。") from None

    async def _infer(self, request: dict) -> dict:
        if self._closed or self._unloading:
            raise QwenVoiceError("qwen_closed", "Qwen 语音服务已关闭或正在卸载。")
        task = asyncio.current_task()
        self._requests.add(task)
        started = time.perf_counter()
        try:
            # Queue time, interpreter spawn, imports, model loading and generation
            # share one deadline. Cancellation before acquiring this lock never
            # owns (and must never kill) another request's active CUDA worker.
            async with asyncio.timeout(INFERENCE_TIMEOUT):
                async with self._inference_lock:
                    binding = self._binding()
                    self._cancel_idle()
                    queued_ms = (time.perf_counter() - started) * 1000
                    result = await self._exchange(request, binding)
                    self._last_error = ""
                    self._last_diagnostics["queue_ms"] = queued_ms
                    self._schedule_idle(binding.get("idle_unload_seconds", 300))
                    return result
        except TimeoutError:
            self._last_error = "Qwen 语音处理超过 120 秒，已停止本次请求；请缩短文本后重试。"
            raise QwenVoiceError("qwen_timeout", self._last_error) from None
        except QwenVoiceError as error:
            self._last_error = error.message
            raise
        finally:
            self._requests.discard(task)

    async def synthesize(self, text: str) -> bytes:
        if not isinstance(text, str) or not 0 < len(text.strip()) <= MAX_TEXT:
            raise QwenVoiceError("speech_too_long", "Qwen 每次支持 1 到 600 字。")
        return self._audio(await self._infer({"operation": "tts", "text": text.strip()}))

    async def preload(self) -> dict:
        await self._infer({"operation": "preload"})
        return self.status()

    async def unload(self) -> None:
        """Cancel owned requests and wait until the native CUDA process is gone."""
        if self._shutdown_task is None or self._shutdown_task.done():
            self._unloading = True
            self._cancel_idle()

            async def finish():
                try:
                    tasks = tuple(self._requests)
                    for task in tasks:
                        task.cancel()
                    if tasks:
                        await asyncio.gather(*tasks, return_exceptions=True)
                    async with self._inference_lock:
                        await self._discard_process()
                    self._last_error = ""
                finally:
                    self._unloading = False

            self._shutdown_task = asyncio.create_task(finish(), name="qwen-unload")
        cancelled = False
        while not self._shutdown_task.done():
            try:
                await asyncio.shield(self._shutdown_task)
            except asyncio.CancelledError:
                cancelled = True
        self._shutdown_task.result()
        if cancelled:
            raise asyncio.CancelledError

    async def close(self) -> None:
        self._closed = True
        await self.unload()
