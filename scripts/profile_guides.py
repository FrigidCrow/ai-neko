"""Collect synthetic retrieval diagnostics; instrumented timings are not acceptance.

The unchanged benchmark owns the dataset, sample counts, correctness checks and
network prohibition. Only its retrieve calls are profiled. Reports contain
synthetic metadata and function counters, never guide bodies or profiler locals.
"""

from __future__ import annotations

import argparse
import cProfile
import hashlib
import importlib.util
import json
import pstats
import sysconfig
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
STDLIB = Path(sysconfig.get_path("stdlib")).resolve()


def _load_benchmark():
    specification = importlib.util.spec_from_file_location(
        "guide_benchmark_diagnostic", ROOT / "scripts/benchmark_guides.py"
    )
    if specification is None or specification.loader is None:
        raise RuntimeError("guide_benchmark_unavailable")
    module = importlib.util.module_from_spec(specification)
    specification.loader.exec_module(module)
    return module


def _function_location(filename: str) -> str:
    """Keep project-relative locations; do not serialize machine-specific paths."""
    if filename == "~":  # cProfile's built-in/C-function marker.
        return "<stdlib>"
    path = Path(filename).resolve()
    if path.is_relative_to(ROOT):
        relative = path.relative_to(ROOT)
        if relative.parts[0] in {"scripts", "src"}:
            return relative.as_posix()
    if path.is_relative_to(STDLIB) and not {"site-packages", "dist-packages"}.intersection(
        path.parts
    ):
        return "<stdlib>"
    return "<external>"


def diagnose() -> dict:
    benchmark = _load_benchmark()
    original = benchmark.GuideRetriever.retrieve
    profiler = cProfile.Profile()
    calls = []

    def measured(self, *args, **kwargs):
        wall_started = time.perf_counter_ns()
        cpu_started = time.process_time_ns()
        profiler.enable()
        try:
            return original(self, *args, **kwargs)
        finally:
            profiler.disable()
            wall_ns = time.perf_counter_ns() - wall_started
            cpu_ns = time.process_time_ns() - cpu_started
            calls.append(
                {
                    "iteration": len(calls),
                    "phase": "warmup" if len(calls) < benchmark.WARMUP_COUNT else "sample",
                    "wall_ns": wall_ns,
                    "process_cpu_ns": cpu_ns,
                }
            )

    benchmark.GuideRetriever.retrieve = measured
    try:
        instrumented = benchmark.benchmark()
    finally:
        profiler.disable()
        benchmark.GuideRetriever.retrieve = original
    if len(calls) != benchmark.WARMUP_COUNT + benchmark.SAMPLE_COUNT:
        raise RuntimeError("unexpected_guide_profile_call_count")
    functions = []
    for (filename, line, name), values in pstats.Stats(profiler).stats.items():
        primitive, total, self_seconds, cumulative_seconds, _callers = values
        functions.append(
            {
                "file": _function_location(filename),
                "line": line,
                "name": name,
                "primitive_calls": primitive,
                "total_calls": total,
                "self_seconds": self_seconds,
                "cumulative_seconds": cumulative_seconds,
            }
        )
    functions.sort(key=lambda item: item["cumulative_seconds"], reverse=True)
    instrumented_summary = dict(instrumented["summary"])
    instrumented_summary["instrumented_threshold_status"] = instrumented_summary.pop("status")
    return {
        "report_version": 1,
        "status": "DIAGNOSTIC_COMPLETE",
        "acceptance": "NOT_ACCEPTANCE",
        "invalid_for_acceptance": True,
        "reason": (
            "cProfile and per-call clocks alter timings. The separate, unchanged benchmark "
            "step alone supplies performance acceptance; this report cannot override its outcome."
        ),
        "diagnostic_source_sha256": hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
        "method": {
            "profiled_operation": "GuideRetriever.retrieve",
            "warmups": benchmark.WARMUP_COUNT,
            "samples": benchmark.SAMPLE_COUNT,
            "wall_clock": "perf_counter_ns",
            "cpu_clock": "process_time_ns (whole process, including all threads)",
            "cpu_clock_caveat": (
                "Nanosecond units do not imply nanosecond resolution; Windows per-call "
                "CPU times may be quantized. Function cumulative times are elapsed, not CPU."
            ),
            "function_times": "cProfile default elapsed timer; includes profiler overhead",
            "per_call_times": "include profiler enable/disable and clock overhead",
            "network_guard": "unchanged benchmark socket prohibitions",
        },
        "instrumented_benchmark": {
            key: instrumented[key]
            for key in ("report_version", "created_at", "source_hashes", "environment", "dataset")
        },
        "instrumented_measurements_ms": instrumented["measurements_ms"],
        "instrumented_summary": instrumented_summary,
        "retrieve_calls": calls,
        "functions": functions,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--output", type=Path, default=ROOT / "artifacts/mvp2/Windows-guide-profile.json"
    )
    arguments = parser.parse_args()
    try:
        report = diagnose()
        arguments.output.parent.mkdir(parents=True, exist_ok=True)
        arguments.output.write_text(
            json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
        )
    except Exception as exc:
        # Exception messages/tracebacks can contain local paths or input values.
        print(json.dumps({"status": "DIAGNOSTIC_ERROR", "error_type": type(exc).__name__}))
        return 1
    print(
        json.dumps(
            {
                "status": report["status"],
                "acceptance": report["acceptance"],
                "retrieve_calls": len(report["retrieve_calls"]),
                "profiled_functions": len(report["functions"]),
            }
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
