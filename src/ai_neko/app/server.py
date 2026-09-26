"""Single-instance loopback service and isolated local chat interface."""

from __future__ import annotations

import asyncio
import contextlib
import json
import os
import secrets
import socket
import threading
import uuid
import webbrowser
from contextlib import asynccontextmanager
from dataclasses import dataclass, field
from pathlib import Path
from typing import TextIO

import uvicorn
from fastapi import FastAPI, HTTPException, Request, WebSocket, WebSocketDisconnect
from filelock import FileLock, Timeout
from starlette.middleware.trustedhost import TrustedHostMiddleware

from ai_neko import APP_ID, __version__
from ai_neko.config.paths import DataPaths, safe_child
from ai_neko.config.settings import Settings, initialize_config


class InstanceInUseError(RuntimeError):
    pass


class PortInUseError(RuntimeError):
    pass


def _windows_kernel32():
    import ctypes
    from ctypes import wintypes

    kernel = ctypes.WinDLL("kernel32", use_last_error=True)
    kernel.OpenProcess.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
    kernel.OpenProcess.restype = wintypes.HANDLE
    kernel.WaitForSingleObject.argtypes = [wintypes.HANDLE, wintypes.DWORD]
    kernel.WaitForSingleObject.restype = wintypes.DWORD
    kernel.CloseHandle.argtypes = [wintypes.HANDLE]
    kernel.CloseHandle.restype = wintypes.BOOL
    return kernel


class WindowsParentProcess:
    """Capture one process object, then observe that HANDLE without PID reuse races."""

    def __init__(self, pid: int):
        if type(pid) is not int or not 0 < pid <= 0xFFFFFFFF or pid == os.getpid():
            raise ValueError("invalid desktop parent process ID")
        self._kernel = _windows_kernel32()
        self._stopped = threading.Event()
        self._thread: threading.Thread | None = None
        # SYNCHRONIZE is sufficient; no termination, memory or inherited rights.
        self._handle = self._kernel.OpenProcess(0x00100000, False, pid)
        if not self._handle:
            raise ValueError("desktop parent process is unavailable")
        try:
            self.ensure_alive()
        except (ValueError, OSError):
            self.close()
            raise

    def ensure_alive(self) -> None:
        if not self._handle or self._kernel.WaitForSingleObject(self._handle, 0) != 0x102:
            raise ValueError("desktop parent process has ended or cannot be monitored")

    def start(self, on_parent_exit) -> None:
        if self._thread is not None:
            raise RuntimeError("desktop parent monitor was already started")
        self.ensure_alive()

        def watch():
            while not self._stopped.is_set():
                # Poll the captured kernel object, never reopen a potentially
                # reused PID. A bounded wait also permits safe normal cleanup.
                outcome = self._kernel.WaitForSingleObject(self._handle, 250)
                if self._stopped.is_set():
                    return
                if outcome != 0x102:
                    # Signaled means exit. Any failed wait must also fail closed.
                    on_parent_exit()
                    return

        self._thread = threading.Thread(target=watch, name="desktop-parent-handle", daemon=True)
        self._thread.start()

    def close(self) -> None:
        self._stopped.set()
        if self._thread is not None:
            # Closing a HANDLE while WaitForSingleObject is pending is undefined.
            # Every wait is bounded to 250 ms, so join before releasing it.
            self._thread.join()
        if self._handle:
            self._kernel.CloseHandle(self._handle)
            self._handle = None


@dataclass(frozen=True)
class Connection:
    port: int
    token: str = field(repr=False)
    instance_id: str

    @property
    def url(self) -> str:
        return f"http://127.0.0.1:{self.port}"

    def descriptor(self) -> dict:
        return {
            "app_id": APP_ID,
            "protocol_version": 1,
            "pid": os.getpid(),
            "instance_id": self.instance_id,
            "host": "127.0.0.1",
            "port": self.port,
            "http_url": self.url,
            "ws_url": f"ws://127.0.0.1:{self.port}/ws",
            "allowed_origin": self.url,
            "token": self.token,
        }


def write_private_json(path: Path, value: dict) -> None:
    temporary = safe_child(path.parent, f".{path.name}.{uuid.uuid4().hex}.tmp")
    try:
        # The connection descriptor carries the bearer token: create with the
        # final mode atomically instead of open-then-chmod.
        fd = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            json.dump(value, handle)
            handle.write("\n")
        temporary.replace(path)
    finally:
        temporary.unlink(missing_ok=True)


def create_app(
    connection: Connection,
    on_shutdown,
    lifespan=None,
    *,
    runtime=None,
    providers=None,
    bootstrap_code=None,
) -> FastAPI:
    app = FastAPI(docs_url=None, redoc_url=None, openapi_url=None, lifespan=lifespan)
    app.add_middleware(TrustedHostMiddleware, allowed_hosts=["127.0.0.1"])

    def authorize(request: Request) -> None:
        given = request.headers.get("authorization", "")
        expected = f"Bearer {connection.token}"
        if not secrets.compare_digest(given.encode(), expected.encode()):
            raise HTTPException(status_code=401, detail="invalid session token")
        if request.headers.get("origin") not in {None, connection.url}:
            raise HTTPException(status_code=403, detail="untrusted origin")

    @app.get("/health")
    async def health(request: Request):
        authorize(request)
        return {"app_id": APP_ID, "status": "ok", "version": __version__, "stage": "M1"}

    @app.post("/shutdown", status_code=202)
    async def shutdown(request: Request):
        authorize(request)
        on_shutdown()
        return {"status": "stopping"}

    @app.websocket("/ws")
    async def websocket(ws: WebSocket):
        if ws.headers.get("origin") != connection.url or ws.query_params:
            await ws.close(code=1008)
            return
        await ws.accept()
        try:
            message = await asyncio.wait_for(ws.receive_json(), timeout=5)
            if not isinstance(message, dict) or message.get("type") != "auth":
                await ws.close(code=1008)
                return
            token = message.get("token")
            if not isinstance(token, str) or not secrets.compare_digest(
                token.encode(), connection.token.encode()
            ):
                await ws.close(code=1008)
                return
            await ws.send_json({"type": "ready", "app_id": APP_ID, "protocol_version": 1})
            while True:
                message = await ws.receive_json()
                if not isinstance(message, dict) or message.get("type") != "ping":
                    await ws.close(code=1008)
                    return
                await ws.send_json({"type": "pong"})
        except (TimeoutError, ValueError):
            await ws.close(code=1008)
        except WebSocketDisconnect:
            pass

    if runtime is not None:
        from ai_neko.app.api import install_api

        install_api(app, connection, authorize, runtime, providers, bootstrap_code)
    return app


