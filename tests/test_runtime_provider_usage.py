"""Actual adapter + synthetic HTTP tests for durable, content-free accounting."""

import asyncio
import json
import os
import sqlite3
import subprocess
import sys
from pathlib import Path
from uuid import uuid4

import httpx
import pytest
from test_providers import BytesStream, delta, event
from test_providers import provider_http as provider_http

from ai_neko.config.paths import initialize_data_root
from ai_neko.providers import ModelAdapter
from ai_neko.runtime import SessionRuntime
from ai_neko.tools import network

USAGE = {"prompt_tokens": 7, "completion_tokens": 2, "total_tokens": 9}


class RealStore:
    def __init__(self, *, key="synthetic-secret-key", model="synthetic-model"):
        self.key, self.name = key, model

    def model(self):
        return ModelAdapter(
            {"model": self.name, "model_base_url": "https://model.example/v1"}, self.key
        )


def summary(runtime, sid):
    return runtime.get_session(sid)["turns"][0]["provider_usage"]


async def run_turn(runtime, sid, *, request_id=None, text="private synthetic prompt"):
    turn = await runtime.start_turn(sid, text, request_id=request_id)
    task = runtime._tasks.get(turn["id"])
    if task:
        await task
    return turn["id"]


def test_actual_http_usage_survives_reconnect_duplicate_request_and_restart(
    tmp_path, provider_http
):
    requests = []
    provider_http(
        [
            delta({"content": "private synthetic response"}),
            event({"choices": [], "usage": {**USAGE, "raw_secret": "never store"}}),
            event("[DONE]"),
        ],
        inspect=lambda request: requests.append(json.loads(request.content)),
    )
    paths = initialize_data_root(tmp_path / "usage")

    async def run():
        runtime = SessionRuntime(paths, RealStore())
        sid = runtime.create_session()["id"]
        rid = uuid4().hex
        tid = await run_turn(runtime, sid, request_id=rid)
        first = summary(runtime, sid)
        assert len(requests) == first["attempt_count"] == 1
        assert first["response_count"] == 1 and first["http_retry_count"] == 0
        assert first["reported_token_totals"] == USAGE and first["usage_complete"]
        assert first["scopes"]["memory_extraction"]["state"] == "not_scheduled"
        assert first["scopes"]["memory_extraction"]["attempt_count"] == 0
        # A duplicated finished observation updates one ID, never sums snapshots.
        runtime._record_provider_request(
            tid,
            "chat_generation",
            {
                "type": "provider_request",
                "phase": "finished",
                "call_id": first["records"][0]["call_id"],
                "outcome": "completed",
                "usage": USAGE,
                "usage_status": "reported",
                "error_code": None,
            },
        )
        for _ in range(3):
            batch = runtime.events(sid, tid)
            runtime.ack(sid, tid, batch["sent_seq"])
            duplicate = await runtime.start_turn(sid, "private synthetic prompt", request_id=rid)
            assert duplicate["id"] == tid and summary(runtime, sid) == first
        persisted = " ".join(
            str(tuple(row)) for row in runtime._db.execute("SELECT * FROM provider_requests")
        )
        for secret in ("synthetic-secret-key", "private synthetic", "model.example", "never store"):
            assert secret not in persisted
        assert "stream_options" not in requests[0]
        await runtime.close()
        reopened = SessionRuntime(paths, RealStore())
        assert summary(reopened, sid) == first
        duplicate = await reopened.start_turn(sid, "private synthetic prompt", request_id=rid)
        assert duplicate["id"] == tid and len(requests) == 1
        await reopened.close()

    asyncio.run(run())


@pytest.mark.parametrize(
    "reported", [None, {}, {"prompt_tokens": True, "completion_tokens": 2, "total_tokens": 3}]
)
def test_missing_or_invalid_usage_is_unknown_not_zero(tmp_path, provider_http, reported):
    provider_http([delta({"content": "ok"}), event({"usage": reported}), event("[DONE]")])

    async def run():
        runtime = SessionRuntime(initialize_data_root(tmp_path / "unknown"), RealStore())
        sid = runtime.create_session()["id"]
        await run_turn(runtime, sid)
        value = summary(runtime, sid)
        assert value["attempt_count"] == 1 and value["unknown_usage_count"] == 1
        assert value["reported_token_totals"] is None and not value["usage_complete"]
        assert value["records"][0]["outcome"] == "completed"
        await runtime.close()

    asyncio.run(run())


