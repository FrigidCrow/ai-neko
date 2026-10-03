"""Local voice setup/IPC boundaries; no model downloads in deterministic tests."""

from __future__ import annotations

import asyncio
import io
import json
import os
import subprocess
import sys
import tarfile
import wave
import zipfile
from pathlib import Path

import pytest

from ai_neko.config.paths import DataRootError, initialize_data_root
from ai_neko.media import local_voice
from ai_neko.media.local_voice import (
    LocalVoice,
    LocalVoiceError,
    _environment,
    _extract,
    validate_wav,
)


def wav(*, seconds=0.1, rate=16000, channels=1):
    data = io.BytesIO()
    with wave.open(data, "wb") as output:
        output.setnchannels(channels)
        output.setsampwidth(2)
        output.setframerate(rate)
        output.writeframes(b"\0\0" * int(rate * seconds) * channels)
    return data.getvalue()


def engine(tmp_path):
    return LocalVoice(initialize_data_root(tmp_path / "data"))


def test_status_is_read_only_and_closed_is_permanent(tmp_path):
    voice = engine(tmp_path)
    assert voice.status()["state"] == "missing"
    assert not voice.root.exists()
    asyncio.run(voice.close())
    with pytest.raises(LocalVoiceError, match="关闭"):
        voice.start_install()
    assert not voice.root.exists()


def test_environment_scrubs_credentials_indexes_and_python(monkeypatch, tmp_path):
    for key in (
        "UV_INDEX_URL",
        "PIP_INDEX_URL",
        "OPENAI_API_KEY",
        "PYTHONPATH",
        "PYTHONHOME",
        "VIRTUAL_ENV",
        "HTTP_PROXY",
        "UV_PYTHON_DOWNLOADS_JSON_URL",
        "UV_PROJECT_ENVIRONMENT",
    ):
        monkeypatch.setenv(key, "foreign-secret")
    values = _environment(tmp_path)
    assert "foreign-secret" not in values.values()
    assert values["UV_CACHE_DIR"] == str(tmp_path / "cache")
    assert values["HOME"] == str(tmp_path / "home")


@pytest.mark.parametrize(
    "data",
    [b"broken", wav(rate=8000), wav(channels=2), wav(seconds=60.1), wav()[:-4]],
    ids=["malformed", "wrong-sample-rate", "stereo", "over-60-seconds", "truncated"],
)
def test_wav_rejects_invalid_or_unbounded(data):
    with pytest.raises(LocalVoiceError):
        validate_wav(data)


def test_wav_accepts_frontend_pcm():
    validate_wav(wav())


def test_foreign_or_redirected_root_not_adopted(tmp_path):
    voice = engine(tmp_path)
    voice.root.mkdir()
    (voice.root / "foreign.txt").write_text("keep")
    with pytest.raises(DataRootError):
        voice._owned(create=True)
    assert (voice.root / "foreign.txt").read_text() == "keep"
    assert not (voice.root / ".owner.json").exists()


@pytest.mark.parametrize(
    "name,kind",
    [
        ("../escape", "file"),
        ("/escape", "file"),
        ("C:/escape", "file"),
        ("safe/link", "symlink"),
        ("safe/hard", "hardlink"),
        ("safe/dev", "device"),
    ],
)
def test_tar_refuses_traversal_links_and_special(tmp_path, name, kind):
    archive = tmp_path / "bad.tar"
    with tarfile.open(archive, "w") as bundle:
        info = tarfile.TarInfo(name)
        info.type = {
            "file": tarfile.REGTYPE,
            "symlink": tarfile.SYMTYPE,
            "hardlink": tarfile.LNKTYPE,
            "device": tarfile.CHRTYPE,
        }[kind]
        info.linkname = "foreign"
        bundle.addfile(info, io.BytesIO())
    destination = tmp_path / "out"
    destination.mkdir()
    with pytest.raises((DataRootError, ValueError)):
        asyncio.run(_extract(archive, destination))
    assert not (tmp_path / "escape").exists()


def test_zip_refuses_windows_traversal(tmp_path):
    archive = tmp_path / "bad.zip"
    with zipfile.ZipFile(archive, "w") as bundle:
        bundle.writestr("..\\escape", b"bad")
    destination = tmp_path / "out"
    destination.mkdir()
    with pytest.raises(DataRootError):
        asyncio.run(_extract(archive, destination))


