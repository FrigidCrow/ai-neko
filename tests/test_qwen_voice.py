"""Synthetic Qwen lifecycle regressions; never import torch or load a GPU model."""

from __future__ import annotations

import asyncio
import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

from ai_neko.config.paths import initialize_data_root
from ai_neko.media import qwen_voice
from ai_neko.media.qwen_runtime.binding import owned_root
from ai_neko.media.qwen_voice import QwenVoice, QwenVoiceError

CHILD = """
import base64,io,json,os,sys,time,wave
data=io.BytesIO()
with wave.open(data,'wb') as wav:
    wav.setnchannels(1);wav.setsampwidth(2);wav.setframerate(24000);wav.writeframes(b'\\0\\0'*240)
audio=base64.b64encode(data.getvalue()).decode('ascii')
for line in sys.stdin:
    request=json.loads(line)
    text=request.get('text','')
    if text=='hang': time.sleep(60)
    if text=='crash': os._exit(3)
    if text=='oversized':
        sys.stdout.write('x'*20000);sys.stdout.flush();time.sleep(60)
    if text=='malformed':
        print('synthetic invalid JSON',flush=True);continue
    timing={name:0.1 for name in ('import_ms','prepare_ms','model_load_ms',
        'inference_ms','encode_ms','worker_total_ms')}
    timing['model_cold']=request['request_id']==1
    resources={name:100 for name in ('available_ram_bytes','available_commit_bytes','gpu_free_bytes',
        'cuda_allocated_bytes','cuda_reserved_bytes','cuda_peak_allocated_bytes','cuda_peak_reserved_bytes')}
    if text=='bad_timing':timing['encode_ms']='sensitive diagnostic'
    if text=='bad_resources':resources['gpu_free_bytes']=float('nan')
    reply={'ok':True,'pid':os.getpid(),'request_id':request['request_id'],
        'timings':timing,'resources':resources,'audio':audio,'loaded':True}
    if text=='bad_audio':reply['audio']='broken wave'
    if text=='missing_audio':del reply['audio']
    if text=='bad_pid':reply['pid']=0
    if text=='bad_sequence':reply['request_id']+=1
    if text=='false_ok':reply.update(ok=False,code='sensitive raw traceback')
    if text=='oom':reply.update(ok=False,code='resource_exhausted')
    print(json.dumps(reply),flush=True)
"""


def prepared(tmp_path, monkeypatch, *, idle=300):
    voice = QwenVoice(initialize_data_root(tmp_path / "data"))
    root = owned_root(voice.paths, create=True)
    runtime = tmp_path / "synthetic-runtime"
    runtime.mkdir()
    (runtime / "worker.py").write_text(CHILD, encoding="utf-8")
    binding = {
        "python": str(Path(sys._base_executable).resolve()),
        "site_packages": str(root),
        "model": str(root),
        "prompt": str(root / "p.pt"),
        "idle_unload_seconds": idle,
    }
    monkeypatch.setattr(qwen_voice, "RUNTIME_SOURCE", runtime)
    monkeypatch.setattr(qwen_voice, "load_binding", lambda paths: dict(binding))
    monkeypatch.setattr(qwen_voice, "_supported", lambda: True)
    return voice


async def wait_child(voice):
    async with asyncio.timeout(5):
        while voice._inference_process is None:
            await asyncio.sleep(0.005)
    return voice._inference_process


def test_registration_status_is_lazy_private_and_no_torch_import(tmp_path, monkeypatch):
    voice = prepared(tmp_path, monkeypatch)
    status = voice.status()
    assert status["state"] == "ready" and status["supported"] and not status["loaded"]
    assert status["gpu_required"] and not status["packaged_runtime"]
    assert str(tmp_path) not in json.dumps(status) and voice._inference_process is None
    assert "torch" not in sys.modules


