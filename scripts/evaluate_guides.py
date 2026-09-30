"""Frozen 30-question evaluation of the real guide retrieval/graph injection path.

No real model, credentials, DNS or external service is used. HTML extraction,
scoped storage, adoption, retrieval and LangGraph routing are production code.
Oracle labels are inspected only after the answer model request is captured.
"""

from __future__ import annotations

import argparse
import asyncio
import copy
import hashlib
import importlib.metadata
import json
import platform
import re
import runpy
import socket
import tempfile
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import patch
from urllib.parse import urlsplit
from uuid import uuid4

import httpx
from langsmith import tracing_context

from ai_neko.chat.graph import build_chat_graph
from ai_neko.config.paths import initialize_data_root
from ai_neko.memory.guides import GuideInputError, GuideStore
from ai_neko.tools import network
from ai_neko.tools.web import WebTools

ROOT = Path(__file__).resolve().parents[1]
FIXTURE_ROOT = ROOT / "tests" / "fixtures" / "guides"
MINIMUM_COVERAGE = 0.8
TOP_K = 3
CANDIDATE_LIMIT = 6
CHARACTER_BUDGET = 8000


def fixture_inputs():
    loader = runpy.run_path(FIXTURE_ROOT / "fixture_loader.py")
    integrity = loader["validate_fixtures"]()
    corpus = {item["guide_id"]: item for item in loader["load_corpus"]()["guides"]}
    questions = loader["load_questions"]()
    return integrity, corpus, questions


class _Bytes(httpx.AsyncByteStream):
    def __init__(self, value: bytes):
        self.value = value

    async def __aiter__(self):
        yield self.value


@contextmanager
def fixture_transport(corpus):
    """Only the app's HTTP/DNS boundary is synthetic, never its page parser."""
    by_path = {urlsplit(item["source"]["original_url"]).path: item for item in corpus.values()}
    calls = []

    async def resolve(_loop, host, port, **_kwargs):
        if host != "guides.example.test":
            raise AssertionError(f"unplanned fixture DNS host: {host}")
        return [(socket.AF_INET, socket.SOCK_STREAM, 6, "", ("93.184.216.34", port))]

    def handler(request):
        if request.url.host != "93.184.216.34" or request.headers["host"] != "guides.example.test":
            raise AssertionError("fixture request bypassed production public-IP pinning")
        guide = by_path[request.url.path]
        calls.append(guide["guide_id"])
        return httpx.Response(
            guide["source"]["response_status"],
            headers={"content-type": guide["source"]["media_type"]},
            stream=_Bytes(guide["html"].encode("utf-8")),
        )

    def client():
        return httpx.AsyncClient(transport=httpx.MockTransport(handler), trust_env=False)

    with (
        patch.object(asyncio.BaseEventLoop, "getaddrinfo", resolve),
        patch.object(network, "client", client),
    ):
        yield calls


async def load_documents(store, available, corpus):
    """Load only the question's allowed corpus, via real bounded page extraction."""
    mapping, outcomes = {}, {}
    reader = WebTools({}, None)
    for fixture_id in available:
        guide = corpus[fixture_id]
        page = guide["source"]
        result = await reader.execute("read_web_page", {"url": page["original_url"]})
        if len(result.get("sources", [])) != 1:
            raise AssertionError(f"fixture page did not produce one source: {fixture_id}")
        source = dict(result["sources"][0])
        # The fixture's declared clock, unlike its evidence labels, is an input.
        # Override only retrieval time; body/status/coverage come from the reader.
        source["retrieved_at"] = page["fetched_at"]
        applicability = guide["applicability"]
        basis = page["version_evidence"]
        try:
            saved = store.ingest(
                source,
                game=applicability["game_id"],
                platform=applicability["platform"],
                mode=applicability["mode"],
                game_version=applicability["game_version"],
                version_basis=basis["text"] if basis else None,
            )
        except GuideInputError as exc:
            outcomes[fixture_id] = {
                "reader_status": result["status"],
                "saved": False,
                "error": str(exc),
            }
        else:
            mapping[fixture_id] = saved
            outcomes[fixture_id] = {
                "reader_status": result["status"],
                "saved": True,
                "retained_characters": saved["retained_characters"],
                "completeness": saved["completeness"],
            }
    return mapping, outcomes


