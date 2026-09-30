"""Independent frozen-oracle scoring of actual LangGraph answer-model inputs."""

import json
import runpy
from pathlib import Path

import pytest

_HARNESS = runpy.run_path(Path(__file__).resolve().parents[1] / "scripts" / "evaluate_guides.py")
_ORACLE = _HARNESS["fixture_inputs"]()[2]["questions"]
_ANSWERABLE = [q["question_id"] for q in _ORACLE if q["answerability"] == "answerable"]
_ABSENT = [q["question_id"] for q in _ORACLE if q["answerability"] == "absent"]


@pytest.fixture(scope="module")
def report():
    return _HARNESS["evaluate"]()


@pytest.fixture(scope="module")
def outcomes(report):
    return {question["question_id"]: question for question in report["questions"]}


def test_frozen_30_question_gate_uses_actual_injection_and_preserves_scope(report):
    summary = report["summary"]
    assert report["fixture_integrity"] == "PASS"
    assert len(report["questions"]) == 30
    assert summary["answerable_questions"] == 24
    assert summary["top3_evidence_hits"] >= 22
    assert summary["absent_questions"] == summary["absent_rejections"] == 6
    assert summary["wrong_game_hits"] == summary["known_conflicting_version_hits"] == 0
    assert summary["prohibited_hits"] == summary["source_identity_errors"] == 0
    assert summary["ingestion_errors"] == 0
    assert summary["maximum_injected_count"] <= 6
    assert summary["maximum_injected_characters"] <= 8000
    assert summary["answerable_zero_network"] == summary["answerable_skipped_planning"] == 24
    assert summary["withheld_or_previous_match_leaks"] == 0
    assert summary["source_files_unchanged"] and summary["plain_chat_checks_passed"]
    assert summary["status"] == "PASS"
    assert report["scoring"]["minimum_label_character_coverage"] == 0.8
    assert report["scoring"]["single_top3_fragment_required"] is True
    assert report["scoring"]["oracle_labels_passed_to_product"] is False


def test_each_question_really_loads_its_entire_allowed_distractor_library(outcomes):
    for question in _ORACLE:
        actual = outcomes[question["question_id"]]
        available = question["query_context"]["available_guide_ids"]
        assert set(actual["ingestion"]) == set(available)
        assert actual["fixture_load_calls"] == len(available)
        assert actual["ingestion_errors"] == []


@pytest.mark.parametrize("question_id", _ANSWERABLE)
def test_all_covered_questions_skip_search_reading_and_planning(outcomes, question_id):
    actual = outcomes[question_id]
    assert actual["network_calls"] == {"search": 0, "read_page": 0}
    assert actual["planning_model_calls"] == 0
    assert actual["answer_model_calls"] == 1
    assert actual["injected_status"] == "sufficient"
    assert actual["injected_sources"]
    assert all(source["local"] is True for source in actual["injected_sources"])
    assert all(
        source["guide_id"] == actual["selection"]["guide_id"]
        and source["revision_id"] == actual["selection"]["revision_id"]
        for source in actual["injected_sources"]
    )
    assert not actual["source_identity_errors"]
    assert not actual["prohibited_hits"]
    assert actual["source_events"] == actual["injected_sources"]


@pytest.mark.parametrize("question_id", _ABSENT)
def test_every_absent_question_enters_real_supplement_or_clarification(outcomes, question_id):
    actual = outcomes[question_id]
    assert actual["injected_status"] in {"gap", "needs_check", "clarify"}
    assert actual["gap_rejected"] is True
    assert actual["injected_sources"] == []
    assert not actual["prohibited_hits"]
    assert actual["network_calls"]["search"] > 0 or actual["injected_status"] == "clarify"
    assert actual["model_quality"] == "NOT_RUN"


@pytest.mark.parametrize("question_id", ["Q23", "Q24"])
def test_unknown_version_general_notes_retain_actual_unknown_status(outcomes, question_id):
    actual = outcomes[question_id]
    assert actual["local_retrieval"]["version_status"] == "unknown"
    assert all(source["game_version"] is None for source in actual["injected_sources"])
    assert all(source["version_status"] == "unknown" for source in actual["injected_sources"])


