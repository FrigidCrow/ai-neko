"""Synthetic desktop assembly proves helper freshness without compiling or capturing."""

from __future__ import annotations

import importlib.util
import json
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location(
    "window_capture_packaging_builder", ROOT / "scripts" / "build_windows.py"
)
builder = importlib.util.module_from_spec(spec)
spec.loader.exec_module(builder)


@pytest.fixture
def assembly(tmp_path, monkeypatch):
    """Every input/output belongs to pytest's new directory, including fake Node."""
    desktop = tmp_path / "desktop"
    package = tmp_path / "portable"
    backend = tmp_path / "backend"
    electron_version = "44.4.5"
    files = {
        "desktop/main.cjs": "// synthetic main",
        "desktop/preload.cjs": "// synthetic preload",
        "desktop/package.json": json.dumps({"devDependencies": {"electron": electron_version}}),
        "desktop/package-lock.json": json.dumps(
            {"packages": {"node_modules/electron": {"version": electron_version}}}
        ),
        "desktop/scripts/build-window-capture.cjs": "// synthetic explicit builder",
        "desktop/lib/window-capture.cs": "// synthetic native source",
        "desktop/lib/window-capture.exe": "stale development executable",
        "desktop/lib/window-capture.build.json": "stale development manifest",
        "desktop/lib/.window-capture-build-old.exe": "unfinished development output",
        "desktop/lib/.window-capture-build-old.json": "unfinished development manifest",
        "desktop/lib/retained.cjs": "// an ordinary runtime file",
        "desktop/renderer/index.html": "synthetic renderer",
        "desktop/consent/index.html": "synthetic consent",
        "desktop/vendor/live2dcubismcore.min.js": "synthetic core",
        "desktop/assets/character.txt": "synthetic asset",
        "desktop/node_modules/electron/dist/version": electron_version,
        "desktop/node_modules/electron/dist/electron.exe": "synthetic Electron",
        "desktop/node_modules/electron/dist/LICENSE": "synthetic notice",
        "desktop/node_modules/electron/dist/LICENSES.chromium.html": "synthetic notices",
        "desktop/node_modules/electron/dist/resources/default_app.asar": "synthetic default",
        "docs/MVP1-ASSETS.md": "synthetic provenance",
        "docs/mvp1-assets-manifest.json": "{}",
        "backend/ai-neko.exe": "synthetic backend",
        "tools/node.exe": "synthetic Node; never executed",
    }
    for relative, contents in files.items():
        target = tmp_path / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(contents, encoding="utf-8")
    node = tmp_path / "tools" / "node.exe"
    calls = []
    verified = []
    state = {
        "desktop": desktop,
        "package": package,
        "backend": backend,
        "node": node,
        "calls": calls,
        "verified": verified,
        "mode": "success",
    }

    def fake_build(command, **kwargs):
        calls.append((command, kwargs))
        assert command == [
            str(node),
            str(desktop / "scripts" / "build-window-capture.cjs"),
            "--output-dir",
            str(package / "resources" / "app" / "lib"),
        ]
        assert kwargs == {
            "cwd": desktop,
            "check": True,
            "timeout": 60,
            "creationflags": subprocess.CREATE_NO_WINDOW,
        }
        output = Path(command[-1])
        # Observe the actual copy boundary before the fake fresh compiler writes.
        assert sorted(path.name for path in output.iterdir()) == [
            "retained.cjs",
            "window-capture.cs",
        ]
        assert (
            output.joinpath("window-capture.cs").read_bytes()
            == desktop.joinpath("lib/window-capture.cs").read_bytes()
        )
        mode = state["mode"]
        if mode == "compiler_failure":
            raise subprocess.CalledProcessError(1, command)
        if mode == "missing_manifest":
            return subprocess.CompletedProcess(command, 0)
        executable = output / "window-capture.exe"
        if mode != "missing_executable":
            executable.write_bytes(b"fresh synthetic compiled helper")
        manifest = {
            "version": 1,
            "source_sha256": builder.sha256(output / "window-capture.cs"),
            "executable_sha256": (builder.sha256(executable) if executable.exists() else "0" * 64),
            "compiler": "synthetic system compiler; not executed",
            "compiler_version": "4.8.0.0",
            "built_at": "2026-10-09T20:00:00.000Z",
        }
        if mode == "source_mismatch":
            manifest["source_sha256"] = "0" * 64
        elif mode == "executable_mismatch":
            manifest["executable_sha256"] = "0" * 64
        elif mode == "manifest_version_mismatch":
            manifest["version"] = 2
        (output / "window-capture.build.json").write_text(json.dumps(manifest), encoding="utf-8")
        state["manifest"] = manifest
        return subprocess.CompletedProcess(command, 0)

    monkeypatch.setattr(builder, "ROOT", tmp_path)
    monkeypatch.setattr(builder, "verify_desktop_assets", lambda: verified.append(True))
    monkeypatch.setattr(builder.shutil, "which", lambda name: str(node) if name == "node" else None)
    monkeypatch.setattr(builder.subprocess, "CREATE_NO_WINDOW", 0x08000000, raising=False)
    monkeypatch.setattr(builder.subprocess, "run", fake_build)
    return state


