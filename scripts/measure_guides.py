"""Opt-in cold/warm Runtime probe; never read an existing application data root.

The cold question runs the production research graph in an empty disposable
store. Its actually read document is explicitly adopted before asking the same
question in a new session. Ineligible pairs are retained, not retried or removed.
This probe measures backend text delivery, not audible playback or advice quality.
"""

from __future__ import annotations

import argparse
import asyncio
import hashlib
import json
import os
import platform
import subprocess
import tempfile
import time
from datetime import UTC, datetime
from pathlib import Path
from uuid import uuid4

from ai_neko.config.paths import initialize_data_root
from ai_neko.config.providers import SEARCH_BASE_URLS, validate
from ai_neko.providers import ModelAdapter
from ai_neko.runtime import SessionRuntime
from ai_neko.tools.web import WebTools, source

ROOT = Path(__file__).resolve().parents[1]


def sha(value):
    return hashlib.sha256(value).hexdigest()


def synthetic_plan():
    return {
        "network_conditions": "No network: synthetic adapters; timings are harness overhead only.",
        "pairs": [
            {
                "question": f"灯芯{i}是什么意思？",
                "url": f"https://example.test/guide/{i}",
                "game": "synthetic",
                "platform": "pc",
                "mode": "notes",
            }
            for i in range(20)
        ],
    }


def validate_plan(plan):
    if not isinstance(plan, dict) or set(plan) != {"network_conditions", "pairs"}:
        raise ValueError("Plan requires network_conditions and pairs only")
    if not isinstance(plan["network_conditions"], str) or not plan["network_conditions"].strip():
        raise ValueError("Record the actual network conditions")
    if not isinstance(plan["pairs"], list) or len(plan["pairs"]) != 20:
        raise ValueError("Keep exactly 20 planned pairs, including failures")
    for item in plan["pairs"]:
        if not isinstance(item, dict) or set(item) != {
            "question",
            "url",
            "game",
            "platform",
            "mode",
        }:
            raise ValueError("Each pair requires question, url, game, platform and mode")
        if any(
            not isinstance(value, str) or not value.strip() or len(value) > 2000
            for value in item.values()
        ):
            raise ValueError("Invalid pair fields")
        from ai_neko.tools.network import parse_url

        parse_url(item["url"])
    return plan


class ProbeProviders:
    def __init__(self, config, item, *, synthetic):
        self.config, self.item, self.synthetic = config, item, synthetic
        self.phase = "cold"
        self.calls = []
        self.web_calls = []

    def model(self):
        parent = self
        adapter = (
            None
            if self.synthetic
            else ModelAdapter(self.config, os.environ["AI_NEKO_MODEL_API_KEY"], request_usage=True)
        )

        class Counted:
            async def stream(self, messages, tools=None):
                entry = {
                    "phase": parent.phase,
                    "started": time.perf_counter(),
                    "usage": None,
                    "request_sha256": sha(json.dumps(messages, ensure_ascii=False).encode()),
                    "status": "running",
                }
                parent.calls.append(entry)
                try:
                    stream = (
                        parent.synthetic_model()
                        if adapter is None
                        else adapter.stream(messages, tools)
                    )
                    async for event in stream:
                        if event.get("type") == "usage":
                            entry["usage"] = event["usage"]
                        yield event
                    entry["status"] = "completed"
                except BaseException as error:
                    entry["status"] = "failed"
                    entry["error_class"] = type(error).__name__
                    raise
                finally:
                    entry["duration_ms"] = (time.perf_counter() - entry.pop("started")) * 1000

        return Counted()

    async def synthetic_model(self):
        names = [item["name"] for item in self.web_calls]
        if self.phase == "cold" and "search_web" not in names:
            yield {
                "type": "tool_call",
                "id": "synthetic-search",
                "name": "search_web",
                "arguments": {"query": self.item["question"]},
            }
        elif self.phase == "cold" and "read_web_page" not in names:
            yield {
                "type": "tool_call",
                "id": "synthetic-read",
                "name": "read_web_page",
                "arguments": {"url": self.item["url"]},
            }
        else:
            term = self.item["question"].removesuffix("是什么意思？")
            yield {"type": "text", "text": "我先核对资料。"}
            yield {"type": "text", "text": f"{term}是潮灯守卫，负责照亮码头。[S1]"}
        yield {
            "type": "usage",
            "usage": {"prompt_tokens": 20, "completion_tokens": 10, "total_tokens": 30},
        }

    def web_tools(self):
        parent = self
        adapter = (
            None
            if self.synthetic
            else WebTools(
                self.config,
                os.environ["AI_NEKO_SEARCH_API_KEY"]
                if self.config.get("search_provider", "tavily") == "tavily"
                else None,
            )
        )

        class Counted:
            async def execute(self, name, arguments):
                entry = {"phase": parent.phase, "name": name, "status": "running"}
                parent.web_calls.append(entry)
                started = time.perf_counter()
                try:
                    if adapter is not None:
                        result = await adapter.execute(name, arguments)
                    else:
                        term = parent.item["question"].removesuffix("是什么意思？")
                        result = {
                            "status": "ok",
                            "sources": [
                                source(
                                    parent.item["url"],
                                    title="合成攻略",
                                    status="read" if name == "read_web_page" else "snippet",
                                    text=f"{term}是潮灯守卫，负责照亮码头。"
                                    if name == "read_web_page"
                                    else "",
                                    snippet="合成搜索结果",
                                )
                            ],
                        }
                    entry["status"] = result.get("status", "unknown")
                    return result
                except BaseException as error:
                    entry["status"] = "failed"
                    entry["error_class"] = type(error).__name__
                    raise
                finally:
                    entry["duration_ms"] = (time.perf_counter() - started) * 1000

        return Counted()


