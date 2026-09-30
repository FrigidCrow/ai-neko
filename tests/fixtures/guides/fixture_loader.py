"""Expand and verify the frozen, synthetic G6 guide corpus using only the stdlib.

This is fixture plumbing, not a retriever, chunker, ingestion implementation, or
answer-quality evaluator. Expected evidence is authored in questions.json.
"""

from __future__ import annotations

import argparse
import hashlib
import html
import json
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parent
BODY_LIMIT = 50_000
FROZEN_FILES = ("corpus.json", "questions.json", "fixture_loader.py", "README.md")


def _read_json(name: str) -> Any:
    return json.loads((ROOT / name).read_text(encoding="utf-8"))


def _sha256(data: str | bytes) -> str:
    if isinstance(data, str):
        data = data.encode("utf-8")
    return hashlib.sha256(data).hexdigest()


def load_corpus() -> dict[str, Any]:
    """Return metadata plus full expanded body, HTML, and authored passage spans.

    body_text is the page's *full* readable text, even when longer than 50,000
    characters. The consumer must enforce the production retention cap itself.
    No body is silently truncated here, and login/403 pages are not made readable.
    Spans use Python Unicode code-point offsets, start inclusive / end exclusive.
    """
    corpus = _read_json("corpus.json")
    for guide in corpus["guides"]:
        segments: list[str] = []
        html_segments: list[str] = []
        spans: dict[str, dict[str, Any]] = {}
        offset = 0
        for block in guide["body_blocks"]:
            if "repeat" in block:
                repeated = block["repeat"]
                for index in range(1, repeated["count"] + 1):
                    paragraph = repeated["text_template"].format(index=index)
                    segments.append(paragraph)
                    html_segments.append(f"<p>{html.escape(paragraph)}</p>")
                    offset += len(paragraph) + 2
                continue
            heading = block["heading"]
            text = block["text"]
            segment = f"{heading}\n{text}"
            start = offset + len(heading) + 1
            if block["passage_id"] in spans:
                raise ValueError(f"duplicate passage: {block['passage_id']}")
            spans[block["passage_id"]] = {
                "start": start,
                "end": start + len(text),
                "exact_text": text,
                "text_sha256": _sha256(text),
            }
            segments.append(segment)
            html_segments.append(
                f'<section id="{html.escape(block["passage_id"], quote=True)}">'
                f"<h2>{html.escape(heading)}</h2><p>{html.escape(text)}</p></section>"
            )
            offset += len(segment) + 2
        guide["body_text"] = "\n\n".join(segments)
        title = html.escape(guide["source"]["title"])
        guide["html"] = (
            '<!doctype html><html lang="zh-CN"><head><meta charset="utf-8">'
            f"<title>{title}</title></head><body><main><article>"
            f"<h1>{title}</h1>{''.join(html_segments)}</article></main></body></html>"
        )
        guide["passage_spans"] = spans
    return corpus


def load_questions() -> dict[str, Any]:
    """Return the frozen oracle; never infer answers from retriever output."""
    return _read_json("questions.json")


def build_manifest() -> dict[str, Any]:
    """Compute hashes for deliberate fixture review; does not change labels."""
    corpus = load_corpus()
    return {
        "schema_version": 1,
        "fixture_release": corpus["fixture_release"],
        "frozen_at": corpus["frozen_at"],
        "hash_algorithm": "sha256",
        "text_encoding": "utf-8",
        "span_unit": "unicode_code_points",
        "files": {name: _sha256((ROOT / name).read_bytes()) for name in FROZEN_FILES},
        "expanded_guides": {
            guide["guide_id"]: {
                "body_characters": len(guide["body_text"]),
                "body_sha256": _sha256(guide["body_text"]),
                "html_sha256": _sha256(guide["html"]),
                "retained_prefix_sha256": _sha256(guide["body_text"][:BODY_LIMIT]),
                "passages": guide["passage_spans"],
            }
            for guide in corpus["guides"]
        },
    }