def current_context(query_context, mapping, selection):
    """Project current-match inputs, excluding every label and previous match."""
    active = copy.deepcopy(query_context["active_match"])
    active["game"] = active["game_id"]
    if active["last_delivered_advice"]:
        advice = active["last_delivered_advice"]
        original_id = advice.pop("guide_id")
        advice.pop("fixture_revision_id", None)
        if original_id in mapping:
            advice.update(
                guide_id=mapping[original_id]["guide_id"],
                revision_id=mapping[original_id]["revision_id"],
                selection_revision=selection["selection_revision"],
            )
    return active


class CaptureModel:
    """Records actual requests; never reads oracle labels or invents evidence."""

    def __init__(self, query):
        self.query = query
        self.calls = []
        self.planning_calls = 0
        self.stage = None

    async def stream(self, messages, tools=None):
        self.calls.append(
            {
                "messages": copy.deepcopy(messages),
                "tools": copy.deepcopy(tools),
                "stage": self.stage,
            }
        )
        if self.stage == "planning":
            self.planning_calls += 1
            if self.planning_calls == 1:
                yield {
                    "type": "tool_call",
                    "name": "search_web",
                    "id": "synthetic-search",
                    "arguments": {"query": self.query},
                }
            return
        yield {"type": "text", "text": "本合成适配器只记录请求，回答质量未评审。"}


class NoExternalEvidence:
    def __init__(self):
        self.calls = []

    async def execute(self, name, arguments):
        self.calls.append({"name": name, "arguments": copy.deepcopy(arguments)})
        return {"status": "error", "sources": [], "error": "synthetic_no_external_evidence"}


def injected_payload(messages):
    """Extract the real answer request, not state.sources or the retriever output."""
    payloads = []
    for message in messages:
        if message.get("role") != "user" or not isinstance(message.get("content"), str):
            continue
        content = message["content"]
        opening = content.find("{")
        if opening < 0:
            continue
        try:
            parsed = json.loads(content[opening:])
        except ValueError:
            continue
        if isinstance(parsed, dict) and isinstance(parsed.get("sources"), list):
            payloads.append(parsed)
    if len(payloads) != 1:
        raise AssertionError(f"expected one actual answer evidence payload, got {len(payloads)}")
    return payloads[0]


def _source_offset(source, body):
    """Independently validate source text against the authoritative saved version."""
    text = source.get("text")
    if not isinstance(text, str) or not text:
        return None
    start = source.get("start")
    if type(start) is int and body[start : start + len(text)] == text:
        return start
    # A source may identify chunks inside its citation metadata. Exact matching
    # remains independent of the retriever's claimed offsets and evidence label.
    first = body.find(text)
    return first if first >= 0 else None


def evidence_scores(question, actual_sources, store, mapping):
    scores = []
    for expected in question["expected_evidence"]:
        target = mapping.get(expected["guide_id"])
        candidates = []
        if target:
            body = store.get_document(target["guide_id"], target["revision_id"])["text"]
            exact = expected["exact_text"]
            label_start = body.find(exact)
            for rank, source in enumerate(actual_sources[:TOP_K], 1):
                if (
                    source.get("guide_id") != target["guide_id"]
                    or source.get("revision_id") != target["revision_id"]
                    or label_start < 0
                ):
                    continue
                start = _source_offset(source, body)
                if start is None:
                    continue
                lower = max(start, label_start)
                upper = min(start + len(source["text"]), label_start + len(exact))
                covered = body[lower:upper] if upper > lower else ""
                required_numbers = set(re.findall(r"\d+(?:\.\d+)?", exact))
                covered_numbers = set(re.findall(r"\d+(?:\.\d+)?", covered))
                candidates.append(
                    {
                        "rank": rank,
                        "chunk_id": source.get("chunk_id"),
                        "coverage": len(covered) / len(exact),
                        "all_labelled_numbers_covered": required_numbers <= covered_numbers,
                    }
                )
        best = max(candidates, key=lambda item: item["coverage"], default=None)
        scores.append(
            {
                "passage_id": expected["passage_id"],
                "best_top3": best,
                "hit": bool(
                    best
                    and best["coverage"] >= MINIMUM_COVERAGE
                    and best["all_labelled_numbers_covered"]
                ),
            }
        )
    return scores