def test_interrupted_usage_is_partial_and_survives_unsent_event_deletion(tmp_path, provider_http):
    provider_http([delta({"content": "unsent response"}), event({"usage": USAGE})])

    async def run():
        runtime = SessionRuntime(initialize_data_root(tmp_path / "partial"), RealStore())
        sid = runtime.create_session()["id"]
        await run_turn(runtime, sid)
        turn = runtime.get_session(sid)["turns"][0]
        value = turn["provider_usage"]
        assert turn["status"] == "error" and turn["delivered_text"] == ""
        assert value["attempt_count"] == 1 and value["partial_usage_count"] == 1
        assert value["reported_token_totals"] is None and not value["usage_complete"]
        assert value["records"][0]["usage"] == USAGE
        assert value["records"][0]["error_code"] == "stream_interrupted"
        await runtime.close()

    asyncio.run(run())


@pytest.mark.parametrize("status", [401, 429, 503])
def test_http_failure_counts_one_attempt_without_automatic_retry(tmp_path, provider_http, status):
    requests = []
    provider_http(status=status, inspect=lambda request: requests.append(request))

    async def run():
        runtime = SessionRuntime(initialize_data_root(tmp_path / "failure"), RealStore())
        sid = runtime.create_session()["id"]
        await run_turn(runtime, sid)
        value = summary(runtime, sid)
        assert value["attempt_count"] == len(requests) == 1
        assert value["http_retry_count"] == 0 and value["unknown_usage_count"] == 1
        assert value["records"][0]["http_status"] == status
        assert value["records"][0]["outcome"] == "failed"
        await runtime.close()

    asyncio.run(run())


@pytest.mark.parametrize("store", [RealStore(key=None), RealStore(model="")])
def test_preflight_failure_is_known_zero_http_attempts(tmp_path, provider_http, store):
    provider_http(inspect=lambda request: pytest.fail("preflight must not dispatch HTTP"))

    async def run():
        runtime = SessionRuntime(initialize_data_root(tmp_path / "preflight"), store)
        sid = runtime.create_session()["id"]
        await run_turn(runtime, sid)
        value = summary(runtime, sid)
        assert value["attempt_count"] == 0 and value["attempt_count_complete"]
        assert value["scopes"]["chat_generation"]["state"] == "settled"
        assert value["records"] == []
        await runtime.close()

    asyncio.run(run())


def test_start_commit_failure_prevents_http_dispatch(tmp_path, provider_http, monkeypatch):
    provider_http(inspect=lambda request: pytest.fail("failed accounting must not dispatch HTTP"))

    async def run():
        runtime = SessionRuntime(initialize_data_root(tmp_path / "commit-failure"), RealStore())
        sid = runtime.create_session()["id"]
        runtime._db.execute("""CREATE TRIGGER fail_provider_start BEFORE INSERT ON provider_requests
            BEGIN SELECT RAISE(ABORT, 'synthetic write failure'); END""")
        await run_turn(runtime, sid)
        assert summary(runtime, sid)["attempt_count"] == 0
        assert runtime.get_session(sid)["turns"][0]["error"] == "provider_tracking_failed"
        await runtime.close()

    asyncio.run(run())


def test_cancel_finishes_existing_request_after_turn_settlement(tmp_path, monkeypatch):
    async def run():
        entered = asyncio.Event()

        class BlockingStream(httpx.AsyncByteStream):
            async def __aiter__(self):
                entered.set()
                yield delta({"content": "unsent"})
                await asyncio.Event().wait()

        async def pin(url, **kwargs):
            return httpx.URL(url), {}, {}

        monkeypatch.setattr(network, "pin_url", pin)
        monkeypatch.setattr(
            network,
            "client",
            lambda: httpx.AsyncClient(
                transport=httpx.MockTransport(
                    lambda request: httpx.Response(200, stream=BlockingStream())
                )
            ),
        )
        runtime = SessionRuntime(initialize_data_root(tmp_path / "cancel"), RealStore())
        sid = runtime.create_session()["id"]
        tid = (await runtime.start_turn(sid, "cancel synthetic"))["id"]
        task = runtime._tasks[tid]
        await asyncio.wait_for(entered.wait(), 5)
        await runtime.cancel_turn(sid, tid)
        with pytest.raises(asyncio.CancelledError):
            await task
        value = summary(runtime, sid)
        assert value["attempt_count"] == 1
        assert value["records"][0]["outcome"] == "cancelled"
        assert value["records"][0]["finished_at"] is not None
        assert runtime.get_session(sid)["turns"][0]["delivered_text"] == ""
        await runtime.close()

    asyncio.run(run())


