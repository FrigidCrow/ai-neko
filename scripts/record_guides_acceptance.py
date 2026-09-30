"""Create and audit explicitly supplied manual G6 records; never invoke a provider.

PASS means the supplied record meets the contract, not that this checker watched
the game, heard playback, verified billing, or authenticated external evidence.
"""

from __future__ import annotations

import argparse
import copy
import hashlib
import ipaddress
import json
import math
import re
from datetime import datetime
from pathlib import Path
from urllib.parse import urlsplit

PHASES = {"generation", "synthesis", "playback"}
MACHINE_FIELDS = (
    "status",
    "output",
    "duration_ms",
    "first_text_ms",
    "text_deliveries",
    "web_calls",
    "retrieval_metrics",
    "retrieval_metrics_status",
    "turn_id",
    "model_calls",
)


def content_hash(value):
    return hashlib.sha256(
        json.dumps(value, ensure_ascii=False, sort_keys=True, allow_nan=False).encode()
    ).hexdigest()


def run_template():
    return {
        "status": None,
        "synthetic": None,
        "question": None,
        "turn_id": None,
        "providers": {key: {"provider": None, "model_id": None} for key in ("model", "asr", "tts")},
        "network_conditions": None,
        "clock_id": None,
        "unit": "ms",
        "output": None,
        "duration_ms": None,
        "first_text_ms": None,
        "text_deliveries": None,
        "web_calls": None,
        "retrieval_metrics": None,
        "retrieval_metrics_status": None,
        "model_calls": None,
        "asr": {"request_id": None, "transcript": None, "reviewer": None, "evidence": None},
        "useful_text": {"end_offset": None, "reviewer": None, "evidence": None},
        "voice": {
            "request_id": None,
            "first_useful_ms": None,
            "clock_id": None,
            "listened": None,
            "actual_hardware_playback": None,
            "reviewer": None,
            "evidence": None,
        },
        "cost": {"amount": None, "currency": None, "basis": None, "evidence": None},
        "evidence": None,
    }


def template():
    return {
        "schema_version": 1,
        "synthetic": None,
        "environment": {
            key: None
            for key in (
                "os",
                "release",
                "arch",
                "hardware",
                "real_hardware",
                "build_id",
                "source_commit",
                "artifact_sha256",
                "evidence",
            )
        },
        "cold_warm": [
            {"id": i, "result": None, "cold": run_template(), "warm": run_template()}
            for i in range(1, 21)
        ],
        "game_rounds": [
            {
                "id": i,
                "result": None,
                "match_id": None,
                "expected_match_id": None,
                "actions": [],
                "mixed_context": None,
                "grounded": None,
                "actionable": None,
                "guide_id": None,
                "revision_id": None,
                "reviewer": None,
                "evidence": None,
            }
            for i in range(1, 11)
        ],
        "public_sources": [
            {
                "id": i,
                "result": None,
                "url": None,
                "question": None,
                "explicit_latest": None,
                "search_succeeded": None,
                "read_succeeded": None,
                "saved": None,
                "adopted": None,
                "warm_web_tool_attempts": None,
                "version_status": None,
                "claimed_latest": None,
                "version_evidence": None,
                "behavior_passed": None,
                "reviewer": None,
                "evidence": None,
            }
            for i in range(1, 4)
        ],
        "stops": [
            {
                "id": i,
                "result": None,
                "phase": None,
                "unit": "ms",
                "clock_id": None,
                "audio_clock_id": None,
                "received_cancel_ms": None,
                "old_audio_stop_ms": None,
                "input_kind": None,
                "utterance_start_ms": None,
                "old_audio_was_playing": None,
                "actual_hardware_playback": None,
                "listened": None,
                "reviewer": None,
                "evidence": None,
            }
            for i in range(1, 21)
        ],
        "imported_probe": None,
    }