async def evaluate_question(question, corpus, directory, *, network_enabled=True):
    from ai_neko.memory.guide_retrieval import GuideRetriever

    query_context = question["query_context"]
    paths = initialize_data_root(directory)
    with (
        fixture_transport(corpus) as loading_calls,
        GuideStore(paths, query_context["scope_id"]) as store,
    ):
        mapping, loaded = await load_documents(store, query_context["available_guide_ids"], corpus)
        ingestion_errors = [
            fixture_id
            for fixture_id, outcome in loaded.items()
            if outcome["saved"] != corpus[fixture_id]["expected_ingestion"]["save_allowed"]
            or (
                outcome["saved"]
                and outcome["completeness"]
                != corpus[fixture_id]["expected_ingestion"]["completeness"]
            )
        ]
        selected = query_context["selected_guide_id"]
        selection = None
        if selected is not None:
            saved = mapping[selected]
            # The adopted document's own dimensions are authoritative. Q27's
            # current match intentionally differs; do not rewrite its adoption.
            selection = store.set_selection(
                saved["game"],
                saved["platform"],
                saved["mode"],
                saved["guide_id"],
                saved["revision_id"],
                expected_revision=0,
                request_id=uuid4().hex,
            )["selection"]
        context = current_context(query_context, mapping, selection)
        now = datetime.fromisoformat(query_context["as_of"].replace("Z", "+00:00"))
        retrieved = []
        retriever = GuideRetriever(store)

        async def local_retrieve():
            value = retriever.retrieve(
                question["question"],
                context=context,
                expected_revision=store.revision(),
                now=now,
                limit=CANDIDATE_LIMIT,
                budget=CHARACTER_BUDGET,
            )
            retrieved.append(copy.deepcopy(value))
            return value

        model, web, events = CaptureModel(question["question"]), NoExternalEvidence(), []

        def emit(event):
            events.append(copy.deepcopy(event))
            if event.get("type") == "status" and event.get("status") in {"planning", "answering"}:
                model.stage = event["status"]

        graph = build_chat_graph(
            model,
            web if network_enabled else None,
            emit,
            None,
            context="当前对局的显式输入（建议不代表已执行）：\n"
            + json.dumps(context, ensure_ascii=False),
            local_retrieve=local_retrieve,
        )
        with tracing_context(enabled=False):
            await graph.ainvoke(
                {
                    "messages": [{"role": "user", "content": question["question"]}],
                    "guide": network_enabled,
                    "rounds": 0,
                    "calls": [],
                    "sources": [],
                    "tool_messages": [],
                    "output": "",
                }
            )
        answers = [call for call in model.calls if call["stage"] == "answering"]
        if len(answers) != 1 or len(retrieved) != 1:
            raise AssertionError("evaluation requires exactly one retrieval and one answer request")
        messages = answers[0]["messages"]
        payload = injected_payload(messages)
        sources = payload["sources"]
        reverse = {saved["guide_id"]: fixture_id for fixture_id, saved in mapping.items()}
        actual_fixture_ids = [reverse.get(source.get("guide_id")) for source in sources]
        source_identity_errors = []
        for rank, source in enumerate(sources, 1):
            try:
                document = store.get_document(source["guide_id"], source["revision_id"])
                chunks = {chunk["chunk_id"]: chunk for chunk in store.chunks(source["revision_id"])}
                chunk = chunks[source["chunk_id"]]
                start = source["start"]
                end = source["end"]
                valid = (
                    source.get("local") is True
                    and source.get("status") == "read"
                    and type(start) is int
                    and type(end) is int
                    and chunk["start"] <= start < end <= chunk["end"]
                    and source["text"] == document["text"][start:end]
                )
            except (KeyError, ValueError, TypeError):
                valid = False
            if not valid:
                source_identity_errors.append(rank)
        wrong_game = []
        wrong_version = []
        active = query_context["active_match"]
        for fixture_id in actual_fixture_ids:
            if fixture_id is None:
                wrong_game.append("unmapped-production-id")
                continue
            applicability = corpus[fixture_id]["applicability"]
            if applicability["game_id"] != active["game_id"]:
                wrong_game.append(fixture_id)
            if (
                applicability["game_version"] is not None
                and active["game_version"] is not None
                and applicability["game_version"] != active["game_version"]
            ):
                wrong_version.append(fixture_id)
        labels = evidence_scores(question, sources, store, mapping)
        local_state = payload.get("local_retrieval", {})
        status = local_state.get("status") if isinstance(local_state, dict) else local_state
        counts = {
            "search": sum(call["name"] == "search_web" for call in web.calls),
            "read_page": sum(call["name"] == "read_web_page" for call in web.calls),
        }
        serialized_messages = json.dumps(messages, ensure_ascii=False, sort_keys=True)
        withheld = question.get("withheld_evidence", {}).get("answer_token")
        previous = query_context.get("excluded_previous_match")
        gaps = question["answerability"] == "absent"
        result = {
            "question_id": question["question_id"],
            "question": question["question"],
            "answerability": question["answerability"],
            "network_enabled": network_enabled,
            "context_seam": "explicit_current_match_only; persistent_G4_lifecycle_NOT_RUN",
            "fixture_load_calls": len(loading_calls),
            "ingestion": loaded,
            "ingestion_errors": ingestion_errors,
            "selection": selection,
            "retrieval_status": retrieved[0].get("status"),
            "injected_status": status,
            "local_retrieval": local_state,
            "network_calls": counts,
            "planning_model_calls": model.planning_calls,
            "answer_model_calls": len(answers),
            "injected_count": len(sources),
            "injected_characters": sum(len(source.get("text", "")) for source in sources),
            "injected_fixture_ids": actual_fixture_ids,
            "injected_sources": sources,
            "source_identity_errors": source_identity_errors,
            "evidence_scores": labels,
            "top3_hit": bool(labels and all(x["hit"] for x in labels)),
            "prohibited_hits": sorted(
                set(actual_fixture_ids) & set(question["prohibited_guide_ids"])
            ),
            "wrong_game_hits": wrong_game,
            "known_conflicting_version_hits": wrong_version,
            "gap_rejected": bool(
                gaps
                and status in {"gap", "needs_check", "clarify"}
                and not sources
                and (model.planning_calls > 0 or status == "clarify" or not network_enabled)
            ),
            "withheld_token_leaked": bool(withheld and withheld in serialized_messages),
            "previous_match_leaked": bool(previous and previous["text"] in serialized_messages),
            "answer_request_sha256": hashlib.sha256(serialized_messages.encode()).hexdigest(),
            "answer_model_system": messages[0]["content"],
            "source_events": [event["source"] for event in events if event.get("type") == "source"],
            "model_quality": "NOT_RUN",
        }
        return result


