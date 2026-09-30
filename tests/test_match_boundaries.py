"""G4 cross-store recovery and replay boundaries against the actual Runtime."""

import asyncio
import json
import os
import sqlite3
import subprocess
import sys
from pathlib import Path
from uuid import uuid4

import pytest
from test_runtime import Model, Store, settled, text_ready

from ai_neko.config.paths import initialize_data_root
from ai_neko.runtime import RuntimeConflictError, RuntimeInputError, SessionRuntime
from ai_neko.tools.web import source

GAME = {"game": "fog", "platform": "pc", "mode": "ranked", "game_version": "2.4"}
OLD = "OLD_GUIDE_ADVICE_765"
NEW = "NEW_GUIDE_ADVICE_871"


async def binding(runtime, sid):
    catalog = await runtime.match_catalog(sid)
    return {
        "match_id": catalog["current"]["match_id"] if catalog["current"] else None,
        "expected_revision": catalog["revision"],
    }


async def begin(runtime, sid):
    return await runtime.match_control(
        sid, "start", {"request_id": uuid4().hex, "expected_revision": 0, **GAME}
    )


async def ask(runtime, sid, question, *, request_id=None, supplied=None, ack=True):
    turn = await runtime.start_turn(
        sid, question, request_id=request_id, match=supplied or await binding(runtime, sid)
    )
    await settled(runtime, sid, turn["id"])
    events = runtime.events(sid, turn["id"])
    assert events["status"] == "completed", events
    if ack:
        runtime.ack(sid, turn["id"], events["sent_seq"])
    return turn["id"]


def save(runtime, suffix, text):
    return runtime.memory.guides.ingest(
        source("https://example.com/" + suffix, status="read", title=suffix, text=text), **GAME
    )


async def adopt(runtime, document):
    return await runtime.guide_selection(
        {
            "request_id": uuid4().hex,
            "expected_revision": runtime.memory.guides.revision(),
            **{key: GAME[key] for key in ("game", "platform", "mode")},
            "guide_id": document["guide_id"],
            "revision_id": document["revision_id"],
        }
    )


@pytest.mark.parametrize("failure_after_cancel", [False, True])
def test_committed_match_control_retries_failed_settlement_before_accepting_new_turn(
    tmp_path, monkeypatch, failure_after_cancel
):
    async def run():
        gate = asyncio.Event()
        providers = Store(Model(["旧局已显示", "不能迟到"], gate))
        runtime = SessionRuntime(initialize_data_root(tmp_path / "settlement-failure"), providers)
        sid = runtime.create_session()["id"]
        cancel = runtime.cancel_turn
        try:
            first = await begin(runtime, sid)
            old_turn = await runtime.start_turn(
                sid, "分析这个局面", match=await binding(runtime, sid)
            )
            await text_ready(runtime, sid, old_turn["id"])
            control = {
                "request_id": uuid4().hex,
                "expected_revision": first["revision"],
                "match_id": first["match_id"],
                **GAME,
            }

            async def fail(*args, **kwargs):
                if failure_after_cancel:
                    await cancel(*args, **kwargs)
                raise sqlite3.OperationalError("synthetic settlement disk failure")

            monkeypatch.setattr(runtime, "cancel_turn", fail)
            with pytest.raises(sqlite3.OperationalError):
                await runtime.match_control(sid, "new", control)
            monkeypatch.setattr(runtime, "cancel_turn", cancel)
            # Even when the old row was already settled, the pending durable
            # control must be completed before accepting fresh generation.
            current = await runtime.match_catalog(sid)
            assert current["current"]["match_id"] != first["match_id"]
            current_binding = {
                "match_id": current["current"]["match_id"],
                "expected_revision": current["revision"],
            }
            with pytest.raises(RuntimeConflictError):
                await runtime.start_turn(sid, "暂停恢复前的新输入", match=current_binding)
            replay = await runtime.match_control(sid, "new", control)
            assert replay["replayed"] and replay["revision"] == current["revision"]
            assert runtime.get_session(sid)["turns"][0]["status"] == "cancelled"
            assert not runtime._tasks
            providers.adapter = Model(["新局可继续"])
            await ask(runtime, sid, "恢复后继续")
        finally:
            monkeypatch.setattr(runtime, "cancel_turn", cancel)
            gate.set()
            await runtime.close()

    asyncio.run(run())


