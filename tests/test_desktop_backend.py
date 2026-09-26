"""Desktop ownership uses a private pipe and a captured Windows process HANDLE."""

from __future__ import annotations

import json
import os
import queue
import subprocess
import sys
import textwrap
import threading
import time

import httpx
import pytest

from ai_neko.config.paths import DataRootError, initialize_data_root


class FakeKernel32:
    """Model a captured process object separately from future PID availability."""

    def __init__(self, *, opened=True, alive=True):
        self.opened = opened
        self.exited = threading.Event()
        if not alive:
            self.exited.set()
        self.opens = []
        self.waits = []
        self.closed = []
        self.waiting = threading.Event()
        self.failed_wait = False

    def OpenProcess(self, access, inherit, pid):
        self.opens.append((access, inherit, pid))
        return 123456 if self.opened else None

    def WaitForSingleObject(self, handle, timeout):
        assert handle == 123456
        self.waits.append((handle, timeout))
        if timeout:
            self.waiting.set()
        try:
            exited = self.exited.wait(timeout / 1000)
            if self.failed_wait:
                return 0xFFFFFFFF
            return 0 if exited else 0x102
        finally:
            self.waiting.clear()

    def CloseHandle(self, handle):
        assert not self.waiting.is_set(), "must not close a HANDLE with a pending wait"
        self.closed.append(handle)
        return True


def child_env():
    return {key: value for key, value in os.environ.items() if not key.startswith("AI_NEKO_")}


@pytest.mark.usefixtures("sandbox_compatible")
def test_paths_initialize_keeps_ownership_and_returns_desktop_root(tmp_path):
    root = tmp_path / "猫娘 data"
    result = subprocess.run(
        [sys.executable, "-m", "ai_neko", "paths", "--initialize", "--data-dir", str(root)],
        capture_output=True,
        text=True,
        encoding="utf-8",
        timeout=20,
        env=child_env(),
        check=True,
    )
    value = json.loads(result.stdout)
    assert value == {
        "app_id": "ai-neko",
        "data_root": str(root),
        "desktop_root": str(root / "desktop"),
    }
    assert (root / "desktop").is_dir()
    assert initialize_data_root(root).root == root


def test_paths_initialize_rejects_foreign_directory(tmp_path):
    root = tmp_path / "foreign"
    root.mkdir()
    (root / "foreign.txt").write_text("other application")
    result = subprocess.run(
        [sys.executable, "-m", "ai_neko", "paths", "--initialize", "--data-dir", str(root)],
        capture_output=True,
        timeout=20,
        env=child_env(),
    )
    assert result.returncode == 2
    assert not (root / "desktop").exists()
    with pytest.raises(DataRootError):
        initialize_data_root(root)


def test_desktop_pipe_disconnect_stops_own_service_and_keeps_token_private(tmp_path):
    root = tmp_path / "owned"
    records: queue.Queue = queue.Queue()
    with (tmp_path / "stderr.txt").open("w", encoding="utf-8") as errors:
        process = subprocess.Popen(
            [
                sys.executable,
                "-m",
                "ai_neko",
                "serve",
                "--desktop-parent",
                "--desktop-parent-pid",
                str(os.getpid()),
                "--data-dir",
                str(root),
            ],
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=errors,
            text=True,
            encoding="utf-8",
            env=child_env(),
        )

        def read_messages():
            for line in process.stdout:
                try:
                    record = json.loads(line)
                except ValueError:
                    continue
                if record.get("event") == "connection":
                    records.put(record)

        reader = threading.Thread(target=read_messages, daemon=True)
        reader.start()
        try:
            descriptor = records.get(timeout=25)
            assert descriptor["app_id"] == "ai-neko"
            assert descriptor["host"] == "127.0.0.1"
            with httpx.Client(trust_env=False, timeout=3) as client:
                response = client.get(
                    descriptor["http_url"] + "/health",
                    headers={"Authorization": "Bearer " + descriptor["token"]},
                )
            assert response.status_code == 200
            assert process.poll() is None
            process.stdin.close()  # Same EOF as an abruptly terminated Electron parent.
            assert process.wait(timeout=12) == 0
            assert not (root / "runtime" / "connection.json").exists()
            logs = (root / "logs" / "service.jsonl").read_text()
            assert descriptor["token"] not in logs
            assert [json.loads(line)["event"] for line in logs.splitlines()] == [
                "started",
                "stopped",
            ]
        finally:
            if process.stdin and not process.stdin.closed:
                process.stdin.close()
            try:
                process.wait(timeout=12)
            except subprocess.TimeoutExpired:
                if os.name == "nt":
                    subprocess.run(
                        ["taskkill", "/PID", str(process.pid), "/T", "/F"],
                        capture_output=True,
                        timeout=10,
                    )
                else:
                    process.kill()
                process.wait(timeout=10)
            reader.join(timeout=2)
            process.stdout.close()
    assert descriptor["token"] not in (tmp_path / "stderr.txt").read_text()


@pytest.mark.parametrize(
    "flags",
    [
        ["--desktop-parent-pid", "123"],
        ["--desktop-parent", "--desktop-parent-pid", "0"],
        ["--desktop-parent", "--desktop-parent-pid", "-1"],
        ["--desktop-parent", "--desktop-parent-pid", "4294967296"],
        ["--desktop-parent", "--desktop-parent-pid", "not-a-pid"],
    ],
)
def test_desktop_parent_pid_rejects_invalid_modes_before_creating_data(tmp_path, flags):
    root = tmp_path / "must-not-create"
    result = subprocess.run(
        [sys.executable, "-m", "ai_neko", "serve", "--data-dir", str(root), *flags],
        input=b"",
        capture_output=True,
        timeout=20,
        env=child_env(),
    )
    assert result.returncode == 2
    assert not root.exists()


