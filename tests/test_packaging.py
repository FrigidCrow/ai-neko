"""Packaging boundaries and the disposable diagnostic; no Windows success claim."""

import importlib.util
import json
import os
import subprocess
import sys
import zipfile
from importlib.metadata import PackagePath
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


@pytest.mark.parametrize("separator", ["/", "\\"])
def test_license_collection_handles_windows_wheel_record_paths(tmp_path, monkeypatch, separator):
    wheel_root = tmp_path / "wheel"
    license_root = wheel_root / "ormsgpack-1.12.2.dist-info" / "licenses"
    license_root.mkdir(parents=True)
    names = ["LICENSE-APACHE", "LICENSE-MIT"]
    for name in names:
        (license_root / name).write_text(f"synthetic {name} notice")

    class SyntheticDistribution:
        version = "1.12.2"
        metadata = {"License-Expression": "MIT OR Apache-2.0"}
        files = [
            PackagePath(separator.join(("ormsgpack-1.12.2.dist-info", "licenses", name)))
            for name in names
        ]

        def locate_file(self, source):
            # Reproduce Windows filesystem interpretation while this test runs on Mac/Linux too.
            return wheel_root / str(source).replace("\\", "/")

    dist = SyntheticDistribution()
    monkeypatch.setattr(builder, "distribution", lambda _name: dist)
    monkeypatch.setattr(builder.sys, "platform", "darwin")
    monkeypatch.setattr(
        builder,
        "python_license_source",
        lambda *_args: (license_root / names[0], {"kind": "synthetic"}),
    )
    manifest = builder.copy_licenses(tmp_path / "notices", {"ormsgpack": dist})
    entry = next(entry for entry in manifest["dependencies"] if entry["name"] == "ormsgpack")
    assert len(entry["files"]) == 2
    for item, name in zip(entry["files"], names):
        saved = tmp_path / "notices" / item["path"]
        assert saved.read_text() == f"synthetic {name} notice"
        assert "\\" not in item["path"]
        assert item["sha256"] == builder.sha256(saved)


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


def test_windows_runtime_notice_corpus_matches_recorded_hashes():
    folder = ROOT / "packaging/licenses"
    provenance = json.loads((folder / "windows-runtime.json").read_text(encoding="utf-8"))
    assert provenance["python_version"] == "3.11.15"
    assert provenance["target"] == "x86_64-pc-windows-msvc"
    components = {record["component"] for record in provenance["files"]}
    assert {"openssl-3", "libffi", "expat", "mpdecimal", "zlib"} <= components
    for record in provenance["files"]:
        assert builder.sha256(folder / record["file"]) == record["sha256"]


def test_windows_runtime_notice_copy_rejects_changed_distribution(tmp_path, monkeypatch):
    folder = ROOT / "packaging/licenses"
    provenance = json.loads((folder / "windows-runtime.json").read_text(encoding="utf-8"))
    python_root = tmp_path / "python"
    python_root.mkdir()
    hashes = provenance["installed_file_sha256"]
    for relative in hashes:
        path = python_root / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(b"synthetic runtime fixture")
    original_sha256 = builder.sha256
    monkeypatch.setattr(builder.platform, "python_version", lambda: "3.11.15")
    monkeypatch.setattr(
        builder,
        "sha256",
        lambda path: (
            hashes[path.relative_to(python_root).as_posix()]
            if path.is_relative_to(python_root)
            else original_sha256(path)
        ),
    )
    output = tmp_path / "notices"
    copied = builder.copy_windows_runtime_notices(output, python_root)
    assert len(list(output.glob("*.txt"))) == len(copied["files"])
    assert json.loads((output / "provenance.json").read_text()) == provenance
    monkeypatch.setattr(builder, "sha256", original_sha256)
    with pytest.raises(RuntimeError, match="distribution changed"):
        builder.copy_windows_runtime_notices(tmp_path / "must-not-exist", python_root)
    assert not (tmp_path / "must-not-exist").exists()


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


def test_desktop_asset_gate_rejects_changed_bytes_and_path_escape(tmp_path, monkeypatch):
    (tmp_path / "docs").mkdir()
    asset = tmp_path / "desktop" / "assets" / "model.json"
    asset.parent.mkdir(parents=True)
    asset.write_bytes(b"synthetic asset")
    record = {
        "path": "desktop/assets/model.json",
        "bytes": asset.stat().st_size,
        "sha256": builder.sha256(asset),
    }
    manifest = tmp_path / "docs" / "mvp1-assets-manifest.json"
    manifest.write_text(json.dumps({"files": [record]}))
    monkeypatch.setattr(builder, "ROOT", tmp_path)
    builder.verify_desktop_assets()
    asset.write_bytes(b"changed asset")
    with pytest.raises(RuntimeError, match="integrity mismatch"):
        builder.verify_desktop_assets()
    manifest.write_text(json.dumps({"files": [{**record, "path": "../foreign"}]}))
    with pytest.raises(RuntimeError, match="escapes"):
        builder.verify_desktop_assets()


def test_desktop_asset_manifest_excludes_accidental_files_and_records_model():
    manifest = json.loads((ROOT / "docs/mvp1-assets-manifest.json").read_text())
    paths = [record["path"] for record in manifest["files"]]
    assert len(paths) == len(set(paths))
    assert any(path.endswith("yui-lolita.model3.json") for path in paths)
    assert not any("mao_pro" in path for path in paths)
    assert not any("node_modules" in path or ".env" in path for path in paths)
    # Core is fetched separately by the pinned helper; all tracked files must match.
    for record in manifest["files"]:
        file = ROOT / record["path"]
        if not file.exists() and record["component"] == "cubism-core":
            continue
        assert file.stat().st_size == record["bytes"]
        assert builder.sha256(file) == record["sha256"]


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
        [*executable, "self-check"], env=env, capture_output=True, text=True, check=True, timeout=30
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
        timeout=30,
    )
    assert json.loads(graph.stdout)["values"]["response"] == "synthetic: entry dispatch"
    assert not (tmp_path / "unused-default").exists()