def test_successful_control_replay_never_cancels_new_revision_stream(tmp_path):
    async def run():
        providers = Store()
        runtime = SessionRuntime(initialize_data_root(tmp_path / "control-replay"), providers)
        sid = runtime.create_session()["id"]
        gate = asyncio.Event()
        try:
            first = await begin(runtime, sid)
            value = {
                "request_id": uuid4().hex,
                "expected_revision": first["revision"],
                "match_id": first["match_id"],
                **GAME,
            }
            current = await runtime.match_control(sid, "new", value)
            providers.adapter = Model(["新局正在显示", "新局完成"], gate)
            turn = await runtime.start_turn(sid, "新局分析", match=await binding(runtime, sid))
            await text_ready(runtime, sid, turn["id"])
            replay = await runtime.match_control(sid, "new", value)
            assert replay["replayed"] and replay["revision"] == current["revision"]
            assert runtime.events(sid, turn["id"])["status"] == "running"
            assert not runtime._tasks[turn["id"]].done()
            gate.set()
            await settled(runtime, sid, turn["id"])
            assert runtime.events(sid, turn["id"])["status"] == "completed"
        finally:
            gate.set()
            await runtime.close()

    asyncio.run(run())


def test_failed_match_control_transaction_preserves_current_turn_and_revision(
    tmp_path, monkeypatch
):
    async def run():
        gate = asyncio.Event()
        runtime = SessionRuntime(
            initialize_data_root(tmp_path / "control-rollback"),
            Store(Model(["原局仍在生成", "原局完成"], gate)),
        )
        sid = runtime.create_session()["id"]
        control = runtime.matches.control
        try:
            first = await begin(runtime, sid)
            original = await binding(runtime, sid)
            turn = await runtime.start_turn(sid, "分析当前局", match=original)
            await text_ready(runtime, sid, turn["id"])

            def fail(*args, **kwargs):
                control(*args, **kwargs)
                raise sqlite3.OperationalError("synthetic control transaction failure")

            monkeypatch.setattr(runtime.matches, "control", fail)
            value = {"request_id": uuid4().hex, **original, **GAME}
            with pytest.raises(sqlite3.OperationalError):
                await runtime.match_control(sid, "new", value)
            monkeypatch.setattr(runtime.matches, "control", control)
            assert await binding(runtime, sid) == original
            assert not runtime._db.execute("SELECT 1 FROM match_settlements").fetchone()
            assert runtime.events(sid, turn["id"])["status"] == "running"
            assert not runtime._tasks[turn["id"]].cancelling()
            current = await runtime.match_control(sid, "new", value)
            assert current["match_id"] != first["match_id"]
            assert runtime.events(sid, turn["id"])["status"] == "cancelled"
        finally:
            monkeypatch.setattr(runtime.matches, "control", control)
            gate.set()
            await runtime.close()

    asyncio.run(run())


