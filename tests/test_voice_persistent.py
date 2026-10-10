"""Reusable voice child boundaries; synthetic protocol, no native model downloads."""

from __future__ import annotations

import asyncio
import io
import json
import os
import subprocess
import sys
import wave
from pathlib import Path
from types import SimpleNamespace

import pytest

from ai_neko.config.paths import initialize_data_root
from ai_neko.media import local_voice
from ai_neko.media.local_voice import LocalVoice, LocalVoiceError

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
    if text=='bad_timing':timing['encode_ms']='sensitive diagnostic'
    reply={'ok':True,'pid':os.getpid(),'request_id':request['request_id'],
        'timings':timing,'text':'synthetic response','audio':audio}
    if text=='bad_audio':reply['audio']='broken wave'
    if text=='missing_audio':del reply['audio']
    if text=='empty_text':reply['text']=''
    if text=='bad_pid':reply['pid']=0
    if text=='bad_sequence':reply['request_id']+=1
    if text=='false_ok':reply['ok']=False
    print(json.dumps(reply),flush=True)
"""


def prepared(tmp_path, monkeypatch):
    voice = LocalVoice(initialize_data_root(tmp_path / "data"))
    root = voice._owned(create=True)
    for name in ("home", "temp", "models"):
        (root / name).mkdir()
    runtime = tmp_path / "synthetic-runtime"
    runtime.mkdir()
    (runtime / "worker.py").write_text(CHILD, encoding="utf-8")
    monkeypatch.setattr(local_voice, "RUNTIME_SOURCE", runtime)
    monkeypatch.setattr(voice, "_ready", lambda: True)
    monkeypatch.setattr(voice, "_python", lambda: Path(sys._base_executable).resolve())
    monkeypatch.setattr(voice, "_site_packages", lambda: root)
    return voice


async def wait_child(voice):
    async with asyncio.timeout(5):
        while voice._inference_process is None:
            await asyncio.sleep(0.005)
    return voice._inference_process


@pytest.mark.parametrize("pipe_error", [ConnectionResetError, BrokenPipeError, RuntimeError])
@pytest.mark.parametrize("cancel", [False, True])
def test_reaper_pipe_error_still_waits_and_closes_without_masking_cancel(pipe_error, cancel):
    async def run():
        waiting, release = asyncio.Event(), asyncio.Event()
        calls = []

        async def read(_limit):
            calls.append("read")
            raise pipe_error("synthetic broken pipe")

        async def wait():
            calls.append("wait")
            waiting.set()
            await release.wait()
            calls.append("reaped")

        process = SimpleNamespace(
            returncode=None,
            kill=lambda: calls.append("kill"),
            wait=wait,
            stdout=SimpleNamespace(
                read=read, _transport=SimpleNamespace(close=lambda: calls.append("pipe-closed"))
            ),
            stdin=SimpleNamespace(close=lambda: calls.append("stdin-closed")),
        )
        task = asyncio.create_task(local_voice._reap_spawn_result(process))
        await asyncio.wait_for(waiting.wait(), 1)
        if cancel:
            task.cancel()
            await asyncio.sleep(0)
            task.cancel()
            await asyncio.sleep(0)
        assert not task.done() and "stdin-closed" not in calls
        release.set()
        if cancel:
            with pytest.raises(asyncio.CancelledError):
                await task
        elif pipe_error is RuntimeError:
            with pytest.raises(RuntimeError, match="synthetic broken pipe"):
                await task
        else:
            await task
        assert calls[-2:] == ["reaped", "stdin-closed"]
        if issubclass(pipe_error, OSError):
            assert "pipe-closed" in calls

    asyncio.run(run())


def test_reaper_kill_failure_still_attempts_wait_and_closes_stdin():
    async def run():
        calls = []

        def kill():
            calls.append("kill")
            raise OSError("synthetic kill error")

        async def wait():
            calls.append("wait")

        process = SimpleNamespace(
            returncode=None,
            kill=kill,
            wait=wait,
            stdout=None,
            stdin=SimpleNamespace(close=lambda: calls.append("stdin-closed")),
        )
        with pytest.raises(OSError, match="synthetic kill error"):
            await local_voice._reap_spawn_result(process)
        assert calls == ["kill", "stdin-closed", "wait", "stdin-closed"]

    asyncio.run(run())


def test_reuses_child_and_reports_only_bounded_metadata_then_close_reaps_idle(
    tmp_path, monkeypatch
):
    async def run():
        voice = prepared(tmp_path, monkeypatch)
        await voice._infer({"operation": "tts", "text": "synthetic one"})
        first = voice.diagnostics()
        child = voice._inference_process
        assert not first["worker_reused"] and first["model_cold"]
        await voice._infer({"operation": "asr", "text": "synthetic two"})
        second = voice.diagnostics()
        assert second["worker_pid"] == first["worker_pid"] and second["worker_reused"]
        assert "synthetic" not in json.dumps(second)
        assert "audio" not in second and "text" not in second
        second.clear()
        assert voice.diagnostics()["operation"] == "asr"
        await voice.close()
        assert child.returncode is not None and not voice._processes
        assert voice._inference_process is None

    asyncio.run(run())


@pytest.mark.parametrize("ending", ["cancel", "timeout", "close"])
def test_active_request_reaped_before_interruption_returns(tmp_path, monkeypatch, ending):
    async def run():
        voice = prepared(tmp_path, monkeypatch)
        if ending == "timeout":
            monkeypatch.setattr(local_voice, "INFERENCE_TIMEOUT", 0.15)
        task = asyncio.create_task(voice._infer({"operation": "tts", "text": "hang"}))
        child = await wait_child(voice)
        if ending == "cancel":
            task.cancel()
        elif ending == "close":
            await voice.close()
        result = (await asyncio.gather(task, return_exceptions=True))[0]
        assert isinstance(
            result, LocalVoiceError if ending == "timeout" else asyncio.CancelledError
        )
        assert child.returncode is not None and voice._inference_process is None
        assert not voice._processes
        if ending != "close":
            monkeypatch.setattr(local_voice, "INFERENCE_TIMEOUT", 5)
            await voice._infer({"operation": "tts", "text": "recovered"})
            assert voice.diagnostics()["worker_pid"] != child.pid
            assert not voice.diagnostics()["worker_reused"]
        await voice.close()

    asyncio.run(run())


def test_cancelled_waiter_does_not_kill_current_worker(tmp_path, monkeypatch):
    async def run():
        voice = prepared(tmp_path, monkeypatch)
        active = asyncio.create_task(voice._infer({"operation": "tts", "text": "hang"}))
        child = await wait_child(voice)
        waiter = asyncio.create_task(voice._infer({"operation": "tts", "text": "queued"}))
        await asyncio.sleep(0)
        waiter.cancel()
        await asyncio.gather(waiter, return_exceptions=True)
        assert voice._inference_process is child and child.returncode is None
        assert not active.done()
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
        "false_ok",
        "bad_audio",
        "missing_audio",
        "empty_text",
    ],
)
def test_bad_protocol_is_reaped_without_replay_and_next_request_recovers(
    tmp_path, monkeypatch, text
):
    async def run():
        voice = prepared(tmp_path, monkeypatch)
        monkeypatch.setattr(local_voice, "MAX_WIRE", 4096)
        with pytest.raises(LocalVoiceError) as error:
            await voice._infer(
                {"operation": "asr" if text == "empty_text" else "tts", "text": text}
            )
        assert error.value.code in {
            "local_voice_failed",
            "local_voice_response",
            "empty_transcript",
        }
        assert "sensitive" not in str(error.value)
        assert not voice._processes and voice._inference_process is None
        assert voice.diagnostics() == {}
        await voice._infer({"operation": "tts", "text": "recovered"})
        assert not voice.diagnostics()["worker_reused"]
        await voice.close()

    asyncio.run(run())


def test_idle_crash_restarts_on_next_explicit_request(tmp_path, monkeypatch):
    async def run():
        voice = prepared(tmp_path, monkeypatch)
        await voice._infer({"operation": "tts", "text": "first"})
        old = voice._inference_process
        old.kill()
        await old.wait()
        await voice._infer({"operation": "tts", "text": "second"})
        assert voice._inference_process.pid != old.pid
        assert len(voice._processes) == 1
        await voice.close()

    asyncio.run(run())


def test_repeated_cancel_during_spawn_still_reaps_unpublished_worker(tmp_path, monkeypatch):
    async def run():
        voice = prepared(tmp_path, monkeypatch)
        original = local_voice._spawn_external
        created, release = asyncio.Event(), asyncio.Event()
        children = []

        async def delayed(*args, **kwargs):
            child = await original(*args, **kwargs)
            children.append(child)
            created.set()
            await release.wait()
            return child

        monkeypatch.setattr(local_voice, "_spawn_external", delayed)
        task = asyncio.create_task(voice._infer({"operation": "tts", "text": "first"}))
        await created.wait()
        task.cancel()
        await asyncio.sleep(0)
        task.cancel()
        release.set()
        await asyncio.gather(task, return_exceptions=True)
        assert children[0].returncode is not None
        assert not voice._processes and voice._inference_process is None
        await voice.close()

    asyncio.run(run())


@pytest.mark.parametrize("speaker", [-1, 103, True, 46.0, "46", None])
def test_speaker_ids_rejected_before_worker_spawn(tmp_path, speaker):
    voice = LocalVoice(initialize_data_root(tmp_path / "data"))
    with pytest.raises(LocalVoiceError) as error:
        asyncio.run(voice.synthesize("synthetic", speaker_id=speaker))
    assert error.value.code == "invalid_local_speaker"
    assert not voice._processes and not voice.root.exists()


def test_real_persistent_main_accepts_second_request_and_owner_eof_without_native_imports():
    worker = local_voice.RUNTIME_SOURCE / "worker.py"
    script = "\n".join(
        [
            "import runpy,sys,time",
            f"namespace=runpy.run_path({str(worker)!r})",
            "namespace['main'].__globals__['run']=lambda request: {'ok':True}",
            "sys.argv=['worker.py','--persistent']",
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
            request = {"operation": "check", "parent": os.getpid(), "request_id": sequence}
            child.stdin.write(json.dumps(request).encode() + b"\n")
            child.stdin.flush()
            result = json.loads(child.stdout.readline())
            assert result == {"ok": True, "pid": child.pid, "request_id": sequence}
        child.stdin.close()
        assert child.wait(timeout=5) in (0, 91)
    finally:
        if child.poll() is None:
            child.kill()
        child.wait(timeout=5)
        if not child.stdin.closed:
            child.stdin.close()
        child.stdout.close()


def test_actual_worker_reuses_both_engines_but_creates_fresh_asr_streams_and_speaker_settings(
    tmp_path, monkeypatch
):
    """Real worker protocol with tiny substitute libraries, explicitly not model quality."""

    async def run():
        voice = LocalVoice(initialize_data_root(tmp_path / "data"))
        root = voice._owned(create=True)
        (root / "models").mkdir()
        site = (
            root
            / "venv"
            / ("Lib/site-packages" if os.name == "nt" else "lib/python3.11/site-packages")
        )
        site.mkdir(parents=True)
        (site / "numpy.py").write_text(
            """
