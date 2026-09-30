"""Measure production guide retrieval in an isolated 200-document synthetic store.

Run from this project with:
    uv run python scripts/benchmark_guides.py --output artifacts/guides-benchmark/current.json

Five warmups precede 100 complete GuideRetriever.retrieve calls, including SQLite
reads, tokenization and ranking. Dataset construction, assertions and report IO
are outside the measured interval. No credentials, model, personal data or
network are used; the temporary application data root is removed on exit.
"""

from __future__ import annotations

import argparse
import hashlib
import importlib.metadata
import json
import math
import os
import platform
import socket
import sqlite3
import statistics
import subprocess
import tempfile
import time
import tomllib
from contextlib import ExitStack
from datetime import UTC, datetime
from pathlib import Path
from unittest.mock import patch
from uuid import uuid4

from ai_neko.config.paths import initialize_data_root
from ai_neko.memory.guide_retrieval import GuideRetriever
from ai_neko.memory.guides import GuideStore

ROOT = Path(__file__).resolve().parents[1]
DOCUMENT_COUNT = 200
WARMUP_COUNT = 5
SAMPLE_COUNT = 100
P95_TARGET_MS = 150
SELECTED_CHARACTERS = 49_900
QUERY = "北门哨戒的行动顺序是什么？"
EVIDENCE = "北门哨戒的行动顺序：先让银鹰停在蓝色塔顶，确认南门回声后再启用护城屏障。"
SOURCE_FILES = (
    "scripts/benchmark_guides.py",
    "src/ai_neko/memory/guide_retrieval.py",
    "src/ai_neko/memory/guides.py",
    "src/ai_neko/memory/recall.py",
    "src/ai_neko/memory/script_fold.py",
    "src/ai_neko/config/paths.py",
    "src/ai_neko/config/schema.py",
    "pyproject.toml",
    "uv.lock",
)


def hashes():
    return {name: hashlib.sha256((ROOT / name).read_bytes()).hexdigest() for name in SOURCE_FILES}


def command_output(arguments):
    try:
        result = subprocess.run(
            arguments, cwd=ROOT, capture_output=True, text=True, timeout=5, check=False
        )
        return result.stdout.strip() if result.returncode == 0 else None
    except (OSError, subprocess.TimeoutExpired):
        return None


def environment():
    cpu = platform.processor() or None
    if platform.system() == "Darwin":
        cpu = command_output(["sysctl", "-n", "machdep.cpu.brand_string"]) or cpu
    elif platform.system() == "Linux":
        try:
            for line in Path("/proc/cpuinfo").read_text(encoding="utf-8").splitlines():
                if line.startswith("model name") or line.startswith("Hardware"):
                    cpu = line.split(":", 1)[1].strip()
                    break
        except OSError:
            pass
    elif platform.system() == "Windows":
        cpu = os.environ.get("PROCESSOR_IDENTIFIER") or cpu
    lock = tomllib.loads((ROOT / "uv.lock").read_text(encoding="utf-8"))
    locked = {item["name"]: item["version"] for item in lock["package"]}
    installed = {}
    for name in ("ai-neko", "langgraph", "langgraph-checkpoint-sqlite", "httpx"):
        try:
            installed[name] = importlib.metadata.version(name)
        except importlib.metadata.PackageNotFoundError:
            installed[name] = None
    return {
        "system": platform.platform(),
        "os": platform.system(),
        "os_release": platform.release(),
        "machine": platform.machine(),
        "cpu": cpu,
        "logical_cpu_count": os.cpu_count(),
        "python": platform.python_version(),
        "python_implementation": platform.python_implementation(),
        "sqlite": sqlite3.sqlite_version,
        "installed_versions": installed,
        "lockfile": "uv.lock",
        "locked_versions": locked,
        "git_head": command_output(["git", "rev-parse", "HEAD"]),
        "source_hashes_are_authoritative_for_uncommitted_work": True,
        "clock": {
            "name": "perf_counter_ns",
            "monotonic": time.get_clock_info("perf_counter").monotonic,
            "resolution_seconds": time.get_clock_info("perf_counter").resolution,
        },
    }