@pytest.mark.parametrize("question_id", ["Q28", "Q29"])
def test_unreadable_page_cannot_become_successful_adoption(outcomes, question_id):
    actual = outcomes[question_id]
    assert actual["selection"] is None
    assert all(not ingestion["saved"] for ingestion in actual["ingestion"].values())
    assert actual["injected_sources"] == []
    assert actual["local_retrieval"]["reason"] == "no_selection"


def test_retention_boundary_really_passed_through_reader_and_store(outcomes):
    actual = outcomes["Q30"]
    assert actual["ingestion"]["G10"]["retained_characters"] == 50000
    assert actual["ingestion"]["G10"]["completeness"] == "partial"
    assert actual["withheld_token_leaked"] is False
    assert actual["gap_rejected"] is True
    for question_id in ("Q14", "Q15", "Q16", "Q24"):
        assert outcomes[question_id]["top3_hit"] is True


@pytest.mark.parametrize("question_id", ["Q04", "Q11", "Q16", "Q19"])
def test_short_followups_use_explicit_current_context_seam_without_claiming_g4(
    outcomes, question_id
):
    actual = outcomes[question_id]
    oracle = next(question for question in _ORACLE if question["question_id"] == question_id)
    context = oracle["query_context"]["active_match"]
    system = actual["answer_model_system"]
    assert context["goal"] in system
    assert all(observation["text"] in system for observation in context["observations"])
    assert '"executed_by_user": false' in system
    assert actual["previous_match_leaked"] is False
    assert actual["top3_hit"] is True
    assert "persistent_G4_lifecycle_NOT_RUN" in actual["context_seam"]


def test_plain_chat_can_use_adopted_local_evidence_and_expose_gaps(report):
    checks = {row["question_id"]: row for row in report["plain_chat_checks"]}
    assert set(checks) == {"Q01", "Q23", "Q30"}
    for row in checks.values():
        assert row["network_enabled"] is False
        assert row["network_calls"] == {"search": 0, "read_page": 0}
        assert row["planning_model_calls"] == 0
    assert checks["Q01"]["top3_hit"] and checks["Q23"]["top3_hit"]
    assert checks["Q23"]["local_retrieval"]["version_status"] == "unknown"
    assert checks["Q30"]["gap_rejected"] and checks["Q30"]["injected_sources"] == []


def test_scoring_does_not_claim_real_model_network_audio_or_windows_quality(report):
    assert report["model_quality"] == "NOT_RUN"
    assert report["real_network"] == "NOT_RUN"
    assert report["windows_runtime"] == "NOT_RUN"
    assert report["persistent_match_lifecycle"] == "NOT_RUN"
    assert all(row["model_quality"] == "NOT_RUN" for row in report["questions"])
    assert "source_hashes" in report and "uv.lock" in report["source_hashes"]


def test_actual_request_parser_does_not_accept_state_or_unrelated_messages():
    parser = _HARNESS["injected_payload"]
    with pytest.raises(AssertionError, match="got 0"):
        parser([{"role": "system", "content": json.dumps({"sources": []})}])
    with pytest.raises(AssertionError, match="got 2"):
        parser([{"role": "user", "content": json.dumps({"sources": []})}] * 2)


def test_single_chunk_coverage_gate_and_numeric_evidence_are_fixed():
    class Store:
        def get_document(self, *_args):
            return {"text": "abcdefghij18"}

    scorer = _HARNESS["evidence_scores"]
    question = {
        "expected_evidence": [{"guide_id": "G", "passage_id": "P", "exact_text": "abcdefghij18"}]
    }
    mapping = {"G": {"guide_id": "guide-id", "revision_id": "rev-id"}}
    base = {"guide_id": "guide-id", "revision_id": "rev-id", "chunk_id": "chunk-id", "start": 0}
    # 10/12 >= 80%, but the omitted numeric evidence prevents a false hit.
    assert not scorer(question, [base | {"text": "abcdefghij"}], Store(), mapping)[0]["hit"]
    assert scorer(question, [base | {"text": "abcdefghij18"}], Store(), mapping)[0]["hit"]
    # A correct fourth source cannot count as a top-three hit.
    wrong = base | {"guide_id": "another", "text": "abcdefghij18"}
    assert not scorer(question, [wrong] * 3 + [base | {"text": "abcdefghij18"}], Store(), mapping)[
        0
    ]["hit"]
