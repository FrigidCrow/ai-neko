"""Probe the exact Windows ZIP using its executable, never the source application.

The Python process is only a test harness. Application subprocesses receive a
minimal Windows PATH, no Python environment, and a freshly extracted working
directory with Chinese characters and spaces. No real model calls are made.
"""

from __future__ import annotations

import argparse
import hashlib
import hmac
import json
import os
import platform
import re
import shutil
import socket
import stat
import struct
import subprocess
import sys
import tempfile
import time
import zipfile
from datetime import datetime, timezone
from pathlib import Path, PurePosixPath
from typing import Any, Callable
from urllib.error import HTTPError, URLError
from urllib.request import HTTPRedirectHandler, ProxyHandler, Request, build_opener

from websockets.exceptions import ConnectionClosed, InvalidStatus
from websockets.sync.client import connect

STAGE = "windows-portable-package"
CASES = (
    "archive_layout_and_safe_extraction",
    "windows_x64_execution_environment",
    "paths_in_chinese_directory",
    "executable_self_check",
    "authenticated_health_and_descriptor",
    "http_rejects_missing_and_wrong_token",
    "websocket_origin_auth_and_ping",
    "second_instance_preserves_descriptor",
    "occupied_explicit_port_is_rejected",
    "graceful_stop_removes_descriptor",
    "graph_pause_fresh_process_get_resume_get",
    "graph_user_and_character_scope_separation",
    "archive_unchanged_after_validation",
)
_DEVICE = re.compile(r"^(CON|PRN|AUX|NUL|COM[1-9]|LPT[1-9])(?:\.|$)", re.IGNORECASE)


def require(condition: bool) -> None:
    if not condition:
        raise AssertionError("Package acceptance condition failed")


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def safe_extract(archive: Path, destination: Path) -> Path:
    """Validate all entries before extraction, including Windows path aliases."""
    with zipfile.ZipFile(archive) as bundle:
        entries = bundle.infolist()
        require(bool(entries) and len(entries) <= 20000)
        require(sum(entry.file_size for entry in entries) <= 2 * 1024**3)
        seen: set[str] = set()
        top_levels: set[str] = set()
        for entry in entries:
            name = entry.filename.rstrip("/")
            parts = name.split("/")
            require(bool(name) and not entry.filename.startswith("/"))
            require(not any(char in name for char in '\\:\x00<>"|?*'))
            require(all(part not in {"", ".", ".."} for part in parts))
            require(all(not part.endswith((" ", ".")) for part in parts))
            require(all(not _DEVICE.match(part) for part in parts))
            require(all(not any(ord(char) < 32 for char in part) for part in parts))
            kind = stat.S_IFMT(entry.external_attr >> 16)
            require(kind in {0, stat.S_IFREG, stat.S_IFDIR})
            require(kind != stat.S_IFDIR or entry.is_dir())
            key = name.casefold()
            require(key not in seen)
            seen.add(key)
            top_levels.add(parts[0])
            require(not entry.flag_bits & 1)  # No encrypted or password-protected entries.
            target = destination.joinpath(*PurePosixPath(name).parts).resolve()
            require(destination.resolve() in target.parents)
        require(len(top_levels) == 1)
        for entry in entries:
            target = destination.joinpath(*PurePosixPath(entry.filename).parts)
            if entry.is_dir():
                target.mkdir(parents=True, exist_ok=True)
            else:
                target.parent.mkdir(parents=True, exist_ok=True)
                with bundle.open(entry) as source, target.open("xb") as output:
                    shutil.copyfileobj(source, output)
    root = destination / next(iter(top_levels))
    require((root / "ai-neko.exe").is_file())
    require((root / "_internal").is_dir())
    require((root / "build-info.json").is_file())
    return root


