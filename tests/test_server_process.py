"""M0 black-box acceptance against real, isolated Python processes.

All data are synthetic. No reference-repository path or model credentials are read.
"""

from __future__ import annotations

import hmac
import json
import os
import socket
import subprocess
import sys
import time
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import httpx
import pytest
from websockets.exceptions import ConnectionClosed, InvalidStatus
from websockets.sync.client import connect

PROJECT_ROOT = Path(__file__).resolve().parents[1]
START_TIMEOUT = 12.0


def cli(*args: str) -> subprocess.CompletedProcess[str]:
    env = os.environ.copy()
    env.pop("AI_NEKO_DATA_DIR", None)
    env.pop("AI_NEKO_MODEL_API_KEY", None)
    return subprocess.run(
        [sys.executable, "-m", "ai_neko", *args],
        cwd=PROJECT_ROOT,
        env=env,
        capture_output=True,
        text=True,
        encoding="utf-8",
        timeout=15,
        check=False,
    )


class ServerProcess:
    def __init__(self, root: Path, log_path: Path) -> None:
        self.root = root
        self.log_path = log_path
        self.descriptor = root / "runtime" / "connection.json"
        self.connection: dict[str, Any] = {}
        self._log = log_path.open("w", encoding="utf-8")
        env = os.environ.copy()
        env.pop("AI_NEKO_DATA_DIR", None)
        env.pop("AI_NEKO_MODEL_API_KEY", None)
        env["PYTHONUTF8"] = "1"
        self.process = subprocess.Popen(
            [sys.executable, "-m", "ai_neko", "serve", "--data-dir", str(root), "--port", "0"],
            cwd=PROJECT_ROOT,
            env=env,
            stdout=self._log,
            stderr=subprocess.STDOUT,
        )

    @property
    def auth(self) -> dict[str, str]:
        return {"Authorization": f"Bearer {self.connection['token']}"}

    def request(self, method: str, path: str, **kwargs: Any) -> httpx.Response:
        with httpx.Client(trust_env=False, timeout=2.0) as client:
            return client.request(method, self.connection["http_url"] + path, **kwargs)

    def wait_ready(self) -> None:
        deadline = time.monotonic() + START_TIMEOUT
        while time.monotonic() < deadline:
            if self.process.poll() is not None:
                pytest.fail(f"Server exited before readiness (exit {self.process.returncode})")
            try:
                connection = json.loads(self.descriptor.read_text(encoding="utf-8"))
                # A stale descriptor must not be mistaken for this process's startup.
                if connection.get("pid") != self.process.pid:
                    time.sleep(0.05)
                    continue
                self.connection = connection
                response = self.request("GET", "/health", headers=self.auth)
                if response.status_code == 200:
                    return
            except (OSError, ValueError, KeyError, httpx.HTTPError):
                pass
            time.sleep(0.05)
        pytest.fail("Server did not become healthy within 12 seconds")

    def close(self) -> None:
        if self.process.poll() is None and self.connection:
            try:
                self.request("POST", "/shutdown", headers=self.auth)
                self.process.wait(timeout=5)
            except (httpx.HTTPError, subprocess.TimeoutExpired):
                pass
        if self.process.poll() is None:
            self.process.terminate()
            try:
                self.process.wait(timeout=3)
            except subprocess.TimeoutExpired:
                self.process.kill()
                self.process.wait(timeout=3)
        self._log.close()

    def crash(self) -> None:
        self.process.kill()
        self.process.wait(timeout=5)
        self._log.close()


@pytest.fixture
def server_factory(tmp_path: Path) -> Iterator[Any]:
    servers: list[ServerProcess] = []

    def start(root: Path | None = None) -> ServerProcess:
        instance = ServerProcess(
            root or tmp_path / f"合成数据 ai-neko {len(servers)}",
            tmp_path / f"process-{len(servers)}.log",
        )
        servers.append(instance)
        instance.wait_ready()
        return instance

    yield start
    for instance in reversed(servers):
        instance.close()