def test_restart_clears_pending_match_settlement_without_replaying_old_generation(
    tmp_path, monkeypatch
):
    async def run():
        paths = initialize_data_root(tmp_path / "settlement-restart")
        gate = asyncio.Event()
        runtime = SessionRuntime(paths, Store(Model([OLD, "不能重启续写"], gate)))
        sid = runtime.create_session()["id"]
        cancel = runtime.cancel_turn
        reopened = None
        try:
            await begin(runtime, sid)
            turn = await runtime.start_turn(sid, "原局分析", match=await binding(runtime, sid))
            await text_ready(runtime, sid, turn["id"])

            async def fail(*args, **kwargs):
                raise sqlite3.OperationalError("synthetic pending settlement before restart")

            value = {"request_id": uuid4().hex, **(await binding(runtime, sid)), **GAME}
            monkeypatch.setattr(runtime, "cancel_turn", fail)
            with pytest.raises(sqlite3.OperationalError):
                await runtime.match_control(sid, "new", value)
            monkeypatch.setattr(runtime, "cancel_turn", cancel)
            assert runtime._db.execute("SELECT 1 FROM match_settlements").fetchone()
            current_id = (await binding(runtime, sid))["match_id"]
            await runtime.close()
            providers = Store(Model([NEW]))
            reopened = SessionRuntime(paths, providers)
            assert not reopened._db.execute("SELECT 1 FROM match_settlements").fetchone()
            assert not reopened._tasks and not providers.adapter.messages
            assert (await binding(reopened, sid))["match_id"] == current_id
            receipt = await reopened.match_control(sid, "new", value)
            assert receipt["replayed"] and receipt["match_id"] == current_id
            await ask(reopened, sid, "新局当前如何？")
            assert OLD not in json.dumps(providers.adapter.messages, ensure_ascii=False)
        finally:
            monkeypatch.setattr(runtime, "cancel_turn", cancel)
            gate.set()
            await runtime.close()
            if reopened:
                await reopened.close()

    asyncio.run(run())


def test_match_restart_across_two_python_processes_invalidates_dynamic_evidence(tmp_path):
    repo = Path(__file__).resolve().parents[1]
    env = {**os.environ, "PYTHONPATH": os.pathsep.join((str(repo / "tests"), str(repo / "src")))}
    data_root = tmp_path / "two-process-match"
    common = """
import asyncio
import json
import os
import sys
from pathlib import Path

from test_runtime import Model, Store
from test_match_boundaries import begin, ask, binding, OLD, NEW
from test_match_runtime import match_context
from ai_neko.config.paths import initialize_data_root
from ai_neko.runtime import SessionRuntime

paths = initialize_data_root(Path(sys.argv[1]))
"""
    writer = (
        common
        + """
async def run():
    runtime = SessionRuntime(paths, Store(Model([OLD])))
    try:
        sid = runtime.create_session()["id"]
        started = await begin(runtime, sid)
        tid = await ask(runtime, sid, "我现在有937261金币。")
        current = await runtime.match_catalog(sid)
        assert current["current"]["status"] == "active"
        assert runtime.get_session(sid)["turns"][0]["confirmed_text"] == OLD
        assert runtime._binding(tid)["match_id"] == started["match_id"]
        receipt = {
            "pid": os.getpid(), "session_id": sid, "turn_id": tid,
            "match_id": started["match_id"], "revision": current["revision"],
        }
    finally:
        await runtime.close()
    print(json.dumps(receipt))

asyncio.run(run())
"""
    )
    first = subprocess.run(
        [sys.executable, "-c", writer, str(data_root)],
        cwd=repo,
        env=env,
        text=True,
        capture_output=True,
        timeout=30,
    )
    assert first.returncode == 0, first.stdout + first.stderr
    receipt = json.loads(first.stdout)
    reader = (
        common
        + """
receipt = json.loads(sys.argv[2])

async def run():
    model = Model([NEW])
    runtime = SessionRuntime(paths, Store(model))
    try:
        sid = receipt["session_id"]
        current = await runtime.match_catalog(sid)
        assert current["current"]["status"] == "needs_update"
        assert current["current"]["match_id"] == receipt["match_id"]
        assert current["revision"] > receipt["revision"]
        assert runtime._binding(receipt["turn_id"])["match_id"] == receipt["match_id"]
        history = runtime.get_session(sid)["turns"][0]
        assert "937261" in history["input"] and history["confirmed_text"] == OLD
        assert not runtime._tasks and not model.messages

        await ask(runtime, sid, "下一步呢？")
        context = match_context(model.messages[-1])
        assert context["match_id"] == receipt["match_id"]
        assert context["status"] == "needs_update"
        assert context["observations"] == [] and context["last_delivered_advice"] is None
        request = json.dumps(model.messages[-1], ensure_ascii=False)
        assert "937261" not in request and OLD not in request

        await ask(runtime, sid, "我现在有718193金币。")
        context = match_context(model.messages[-1])
        assert context["status"] == "active"
        assert context["match_id"] == receipt["match_id"]
        assert context["observations"][0]["text"] == "我现在有718193金币。"
        request = json.dumps(model.messages[-1], ensure_ascii=False)
        assert "718193" in request and "937261" not in request and OLD not in request
        assert (await runtime.match_catalog(sid))["current"]["status"] == "active"
    finally:
        await runtime.close()
    print(json.dumps({"pid": os.getpid(), "match_id": receipt["match_id"]}))

asyncio.run(run())
"""
    )
    second = subprocess.run(
        [sys.executable, "-c", reader, str(data_root), json.dumps(receipt)],
        cwd=repo,
        env=env,
        text=True,
        capture_output=True,
        timeout=30,
    )
    assert second.returncode == 0, second.stdout + second.stderr
    recovered = json.loads(second.stdout)
    assert len({receipt["pid"], recovered["pid"], os.getpid()}) == 3
    assert recovered["match_id"] == receipt["match_id"]