def serve(
    paths: DataPaths,
    settings: Settings,
    *,
    open_browser: bool = False,
    parent_input: TextIO | None = None,
    parent_process: WindowsParentProcess | None = None,
) -> None:
    if parent_process is not None and parent_input is None:
        raise ValueError("desktop parent process monitoring requires the private pipe mode")
    lock_path = safe_child(paths.runtime, "instance.lock")
    lock = FileLock(lock_path, timeout=0)
    try:
        lock.acquire()
    except Timeout as exc:
        raise InstanceInUseError("another ai-neko instance owns this data root") from exc
    sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    connection = None
    try:
        connection_file = safe_child(paths.runtime, "connection.json")
        log_file = safe_child(paths.logs, "service.jsonl")
        initialize_config(paths)
        # Windows must exclusively own its port, without SO_REUSEADDR's sharing behavior.
        if os.name == "nt":
            sock.setsockopt(socket.SOL_SOCKET, socket.SO_EXCLUSIVEADDRUSE, 1)
        try:
            sock.bind(("127.0.0.1", settings.port))
            sock.listen(128)
        except OSError as exc:
            raise PortInUseError(
                "requested loopback port cannot be bound; no fallback port was used"
            ) from exc
        connection = Connection(sock.getsockname()[1], secrets.token_urlsafe(32), uuid.uuid4().hex)
        from ai_neko.config.providers import ProviderStore
        from ai_neko.runtime import SessionRuntime

        providers = ProviderStore(paths)
        runtime = SessionRuntime(paths, providers)
        bootstrap_code = secrets.token_urlsafe(32) if open_browser else None

        def event(name: str):
            record = {
                "event": name,
                "app_id": APP_ID,
                "instance_id": connection.instance_id,
                "port": connection.port,
            }
            with log_file.open("a", encoding="utf-8") as handle:
                handle.write(json.dumps(record) + "\n")
            print(json.dumps(record), flush=True)

        @asynccontextmanager
        async def lifespan(_app):
            write_private_json(connection_file, connection.descriptor())
            event("started")
            runtime.start_memory_worker()
            if parent_input is not None:
                # A private inherited pipe is an ownership capability, avoiding
                # stale PID checks or attaching to another service's descriptor.
                # This credential message is never written to persistent logs.
                print(json.dumps({"event": "connection", **connection.descriptor()}), flush=True)
                loop = asyncio.get_running_loop()

                def parent_ended():
                    with contextlib.suppress(RuntimeError):
                        # The service has already completed shutdown.
                        loop.call_soon_threadsafe(request_shutdown)

                def watch_parent():
                    try:
                        # Do not hold TextIO/BufferedReader locks in a daemon:
                        # Windows may retain another pipe writer after the GUI
                        # exits, while the HANDLE watcher must still shut down.
                        descriptor = parent_input.fileno()
                        while os.read(descriptor, 4096):
                            pass
                    except (OSError, ValueError):
                        pass
                    parent_ended()

                threading.Thread(target=watch_parent, name="desktop-parent", daemon=True).start()
                if parent_process is not None:
                    parent_process.start(parent_ended)
            if bootstrap_code is not None:
                # Fragments never reach the HTTP server or access logs. The UI
                # consumes the one-use code, clears the URL, and obtains a token.
                asyncio.get_running_loop().call_later(
                    0.5, webbrowser.open, f"{connection.url}/#bootstrap={bootstrap_code}"
                )
            try:
                yield
            finally:
                await runtime.close()
                event("stopped")

        def request_shutdown():
            server.should_exit = True

        app = create_app(
            connection,
            request_shutdown,
            lifespan,
            runtime=runtime,
            providers=providers,
            bootstrap_code=bootstrap_code,
        )
        config = uvicorn.Config(
            app,
            host="127.0.0.1",
            port=connection.port,
            access_log=False,
            log_level="error",
            ws="websockets-sansio",
            ws_max_size=4096,
            timeout_graceful_shutdown=3,
            proxy_headers=False,
            server_header=False,
        )
        server = uvicorn.Server(config)
        server.run(sockets=[sock])
        if not server.started:
            raise RuntimeError("local service failed during startup")
    finally:
        sock.close()
        if connection is not None and connection_file.exists() and not connection_file.is_symlink():
            try:
                current = json.loads(connection_file.read_text(encoding="utf-8"))
                if current.get("instance_id") == connection.instance_id:
                    connection_file.unlink()
            except (OSError, ValueError, AttributeError):
                pass
        lock.release()