def test_desktop_assembly_builds_fresh_helper_and_records_packaged_hashes(assembly):
    result = builder.assemble_desktop(assembly["package"], assembly["backend"])
    assert assembly["verified"] == [True]
    assert len(assembly["calls"]) == 1
    output = assembly["package"] / "resources" / "app" / "lib"
    assert result["window_capture"] == assembly["manifest"]
    assert result["window_capture"]["source_sha256"] == builder.sha256(output / "window-capture.cs")
    assert result["window_capture"]["executable_sha256"] == builder.sha256(
        output / "window-capture.exe"
    )
    assert (output / "window-capture.exe").read_bytes() == b"fresh synthetic compiled helper"
    assert not list(output.glob(".window-capture-build-*"))
    assert (output / "retained.cjs").read_text() == "// an ordinary runtime file"
    assert (assembly["desktop"] / "lib" / "window-capture.exe").read_text() == (
        "stale development executable"
    )
    assert (assembly["desktop"] / "lib" / "window-capture.build.json").read_text() == (
        "stale development manifest"
    )
    assert (assembly["package"] / "resources" / "backend" / "ai-neko.exe").is_file()
    assert not assembly["backend"].exists()


@pytest.mark.parametrize(
    ("mode", "error"),
    [
        ("source_mismatch", RuntimeError),
        ("executable_mismatch", RuntimeError),
        ("manifest_version_mismatch", RuntimeError),
        ("missing_manifest", FileNotFoundError),
        ("missing_executable", FileNotFoundError),
        ("compiler_failure", subprocess.CalledProcessError),
    ],
)
def test_unusable_helper_build_aborts_before_backend_is_moved(assembly, mode, error):
    assembly["mode"] = mode
    with pytest.raises(error):
        builder.assemble_desktop(assembly["package"], assembly["backend"])
    assert len(assembly["calls"]) == 1
    assert (assembly["backend"] / "ai-neko.exe").is_file()
    assert not (assembly["package"] / "resources" / "backend").exists()


def test_missing_node_fails_without_reusing_development_helper(assembly, monkeypatch):
    monkeypatch.setattr(builder.shutil, "which", lambda _name: None)
    with pytest.raises(RuntimeError, match="Node is required"):
        builder.assemble_desktop(assembly["package"], assembly["backend"])
    assert assembly["calls"] == []
    assert (assembly["backend"] / "ai-neko.exe").is_file()
    output = assembly["package"] / "resources" / "app" / "lib"
    assert not (output / "window-capture.exe").exists()
    assert not (output / "window-capture.build.json").exists()