def import_probe(probe, *, path, digest):
    """Copy backend data only. Never infer ASR, TTS, useful text, cost or listening."""
    if not isinstance(probe, dict) or not isinstance(probe.get("pairs"), list):
        raise ValueError("Expected a measure_guides JSON report")
    record = template()
    record["synthetic"] = probe.get("synthetic")
    record["imported_probe"] = {
        "path": str(path),
        "sha256": digest,
        "report_sha256": content_hash(probe),
        "report": copy.deepcopy(probe),
    }
    seen = set()
    for item in probe["pairs"]:
        index = item.get("index")
        if type(index) is not int or index not in range(1, 21) or index in seen:
            raise ValueError("Probe pair indices must be unique 1..20; no sample is discarded")
        seen.add(index)
        pair = record["cold_warm"][index - 1]
        pair["result"] = (
            "failed"
            if item.get("status") in {"FAILED", "INELIGIBLE_ROUTE"}
            else "completed"
            if item.get("status") == "CAPTURED_REVIEW_PENDING"
            else None
        )
        for phase in ("cold", "warm"):
            source = item.get(phase)
            if source is None:
                continue
            if not isinstance(source, dict):
                raise ValueError("Invalid probe phase")
            run = pair[phase]
            for key in MACHINE_FIELDS:
                if key in source:
                    run[key] = copy.deepcopy(source[key])
            run.update(
                question=item.get("question"),
                synthetic=probe.get("synthetic"),
                network_conditions=probe.get("network_conditions"),
                clock_id=f"probe:{digest}:{index}:{phase}:perf_counter",
            )
            config = probe.get("config", {})
            run["providers"]["model"] = {
                "provider": config.get("model_base_url"),
                "model_id": config.get("model"),
            }
    return record


class Audit:
    def __init__(self):
        self.issues = []

    def issue(self, path, reason, severity="PENDING"):
        self.issues.append({"path": path, "reason": reason, "severity": severity})

    def obj(self, value, path):
        if not isinstance(value, dict):
            self.issue(path, "object required", "PENDING" if value is None else "FAIL")
            return {}
        return value

    def text(self, value, path):
        if value is None:
            self.issue(path, "unknown")
        elif not isinstance(value, str) or not value.strip():
            self.issue(path, "nonempty text required", "FAIL")
        else:
            return value
        return None

    def number(self, value, path, *, integer=False):
        try:
            finite = type(value) in (int, float) and math.isfinite(value)
        except OverflowError:
            finite = False
        if value is None:
            self.issue(path, "unknown")
        elif not finite or value < 0 or (integer and type(value) is not int):
            self.issue(path, "finite nonnegative number required", "FAIL")
        else:
            return value
        return None

    def flag(self, value, path, *, expected=None):
        if value is None:
            self.issue(path, "unknown")
        elif type(value) is not bool:
            self.issue(path, "boolean required", "FAIL")
        elif expected is not None and value != expected:
            self.issue(path, f"must be {expected}", "FAIL")
        return value if type(value) is bool else None

    def choice(self, value, path, options):
        if value is None:
            self.issue(path, "unknown")
        elif value not in list(options):
            self.issue(path, "unsupported value", "FAIL")
            return None
        return value

    def evidence(self, value, path):
        value = self.obj(value, path)
        self.text(value.get("path"), path + ".path")
        stamp = self.text(value.get("recorded_at"), path + ".recorded_at")
        if stamp:
            try:
                if datetime.fromisoformat(stamp.replace("Z", "+00:00")).tzinfo is None:
                    raise ValueError
            except ValueError:
                self.issue(path + ".recorded_at", "ISO timestamp with timezone required", "FAIL")
        digest = self.text(value.get("sha256"), path + ".sha256")
        if digest and not re.fullmatch(r"[0-9a-f]{64}", digest):
            self.issue(path + ".sha256", "SHA256 required", "FAIL")

    def rows(self, record, key, count):
        rows = record.get(key)
        if not isinstance(rows, list):
            self.issue(key, "sample list required", "PENDING" if rows is None else "FAIL")
            return []
        if len(rows) != count:
            self.issue(key, f"retain exactly {count} samples, including failures", "FAIL")
        ids = [r.get("id") if isinstance(r, dict) else None for r in rows]
        if any(type(value) is not int for value in ids) or ids != list(range(1, count + 1)):
            self.issue(key, "ordered unique sample IDs required; no omission/replacement", "FAIL")
        return [self.obj(row, f"{key}[{i}]") for i, row in enumerate(rows)]

    def result(self, row, path):
        result = self.choice(row.get("result"), path + ".result", {"completed", "failed"})
        if result == "failed":
            self.issue(path + ".result", "failed sample retained", "FAIL")

    def status(self, start=0):
        levels = {i["severity"] for i in self.issues[start:]}
        return "FAIL" if "FAIL" in levels else "PENDING" if levels else "PASS"