def test_tar_safe_content_and_file_modes(tmp_path):
    archive = tmp_path / "ok.tar"
    with tarfile.open(archive, "w") as bundle:
        info = tarfile.TarInfo("nested/model")
        info.size = 4
        bundle.addfile(info, io.BytesIO(b"data"))
    destination = tmp_path / "out"
    destination.mkdir()
    asyncio.run(_extract(archive, destination))
    assert (destination / "nested/model").read_bytes() == b"data"


def test_install_is_single_task_and_cancel_awaits_cleanup(tmp_path, monkeypatch):
    async def run():
        voice = engine(tmp_path)
        started, cleanup = asyncio.Event(), asyncio.Event()

        async def setup():
            try:
                started.set()
                await asyncio.Event().wait()
            finally:
                await asyncio.sleep(0)
                cleanup.set()

        monkeypatch.setattr(voice, "_setup", setup)
        voice.start_install()
        task = voice._install
        voice.start_install()
        assert voice._install is task
        await started.wait()
        await voice.cancel_install()
        assert cleanup.is_set() and task.done()

    asyncio.run(run())


def test_setup_failure_never_marks_ready_or_exposes_error(tmp_path, monkeypatch):
    async def fail(*args):
        raise ValueError("sensitive-response-body")

    async def run():
        voice = engine(tmp_path)
        monkeypatch.setattr(voice, "_download", fail)
        voice.start_install()
        await voice._install
        status = voice.status()
        assert status["state"] == "error"
        assert "sensitive" not in json.dumps(status)
        assert not (voice.root / "ready.json").exists()

    asyncio.run(run())


@pytest.mark.parametrize("ending", ["cancel", "timeout", "close"])
def test_child_is_reaped_before_cancellation_timeout_or_close_returns(tmp_path, ending):
    async def run():
        voice = engine(tmp_path)
        voice._owned(create=True)
        for name in ("home", "temp"):
            (voice.root / name).mkdir()
        task = asyncio.create_task(
            voice._command(
                [sys.executable, "-I", "-c", "import time; time.sleep(60)"],
                timeout=0.05 if ending == "timeout" else 60,
            )
        )
        # _infer registers requests; direct command here manually models it.
        voice._requests.add(task)
        for _ in range(100):
            if voice._processes:
                break
            await asyncio.sleep(0.01)
        assert len(voice._processes) == 1
        child = next(iter(voice._processes))
        if ending == "cancel":
            task.cancel()
        elif ending == "close":
            await voice.close()
        result = await asyncio.gather(task, return_exceptions=True)
        assert child.returncode is not None
        assert not voice._processes
        if ending == "timeout":
            assert isinstance(result[0], LocalVoiceError)
            assert result[0].code == "local_voice_timeout"

    asyncio.run(run())


def test_oversized_stdout_kills_child_without_hanging(tmp_path, monkeypatch):
    async def run():
        voice = engine(tmp_path)
        voice._owned(create=True)
        monkeypatch.setattr(local_voice, "MAX_WIRE", 100)
        with pytest.raises(LocalVoiceError) as error:
            await voice._command(
                [
                    sys.executable,
                    "-I",
                    "-c",
                    "import sys,time; sys.stdout.write('x'*200);sys.stdout.flush();time.sleep(60)",
                ],
                timeout=5,
            )
        assert error.value.code == "local_voice_response"
        assert not voice._processes

    asyncio.run(run())


def test_worker_owner_pipe_ends_process_before_inference(tmp_path):
    # Import guard only: no NumPy/sherpa or real models required. This exercises
    # the exact worker guard with the parent pipe closing unexpectedly.
    worker = local_voice.RUNTIME_SOURCE / "worker.py"
    script = (
        "import runpy,os,time; m=runpy.run_path(" + repr(str(worker)) + ");"
        f"m['guard_parent']({os.getpid()});os.write(1,b'guard-ready\\n');time.sleep(60)"
    )
    child = subprocess.Popen(
        [str(Path(sys._base_executable).resolve()), "-I", "-c", script],
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.DEVNULL,
    )
    try:
        assert child.stdout.readline() == b"guard-ready\n"
        child.stdin.close()
        assert child.wait(timeout=5) == 91
    finally:
        if child.poll() is None:
            child.kill()
        child.wait(timeout=5)
        child.stdout.close()


def test_worker_does_not_import_native_libraries_in_main_app():
    source = Path(local_voice.__file__).read_text()
    assert "import sherpa_onnx" not in source and "import numpy" not in source
    lock = (local_voice.RUNTIME_SOURCE / "uv.lock").read_text()
    assert 'name = "sherpa-onnx"' in lock and 'version = "1.13.8"' in lock
    assert 'registry = "https://pypi.org/simple"' in lock