def test_health_descriptor_and_graceful_cli_stop(server_factory: Any) -> None:
    server = server_factory()
    connection = server.connection
    assert connection["app_id"] == "ai-neko"
    assert connection["host"] == "127.0.0.1"
    assert 0 < connection["port"] < 65536
    assert connection["http_url"] == f"http://127.0.0.1:{connection['port']}"
    assert connection["ws_url"] == connection["http_url"].replace("http:", "ws:") + "/ws"
    assert connection["allowed_origin"] == connection["http_url"]
    assert isinstance(connection["instance_id"], str)
    assert len(connection["instance_id"]) >= 16
    assert len(connection["token"]) >= 32
    response = server.request("GET", "/health", headers=server.auth)
    assert response.json()["status"] == "ok"
    assert response.json()["app_id"] == "ai-neko"

    result = cli("stop", "--data-dir", str(server.root))
    assert result.returncode == 0
    server.process.wait(timeout=5)
    assert server.process.returncode == 0
    assert not server.descriptor.exists()


@pytest.mark.parametrize("headers", [{}, {"Authorization": "Bearer synthetic-wrong-token"}])
def test_http_requires_token(server_factory: Any, headers: dict[str, str]) -> None:
    server = server_factory()
    for method, path in [("GET", "/health"), ("POST", "/shutdown")]:
        response = server.request(method, path, headers=headers)
        assert response.status_code in {401, 403}
    assert server.process.poll() is None
    assert server.request("GET", "/health", headers=server.auth).status_code == 200


def test_websocket_auth_and_ping(server_factory: Any) -> None:
    server = server_factory()
    with connect(
        server.connection["ws_url"],
        origin=server.connection["allowed_origin"],
        open_timeout=3,
        close_timeout=2,
        proxy=None,
    ) as ws:
        ws.send(json.dumps({"type": "auth", "token": server.connection["token"]}))
        assert json.loads(ws.recv(timeout=3))["type"] == "ready"
        ws.send(json.dumps({"type": "ping"}))
        assert json.loads(ws.recv(timeout=3))["type"] == "pong"


@pytest.mark.parametrize("origin", [None, "https://synthetic-untrusted.invalid"])
def test_websocket_rejects_missing_or_wrong_origin(server_factory: Any, origin: str | None) -> None:
    server = server_factory()
    with pytest.raises(InvalidStatus) as error:
        with connect(server.connection["ws_url"], origin=origin, open_timeout=3, proxy=None):
            pytest.fail("Untrusted WebSocket origin was accepted")
    assert error.value.response.status_code == 403


def test_websocket_rejects_wrong_token(server_factory: Any) -> None:
    server = server_factory()
    with connect(
        server.connection["ws_url"],
        origin=server.connection["allowed_origin"],
        open_timeout=3,
        close_timeout=2,
        proxy=None,
    ) as ws:
        ws.send(json.dumps({"type": "auth", "token": "synthetic-wrong-token"}))
        with pytest.raises(ConnectionClosed) as error:
            ws.recv(timeout=3)
        assert error.value.rcvd is not None
        assert error.value.rcvd.code == 1008


def test_websocket_requires_auth_before_ping(server_factory: Any) -> None:
    server = server_factory()
    with connect(
        server.connection["ws_url"],
        origin=server.connection["allowed_origin"],
        open_timeout=3,
        close_timeout=2,
        proxy=None,
    ) as ws:
        ws.send(json.dumps({"type": "ping"}))
        with pytest.raises(ConnectionClosed) as error:
            ws.recv(timeout=3)
        assert error.value.rcvd is not None
        assert error.value.rcvd.code == 1008


def test_websocket_rejects_token_in_query_string(server_factory: Any) -> None:
    server = server_factory()
    with pytest.raises(InvalidStatus) as error:
        with connect(
            server.connection["ws_url"] + "?token=synthetic-query-token",
            origin=server.connection["allowed_origin"],
            open_timeout=3,
            proxy=None,
        ):
            pytest.fail("WebSocket token query was accepted")
    assert error.value.response.status_code == 403


def test_http_rejects_untrusted_host(server_factory: Any) -> None:
    server = server_factory()
    response = server.request(
        "GET",
        "/health",
        headers={**server.auth, "Host": "synthetic-rebinding.invalid"},
    )
    assert response.status_code == 400


def test_shutdown_closes_active_websocket(server_factory: Any) -> None:
    server = server_factory()
    with connect(
        server.connection["ws_url"],
        origin=server.connection["allowed_origin"],
        open_timeout=3,
        close_timeout=2,
        proxy=None,
    ) as ws:
        ws.send(json.dumps({"type": "auth", "token": server.connection["token"]}))
        assert json.loads(ws.recv(timeout=3))["type"] == "ready"
        response = server.request("POST", "/shutdown", headers=server.auth)
        assert response.status_code in {200, 202}
        with pytest.raises(ConnectionClosed):
            ws.recv(timeout=5)
    server.process.wait(timeout=5)
    assert server.process.returncode == 0
    assert not server.descriptor.exists()


