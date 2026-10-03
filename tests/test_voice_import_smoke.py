"""Bounded harness tests; import stubs are not native-library/model evidence."""

from __future__ import annotations

import importlib.util
import json
import os
import sys
from pathlib import Path

import pytest

SCRIPT = Path(__file__).resolve().parents[1] / "scripts/voice_import_smoke.py"
SPEC = importlib.util.spec_from_file_location("voice_import_smoke", SCRIPT)
assert SPEC is not None and SPEC.loader is not None
smoke = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(smoke)


def private_venv(tmp_path):
    profile = tmp_path / "ai-neko-voice-import-synthetic"
    venv = profile / "venv"
    site = venv / ("Lib/site-packages" if os.name == "nt" else "lib/python3.11/site-packages")
    site.mkdir(parents=True)
    base = Path(sys._base_executable).resolve()
    (venv / "pyvenv.cfg").write_text(
        f"home = {base.parent}\nimplementation = CPython\nversion_info = 3.11.15\n"
        "include-system-site-packages = false\n",
        encoding="utf-8",
    )
    for name, version in (
        ("numpy", "2.4.4"),
        ("sherpa-onnx", "1.13.8"),
        ("sherpa-onnx-core", "1.13.8"),
    ):
        metadata = site / f"{name.replace('-', '_')}-{version}.dist-info/METADATA"
        metadata.parent.mkdir()
        metadata.write_text(f"Name: {name}\nVersion: {version}\n", encoding="utf-8")
    return venv, site, base


def prepared(tmp_path, numpy_source="", sherpa_source=""):
    venv, site, base = private_venv(tmp_path)
    names = json.loads((smoke.RUNTIME / "model_files.json").read_text())
    models = smoke.prepare_profile(venv, names)
    (site / "numpy.py").write_text(numpy_source, encoding="utf-8")
    (site / "sherpa_onnx.py").write_text(sherpa_source, encoding="utf-8")
    return venv, site, base, models


def test_inspect_requires_base_python_and_exact_private_locked_versions(tmp_path):
    venv, site, base = private_venv(tmp_path)
    actual_base, actual_site, packages = smoke.inspect_venv(venv)
    assert actual_base == base and actual_site == site
    assert {item["name"]: item["version"] for item in packages} == {
        "numpy": "2.4.4",
        "sherpa-onnx": "1.13.8",
        "sherpa-onnx-core": "1.13.8",
    }
    (site / "numpy-2.4.4.dist-info/METADATA").write_text("Name: numpy\nVersion: 0.0.0\n")
    with pytest.raises(ValueError, match="installed_versions_differ_from_lock"):
        smoke.inspect_venv(venv)


def test_primary_or_unrelated_venv_and_inherited_packages_are_rejected(tmp_path):
    with pytest.raises(ValueError, match="dedicated_venv"):
        smoke.inspect_venv(tmp_path / ".venv")
    with pytest.raises(ValueError, match="dedicated_profile"):
        smoke.inspect_venv(tmp_path / "venv")
    venv, site, _ = private_venv(tmp_path)
    extra = site / "foreign-1.0.dist-info/METADATA"
    extra.parent.mkdir()
    extra.write_text("Name: foreign\nVersion: 1.0\n")
    with pytest.raises(ValueError, match="unexpected_installed_package"):
        smoke.inspect_venv(venv)


def test_placeholder_profile_refuses_existing_models_and_path_escape(tmp_path):
    venv, _, _ = private_venv(tmp_path)
    with pytest.raises(ValueError, match="unsafe_resource_name"):
        smoke.prepare_profile(venv, ["../outside.txt"])
    assert not (venv.parent / "models").exists()
    models = smoke.prepare_profile(venv, ["synthetic/model.bin"])
    assert (models / "synthetic/model.bin").read_bytes() == b"x"
    with pytest.raises(ValueError, match="placeholder_models_already_exist"):
        smoke.prepare_profile(venv, ["synthetic/model.bin"])


def test_minimal_environment_never_inherits_user_keys_or_main_python(tmp_path, monkeypatch):
    for name in (
        "OPENAI_API_KEY",
        "PYTHONPATH",
        "PYTHONHOME",
        "VIRTUAL_ENV",
        "UV_INDEX_URL",
        "AI_NEKO_DATA_DIR",
    ):
        monkeypatch.setenv(name, "synthetic-private-value")
    env = smoke.child_environment(tmp_path)
    assert "synthetic-private-value" not in json.dumps(env)
    assert env["HOME"] == str(tmp_path / "home")
    assert env["USERPROFILE"] == str(tmp_path / "home")
    assert env["PYTHONUTF8"] == "1"


def test_actual_worker_main_protocol_exits_with_stdin_open_and_discards_diagnostics(tmp_path):
    _, site, base, models = prepared(
        tmp_path,
        "print('synthetic sensitive native stdout')\n",
        "import sys\nprint('synthetic sensitive native stderr', file=sys.stderr)\n",
    )
    report = smoke.run_worker(base, site, models, smoke.RUNTIME / "worker.py", timeout=5)
    assert report["status"] == "PASS", report
    assert report["fixed_stages"] == smoke.EXPECTED_STAGES
    assert report["fixed_result"] == {"ok": True, "pid": report["pid"]}
    assert report["owner_stdin_kept_open_until_exit"] is True
    assert report["natural_exit"] is True and report["runtime_identity"]["base_interpreter"] is True
    assert "synthetic sensitive" not in json.dumps(report)


def test_hung_import_is_killed_with_only_fixed_last_stage_recorded(tmp_path):
    _, site, base, models = prepared(tmp_path, "import time\ntime.sleep(60)\n")
    report = smoke.run_worker(base, site, models, smoke.RUNTIME / "worker.py", timeout=0.5)
    assert report["status"] == "FAILED"
    assert report["timed_out"] is True and report["natural_exit"] is False
    assert report["fixed_stages"] == ["numpy_before"]
    assert report["fixed_result"] is None
    assert report["elapsed_seconds"] < 5


def test_import_exception_cannot_be_mistaken_for_success_or_leak_detail(tmp_path):
    _, site, base, models = prepared(tmp_path, "raise RuntimeError('private error detail')\n")
    report = smoke.run_worker(base, site, models, smoke.RUNTIME / "worker.py", timeout=5)
    assert report["status"] == "FAILED"
    assert report["protocol_matches"] is False
    assert report["fixed_stages"] == ["numpy_before", "main_returned"]
    assert "private error detail" not in json.dumps(report)


def test_main_evidence_separates_placeholder_check_from_real_inference(tmp_path, capsys):
    venv, site, _ = private_venv(tmp_path)
    (site / "numpy.py").write_text("")
    (site / "sherpa_onnx.py").write_text("")
    output = tmp_path / "voice-import.json"
    assert smoke.main(["--venv", str(venv), "--output", str(output)]) == 0
    report = json.loads(output.read_text())
    assert report["scope"]["actual_model_inference"] is False
    assert report["scope"]["real_models_downloaded"] == 0
    assert report["placeholder_resources"]["count"] == len(
        json.loads((smoke.RUNTIME / "model_files.json").read_text())
    )
    assert report["placeholder_resources"]["bytes_per_file"] == 1
    assert "PASS" in capsys.readouterr().out
    before = output.read_bytes()
    with pytest.raises(SystemExit):
        smoke.main(["--venv", str(venv), "--output", str(output)])
    assert output.read_bytes() == before