def audit_run(a, run, path, phase):
    run = a.obj(run, path)
    status = a.choice(run.get("status"), path + ".status", {"completed", "failed", "cancelled"})
    if status in {"failed", "cancelled"}:
        a.issue(path + ".status", "unsuccessful run retained", "FAIL")
    synthetic = a.flag(run.get("synthetic"), path + ".synthetic")
    if synthetic:
        a.issue(path, "synthetic measurements are not real acceptance")
    a.text(run.get("question"), path + ".question")
    turn_id = a.text(run.get("turn_id"), path + ".turn_id")
    providers = a.obj(run.get("providers"), path + ".providers")
    for kind in ("model", "asr", "tts"):
        identity = a.obj(providers.get(kind), path + ".providers." + kind)
        for key in ("provider", "model_id"):
            a.text(identity.get(key), f"{path}.providers.{kind}.{key}")
    a.text(run.get("network_conditions"), path + ".network_conditions")
    asr = a.obj(run.get("asr"), path + ".asr")
    for key in ("request_id", "transcript", "reviewer"):
        a.text(asr.get(key), path + ".asr." + key)
    if asr.get("transcript") is not None and asr.get("transcript") != run.get("question"):
        a.issue(path + ".asr.transcript", "ASR transcript must match the measured question", "FAIL")
    a.evidence(asr.get("evidence"), path + ".asr.evidence")
    clock = a.text(run.get("clock_id"), path + ".clock_id")
    a.choice(run.get("unit"), path + ".unit", {"ms"})
    a.evidence(run.get("evidence"), path + ".evidence")
    duration = a.number(run.get("duration_ms"), path + ".duration_ms")
    first = a.number(run.get("first_text_ms"), path + ".first_text_ms")
    output = a.text(run.get("output"), path + ".output")
    deliveries = run.get("text_deliveries")
    valid_deliveries = []
    if not isinstance(deliveries, list) or not deliveries:
        a.issue(path + ".text_deliveries", "backend delivery timeline missing")
    else:
        end_before, ms_before = 0, -1
        for i, delivery in enumerate(deliveries):
            delivery = a.obj(delivery, f"{path}.text_deliveries[{i}]")
            end = a.number(delivery.get("text_end"), path + ".text_end", integer=True)
            ms = a.number(delivery.get("delivered_ms"), path + ".delivered_ms")
            if end is None or ms is None:
                continue
            if (
                end <= end_before
                or ms < ms_before
                or (output and end > len(output))
                or (duration is not None and ms > duration)
            ):
                a.issue(path + ".text_deliveries", "nonmonotonic/out-of-range timeline", "FAIL")
            end_before, ms_before = end, ms
            valid_deliveries.append((end, ms))
        if output and end_before != len(output):
            a.issue(path + ".text_deliveries", "timeline does not cover exact output", "FAIL")
        if first is not None and valid_deliveries and first != valid_deliveries[0][1]:
            a.issue(path + ".first_text_ms", "does not match first backend delivery", "FAIL")
    useful = a.obj(run.get("useful_text"), path + ".useful_text")
    offset = a.number(useful.get("end_offset"), path + ".useful_text.end_offset", integer=True)
    a.text(useful.get("reviewer"), path + ".useful_text.reviewer")
    a.evidence(useful.get("evidence"), path + ".useful_text.evidence")
    useful_ms = None
    if offset is not None:
        if not output or not 1 <= offset <= len(output):
            a.issue(
                path + ".useful_text.end_offset",
                "exclusive Unicode character offset outside output",
                "FAIL",
            )
        else:
            useful_ms = next((ms for end, ms in valid_deliveries if end >= offset), None)
    voice = a.obj(run.get("voice"), path + ".voice")
    a.text(voice.get("request_id"), path + ".voice.request_id")
    voice_ms = a.number(voice.get("first_useful_ms"), path + ".voice.first_useful_ms")
    if voice_ms is not None and first is not None and voice_ms < first:
        a.issue(
            path + ".voice.first_useful_ms",
            "audible answer cannot precede first backend text",
            "FAIL",
        )
    voice_clock = a.text(voice.get("clock_id"), path + ".voice.clock_id")
    if clock and voice_clock and clock != voice_clock:
        a.issue(
            path + ".voice.clock_id",
            "voice and backend timestamps must share run origin/clock",
            "FAIL",
        )
    for key in ("listened", "actual_hardware_playback"):
        a.flag(voice.get(key), path + ".voice." + key, expected=True)
    a.text(voice.get("reviewer"), path + ".voice.reviewer")
    a.evidence(voice.get("evidence"), path + ".voice.evidence")
    calls = run.get("web_calls")
    if not isinstance(calls, list):
        a.issue(path + ".web_calls", "web tool attempt list missing")
    elif phase == "warm" and calls:
        a.issue(path + ".web_calls", "warm route must have zero web tool attempts", "FAIL")
    elif phase == "cold":
        names = {c.get("name") for c in calls if isinstance(c, dict) and c.get("status") == "ok"}
        if not {"search_web", "read_web_page"} <= names:
            a.issue(
                path + ".web_calls", "cold route requires successful search and body read", "FAIL"
            )
    metrics = run.get("retrieval_metrics")
    metric_status = a.choice(
        run.get("retrieval_metrics_status"),
        path + ".retrieval_metrics_status",
        {"recorded", "unknown"},
    )
    if metric_status == "unknown":
        a.issue(path + ".retrieval_metrics_status", "retrieval instrumentation incomplete")
    retrieval_ms = []
    if not isinstance(metrics, list) or not metrics:
        a.issue(path + ".retrieval_metrics", "phase-specific retrieval measurements missing")
    else:
        for metric in metrics:
            metric = a.obj(metric, path + ".retrieval_metrics[]")
            a.choice(metric.get("metric"), path + ".metric", {"guide_retrieve_ms"})
            owner = a.text(metric.get("turn_id"), path + ".retrieval_metrics[].turn_id")
            if owner and turn_id and owner != turn_id:
                a.issue(
                    path + ".retrieval_metrics[].turn_id",
                    "metric belongs to a different turn",
                    "FAIL",
                )
            value = a.number(metric.get("value"), path + ".retrieval_ms")
            if value is not None:
                retrieval_ms.append(value)
    model_calls = run.get("model_calls")
    tokens = []
    if not isinstance(model_calls, list) or not model_calls:
        a.issue(path + ".model_calls", "actual model attempts and usage missing")
    else:
        for i, call in enumerate(model_calls):
            call = a.obj(call, path + ".model_calls[]")
            if call.get("status") != "completed":
                a.issue(path + ".model_calls[]", "unsuccessful/unknown model attempt", "FAIL")
            usage = a.obj(call.get("usage"), f"{path}.model_calls[{i}].usage")
            values = [
                a.number(usage.get(key), path + ".usage." + key, integer=True)
                for key in ("prompt_tokens", "completion_tokens", "total_tokens")
            ]
            if all(v is not None for v in values):
                if values[0] + values[1] != values[2]:
                    a.issue(path + ".usage", "token total mismatch", "FAIL")
                tokens.append(values[2])
    cost = a.obj(run.get("cost"), path + ".cost")
    amount = a.number(cost.get("amount"), path + ".cost.amount")
    currency = a.text(cost.get("currency"), path + ".cost.currency")
    if currency and not re.fullmatch(r"[A-Z]{3}", currency):
        a.issue(path + ".cost.currency", "three-letter currency required", "FAIL")
    a.text(cost.get("basis"), path + ".cost.basis")
    a.evidence(cost.get("evidence"), path + ".cost.evidence")
    return {
        "first_backend_text_ms": first,
        "useful_backend_text_ms": useful_ms,
        "useful_voice_ms": voice_ms,
        "retrieval_ms": retrieval_ms,
        "reported_total_tokens": sum(tokens)
        if isinstance(model_calls, list) and model_calls and len(tokens) == len(model_calls)
        else None,
        "cost": {"amount": amount, "currency": currency},
        "web_tool_attempts": len(calls) if isinstance(calls, list) else None,
    }


