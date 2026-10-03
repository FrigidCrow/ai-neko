"""Optional, application-owned local voice runtime; no cloud calls or media files.

The main application never imports the native inference dependencies. A pinned
uv bootstraps a separate managed Python and locked wheels after explicit setup.
Each inference is a bounded child process with an owner pipe held open by us.
"""

from __future__ import annotations

import asyncio
import base64
import contextlib
import ctypes
import hashlib
import io
import json
import os
import platform
import stat
import sys
import sysconfig
import tarfile
import wave
import zipfile
from dataclasses import dataclass
from pathlib import Path, PurePosixPath

import httpx

from ai_neko.config.paths import DataPaths, DataRootError, safe_child

MAX_AUDIO = 8 * 1024 * 1024
MAX_WIRE = 12 * 1024 * 1024
MAX_TEXT = 600
INFERENCE_TIMEOUT = 120
PYTHON_VERSION = "3.11.15"
UV_VERSION = "0.11.8"
RUNTIME_SOURCE = Path(__file__).with_name("local_runtime")
OWNER = {"app": "ai-neko-local-voice", "schema": 1}
# The application owns one asyncio runtime. This shared lock also serializes
# installs and inference from different LocalVoice instances on that runtime.
_DLL_SPAWN_LOCK = asyncio.Lock()


@dataclass(frozen=True)
class Download:
    url: str
    size: int
    sha256: str
    directory: str


UV_RELEASE = f"https://github.com/astral-sh/uv/releases/download/{UV_VERSION}/"
UV_DOWNLOADS = {
    ("darwin", "arm64"): Download(
        UV_RELEASE + "uv-aarch64-apple-darwin.tar.gz",
        20800166,
        "c729adb365114e844dd7f9316313a7ed6443b89bb5681d409eebac78b0bd06c8",
        "uv-aarch64-apple-darwin",
    ),
    ("linux", "x86_64"): Download(
        UV_RELEASE + "uv-x86_64-unknown-linux-gnu.tar.gz",
        24147124,
        "56dd1b66701ecb62fe896abb919444e4b83c5e8645cca953e6ddd496ff8a0feb",
        "uv-x86_64-unknown-linux-gnu",
    ),
    ("win32", "amd64"): Download(
        UV_RELEASE + "uv-x86_64-pc-windows-msvc.zip",
        23530194,
        "c84629a56e0706b69a47ea35862208af827cb6fbfa1d0ca763c52c67594637e8",
        "",
    ),
}
ASR = Download(
    "https://github.com/k2-fsa/sherpa-onnx/releases/download/asr-models/"
    "sherpa-onnx-sense-voice-zh-en-ja-ko-yue-int8-2024-07-17.tar.bz2",
    163002883,
    "7d1efa2138a65b0b488df37f8b89e3d91a60676e416f515b952358d83dfd347e",
    "sherpa-onnx-sense-voice-zh-en-ja-ko-yue-int8-2024-07-17",
)
TTS = Download(
    "https://github.com/k2-fsa/sherpa-onnx/releases/download/tts-models/"
    "kokoro-int8-multi-lang-v1_1.tar.bz2",
    147031220,
    "a1e94694776049035c4f2c6529f003aaece993c76aae9a78995831c3c4dcafc6",
    "kokoro-int8-multi-lang-v1_1",
)


class LocalVoiceError(RuntimeError):
    def __init__(self, code: str, message: str):
        self.code, self.message = code, message
        super().__init__(message)