def application_env(environment: dict[str, str]) -> dict[str, str]:
    """Do not inherit installed Python, source imports, settings, or model keys."""
    keep = {"SYSTEMROOT", "WINDIR", "COMSPEC", "TEMP", "TMP", "PATHEXT"}
    result = {key: value for key, value in environment.items() if key.upper() in keep}
    windows = next(
        (value for key, value in environment.items() if key.upper() == "SYSTEMROOT"),
        r"C:\Windows",
    )
    result["PATH"] = ";".join((str(Path(windows) / "System32"), windows))
    result["PYTHONUTF8"] = "1"
    return result


class _NoRedirect(HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


class Probe:
    def __init__(self, archive: Path, evidence: dict[str, Any]) -> None:
        self.archive = archive
        self.evidence = evidence
        self.current_stage = "initialize"
        self.root: Path | None = None
        self.exe: Path | None = None
        self.env = application_env(dict(os.environ))
        self.children: list[subprocess.Popen] = []
        self.connection: dict[str, Any] = {}
        self.server: subprocess.Popen | None = None

    def case(self, name: str, action: Callable[[], Any]) -> Any:
        self.current_stage = name
        record = next(item for item in self.evidence["tests"]["cases"] if item["name"] == name)
        try:
            result = action()
        except Exception:
            record["outcome"] = "FAILED"
            raise
        record["outcome"] = "PASS"
        return result

    def cli(self, *args: str, expected: int = 0) -> subprocess.CompletedProcess[str]:
        require(self.exe is not None and self.root is not None)
        # Popen + explicit tracking ensures timeout cleanup targets only our child.
        process = subprocess.Popen(
            [str(self.exe), *args],
            cwd=self.root,
            env=self.env,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            encoding="utf-8",
            errors="replace",
        )
        self.children.append(process)
        stdout, stderr = process.communicate(timeout=40)
        require(process.returncode == expected)
        return subprocess.CompletedProcess(process.args, process.returncode, stdout, stderr)

    def cli_json(self, *args: str) -> dict[str, Any]:
        value = json.loads(self.cli(*args).stdout)
        require(isinstance(value, dict))
        return value

    def request(self, method: str, path: str, *, token: str | None = None) -> tuple[int, bytes]:
        headers = {} if token is None else {"Authorization": f"Bearer {token}"}
        request = Request(
            self.connection["http_url"] + path,
            method=method,
            data=b"" if method == "POST" else None,
            headers=headers,
        )
        opener = build_opener(ProxyHandler({}), _NoRedirect())
        try:
            with opener.open(request, timeout=3) as response:
                return response.status, response.read()
        except HTTPError as error:
            return error.code, error.read()

    def start_server(self, data: Path) -> None:
        require(self.exe is not None and self.root is not None)
        self.server = subprocess.Popen(
            [str(self.exe), "serve", "--data-dir", str(data), "--port", "0"],
            cwd=self.root,
            env=self.env,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
        self.children.append(self.server)
        descriptor = data / "runtime" / "connection.json"
        deadline = time.monotonic() + 40
        while time.monotonic() < deadline:
            require(self.server.poll() is None)
            try:
                value = json.loads(descriptor.read_text(encoding="utf-8"))
                require(value.get("pid") == self.server.pid)
                require(value.get("app_id") == "ai-neko")
                require(value.get("host") == "127.0.0.1")
                port = value.get("port")
                require(type(port) is int and 0 < port < 65536)
                require(value.get("http_url") == f"http://127.0.0.1:{port}")
                require(value.get("allowed_origin") == value["http_url"])
                require(value.get("ws_url") == f"ws://127.0.0.1:{port}/ws")
                require(isinstance(value.get("token"), str) and len(value["token"]) >= 32)
                self.connection = value
                code, body = self.request("GET", "/health", token=value["token"])
                if code == 200:
                    health = json.loads(body)
                    require(health["app_id"] == "ai-neko" and health["status"] == "ok")
                    return
            except (OSError, ValueError, URLError):
                pass
            time.sleep(0.1)
        raise TimeoutError("Package server readiness timed out")

    def check_http_auth(self) -> None:
        for token in (None, "synthetic-wrong-token"):
            for method, path in (("GET", "/health"), ("POST", "/shutdown")):
                code, _ = self.request(method, path, token=token)
                require(code in {401, 403})
        require(self.server is not None and self.server.poll() is None)

    def check_self_check(self) -> None:
        result = self.cli_json("self-check")
        require(result["app_id"] == "ai-neko" and result["status"] == "passed")
        require(result["real_model_calls"] == 0 and result["external_network_calls"] == 0)
        require(result["user_data_accessed"] is False)

    def check_websocket(self) -> None:
        options = {"open_timeout": 4, "close_timeout": 2, "proxy": None}
        for origin in (None, "https://synthetic-untrusted.invalid"):
            try:
                with connect(self.connection["ws_url"], origin=origin, **options):
                    raise AssertionError("Untrusted origin accepted")
            except InvalidStatus as error:
                require(error.response.status_code == 403)
        for message in (
            {"type": "auth", "token": "synthetic-wrong-token"},
            {"type": "ping"},
        ):
            with connect(
                self.connection["ws_url"], origin=self.connection["allowed_origin"], **options
            ) as ws:
                ws.send(json.dumps(message))
                try:
                    ws.recv(timeout=4)
                    raise AssertionError("Unauthorized websocket accepted")
                except ConnectionClosed as error:
                    require(error.rcvd is not None and error.rcvd.code == 1008)
        with connect(
            self.connection["ws_url"], origin=self.connection["allowed_origin"], **options
        ) as ws:
            ws.send(json.dumps({"type": "auth", "token": self.connection["token"]}))
            require(json.loads(ws.recv(timeout=4))["type"] == "ready")
            ws.send(json.dumps({"type": "ping"}))
            require(json.loads(ws.recv(timeout=4))["type"] == "pong")

    def check_second_instance(self, data: Path) -> None:
        descriptor = data / "runtime" / "connection.json"
        before = descriptor.read_bytes()
        self.cli("serve", "--data-dir", str(data), "--port", "0", expected=3)
        require(hmac.compare_digest(before, descriptor.read_bytes()))
        require(self.request("GET", "/health", token=self.connection["token"])[0] == 200)

    def check_occupied_port(self, data: Path) -> None:
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as occupied:
            if hasattr(socket, "SO_EXCLUSIVEADDRUSE"):
                occupied.setsockopt(socket.SOL_SOCKET, socket.SO_EXCLUSIVEADDRUSE, 1)
            occupied.bind(("127.0.0.1", 0))
            occupied.listen(1)
            self.cli(
                "serve",
                "--data-dir",
                str(data),
                "--port",
                str(occupied.getsockname()[1]),
                expected=4,
            )
        require(not (data / "runtime" / "connection.json").exists())

    def stop_server(self, data: Path) -> None:
        self.cli("stop", "--data-dir", str(data))
        require(self.server is not None)
        require(self.server.wait(timeout=10) == 0)
        require(not (data / "runtime" / "connection.json").exists())

    @staticmethod
    def graph_args(data: Path, user: str = "synthetic-user", character: str = "synthetic-cat"):
        return (
            "graph",
            "--data-root",
            str(data),
            "--user",
            user,
            "--character",
            character,
            "--thread",
            "synthetic-session",
        )

    def check_graph_recovery(self, data: Path) -> dict[str, Any]:
        args = self.graph_args(data)
        paused = self.cli_json(*args, "run", "--text", "package restart evidence", "--pause")
        require(bool(paused["interrupts"]) and paused["values"]["response"] == "")
        recovered = self.cli_json(*args, "get")
        require(recovered["thread_id"] == paused["thread_id"])
        require(recovered["interrupts"] == paused["interrupts"])
        resumed = self.cli_json(*args, "resume", "--response", "fresh executable process")
        expected = "synthetic: package restart evidence | resumed: fresh executable process"
        require(resumed["values"]["response"] == expected and resumed["interrupts"] == [])
        final = self.cli_json(*args, "get")
        require(final["values"]["response"] == expected)
        require(final["thread_id"] == paused["thread_id"])
        return final

    def check_scope_separation(self, data: Path, owner: dict[str, Any]) -> None:
        for user, character in (
            ("other-user", "synthetic-cat"),
            ("synthetic-user", "other-cat"),
        ):
            args = self.graph_args(data, user, character)
            self.cli(*args, "--handle", owner["thread_id"], "get", expected=2)
            other = self.cli_json(*args, "run", "--text", "separate synthetic scope")
            require(other["thread_id"] != owner["thread_id"])
            require(other["values"]["response"] == "synthetic: separate synthetic scope")
        unchanged = self.cli_json(*self.graph_args(data), "get")
        require(unchanged["values"] == owner["values"])

    def cleanup(self) -> None:
        failed = False
        for child in reversed(self.children):
            try:
                if child.poll() is None:
                    child.terminate()
                    try:
                        child.wait(timeout=5)
                    except subprocess.TimeoutExpired:
                        child.kill()
                        child.wait(timeout=5)
            except (OSError, subprocess.TimeoutExpired):
                failed = True
            finally:
                if child.stdout is not None:
                    child.stdout.close()
                if child.stderr is not None:
                    child.stderr.close()
        if failed:
            self.current_stage = "created_process_cleanup"
            raise RuntimeError("One or more created processes could not be cleaned up")

    def prepare_archive(self, temporary: Path) -> None:
        self.root = safe_extract(self.archive, temporary / "解压 程序")
        self.exe = self.root / "ai-neko.exe"
        self.evidence["executable_sha256"] = sha256(self.exe)
        build = json.loads((self.root / "build-info.json").read_text(encoding="utf-8"))
        require(build.get("app_id") == "ai-neko" and build.get("schema_version") == 1)
        commit = build.get("source", {}).get("commit")
        require(isinstance(commit, str) and bool(re.fullmatch(r"[0-9a-f]{40}", commit)))
        self.evidence["source_commit"] = commit

    def run(self, temporary: Path) -> None:
        self.case("archive_layout_and_safe_extraction", lambda: self.prepare_archive(temporary))
        self.case(
            "windows_x64_execution_environment",
            lambda: require(
                os.name == "nt"
                and platform.machine().lower() in {"amd64", "x86_64"}
                and struct.calcsize("P") == 8
            ),
        )
        data = temporary / "合成 数据 ai-neko"
        self.case(
            "paths_in_chinese_directory",
            lambda: require(
                self.cli_json("paths", "--data-dir", str(data))["data_root"] == str(data)
                and not data.exists()
            ),
        )
        self.case("executable_self_check", self.check_self_check)
        self.case("authenticated_health_and_descriptor", lambda: self.start_server(data))
        self.case("http_rejects_missing_and_wrong_token", self.check_http_auth)
        self.case("websocket_origin_auth_and_ping", self.check_websocket)
        self.case("second_instance_preserves_descriptor", lambda: self.check_second_instance(data))
        self.case(
            "occupied_explicit_port_is_rejected",
            lambda: self.check_occupied_port(temporary / "占用端口 数据"),
        )
        self.case("graceful_stop_removes_descriptor", lambda: self.stop_server(data))
        graph_data = temporary / "图恢复 数据"
        owner = self.case(
            "graph_pause_fresh_process_get_resume_get",
            lambda: self.check_graph_recovery(graph_data),
        )
        self.case(
            "graph_user_and_character_scope_separation",
            lambda: self.check_scope_separation(graph_data, owner),
        )
        self.case(
            "archive_unchanged_after_validation",
            lambda: require(sha256(self.archive) == self.evidence["archive_sha256"]),
        )


def environment_info() -> dict[str, Any]:
    version = sys.getwindowsversion() if os.name == "nt" else None
    return {
        "system": platform.system(),
        "release": platform.release(),
        "version": platform.version(),
        "machine": platform.machine(),
        "harness_python": platform.python_version(),
        "harness_pointer_bits": struct.calcsize("P") * 8,
        "windows_product_type": version.product_type if version else None,
        "windows_build": version.build if version else None,
        "application_runtime": "extracted_executable_only",
        "application_path": "windows_system_directories_only",
    }


def validate_output(output: Path, archive: Path, parser: argparse.ArgumentParser) -> None:
    root = Path(__file__).resolve().parents[1]
    if (
        output.suffix.lower() != ".json"
        or output.name in {"connection.json", "build-info.json"}
        or output == archive
        or output.is_symlink()
        or any(
            root / name in output.parents for name in ("src", "tests", "scripts", ".git", ".venv")
        )
    ):
        parser.error("Use a dedicated package evidence JSON file in artifacts/package")
    if output.exists():
        try:
            existing = json.loads(output.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            parser.error("Refusing to overwrite unrelated evidence")
        if not isinstance(existing, dict) or existing.get("stage") != STAGE:
            parser.error("Refusing to overwrite unrelated evidence")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--archive", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    archive = args.archive.expanduser().resolve()
    # Check the unresolved path too: resolve() would hide a final symlink.
    if args.output.expanduser().is_symlink():
        parser.error("Evidence output must not be a symlink")
    output = args.output.expanduser().resolve()
    validate_output(output, archive, parser)
    started = time.monotonic()
    evidence: dict[str, Any] = {
        "schema_version": 1,
        "app_id": "ai-neko",
        "stage": STAGE,
        "status": "FAILED",
        "started_at_utc": datetime.now(timezone.utc).isoformat(),
        "archive_sha256": None,
        "executable_sha256": None,
        "source_commit": None,
        "environment": environment_info(),
        "tests": {
            "kind": "synthetic_deterministic_packaged_executable",
            "real_model_tests": 0,
            "cases": [{"name": name, "outcome": "NOT_RUN"} for name in CASES],
        },
    }
    probe = Probe(archive, evidence)
    try:
        evidence["archive_sha256"] = sha256(archive)
        with tempfile.TemporaryDirectory(prefix="ai-neko 便携包 验证 ") as directory:
            try:
                probe.run(Path(directory).resolve())
            finally:
                probe.cleanup()
        evidence["status"] = "PASS"
    except (Exception, KeyboardInterrupt) as error:
        evidence["failure"] = {"stage": probe.current_stage, "error_class": type(error).__name__}
    environment = evidence["environment"]
    is_windows = environment["system"] == "Windows"
    is_server = is_windows and environment["windows_product_type"] in {2, 3}
    is_windows_11 = (
        is_windows
        and environment["windows_product_type"] == 1
        and environment["windows_build"] >= 22000
    )
    evidence["scope"] = {
        "packaged_service_and_synthetic_graph": evidence["status"],
        "windows_server_x64_execution": evidence["status"] if is_server else "NOT_VERIFIED",
        "windows_11_x64_execution": evidence["status"] if is_windows_11 else "NOT_VERIFIED",
        "desktop_tray_audio": "NOT_TESTED",
        "real_model_search_and_chat": "NOT_TESTED",
    }
    evidence["duration_seconds"] = round(time.monotonic() - started, 3)
    evidence["failure_detail_policy"] = (
        "No raw stdout, stderr, logs, tokens, or tracebacks included."
    )
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(evidence, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    passed = sum(item["outcome"] == "PASS" for item in evidence["tests"]["cases"])
    print(f"Windows portable package {evidence['status']}: {passed}/{len(CASES)} checks passed")
    if "failure" in evidence:
        print(f"Stage: {evidence['failure']['stage']}; error: {evidence['failure']['error_class']}")
    print(f"Evidence: {output}")
    return 0 if evidence["status"] == "PASS" else 1


if __name__ == "__main__":
    raise SystemExit(main())
