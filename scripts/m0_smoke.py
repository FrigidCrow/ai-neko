"""Run synthetic M0 tests and emit portable evidence without logs or credentials."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import platform
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


def sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def source_identity() -> dict:
    # Explicit inclusion avoids credentials, user data, virtualenvs and generated reports.
    files = [
        ROOT / name
        for name in ("pyproject.toml", "uv.lock", ".python-version", ".gitattributes", "AGENTS.md")
    ]
    for directory, patterns in {
        "src": ["*.py"],
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
    if not path.exists():
        return {"collected": 0, "passed": 0, "failed": 0, "errors": 0, "skipped": 0, "cases": []}
    document = ET.parse(path)
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
                "name": node.get("name"),
                "class": node.get("classname"),
                "seconds": float(node.get("time", "0")),
                "outcome": outcome,
            }
        )
    return {**counts, "cases": cases}


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


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--output",
        type=Path,
        required=True,
        help="Evidence JSON file; never a data-root descriptor",
    )
    args = parser.parse_args()
    output = args.output.expanduser().resolve()
    validate_output(output, parser)
    before = source_identity()
    started = datetime.now(timezone.utc).isoformat()
    start = time.monotonic()
    with tempfile.TemporaryDirectory(prefix="ai-neko-m0-evidence-") as directory:
        junit_path = Path(directory) / "results.xml"
        command = [
            sys.executable,
            "-m",
            "pytest",
            "tests",
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
            returncode = process.wait(timeout=180)
        except subprocess.TimeoutExpired:
            terminate_test_tree(process)
            returncode = 124
        except KeyboardInterrupt:
            terminate_test_tree(process)
            returncode = 130
        results = junit_summary(junit_path)
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
            **results,
            "real_model_tests": 0,
        },
        "scope": {
            "service_and_synthetic_graph": status,
            "windows_11_x64_execution": status if is_windows_11_x64 else "NOT_VERIFIED",
            "desktop_tray_audio_packaging": "NOT_TESTED",
            "original_neko_coexistence": "NOT_TESTED",
        },
        "failure_detail_policy": "Raw JUnit, stdout, stderr and failure locals are excluded; rerun uv run pytest -q for local diagnosis.",
    }
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(evidence, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(
        f"M0 {evidence['status']}: {results['passed']} passed, {results['failed']} failed, {results['errors']} errors, {results['skipped']} skipped; real model tests: 0"
    )
    print(f"Evidence: {output}")
    return 0 if status == "PASS" else 1


if __name__ == "__main__":
    raise SystemExit(main())