def test_worker_can_finish_normally_while_owner_pipe_stays_open():
    worker = local_voice.RUNTIME_SOURCE / "worker.py"
    script = (
        "import runpy,os; m=runpy.run_path(" + repr(str(worker)) + ");"
        f"m['guard_parent']({os.getpid()});os.write(1,b'completed\\n')"
    )
    child = subprocess.Popen(
        [str(Path(sys._base_executable).resolve()), "-I", "-c", script],
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    )
    try:
        assert child.wait(timeout=5) == 0
        assert child.stdout.read() == b"completed\n"
        assert child.stderr.read() == b""
    finally:
        if child.poll() is None:
            child.kill()
        child.wait(timeout=5)
        child.stdin.close()
        child.stdout.close()
        child.stderr.close()


def test_cancellation_during_process_creation_still_reaps_child(tmp_path, monkeypatch):
    async def run():
        voice = engine(tmp_path)
        voice._owned(create=True)
        original = asyncio.create_subprocess_exec
        created, release = asyncio.Event(), asyncio.Event()
        children = []

        async def delayed_spawn(*args, **kwargs):
            child = await original(*args, **kwargs)
            children.append(child)
            created.set()
            await release.wait()
            return child

        monkeypatch.setattr(asyncio, "create_subprocess_exec", delayed_spawn)
        task = asyncio.create_task(
            voice._command([sys.executable, "-I", "-c", "import time;time.sleep(60)"], timeout=60)
        )
        await created.wait()
        task.cancel()
        await asyncio.sleep(0)
        assert not task.done()
        release.set()
        result = await asyncio.gather(task, return_exceptions=True)
        assert isinstance(result[0], asyncio.CancelledError)
        assert children[0].returncode is not None

    asyncio.run(run())


def test_missing_required_model_resource_makes_reinstallation_available(tmp_path, monkeypatch):
    import hashlib

    async def run():
        voice = engine(tmp_path)
        root = voice._owned(create=True)
        marker = {
            "schema": 1,
            "lock": hashlib.sha256(
                (local_voice.RUNTIME_SOURCE / "uv.lock").read_bytes()
            ).hexdigest(),
            "asr": local_voice.ASR.sha256,
            "tts": local_voice.TTS.sha256,
            "python": local_voice.PYTHON_VERSION,
        }
        (root / "ready.json").write_text(json.dumps(marker))
        files = json.loads((local_voice.RUNTIME_SOURCE / "model_files.json").read_text())
        for name in files:
            path = root / "models" / name
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(b"synthetic")
        monkeypatch.setattr(voice, "_python", lambda: sys.executable)
        monkeypatch.setattr(voice, "_site_packages", lambda: root)
        assert voice.status()["state"] == "ready"
        (root / "models" / local_voice.TTS.directory / "tokens.txt").unlink()
        assert voice.status()["state"] == "missing"
        calls = []

        async def setup():
            calls.append(True)

        monkeypatch.setattr(voice, "_setup", setup)
        assert voice.start_install()["state"] == "installing"
        await voice._install
        assert calls == [True]

    asyncio.run(run())


def test_synthesis_rejects_truncated_wav(tmp_path, monkeypatch):
    import base64

    async def run():
        voice = engine(tmp_path)

        async def infer(request):
            return {"audio": base64.b64encode(wav()[:-4]).decode()}

        monkeypatch.setattr(voice, "_infer", infer)
        with pytest.raises(LocalVoiceError) as error:
            await voice.synthesize("测试")
        assert error.value.code == "local_voice_response"

    asyncio.run(run())


@pytest.mark.parametrize("failure", ["hash", "size", "redirect"])
def test_download_integrity_and_redirect_fail_closed(tmp_path, monkeypatch, failure):
    import hashlib

    import httpx

    async def run():
        voice = engine(tmp_path)
        root = voice._owned(create=True)
        blob = b"official-synthetic-resource"
        spec = local_voice.Download(
            "https://github.com/example/file.tar", len(blob), hashlib.sha256(blob).hexdigest(), ""
        )

        def response(request):
            if failure == "redirect":
                return httpx.Response(302, headers={"location": "https://other.invalid/file"})
            return httpx.Response(
                200, content=(blob + b"extra") if failure == "size" else b"x" * len(blob)
            )

        original = httpx.AsyncClient
        monkeypatch.setattr(
            local_voice.httpx,
            "AsyncClient",
            lambda **kwargs: original(transport=httpx.MockTransport(response), **kwargs),
        )
        with pytest.raises(ValueError):
            await voice._download(spec, root)
        assert not (root / "file.tar").exists()

    asyncio.run(run())