class _WindowsDllDirectory:
    """Narrow wrapper for PyInstaller's process-wide Windows DLL directory."""

    def __init__(self):
        from ctypes import wintypes

        self.kernel = ctypes.WinDLL("kernel32", use_last_error=True)
        self.kernel.GetDllDirectoryW.argtypes = [wintypes.DWORD, wintypes.LPWSTR]
        self.kernel.GetDllDirectoryW.restype = wintypes.DWORD
        self.kernel.SetDllDirectoryW.argtypes = [wintypes.LPCWSTR]
        self.kernel.SetDllDirectoryW.restype = wintypes.BOOL

    def get(self) -> str:
        ctypes.set_last_error(0)
        needed = self.kernel.GetDllDirectoryW(0, None)
        if not needed and ctypes.get_last_error():
            raise OSError("cannot read DLL directory")
        buffer = ctypes.create_unicode_buffer(needed + 1)
        ctypes.set_last_error(0)
        copied = self.kernel.GetDllDirectoryW(len(buffer), buffer)
        if copied >= len(buffer) or (not copied and ctypes.get_last_error()):
            raise OSError("cannot read DLL directory")
        return buffer.value

    def set(self, value: str | None) -> None:
        if not self.kernel.SetDllDirectoryW(value):
            raise OSError("cannot set DLL directory")


async def _reap_spawn_result(process) -> None:
    """Reap even when a setup failure prevents handing the child to its owner."""
    if process.returncode is None:
        with contextlib.suppress(ProcessLookupError):
            process.kill()
    reaped = asyncio.create_task(process.wait())
    while not reaped.done():
        try:
            await asyncio.shield(reaped)
        except asyncio.CancelledError:
            continue
    if process.stdin:
        process.stdin.close()


async def _spawn_external(*args, **kwargs):
    if sys.platform != "win32" or not getattr(sys, "frozen", False):
        return await asyncio.create_subprocess_exec(*args, **kwargs)
    # Clearing environment variables cannot undo SetDllDirectoryW inherited
    # from a frozen app. Clear only during creation, then immediately restore
    # the host before inference or network installation continues. The caller
    # shields this entire operation, retaining children created during cancel.
    async with _DLL_SPAWN_LOCK:
        api = _WindowsDllDirectory()
        process = None
        try:
            previous = api.get()
            api.set(None)
        except OSError:
            raise LocalVoiceError("local_voice_spawn", "无法准备独立语音运行环境。") from None
        try:
            process = await asyncio.create_subprocess_exec(*args, **kwargs)
        finally:
            try:
                api.set(previous)
            except OSError:
                # Never lose a live child because restoring the host DLL path
                # failed after CreateProcess had already succeeded.
                if process is not None:
                    await _reap_spawn_result(process)
                raise LocalVoiceError("local_voice_spawn", "无法恢复应用运行环境。") from None
        return process


def _download_spec() -> Download | None:
    if sys.platform == "win32":
        # CPython 3.11 platform.machine() reads PROCESSOR_* environment
        # variables on Windows; our intentionally scrubbed child environment
        # omits them. sysconfig's Windows tag derives from the interpreter's
        # compiled sys.version instead, including in a frozen CPython process.
        # Select the actual process ABI, not a possibly different host ISA.
        if sysconfig.get_platform() != "win-amd64":
            return None
        return UV_DOWNLOADS[("win32", "amd64")]
    return UV_DOWNLOADS.get((sys.platform, platform.machine().lower()))


def _child(root: Path, relative: str) -> Path:
    """Check each component, including pre-existing nested redirect points."""
    parts = PurePosixPath(relative).parts
    if not parts or PurePosixPath(relative).is_absolute() or "\\" in relative:
        raise DataRootError("unsafe local voice path")
    result = root
    for part in parts:
        if part in {"..", "."} or ":" in part:
            raise DataRootError("unsafe local voice path")
        result = safe_child(result, part)
    return result


