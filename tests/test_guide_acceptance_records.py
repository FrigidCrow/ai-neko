"""The completed fixture is invented test data, never live acceptance evidence."""

import copy
import hashlib
import importlib.util
import json
from pathlib import Path

import pytest

_SCRIPT = Path(__file__).resolve().parents[1] / "scripts" / "record_guides_acceptance.py"
_SPEC = importlib.util.spec_from_file_location("guide_acceptance_records_under_test", _SCRIPT)
_RECORDS = importlib.util.module_from_spec(_SPEC)
_SPEC.loader.exec_module(_RECORDS)
audit, import_probe, main, template = (
    _RECORDS.audit,
    _RECORDS.import_probe,
    _RECORDS.main,
    _RECORDS.template,
)


def evidence():
    # The audit validates declarations; it intentionally never opens this path.
    return {
        "path": "manual-test-evidence.mp4",
        "recorded_at": "2026-09-30T12:00:00+09:00",
        "sha256": "a" * 64,
    }


def completed_record():
    record = template()
    record["synthetic"] = False
    record["environment"] = {
        "os": "Windows",
        "release": "11",
        "arch": "x64",
        "hardware": "Test device declaration",
        "real_hardware": True,
        "build_id": "test-build",
        "source_commit": "b" * 40,
        "artifact_sha256": "c" * 64,
        "evidence": evidence(),
    }
    for pair in record["cold_warm"]:
        pair["result"] = "completed"
        for phase in ("cold", "warm"):
            run = pair[phase]
            run.update(
                status="completed",
                synthetic=False,
                question="灯芯是什么意思？",
                turn_id=f"turn-{pair['id']}-{phase}",
                retrieval_metrics_status="recorded",
                network_conditions="Manual network declaration",
                clock_id="same-run-clock",
                output="我先看看。灯芯负责照亮码头。",
                duration_ms=1000,
                first_text_ms=100,
                text_deliveries=[
                    {"text_end": 5, "delivered_ms": 100},
                    {"text_end": 14, "delivered_ms": 700},
                ],
                web_calls=[
                    {"name": name, "status": "ok"} for name in ("search_web", "read_web_page")
                ]
                if phase == "cold"
                else [],
                retrieval_metrics=[
                    {
                        "metric": "guide_retrieve_ms",
                        "value": 2.5,
                        "turn_id": f"turn-{pair['id']}-{phase}",
                    }
                ],
                model_calls=[
                    {
                        "status": "completed",
                        "usage": {"prompt_tokens": 12, "completion_tokens": 4, "total_tokens": 16},
                    }
                ],
                evidence=evidence(),
            )
            run["providers"] = {
                key: {"provider": "test-vendor", "model_id": key + "-identity"}
                for key in ("model", "asr", "tts")
            }
            run["useful_text"] = {
                "end_offset": 14,
                "reviewer": "test-reviewer",
                "evidence": evidence(),
            }
            run["asr"] = {
                "request_id": "asr-request",
                "transcript": run["question"],
                "reviewer": "test-reviewer",
                "evidence": evidence(),
            }
            run["voice"] = {
                "request_id": "tts-request",
                "first_useful_ms": 1200,
                "clock_id": "same-run-clock",
                "listened": True,
                "actual_hardware_playback": True,
                "reviewer": "test-reviewer",
                "evidence": evidence(),
            }
            run["cost"] = {
                "amount": 0.01,
                "currency": "USD",
                "basis": "Model/search/ASR/TTS bill reconciliation",
                "evidence": evidence(),
            }
    for row in record["game_rounds"]:
        row.update(
            result="completed",
            match_id="current",
            expected_match_id="current",
            mixed_context=False,
            grounded=True,
            actionable=True,
            guide_id="guide",
            revision_id="revision",
            reviewer="test-reviewer",
            evidence=evidence(),
        )
    record["game_rounds"][0]["actions"] = ["switch_guide", "new_match", "restart"]
    for row in record["public_sources"]:
        row.update(
            result="completed",
            url=f"https://example.org/guides/{row['id']}",
            question="核查公开攻略",
            explicit_latest=row["id"] == 1,
            search_succeeded=True,
            read_succeeded=True,
            saved=True,
            adopted=True,
            warm_web_tool_attempts=0,
            version_status="unknown",
            claimed_latest=False,
            behavior_passed=True,
            reviewer="test-reviewer",
            evidence=evidence(),
        )
    for row in record["stops"]:
        row.update(
            result="completed",
            phase=("generation", "synthesis", "playback")[(row["id"] - 1) % 3],
            clock_id="audio-clock",
            audio_clock_id="audio-clock",
            received_cancel_ms=500,
            old_audio_stop_ms=600,
            input_kind="speech",
            utterance_start_ms=100,
            old_audio_was_playing=True,
            actual_hardware_playback=True,
            listened=True,
            reviewer="test-reviewer",
            evidence=evidence(),
        )
    return record


