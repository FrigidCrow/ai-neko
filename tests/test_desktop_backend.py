"""Desktop ownership is a private inherited pipe, never a stale descriptor PID."""

from __future__ import annotations

import json
import os
import queue
import subprocess
import sys
import threading

import httpx
import pytest

from ai_neko.config.paths import DataRootError, initialize_data_root


def child_env():
    return {key: value for key, value in os.environ.items() if not key.startswith("AI_NEKO_")}


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