def _environment(root: Path) -> dict[str, str]:
    """Allowlist only OS plumbing; never inherit keys, indexes or Python config."""
    result = {k: os.environ[k] for k in ("SYSTEMROOT", "SystemRoot", "WINDIR") if k in os.environ}
    system = os.environ.get("SYSTEMROOT", r"C:\Windows")
    result.update(
        {
            "PATH": str(Path(system) / "System32") if sys.platform == "win32" else "/usr/bin:/bin",
            "HOME": str(root / "home"),
            "USERPROFILE": str(root / "home"),
            "LOCALAPPDATA": str(root / "home"),
            "APPDATA": str(root / "home"),
            "TMPDIR": str(root / "temp"),
            "TEMP": str(root / "temp"),
            "TMP": str(root / "temp"),
            "UV_CACHE_DIR": str(root / "cache"),
            "UV_PYTHON_INSTALL_DIR": str(root / "python"),
            "UV_PROJECT_ENVIRONMENT": str(root / "venv"),
            "UV_NO_CONFIG": "1",
            "UV_LINK_MODE": "copy",
            "PYTHONDONTWRITEBYTECODE": "1",
            "PYTHONUTF8": "1",
            "OMP_NUM_THREADS": "2",
            "OPENBLAS_NUM_THREADS": "2",
            "MKL_NUM_THREADS": "2",
            "LANG": "C.UTF-8",
        }
    )
    return result


def validate_wav(data: bytes) -> None:
    if not isinstance(data, bytes) or not 44 <= len(data) <= MAX_AUDIO:
        raise LocalVoiceError("invalid_audio", "录音必须是 8 MiB 以内的 WAV。")
    try:
        with wave.open(io.BytesIO(data), "rb") as audio:
            if (
                audio.getnchannels() != 1
                or audio.getsampwidth() != 2
                or audio.getframerate() != 16000
                or audio.getnframes() > 60 * 16000
                or audio.getnframes() <= 0
                or audio.getcomptype() != "NONE"
            ):
                raise ValueError
            if len(audio.readframes(audio.getnframes())) != audio.getnframes() * 2:
                raise ValueError
    except (wave.Error, EOFError, ValueError):
        raise LocalVoiceError(
            "invalid_audio", "本地识别需要 60 秒以内的单声道 16 kHz PCM WAV。"
        ) from None


async def _extract(archive: Path, destination: Path) -> None:
    """No archive extractall: deny links/devices/traversal and bound expansion."""
    total, count = 0, 0
    if archive.name.endswith(".zip"):
        with zipfile.ZipFile(archive) as bundle:
            for info in bundle.infolist():
                mode = info.external_attr >> 16
                if stat.S_ISLNK(mode) or (stat.S_IFMT(mode) not in (0, stat.S_IFREG, stat.S_IFDIR)):
                    raise ValueError("archive special member")
                name = info.filename.rstrip("/")
                target = _child(destination, name)
                count += 1
                total += info.file_size
                if count > 20000 or total > 1024 * 1024 * 1024:
                    raise ValueError("archive expansion limit")
                target.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
                if info.is_dir():
                    target.mkdir(exist_ok=True, mode=0o700)
                    continue
                with bundle.open(info) as source, target.open("wb") as output:
                    while block := source.read(256 * 1024):
                        output.write(block)
                        await asyncio.sleep(0)
                target.chmod(0o700 if mode & 0o111 else 0o600)
        return
    with tarfile.open(archive, mode="r|*") as bundle:
        for info in bundle:
            if not (info.isfile() or info.isdir()):
                raise ValueError("archive special member")
            target = _child(destination, info.name.rstrip("/"))
            count += 1
            total += info.size
            if count > 20000 or total > 1024 * 1024 * 1024:
                raise ValueError("archive expansion limit")
            target.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
            if info.isdir():
                target.mkdir(exist_ok=True, mode=0o700)
                continue
            with bundle.extractfile(info) as source, target.open("wb") as output:
                while block := source.read(256 * 1024):
                    output.write(block)
                    await asyncio.sleep(0)
            target.chmod(0o700 if info.mode & 0o111 else 0o600)


