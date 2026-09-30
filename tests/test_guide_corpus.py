"""Frozen G6 public-page fixtures through the real G1 reader and guide store.

These checks prove ingestion/retention behavior only, not A03 retrieval or model
answer quality. HTTP and DNS are synthetic; page extraction and storage are real.
"""

import asyncio
import runpy
from pathlib import Path

import pytest
from test_web_tools import dns as dns
from test_web_tools import reply
from test_web_tools import web_http as web_http

from ai_neko.config.paths import initialize_data_root
from ai_neko.memory.guides import GuideInputError, GuideStore
from ai_neko.tools.web import WebTools

_FIXTURE = runpy.run_path(Path(__file__).parent / "fixtures" / "guides" / "fixture_loader.py")
_CORPUS = _FIXTURE["load_corpus"]()
_QUESTIONS = _FIXTURE["load_questions"]()["questions"]


@pytest.fixture(scope="module", autouse=True)
def verify_frozen_corpus():
    # Do not regenerate the manifest to make changed inputs pass.
    result = _FIXTURE["validate_fixtures"]()
    assert result["fixture_integrity"] == "PASS"
    assert result["retrieval_evaluation"] == "NOT_RUN"


@pytest.mark.parametrize("guide", _CORPUS["guides"], ids=lambda guide: guide["guide_id"])
def test_frozen_public_page_ingestion_retains_expected_evidence(guide, web_http, tmp_path):
    requests = []
    page = guide["source"]

    def handle(request):
        requests.append(request)
        return reply(
            guide["html"],
            status=page["response_status"],
            headers={"content-type": page["media_type"]},
        )

    web_http(handle)
    result = asyncio.run(WebTools({}, None).execute("read_web_page", {"url": page["original_url"]}))
    assert len(requests) == 1
    assert requests[0].url.host == "93.184.216.34"
    assert requests[0].headers["host"] == "guides.example.test"
    assert len(result["sources"]) == 1
    source = result["sources"][0]
    expected = guide["expected_ingestion"]
    applicability = guide["applicability"]
    paths = initialize_data_root(tmp_path / "fixture-data")

    with GuideStore(paths, "frozen-corpus-ingestion") as store:
        if not expected["save_allowed"]:
            assert result["status"] == "error"
            assert source["status"] == source["completeness"] == "unreadable"
            assert source["text"] == ""
            with pytest.raises(GuideInputError, match="guide_body_unreadable"):
                store.ingest(source)
            assert store.list_documents() == []
            return

        assert result["status"] == "ok"
        assert source["status"] == "read"
        assert source["completeness"] == expected["completeness"]
        basis = page["version_evidence"]
        saved = store.ingest(
            source,
            game=applicability["game_id"],
            platform=applicability["platform"],
            mode=applicability["mode"],
            game_version=applicability["game_version"],
            version_basis=basis["text"] if basis is not None else None,
        )
        assert saved["saved"] is True
        assert saved["completeness"] == expected["completeness"]
        assert saved["game_version"] == applicability["game_version"]
        assert saved["original_url"] == page["original_url"]
        assert saved["final_url"] == page["final_url"]
        document = store.get_document(saved["guide_id"])
        body = document["text"]
        assert body == source["text"]
        chunks = store.chunks(saved["revision_id"])
        assert chunks and "".join(chunk["text"] for chunk in chunks) == body
        for chunk in chunks:
            assert body[chunk["start"] : chunk["end"]] == chunk["text"]

        # Use the frozen question oracle, never expected text manufactured from
        # production chunks. Source offsets may change during HTML cleanup.
        labelled_evidence = [
            evidence
            for question in _QUESTIONS
            for evidence in question["expected_evidence"]
            if evidence["guide_id"] == guide["guide_id"]
        ]
        assert labelled_evidence
        for evidence in labelled_evidence:
            exact = evidence["exact_text"]
            assert exact in body, evidence["passage_id"]
            start = body.index(exact)
            end = start + len(exact)
            containing_chunks = [
                chunk for chunk in chunks if chunk["start"] < end and chunk["end"] > start
            ]
            recovered = "".join(
                chunk["text"][max(0, start - chunk["start"]) : end - chunk["start"]]
                for chunk in containing_chunks
            )
            assert recovered == exact, evidence["passage_id"]

        if guide["guide_id"] == "G06":
            assert len(body) > 20_000
            for evidence in labelled_evidence:
                lower_bound = 6_000 if evidence["passage_id"] == "G06-P02" else 20_000
                assert body.index(evidence["exact_text"]) > lower_bound

        if expected["completeness"] == "partial":
            assert len(body) == expected["retention_character_limit"] == 50_000
            assert source["extracted_characters"] > 50_000
            assert "body_limit" in saved["completeness_reasons"]
            withheld = next(q["withheld_evidence"] for q in _QUESTIONS if q["question_id"] == "Q30")
            assert withheld["answer_token"] in guide["body_text"]
            assert withheld["answer_token"] not in body
            assert all(withheld["answer_token"] not in chunk["text"] for chunk in chunks)
            assert body.index(labelled_evidence[-1]["exact_text"]) > 20_000
            assert saved["game_version"] is None
            assert saved["version_basis"] is None
            assert saved["content_date"] is None
        else:
            assert source["extracted_characters"] == len(body)
            assert saved["completeness_reasons"] == []

        # Reopening the actual app-owned database must retain the same evidence.
        saved_id = saved["guide_id"]
        saved_revision = saved["revision_id"]
    with GuideStore(paths, "frozen-corpus-ingestion") as reopened:
        assert reopened.get_document(saved_id)["text"] == body
        assert reopened.chunks(saved_revision) == chunks
