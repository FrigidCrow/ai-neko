"""Explicit graph controls use durable jobs and existing mutation authorities."""

import asyncio
import json
import sqlite3
from uuid import uuid4

import pytest
from test_match_retrieval_runtime import GAME, CaptureProviders, begin, load_g01
from test_match_runtime import binding

from ai_neko.chat.control_intent import explicit_control
from ai_neko.config.paths import initialize_data_root
from ai_neko.runtime import RuntimeConflictError, RuntimeInputError, SessionRuntime
from ai_neko.tools.web import source


async def target(runtime):
    saved, _ = await load_g01(runtime)
    return {
        **{key: GAME[key] for key in ("game", "platform", "mode")},
        **{key: saved[key] for key in ("guide_id", "revision_id")},
        "expected_revision": runtime.memory.guides.revision(),
    }


async def finish(runtime, sid, tid, *, job=True):
    for _ in range(400):
        try:
            batch = runtime.events(sid, tid)
        except RuntimeConflictError:
            await asyncio.sleep(0.005)
            continue
        identifier = batch["control_job_id"]
        if job and identifier:
            result = runtime.control_job(sid, identifier)
            if result["status"] not in {"pending", "running"}:
                return batch, result
        elif not job and batch["status"] not in {"accepted", "running"}:
            return batch, None
        await asyncio.sleep(0.005)
    raise AssertionError("control did not settle")


async def send(runtime, sid, text, *, selected=None, request_id=None):
    return await runtime.start_turn(
        sid,
        text,
        match=await binding(runtime, sid),
        input_origin="voice",
        guide_target=selected,
        request_id=request_id,
    )


@pytest.mark.parametrize(
    "text",
    [
        "按这份攻略",
        "请采用这份攻略吧。",
        "换成这份攻略！",
        "切换到这份",
        "新一局",
        "请开始新一局吧",
    ],
)
def test_whole_explicit_user_commands(text):
    assert explicit_control(text) in {"select_guide", "new_match"}


@pytest.mark.parametrize(
    "text",
    [
        "不要按这份攻略",
        "如果按这份攻略",
        "网页说按这份攻略",
        "“新一局”",
        "新一局是什么？",
        "如何换成这份攻略",
        "先查攻略，再新一局",
        {"text": "新一局"},
    ],
)
def test_embedded_quoted_conditional_or_negative_speech_never_becomes_control(text):
    assert explicit_control(text) is None


def test_adopt_then_switch_and_new_match_confirm_with_new_binding_zero_model_calls(tmp_path):
    async def run():
        providers = CaptureProviders()
        runtime = SessionRuntime(initialize_data_root(tmp_path / "controls"), providers)
        try:
            sid = runtime.create_session()["id"]
            selected = await target(runtime)
            old = (await begin(runtime, sid))["match_id"]
            original = await binding(runtime, sid)
            request_id = uuid4().hex
            turn = await send(runtime, sid, "按这份攻略", selected=selected, request_id=request_id)
            batch, job = await finish(runtime, sid, turn["id"])
            assert batch["status"] == "completed" and job["status"] == "completed"
            assert job["committed"] and not job["replayed"]
            assert not any(event["type"] == "text" for event in batch["events"])
            response = job["response_turn"]
            assert response["id"] != turn["id"] and response["kind"] == "control_response"
            assert response["context"]["match"]["expected_revision"] > original["expected_revision"]
            delivered = runtime.events(sid, response["id"])
            assert "已经采用" in "".join(event.get("text", "") for event in delivered["events"])
            assert delivered["match_binding"] == await binding(runtime, sid)
            runtime.ack(sid, response["id"], delivered["sent_seq"])
            revision = runtime.memory.guides.revision()
            replay = await runtime.start_turn(
                sid,
                "按这份攻略",
                match=original,
                guide_target=selected,
                input_origin="voice",
                request_id=request_id,
            )
            assert (
                replay["control_job_id"] == job["id"]
                and runtime.memory.guides.revision() == revision
            )
            with pytest.raises(RuntimeConflictError):
                await runtime.start_turn(
                    sid,
                    "按这份攻略",
                    match=original,
                    guide_target={**selected, "mode": "other"},
                    input_origin="voice",
                    request_id=request_id,
                )
            other = runtime.memory.guides.ingest(
                source(
                    "https://example.com/b",
                    status="read",
                    title="B",
                    text="另一份合成公开攻略正文。" * 10,
                ),
                **GAME,
            )
            changed = {
                **selected,
                "guide_id": other["guide_id"],
                "revision_id": other["revision_id"],
                "expected_revision": revision,
            }
            switch = await send(runtime, sid, "换成这份攻略", selected=changed)
            _, switched = await finish(runtime, sid, switch["id"])
            assert switched["result"]["selection"]["guide_id"] == other["guide_id"]
            new = await send(runtime, sid, "新一局")
            _, new_job = await finish(runtime, sid, new["id"])
            current = (await runtime.match_catalog(sid))["current"]
            assert new_job["status"] == "completed" and current["match_id"] != old
            assert current["goal"] == "" and current["game_version"] == GAME["game_version"]
            assert current["selection"]["guide_id"] == other["guide_id"]
            assert runtime.matches.read_context(sid, current["match_id"])["observations"] == []
            assert providers.requests == [] and providers.web_calls == []
            assert runtime._last_match_advice(sid, new["id"], current) is None
        finally:
            await runtime.close()

    asyncio.run(run())