def metric_offset(runtime):
    """Exclude already buffered metrics before this isolated serial probe turn."""
    runtime.metrics.flush()
    if runtime.metrics.pending:
        return None
    path = runtime.paths.logs / "metrics.jsonl"
    try:
        return path.stat().st_size if path.exists() else 0
    except OSError:
        return None


def retrieval_metrics(runtime, offset, turn_id):
    runtime.metrics.flush()
    if offset is None or runtime.metrics.pending:
        return [], "unknown"
    try:
        with (runtime.paths.logs / "metrics.jsonl").open("rb") as handle:
            handle.seek(offset)
            entries = [json.loads(line) for line in handle if line.strip()]
    except (OSError, ValueError):
        return [], "unknown"
    return [
        {**entry, "turn_id": turn_id}
        for entry in entries
        if entry.get("metric") == "guide_retrieve_ms"
    ], "recorded"


async def measure_turn(runtime, providers, question):
    metrics_start = metric_offset(runtime)
    started = time.perf_counter()
    sid = runtime.create_session()["id"]
    turn = await runtime.start_turn(sid, question, guide=True, request_id=uuid4().hex)
    cursor, text, deliveries, sources, retrieval = 0, "", [], [], []
    async with asyncio.timeout(540):
        while True:
            batch = runtime.events(sid, turn["id"], cursor)
            for event in batch["events"]:
                cursor = event["seq"]
                if event["type"] == "text":
                    text += event["text"]
                    deliveries.append(
                        {
                            "text_end": len(text),
                            "delivered_ms": (time.perf_counter() - started) * 1000,
                        }
                    )
                elif event["type"] == "source":
                    sources.append(event["source"])
                elif event.get("status") == "local_retrieval":
                    retrieval.append(event.get("retrieval"))
            if cursor:
                runtime.ack(sid, turn["id"], cursor)
            if batch["status"] not in {"accepted", "running"} and cursor >= batch["last_seq"]:
                break
            await asyncio.sleep(0.01)
    calls = [c for c in providers.calls if c["phase"] == providers.phase]
    web_calls = [c for c in providers.web_calls if c["phase"] == providers.phase]
    usages = [c["usage"] for c in calls if c["status"] == "completed" and c["usage"] is not None]
    complete_usage = bool(calls) and len(usages) == len(calls)
    duration_ms = (time.perf_counter() - started) * 1000
    metrics, metrics_status = retrieval_metrics(runtime, metrics_start, turn["id"])
    return {
        "status": batch["status"],
        "turn_id": turn["id"],
        "output": text,
        "duration_ms": duration_ms,
        "first_text_ms": deliveries[0]["delivered_ms"] if deliveries else None,
        "text_deliveries": deliveries,
        "sources": sources,
        "retrieval": retrieval,
        "retrieval_metrics": metrics,
        "retrieval_metrics_status": metrics_status,
        "model_calls": calls,
        "web_calls": web_calls,
        "model_adapter_attempts": len(calls),
        "web_tool_attempts": len(web_calls),
        "reported_total_tokens": sum(u["total_tokens"] for u in usages) if complete_usage else None,
        "usage_status": "reported" if complete_usage else "unknown",
        "first_useful_text_ms": None,
        "first_useful_voice_ms": None,
        "advice_quality": "manual_review_required",
        "audio_playback": "NOT_RUN",
        "cost": {
            "amount": None,
            "currency": None,
            "basis": "unknown; reconcile provider billing, no hardcoded prices",
        },
    }