def validate_import(a, record, imported):
    """Imported failures and machine facts remain authoritative inside this record."""
    imported = a.obj(imported, "imported_probe")
    probe = a.obj(imported.get("report"), "imported_probe.report")
    for field in ("path", "sha256", "report_sha256"):
        a.text(imported.get(field), "imported_probe." + field)
    if probe.get("synthetic") is not False:
        a.issue(
            "imported_probe", "imported evidence is not explicitly live; cannot relabel it real"
        )
    if imported.get("report_sha256") is not None and imported["report_sha256"] != content_hash(
        probe
    ):
        a.issue("imported_probe", "retained probe content changed", "FAIL")
    rows = probe.get("pairs")
    if not isinstance(rows, list):
        a.issue("imported_probe.pairs", "original pair list missing")
        return
    if len(rows) != 20:
        a.issue("imported_probe.pairs", "probe did not capture all 20 planned pairs")
    try:
        expected = import_probe(probe, path=imported.get("path"), digest=imported.get("sha256"))
    except (ValueError, TypeError, AttributeError):
        a.issue("imported_probe", "invalid retained probe structure", "FAIL")
        return
    actual = record.get("cold_warm")
    if not isinstance(actual, list) or len(actual) != 20:
        return  # Main sample validation reports this.
    for row in rows:
        index = row["index"] - 1
        target = actual[index]
        reference = expected["cold_warm"][index]
        if not isinstance(target, dict):
            continue
        if row.get("status") in {"FAILED", "INELIGIBLE_ROUTE"}:
            a.issue(f"imported_probe.pairs[{index}]", "failed probe sample retained", "FAIL")
        if target.get("result") != reference["result"]:
            a.issue(f"cold_warm[{index}].result", "cannot override imported outcome", "FAIL")
        for phase in ("cold", "warm"):
            source = row.get(phase)
            if not isinstance(source, dict):
                a.issue(f"imported_probe.pairs[{index}].{phase}", "phase not captured")
                continue
            changed = target.get(phase)
            if not isinstance(changed, dict):
                continue
            original = reference[phase]
            for key in (*MACHINE_FIELDS, "question", "synthetic", "network_conditions", "clock_id"):
                if changed.get(key) != original.get(key):
                    a.issue(
                        f"cold_warm[{index}].{phase}.{key}",
                        "cannot rewrite imported machine data",
                        "FAIL",
                    )
            providers = changed.get("providers")
            if (
                not isinstance(providers, dict)
                or providers.get("model") != original["providers"]["model"]
            ):
                a.issue(
                    f"cold_warm[{index}].{phase}.providers.model",
                    "cannot rewrite imported model identity",
                    "FAIL",
                )