def test_old_match_late_ack_remains_history_and_cannot_become_current_advice(tmp_path):
    async def run():
        providers = Store(Model([OLD]))
        runtime = SessionRuntime(initialize_data_root(tmp_path / "late-ack"), providers)
        sid = runtime.create_session()["id"]
        try:
            await begin(runtime, sid)
            old = await ask(runtime, sid, "分析旧局", ack=False)
            sent = runtime.events(sid, old)["sent_seq"]
            await runtime.match_control(
                sid, "new", {"request_id": uuid4().hex, **(await binding(runtime, sid)), **GAME}
            )
            current = await binding(runtime, sid)
            runtime.ack(sid, old, sent)
            assert OLD in runtime.get_session(sid)["turns"][0]["confirmed_text"]
            assert await binding(runtime, sid) == current
            providers.adapter = Model([NEW])
            new = await ask(runtime, sid, "下一步呢？")
            assert OLD not in json.dumps(providers.adapter.messages, ensure_ascii=False)
            assert not runtime._db.execute(
                "SELECT 1 FROM turn_history WHERE turn_id=? AND source_turn_id=?", (new, old)
            ).fetchone()
        finally:
            await runtime.close()

    asyncio.run(run())


@pytest.mark.parametrize(
    ("active", "invalid_revision"),
    [
        pytest.param(True, True, id="active-bool"),
        pytest.param(True, 1.0, id="active-float"),
        pytest.param(False, False, id="inactive-bool"),
        pytest.param(False, 0.0, id="inactive-float"),
    ],
)
def test_direct_runtime_retry_rejects_non_integer_match_revision(
    tmp_path, active, invalid_revision
):
    async def run():
        providers = Store(Model(["已接受的建议"]))
        runtime = SessionRuntime(initialize_data_root(tmp_path / "strict-match-retry"), providers)
        sid = runtime.create_session()["id"]
        try:
            if active:
                await begin(runtime, sid)
            original = await binding(runtime, sid)
            request = uuid4().hex
            tid = await ask(runtime, sid, "请分析局面", request_id=request, supplied=original)
            malformed = {**original, "expected_revision": invalid_revision}
            assert malformed == original  # Python equality must not define the input contract.
            calls = len(providers.adapter.messages)
            with pytest.raises(RuntimeInputError):
                await runtime.start_turn(sid, "请分析局面", request_id=uuid4().hex, match=malformed)
            with pytest.raises(RuntimeInputError):
                await runtime.start_turn(sid, "请分析局面", request_id=request, match=malformed)
            receipt = await runtime.start_turn(
                sid, "请分析局面", request_id=request, match=original
            )
            assert receipt["id"] == tid and receipt["status"] == "completed"
            assert len(providers.adapter.messages) == calls
            assert len(runtime.get_session(sid)["turns"]) == 1
            assert await binding(runtime, sid) == original
        finally:
            await runtime.close()

    asyncio.run(run())


