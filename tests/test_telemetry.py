"""Metrics sink: recording, batching, percentiles, disk-failure bounds."""

import json

from ai_neko.config.telemetry import Metrics


def test_record_flush_and_percentiles(tmp_path):
    path = tmp_path / "metrics.jsonl"
    metrics = Metrics(path, flush_threshold=4)
    for value in range(1, 101):
        metrics.record("memory_recall_ms", float(value), scope="personal")
    metrics.flush()
    lines = [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines()]
    assert len(lines) == 100
    assert all(line["metric"] == "memory_recall_ms" for line in lines)
    assert all(line["tags"] == {"scope": "personal"} for line in lines)
    stats = metrics.percentiles("memory_recall_ms")
    assert stats["count"] == 100
    assert stats["p50"] == 50 and stats["p95"] == 95 and stats["p99"] == 99
    assert stats["mean"] == 50.5


def test_invalid_inputs_are_ignored(tmp_path):
    metrics = Metrics(tmp_path / "metrics.jsonl")
    for metric in ("Upper", "has space", "你好", "", "x" * 65, "1abc"):
        metrics.record(metric, 1.0)
    for value in ("1", None, True, float("nan"), float("inf")):
        metrics.record("ok_metric", value)
    metrics.record("ok_metric", 5.0, bad_tag="not ascii ok?", other=object())
    metrics.flush()
    assert metrics.percentiles("ok_metric")["count"] == 1


def test_disk_failure_keeps_records_and_counts_drops(tmp_path):
    metrics = Metrics(tmp_path, flush_threshold=2)  # path is a directory
    metrics.record("turn_total_ms", 12.0, status="completed")
    assert metrics.flush() == 0
    assert metrics.pending == 1
    assert metrics.dropped == 0
    stats = metrics.percentiles("turn_total_ms")
    assert stats["count"] == 1


def test_null_path_never_writes_but_percentiles_work():
    metrics = Metrics(None)
    metrics.record("memory_recall_ms", 3.5)
    assert metrics.flush() == 1
    assert metrics.percentiles("memory_recall_ms")["p50"] == 3.5


def test_runtime_records_real_metrics(tmp_path):
    import asyncio

    from test_runtime import Model, Store, settled

    from ai_neko.config.paths import initialize_data_root
    from ai_neko.runtime import SessionRuntime

    async def run():
        paths = initialize_data_root(tmp_path / "runtime-metrics")
        runtime = SessionRuntime(paths, Store(Model(["你好呀", "，很高兴见到你。"])))
        sid = runtime.create_session()["id"]
        tid = (await runtime.start_turn(sid, "打个招呼"))["id"]
        await settled(runtime, sid, tid)
        assert runtime.metrics.percentiles("memory_recall_ms")["count"] == 1
        first = runtime.metrics.percentiles("first_text_ms")
        total = runtime.metrics.percentiles("turn_total_ms")
        assert first is not None and first["count"] == 1
        assert total is not None and total["count"] == 1
        await runtime.close()
        lines = [
            json.loads(line)
            for line in (paths.logs / "metrics.jsonl").read_text(encoding="utf-8").splitlines()
        ]
        names = {line["metric"] for line in lines}
        assert {"memory_recall_ms", "first_text_ms", "turn_total_ms"} <= names
        assert all("打个招呼" not in json.dumps(line, ensure_ascii=False) for line in lines)

    asyncio.run(run())
