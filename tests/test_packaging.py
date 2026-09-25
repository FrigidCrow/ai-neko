"""Packaging boundaries and the disposable diagnostic; no Windows success claim."""

import importlib.util
import json
import os
import subprocess
import sys
import zipfile
from pathlib import Path

import pytest

from ai_neko.diagnostics import check_foundation

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location("build_windows", ROOT / "scripts/build_windows.py")
builder = importlib.util.module_from_spec(spec)
spec.loader.exec_module(builder)


@pytest.mark.parametrize("value", ["0.1.0", "0.1.0-alpha.1", "0.1.0-dev.123abcd"])
def test_release_version(value):
    assert builder.validate_version(value, "0.1.0") == value


@pytest.mark.parametrize(
    "value",
    [
        "v0.1.0",
        "0.2.0",
        "../0.1.0",
        "0.1.0\n",
        "0.1.0-../../escape",
        "0.1.0-",
        "0.1.0-a" + "x" * 90,
    ],
)
def test_reject_unsafe_or_mismatched_version(value):
    with pytest.raises(ValueError):
        builder.validate_version(value, "0.1.0")


def test_build_never_cross_compiles(monkeypatch):
    monkeypatch.setattr(builder.sys, "platform", "darwin")
    with pytest.raises(RuntimeError, match="does not cross-compile"):
        builder.check_platform()


def test_license_fallbacks_match_recorded_hashes():
    folder = ROOT / "packaging/licenses"
    records = json.loads((folder / "provenance.json").read_text(encoding="utf-8"))
    assert {record["distribution"] for record in records} == {"langsmith", "sqlite-vec", "cpython"}
    for record in records:
        assert builder.sha256(folder / record["file"]) == record["sha256"]
        assert record["source_commit"] in record["source_url"]


def test_python_license_fallback_checks_runtime_version_and_hash(tmp_path, monkeypatch):
    folder = ROOT / "packaging/licenses"
    records = json.loads((folder / "provenance.json").read_text(encoding="utf-8"))
    monkeypatch.setattr(builder.sys, "base_prefix", str(tmp_path))
    monkeypatch.setattr(builder.sysconfig, "get_path", lambda _name: str(tmp_path / "stdlib"))
    monkeypatch.setattr(builder.platform, "python_implementation", lambda: "CPython")
    monkeypatch.setattr(builder.platform, "python_version", lambda: "3.11.15")
    path, origin = builder.python_license_source(folder, records)
    assert origin["distribution"] == "cpython"
    assert builder.sha256(path) == origin["sha256"]
    with pytest.raises(RuntimeError, match="missing license text"):
        builder.python_license_source(folder, [])
    corrupted = [{**origin, "sha256": "0" * 64}]
    with pytest.raises(RuntimeError, match="hash mismatch"):
        builder.python_license_source(folder, corrupted)
    monkeypatch.setattr(builder.platform, "python_version", lambda: "3.11.14")
    with pytest.raises(RuntimeError, match="requires CPython 3.11.15"):
        builder.python_license_source(folder, records)


def test_python_license_prefers_interpreter_stdlib_notice(tmp_path, monkeypatch):
    stdlib = tmp_path / "stdlib"
    stdlib.mkdir()
    notice = stdlib / "LICENSE.txt"
    notice.write_text("synthetic interpreter license")
    monkeypatch.setattr(builder.sys, "base_prefix", str(tmp_path))
    monkeypatch.setattr(builder.sysconfig, "get_path", lambda _name: str(stdlib))
    monkeypatch.setattr(builder.platform, "python_implementation", lambda: "CPython")
    monkeypatch.setattr(builder.platform, "python_version", lambda: "3.11.15")
    path, origin = builder.python_license_source(tmp_path, [])
    assert path == notice
    assert origin["kind"] == "installed-interpreter"


def test_archive_has_one_root_and_no_outside_files(tmp_path):
    root = tmp_path / "ai-neko-0.1.0-windows-x64"
    (root / "_internal").mkdir(parents=True)
    (root / "ai-neko.exe").write_bytes(b"synthetic executable")
    (root / "_internal" / "python311.dll").write_bytes(b"synthetic library")
    (tmp_path / ".env").write_text("SYNTHETIC=must-not-enter-archive")
    archive = tmp_path / "package.zip"
    builder.make_zip(root, archive)
    with zipfile.ZipFile(archive) as zipped:
        assert sorted(zipped.namelist()) == [
            f"{root.name}/_internal/python311.dll",
            f"{root.name}/ai-neko.exe",
        ]


def test_diagnostic_ignores_user_data_and_credentials(tmp_path, monkeypatch):
    foreign = tmp_path / "foreign-data"
    monkeypatch.setenv("AI_NEKO_DATA_DIR", str(foreign))
    monkeypatch.setenv("AI_NEKO_MODEL_API_KEY", "synthetic-secret-not-for-output")
    monkeypatch.setenv("LANGSMITH_TRACING", "true")
    result = check_foundation()
    assert result["status"] == "passed"
    assert result["user_data_accessed"] is False
    assert result["real_model_calls"] == result["external_network_calls"] == 0
    assert "graph_reopen_resume" in result["checks"]
    assert not foreign.exists()


def test_new_entry_dispatches_self_check_and_graph(tmp_path):
    env = {**os.environ, "AI_NEKO_DATA_DIR": str(tmp_path / "unused-default")}
    executable = [sys.executable, "-m", "ai_neko"]
    checked = subprocess.run(
        [*executable, "self-check"], env=env, capture_output=True, text=True, check=True
    )
    assert json.loads(checked.stdout)["status"] == "passed"
    graph = subprocess.run(
        [
            *executable,
            "graph",
            "--data-root",
            str(tmp_path / "explicit-root"),
            "--user",
            "synthetic",
            "--character",
            "cat",
            "--thread",
            "one",
            "run",
            "--text",
            "entry dispatch",
        ],
        env=env,
        capture_output=True,
        text=True,
        check=True,
    )
    assert json.loads(graph.stdout)["values"]["response"] == "synthetic: entry dispatch"
    assert not (tmp_path / "unused-default").exists()