def test_unsupported_adapter_and_legacy_turn_are_unknown_not_zero(tmp_path):
    from test_runtime import Store

    async def run():
        paths = initialize_data_root(tmp_path / "legacy")
        runtime = SessionRuntime(paths, Store())
        sid = runtime.create_session()["id"]
        await run_turn(runtime, sid)
        value = summary(runtime, sid)
        assert value["attempt_count"] is None and not value["attempt_count_complete"]
        assert value["scopes"]["chat_generation"]["coverage"] == "unavailable"
        await runtime.close()
        with sqlite3.connect(paths.memory / "conversation.sqlite") as db:
            db.execute("DROP TABLE provider_requests")
            db.execute("DROP TABLE turn_provider_tracking")
            db.execute("PRAGMA user_version=6")
        reopened = SessionRuntime(paths, RealStore())
        value = summary(reopened, sid)
        assert value["attempt_count"] is None and value["recorded_attempt_count"] == 0
        assert value["scopes"]["chat_generation"]["coverage"] == "legacy"
        assert reopened._db.execute("PRAGMA user_version").fetchone()[0] == 7
        assert not (paths.backups / "conversation-pre-migration-v7.sqlite").exists()
        await reopened.close()

    asyncio.run(run())


def test_memory_job_attempts_are_separate_from_http_retries(tmp_path, monkeypatch):
    requests = []

    async def pin(url, **kwargs):
        return httpx.URL(url), {}, {}

    def handle(request):
        payload = json.loads(request.content)
        extraction = "从用户原话中整理" in payload["messages"][0]["content"]
        requests.append("extraction" if extraction else "chat")
        if extraction and requests.count("extraction") == 1:
            return httpx.Response(503)
        text = '{"facts":[]}' if extraction else "answer"
        return httpx.Response(
            200,
            stream=BytesStream(
                [delta({"content": text}), event({"usage": USAGE}), event("[DONE]")]
            ),
        )

    monkeypatch.setattr(network, "pin_url", pin)
    monkeypatch.setattr(
        network, "client", lambda: httpx.AsyncClient(transport=httpx.MockTransport(handle))
    )

    async def run():
        paths = initialize_data_root(tmp_path / "memory")
        runtime = SessionRuntime(paths, RealStore())
        runtime.memory_preferences.update({"auto_extract": True})
        sid = runtime.create_session()["id"]
        await run_turn(runtime, sid)
        await runtime._memory_task
        before = summary(runtime, sid)
        assert before["scopes"]["memory_extraction"]["state"] == "retry_pending"
        assert before["attempt_count"] == 2 and before["http_retry_count"] == 0
        # Invoke the existing worker's next iteration without sleeping 30 seconds.
        await runtime._process_memories()
        value = summary(runtime, sid)
        assert requests == ["chat", "extraction", "extraction"]
        assert value["attempt_count"] == 3 and value["http_retry_count"] == 0
        assert value["scopes"]["chat_generation"]["attempt_count"] == 1
        extraction = value["scopes"]["memory_extraction"]
        assert extraction["attempt_count"] == 2 and extraction["state"] == "settled"
        items = [r for r in value["records"] if r["scope"] == "memory_extraction"]
        assert [item["job_attempt"] for item in items] == [1, 2]
        assert len({item["job_id"] for item in items}) == 1
        assert value["reported_token_totals"]["total_tokens"] == 18
        assert not value["usage_complete"]  # 503 request never reported token usage.
        await runtime.close()
        reopened = SessionRuntime(paths, RealStore())
        assert summary(reopened, sid) == value
        await reopened.close()

    asyncio.run(run())