async def evaluate(plan, config, output, *, synthetic):
    report = {
        "stage": "G6 cold-warm backend probe",
        "status": "RUNNING",
        "started_at": datetime.now(UTC).isoformat(),
        "synthetic": synthetic,
        "platform": platform.platform(),
        "python": platform.python_version(),
        "source_commit": subprocess.check_output(
            ["git", "rev-parse", "HEAD"], cwd=ROOT, text=True
        ).strip(),
        "source_hashes": {
            p: sha((ROOT / p).read_bytes())
            for p in ("scripts/measure_guides.py", "src/ai_neko/providers.py", "uv.lock")
        },
        "config": config,
        "network_conditions": plan["network_conditions"],
        "asr": "NOT_RUN; typed paired questions",
        "tts": "NOT_RUN; desktop measurement still required",
        "plan_sha256": sha(json.dumps(plan, sort_keys=True).encode()),
        "pairs": [],
        "real_model_adapter_attempts": 0,
        "real_web_tool_attempts": 0,
        "real_model_calls": 0,
        "real_web_calls": 0,
        "counter_units": {
            "model_adapter_attempts": "adapter stream attempts, including failures; not HTTP requests",
            "web_tool_attempts": "tool execute attempts, including failures; not HTTP requests or redirect hops",
        },
        "counter_aliases": {
            "real_model_calls": "real_model_adapter_attempts",
            "real_web_calls": "real_web_tool_attempts",
        },
        "boundaries": [
            "Same question; independent sessions avoid reusing the cold answer as warm history.",
            "Polling timestamp is backend delivery, not UI rendering or audible speech.",
            "Unknown useful advice, voice timing and prices remain null, never inferred from first token.",
            "Synthetic counters and timings cannot support a real performance claim.",
            "Retrieval metrics use per-turn byte ranges from the isolated serial Runtime log; appended turn_id records that association.",
        ],
    }

    def save():
        output.parent.mkdir(parents=True, exist_ok=True)
        output.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n")

    save()
    with tempfile.TemporaryDirectory(prefix="ai-neko-paired-probe-") as folder:
        for index, item in enumerate(plan["pairs"]):
            pair = {"index": index + 1, "question": item["question"], "status": "RUNNING"}
            report["pairs"].append(pair)
            providers, runtime = None, None
            try:
                providers = ProbeProviders(config, item, synthetic=synthetic)
                runtime = SessionRuntime(initialize_data_root(Path(folder) / str(index)), providers)
                await runtime.update_memory_preferences({"auto_extract": False})
                pair["cold"] = await measure_turn(runtime, providers, item["question"])
                candidates = [
                    s
                    for s in pair["cold"]["sources"]
                    if s.get("url") == item["url"] and s.get("storage", {}).get("saved")
                ]
                if not candidates:
                    raise ValueError("planned_source_not_read_and_saved")
                doc = candidates[-1]["storage"]
                await runtime.guide_selection(
                    {
                        "request_id": uuid4().hex,
                        "expected_revision": runtime.memory.guides.revision(),
                        **{k: item[k] for k in ("game", "platform", "mode")},
                        "guide_id": doc["guide_id"],
                        "revision_id": doc["revision_id"],
                    }
                )
                providers.phase = "warm"
                pair["warm"] = await measure_turn(runtime, providers, item["question"])
                successful_cold_names = {
                    c["name"] for c in pair["cold"]["web_calls"] if c["status"] == "ok"
                }
                pair["eligible_route"] = (
                    all(pair[k]["status"] == "completed" for k in ("cold", "warm"))
                    and "search_web" in successful_cold_names
                    and "read_web_page" in successful_cold_names
                    and not pair["warm"]["web_calls"]
                    and any(
                        s.get("guide_id") == doc["guide_id"]
                        and s.get("revision_id") == doc["revision_id"]
                        and s.get("local")
                        for s in pair["warm"]["sources"]
                    )
                )
                pair["status"] = (
                    "CAPTURED_REVIEW_PENDING" if pair["eligible_route"] else "INELIGIBLE_ROUTE"
                )
                # Compatibility aggregate; consumers should use each phase's
                # list so cold and warm retrieval time never get conflated.
                pair["retrieval_metrics"] = (
                    pair["cold"]["retrieval_metrics"] + pair["warm"]["retrieval_metrics"]
                )
            except Exception as error:
                pair["status"] = "FAILED"
                pair["error_class"] = type(error).__name__
                # No upstream body, exception text, URL credentials or keys.
            finally:
                if runtime is not None:
                    try:
                        await runtime.close()
                    except Exception as error:
                        pair["status"] = "FAILED"
                        pair["cleanup_error_class"] = type(error).__name__
                if not synthetic and providers is not None:
                    report["real_model_adapter_attempts"] += len(providers.calls)
                    report["real_web_tool_attempts"] += len(providers.web_calls)
                    report["real_model_calls"] = report["real_model_adapter_attempts"]
                    report["real_web_calls"] = report["real_web_tool_attempts"]
                save()
    report["finished_at"] = datetime.now(UTC).isoformat()
    report["status"] = (
        "SYNTHETIC_PIPELINE_PASS"
        if synthetic and all(p["status"] == "CAPTURED_REVIEW_PENDING" for p in report["pairs"])
        else "PARTIAL_REVIEW_REQUIRED"
    )
    save()
    return report


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--live",
        action="store_true",
        help="Explicitly enable billable configured model/search requests",
    )
    parser.add_argument("--plan", type=Path)
    parser.add_argument("--model", default="synthetic")
    parser.add_argument("--model-base-url", default="https://api.openai.com/v1")
    parser.add_argument("--search-provider", choices=tuple(SEARCH_BASE_URLS), default="tavily")
    parser.add_argument("--search-base-url")
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    output = args.output.resolve()
    if not output.is_relative_to(ROOT / "artifacts") or output.exists() or output.suffix != ".json":
        parser.error("Use a new JSON under artifacts; never overwrite prior evidence")
    if args.live and (not args.plan or args.model == "synthetic"):
        parser.error("Live mode requires an explicit 20-pair plan and model")
    required_keys = ["AI_NEKO_MODEL_API_KEY"]
    if args.search_provider == "tavily":
        required_keys.append("AI_NEKO_SEARCH_API_KEY")
    if args.live and any(not os.environ.get(key) for key in required_keys):
        parser.error(
            "Missing project-specific model/search environment keys; no ordinary provider key fallback"
        )
    plan = validate_plan(json.loads(args.plan.read_text()) if args.plan else synthetic_plan())
    config = validate(
        {
            "model": args.model,
            "model_base_url": args.model_base_url,
            "search_provider": args.search_provider,
            "search_base_url": args.search_base_url or SEARCH_BASE_URLS[args.search_provider],
        }
    )
    report = asyncio.run(evaluate(plan, config, output, synthetic=not args.live))
    print(
        json.dumps(
            {"status": report["status"], "pairs": len(report["pairs"]), "output": str(output)}
        )
    )
    return 0 if report["status"] == "SYNTHETIC_PIPELINE_PASS" else 2


if __name__ == "__main__":
    raise SystemExit(main())
