"""Incremental smoke diagnostics survive termination without exposing test details."""

from __future__ import annotations

import importlib.util
import json
from pathlib import Path
from types import SimpleNamespace

import pytest

SCRIPT = Path(__file__).resolve().parents[1] / "scripts" / "m0_smoke.py"
SPEC = importlib.util.spec_from_file_location("m0_progress_smoke", SCRIPT)
assert SPEC is not None and SPEC.loader is not None
smoke = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(smoke)


def test_recorder_excludes_parameters_reports_and_exception_details(tmp_path: Path) -> None:
    path = tmp_path / "progress.jsonl"
    recorder = smoke.ProgressRecorder(path)
    recorder.pytest_runtest_logstart(
        "tests/test_example.py::test_example[PARAMETER_SECRET]", ("PATH_SECRET", 1, "LOCAL_SECRET")
    )
    recorder.pytest_runtest_logreport(
        SimpleNamespace(
            when="call",
            failed=True,
            skipped=False,
            passed=False,
            outcome="failed",
            duration=0.01,
            longrepr="EXCEPTION_SECRET",
            sections=[("stdout", "OUTPUT_SECRET")],
            user_properties=[("password", "PROPERTY_SECRET")],
            nodeid="NODE_SECRET",
        )
    )
    recorder.pytest_runtest_logfinish("NODE_SECRET", "LOCATION_SECRET")
    raw = path.read_text(encoding="utf-8")
    assert "SECRET" not in raw
    result = smoke.progress_summary(path)
    assert result["completed"] == 1
    assert result["completed_counts"]["failed"] == 1
    assert result["last_started"]["name"] == "test_example"
    assert result["active_test"] is None


def test_partial_last_line_is_ignored_and_fields_are_allowlisted(tmp_path: Path) -> None:
    path = tmp_path / "progress.jsonl"
    path.write_text(
        json.dumps(
            {
                "test_index": 1,
                "name": "test_pending[PARAMETER_SECRET]",
                "phase": "start",
                "outcome": "started",
                "seconds": 0,
                "longrepr": "EXCEPTION_SECRET",
            }
        )
        + '\n{"name":"UNFINISHED_SECRET',
        encoding="utf-8",
    )
    result = smoke.progress_summary(path)
    assert result["incomplete_or_invalid_records"] == 1
    assert result["active_test"]["name"] == "test_pending"
    assert result["completed"] == 0
    assert result["session_finished"] is False
    assert "SECRET" not in json.dumps(result)


def test_partial_junit_does_not_claim_any_completed_suite_results(tmp_path: Path) -> None:
    path = tmp_path / "partial.xml"
    path.write_text('<testsuite><testcase name="test_value[PARAMETER_SECRET]">', encoding="utf-8")
    assert smoke.junit_summary(path) == {
        "collected": 0,
        "passed": 0,
        "failed": 0,
        "errors": 0,
        "skipped": 0,
        "cases": [],
        "junit_status": "INCOMPLETE",
    }


def test_junit_test_names_also_exclude_parameter_ids(tmp_path: Path) -> None:
    path = tmp_path / "complete.xml"
    path.write_text(
        '<testsuite><testcase name="test_value[PARAMETER_SECRET]" classname="test_module" '
        'time="0.1"><failure>EXCEPTION_SECRET</failure></testcase></testsuite>',
        encoding="utf-8",
    )
    result = smoke.junit_summary(path)
    assert result["failed"] == 1
    assert result["cases"][0]["name"] == "test_value"
    assert "SECRET" not in json.dumps(result)


def test_real_pytest_timeout_retains_completed_and_active_tests(tmp_path: Path) -> None:
    source = tmp_path / "test_timeout_cases.py"
    source.write_text(
        "import time\nimport pytest\n"
        "@pytest.mark.parametrize('value', ['PARAMETER_SECRET'])\n"
        "def test_completed(value):\n    assert value\n"
        "def test_failure():\n    raise RuntimeError('EXCEPTION_SECRET')\n"
        "def test_hangs():\n    time.sleep(60)\n",
        encoding="utf-8",
    )
    returncode, junit, progress = smoke.run_pytest(
        tmp_path / "junit.xml",
        tmp_path / "progress.jsonl",
        (str(source),),
        timeout=8,
    )
    assert returncode == 124
    assert junit["passed"] == 0
    assert junit["junit_status"] in {"NOT_WRITTEN", "INCOMPLETE"}
    assert progress["diagnostic_only"] is True
    assert progress["completed_counts"]["passed"] == 1
    assert progress["completed_counts"]["failed"] == 1
    assert progress["last_started"]["name"] == "test_hangs"
    assert progress["active_test"]["name"] == "test_hangs"
    assert progress["last_event"]["phase"] == "call"
    assert progress["last_event"]["outcome"] == "started"
    assert progress["session_finished"] is False
    assert "SECRET" not in json.dumps(progress)


def test_successful_pytest_run_completes_progress_and_junit(tmp_path: Path) -> None:
    source = tmp_path / "test_success_cases.py"
    source.write_text(
        "import pytest\n"
        "@pytest.mark.parametrize('value', ['PARAMETER_SECRET'])\n"
        "def test_completed(value):\n    assert value\n",
        encoding="utf-8",
    )
    returncode, junit, progress = smoke.run_pytest(
        tmp_path / "junit.xml",
        tmp_path / "progress.jsonl",
        (str(source),),
        timeout=15,
    )
    assert returncode == 0
    assert junit["junit_status"] == "COMPLETE" and junit["passed"] == 1
    assert progress["session_finished"] is True
    assert progress["active_test"] is None
    assert progress["completed_counts"]["passed"] == 1
    assert "SECRET" not in json.dumps({"junit": junit, "progress": progress})


def test_timeout_keeps_main_status_failed_despite_completed_progress(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    output = tmp_path / "evidence.json"
    identity = {"tree_sha256": "synthetic", "manifest": []}
    monkeypatch.setattr(smoke, "source_identity", lambda: identity)
    monkeypatch.setattr(smoke, "dependency_versions", lambda: {"installed_version_mismatches": []})
    monkeypatch.setattr(
        smoke,
        "run_pytest",
        lambda *args: (
            124,
            {
                "collected": 0,
                "passed": 0,
                "failed": 0,
                "errors": 0,
                "skipped": 0,
                "cases": [],
                "junit_status": "NOT_WRITTEN",
            },
            {"diagnostic_only": True, "completed_counts": {"passed": 10}},
        ),
    )
    monkeypatch.setattr(smoke.sys, "argv", [str(SCRIPT), "--output", str(output)])
    assert smoke.main() == 1
    evidence = json.loads(output.read_text(encoding="utf-8"))
    assert evidence["status"] == "FAILED"
    assert evidence["tests"]["timed_out"] is True
    assert evidence["tests"]["timeout_seconds"] == 600
    assert evidence["tests"]["passed"] == 0
    assert evidence["tests"]["progress"]["completed_counts"]["passed"] == 10