def test_websocket_auth_timeout(server_factory: Any) -> None:
    server = server_factory()
    with connect(
        server.connection["ws_url"],
        origin=server.connection["allowed_origin"],
        open_timeout=3,
        close_timeout=2,
        proxy=None,
    ) as ws:
        with pytest.raises(ConnectionClosed) as error:
            ws.recv(timeout=7)
        assert error.value.rcvd is not None
        assert error.value.rcvd.code == 1008


def test_invalid_roots_are_rejected(tmp_path: Path) -> None:
    occupied_file = tmp_path / "synthetic-file"
    occupied_file.write_text("synthetic sentinel", encoding="utf-8")
    for root in ["relative-synthetic-root", str(occupied_file)]:
        result = cli("serve", "--data-dir", root, "--port", "0")
        assert result.returncode == 2
    assert occupied_file.read_text(encoding="utf-8") == "synthetic sentinel"
    assert not (PROJECT_ROOT / "relative-synthetic-root").exists()


def test_occupied_explicit_port_is_rejected(tmp_path: Path) -> None:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as occupied:
        occupied.bind(("127.0.0.1", 0))
        occupied.listen(1)
        port = occupied.getsockname()[1]
        root = tmp_path / "occupied-port"
        result = cli("serve", "--data-dir", str(root), "--port", str(port))
        assert result.returncode == 4
        assert not (root / "runtime" / "connection.json").exists()


def test_second_instance_cannot_replace_running_descriptor(server_factory: Any) -> None:
    server = server_factory()
    before = server.descriptor.read_bytes()
    result = cli("serve", "--data-dir", str(server.root), "--port", "0")
    assert result.returncode == 3
    descriptor_preserved = hmac.compare_digest(server.descriptor.read_bytes(), before)
    assert descriptor_preserved, "Second instance modified running descriptor"
    assert server.request("GET", "/health", headers=server.auth).status_code == 200


def test_two_data_roots_coexist_and_cleanup_independently(server_factory: Any) -> None:
    first = server_factory()
    second = server_factory()
    assert first.connection["port"] != second.connection["port"]
    same_token = hmac.compare_digest(first.connection["token"], second.connection["token"])
    assert not same_token, "Separate instances must have distinct session tokens"
    assert second.request("GET", "/health", headers=first.auth).status_code in {401, 403}
    response = first.request("POST", "/shutdown", headers=first.auth)
    assert response.status_code in {200, 202}
    first.process.wait(timeout=5)
    assert first.process.returncode == 0
    assert not first.descriptor.exists()
    assert second.descriptor.exists()
    assert second.request("GET", "/health", headers=second.auth).status_code == 200


def test_crash_restart_replaces_stale_descriptor_and_token(server_factory: Any) -> None:
    first = server_factory()
    old_token = first.connection["token"]
    old_instance = first.connection["instance_id"]
    first.crash()
    assert first.descriptor.exists(), "Crash test requires an actual stale descriptor"
    second = server_factory(first.root)
    assert second.connection["instance_id"] != old_instance
    token_reused = hmac.compare_digest(second.connection["token"], old_token)
    assert not token_reused, "Restart reused the previous session token"
    assert second.request(
        "GET",
        "/health",
        headers={"Authorization": f"Bearer {old_token}"},
    ).status_code in {401, 403}


def test_session_token_is_absent_from_process_and_persistent_logs(server_factory: Any) -> None:
    server = server_factory()
    assert server.request("GET", "/health", headers=server.auth).status_code == 200
    token = server.connection["token"]
    result = cli("stop", "--data-dir", str(server.root))
    assert result.returncode == 0
    server.process.wait(timeout=5)
    server._log.flush()
    outputs = [server.log_path.read_text(encoding="utf-8"), result.stdout, result.stderr]
    outputs.extend(
        path.read_text(encoding="utf-8", errors="replace")
        for path in (server.root / "logs").rglob("*")
        if path.is_file()
    )
    token_leaked = any(token in output for output in outputs)
    assert not token_leaked, "A session token appeared in process output or logs"
