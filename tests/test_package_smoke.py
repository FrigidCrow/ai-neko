"""Safety tests for the package harness; these do not claim Windows execution."""

from __future__ import annotations

import importlib.util
import json
import ntpath
import stat
import zipfile
from pathlib import Path

import pytest
import test_server_process

server_factory = test_server_process.server_factory

SCRIPT = Path(__file__).resolve().parents[1] / "scripts" / "package_smoke.py"
SPEC = importlib.util.spec_from_file_location("package_smoke", SCRIPT)
assert SPEC is not None and SPEC.loader is not None
smoke = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(smoke)


def make_archive(path: Path, extras: list[tuple[str | zipfile.ZipInfo, bytes]]) -> None:
    with zipfile.ZipFile(path, "w") as archive:
        archive.writestr("ai-neko-test-windows-x64/ai-neko.exe", b"synthetic placeholder")
        archive.writestr("ai-neko-test-windows-x64/_internal/runtime.dll", b"synthetic placeholder")
        archive.writestr("ai-neko-test-windows-x64/build-info.json", b"{}")
        for name, body in extras:
            archive.writestr(name, body)


def test_safe_extract_preserves_artifact_and_chinese_space_path(tmp_path: Path) -> None:
    archive = tmp_path / "portable.zip"
    make_archive(archive, [])
    before = smoke.sha256(archive)
    root = smoke.safe_extract(archive, tmp_path / "解压 程序")
    assert root == tmp_path / "解压 程序" / "ai-neko-test-windows-x64"
    assert (root / "ai-neko.exe").read_bytes() == b"synthetic placeholder"
    assert (root / "_internal").is_dir()
    assert smoke.sha256(archive) == before


@pytest.mark.parametrize(
    "name",
    [
        "../escape.txt",
        "/absolute.txt",
        "C:/escape.txt",
        "folder/../../escape.txt",
        "folder\\escape.txt",
        "folder/./escape.txt",
        "folder//escape.txt",
        "ai-neko-test-windows-x64/CON",
        "ai-neko-test-windows-x64/aux.txt",
        "ai-neko-test-windows-x64/trailing. ",
        "ai-neko-test-windows-x64/trailing.",
        "ai-neko-test-windows-x64/file:stream",
        "ai-neko-test-windows-x64/wild?.txt",
        "ai-neko-test-windows-x64/AI-NEKO.EXE",
        "second-root/file.txt",
    ],
)
def test_unsafe_archive_is_rejected_before_writing(tmp_path: Path, name: str) -> None:
    archive = tmp_path / "unsafe.zip"
    make_archive(archive, [(name, b"synthetic")])
    destination = tmp_path / "unpacked"
    with pytest.raises(AssertionError):
        smoke.safe_extract(archive, destination)
    assert not destination.exists()
    assert not (tmp_path / "escape.txt").exists()


def test_symlink_archive_entry_is_rejected_before_writing(tmp_path: Path) -> None:
    archive = tmp_path / "symlink.zip"
    link = zipfile.ZipInfo("ai-neko-test-windows-x64/link")
    link.create_system = 3
    link.external_attr = (stat.S_IFLNK | 0o777) << 16
    make_archive(archive, [(link, b"../../outside")])
    destination = tmp_path / "unpacked"
    with pytest.raises(AssertionError):
        smoke.safe_extract(archive, destination)
    assert not destination.exists()


def test_application_environment_excludes_source_python_and_credentials() -> None:
    env = smoke.application_env(
        {
            "SystemRoot": r"C:\Windows",
            "TEMP": r"C:\Temp",
            "PATH": r"C:\source\.venv",
            "PYTHONPATH": "source",
            "PYTHONHOME": "external-runtime",
            "VIRTUAL_ENV": "venv",
            "AI_NEKO_DATA_DIR": "private-data",
            "AI_NEKO_MODEL_API_KEY": "synthetic-key",
            "OPENAI_API_KEY": "synthetic-key",
            "LOCALAPPDATA": "private-profile",
            "HTTPS_PROXY": "synthetic-proxy",
            "arbitrary": "synthetic",
        }
    )
    assert set(env) == {"SystemRoot", "TEMP", "PATH", "PYTHONUTF8"}
    assert "source" not in env["PATH"]
    assert "System32" in env["PATH"]
    assert env["PYTHONUTF8"] == "1"