class LocalVoice:
    def __init__(self, paths: DataPaths):
        self.paths = paths
        self.root = paths.assets / "voice"
        self._state, self._message, self._stage = (
            "missing",
            "首次使用需要下载免费语音资源。",
            "idle",
        )
        self._downloaded = 0
        self._total = ASR.size + TTS.size + (_download_spec().size if _download_spec() else 0)
        self._install: asyncio.Task | None = None
        self._processes: set[asyncio.subprocess.Process] = set()
        self._requests: set[asyncio.Task] = set()
        self._inference_lock = asyncio.Lock()
        self._closed = False

    def _owned(self, create: bool = False) -> Path:
        root = safe_child(self.paths.assets, "voice")
        if create:
            root.mkdir(mode=0o700, exist_ok=True)
        marker = safe_child(root, ".owner.json")
        if create and not marker.exists():
            if any(root.iterdir()):
                raise DataRootError("foreign local voice directory")
            with marker.open("x", encoding="utf-8") as handle:
                json.dump(OWNER, handle)
            marker.chmod(0o600)
        if json.loads(marker.read_text(encoding="utf-8")) != OWNER:
            raise DataRootError("foreign local voice owner")
        return root

    def _ready(self) -> bool:
        if not self.root.exists():
            return False
        root = self._owned()
        marker = _child(root, "ready.json")
        if not marker.exists():
            return False
        value = json.loads(marker.read_text(encoding="utf-8"))
        expected = {
            "schema": 1,
            "lock": hashlib.sha256((RUNTIME_SOURCE / "uv.lock").read_bytes()).hexdigest(),
            "asr": ASR.sha256,
            "tts": TTS.sha256,
            "python": PYTHON_VERSION,
        }
        if value != expected:
            return False
        self._python()
        self._site_packages()
        for relative in json.loads((RUNTIME_SOURCE / "model_files.json").read_text()):
            path = _child(root, "models/" + relative)
            if not path.is_file() or path.stat().st_size == 0:
                return False
        return True

    def status(self) -> dict:
        if self._state != "installing":
            try:
                if self._ready():
                    self._state, self._message = "ready", "免费本地语音已就绪，无需 API Key。"
                elif self._state == "ready":
                    self._state, self._message = "missing", "语音资源不完整，请重新安装。"
            except (OSError, ValueError):
                self._state, self._message = "error", "语音资源目录无效，请检查本应用数据目录。"
        supported = _download_spec() is not None
        return {
            "state": self._state,
            "message": self._message,
            "stage": self._stage,
            "downloaded_bytes": self._downloaded,
            "total_bytes": self._total,
            "supported": supported,
            "closed": self._closed,
        }

    def start_install(self) -> dict:
        if self._closed:
            raise LocalVoiceError("voice_closed", "语音服务已经关闭。")
        if not _download_spec():
            raise LocalVoiceError("local_voice_unsupported", "当前系统架构暂不支持本地语音安装。")
        if self._install and not self._install.done():
            return self.status()
        if self.status()["state"] == "ready":
            return self.status()
        self._state, self._message, self._stage = "installing", "正在准备本地语音资源。", "prepare"
        self._downloaded = 0
        self._install = asyncio.create_task(self._setup(), name="ai-neko-local-voice-setup")
        return self.status()

    async def _download(self, spec: Download, directory: Path) -> Path:
        name = spec.url.rsplit("/", 1)[-1]
        output = _child(directory, name)
        if output.is_file():
            digest = hashlib.sha256()
            with output.open("rb") as handle:
                while block := handle.read(256 * 1024):
                    digest.update(block)
                    await asyncio.sleep(0)
            if output.stat().st_size == spec.size and digest.hexdigest() == spec.sha256:
                self._downloaded += spec.size
                return output
        temporary = _child(directory, name + ".partial")
        digest, size = hashlib.sha256(), 0
        url = spec.url
        async with httpx.AsyncClient(
            trust_env=False, timeout=httpx.Timeout(60, connect=20)
        ) as client:
            for _ in range(6):
                async with client.stream("GET", url) as response:
                    if response.status_code in {301, 302, 303, 307, 308}:
                        next_url = httpx.URL(url).join(response.headers.get("location", ""))
                        if next_url.scheme != "https" or next_url.host not in {
                            "github.com",
                            "release-assets.githubusercontent.com",
                            "objects.githubusercontent.com",
                        }:
                            raise ValueError("download redirect refused")
                        url = str(next_url)
                        continue
                    response.raise_for_status()
                    if response.headers.get("content-length") not in (None, str(spec.size)):
                        raise ValueError("download size mismatch")
                    with temporary.open("wb") as handle:
                        temporary.chmod(0o600)
                        async for block in response.aiter_bytes(256 * 1024):
                            size += len(block)
                            if size > spec.size:
                                raise ValueError("download limit")
                            handle.write(block)
                            digest.update(block)
                            self._downloaded += len(block)
                    break
            else:
                raise ValueError("download redirects exhausted")
        if size != spec.size or digest.hexdigest() != spec.sha256:
            raise ValueError("download checksum mismatch")
        temporary.replace(output)
        return output

    async def _setup(self) -> None:
        try:
            root = self._owned(create=True)
            for name in (
                "downloads",
                "bootstrap",
                "runtime",
                "models",
                "cache",
                "python",
                "home",
                "temp",
            ):
                _child(root, name).mkdir(exist_ok=True, mode=0o700)
            for name in (
                "pyproject.toml",
                "uv.lock",
                "worker.py",
                "LICENSES.md",
                "model_files.json",
            ):
                _child(root, f"runtime/{name}").write_bytes((RUNTIME_SOURCE / name).read_bytes())
            _child(root, "runtime/licenses").mkdir(exist_ok=True, mode=0o700)
            for source in sorted((RUNTIME_SOURCE / "licenses").glob("*.txt")):
                _child(root, "runtime/licenses/" + source.name).write_bytes(source.read_bytes())
            self._stage, self._message = "runtime", "正在下载独立 Python 运行环境。"
            spec = _download_spec()
            archive = await self._download(spec, root / "downloads")
            await _extract(archive, root / "bootstrap")
            uv_name = "uv.exe" if sys.platform == "win32" else f"{spec.directory}/uv"
            uv = _child(root, f"bootstrap/{uv_name}")
            uv.chmod(0o700)
            await self._command(
                [
                    str(uv),
                    "--no-config",
                    "python",
                    "install",
                    PYTHON_VERSION,
                    "--no-bin",
                    "--no-registry",
                    "--managed-python",
                ],
                timeout=600,
            )
            await self._command(
                [
                    str(uv),
                    "--no-config",
                    "sync",
                    "--project",
                    str(root / "runtime"),
                    "--locked",
                    "--no-build",
                    "--no-install-project",
                    "--no-sources",
                    "--python",
                    PYTHON_VERSION,
                    "--managed-python",
                    "--no-python-downloads",
                    "--default-index",
                    "https://pypi.org/simple",
                    "--link-mode",
                    "copy",
                ],
                timeout=600,
            )
            for label, model in (("asr", ASR), ("tts", TTS)):
                self._stage, self._message = (
                    label,
                    "正在下载中文识别模型。" if label == "asr" else "正在下载中文朗读模型。",
                )
                archive = await self._download(model, root / "downloads")
                await _extract(archive, root / "models")
            self._stage, self._message = "verify", "正在检查本地语音运行环境。"
            await self._worker({"operation": "check"}, timeout=60, require_ready=False)
            manifest = {
                "schema": 1,
                "lock": hashlib.sha256((RUNTIME_SOURCE / "uv.lock").read_bytes()).hexdigest(),
                "asr": ASR.sha256,
                "tts": TTS.sha256,
                "python": PYTHON_VERSION,
            }
            pending = _child(root, "ready.pending.json")
            pending.write_text(json.dumps(manifest), encoding="utf-8")
            pending.replace(_child(root, "ready.json"))
            self._state, self._message, self._stage = (
                "ready",
                "免费本地语音已就绪，无需 API Key。",
                "complete",
            )
        except asyncio.CancelledError:
            self._state, self._message, self._stage = (
                "missing",
                "已取消安装，可重新开始。",
                "cancelled",
            )
            raise
        except Exception:
            # Never expose subprocess output, download content, local paths or keys.
            self._state, self._message = "error", "本地语音安装未完成，请检查网络、磁盘空间后重试。"

    def _site_packages(self) -> Path:
        suffix = (
            "venv/Lib/site-packages"
            if sys.platform == "win32"
            else "venv/lib/python3.11/site-packages"
        )
        directory = _child(self._owned(), suffix)
        if not directory.is_dir():
            raise DataRootError("private voice packages missing")
        return directory

    def _python(self) -> Path:
        root = self._owned()
        allowed = _child(root, "python").resolve()
        if sys.platform == "win32":
            # Windows venv/python.exe is a launcher creating a second process.
            # Execute the actual managed interpreter so kill+wait owns native
            # inference itself, not merely that intermediate launcher.
            cfg = _child(root, "venv/pyvenv.cfg")
            if cfg.stat().st_size > 8192:
                raise DataRootError("invalid private Python metadata")
            fields = dict(
                line.split("=", 1)
                for line in cfg.read_text(encoding="utf-8").splitlines()
                if "=" in line
            )
            home = next(
                (value.strip() for key, value in fields.items() if key.strip() == "home"), ""
            )
            directory = Path(home)
            if not directory.is_absolute() or not directory.resolve().is_relative_to(allowed):
                raise DataRootError("local voice interpreter outside owned environment")
            target = _child(directory, "python.exe").resolve(strict=True)
        else:
            folder = _child(root, "venv/bin")
            target = (folder / "python").resolve(strict=True)
        if not target.is_relative_to(allowed) or not target.is_file():
            raise DataRootError("local voice interpreter outside owned environment")
        return target

    async def _command(
        self, args: list[str], *, timeout: float, payload: bytes | None = None
    ) -> bytes:
        spawn = asyncio.create_task(
            _spawn_external(
                *args,
                cwd=str(self.root),
                env=_environment(self.root),
                stdin=asyncio.subprocess.PIPE
                if payload is not None
                else asyncio.subprocess.DEVNULL,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.DEVNULL,
                creationflags=0x08000000 if sys.platform == "win32" else 0,
            )
        )
        try:
            process = await asyncio.shield(spawn)
        except asyncio.CancelledError:
            # Creation may already have started the OS child. Retain the spawn
            # result and reap it even when cancellation arrives before return.
            while not spawn.done():
                try:
                    await asyncio.shield(spawn)
                except asyncio.CancelledError:
                    continue
            if not spawn.cancelled() and spawn.exception() is None:
                process = spawn.result()
                if process.returncode is None:
                    with contextlib.suppress(ProcessLookupError):
                        process.kill()
                reap = asyncio.create_task(process.wait())
                while not reap.done():
                    try:
                        await asyncio.shield(reap)
                    except asyncio.CancelledError:
                        continue
                if process.stdin:
                    process.stdin.close()
            raise
        self._processes.add(process)
        try:
            async with asyncio.timeout(timeout):
                if payload is not None:
                    process.stdin.write(payload + b"\n")
                    await process.stdin.drain()
                    # Leave stdin open. EOF is the worker's owner-death signal.
                output = bytearray()
                while chunk := await process.stdout.read(65536):
                    output.extend(chunk)
                    if len(output) > MAX_WIRE:
                        raise LocalVoiceError("local_voice_response", "本地语音输出超过限制。")
                await process.wait()
                if process.returncode != 0:
                    raise LocalVoiceError("local_voice_failed", "本地语音处理失败，请重试。")
                if payload is not None:
                    try:
                        if json.loads(output).get("pid") != process.pid:
                            raise ValueError
                    except (ValueError, AttributeError, UnicodeError):
                        raise LocalVoiceError(
                            "local_voice_process", "本地语音进程校验失败。"
                        ) from None
                return bytes(output)
        except TimeoutError:
            raise LocalVoiceError(
                "local_voice_timeout", "本地语音处理超时，请缩短内容后重试。"
            ) from None
        finally:
            if process.returncode is None:
                with contextlib.suppress(ProcessLookupError):
                    process.kill()
            # shield cleanup from repeated cancellation and do not return while
            # native inference can still run or own audio buffers.
            wait = asyncio.create_task(process.wait())
            while not wait.done():
                try:
                    await asyncio.shield(wait)
                except asyncio.CancelledError:
                    continue
            self._processes.discard(process)
            if process.stdin:
                process.stdin.close()

    async def _worker(
        self, request: dict, *, timeout: float = 120, require_ready: bool = True
    ) -> dict:
        if self._closed or (require_ready and self.status()["state"] != "ready"):
            raise LocalVoiceError("local_voice_not_ready", "请先下载免费本地语音资源。")
        request.update(
            {
                "models": str(_child(self.root, "models")),
                "parent": os.getpid(),
                "site_packages": str(self._site_packages()),
            }
        )
        wire = json.dumps(request, ensure_ascii=False).encode("utf-8")
        result = await self._command(
            [str(self._python()), "-I", "-B", str(RUNTIME_SOURCE / "worker.py")],
            timeout=timeout,
            payload=wire,
        )
        try:
            value = json.loads(result)
            if not isinstance(value, dict) or value.get("ok") is not True:
                raise ValueError
            return value
        except (ValueError, UnicodeError):
            raise LocalVoiceError("local_voice_failed", "本地语音处理失败，请重试。") from None

    async def _infer(self, request: dict) -> dict:
        task = asyncio.current_task()
        self._requests.add(task)
        try:
            # This deadline covers queueing as well as native inference. A
            # request that has already timed out must never acquire the lock
            # later and start work after its desktop caller has given up.
            async with asyncio.timeout(INFERENCE_TIMEOUT):
                async with self._inference_lock:
                    return await self._worker(request)
        except TimeoutError:
            raise LocalVoiceError(
                "local_voice_timeout", "本地语音处理超时，请缩短内容后重试。"
            ) from None
        finally:
            self._requests.discard(task)

    async def transcribe(self, wav_bytes: bytes) -> str:
        validate_wav(wav_bytes)
        value = await self._infer(
            {"operation": "asr", "audio": base64.b64encode(wav_bytes).decode("ascii")}
        )
        text = value.get("text")
        if not isinstance(text, str) or not 0 < len(text.strip()) <= 8000:
            raise LocalVoiceError("empty_transcript", "没有识别到说话内容，请重试。")
        return text.strip()

    async def synthesize(self, text: str) -> bytes:
        if not isinstance(text, str) or not 0 < len(text.strip()) <= MAX_TEXT:
            raise LocalVoiceError("speech_too_long", "本地朗读每次支持 1 至 600 字。")
        value = await self._infer({"operation": "tts", "text": text.strip()})
        try:
            audio = base64.b64decode(value["audio"], validate=True)
            if not 44 <= len(audio) <= MAX_AUDIO:
                raise ValueError
            with wave.open(io.BytesIO(audio), "rb") as data:
                if (
                    data.getnchannels() != 1
                    or data.getsampwidth() != 2
                    or data.getcomptype() != "NONE"
                ):
                    raise ValueError
                if (
                    not 8000 <= data.getframerate() <= 48000
                    or not 0 < data.getnframes() * 2 <= MAX_AUDIO
                ):
                    raise ValueError
                if len(data.readframes(data.getnframes())) != data.getnframes() * 2:
                    raise ValueError
            return audio
        except (KeyError, TypeError, ValueError, wave.Error, EOFError):
            raise LocalVoiceError("local_voice_response", "本地语音返回的音频无效。") from None

    async def cancel_install(self) -> dict:
        if self._install and not self._install.done():
            self._install.cancel()
            await asyncio.gather(self._install, return_exceptions=True)
        return self.status()

    async def close(self) -> None:
        self._closed = True
        await self.cancel_install()
        tasks = tuple(self._requests)
        for task in tasks:
            task.cancel()
        if tasks:
            await asyncio.gather(*tasks, return_exceptions=True)
        for process in tuple(self._processes):
            if process.returncode is None:
                with contextlib.suppress(ProcessLookupError):
                    process.kill()
            await process.wait()