def test_empty_template_pending_and_cli_never_overwrites(tmp_path):
    path, report = tmp_path / "record.json", tmp_path / "audit.json"
    assert main(["init", "--output", str(path)]) == 0
    before = path.read_bytes()
    assert audit(json.loads(before))["status"] == "PENDING"
    assert main(["check", str(path), "--output", str(report)]) == 2
    assert json.loads(report.read_bytes())["status"] == "PENDING"
    with pytest.raises(SystemExit):
        main(["init", "--output", str(path)])
    with pytest.raises(SystemExit):
        main(["check", str(path), "--output", str(path)])
    assert path.read_bytes() == before


def test_synthetic_probe_cannot_be_relabelled_as_real_pass():
    record = completed_record()
    assert (
        audit(record)["status"] == "PASS"
    )  # Only tests contract arithmetic, not real observations.
    record["synthetic"] = True
    assert audit(record)["status"] == "PENDING"
    record["synthetic"] = False
    record["imported_probe"] = {"report": {"synthetic": True}}
    assert audit(record)["status"] == "PENDING"
    assert audit(record)["sections"]["stops"]["status"] == "PENDING"


def test_failed_samples_kept_exact_count_and_input_unmodified():
    record = completed_record()
    record["cold_warm"][8]["result"] = "failed"
    before = copy.deepcopy(record)
    result = audit(record)
    assert result["status"] == "FAIL"
    assert result["sections"]["cold_warm"]["samples"][8]["result"] == "failed"
    assert len(result["sections"]["cold_warm"]["samples"]) == 20
    assert record == before
    del record["cold_warm"][8]
    assert audit(record)["status"] == "FAIL"


def test_pair_identity_network_usage_cost_and_voice_are_not_optional():
    mutations = [
        ("question", "different question", "FAIL"),
        ("providers", {"model": {"provider": "changed", "model_id": "other"}}, "FAIL"),
        ("web_calls", [{"name": "search_web", "status": "error"}], "FAIL"),
        ("model_calls", [{"status": "completed", "usage": None}], "PENDING"),
        ("cost", None, "PENDING"),
        ("voice", None, "PENDING"),
        ("asr", None, "PENDING"),
    ]
    for field, value, expected in mutations:
        record = completed_record()
        record["cold_warm"][0]["warm"][field] = value
        assert audit(record)["status"] == expected, field
    record = completed_record()
    record["cold_warm"][0]["cold"]["web_calls"] = [{"name": "search_web", "status": "ok"}]
    assert audit(record)["status"] == "FAIL"


def test_useful_text_uses_reviewed_unicode_offset_not_placeholder_first_token():
    record = completed_record()
    result = audit(record)
    metrics = result["sections"]["cold_warm"]["samples"][0]["metrics"]["cold"]
    assert metrics["first_backend_text_ms"] == 100
    assert metrics["useful_backend_text_ms"] == 700
    assert metrics["useful_voice_ms"] == 1200
    for offset in (0, 15, True, -1):
        record["cold_warm"][0]["cold"]["useful_text"]["end_offset"] = offset
        assert audit(record)["status"] == "FAIL"
    record["cold_warm"][0]["cold"]["useful_text"]["end_offset"] = None
    assert audit(record)["status"] == "PENDING"


def test_nonmonotonic_nan_wrong_unit_and_clock_fail():
    for field, value in (
        ("unit", "s"),
        ("duration_ms", float("nan")),
        ("duration_ms", 10**400),
        ("status", ["completed"]),
        (
            "text_deliveries",
            [{"text_end": 14, "delivered_ms": 100}, {"text_end": 5, "delivered_ms": 50}],
        ),
    ):
        record = completed_record()
        record["cold_warm"][0]["warm"][field] = value
        assert audit(record)["status"] == "FAIL"
    record = completed_record()
    record["cold_warm"][0]["cold"]["voice"]["clock_id"] = "different-device-clock"
    assert audit(record)["status"] == "FAIL"
    record = completed_record()
    record["cold_warm"][0]["cold"]["voice"]["first_useful_ms"] = 0
    assert audit(record)["status"] == "FAIL"
    record = completed_record()
    record["cold_warm"][0]["warm"]["retrieval_metrics"][0]["turn_id"] = "turn-1-cold"
    assert audit(record)["status"] == "FAIL"