def validate_fixtures() -> dict[str, Any]:
    """Check fixture integrity only; this is not an A03 product PASS."""
    corpus = load_corpus()
    questions = load_questions()
    guides = {guide["guide_id"]: guide for guide in corpus["guides"]}
    assert len(guides) == len(corpus["guides"]) == 12
    assert len({g["applicability"]["game_id"] for g in guides.values()}) == 2
    assert len(questions["questions"]) == 30
    ids = [q["question_id"] for q in questions["questions"]]
    assert len(set(ids)) == 30
    answerable = 0
    missing = 0
    next_steps = 0
    for question in questions["questions"]:
        query = question["query_context"]
        current = query["active_match"]
        selected_id = query["selected_guide_id"] or query.get("requested_guide_id")
        selected = guides[selected_id]
        assert selected_id in query["available_guide_ids"]
        assert current["match_id"] and current["game_id"] and current["platform"]
        assert current["mode"] and "game_version" in current
        assert query["as_of"] and query["scope_id"]
        if "follow_up" in question["tags"]:
            next_steps += 1
            assert current["goal"] and current["observations"]
            assert current["last_delivered_advice"]["delivered"] is True
        for prohibited in question["prohibited_guide_ids"]:
            assert prohibited in guides
        if question["answerability"] == "answerable":
            answerable += 1
            assert question["expected_evidence"]
            assert question["required_path"] == "local_evidence"
            for expected in question["expected_evidence"]:
                guide = guides[expected["guide_id"]]
                span = guide["passage_spans"][expected["passage_id"]]
                assert expected["exact_text"] == span["exact_text"]
                assert expected["applicability"] == guide["applicability"]
                assert guide["body_text"][span["start"] : span["end"]] == expected["exact_text"]
                assert span["end"] <= BODY_LIMIT, question["question_id"]
                assert guide["source"]["readability"] == "readable"
                assert guide["source"]["response_status"] == 200
                assert guide["guide_id"] == selected["guide_id"]
                assert guide["guide_id"] not in question["prohibited_guide_ids"]
                for field in ("game_id", "platform", "mode", "game_version"):
                    assert expected["applicability"][field] == current[field]
                if current["game_version"] is None:
                    assert question["version_sensitive"] is False
        elif question["answerability"] == "absent":
            missing += 1
            assert question["expected_evidence"] == []
            assert question["required_path"] == "evidence_gap"
            assert question["absence_reason"]
            if question["absence_reason"] == "beyond_retention_limit":
                withheld = question["withheld_evidence"]
                span = selected["passage_spans"][withheld["passage_id"]]
                assert span["start"] >= BODY_LIMIT
                assert span["exact_text"] == withheld["exact_text"]
                assert withheld["answer_token"] not in selected["body_text"][:BODY_LIMIT]
            if question["absence_reason"] == "known_version_conflict":
                assert selected["applicability"]["game_version"] != current["game_version"]
            if question["absence_reason"] == "mode_conflict":
                assert selected["applicability"]["mode"] != current["mode"]
            if question["absence_reason"] in ("login_required", "access_denied"):
                assert selected["source"]["readability"] == question["absence_reason"]
                assert query["selected_guide_id"] is None
        else:
            raise AssertionError(f"unknown answerability: {question['answerability']}")
    assert (answerable, missing, next_steps) == (24, 6, 4)
    assert len(guides["G06"]["body_text"]) > 20_000
    assert len(guides["G10"]["body_text"]) > 50_000
    assert guides["G06"]["passage_spans"]["G06-P02"]["start"] > 6_000
    assert guides["G06"]["passage_spans"]["G06-P03"]["start"] > 20_000
    assert 20_000 < guides["G10"]["passage_spans"]["G10-P02"]["start"] < 50_000
    expected_manifest = _read_json("manifest.json")
    assert build_manifest() == expected_manifest, "frozen fixture checksum mismatch"
    return {
        "fixture_integrity": "PASS",
        "retrieval_evaluation": "NOT_RUN",
        "model_quality": "NOT_RUN",
        "guide_count": len(guides),
        "answerable_questions": answerable,
        "absent_questions": missing,
        "follow_up_questions": next_steps,
        "body_characters": {
            guide_id: len(guide["body_text"]) for guide_id, guide in guides.items()
        },
    }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--verify", action="store_true", help="validate the frozen corpus")
    parser.add_argument(
        "--write-manifest",
        action="store_true",
        help="explicit authoring action; review this diff and never use to hide an eval failure",
    )
    parser.add_argument(
        "--export",
        type=Path,
        help="write expanded fixture JSON to a caller-selected temporary output file",
    )
    args = parser.parse_args()
    if args.write_manifest:
        (ROOT / "manifest.json").write_text(
            json.dumps(build_manifest(), ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
        )
    if args.export:
        args.export.write_text(
            json.dumps(load_corpus(), ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
        )
    if args.verify or not (args.write_manifest or args.export):
        print(json.dumps(validate_fixtures(), ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