@pytest.mark.parametrize("valid_pid", [True, False])
def test_worker_protocol_verifies_native_process_pid(tmp_path, valid_pid):
    async def run():
        voice = engine(tmp_path)
        voice._owned(create=True)
        executable = str(Path(sys._base_executable).resolve())
        script = (
            "import os,json,sys;sys.stdin.readline();print(json.dumps({'ok':True,'pid':"
            + ("os.getpid()" if valid_pid else "0")
            + "}))"
        )
        if valid_pid:
            response = await voice._command(
                [executable, "-I", "-c", script], timeout=5, payload=b"{}"
            )
            assert json.loads(response)["pid"] > 0
        else:
            with pytest.raises(LocalVoiceError) as error:
                await voice._command([executable, "-I", "-c", script], timeout=5, payload=b"{}")
            assert error.value.code == "local_voice_process"

    asyncio.run(run())


@pytest.mark.parametrize("location", ["owned", "foreign", "relative"])
def test_windows_interpreter_resolution_bypasses_launcher_and_rejects_foreign(
    tmp_path, monkeypatch, location
):
    voice = engine(tmp_path)
    root = voice._owned(create=True)
    (root / "venv").mkdir()
    (root / "python").mkdir()
    target = root / "python" / "cpython-3.11.15" if location == "owned" else tmp_path / "foreign"
    target.mkdir()
    (target / "python.exe").write_bytes(b"synthetic executable")
    home = str(target) if location != "relative" else "relative-python"
    (root / "venv" / "pyvenv.cfg").write_text("home = " + home + "\n")
    monkeypatch.setattr(local_voice.sys, "platform", "win32")
    if location == "owned":
        assert voice._python() == target / "python.exe"
    else:
        with pytest.raises(DataRootError):
            voice._python()


def test_inference_deadline_includes_queue_and_never_starts_expired_request(tmp_path, monkeypatch):
    async def run():
        voice = engine(tmp_path)
        monkeypatch.setattr(local_voice, "INFERENCE_TIMEOUT", 0.02)
        calls = []

        async def worker(request):
            calls.append(request)
            raise AssertionError("expired queued request must never start inference")

        monkeypatch.setattr(voice, "_worker", worker)
        await voice._inference_lock.acquire()
        try:
            # Outer watchdog only bounds a broken regression; the expected
            # UI-safe exception must come from the request's own deadline.
            async with asyncio.timeout(1):
                with pytest.raises(LocalVoiceError) as error:
                    await voice._infer({"operation": "tts", "text": "合成排队测试"})
            assert error.value.code == "local_voice_timeout"
            assert voice._inference_lock.locked()
            assert not voice._requests
        finally:
            voice._inference_lock.release()
        await asyncio.sleep(0)
        assert calls == []

    asyncio.run(run())


def test_native_paths_are_ascii_relative_under_unicode_root_without_changing_app_cwd(tmp_path):
    worker = local_voice.RUNTIME_SOURCE / "worker.py"
    models = tmp_path / "中文 用户" / "assets" / "voice" / "models"
    required = json.loads((local_voice.RUNTIME_SOURCE / "model_files.json").read_text())
    for name in required:
        path = models / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(b"synthetic resource")
    app_cwd = Path.cwd()
    script = (
        "import json,runpy,sys;from pathlib import Path;"
        "m=runpy.run_path(sys.argv[1]);models=Path(sys.argv[2]);"
        "asr,tts=m['prepare_native_paths'](models);"
        "names=json.loads(Path(sys.argv[1]).with_name('model_files.json').read_text());"
        "assert not asr.is_absolute() and not tts.is_absolute();"
        "assert str(asr).isascii() and str(tts).isascii();"
        "assert Path.cwd()==models.resolve();"
        "assert all(name.isascii() and not Path(name).is_absolute() "
        "and Path(name).resolve()==models.resolve()/name "
        "and Path(name).read_bytes()==b'synthetic resource' for name in names);"
        "print(json.dumps({'checked_resources':len(names),'relative_ascii_paths':True}))"
    )
    child = subprocess.run(
        [str(Path(sys._base_executable).resolve()), "-I", "-c", script, str(worker), str(models)],
        capture_output=True,
        check=True,
        timeout=5,
    )
    assert json.loads(child.stdout) == {
        "checked_resources": len(required),
        "relative_ascii_paths": True,
    }
    assert Path.cwd() == app_cwd