def test_erasure_removes_target_ledger_without_removing_other_session(tmp_path, provider_http):
    provider_http([delta({"content": "answer"}), event({"usage": USAGE}), event("[DONE]")])

    async def run():
        runtime = SessionRuntime(initialize_data_root(tmp_path / "erase"), RealStore())
        sid = runtime.create_session()["id"]
        tid = await run_turn(runtime, sid)
        other = runtime.create_session()["id"]
        await run_turn(runtime, other, text="unrelated independent request")
        fact = runtime.memory.remember(
            "private synthetic prompt",
            source_id="turn:" + tid,
            source_text="private synthetic prompt",
        )
        before = summary(runtime, other)
        await runtime.forget_memory(fact["id"])
        for table in ("provider_requests", "turn_provider_tracking"):
            assert (
                runtime._db.execute(f"SELECT 1 FROM {table} WHERE turn_id=?", (tid,)).fetchone()
                is None
            )
        assert summary(runtime, other) == before
        assert summary(runtime, sid)["attempt_count"] is None
        await runtime.close()

    asyncio.run(run())


@pytest.mark.parametrize("scope", ["chat_generation", "memory_extraction"])
def test_consumer_output_limit_closes_actual_request_before_scope_returns(
    tmp_path, monkeypatch, scope
):
    async def pin(url, **kwargs):
        return httpx.URL(url), {}, {}

    def handle(request):
        payload = json.loads(request.content)
        extraction = "从用户原话中整理" in payload["messages"][0]["content"]
        limited = extraction if scope == "memory_extraction" else not extraction
        text = "x" * (17000 if extraction else 25000) if limited else "answer"
        return httpx.Response(
            200,
            stream=BytesStream(
                [delta({"content": text}), event({"usage": USAGE}), event("[DONE]")]
            ),
        )

    monkeypatch.setattr(network, "pin_url", pin)
    monkeypatch.setattr(
        network, "client", lambda: httpx.AsyncClient(transport=httpx.MockTransport(handle))
    )

    async def run():
        runtime = SessionRuntime(initialize_data_root(tmp_path / scope), RealStore())
        runtime.memory_preferences.update({"auto_extract": scope == "memory_extraction"})
        sid = runtime.create_session()["id"]
        await run_turn(runtime, sid)
        if scope == "memory_extraction":
            await runtime._memory_task
        value = summary(runtime, sid)
        item = next(item for item in value["records"] if item["scope"] == scope)
        assert item["finished_at"] is not None
        assert item["outcome"] == "interrupted"
        assert item["error_code"] == "stream_closed"
        assert item["usage_status"] == "unknown"
        await runtime.close()

    asyncio.run(run())