def test_parent_handle_is_captured_once_and_failure_to_open_or_dead_parent_is_rejected(monkeypatch):
    from ai_neko.app import server

    for kernel in (FakeKernel32(opened=False), FakeKernel32(alive=False)):
        monkeypatch.setattr(server, "_windows_kernel32", lambda kernel=kernel: kernel)
        with pytest.raises(ValueError, match="desktop parent process"):
            server.WindowsParentProcess(123)
        assert kernel.opens == [(0x00100000, False, 123)]
        assert kernel.closed == ([123456] if kernel.opened else [])
    kernel = FakeKernel32()
    monkeypatch.setattr(server, "_windows_kernel32", lambda: kernel)
    for pid in (0, -1, 0x100000000, True, os.getpid()):
        with pytest.raises(ValueError, match="invalid desktop parent"):
            server.WindowsParentProcess(pid)
    assert not kernel.opens


@pytest.mark.parametrize("wait_failed", [False, True])
def test_parent_handle_shutdown_uses_captured_object_even_if_pid_lookup_would_change(
    monkeypatch, wait_failed
):
    from ai_neko.app import server

    kernel = FakeKernel32()
    monkeypatch.setattr(server, "_windows_kernel32", lambda: kernel)
    parent = server.WindowsParentProcess(123)
    ended = threading.Event()
    parent.start(ended.set)
    assert kernel.waiting.wait(2)
    # Any future OpenProcess would fail. Monitoring must continue on the held object.
    kernel.opened = False
    kernel.failed_wait = wait_failed
    kernel.exited.set()
    assert ended.wait(2)
    parent.close()
    parent.close()  # Cleanup is idempotent.
    assert kernel.opens == [(0x00100000, False, 123)]
    assert kernel.closed == [123456]


def test_parent_handle_normal_close_waits_for_pending_wait_without_reporting_parent_exit(
    monkeypatch,
):
    from ai_neko.app import server

    kernel = FakeKernel32()
    monkeypatch.setattr(server, "_windows_kernel32", lambda: kernel)
    parent = server.WindowsParentProcess(123)
    ended = threading.Event()
    parent.start(ended.set)
    assert kernel.waiting.wait(2)
    parent.close()
    assert not ended.is_set()
    assert kernel.closed == [123456]


def test_parent_death_monitor_with_still_open_stdin_uses_native_handle_on_windows(tmp_path):
    """Real Win32 on Windows; an explicit fake HANDLE in a real service elsewhere."""
    sentinel = subprocess.Popen(
        [sys.executable, "-c", "import time; time.sleep(60)"],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    root = tmp_path / "handle-owned"
    marker = tmp_path / "synthetic-parent-alive"
    marker.touch()
    command = [
        sys.executable,
        "-m",
        "ai_neko",
        "serve",
        "--desktop-parent",
        "--desktop-parent-pid",
        str(sentinel.pid),
        "--data-dir",
        str(root),
    ]
    if os.name != "nt":
        # Exercise the complete server shutdown with stdin still blocked on
        # non-Windows hosts too, without claiming this stub is a native API test.
        fixture = textwrap.dedent("""
            import sys, time
            from pathlib import Path
            from ai_neko.app import server
            from ai_neko.config.paths import initialize_data_root
            from ai_neko.config.settings import Settings
            marker = Path(sys.argv[2])
            class SyntheticKernel:
                def OpenProcess(self, access, inherit, pid):
                    return 123456
                def WaitForSingleObject(self, handle, milliseconds):
                    time.sleep(milliseconds / 1000)
                    return 0x102 if marker.exists() else 0
                def CloseHandle(self, handle):
                    return True
            server._windows_kernel32 = SyntheticKernel
            parent = server.WindowsParentProcess(int(sys.argv[3]))
            try:
                server.serve(initialize_data_root(sys.argv[1]), Settings.load(0),
                             parent_input=sys.stdin, parent_process=parent)
            finally:
                parent.close()
        """)
        command = [sys.executable, "-c", fixture, str(root), str(marker), str(sentinel.pid)]
    with (tmp_path / "handle-out.txt").open("w") as output:
        backend = subprocess.Popen(
            command,
            stdin=subprocess.PIPE,
            stdout=output,
            stderr=subprocess.STDOUT,
            env=child_env(),
        )
        try:
            descriptor = root / "runtime" / "connection.json"
            deadline = time.monotonic() + 25
            while not descriptor.exists() and backend.poll() is None:
                assert time.monotonic() < deadline
                time.sleep(0.05)
            assert descriptor.exists()
            assert backend.poll() is None
            connection = json.loads(descriptor.read_text())
            with httpx.Client(trust_env=False, timeout=5) as client:
                assert (
                    client.get(
                        connection["http_url"] + "/health",
                        headers={"Authorization": "Bearer " + connection["token"]},
                    ).status_code
                    == 200
                )
            sentinel.kill()
            sentinel.wait(timeout=10)
            marker.unlink()
            # Keep stdin's writer open: only the captured process HANDLE can end it.
            assert not backend.stdin.closed
            assert backend.wait(timeout=12) == 0
            assert not descriptor.exists()
        finally:
            if sentinel.poll() is None:
                sentinel.kill()
                sentinel.wait(timeout=10)
            backend.stdin.close()
            if backend.poll() is None:
                if os.name == "nt":
                    subprocess.run(
                        ["taskkill", "/PID", str(backend.pid), "/T", "/F"],
                        capture_output=True,
                        timeout=10,
                    )
                else:
                    backend.kill()
                backend.wait(timeout=10)