import struct
class Samples:
    def __init__(self,value=1):self.value=value
    def __len__(self):return 16
    def astype(self,*args):return self
    def __truediv__(self,other):return self
    def __mul__(self,other):return self
    def tobytes(self):return struct.pack('<h',self.value)*16
def frombuffer(*args,**kwargs):return Samples()
def clip(value,*args):return value
class Finite:
    def all(self):return True
def isfinite(value):return Finite()
float32='float32'
""",
            encoding="utf-8",
        )
        (site / "sherpa_onnx.py").write_text(
            """
from types import SimpleNamespace
from numpy import Samples
loads={'asr':0,'tts':0}
class Config:
    def __init__(self,**kwargs):pass
    def validate(self):return True
OfflineTtsConfig=OfflineTtsModelConfig=OfflineTtsKokoroModelConfig=Config
GenerationConfig=Config
class OfflineTts:
    def __init__(self,config):loads['tts']+=1
    def generate(self,text,generation):
        return SimpleNamespace(sample_rate=24000,samples=Samples(loads['tts']*100+generation.sid))
class OfflineRecognizer:
    @classmethod
    def from_sense_voice(cls,**kwargs):
        loads['asr']+=1
        return cls()
    def __init__(self):self.streams=0
    def create_stream(self):
        self.streams+=1
        return SimpleNamespace(accept_waveform=lambda *args:None,result=SimpleNamespace(text=f"load={loads['asr']},stream={self.streams}"))
    def decode_stream(self,stream):pass