def summarize(results):
    answerable = [row for row in results if row["answerability"] == "answerable"]
    absent = [row for row in results if row["answerability"] == "absent"]
    summary = {
        "answerable_questions": len(answerable),
        "top3_evidence_hits": sum(row["top3_hit"] for row in answerable),
        "absent_questions": len(absent),
        "absent_rejections": sum(row["gap_rejected"] for row in absent),
        "wrong_game_hits": sum(len(row["wrong_game_hits"]) for row in results),
        "known_conflicting_version_hits": sum(
            len(row["known_conflicting_version_hits"]) for row in results
        ),
        "prohibited_hits": sum(len(row["prohibited_hits"]) for row in results),
        "source_identity_errors": sum(len(row["source_identity_errors"]) for row in results),
        "ingestion_errors": sum(len(row["ingestion_errors"]) for row in results),
        "maximum_injected_count": max(row["injected_count"] for row in results),
        "maximum_injected_characters": max(row["injected_characters"] for row in results),
        "answerable_zero_network": sum(
            not any(row["network_calls"].values()) for row in answerable
        ),
        "answerable_skipped_planning": sum(row["planning_model_calls"] == 0 for row in answerable),
        "withheld_or_previous_match_leaks": sum(
            row["withheld_token_leaked"] or row["previous_match_leaked"] for row in results
        ),
    }
    summary["status"] = (
        "PASS"
        if (
            summary["answerable_questions"] == 24
            and summary["top3_evidence_hits"] >= 22
            and summary["absent_questions"] == summary["absent_rejections"] == 6
            and summary["wrong_game_hits"] == summary["known_conflicting_version_hits"] == 0
            and summary["prohibited_hits"] == summary["withheld_or_previous_match_leaks"] == 0
            and summary["source_identity_errors"] == 0
            and summary["ingestion_errors"] == 0
            and summary["maximum_injected_count"] <= CANDIDATE_LIMIT
            and summary["maximum_injected_characters"] <= CHARACTER_BUDGET
            and summary["answerable_zero_network"] == summary["answerable_skipped_planning"] == 24
        )
        else "FAIL"
    )
    return summary


