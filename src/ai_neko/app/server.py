"""Single-instance loopback service and isolated local chat interface."""

from __future__ import annotations

import asyncio
import json
import os
import secrets
import socket
import uuid
import webbrowser
from contextlib import asynccontextmanager
from dataclasses import dataclass, field
from pathlib import Path

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
        with temporary.open("x", encoding="utf-8") as handle:
            temporary.chmod(0o600)
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


def serve(paths: DataPaths, settings: Settings, *, open_browser: bool = False) -> None:
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