@pytest.mark.parametrize("phase", ["started", "response"])
@pytest.mark.parametrize("scope", ["chat_generation", "memory_extraction"])
def test_process_crash_keeps_attempt_unknown_and_never_replays(
    tmp_path, phase, scope, provider_http
):
    root = tmp_path / "crash"
    program = r"""
import asyncio, json, os, sys
from pathlib import Path
import httpx
from ai_neko.config.paths import initialize_data_root
from ai_neko.providers import ModelAdapter
from ai_neko.runtime import SessionRuntime
from ai_neko.tools import network
async def pin(url, **kwargs): return httpx.URL(url), {}, {}
network.pin_url=pin
class SyntheticStream(httpx.AsyncByteStream):
    async def __aiter__(self):
        yield b'data: {"choices":[{"delta":{"content":"answer"}}]}\n\ndata: [DONE]\n\n'
network.client=lambda: httpx.AsyncClient(transport=httpx.MockTransport(lambda r: httpx.Response(200,stream=SyntheticStream())))
class CrashingAdapter(ModelAdapter):
    def set_request_observer(self, callback):
        def observe(event):
            callback(event)
            if event['phase']==sys.argv[2]: os._exit(86)
        super().set_request_observer(observe)
class Store:
    calls=0
    def model(self):
        self.calls+=1
        adapter=CrashingAdapter if sys.argv[3]=='chat_generation' or self.calls>1 else ModelAdapter
        return adapter({'model':'synthetic','model_base_url':'https://model.example/v1'}, 'synthetic')
async def run():
    runtime=SessionRuntime(initialize_data_root(Path(sys.argv[1])), Store())
    runtime.memory_preferences.update({'auto_extract':sys.argv[3]=='memory_extraction'})
    sid=runtime.create_session()['id']
    tid=(await runtime.start_turn(sid,'synthetic crash'))['id']
    print(json.dumps({'sid':sid,'tid':tid}),flush=True)
    await runtime._tasks[tid]
    if runtime._memory_task is not None: await runtime._memory_task
asyncio.run(run())
"""
    env = {
        key: value
        for key, value in os.environ.items()
        if key.upper()
        in {"PATH", "SYSTEMROOT", "WINDIR", "TEMP", "TMP", "HOME", "USERPROFILE", "LOCALAPPDATA"}
    }
    env["PYTHONPATH"] = str(Path(__file__).resolve().parents[1] / "src")
    result = subprocess.run(
        [sys.executable, "-c", program, str(root), phase, scope],
        capture_output=True,
        text=True,
        env=env,
        timeout=20,
    )
    assert result.returncode == 86, result.stderr
    ids = json.loads(result.stdout.strip().splitlines()[-1])

    class NeverReplay:
        def model(self):
            raise AssertionError("recovery must never call provider")

    async def run():
        runtime = SessionRuntime(initialize_data_root(root), NeverReplay())
        value = summary(runtime, ids["sid"])
        expected_count = 2 if scope == "memory_extraction" else 1
        assert value["attempt_count"] == value["unknown_usage_count"] == expected_count
        crashed = next(item for item in value["records"] if item["scope"] == scope)
        assert crashed["outcome"] == "interrupted"
        assert value["dispatch_uncertain_count"] == (1 if phase == "started" else 0)
        assert crashed["usage"] is None and value["reported_token_totals"] is None
        if scope == "memory_extraction":
            assert not value["scopes"][scope]["is_final"] and not value["is_final"]
        runtime.events(ids["sid"], ids["tid"])
        await runtime.close()
        reopened = SessionRuntime(initialize_data_root(root), NeverReplay())
        assert summary(reopened, ids["sid"]) == value
        await reopened.close()
        if scope == "memory_extraction":
            provider_http(
                [delta({"content": '{"facts":[]}'}), event({"usage": USAGE}), event("[DONE]")]
            )
            retrying = SessionRuntime(initialize_data_root(root), RealStore())
            with retrying.memory._db:
                retrying.memory._db.execute("UPDATE memory_jobs SET lease_until=0")
            await retrying._process_memories()
            after = summary(retrying, ids["sid"])
            assert after["attempt_count"] == expected_count + 1
            assert after["http_retry_count"] == 0
            assert after["records"][:expected_count] == value["records"]
            assert after["records"][-1]["job_attempt"] == 2
            assert after["scopes"][scope]["is_final"]
            await retrying.close()

    asyncio.run(run())


@pytest.mark.parametrize(
    "failure", [OSError("synthetic disk failure"), ValueError("synthetic rejection")]
)
def test_failed_extraction_enqueue_has_final_zero_usage(
    tmp_path, provider_http, monkeypatch, failure
):
    provider_http([delta({"content": "answer"}), event({"usage": USAGE}), event("[DONE]")])

    async def run():
        paths = initialize_data_root(tmp_path / "enqueue-failure")
        runtime = SessionRuntime(paths, RealStore())
        try:
            runtime.memory_preferences.update({"auto_extract": True})
            sid = runtime.create_session()["id"]

            def fail(*args, **kwargs):
                raise failure

            monkeypatch.setattr(runtime.memory, "enqueue_extraction", fail)
            await run_turn(runtime, sid)
            value = summary(runtime, sid)
            extraction = value["scopes"]["memory_extraction"]
            assert runtime.get_session(sid)["turns"][0]["status"] == "completed"
            assert runtime.memory.pending_jobs() == []
            assert extraction["state"] == "not_scheduled"
            assert extraction["attempt_count"] == 0 and extraction["is_final"]
            assert value["attempt_count"] == 1 and value["is_final"]
        finally:
            await runtime.close()
        reopened = SessionRuntime(paths, RealStore())
        try:
            assert summary(reopened, sid) == value
        finally:
            await reopened.close()

    asyncio.run(run())