async def evaluate_async():
    source_files = [
        "scripts/evaluate_guides.py",
        "src/ai_neko/chat/graph.py",
        "src/ai_neko/memory/guide_retrieval.py",
        "src/ai_neko/memory/guides.py",
        "src/ai_neko/memory/recall.py",
        "src/ai_neko/tools/web.py",
        "uv.lock",
    ]
    source_hashes = {
        name: hashlib.sha256((ROOT / name).read_bytes()).hexdigest() for name in source_files
    }
    integrity, corpus, questionnaire = fixture_inputs()
    with tempfile.TemporaryDirectory(prefix="ai-neko-guide-eval-") as root:
        results = [
            await evaluate_question(question, corpus, Path(root) / question["question_id"])
            for question in questionnaire["questions"]
        ]
        plain = [
            await evaluate_question(
                question,
                corpus,
                Path(root) / (question["question_id"] + "-plain"),
                network_enabled=False,
            )
            for question in questionnaire["questions"]
            if question["question_id"] in {"Q01", "Q23", "Q30"}
        ]
    manifest = json.loads((FIXTURE_ROOT / "manifest.json").read_text(encoding="utf-8"))
    unchanged = all(
        hashlib.sha256((ROOT / name).read_bytes()).hexdigest() == value
        for name, value in source_hashes.items()
    )
    summary = summarize(results)
    plain_pass = all(
        not any(row["network_calls"].values())
        and row["planning_model_calls"] == 0
        and not row["source_identity_errors"]
        and (row["top3_hit"] if row["answerability"] == "answerable" else row["gap_rejected"])
        for row in plain
    )
    summary.update(plain_chat_checks_passed=plain_pass, source_files_unchanged=unchanged)
    if not plain_pass or not unchanged:
        summary["status"] = "FAIL"
    return {
        "report_version": 1,
        "created_at": datetime.now(timezone.utc).isoformat(),
        "fixture_release": questionnaire["fixture_release"],
        "fixture_hashes": manifest["files"],
        "fixture_integrity": integrity["fixture_integrity"],
        "source_hashes": source_hashes,
        "environment": {
            "system": platform.platform(),
            "python": platform.python_version(),
            "dependencies": {
                name: importlib.metadata.version(name)
                for name in ("langgraph", "langgraph-checkpoint-sqlite", "httpx")
            },
        },
        "scoring": {
            **questionnaire["scoring_contract"],
            "minimum_label_character_coverage": MINIMUM_COVERAGE,
            "single_top3_fragment_required": True,
            "all_ascii_label_numbers_required": True,
            "actual_answer_request_sources_only": True,
            "oracle_labels_passed_to_product": False,
        },
        "summary": summary,
        "questions": results,
        "plain_chat_checks": plain,
        "model_quality": "NOT_RUN",
        "real_network": "NOT_RUN",
        "windows_runtime": "NOT_RUN",
        "persistent_match_lifecycle": "NOT_RUN",
    }


def evaluate():
    return asyncio.run(evaluate_async())


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, default=ROOT / "artifacts" / "guide-evaluation.json")
    arguments = parser.parse_args()
    report = evaluate()
    arguments.output.parent.mkdir(parents=True, exist_ok=True)
    arguments.output.write_text(
        json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    print(
        json.dumps(
            {"report": str(arguments.output.resolve()), **report["summary"]}, ensure_ascii=False
        )
    )
    raise SystemExit(0 if report["summary"]["status"] == "PASS" else 1)


if __name__ == "__main__":
    main()