def test_stop_p95_phase_coverage_hardware_and_utterance_delay_separate():
    record = completed_record()
    record["stops"][-1]["old_audio_stop_ms"] = 1500
    result = audit(record)
    assert result["status"] == "PASS"
    stops = result["sections"]["stops"]
    assert stops["p95_ms"] == 100
    assert stops["utterance_to_received_cancel_ms"] == [400] * 20
    record["stops"][-2]["old_audio_stop_ms"] = 900
    assert audit(record)["sections"]["stops"]["p95_ms"] == 400
    assert audit(record)["status"] == "FAIL"
    record["cold_warm"][0]["result"] = "completed"
    record["cold_warm"][0]["cold"]["status"] = "completed"
    record["synthetic"] = False
    assert audit(record)["status"] == "FAIL"
    for field, value, expected in (
        ("phase", "playback", "PENDING"),
        ("actual_hardware_playback", False, "FAIL"),
        ("audio_clock_id", "other", "FAIL"),
        ("unit", "seconds", "FAIL"),
        ("old_audio_stop_ms", 400, "FAIL"),
    ):
        record = completed_record()
        for row in record["stops"]:
            row[field] = value
        assert audit(record)["status"] == expected


def test_windows_game_threshold_keeps_quality_failure_and_rejects_mixed_match():
    record = completed_record()
    record["game_rounds"][0]["grounded"] = False
    result = audit(record)
    assert result["status"] == "PASS"
    assert result["sections"]["game_rounds"]["quality_failures"] == [1]
    record["game_rounds"][1]["actionable"] = False
    assert audit(record)["status"] == "FAIL"
    record = completed_record()
    record["game_rounds"][0]["expected_match_id"] = "different"
    assert audit(record)["status"] == "FAIL"
    record = completed_record()
    record["environment"]["os"] = "Darwin"
    assert audit(record)["sections"]["game_rounds"]["status"] == "PENDING"


def test_unknown_version_behavior_pass_is_not_latest_acquired():
    record = completed_record()
    result = audit(record)
    assert result["sections"]["public_sources"]["status"] == "PASS"
    assert result["sections"]["public_sources"]["latest_obtained"] == 0
    record["public_sources"][0]["claimed_latest"] = True
    assert audit(record)["status"] == "FAIL"
    record = completed_record()
    record["public_sources"][0]["url"] = "http://127.0.0.1/private"
    assert audit(record)["status"] == "FAIL"
    record = completed_record()
    for row in record["public_sources"]:
        row["explicit_latest"] = False
    assert audit(record)["status"] == "PENDING"


def test_probe_import_preserves_raw_failures_metrics_and_never_infers_audio(tmp_path):
    probe = {
        "synthetic": True,
        "config": {"model": "fake", "model_base_url": "http://127.0.0.1"},
        "network_conditions": "synthetic",
        "pairs": [
            {
                "index": 1,
                "question": "问一",
                "status": "FAILED",
                "cold": {
                    "status": "failed",
                    "output": "",
                    "retrieval_metrics": [{"metric": "guide_retrieve_ms", "value": 2}],
                },
            }
        ],
    }
    source = tmp_path / "probe.json"
    source.write_text(json.dumps(probe))
    original = source.read_bytes()
    out = tmp_path / "import.json"
    assert main(["init", "--probe", str(source), "--output", str(out)]) == 0
    record = json.loads(out.read_bytes())
    assert record["imported_probe"]["report"] == probe
    assert record["imported_probe"]["sha256"] == hashlib.sha256(original).hexdigest()
    assert source.read_bytes() == original
    assert record["cold_warm"][0]["cold"]["retrieval_metrics"][0]["value"] == 2
    assert record["cold_warm"][0]["warm"]["retrieval_metrics"] is None
    assert record["cold_warm"][0]["cold"]["voice"]["listened"] is None
    assert audit(record)["status"] == "FAIL"
    probe["pairs"].append(copy.deepcopy(probe["pairs"][0]))
    with pytest.raises(ValueError, match="no sample is discarded"):
        import_probe(probe, path=source, digest="d" * 64)