def test_missing_unsupported_and_closed_status(tmp_path, monkeypatch):
    monkeypatch.setattr(qwen_voice, "_supported", lambda: True)
    voice = QwenVoice(initialize_data_root(tmp_path / "data"))
    assert voice.status()["state"] == "missing"
    monkeypatch.setattr(qwen_voice, "_supported", lambda: False)
    assert voice.status()["state"] == "error" and not voice.status()["supported"]
    monkeypatch.setattr(qwen_voice, "_supported", lambda: True)
    asyncio.run(voice.close())
    assert voice.status()["state"] == "error" and not voice.status()["loaded"]


def test_preload_reuses_child_prompt_then_unload_reaps_and_allows_reload(tmp_path, monkeypatch):
    async def run():
        voice = prepared(tmp_path, monkeypatch)
        try:
            assert (await voice.preload())["loaded"]
            first = voice.diagnostics()
            child = voice._inference_process
            audio = await voice.synthesize("synthetic private text")
            second = voice.diagnostics()
            assert audio.startswith(b"RIFF")
            assert first["model_cold"] and not first["worker_reused"]
            assert not second["model_cold"] and second["worker_reused"]
            assert first["worker_pid"] == second["worker_pid"]
            assert all(type(value) in (bool, int, float) for value in second.values())
            assert "synthetic" not in json.dumps(second)
            second.clear()
            assert voice.diagnostics()
            await voice.unload()
            assert child.returncode is not None and not voice._processes
            assert not voice.status()["loaded"]
            await voice.synthesize("again")
            assert voice._inference_process.pid != child.pid
        finally:
            await voice.close()
        with pytest.raises(QwenVoiceError, match="已关闭"):
            await voice.preload()

    asyncio.run(run())


@pytest.mark.parametrize("ending", ["cancel", "timeout", "unload", "close"])
def test_active_request_killed_and_reaped_before_interruption_returns(
    tmp_path, monkeypatch, ending
):
    async def run():
        voice = prepared(tmp_path, monkeypatch)
        if ending == "timeout":
            monkeypatch.setattr(qwen_voice, "INFERENCE_TIMEOUT", 0.2)
        task = asyncio.create_task(voice.synthesize("hang"))
        child = await wait_child(voice)
        if ending == "cancel":
            task.cancel()
        elif ending == "close":
            await voice.close()
        elif ending == "unload":
            await voice.unload()
        result = (await asyncio.gather(task, return_exceptions=True))[0]
        assert isinstance(result, QwenVoiceError if ending == "timeout" else asyncio.CancelledError)
        assert child.returncode is not None and voice._inference_process is None
        assert not voice._processes
        if ending != "close":
            monkeypatch.setattr(qwen_voice, "INFERENCE_TIMEOUT", 5)
            await voice.synthesize("recovered")
            assert voice._inference_process.pid != child.pid
        await voice.close()

    asyncio.run(run())


@pytest.mark.parametrize("ending", ["cancel", "timeout"])
def test_queued_cancel_or_timeout_does_not_touch_active_worker(tmp_path, monkeypatch, ending):
    async def run():
        voice = prepared(tmp_path, monkeypatch)
        active = asyncio.create_task(voice.synthesize("hang"))
        child = await wait_child(voice)
        if ending == "timeout":
            monkeypatch.setattr(qwen_voice, "INFERENCE_TIMEOUT", 0.05)
        waiter = asyncio.create_task(voice.synthesize("queued"))
        await asyncio.sleep(0)
        if ending == "cancel":
            waiter.cancel()
        result = (await asyncio.gather(waiter, return_exceptions=True))[0]
        assert isinstance(result, QwenVoiceError if ending == "timeout" else asyncio.CancelledError)
        assert voice._inference_process is child and child.returncode is None and not active.done()
        await voice.close()
        assert active.done() and child.returncode is not None

    asyncio.run(run())