def test_cancel_after_enqueue_commit_keeps_pending_extraction_nonfinal(
    tmp_path, provider_http, monkeypatch
):
    import threading

    provider_http([delta({"content": "answer"}), event({"usage": USAGE}), event("[DONE]")])

    async def run():
        runtime = SessionRuntime(initialize_data_root(tmp_path / "enqueue-cancel"), RealStore())
        runtime.memory_preferences.update({"auto_extract": True})
        entered, release = asyncio.Event(), threading.Event()
        loop = asyncio.get_running_loop()
        original = runtime.memory.enqueue_extraction

        def pause_after_commit(*args, **kwargs):
            result = original(*args, **kwargs)
            loop.call_soon_threadsafe(entered.set)
            if not release.wait(5):
                raise AssertionError("synthetic enqueue barrier timed out")
            return result

        monkeypatch.setattr(runtime.memory, "enqueue_extraction", pause_after_commit)
        try:
            sid = runtime.create_session()["id"]
            turn = await runtime.start_turn(sid, "synthetic preference")
            task = runtime._tasks[turn["id"]]
            await asyncio.wait_for(entered.wait(), 5)
            task.cancel()
            release.set()
            await asyncio.gather(task, return_exceptions=True)
            extraction = summary(runtime, sid)["scopes"]["memory_extraction"]
            assert len(runtime.memory.pending_jobs()) == 1
            assert extraction["state"] == "queued"
            assert extraction["attempt_count"] == 0 and not extraction["is_final"]
        finally:
            release.set()
            await runtime.close()

    asyncio.run(run())


@pytest.mark.parametrize("prior_failures", [0, 2])
def test_extraction_cancel_finality_matches_durable_attempt_budget(
    tmp_path, provider_http, monkeypatch, prior_failures
):
    import ai_neko.runtime.service as service_module

    provider_http([delta({"content": "answer"}), event({"usage": USAGE}), event("[DONE]")])

    async def run():
        runtime = SessionRuntime(initialize_data_root(tmp_path / "cancel-budget"), RealStore())
        runtime.memory_preferences.update({"auto_extract": True})
        entered = asyncio.Event()
        calls = 0

        async def extract(*args, **kwargs):
            nonlocal calls
            calls += 1
            if calls <= prior_failures:
                raise ValueError("synthetic extraction failure")
            entered.set()
            await asyncio.Event().wait()

        monkeypatch.setattr(service_module, "extract_facts", extract)
        try:
            sid = runtime.create_session()["id"]
            await run_turn(runtime, sid)
            task = runtime._memory_task
            if prior_failures:
                await task
                for _ in range(prior_failures - 1):
                    await runtime._process_memories()
                task = asyncio.create_task(runtime._process_memories())
            await asyncio.wait_for(entered.wait(), 5)
            task.cancel()
            await asyncio.gather(task, return_exceptions=True)
            extraction = summary(runtime, sid)["scopes"]["memory_extraction"]
            job = runtime.memory._db.execute("SELECT status,attempts FROM memory_jobs").fetchone()
            exhausted = prior_failures == 2
            assert job["attempts"] == prior_failures + 1
            assert job["status"] == ("failed" if exhausted else "pending")
            assert extraction["state"] == ("settled" if exhausted else "retry_pending")
            assert extraction["is_final"] is exhausted
            if exhausted:
                await runtime._process_memories()
                assert calls == 3
        finally:
            await runtime.close()

    asyncio.run(run())


def test_corrected_memory_invalidates_old_job_and_settles_its_usage(
    tmp_path, provider_http, monkeypatch
):
    provider_http([delta({"content": "answer"}), event({"usage": USAGE}), event("[DONE]")])

    async def run():
        runtime = SessionRuntime(initialize_data_root(tmp_path / "job-invalidated"), RealStore())
        try:
            runtime.memory_preferences.update({"auto_extract": True})
            monkeypatch.setattr(runtime, "start_memory_worker", lambda: None)
            fact = runtime.memory.remember(
                "synthetic old preference", source_id="manual:review", source_text="synthetic"
            )
            sid = runtime.create_session()["id"]
            await run_turn(runtime, sid)
            job = runtime.memory.pending_jobs()[0]
            await runtime.correct_memory(fact["id"], "synthetic corrected preference")
            await runtime._process_memories()
            metadata = runtime.memory.extraction_status(job["id"])
            assert set(metadata) == {"status", "attempts", "lease_until"}
            assert metadata["status"] == "cancelled" and metadata["attempts"] == 0
            extraction = summary(runtime, sid)["scopes"]["memory_extraction"]
            assert extraction["state"] == "settled" and extraction["is_final"]
            assert extraction["attempt_count"] == 0
        finally:
            await runtime.close()

    asyncio.run(run())