def test_bound_turn_replay_after_new_match_is_receipt_only_and_rejects_rebinding(tmp_path):
    async def run():
        providers = Store(Model(["原局建议"]))
        runtime = SessionRuntime(initialize_data_root(tmp_path / "turn-replay"), providers)
        sid = runtime.create_session()["id"]
        try:
            first = await begin(runtime, sid)
            original = await binding(runtime, sid)
            request = uuid4().hex
            tid = await ask(runtime, sid, "我目前有6金币。", request_id=request, supplied=original)
            old_count = runtime._db.execute("SELECT COUNT(*) FROM match_observations").fetchone()[0]
            latest = await binding(runtime, sid)
            await runtime.match_control(sid, "new", {"request_id": uuid4().hex, **latest, **GAME})
            current = await binding(runtime, sid)
            calls = len(providers.adapter.messages)
            replay = await runtime.start_turn(
                sid, "我目前有6金币。", request_id=request, match=original
            )
            assert replay["id"] == tid and replay["status"] == "completed"
            assert replay["context"]["match"]["match_id"] == first["match_id"]
            assert await binding(runtime, sid) == current
            assert len(providers.adapter.messages) == calls
            assert (
                runtime._db.execute("SELECT COUNT(*) FROM match_observations").fetchone()[0]
                == old_count
            )
            for change in (
                {"match": current},
                {"match": original, "input_origin": "voice"},
                {"match": original, "review_match_id": first["match_id"]},
            ):
                with pytest.raises(RuntimeConflictError):
                    await runtime.start_turn(sid, "我目前有6金币。", request_id=request, **change)
        finally:
            await runtime.close()

    asyncio.run(run())


@pytest.mark.parametrize("action", ["selection", "delete", "restore"])
def test_guide_control_syncs_match_binding_and_excludes_old_advice(tmp_path, action):
    async def run():
        providers = Store(Model([OLD + " [S1]。"]))
        runtime = SessionRuntime(initialize_data_root(tmp_path / "guide-control"), providers)
        sid = runtime.create_session()["id"]
        try:
            a = save(runtime, "a", "灯芯是后排中央的潮灯守卫。" * 4)
            b = save(runtime, "b", "灯芯是前排侧翼的红色工匠。" * 4)
            await adopt(runtime, a)
            backup = await runtime.backup_guides({"request_id": uuid4().hex})
            if action == "restore":
                await adopt(runtime, b)
            await begin(runtime, sid)
            first = await ask(runtime, sid, "灯芯是什么意思？")
            assert runtime._db.execute(
                "SELECT 1 FROM turn_guides WHERE turn_id=?", (first,)
            ).fetchone()
            before = await binding(runtime, sid)
            if action == "selection":
                await adopt(runtime, b)
                expected = b
            elif action == "delete":
                await runtime.guide_delete(
                    a["guide_id"],
                    {
                        "request_id": uuid4().hex,
                        "expected_revision": runtime.memory.guides.revision(),
                    },
                )
                expected = None
            else:
                await runtime.restore_guides(
                    backup["backup_id"],
                    {
                        "request_id": uuid4().hex,
                        "expected_revision": runtime.memory.guides.revision(),
                    },
                )
                expected = a
            catalog = await runtime.match_catalog(sid)
            assert catalog["revision"] > before["expected_revision"]
            assert (
                catalog["current"]["selection"]["guide_id"]
                if catalog["current"]["selection"]
                else None
            ) == (expected["guide_id"] if expected else None)
            providers.adapter = Model([NEW])
            second = await ask(runtime, sid, "灯芯是什么意思？")
            sent = json.dumps(providers.adapter.messages, ensure_ascii=False)
            assert OLD not in sent
            assert (
                runtime._binding(second)["selection"]["guide_id"]
                if expected
                else runtime._binding(second)["selection"]
            ) == (expected["guide_id"] if expected else None)
            if action == "selection":
                assert OLD in runtime.get_session(sid)["turns"][0]["confirmed_text"]
            else:
                assert not runtime.get_session(sid)["turns"][0]["delivered_text"]
        finally:
            await runtime.close()

    asyncio.run(run())