@pytest.mark.parametrize(
    "text",
    [
        "crash",
        "oversized",
        "malformed",
        "bad_pid",
        "bad_sequence",
        "bad_timing",
        "bad_resources",
        "false_ok",
        "bad_audio",
        "missing_audio",
        "oom",
    ],
)
def test_bad_protocol_or_oom_reaps_worker_without_replay_and_can_recover(
    tmp_path, monkeypatch, text
):
    async def run():
        voice = prepared(tmp_path, monkeypatch)
        monkeypatch.setattr(qwen_voice, "MAX_WIRE", 4096)
        try:
            with pytest.raises(QwenVoiceError) as error:
                await voice.synthesize(text)
            assert "sensitive" not in str(error.value)
            assert not voice._processes and voice._inference_process is None
            assert voice.diagnostics() == {}
            if text == "oom":
                assert error.value.code == "qwen_resource_exhausted"
            await voice.synthesize("recovered")
            assert not voice.diagnostics()["worker_reused"]
        finally:
            await voice.close()

    asyncio.run(run())


def test_idle_timer_releases_gpu_worker_and_next_request_cold_starts(tmp_path, monkeypatch):
    async def run():
        voice = prepared(tmp_path, monkeypatch, idle=0.05)
        try:
            await voice.preload()
            child = voice._inference_process
            async with asyncio.timeout(5):
                while child.returncode is None:
                    await asyncio.sleep(0.01)
            assert not voice.status()["loaded"]
            await voice.synthesize("new request")
            assert voice._inference_process.pid != child.pid
        finally:
            await voice.close()

    asyncio.run(run())


def test_repeated_cancel_during_spawn_does_not_orphan_native_worker(tmp_path, monkeypatch):
    async def run():
        voice = prepared(tmp_path, monkeypatch)
        original = qwen_voice._spawn_external
        created, release = asyncio.Event(), asyncio.Event()
        children = []

        async def delayed(*args, **kwargs):
            child = await original(*args, **kwargs)
            children.append(child)
            created.set()
            await release.wait()
            return child

        monkeypatch.setattr(qwen_voice, "_spawn_external", delayed)
        task = asyncio.create_task(voice.synthesize("first"))
        await created.wait()
        task.cancel()
        await asyncio.sleep(0)
        task.cancel()
        release.set()
        await asyncio.gather(task, return_exceptions=True)
        assert children[0].returncode is not None and not voice._processes
        await voice.close()

    asyncio.run(run())


def test_invalidated_binding_does_not_disable_idle_gpu_cleanup(tmp_path, monkeypatch):
    async def run():
        voice = prepared(tmp_path, monkeypatch, idle=0.05)
        await voice.preload()
        child = voice._inference_process

        def broken(paths):
            raise ValueError("synthetic invalid binding")

        monkeypatch.setattr(qwen_voice, "load_binding", broken)
        with pytest.raises(QwenVoiceError):
            await voice.synthesize("next")
        await asyncio.wait_for(asyncio.shield(voice._idle_task), 5)
        assert child.returncode is not None and not voice._processes
        await voice.close()

    asyncio.run(run())


def test_concurrent_unload_and_close_share_cleanup_even_if_unload_caller_cancelled(
    tmp_path, monkeypatch
):
    async def run():
        voice = prepared(tmp_path, monkeypatch)
        active = asyncio.create_task(voice.synthesize("hang"))
        child = await wait_child(voice)
        unloading = asyncio.create_task(voice.unload())
        await asyncio.sleep(0)
        closing = asyncio.create_task(voice.close())
        unloading.cancel()
        result = await asyncio.gather(unloading, closing, return_exceptions=True)
        assert isinstance(result[0], asyncio.CancelledError) and result[1] is None
        assert child.returncode is not None and active.done() and not voice._processes
        assert voice._closed
        with pytest.raises(QwenVoiceError):
            await voice.synthesize("no restart")

    asyncio.run(run())