@pytest.mark.parametrize(
    "text,start", [("按这份攻略", True), ("换成这份攻略", True), ("新一局", False)]
)
def test_missing_target_or_current_match_clarifies_without_model_or_control(tmp_path, text, start):
    async def run():
        providers = CaptureProviders()
        providers.model = lambda: (_ for _ in ()).throw(AssertionError("no model needed"))
        runtime = SessionRuntime(initialize_data_root(tmp_path / "clarify"), providers)
        try:
            sid = runtime.create_session()["id"]
            if start:
                await begin(runtime, sid)
            before = await binding(runtime, sid)
            turn = await send(runtime, sid, text)
            batch, _ = await finish(runtime, sid, turn["id"], job=False)
            assert batch["control_job_id"] is None
            assert "请先" in "".join(event.get("text", "") for event in batch["events"])
            assert await binding(runtime, sid) == before
            assert runtime.memory.guides.revision() == 0
        finally:
            await runtime.close()

    asyncio.run(run())


@pytest.mark.parametrize("cancel_request", [False, True])
def test_cancel_before_dispatch_keeps_state_and_never_creates_confirmation(
    tmp_path, monkeypatch, cancel_request
):
    async def run():
        runtime = SessionRuntime(initialize_data_root(tmp_path / "cancel"), CaptureProviders())
        try:
            sid = runtime.create_session()["id"]
            selected = await target(runtime)
            await begin(runtime, sid)
            launch = runtime._launch_control
            monkeypatch.setattr(runtime, "_launch_control", lambda _: None)
            request_id = uuid4().hex
            turn = await send(runtime, sid, "按这份攻略", selected=selected, request_id=request_id)
            batch, _ = await finish(runtime, sid, turn["id"], job=False)
            if cancel_request:
                await runtime.cancel_request(sid, request_id)
            else:
                await runtime.cancel_turn(sid, turn["id"])
            launch(batch["control_job_id"])
            job = runtime.control_job(sid, batch["control_job_id"])
            assert job["status"] == "cancelled" and job["response_turn"] is None
            assert runtime.memory.guides.revision() == 0
        finally:
            await runtime.close()

    asyncio.run(run())


@pytest.mark.parametrize("changed", ["match", "guide"])
def test_stale_match_or_guide_revision_between_graph_and_commit_has_no_effect(
    tmp_path, monkeypatch, changed
):
    async def run():
        runtime = SessionRuntime(initialize_data_root(tmp_path / "stale"), CaptureProviders())
        try:
            sid = runtime.create_session()["id"]
            selected = await target(runtime)
            old = (await begin(runtime, sid))["match_id"]
            launch = runtime._launch_control
            monkeypatch.setattr(runtime, "_launch_control", lambda _: None)
            turn = await send(runtime, sid, "按这份攻略", selected=selected)
            batch, _ = await finish(runtime, sid, turn["id"], job=False)
            if changed == "match":
                await begin(runtime, sid, old=old)
            else:
                await runtime.guide_selection({**selected, "request_id": uuid4().hex})
            revision = runtime.memory.guides.revision()
            launch(batch["control_job_id"])
            _, job = await finish(runtime, sid, turn["id"])
            assert job["status"] == "error" and not job["committed"]
            assert runtime.memory.guides.revision() == revision
            text = runtime.events(sid, job["response_turn_id"])
            assert "已经采用" not in json.dumps(text, ensure_ascii=False)
        finally:
            await runtime.close()

    asyncio.run(run())