@pytest.mark.parametrize("frozen", [False, True])
def test_windows_download_selection_uses_compiled_abi_with_processor_environment_absent(
    monkeypatch, frozen
):
    from types import SimpleNamespace

    for name in ("PROCESSOR_ARCHITECTURE", "PROCESSOR_ARCHITEW6432"):
        monkeypatch.delenv(name, raising=False)
    # This is the actual fixed CPython 3.11 Windows helper, not a mock:
    # platform.machine() loses its only machine source in a scrubbed child.
    assert local_voice.platform._get_machine_win32() == ""
    interpreter = SimpleNamespace(platform="win32", version="3.11.15 [MSC v.1944 64 bit (AMD64)]")
    if frozen:
        interpreter.frozen = True
    monkeypatch.setattr(local_voice, "sys", interpreter)
    # Keep the actual sysconfig.get_platform implementation; replace only its
    # OS/compiler facts so this Windows regression runs on every CI platform.
    monkeypatch.setattr(local_voice.sysconfig, "os", SimpleNamespace(name="nt"))
    monkeypatch.setattr(local_voice.sysconfig, "sys", interpreter)
    assert local_voice._download_spec() == local_voice.UV_DOWNLOADS[("win32", "amd64")]


@pytest.mark.parametrize(
    "version",
    [
        "3.11.15 [MSC v.1944 64 bit (ARM64)]",
        "3.11.15 [MSC v.1944 32 bit (Intel)]",
        "unknown-interpreter",
    ],
)
def test_windows_download_selection_rejects_unsupported_abi_even_with_amd64_environment(
    monkeypatch, version
):
    from types import SimpleNamespace

    monkeypatch.setenv("PROCESSOR_ARCHITECTURE", "AMD64")
    monkeypatch.setenv("PROCESSOR_ARCHITEW6432", "AMD64")
    interpreter = SimpleNamespace(platform="win32", version=version, frozen=True)
    monkeypatch.setattr(local_voice, "sys", interpreter)
    monkeypatch.setattr(local_voice.sysconfig, "os", SimpleNamespace(name="nt"))
    monkeypatch.setattr(local_voice.sysconfig, "sys", interpreter)
    assert local_voice._download_spec() is None


class FakeDllDirectory:
    def __init__(self, *, fail_restore=False):
        self.original = r"C:\synthetic-app\_internal"
        self.current = self.original
        self.events = []
        self.fail_restore = fail_restore

    def get(self):
        self.events.append(("get", self.current))
        return self.current

    def set(self, value):
        self.events.append(("set", value))
        if self.fail_restore and value == self.original:
            raise OSError("synthetic restore failure")
        self.current = value


def frozen_windows_dll_guard(monkeypatch, api):
    from types import SimpleNamespace

    monkeypatch.setattr(local_voice, "sys", SimpleNamespace(platform="win32", frozen=True))
    monkeypatch.setattr(local_voice, "_WindowsDllDirectory", lambda: api)
    monkeypatch.setattr(local_voice, "_DLL_SPAWN_LOCK", asyncio.Lock())


def test_frozen_windows_spawn_restores_dll_directory_before_return(monkeypatch):
    async def run():
        api = FakeDllDirectory()
        frozen_windows_dll_guard(monkeypatch, api)
        child = object()

        async def create(*args, **kwargs):
            assert api.current is None
            await asyncio.sleep(0)
            return child

        monkeypatch.setattr(asyncio, "create_subprocess_exec", create)
        assert await local_voice._spawn_external("synthetic") is child
        assert api.current == api.original
        assert api.events == [("get", api.original), ("set", None), ("set", api.original)]

    asyncio.run(run())


def test_frozen_windows_spawn_restores_dll_directory_on_creation_failure(monkeypatch):
    async def run():
        api = FakeDllDirectory()
        frozen_windows_dll_guard(monkeypatch, api)

        async def create(*args, **kwargs):
            assert api.current is None
            raise OSError("synthetic CreateProcess failure")

        monkeypatch.setattr(asyncio, "create_subprocess_exec", create)
        with pytest.raises(OSError, match="CreateProcess failure"):
            await local_voice._spawn_external("synthetic")
        assert api.current == api.original
        assert not local_voice._DLL_SPAWN_LOCK.locked()

    asyncio.run(run())