@pytest.mark.parametrize("attempt", [1, 3])
def test_competing_live_extraction_lease_is_never_final(
    tmp_path, provider_http, monkeypatch, attempt
):
    provider_http([delta({"content": "answer"}), event({"usage": USAGE}), event("[DONE]")])

    async def run():
        runtime = SessionRuntime(initialize_data_root(tmp_path / "job-live-lease"), RealStore())
        try:
            runtime.memory_preferences.update({"auto_extract": True})
            monkeypatch.setattr(runtime, "start_memory_worker", lambda: None)
            sid = runtime.create_session()["id"]
            await run_turn(runtime, sid)
            job = runtime.memory.pending_jobs()[0]
            with runtime.memory._db:
                runtime.memory._db.execute("UPDATE memory_jobs SET attempts=?", (attempt - 1,))
            original = runtime.memory.claim_extraction

            def claimed_elsewhere(*args, **kwargs):
                assert original(*args, **kwargs) is not None
                return None

            monkeypatch.setattr(runtime.memory, "claim_extraction", claimed_elsewhere)
            await runtime._process_memories()
            metadata = runtime.memory.extraction_status(job["id"])
            assert metadata["status"] == "running" and metadata["attempts"] == attempt
            extraction = summary(runtime, sid)["scopes"]["memory_extraction"]
            assert extraction["state"] == "queued" and not extraction["is_final"]
        finally:
            await runtime.close()

    asyncio.run(run())


@pytest.mark.parametrize("job_status", ["pending", "running"])
def test_exhausted_legacy_pending_or_expired_job_becomes_final(
    tmp_path, provider_http, monkeypatch, job_status
):
    provider_http([delta({"content": "answer"}), event({"usage": USAGE}), event("[DONE]")])

    async def run():
        runtime = SessionRuntime(initialize_data_root(tmp_path / "job-exhausted"), RealStore())
        try:
            runtime.memory_preferences.update({"auto_extract": True})
            monkeypatch.setattr(runtime, "start_memory_worker", lambda: None)
            sid = runtime.create_session()["id"]
            await run_turn(runtime, sid)
            with runtime.memory._db:
                runtime.memory._db.execute(
                    "UPDATE memory_jobs SET status=?,attempts=3,lease_until=0", (job_status,)
                )
            await runtime._process_memories()
            extraction = summary(runtime, sid)["scopes"]["memory_extraction"]
            assert extraction["state"] == "settled" and extraction["is_final"]
            assert extraction["attempt_count"] == 0
        finally:
            await runtime.close()

    asyncio.run(run())


def test_cancel_after_extraction_completion_commit_keeps_final_usage(
    tmp_path, provider_http, monkeypatch
):
    import threading

    import ai_neko.runtime.service as service_module

    provider_http([delta({"content": "answer"}), event({"usage": USAGE}), event("[DONE]")])

    async def run():
        runtime = SessionRuntime(initialize_data_root(tmp_path / "complete-cancel"), RealStore())
        runtime.memory_preferences.update({"auto_extract": True})
        entered, release = asyncio.Event(), threading.Event()
        loop = asyncio.get_running_loop()
        original = runtime.memory.complete_extraction

        async def extract(*args, **kwargs):
            return []

        def pause_after_commit(*args, **kwargs):
            result = original(*args, **kwargs)
            loop.call_soon_threadsafe(entered.set)
            if not release.wait(5):
                raise AssertionError("synthetic completion barrier timed out")
            return result

        monkeypatch.setattr(service_module, "extract_facts", extract)
        monkeypatch.setattr(runtime.memory, "complete_extraction", pause_after_commit)
        try:
            sid = runtime.create_session()["id"]
            await run_turn(runtime, sid)
            task = runtime._memory_task
            await asyncio.wait_for(entered.wait(), 5)
            task.cancel()
            release.set()
            result = (await asyncio.gather(task, return_exceptions=True))[0]
            assert isinstance(result, asyncio.CancelledError)
            job = runtime.memory._db.execute("SELECT id,status FROM memory_jobs").fetchone()
            assert job["status"] == "completed"
            extraction = summary(runtime, sid)["scopes"]["memory_extraction"]
            assert extraction["state"] == "settled" and extraction["is_final"]
            assert runtime.memory.pending_jobs() == []
        finally:
            release.set()
            await runtime.close()

    asyncio.run(run())


