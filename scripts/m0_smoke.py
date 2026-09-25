"""Run synthetic M0 and M1 tests and emit portable evidence without logs or credentials."""

from __future__ import annotations

import argparse
import hashlib
import json
import math
import os
import platform
import re
import signal
import struct
import subprocess
import sys
import tempfile
import time
import tomllib
import xml.etree.ElementTree as ET
from datetime import datetime, timezone
from importlib import metadata
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
TEST_TIMEOUT_SECONDS = 600


def expected_platform_skips(results: dict, system: str) -> bool:
    """Only the explicit Windows-vault probe may be inapplicable off Windows.

    Keep the report PARTIAL and its skip count: CI eligibility does not turn an
    unexecuted Windows API call into a successful Linux/Mac test.
    """
    skipped = [case for case in results.get("cases", []) if case["outcome"] == "skipped"]
    return (
        system != "Windows"
        and results.get("skipped") == 1
        and len(skipped) == 1
        and skipped[0].get("class") == "tests.test_provider_config"
        and skipped[0].get("name") == "test_windows_vault_real_roundtrip_and_delete"
    )


def safe_test_name(value: str) -> str:
    """Retain a source function name, never parameter IDs or arbitrary node text."""
    name = value.split("[", 1)[0].rsplit("::", 1)[-1]
    return name if re.fullmatch(r"test_[A-Za-z0-9_]+", name) else "redacted_test"


class ProgressRecorder:
    """Explicit pytest plugin; only allowlisted fields reach the append-only file."""

    def __init__(self, path: Path) -> None:
        self.path = path
        self.index = 0
        self.name = ""
        self.outcome = "incomplete"
        self.started = 0.0
        self.path.write_text("", encoding="utf-8")

    def write(self, phase: str, outcome: str, seconds: float = 0) -> None:
        record = {
            "test_index": self.index,
            "name": self.name,
            "phase": phase,
            "outcome": outcome,
            "seconds": round(max(0.0, seconds), 6),
        }
        # Close after each record so termination cannot strand Python-buffered progress.
        with self.path.open("a", encoding="utf-8") as stream:
            stream.write(json.dumps(record, separators=(",", ":")) + "\n")

    def pytest_sessionstart(self, session) -> None:
        self.write("session", "started")

    def pytest_collection_finish(self, session) -> None:
        self.write("collection", "complete")

    def pytest_runtest_logstart(self, nodeid, location) -> None:
        self.index += 1
        self.name = safe_test_name(nodeid)
        self.outcome = "incomplete"
        self.started = time.monotonic()
        self.write("start", "started")

    def pytest_runtest_setup(self, item) -> None:
        self.write("setup", "started")

    def pytest_runtest_call(self, item) -> None:
        self.write("call", "started")

    def pytest_runtest_teardown(self, item, nextitem) -> None:
        self.write("teardown", "started")

    def pytest_runtest_logreport(self, report) -> None:
        if report.when not in {"setup", "call", "teardown"}:
            return
        if report.failed:
            self.outcome = "failed" if report.when == "call" else "errors"
        elif report.skipped and self.outcome not in {"failed", "errors"}:
            self.outcome = "skipped"
        elif report.when == "call" and report.passed and self.outcome == "incomplete":
            self.outcome = "passed"
        # Never serialize longrepr, sections, user_properties, location, or report.nodeid.
        self.write(report.when, report.outcome, report.duration)

    def pytest_runtest_logfinish(self, nodeid, location) -> None:
        self.write("finish", self.outcome, time.monotonic() - self.started)

    def pytest_sessionfinish(self, session, exitstatus) -> None:
        self.name = ""
        self.write("session", "complete")