def distribution(values):
    values = sorted(v for v in values if v is not None)
    return {
        "count": len(values),
        "mean": sum(values) / len(values) if values else None,
        "p95": values[math.ceil(len(values) * 0.95) - 1] if values else None,
        "maximum": max(values) if values else None,
    }


def audit(record):
    a = Audit()
    record = a.obj(record, "record")
    if type(record.get("schema_version")) is not int or record.get("schema_version") != 1:
        a.issue("schema_version", "integer schema version 1 required", "FAIL")
    if a.flag(record.get("synthetic"), "synthetic"):
        a.issue("synthetic", "synthetic evidence can never pass real acceptance")
    imported = record.get("imported_probe")
    if imported is not None:
        validate_import(a, record, imported)
    env = a.obj(record.get("environment"), "environment")
    for key in (
        "os",
        "release",
        "arch",
        "hardware",
        "build_id",
        "source_commit",
        "artifact_sha256",
    ):
        a.text(env.get(key), "environment." + key)
    for key, length in (("source_commit", 40), ("artifact_sha256", 64)):
        value = env.get(key)
        if isinstance(value, str) and not re.fullmatch(f"[0-9a-f]{{{length}}}", value):
            a.issue("environment." + key, "invalid hex digest", "FAIL")
    a.flag(env.get("real_hardware"), "environment.real_hardware", expected=True)
    a.evidence(env.get("evidence"), "environment.evidence")
    qualification = a.status()
    sections = {}
    start = len(a.issues)
    pairs = a.rows(record, "cold_warm", 20)
    pair_results = []
    for i, pair in enumerate(pairs):
        path = f"cold_warm[{i}]"
        at = len(a.issues)
        a.result(pair, path)
        metrics = {
            phase: audit_run(a, pair.get(phase), path + "." + phase, phase)
            for phase in ("cold", "warm")
        }
        cold, warm = pair.get("cold"), pair.get("warm")
        if isinstance(cold, dict) and isinstance(warm, dict):
            for key in ("question", "providers", "network_conditions"):
                if cold.get(key) != warm.get(key):
                    a.issue(path + "." + key, "cold/warm inputs differ", "FAIL")
            if cold.get("turn_id") is not None and cold.get("turn_id") == warm.get("turn_id"):
                a.issue(path, "cold and warm must be separate recorded turns", "FAIL")
        pair_results.append(
            {
                "id": pair.get("id"),
                "result": pair.get("result"),
                "status": a.status(at),
                "metrics": metrics,
            }
        )
    summary = {
        phase: {
            key: distribution([p["metrics"][phase][key] for p in pair_results])
            for key in ("first_backend_text_ms", "useful_backend_text_ms", "useful_voice_ms")
        }
        for phase in ("cold", "warm")
    }
    savings = {
        key: distribution(
            [
                p["metrics"]["cold"][key] - p["metrics"]["warm"][key]
                for p in pair_results
                if p["metrics"]["cold"][key] is not None and p["metrics"]["warm"][key] is not None
            ]
        )
        for key in ("useful_backend_text_ms", "useful_voice_ms")
    }
    sections["cold_warm"] = {
        "status": a.status(start),
        "count": len(pairs),
        "samples": pair_results,
        "timing_summary_ms": summary,
        "cold_minus_warm_ms": savings,
        "summary_scope": "available values only; counts expose incomplete data, not qualification",
    }
    start = len(a.issues)
    games = a.rows(record, "game_rounds", 10)
    if not (env.get("os") == "Windows" and env.get("release") == "11" and env.get("arch") == "x64"):
        a.issue("game_rounds", "requires Windows 11 x64 real-game evidence")
    actions, good, judged = set(), 0, 0
    for i, row in enumerate(games):
        path = f"game_rounds[{i}]"
        a.result(row, path)
        for key in ("match_id", "expected_match_id", "guide_id", "revision_id", "reviewer"):
            a.text(row.get(key), path + "." + key)
        if row.get("match_id") != row.get("expected_match_id"):
            a.issue(path, "mixed match binding", "FAIL")
        a.flag(row.get("mixed_context"), path + ".mixed_context", expected=False)
        grounded = a.flag(row.get("grounded"), path + ".grounded")
        actionable = a.flag(row.get("actionable"), path + ".actionable")
        judged += grounded is not None and actionable is not None
        good += grounded is True and actionable is True
        for action in row.get("actions", []) if isinstance(row.get("actions"), list) else [None]:
            a.choice(action, path + ".actions", {"switch_guide", "new_match", "restart"})
            if isinstance(action, str):
                actions.add(action)
        a.evidence(row.get("evidence"), path + ".evidence")
    missing = {"switch_guide", "new_match", "restart"} - actions
    if missing:
        a.issue("game_rounds.actions", "missing " + ", ".join(sorted(missing)))
    if judged == 10 and good < 9:
        a.issue("game_rounds", "fewer than 9/10 grounded and actionable rounds", "FAIL")
    sections["game_rounds"] = {
        "status": a.status(start),
        "count": len(games),
        "judged": judged,
        "grounded_and_actionable": good,
        "quality_failures": [
            r.get("id") for r in games if r.get("grounded") is False or r.get("actionable") is False
        ],
    }
    start = len(a.issues)
    sources = a.rows(record, "public_sources", 3)
    latest = 0
    for i, row in enumerate(sources):
        path = f"public_sources[{i}]"
        a.result(row, path)
        url = a.text(row.get("url"), path + ".url")
        if url:
            try:
                parsed = urlsplit(url)
                if (
                    parsed.scheme not in {"https", "http"}
                    or not parsed.hostname
                    or parsed.username
                    or parsed.password
                ):
                    raise ValueError
                host = parsed.hostname.lower()
                if host == "localhost" or host.endswith(
                    (".test", ".invalid", ".local", ".internal")
                ):
                    raise ValueError
                try:
                    address = ipaddress.ip_address(host)
                except ValueError:
                    address = None
                if address is not None and not address.is_global:
                    raise ValueError
            except ValueError:
                a.issue(
                    path + ".url", "public URL required; no DNS/network lookup performed", "FAIL"
                )
        a.text(row.get("question"), path + ".question")
        latest += a.flag(row.get("explicit_latest"), path + ".explicit_latest") is True
        for key in ("search_succeeded", "read_succeeded", "saved", "adopted", "behavior_passed"):
            a.flag(row.get(key), path + "." + key, expected=True)
        attempts = a.number(
            row.get("warm_web_tool_attempts"), path + ".warm_web_tool_attempts", integer=True
        )
        if attempts is not None and attempts != 0:
            a.issue(path, "reuse requires zero web tool attempts", "FAIL")
        version = a.choice(
            row.get("version_status"), path + ".version_status", {"verified", "unknown"}
        )
        claimed = a.flag(row.get("claimed_latest"), path + ".claimed_latest")
        if claimed:
            if version != "verified":
                a.issue(path, "unknown version cannot be claimed latest", "FAIL")
            a.evidence(row.get("version_evidence"), path + ".version_evidence")
        a.text(row.get("reviewer"), path + ".reviewer")
        a.evidence(row.get("evidence"), path + ".evidence")
    if not latest:
        a.issue("public_sources", "one explicit_latest flow required")
    sections["public_sources"] = {
        "status": a.status(start),
        "count": len(sources),
        "explicit_latest": latest,
        "latest_obtained": sum(
            r.get("version_status") == "verified" and r.get("claimed_latest") is True
            for r in sources
        ),
    }
    start = len(a.issues)
    stops = a.rows(record, "stops", 20)
    phases, latencies, user_latencies = set(), [], []
    for i, row in enumerate(stops):
        path = f"stops[{i}]"
        a.result(row, path)
        phase = a.choice(row.get("phase"), path + ".phase", PHASES)
        if isinstance(phase, str):
            phases.add(phase)
        a.choice(row.get("unit"), path + ".unit", {"ms"})
        for key in ("clock_id", "audio_clock_id", "reviewer"):
            a.text(row.get(key), path + "." + key)
        if row.get("clock_id") != row.get("audio_clock_id"):
            a.issue(path, "cancel/audio clocks must share origin", "FAIL")
        cancel = a.number(row.get("received_cancel_ms"), path + ".received_cancel_ms")
        stop = a.number(row.get("old_audio_stop_ms"), path + ".old_audio_stop_ms")
        if cancel is not None and stop is not None:
            if stop < cancel:
                a.issue(path, "audio stop precedes received cancellation", "FAIL")
            else:
                latencies.append(stop - cancel)
        kind = a.choice(row.get("input_kind"), path + ".input_kind", {"speech", "button"})
        if kind == "speech" or row.get("utterance_start_ms") is not None:
            onset = a.number(row.get("utterance_start_ms"), path + ".utterance_start_ms")
            if onset is not None and cancel is not None:
                if onset > cancel:
                    a.issue(path, "utterance starts after cancellation", "FAIL")
                else:
                    user_latencies.append(cancel - onset)
        for key in ("old_audio_was_playing", "actual_hardware_playback", "listened"):
            a.flag(row.get(key), path + "." + key, expected=True)
        a.evidence(row.get("evidence"), path + ".evidence")
    if PHASES - phases:
        a.issue("stops.phase", "all generation/synthesis/playback boundaries required")
    p95 = sorted(latencies)[math.ceil(len(latencies) * 0.95) - 1] if latencies else None
    if len(latencies) == 20 and p95 > 300:
        a.issue("stops", "received_cancel to old_audio_stop p95 exceeds 300ms", "FAIL")
    sections["stops"] = {
        "status": a.status(start),
        "count": len(stops),
        "valid_time_samples": len(latencies),
        "cancel_to_audio_stop_ms": latencies,
        "p95_ms": p95,
        "utterance_to_received_cancel_ms": user_latencies,
        "percentile_method": "nearest rank",
        "target_ms": 300,
    }
    for section in sections.values():
        if qualification != "PASS" and section["status"] == "PASS":
            section["status"] = qualification
    return {
        "schema_version": 1,
        "status": a.status(),
        "qualification": qualification,
        "sections": sections,
        "issues": a.issues,
        "boundaries": [
            "Manual record consistency audit; evidence files are not opened or authenticated.",
            "Backend delivery is not UI rendering. Useful text/voice require human review.",
            "Web counts are tool attempts, not HTTP connections, redirects or bytes.",
            "No provider, game, microphone, screen, credential store or upload is accessed.",
        ],
    }