def test_committed_return_lost_uses_receipt_without_duplicate_mutation(tmp_path, monkeypatch):
    async def run():
        runtime = SessionRuntime(initialize_data_root(tmp_path / "return-lost"), CaptureProviders())
        try:
            sid = runtime.create_session()["id"]
            selected = await target(runtime)
            original = runtime.guide_selection

            async def lost(*args, **kwargs):
                await original(*args, **kwargs)
                raise OSError("return lost after commit")

            monkeypatch.setattr(runtime, "guide_selection", lost)
            turn = await send(runtime, sid, "按这份攻略", selected=selected)
            _, job = await finish(runtime, sid, turn["id"])
            assert job["status"] == "completed" and job["committed"]
            assert runtime.memory.guides.revision() == 1
            assert (
                runtime.memory.guides._db.execute("SELECT COUNT(*) FROM guide_requests").fetchone()[
                    0
                ]
                == 1
            )
        finally:
            await runtime.close()

    asyncio.run(run())


def test_cancel_after_commit_waits_settlement_and_suppresses_confirmation(tmp_path, monkeypatch):
    async def run():
        runtime = SessionRuntime(initialize_data_root(tmp_path / "late-cancel"), CaptureProviders())
        entered, release = asyncio.Event(), asyncio.Event()
        try:
            sid = runtime.create_session()["id"]
            selected = await target(runtime)
            original = runtime.guide_selection

            async def held(*args, **kwargs):
                result = await original(*args, **kwargs)
                entered.set()
                try:
                    await release.wait()
                except asyncio.CancelledError:
                    await release.wait()
                return result

            monkeypatch.setattr(runtime, "guide_selection", held)
            turn = await send(runtime, sid, "按这份攻略", selected=selected)
            await asyncio.wait_for(entered.wait(), 2)
            job_id = runtime.events(sid, turn["id"])["control_job_id"]
            stopping = asyncio.create_task(runtime.cancel_control_job(sid, job_id))
            await asyncio.sleep(0)
            assert not stopping.done()
            release.set()
            result = await asyncio.wait_for(stopping, 2)
            assert result["status"] == "cancelled" and result["committed"]
            assert result["response_turn_id"] is None
            assert runtime.memory.guides.revision() == 1
        finally:
            release.set()
            await runtime.close()

    asyncio.run(run())


def test_close_drains_control_worker_before_acquiring_its_mutation_lock(tmp_path, monkeypatch):
    async def run():
        runtime = SessionRuntime(
            initialize_data_root(tmp_path / "closing-control"), CaptureProviders()
        )
        entered, release = asyncio.Event(), asyncio.Event()
        original = runtime._stop_memory_work

        async def blocked_cleanup():
            await original()
            entered.set()
            await release.wait()

        monkeypatch.setattr(runtime, "_stop_memory_work", blocked_cleanup)
        try:
            sid = runtime.create_session()["id"]
            await send(runtime, sid, "按这份攻略", selected=await target(runtime))
            await asyncio.wait_for(entered.wait(), 2)
            closing = asyncio.create_task(runtime.close())
            await asyncio.sleep(0)
            assert not closing.done()
            release.set()
            await asyncio.wait_for(closing, 2)
            assert runtime._closed and not runtime._control_tasks
        finally:
            release.set()
            await runtime.close()

    asyncio.run(run())


def test_completed_control_restarts_read_only_and_cancel_blocks_audio(tmp_path):
    async def run():
        paths = initialize_data_root(tmp_path / "restart")
        runtime = SessionRuntime(paths, CaptureProviders())
        sid = runtime.create_session()["id"]
        turn = await send(runtime, sid, "按这份攻略", selected=await target(runtime))
        _, job = await finish(runtime, sid, turn["id"])
        revision = runtime.memory.guides.revision()
        await runtime.close()
        providers = CaptureProviders()
        runtime = SessionRuntime(paths, providers)
        try:
            replay = runtime.control_job(sid, job["id"])
            assert replay["replayed"] and replay["response_turn_id"] == job["response_turn_id"]
            assert runtime.memory.guides.revision() == revision and providers.requests == []
            await runtime.cancel_control_job(sid, job["id"])
            with pytest.raises(RuntimeConflictError):
                runtime.validate_voice_turn(sid, job["response_turn_id"])
        finally:
            await runtime.close()

    asyncio.run(run())