def progress_summary(path: Path) -> dict:
    events = []
    malformed = 0
    try:
        lines = path.read_text(encoding="utf-8").splitlines()
    except OSError:
        lines = []
    for line in lines:
        try:
            raw = json.loads(line)
            phase, outcome, seconds = raw["phase"], raw["outcome"], raw["seconds"]
            if (
                phase
                not in {"session", "collection", "start", "setup", "call", "teardown", "finish"}
                or outcome
                not in {
                    "started",
                    "complete",
                    "passed",
                    "failed",
                    "errors",
                    "skipped",
                    "incomplete",
                }
                or type(raw["test_index"]) is not int
                or raw["test_index"] < 0
                or type(seconds) not in {int, float}
                or not math.isfinite(seconds)
                or seconds < 0
            ):
                raise ValueError
            events.append(
                {
                    "test_index": raw["test_index"],
                    "name": safe_test_name(str(raw["name"])) if raw["name"] else "",
                    "phase": phase,
                    "outcome": outcome,
                    "seconds": seconds,
                }
            )
        except (ValueError, KeyError, TypeError):
            malformed += 1
    started = [item for item in events if item["phase"] == "start"]
    completed = [item for item in events if item["phase"] == "finish"]
    last_started = started[-1] if started else None
    finished_indices = {item["test_index"] for item in completed}
    active = (
        last_started
        if last_started and last_started["test_index"] not in finished_indices
        else None
    )
    return {
        "diagnostic_only": True,
        "session_finished": any(
            item["phase"] == "session" and item["outcome"] == "complete" for item in events
        ),
        "last_started": last_started,
        "active_test": active,
        "last_event": events[-1] if events else None,
        "completed": len(completed),
        "completed_counts": {
            outcome: sum(item["outcome"] == outcome for item in completed)
            for outcome in ("passed", "failed", "errors", "skipped", "incomplete")
        },
        "events": events,
        "incomplete_or_invalid_records": malformed,
    }


def sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def source_identity() -> dict:
    # Explicit inclusion avoids credentials, user data, virtualenvs and generated reports.
    files = [
        ROOT / name
        for name in ("pyproject.toml", "uv.lock", ".python-version", ".gitattributes", "AGENTS.md")
    ]
    for directory, patterns in {
        "src": ["*.py", "*.html", "*.css", "*.js"],
        "tests": ["*.py"],
        "scripts": ["*.py", "*.ps1"],
        "packaging": ["*"],
        ".github/workflows": ["*.yml", "*.yaml"],
    }.items():
        for pattern in patterns:
            files.extend((ROOT / directory).rglob(pattern))
    manifest = [
        {"path": path.relative_to(ROOT).as_posix(), "sha256": sha256(path)}
        for path in sorted(set(files))
        if path.is_file() and "__pycache__" not in path.parts
    ]
    encoded = json.dumps(manifest, sort_keys=True, separators=(",", ":")).encode("utf-8")
    commit = None
    dirty = None
    try:
        result = subprocess.run(
            ["git", "rev-parse", "HEAD"],
            cwd=ROOT,
            capture_output=True,
            text=True,
            timeout=5,
            check=False,
        )
        if result.returncode == 0:
            commit = result.stdout.strip()
        status = subprocess.run(
            ["git", "status", "--porcelain"],
            cwd=ROOT,
            capture_output=True,
            text=True,
            timeout=5,
            check=False,
        )
        if status.returncode == 0:
            dirty = bool(status.stdout.strip())
    except (OSError, subprocess.TimeoutExpired):
        pass
    return {
        "git_commit": commit,
        "git_dirty": dirty,
        "tree_sha256": hashlib.sha256(encoded).hexdigest(),
        "manifest": manifest,
    }


def dependency_versions() -> dict:
    lock_path = ROOT / "uv.lock"
    packages = tomllib.loads(lock_path.read_text(encoding="utf-8")).get("package", [])
    records = []
    mismatches = []
    for package in packages:
        name = package["name"]
        locked = package.get("version")
        try:
            installed = metadata.version(name)
        except metadata.PackageNotFoundError:
            installed = None
        # uv.lock may contain optional or platform-specific packages absent on this host.
        source = package.get("source", {})
        is_local = "editable" in source or "virtual" in source
        records.append(
            {"name": name, "locked": locked, "installed": installed, "local_project": is_local}
        )
        if installed is not None and installed != locked:
            mismatches.append(name)
    return {
        "lock_sha256": sha256(lock_path),
        "packages": records,
        "installed_version_mismatches": mismatches,
    }