def test_worker_stdout_failure_during_unload_still_reaps_and_closes_owner_pipe(
    tmp_path, monkeypatch
):
    async def run():
        voice = prepared(tmp_path, monkeypatch)
        await voice.preload()
        child = voice._inference_process
        child.stdout.set_exception(ConnectionResetError("synthetic private pipe failure"))
        await voice.unload()
        assert child.returncode is not None and child.stdin.is_closing()
        assert not voice._processes and voice._inference_process is None
        await voice.close()

    asyncio.run(run())


@pytest.mark.parametrize("text", ["", "  ", "x" * 601, None, 12])
def test_invalid_text_rejected_without_spawn_or_binding(tmp_path, text):
    voice = QwenVoice(initialize_data_root(tmp_path / "data"))
    with pytest.raises(QwenVoiceError):
        asyncio.run(voice.synthesize(text))
    assert not voice.root.exists() and not voice._processes


def test_worker_environment_drops_keys_pythonpath_and_enforces_offline(tmp_path, monkeypatch):
    monkeypatch.setenv("OPENAI_API_KEY", "synthetic-key")
    monkeypatch.setenv("PYTHONPATH", "synthetic-sensitive-path")
    env = qwen_voice._worker_environment(tmp_path)
    assert "OPENAI_API_KEY" not in env and "PYTHONPATH" not in env
    assert env["HF_HUB_OFFLINE"] == env["TRANSFORMERS_OFFLINE"] == "1"
    assert str(tmp_path) in env["HF_HOME"]


def test_actual_qwen_main_protocol_and_owner_eof_with_no_native_imports():
    worker = qwen_voice.RUNTIME_SOURCE / "worker.py"
    script = "\n".join(
        [
            "import runpy,sys",
            f"namespace=runpy.run_path({str(worker)!r})",
            "namespace['main'].__globals__['run']=lambda request: {'ok':True}",
            "namespace['main']()",
        ]
    )
    child = subprocess.Popen(
        [str(Path(sys._base_executable).resolve()), "-I", "-B", "-c", script],
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.DEVNULL,
    )
    try:
        for sequence in (1, 2):
            request = {"operation": "preload", "parent": os.getpid(), "request_id": sequence}
            child.stdin.write(json.dumps(request).encode() + b"\n")
            child.stdin.flush()
            assert json.loads(child.stdout.readline()) == {
                "ok": True,
                "pid": child.pid,
                "request_id": sequence,
            }
        child.stdin.close()
        assert child.wait(timeout=5) in (0, 91)
    finally:
        if child.poll() is None:
            child.kill()
        child.wait(timeout=5)
        if not child.stdin.closed:
            child.stdin.close()
        child.stdout.close()


def test_actual_qwen_parent_death_guard_releases_worker_output_handle():
    async def run():
        worker = str(qwen_voice.RUNTIME_SOURCE / "worker.py")
        child_script = (
            "import runpy,os,time;"
            f"m=runpy.run_path({worker!r});"
            "m['guard_parent'](os.getppid(),persistent=True);"
            "os.write(1,b'guard-ready\\n');time.sleep(60)"
        )
        owner_script = (
            "import subprocess,sys,time;"
            f"p=subprocess.Popen([sys.executable,'-I','-B','-c',{child_script!r}],"
            "stdin=subprocess.PIPE,stdout=sys.stdout,stderr=subprocess.DEVNULL);time.sleep(60)"
        )
        owner = await asyncio.create_subprocess_exec(
            str(Path(sys._base_executable).resolve()),
            "-I",
            "-B",
            "-c",
            owner_script,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.DEVNULL,
        )
        try:
            assert await asyncio.wait_for(owner.stdout.readline(), 5) == b"guard-ready\n"
            owner.kill()
            assert await asyncio.wait_for(owner.stdout.read(), 5) == b""
            await asyncio.wait_for(owner.wait(), 5)
        finally:
            if owner.returncode is None:
                owner.kill()
            await owner.wait()

    asyncio.run(run())