def test_v5_migration_and_uncommitted_crash_job_are_read_only(tmp_path):
    async def run():
        paths = initialize_data_root(tmp_path / "migration")
        runtime = SessionRuntime(paths, CaptureProviders())
        sid = runtime.create_session()["id"]
        selected = await target(runtime)
        runtime._launch_control = lambda _: None
        turn = await send(runtime, sid, "按这份攻略", selected=selected)
        batch, _ = await finish(runtime, sid, turn["id"], job=False)
        await runtime.close()
        with sqlite3.connect(paths.memory / "conversation.sqlite") as db:
            db.execute("UPDATE control_jobs SET status='pending',cancel_requested=0")
        runtime = SessionRuntime(paths, CaptureProviders())
        assert runtime.control_job(sid, batch["control_job_id"])["status"] == "interrupted"
        assert runtime.memory.guides.revision() == 0
        await runtime.close()
        with sqlite3.connect(paths.memory / "conversation.sqlite") as db:
            db.execute("DROP TABLE control_jobs")
            db.execute("PRAGMA user_version=5")
        runtime = SessionRuntime(paths, CaptureProviders())
        assert runtime._db.execute("PRAGMA user_version").fetchone()[0] == 6
        assert runtime.get_session(sid)["turns"]
        assert not (paths.backups / "conversation-pre-migration-v6.sqlite").exists()
        await runtime.close()

    asyncio.run(run())


@pytest.mark.parametrize("cancelled", [False, True])
def test_restart_finds_committed_receipt_after_missing_confirmation_without_reexecuting(
    tmp_path, cancelled
):
    async def run():
        paths = initialize_data_root(tmp_path / "receipt-recovery")
        runtime = SessionRuntime(paths, CaptureProviders())
        sid = runtime.create_session()["id"]
        turn = await send(runtime, sid, "按这份攻略", selected=await target(runtime))
        _, job = await finish(runtime, sid, turn["id"])
        await runtime.close()
        with sqlite3.connect(paths.memory / "conversation.sqlite") as db:
            for table in (
                "events",
                "turn_matches",
                "turn_metadata",
                "turn_user_history",
                "turn_guides",
            ):
                db.execute(f"DELETE FROM {table} WHERE turn_id=?", (job["response_turn_id"],))
            db.execute("DELETE FROM turns WHERE id=?", (job["response_turn_id"],))
            db.execute(
                "UPDATE control_jobs SET status=?,cancel_requested=?,result=NULL",
                ("cancelled" if cancelled else "running", int(cancelled)),
            )
        providers = CaptureProviders()
        runtime = SessionRuntime(paths, providers)
        try:
            recovered = runtime.control_job(sid, job["id"])
            assert recovered["committed"] and recovered["replayed"]
            assert recovered["status"] == ("cancelled" if cancelled else "completed")
            assert (recovered["response_turn"] is None) == cancelled
            assert runtime.memory.guides.revision() == 1 and providers.requests == []
            assert (
                runtime.memory.guides._db.execute("SELECT COUNT(*) FROM guide_requests").fetchone()[
                    0
                ]
                == 1
            )
        finally:
            await runtime.close()

    asyncio.run(run())


def test_forgetting_pending_control_origin_prevents_commit_and_response_resurrection(
    tmp_path, monkeypatch
):
    async def run():
        runtime = SessionRuntime(
            initialize_data_root(tmp_path / "erased-control"), CaptureProviders()
        )
        try:
            sid = runtime.create_session()["id"]
            selected = await target(runtime)
            launch = runtime._launch_control
            monkeypatch.setattr(runtime, "_launch_control", lambda _: None)
            turn = await send(runtime, sid, "按这份攻略", selected=selected)
            batch, _ = await finish(runtime, sid, turn["id"], job=False)
            fact = runtime.memory.remember(
                "按这份攻略", source_id="turn:" + turn["id"], source_text="按这份攻略"
            )
            await runtime.forget_memory(fact["id"])
            launch(batch["control_job_id"])
            for _ in range(100):
                job = runtime.control_job(sid, batch["control_job_id"])
                if job["status"] not in {"pending", "running"}:
                    break
                await asyncio.sleep(0.005)
            assert not job["committed"] and runtime.memory.guides.revision() == 0
            assert runtime._turn(sid, turn["id"])["input"] == "[已遗忘的对话]"
            assert job["status"] == "cancelled" and job["response_turn"] is None
        finally:
            await runtime.close()

    asyncio.run(run())


@pytest.mark.parametrize("value", [[], {}, {"expected_revision": True}])
def test_invalid_guide_target_rejected_before_acceptance(tmp_path, value):
    async def run():
        runtime = SessionRuntime(initialize_data_root(tmp_path / "shape"), CaptureProviders())
        try:
            sid = runtime.create_session()["id"]
            with pytest.raises(RuntimeInputError):
                await runtime.start_turn(sid, "按这份攻略", guide_target=value)
            assert runtime.get_session(sid)["turns"] == []
        finally:
            await runtime.close()

    asyncio.run(run())