@pytest.mark.parametrize("attempt", [1, 3])
def test_cancel_during_failure_settlement_uses_its_committed_outcome(
    tmp_path, provider_http, monkeypatch, attempt
):
    import threading

    import ai_neko.runtime.service as service_module

    provider_http([delta({"content": "answer"}), event({"usage": USAGE}), event("[DONE]")])

    async def run():
        runtime = SessionRuntime(initialize_data_root(tmp_path / "fail-commit-cancel"), RealStore())
        runtime.memory_preferences.update({"auto_extract": True})
        monkeypatch.setattr(runtime, "start_memory_worker", lambda: None)
        entered, release = asyncio.Event(), threading.Event()
        loop = asyncio.get_running_loop()
        original = runtime.memory.fail_extraction

        async def extract(*args, **kwargs):
            raise ValueError("synthetic failure")

        def pause_after_commit(*args, **kwargs):
            result = original(*args, **kwargs)
            loop.call_soon_threadsafe(entered.set)
            if not release.wait(5):
                raise AssertionError("synthetic failure barrier timed out")
            return result

        monkeypatch.setattr(service_module, "extract_facts", extract)
        monkeypatch.setattr(runtime.memory, "fail_extraction", pause_after_commit)
        try:
            sid = runtime.create_session()["id"]
            await run_turn(runtime, sid)
            with runtime.memory._db:
                runtime.memory._db.execute("UPDATE memory_jobs SET attempts=?", (attempt - 1,))
            task = asyncio.create_task(runtime._process_memories())
            await asyncio.wait_for(entered.wait(), 5)
            task.cancel()
            release.set()
            result = (await asyncio.gather(task, return_exceptions=True))[0]
            assert isinstance(result, asyncio.CancelledError)
            job = runtime.memory._db.execute("SELECT id,status FROM memory_jobs").fetchone()
            final = attempt == 3
            assert job["status"] == ("failed" if final else "pending")
            extraction = summary(runtime, sid)["scopes"]["memory_extraction"]
            assert extraction["state"] == ("settled" if final else "retry_pending")
            assert extraction["is_final"] is final
        finally:
            release.set()
            await runtime.close()

    asyncio.run(run())


@pytest.mark.parametrize("boundary", ["claim_extraction", "extraction_status"])
def test_cancel_during_invalidated_claim_or_status_read_preserves_finality(
    tmp_path, provider_http, monkeypatch, boundary
):
    import threading

    provider_http([delta({"content": "answer"}), event({"usage": USAGE}), event("[DONE]")])

    async def run():
        runtime = SessionRuntime(initialize_data_root(tmp_path / "claim-cancel"), RealStore())
        runtime.memory_preferences.update({"auto_extract": True})
        monkeypatch.setattr(runtime, "start_memory_worker", lambda: None)
        entered, release = asyncio.Event(), threading.Event()
        loop = asyncio.get_running_loop()
        original = getattr(runtime.memory, boundary)

        def pause_after_commit(*args, **kwargs):
            result = original(*args, **kwargs)
            loop.call_soon_threadsafe(entered.set)
            if not release.wait(5):
                raise AssertionError("synthetic claim/status barrier timed out")
            return result

        monkeypatch.setattr(runtime.memory, boundary, pause_after_commit)
        try:
            fact = runtime.memory.remember(
                "synthetic old preference", source_id="manual:review", source_text="synthetic"
            )
            sid = runtime.create_session()["id"]
            await run_turn(runtime, sid)
            await runtime.correct_memory(fact["id"], "synthetic corrected preference")
            task = asyncio.create_task(runtime._process_memories())
            await asyncio.wait_for(entered.wait(), 5)
            task.cancel()
            await asyncio.sleep(0)
            task.cancel()
            release.set()
            result = (await asyncio.gather(task, return_exceptions=True))[0]
            assert isinstance(result, asyncio.CancelledError)
            job = runtime.memory._db.execute("SELECT status FROM memory_jobs").fetchone()
            assert job["status"] == "cancelled"
            extraction = summary(runtime, sid)["scopes"]["memory_extraction"]
            assert extraction["state"] == "settled" and extraction["is_final"]
            assert runtime.memory.pending_jobs() == []
        finally:
            release.set()
            await runtime.close()

    asyncio.run(run())