""",
            encoding="utf-8",
        )
        monkeypatch.setattr(voice, "_ready", lambda: True)
        monkeypatch.setattr(voice, "_python", lambda: Path(sys._base_executable).resolve())
        monkeypatch.setattr(voice, "_site_packages", lambda: site)
        try:
            audio1 = await voice.synthesize("synthetic", speaker_id=3)
            first = voice.diagnostics()
            audio2 = await voice.synthesize("synthetic", speaker_id=54)
            second = voice.diagnostics()
            assert first["model_cold"] and not second["model_cold"]
            assert first["worker_pid"] == second["worker_pid"]
            for audio, expected in ((audio1, 103), (audio2, 154)):
                with wave.open(io.BytesIO(audio), "rb") as wav:
                    assert int.from_bytes(wav.readframes(1), "little") == expected
            data = io.BytesIO()
            with wave.open(data, "wb") as wav:
                wav.setnchannels(1)
                wav.setsampwidth(2)
                wav.setframerate(16000)
                wav.writeframes(b"\0\0" * 16)
            assert await voice.transcribe(data.getvalue()) == "load=1,stream=1"
            assert voice.diagnostics()["model_cold"] and voice.diagnostics()["worker_reused"]
            assert await voice.transcribe(data.getvalue()) == "load=1,stream=2"
            assert not voice.diagnostics()["model_cold"]
        finally:
            await voice.close()

    asyncio.run(run())


def test_persistent_owner_death_closes_child_output_without_consuming_request_pipe():
    async def run():
        worker = str(local_voice.RUNTIME_SOURCE / "worker.py")
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
            # The grandchild inherits the stdout writer. EOF requires both the
            # killed owner and its voice worker to have released that handle.
            assert await asyncio.wait_for(owner.stdout.read(), 5) == b""
            await asyncio.wait_for(owner.wait(), 5)
        finally:
            if owner.returncode is None:
                owner.kill()
            await owner.wait()

    asyncio.run(run())


@pytest.mark.parametrize("cancel", [False, True])
def test_reaper_kill_failure_signals_owner_eof_before_waiting(cancel):
    """A real owned child can exit via EOF even when termination is denied."""

    async def run():
        process = await asyncio.create_subprocess_exec(
            sys._base_executable,
            "-I",
            "-B",
            "-c",
            "import sys,time;print('ready',flush=True);sys.stdin.buffer.read();time.sleep(0.05)",
            stdin=asyncio.subprocess.PIPE,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.DEVNULL,
            creationflags=0x08000000 if sys.platform == "win32" else 0,
        )
        original_kill = process.kill
        attempted = asyncio.Event()
        cleanup = None
        try:
            assert (await asyncio.wait_for(process.stdout.readline(), 5)).strip() == b"ready"

            def denied():
                attempted.set()
                raise PermissionError("synthetic termination denied")

            process.kill = denied
            cleanup = asyncio.create_task(local_voice._reap_spawn_result(process))
            await asyncio.wait_for(attempted.wait(), 5)
            if cancel:
                cleanup.cancel()
                await asyncio.sleep(0)
                cleanup.cancel()
            done, _ = await asyncio.wait([cleanup], timeout=5)
            assert done, "cleanup must signal owner EOF before waiting for child exit"
            with pytest.raises(asyncio.CancelledError if cancel else PermissionError):
                await cleanup
            assert process.stdin.is_closing()
            assert process.returncode == 0
        finally:
            # Only this test's owned child is touched, including on regression.
            process.kill = original_kill
            process.stdin.close()
            if process.returncode is None:
                original_kill()
            await process.wait()
            if cleanup is not None:
                await asyncio.gather(cleanup, return_exceptions=True)

    asyncio.run(run())