def read_json(path):
    def unique(pairs):
        result = {}
        for key, value in pairs:
            if key in result:
                raise ValueError("Duplicate JSON key; preserve samples instead of overwriting keys")
            result[key] = value
        return result

    data = path.read_bytes()
    if len(data) > 30_000_000:
        raise ValueError("Record too large")
    return json.loads(data, object_pairs_hook=unique), hashlib.sha256(data).hexdigest()


def write_new(path, value):
    text = json.dumps(value, ensure_ascii=False, indent=2, allow_nan=False) + "\n"
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("x", encoding="utf-8") as stream:
        stream.write(text)


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)
    init = commands.add_parser("init", help="create a new empty/manual template")
    init.add_argument("--output", type=Path, required=True)
    init.add_argument("--probe", type=Path, help="explicit read-only measure_guides report import")
    check = commands.add_parser("check", help="audit only the supplied record")
    check.add_argument("record", type=Path)
    check.add_argument("--output", type=Path, required=True)
    args = parser.parse_args(argv)
    try:
        if args.output.exists():
            raise ValueError("Output exists; preserve prior evidence and choose a new path")
        if args.command == "init":
            value = template()
            if args.probe:
                probe, digest = read_json(args.probe)
                value = import_probe(probe, path=args.probe, digest=digest)
            write_new(args.output, value)
            status = audit(value)["status"]
        else:
            record, digest = read_json(args.record)
            value = audit(record)
            value.update(record_path=str(args.record), record_sha256=digest)
            write_new(args.output, value)
            status = value["status"]
        print(json.dumps({"status": status, "output": str(args.output)}))
        return 0 if args.command == "init" or status == "PASS" else 2 if status == "PENDING" else 1
    except (OSError, ValueError, TypeError) as error:
        parser.error(str(error))


if __name__ == "__main__":
    raise SystemExit(main())