def test_failure_records_only_case_result() -> None:
    evidence = {"tests": {"cases": [{"name": "check", "outcome": "NOT_RUN"}]}}
    probe = smoke.Probe(Path("synthetic.zip"), evidence)

    def failure() -> None:
        raise RuntimeError("synthetic sensitive error details")

    with pytest.raises(RuntimeError):
        probe.case("check", failure)
    assert evidence == {"tests": {"cases": [{"name": "check", "outcome": "FAILED"}]}}
    assert probe.current_stage == "check"


def test_chat_probe_against_real_source_service(server_factory, monkeypatch) -> None:
    """Exercise the shipped probe's protocol assertions before the Windows build."""
    monkeypatch.syspath_prepend(str(SCRIPT.parent))
    server = server_factory()
    probe = smoke.Probe(Path("unused-synthetic.zip"), {})
    probe.connection = dict(server.connection)
    probe.check_chat()
    session = probe.api("GET", f"/api/sessions/{probe.recovery_session_id}")
    assert [turn["status"] for turn in session["turns"]] == [
        "cancelled",
        "completed",
        "completed",
    ]
    assert session["turns"][0]["confirmed_text"] == probe.recovery_text
    assert session["turns"][-1]["confirmed_text"]


def test_packaged_process_has_a_disposable_windows_home(tmp_path, monkeypatch) -> None:
    monkeypatch.setenv("USERPROFILE", "private-runner-profile")
    monkeypatch.setenv("LOCALAPPDATA", "private-runner-appdata")
    probe = smoke.Probe(Path("synthetic.zip"), {})
    assert "USERPROFILE" not in probe.env
    probe.prepare_profile(tmp_path)
    assert Path(probe.env["USERPROFILE"]).is_relative_to(tmp_path)
    assert Path(probe.env["LOCALAPPDATA"]).is_dir()
    assert "private-runner" not in json.dumps(probe.env)
    # Exercise Windows home expansion even on a non-Windows harness host.
    monkeypatch.setenv("USERPROFILE", probe.env["USERPROFILE"])
    assert ntpath.expanduser("~") == probe.env["USERPROFILE"]


def test_unsupported_host_writes_failure_evidence_without_executing(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    archive = tmp_path / "portable.zip"
    with zipfile.ZipFile(archive, "w") as bundle:
        bundle.writestr("bundle/ai-neko.exe", b"must never execute this placeholder")
        bundle.writestr("bundle/_internal/runtime.dll", b"synthetic")
        bundle.writestr(
            "bundle/build-info.json",
            json.dumps({"schema_version": 1, "app_id": "ai-neko", "source": {"commit": "a" * 40}}),
        )
    output = tmp_path / "package-smoke.json"
    monkeypatch.setattr(smoke.platform, "machine", lambda: "synthetic-unsupported-architecture")
    monkeypatch.setattr(
        smoke.sys, "argv", [str(SCRIPT), "--archive", str(archive), "--output", str(output)]
    )

    def unexpected_process(*args, **kwargs):
        pytest.fail("An unsupported host must not start the packaged executable")

    monkeypatch.setattr(smoke.subprocess, "Popen", unexpected_process)
    assert smoke.main() == 1
    evidence = json.loads(output.read_text(encoding="utf-8"))
    assert evidence["status"] == "FAILED"
    assert evidence["source_commit"] == "a" * 40
    assert evidence["archive_sha256"] == smoke.sha256(archive)
    assert evidence["failure"] == {
        "stage": "windows_x64_execution_environment",
        "error_class": "AssertionError",
    }
    assert all(item["outcome"] == "NOT_RUN" for item in evidence["tests"]["cases"][2:])