def synthetic_document(index):
    """Deterministic input independent of the frozen 30-question corpus."""
    if index == 0:
        size = SELECTED_CHARACTERS
        header = "星堡检索基准，合成规则版本 1.0。公开格式正文。\n"
        repeated = "园区观察日志记录建筑颜色与日常巡视，没有行动命令。\n"
        footer = "\n北门哨戒\n" + EVIDENCE + "\n"
    else:
        # Other documents deliberately overlap the query vocabulary. They are
        # retained in the same scope/conditions but are not the adopted version.
        size = 8_000 + (index % 5) * 1_000
        header = f"星堡合成参考 {index:03d}，规则版本 1.0。\n"
        repeated = f"参考档案 {index:03d}：银鹰、塔顶与护城屏障属于背景记录。\n"
        footer = f"\n参考 {index:03d} 北门哨戒的行动顺序尚待演习；不可替代当前采用攻略。\n"
    filler_size = size - len(header) - len(footer)
    body = header + (repeated * (filler_size // len(repeated) + 1))[:filler_size] + footer
    assert len(body) == size
    return body


def _nearest_rank(values, percentile):
    return sorted(values)[max(0, math.ceil(len(values) * percentile / 100) - 1)]


def benchmark():
    source_hashes = hashes()
    machine = environment()
    created_at = datetime.now(UTC).isoformat()
    network_attempts = []

    def forbidden_network(*_args, **_kwargs):
        network_attempts.append("attempted")
        raise AssertionError("The retrieval benchmark must not use network IO")

    temporary_removed = False
    with ExitStack() as guard:
        for name in ("getaddrinfo", "create_connection"):
            guard.enter_context(patch.object(socket, name, forbidden_network))
        guard.enter_context(patch.object(socket.socket, "connect", forbidden_network))
        guard.enter_context(patch.object(socket.socket, "connect_ex", forbidden_network))
        with tempfile.TemporaryDirectory(prefix="ai-neko-guide-benchmark-") as temporary:
            temporary_path = Path(temporary)
            paths = initialize_data_root(temporary_path / "isolated-data")
            with GuideStore(paths, "synthetic-guide-benchmark") as store:
                documents = []
                dataset_digest = hashlib.sha256()
                selected = None
                for index in range(DOCUMENT_COUNT):
                    body = synthetic_document(index)
                    encoded = body.encode("utf-8")
                    dataset_digest.update(index.to_bytes(4, "big"))
                    dataset_digest.update(len(encoded).to_bytes(8, "big"))
                    dataset_digest.update(encoded)
                    url = f"https://benchmark.example.test/guides/{index:03d}"
                    saved = store.ingest(
                        {
                            "status": "read",
                            "completeness": "full",
                            "completeness_reasons": [],
                            "text": body,
                            "url": url,
                            "original_url": url,
                            "title": f"合成检索基准 {index:03d}",
                            "retrieved_at": created_at,
                            "headings": [],
                        },
                        game="benchmark-game",
                        platform="pc",
                        mode="ranked",
                        game_version="1.0",
                        version_basis="合成基准输入显式声明规则版本 1.0",
                    )
                    if saved["evicted_guide_ids"]:
                        raise AssertionError("The benchmark dataset must not be evicted")
                    if index == 0:
                        selected = saved
                    documents.append(
                        {
                            "index": index,
                            "characters": len(body),
                            "utf8_bytes": len(encoded),
                            "chunks": len(store.chunks(saved["revision_id"])),
                            "adopted": index == 0,
                            "body_sha256": hashlib.sha256(encoded).hexdigest(),
                        }
                    )
                assert selected is not None
                assert len(store.list_documents()) == DOCUMENT_COUNT
                assert documents[0]["characters"] == SELECTED_CHARACTERS
                assert 1 <= documents[0]["chunks"] <= 100
                store.set_selection(
                    "benchmark-game",
                    "pc",
                    "ranked",
                    selected["guide_id"],
                    selected["revision_id"],
                    expected_revision=store.revision(),
                    request_id=uuid4().hex,
                )
                expected_revision = store.revision()
                retriever = GuideRetriever(store)
                arguments = {
                    "context": {
                        "game": "benchmark-game",
                        "platform": "pc",
                        "mode": "ranked",
                        "game_version": "1.0",
                    },
                    "expected_revision": expected_revision,
                    "now": created_at,
                }
                warmups, samples, errors = [], [], []
                reference = None
                for iteration in range(WARMUP_COUNT + SAMPLE_COUNT):
                    started = time.perf_counter_ns()
                    result = retriever.retrieve(QUERY, **arguments)
                    elapsed_ms = (time.perf_counter_ns() - started) / 1_000_000
                    (warmups if iteration < WARMUP_COUNT else samples).append(elapsed_ms)
                    # Verification deliberately occurs after stopping the timer.
                    sources = result["sources"]
                    signature = [
                        (source["chunk_id"], source["start"], source["end"], source["text"])
                        for source in sources
                    ]
                    if reference is None:
                        reference = signature
                    valid = (
                        result["status"] == "sufficient"
                        and result["version_status"] == "matched"
                        and result["revision"] == expected_revision
                        and 1 <= len(sources) <= 6
                        and sum(len(source["text"]) for source in sources) <= 8_000
                        and all(
                            source["guide_id"] == selected["guide_id"]
                            and source["revision_id"] == selected["revision_id"]
                            and not source["context_truncated"]
                            for source in sources
                        )
                        and any(EVIDENCE in source["text"] for source in sources[:3])
                        and signature == reference
                    )
                    if not valid:
                        errors.append({"iteration": iteration, "status": result["status"]})
                database = paths.guides / "guides.sqlite"
                database_bytes = database.stat().st_size
                logical_bytes = store.logical_bytes()
                journal_bytes = sum(
                    path.stat().st_size
                    for path in paths.guides.iterdir()
                    if path.is_file() and path != database
                )
                adopted_result = {
                    "guide_id": selected["guide_id"],
                    "revision_id": selected["revision_id"],
                    "control_revision": expected_revision,
                    "chunks_returned": len(result["sources"]),
                    "characters_returned": sum(len(item["text"]) for item in result["sources"]),
                    "top3_starts": [item["start"] for item in result["sources"][:3]],
                }
        temporary_removed = not temporary_path.exists()
    unchanged = source_hashes == hashes()
    p95 = _nearest_rank(samples, 95)
    passed = (
        len(samples) == SAMPLE_COUNT
        and p95 <= P95_TARGET_MS
        and not errors
        and unchanged
        and not network_attempts
        and temporary_removed
    )
    return {
        "report_version": 1,
        "created_at": created_at,
        "source_hashes": source_hashes,
        "environment": machine,
        "method": {
            "timed_operation": "GuideRetriever.retrieve",
            "includes": [
                "SQLite reads and LRU transactions",
                "tokenization",
                "BM25 scoring",
                "coverage and version checks",
            ],
            "excludes": [
                "dataset setup",
                "post-call assertions",
                "report IO",
                "network",
                "model",
                "ASR",
                "TTS",
            ],
            "warmups": WARMUP_COUNT,
            "measured_samples": SAMPLE_COUNT,
            "query": QUERY,
            "repeated_query_and_fixed_adoption": True,
            "time_source": "perf_counter_ns",
            "percentile_method": "nearest rank: sorted_values[ceil(N * p / 100) - 1]",
            "p95_target_ms": P95_TARGET_MS,
            "synthetic_clock_used_for_version_freshness_only": created_at,
        },
        "dataset": {
            "documents": DOCUMENT_COUNT,
            "document_characters_total": sum(item["characters"] for item in documents),
            "document_utf8_bytes_total": sum(item["utf8_bytes"] for item in documents),
            "document_characters_min": min(item["characters"] for item in documents),
            "document_characters_max": max(item["characters"] for item in documents),
            "chunks_total": sum(item["chunks"] for item in documents),
            "adopted_document_characters": documents[0]["characters"],
            "adopted_document_chunks": documents[0]["chunks"],
            "database_file_bytes": database_bytes,
            "database_journal_bytes": journal_bytes,
            "store_logical_bytes": logical_bytes,
            "content_sha256": dataset_digest.hexdigest(),
            "individual_documents": documents,
        },
        "measurements_ms": {
            "warmups": warmups,
            "samples": samples,
            "minimum": min(samples),
            "median": statistics.median(samples),
            "mean": statistics.mean(samples),
            "p95": p95,
            "p99": _nearest_rank(samples, 99),
            "maximum": max(samples),
            "standard_deviation": statistics.stdev(samples),
        },
        "summary": {
            "status": "PASS" if passed else "FAIL",
            "p95_ms": p95,
            "p95_target_ms": P95_TARGET_MS,
            "sample_count": len(samples),
            "retrieval_verification_failures": errors,
            "source_files_unchanged": unchanged,
            "network_attempts": len(network_attempts),
            "temporary_data_removed": temporary_removed,
            "adopted_result": adopted_result,
        },
        "model_quality": "NOT_RUN",
        "end_to_end_latency": "NOT_RUN",
        "windows_runtime": "NOT_RUN" if platform.system() != "Windows" else "LOCAL_BENCHMARK_ONLY",
    }


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--output", type=Path, default=ROOT / "artifacts" / "guides-benchmark" / "current.json"
    )
    arguments = parser.parse_args()
    report = benchmark()
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