def junit_summary(path: Path) -> dict:
    empty = {"collected": 0, "passed": 0, "failed": 0, "errors": 0, "skipped": 0, "cases": []}
    if not path.exists():
        return {**empty, "junit_status": "NOT_WRITTEN"}
    try:
        document = ET.parse(path)
    except (ET.ParseError, OSError):
        return {**empty, "junit_status": "INCOMPLETE"}
    cases = []
    counts = {"collected": 0, "passed": 0, "failed": 0, "errors": 0, "skipped": 0}
    for node in document.iter("testcase"):
        outcome = "passed"
        for xml_name, result_name in [
            ("failure", "failed"),
            ("error", "errors"),
            ("skipped", "skipped"),
        ]:
            if node.find(xml_name) is not None:
                outcome = result_name
                break
        counts["collected"] += 1
        counts[outcome] += 1
        # Deliberately omit failure bodies, stdout and stderr, which may contain locals.
        cases.append(
            {
                "name": safe_test_name(node.get("name", "")),
                "class": node.get("classname"),
                "seconds": float(node.get("time", "0")),
                "outcome": outcome,
            }
        )
    return {**counts, "cases": cases, "junit_status": "COMPLETE"}


def validate_output(output: Path, parser: argparse.ArgumentParser) -> None:
    protected = [ROOT / name for name in ("src", "tests", "scripts", ".git", ".venv")]
    protected_docs = (
        ROOT / "docs" in output.parents and ROOT / "docs" / "evidence" / "m0" not in output.parents
    )
    if (
        output.suffix.lower() != ".json"
        or output.name == "connection.json"
        or protected_docs
        or any(root in output.parents for root in protected)
    ):
        parser.error("Use an evidence .json file, preferably artifacts/m0 or docs/evidence/m0")
    if output.exists():
        try:
            existing = json.loads(output.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            parser.error("Refusing to overwrite a file that is not M0 evidence")
        if (
            not isinstance(existing, dict)
            or existing.get("app_id") != "ai-neko"
            or existing.get("stage") != "M0"
            or existing.get("schema_version") != 1
        ):
            parser.error("Refusing to overwrite a file that is not M0 evidence")


def terminate_test_tree(process: subprocess.Popen) -> None:
    if os.name == "nt":
        subprocess.run(
            ["taskkill", "/PID", str(process.pid), "/T", "/F"],
            capture_output=True,
            timeout=10,
            check=False,
        )
    else:
        try:
            os.killpg(process.pid, signal.SIGKILL)
        except ProcessLookupError:
            pass
    process.wait(timeout=5)


def run_pytest(
    junit_path: Path,
    progress_path: Path,
    targets: tuple[str, ...] = ("tests",),
    timeout: float = TEST_TIMEOUT_SECONDS,
) -> tuple[int, dict, dict]:
    command = [
        sys.executable,
        str(Path(__file__).resolve()),
        "--pytest-progress",
        str(progress_path),
        "--",
        *targets,
        "-q",
        "--tb=short",
        "-o",
        "junit_family=xunit2",
        "-o",
        "junit_logging=no",
        f"--junitxml={junit_path}",
    ]
    env = os.environ.copy()
    for name in (
        "PYTEST_ADDOPTS",
        "PYTEST_PLUGINS",
        "AI_NEKO_DATA_DIR",
        "AI_NEKO_MODEL_API_KEY",
        "AI_NEKO_SEARCH_API_KEY",
    ):
        env.pop(name, None)
    env["PYTEST_DISABLE_PLUGIN_AUTOLOAD"] = "1"
    env["PYTHONUTF8"] = "1"
    # A private process group lets timeout/interrupt cleanup target only this run.
    process = subprocess.Popen(
        command,
        cwd=ROOT,
        env=env,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        start_new_session=os.name != "nt",
        creationflags=subprocess.CREATE_NEW_PROCESS_GROUP if os.name == "nt" else 0,
    )
    try:
        returncode = process.wait(timeout=timeout)
    except subprocess.TimeoutExpired:
        terminate_test_tree(process)
        returncode = 124
    except KeyboardInterrupt:
        terminate_test_tree(process)
        returncode = 130
    return returncode, junit_summary(junit_path), progress_summary(progress_path)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--output",
        type=Path,
        required=False,
        help="Evidence JSON file; never a data-root descriptor",
    )
    parser.add_argument("--pytest-progress", type=Path, help=argparse.SUPPRESS)
    parser.add_argument("pytest_args", nargs=argparse.REMAINDER, help=argparse.SUPPRESS)
    args = parser.parse_args()
    if args.pytest_progress is not None:
        import pytest

        pytest_args = args.pytest_args
        if pytest_args and pytest_args[0] == "--":
            pytest_args = pytest_args[1:]
        return pytest.main(pytest_args, plugins=[ProgressRecorder(args.pytest_progress)])
    if args.output is None or args.pytest_args:
        parser.error("--output is required; pytest arguments are internal to the smoke runner")
    output = args.output.expanduser().resolve()
    validate_output(output, parser)
    before = source_identity()
    started = datetime.now(timezone.utc).isoformat()
    start = time.monotonic()
    with tempfile.TemporaryDirectory(prefix="ai-neko-m0-evidence-") as directory:
        junit_path = Path(directory) / "results.xml"
        progress_path = Path(directory) / "progress.jsonl"
        returncode, results, progress = run_pytest(junit_path, progress_path)
    after = source_identity()
    dependencies = dependency_versions()
    unchanged = before["tree_sha256"] == after["tree_sha256"]
    pinned_python = (ROOT / ".python-version").read_text(encoding="utf-8").strip()
    python_matches_pin = platform.python_version() == pinned_python
    valid_run = (
        returncode == 0
        and results["passed"] > 0
        and unchanged
        and python_matches_pin
        and not dependencies["installed_version_mismatches"]
    )
    status = (
        "PASS" if valid_run and results["skipped"] == 0 else "PARTIAL" if valid_run else "FAILED"
    )
    platform_skip = expected_platform_skips(results, platform.system())
    ci_gate = "PASS" if valid_run and (results["skipped"] == 0 or platform_skip) else "FAILED"
    is_windows_11_x64 = (
        platform.system() == "Windows"
        and platform.release() == "11"
        and platform.machine().lower() in {"amd64", "x86_64"}
        and struct.calcsize("P") == 8
    )
    evidence = {
        "schema_version": 1,
        "app_id": "ai-neko",
        "stage": "M0",
        "started_at_utc": started,
        "duration_seconds": round(time.monotonic() - start, 3),
        "status": status,
        "ci_gate": ci_gate,
        "environment": {
            "system": platform.system(),
            "release": platform.release(),
            "version": platform.version(),
            "machine": platform.machine(),
            "python": platform.python_version(),
            "python_implementation": platform.python_implementation(),
            "python_pointer_bits": struct.calcsize("P") * 8,
            "pinned_python": pinned_python,
            "python_matches_pin": python_matches_pin,
        },
        "source": before,
        "source_unchanged_during_run": unchanged,
        "dependencies": dependencies,
        "tests": {
            "kind": "synthetic_deterministic",
            "pytest_exit_code": returncode,
            "timeout_seconds": TEST_TIMEOUT_SECONDS,
            "timed_out": returncode == 124,
            **results,
            "progress": progress,
            "real_model_tests": 0,
            "expected_platform_skips": 1 if platform_skip else 0,
        },
        "scope": {
            "service_and_synthetic_graph": status,
            "m1_deterministic_chat_and_guides": status,
            "windows_11_x64_execution": status if is_windows_11_x64 else "NOT_VERIFIED",
            "desktop_tray_audio_packaging": "NOT_TESTED",
            "original_neko_coexistence": "NOT_TESTED",
        },
        "failure_detail_policy": "Raw JUnit, stdout, stderr, failure locals and parameter IDs are excluded. Incremental progress is diagnostic only, not completed-suite evidence; rerun uv run pytest -q for local diagnosis.",
    }
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(evidence, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(
        f"M0 {evidence['status']}: {results['passed']} passed, {results['failed']} failed, {results['errors']} errors, {results['skipped']} skipped; real model tests: 0"
    )
    print(f"Evidence: {output}")
    return 0 if ci_gate == "PASS" else 1


if __name__ == "__main__":
    raise SystemExit(main())