def test_frozen_windows_cancel_during_spawn_restores_dll_directory_and_reaps_child(
    tmp_path, monkeypatch
):
    async def run():
        voice = engine(tmp_path)
        voice._owned(create=True)
        native_python = str(Path(sys._base_executable).resolve())
        original_create = asyncio.create_subprocess_exec
        api = FakeDllDirectory()
        frozen_windows_dll_guard(monkeypatch, api)
        created, release = asyncio.Event(), asyncio.Event()
        children = []

        async def create(*args, **kwargs):
            assert api.current is None
            # The DLL API is synthetic but lifecycle uses a real native child.
            # Only Windows supports CREATE_NO_WINDOW, so ignore it on this host.
            if sys.platform != "win32":
                kwargs["creationflags"] = 0
            child = await original_create(*args, **kwargs)
            children.append(child)
            created.set()
            await release.wait()
            return child

        monkeypatch.setattr(asyncio, "create_subprocess_exec", create)
        task = asyncio.create_task(
            voice._command([native_python, "-I", "-c", "import time;time.sleep(60)"], timeout=60)
        )
        await created.wait()
        task.cancel()
        await asyncio.sleep(0)
        assert not task.done()
        release.set()
        result = await asyncio.gather(task, return_exceptions=True)
        assert isinstance(result[0], asyncio.CancelledError)
        assert api.current == api.original
        assert children[0].returncode is not None
        assert not voice._processes
        assert not local_voice._DLL_SPAWN_LOCK.locked()

    asyncio.run(run())


def test_failed_dll_restore_does_not_lose_live_spawn_result(tmp_path, monkeypatch):
    async def run():
        original_create = asyncio.create_subprocess_exec
        native_python = str(Path(sys._base_executable).resolve())
        api = FakeDllDirectory(fail_restore=True)
        frozen_windows_dll_guard(monkeypatch, api)
        children = []

        async def create(*args, **kwargs):
            child = await original_create(*args, **kwargs)
            children.append(child)
            return child

        monkeypatch.setattr(asyncio, "create_subprocess_exec", create)
        with pytest.raises(LocalVoiceError) as error:
            await local_voice._spawn_external(
                native_python,
                "-I",
                "-c",
                "import time;time.sleep(60)",
                stdin=asyncio.subprocess.PIPE,
            )
        assert error.value.code == "local_voice_spawn"
        assert children[0].returncode is not None
        assert not local_voice._DLL_SPAWN_LOCK.locked()

    asyncio.run(run())


def test_concurrent_frozen_spawns_do_not_overwrite_dll_restore_order(monkeypatch):
    async def run():
        api = FakeDllDirectory()
        frozen_windows_dll_guard(monkeypatch, api)
        active = 0

        async def create(name):
            nonlocal active
            active += 1
            assert active == 1 and api.current is None
            api.events.append(("spawn-start", name))
            await asyncio.sleep(0)
            api.events.append(("spawn-end", name))
            active -= 1
            return name

        monkeypatch.setattr(asyncio, "create_subprocess_exec", create)
        assert await asyncio.gather(
            local_voice._spawn_external("first"), local_voice._spawn_external("second")
        ) == ["first", "second"]
        assert api.current == api.original
        assert api.events == [
            ("get", api.original),
            ("set", None),
            ("spawn-start", "first"),
            ("spawn-end", "first"),
            ("set", api.original),
            ("get", api.original),
            ("set", None),
            ("spawn-start", "second"),
            ("spawn-end", "second"),
            ("set", api.original),
        ]

    asyncio.run(run())


@pytest.mark.parametrize("system,frozen", [("win32", False), ("darwin", True), ("linux", True)])
def test_dll_directory_is_untouched_outside_frozen_windows(monkeypatch, system, frozen):
    from types import SimpleNamespace

    async def run():
        monkeypatch.setattr(local_voice, "sys", SimpleNamespace(platform=system, frozen=frozen))

        def api():
            raise AssertionError("must not call Windows DLL APIs on this platform")

        async def create(*args, **kwargs):
            return "created"

        monkeypatch.setattr(local_voice, "_WindowsDllDirectory", api)
        monkeypatch.setattr(asyncio, "create_subprocess_exec", create)
        assert await local_voice._spawn_external("synthetic") == "created"

    asyncio.run(run())
