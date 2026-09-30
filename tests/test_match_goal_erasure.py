"""Runtime -> real graph goal consumers, durable erasure and actual DB scrubbing.

The forgotten personal fact is added *after* the model consumes the match goal.
This excludes turn_memory as an accidental substitute for goal provenance.
All model responses are synthetic and deliberately do not quote the goal.
"""

import asyncio
import hashlib
import json
import sqlite3
from pathlib import Path
from uuid import uuid4

import pytest
from test_runtime import Model, Store, settled

from ai_neko.config.paths import initialize_data_root
from ai_neko.memory import MemoryAccessError
from ai_neko.runtime import RuntimeConflictError, SessionRuntime
from ai_neko.runtime.match_store import MatchStore


async def control(runtime, sid, action="start", **payload):
    catalog = await runtime.match_catalog(sid)
    if action in {"start", "new"}:
        payload = {"game": "fog", "platform": "pc", "mode": "ranked", **payload}
    return await runtime.match_control(
        sid,
        action,
        {"request_id": uuid4().hex, "expected_revision": catalog["revision"], **payload},
    )


async def ask(runtime, sid, model, answer, *, ack=True, review=None):
    model.chunks = [answer]
    catalog = await runtime.match_catalog(sid)
    turn = await runtime.start_turn(
        sid,
        "请给出建议。" if review is None else "请回顾该局。",
        match={
            "match_id": catalog["current"]["match_id"] if catalog["current"] else None,
            "expected_revision": catalog["revision"],
        },
        **({"review_match_id": review} if review else {}),
    )
    tid = turn["id"]
    await settled(runtime, sid, tid)
    batch = runtime.events(sid, tid)
    assert batch["status"] == "completed", runtime.get_session(sid)["turns"][-1]
    if ack:
        runtime.ack(sid, tid, batch["sent_seq"])
    return tid


def context(messages):
    for message in messages:
        content = message.get("content")
        if isinstance(content, str) and '"match_context"' in content:
            return json.loads(content[content.index("{") :])["match_context"]
    raise AssertionError("Actual model messages did not contain match context")


def assert_goal_consumed(runtime, model, tid, mid, goal):
    assert context(model.messages[-1])["goal"] == goal
    assert not runtime._db.execute("SELECT 1 FROM turn_memory WHERE turn_id=?", (tid,)).fetchone()
    digest = hashlib.sha256(goal.encode()).hexdigest()
    assert tuple(
        runtime._db.execute(
            "SELECT match_id,goal_hash FROM turn_match_goals WHERE turn_id=?", (tid,)
        ).fetchone()
    ) == (mid, digest)
    assert (
        runtime._db.execute(
            "SELECT goal FROM match_goal_evidence WHERE match_id=? AND goal_hash=?", (mid, digest)
        ).fetchone()[0]
        == goal
    )
    return digest


def assert_erased(runtime, sid, tids):
    summaries = {row["id"]: row for row in runtime.get_session(sid)["turns"]}
    for tid in tids:
        assert summaries[tid]["assistant_text"] == ""
        for table in ("events", "turn_match_goals", "turn_history", "turn_memory"):
            assert not runtime._db.execute(
                f"SELECT 1 FROM {table} WHERE turn_id=?", (tid,)
            ).fetchone()
    assert not runtime._db.execute("SELECT 1 FROM memory_erasure").fetchone()


def assert_db_bytes_erased(root, *texts):
    candidates = [path for path in root.rglob("*.sqlite*") if path.is_file()]
    assert any(path.name == "conversation.sqlite" for path in candidates)
    assert any(path.name == "chat-graph.sqlite" for path in candidates)
    for path in candidates:
        raw = path.read_bytes()
        for text in texts:
            assert text.encode() not in raw, (path, text)
            assert json.dumps(text, ensure_ascii=True)[1:-1].encode() not in raw, (path, text)


def remember_after_consumption(runtime, goal):
    result = runtime.memory.remember(goal, source_id="manual:goal-erasure-" + uuid4().hex)
    return result["id"]


def test_manual_goal_consumer_erased_without_personal_recall_dependency(tmp_path):
    async def run():
        root = tmp_path / "synthetic-root"
        model = Model()
        runtime = SessionRuntime(initialize_data_root(root), Store(model))
        goal, derived = "私人目标水晶玫瑰-原始副本", "DERIVED_GOAL_ONLY_REPLY_7318"
        try:
            sid = runtime.create_session()["id"]
            created = await control(runtime, sid, goal=goal)
            mid = created["match_id"]
            tid = await ask(runtime, sid, model, derived)
            digest = assert_goal_consumed(runtime, model, tid, mid, goal)
            fact_id = remember_after_consumption(runtime, goal)
            await runtime.forget_memory(fact_id)
            assert runtime.matches.current(sid)["goal"] == ""
            assert runtime.matches.current(sid)["state_revision"] > created["revision"]
            assert_erased(runtime, sid, [tid])
            assert not runtime._db.execute(
                "SELECT 1 FROM match_goal_evidence WHERE match_id=? AND goal_hash=?", (mid, digest)
            ).fetchone()
            assert_db_bytes_erased(root, goal, derived)
            await ask(runtime, sid, model, "安全的新建议")
            request = json.dumps(model.messages[-1], ensure_ascii=False)
            assert goal not in request and derived not in request
        finally:
            await runtime.close()
        assert_db_bytes_erased(root, goal, derived)

    asyncio.run(run())


def test_changed_goal_erases_old_consumers_and_preserves_new_goal_and_reply(tmp_path):
    async def run():
        root = tmp_path / "synthetic-root"
        model = Model()
        runtime = SessionRuntime(initialize_data_root(root), Store(model))
        old, new = "待遗忘目标碧蓝绒花", "本局独立保留目标赤色山雀"
        old_reply, new_reply = "OLD_GOAL_DERIVED_2684", "UNRELATED_NEW_GOAL_REPLY_9526"
        try:
            sid = runtime.create_session()["id"]
            mid = (await control(runtime, sid, goal=old))["match_id"]
            # No display ACK: the later reply must not depend on old advice.
            old_tid = await ask(runtime, sid, model, old_reply, ack=False)
            old_hash = assert_goal_consumed(runtime, model, old_tid, mid, old)
            await control(runtime, sid, "update", match_id=mid, goal=new)
            new_tid = await ask(runtime, sid, model, new_reply)
            new_hash = assert_goal_consumed(runtime, model, new_tid, mid, new)
            assert old not in json.dumps(model.messages[-1], ensure_ascii=False)
            assert old_reply not in json.dumps(model.messages[-1], ensure_ascii=False)
            await runtime.forget_memory(remember_after_consumption(runtime, old))
            assert runtime.matches.current(sid)["goal"] == new
            assert_erased(runtime, sid, [old_tid])
            kept = next(row for row in runtime.get_session(sid)["turns"] if row["id"] == new_tid)
            assert kept["assistant_text"] == new_reply
            evidence = runtime._db.execute(
                "SELECT goal_hash,goal FROM match_goal_evidence WHERE match_id=?", (mid,)
            ).fetchall()
            assert [tuple(row) for row in evidence] == [(new_hash, new)]
            assert old_hash != new_hash
            assert_db_bytes_erased(root, old, old_reply)
        finally:
            await runtime.close()

    asyncio.run(run())


def test_ended_goal_cleared_without_reviving_or_erasing_unrelated_matches(tmp_path):
    async def run():
        root = tmp_path / "synthetic-root"
        model = Model()
        runtime = SessionRuntime(initialize_data_root(root), Store(model))
        old = "历史待删目标紫水晶"
        try:
            sid = runtime.create_session()["id"]
            old_mid = (await control(runtime, sid, goal=old))["match_id"]
            old_tid = await ask(runtime, sid, model, "OLD_MATCH_DERIVED_6612")
            assert_goal_consumed(runtime, model, old_tid, old_mid, old)
            new_mid = (await control(runtime, sid, "new", match_id=old_mid, goal="新局目标保留"))[
                "match_id"
            ]
            new_tid = await ask(runtime, sid, model, "KEEP_CURRENT_REPLY_8923")
            foreign_sid = runtime.create_session()["id"]
            foreign_mid = (await control(runtime, foreign_sid, goal="另一会话目标保留"))["match_id"]
            foreign_tid = await ask(runtime, foreign_sid, model, "KEEP_OTHER_SESSION_REPLY_5943")
            await runtime.forget_memory(remember_after_consumption(runtime, old))
            assert_erased(runtime, sid, [old_tid])
            assert runtime.matches.get(sid, old_mid)["goal"] == ""
            assert runtime.matches.get(sid, old_mid)["status"] == "ended"
            assert runtime.matches.current(sid)["match_id"] == new_mid
            assert runtime.matches.current(sid)["goal"] == "新局目标保留"
            assert runtime.matches.get(foreign_sid, foreign_mid)["goal"] == "另一会话目标保留"
            assert (
                next(row for row in runtime.get_session(sid)["turns"] if row["id"] == new_tid)[
                    "assistant_text"
                ]
                == "KEEP_CURRENT_REPLY_8923"
            )
            assert runtime.get_session(foreign_sid)["turns"][0]["id"] == foreign_tid
            assert (
                runtime.get_session(foreign_sid)["turns"][0]["assistant_text"]
                == "KEEP_OTHER_SESSION_REPLY_5943"
            )
            assert_db_bytes_erased(root, old, "OLD_MATCH_DERIVED_6612")
        finally:
            await runtime.close()

    asyncio.run(run())


def test_old_goal_advice_consumer_is_erased_through_actual_history_dependency(tmp_path):
    async def run():
        model = Model()
        runtime = SessionRuntime(initialize_data_root(tmp_path / "data"), Store(model))
        goal = "传递依赖目标珍珠绒树"
        try:
            sid = runtime.create_session()["id"]
            mid = (await control(runtime, sid, goal=goal))["match_id"]
            original = await ask(runtime, sid, model, "FIRST_DERIVED_ADVICE_5921")
            assert_goal_consumed(runtime, model, original, mid, goal)
            await control(runtime, sid, "update", match_id=mid, goal="")
            consumer = await ask(runtime, sid, model, "SECOND_DERIVED_ADVICE_7513")
            request_context = context(model.messages[-1])
            assert request_context["goal"] == ""
            assert request_context["last_delivered_advice"]["turn_id"] == original
            assert runtime._db.execute(
                "SELECT 1 FROM turn_history WHERE turn_id=? AND source_turn_id=?",
                (consumer, original),
            ).fetchone()
            assert not runtime._db.execute(
                "SELECT 1 FROM turn_match_goals WHERE turn_id=?", (consumer,)
            ).fetchone()
            await runtime.forget_memory(remember_after_consumption(runtime, goal))
            assert_erased(runtime, sid, [original, consumer])
            assert_db_bytes_erased(
                runtime.paths.root, goal, "FIRST_DERIVED_ADVICE_5921", "SECOND_DERIVED_ADVICE_7513"
            )
        finally:
            await runtime.close()

    asyncio.run(run())


def test_explicit_historical_review_records_goal_consumer_for_erasure(tmp_path):
    async def run():
        root = tmp_path / "data"
        model = Model()
        runtime = SessionRuntime(initialize_data_root(root), Store(model))
        goal = "复盘专用旧目标海盐玻璃"
        try:
            sid = runtime.create_session()["id"]
            mid = (await control(runtime, sid, goal=goal))["match_id"]
            original = await ask(runtime, sid, model, "ORIGINAL_REVIEW_TARGET_7419", ack=False)
            await control(runtime, sid, "end", match_id=mid)
            review = await ask(runtime, sid, model, "HISTORICAL_REVIEW_DERIVED_8719", review=mid)
            assert context(model.messages[-1])["history_only"]
            assert_goal_consumed(runtime, model, review, mid, goal)
            await runtime.forget_memory(remember_after_consumption(runtime, goal))
            assert_erased(runtime, sid, [original, review])
            assert runtime.matches.current(sid) is None
            assert runtime.matches.get(sid, mid)["goal"] == ""
            assert_db_bytes_erased(
                root, goal, "ORIGINAL_REVIEW_TARGET_7419", "HISTORICAL_REVIEW_DERIVED_8719"
            )
        finally:
            await runtime.close()

    asyncio.run(run())


@pytest.mark.parametrize("phase", ["after_source", "before_goals", "after_goals", "checkpoint"])
def test_goal_erasure_failure_recovers_from_identifier_only_intent_after_restart(
    tmp_path, monkeypatch, phase
):
    async def run():
        root = tmp_path / "isolated-root"
        paths = initialize_data_root(root)
        model = Model()
        runtime = SessionRuntime(paths, Store(model))
        goal, answer = "故障恢复目标翡翠紫罗兰", "RECOVERY_DERIVED_6924"
        sid = runtime.create_session()["id"]
        mid = (await control(runtime, sid, goal=goal))["match_id"]
        tid = await ask(runtime, sid, model, answer)
        digest = assert_goal_consumed(runtime, model, tid, mid, goal)
        fact_id = remember_after_consumption(runtime, goal)
        original_forget = MatchStore.forget_goals
        original_forget_source = runtime.memory.forget_source
        original_connect = sqlite3.connect

        def fail_forget(store, ids, **kwargs):
            if phase == "after_goals":
                original_forget(store, ids, **kwargs)
            raise OSError("synthetic goal erasure interruption")

        def fail_checkpoint(path, *args, **kwargs):
            if Path(path) == paths.checkpoints / "chat-graph.sqlite":
                raise OSError("synthetic checkpoint erasure interruption")
            return original_connect(path, *args, **kwargs)

        def fail_after_source(*args, **kwargs):
            result = original_forget_source(*args, **kwargs)
            # Source traversal order is intentionally irrelevant. Interrupt only
            # after the actual fact removal, never on a derived nonexistent turn.
            if fact_id in result["fact_ids"]:
                raise OSError("synthetic source erasure interruption")
            return result

        try:
            with monkeypatch.context() as fail:
                if phase == "after_source":
                    fail.setattr(runtime.memory, "forget_source", fail_after_source)
                elif phase == "checkpoint":
                    fail.setattr(sqlite3, "connect", fail_checkpoint)
                else:
                    fail.setattr(MatchStore, "forget_goals", fail_forget)
                with pytest.raises(OSError, match="synthetic"):
                    await runtime.forget_memory(fact_id)
            raw = runtime._db.execute("SELECT payload FROM memory_erasure WHERE id=1").fetchone()[0]
            pending = json.loads(raw)
            assert goal not in json.dumps(pending, ensure_ascii=False)
            assert mid in pending["match_goal_ids"]
            assert [mid, digest] in pending["match_goal_evidence"]
            assert "turn:" + tid in pending["source_ids"]
            assert not runtime.memory.list_facts()
            if phase in {"after_goals", "checkpoint"}:
                assert runtime.matches.current(sid)["goal"] == ""
            if phase == "checkpoint":
                assert not runtime._db.execute(
                    "SELECT 1 FROM match_goal_evidence WHERE match_id=?", (mid,)
                ).fetchone()
            with pytest.raises(RuntimeConflictError):
                runtime.get_session(sid)
        finally:
            await runtime.close()

        reopened_model = Model()
        reopened = SessionRuntime(paths, Store(reopened_model))
        try:
            assert not reopened_model.messages
            assert_erased(reopened, sid, [tid])
            assert reopened.matches.current(sid)["goal"] == ""
            assert reopened.matches.current(sid)["status"] == "needs_update"
            assert_db_bytes_erased(root, goal, answer)
            await ask(reopened, sid, reopened_model, "恢复后安全建议")
            actual_request = json.dumps(reopened_model.messages[-1], ensure_ascii=False)
            assert goal not in actual_request and answer not in actual_request
        finally:
            await reopened.close()
        assert_db_bytes_erased(root, goal, answer)

    asyncio.run(run())


@pytest.mark.parametrize("interrupt_after_commit", [False, True])
def test_memory_restore_removal_erases_goal_consumers_even_if_result_is_lost(
    tmp_path, monkeypatch, interrupt_after_commit
):
    async def run():
        root = tmp_path / "isolated-restore-root"
        paths = initialize_data_root(root)
        model = Model()
        runtime = SessionRuntime(paths, Store(model))
        goal, answer = "旧快照移除目标海棠玉石", "RESTORE_GOAL_CONSUMER_3792"
        try:
            backup = await runtime.backup_memory()
            sid = runtime.create_session()["id"]
            mid = (await control(runtime, sid, goal=goal))["match_id"]
            tid = await ask(runtime, sid, model, answer)
            assert_goal_consumed(runtime, model, tid, mid, goal)
            remember_after_consumption(runtime, goal)
            original_restore = runtime.memory.restore

            def interrupted_restore(*args, **kwargs):
                original_restore(*args, **kwargs)
                raise OSError("synthetic restore committed before response")

            if interrupt_after_commit:
                with monkeypatch.context() as fail:
                    fail.setattr(runtime.memory, "restore", interrupted_restore)
                    with pytest.raises(OSError, match="synthetic restore"):
                        await runtime.restore_memory(backup["id"], runtime.memory.revision())
            else:
                await runtime.restore_memory(backup["id"], runtime.memory.revision())
            assert runtime.memory.list_facts() == []
        finally:
            await runtime.close()
        reopened = SessionRuntime(paths, Store(Model()))
        try:
            assert reopened.matches.current(sid)["goal"] == ""
            assert_erased(reopened, sid, [tid])
            assert_db_bytes_erased(root, goal, answer)
        finally:
            await reopened.close()

    asyncio.run(run())


def test_rejected_restore_prepares_candidates_without_erasing_retained_goals(tmp_path, monkeypatch):
    async def run():
        model = Model()
        runtime = SessionRuntime(initialize_data_root(tmp_path / "data"), Store(model))
        goal, answer = "拒绝恢复需保留目标碧玉花", "REJECTED_RESTORE_KEEP_REPLY_5832"
        try:
            sid = runtime.create_session()["id"]
            mid = (await control(runtime, sid, goal=goal))["match_id"]
            tid = await ask(runtime, sid, model, answer)
            digest = assert_goal_consumed(runtime, model, tid, mid, goal)
            fact_id = remember_after_consumption(runtime, goal)
            previous_revision = runtime.matches.revision(sid)
            prepared = []
            original_restore = runtime.memory.restore

            def inspect_then_restore(*args, **kwargs):
                raw = runtime._db.execute(
                    "SELECT payload FROM memory_erasure WHERE id=1"
                ).fetchone()[0]
                prepared.append(json.loads(raw))
                return original_restore(*args, **kwargs)

            with monkeypatch.context() as observer:
                observer.setattr(runtime.memory, "restore", inspect_then_restore)
                with pytest.raises(MemoryAccessError, match="backup_not_found"):
                    await runtime.restore_memory(
                        "memory-" + uuid4().hex + ".sqlite", runtime.memory.revision()
                    )
            assert prepared and prepared[0]["evidence_candidates"]
            assert goal not in json.dumps(prepared[0], ensure_ascii=False)
            assert any(
                item["kind"] == "fact" and item["id"] == fact_id
                for item in prepared[0]["evidence_candidates"]
            )
            assert runtime.matches.get(sid, mid)["goal"] == goal
            assert runtime.matches.revision(sid) == previous_revision
            assert runtime.get_session(sid)["turns"][0]["assistant_text"] == answer
            assert (
                runtime._db.execute(
                    "SELECT goal FROM match_goal_evidence WHERE match_id=? AND goal_hash=?",
                    (mid, digest),
                ).fetchone()[0]
                == goal
            )
            assert runtime.memory.list_facts()[0]["id"] == fact_id
            assert not runtime._db.execute("SELECT 1 FROM memory_erasure").fetchone()
            assert runtime._db.execute(
                "SELECT 1 FROM turn_match_goals WHERE turn_id=?", (tid,)
            ).fetchone()
        finally:
            await runtime.close()

    asyncio.run(run())


@pytest.mark.parametrize("lose_result", [False, True])
def test_restore_activates_only_removed_fact_candidates_and_keeps_retained_match(
    tmp_path, monkeypatch, lose_result
):
    async def run():
        model = Model()
        root = tmp_path / "data"
        runtime = SessionRuntime(initialize_data_root(root), Store(model))
        kept_goal, removed_goal = "快照保留目标赤羽茶树", "快照移除目标蓝莓雪莲"
        try:
            kept_sid = runtime.create_session()["id"]
            kept_mid = (await control(runtime, kept_sid, goal=kept_goal))["match_id"]
            kept_tid = await ask(runtime, kept_sid, model, "RETAINED_FACT_ADVICE_7824")
            assert_goal_consumed(runtime, model, kept_tid, kept_mid, kept_goal)
            kept_fact = remember_after_consumption(runtime, kept_goal)
            backup = await runtime.backup_memory()
            removed_sid = runtime.create_session()["id"]
            removed_mid = (await control(runtime, removed_sid, goal=removed_goal))["match_id"]
            removed_tid = await ask(runtime, removed_sid, model, "REMOVED_FACT_ADVICE_9463")
            assert context(model.messages[-1])["goal"] == removed_goal
            removed_fact = remember_after_consumption(runtime, removed_goal)
            original_restore = runtime.memory.restore
            captured = []

            def inspect_restore(*args, **kwargs):
                captured.append(
                    json.loads(
                        runtime._db.execute(
                            "SELECT payload FROM memory_erasure WHERE id=1"
                        ).fetchone()[0]
                    )
                )
                result = original_restore(*args, **kwargs)
                if lose_result:
                    raise OSError("synthetic restore result lost")
                return result

            with monkeypatch.context() as observer:
                observer.setattr(runtime.memory, "restore", inspect_restore)
                if lose_result:
                    with pytest.raises(OSError, match="result lost"):
                        await runtime.restore_memory(backup["id"], runtime.memory.revision())
                else:
                    await runtime.restore_memory(backup["id"], runtime.memory.revision())
            candidate_facts = {
                item["id"] for item in captured[0]["evidence_candidates"] if item["kind"] == "fact"
            }
            assert {kept_fact, removed_fact} <= candidate_facts
            assert kept_goal not in json.dumps(captured[0], ensure_ascii=False)
            assert removed_goal not in json.dumps(captured[0], ensure_ascii=False)
            assert {fact["id"] for fact in runtime.memory.list_facts()} == {kept_fact}
            assert runtime.matches.get(kept_sid, kept_mid)["goal"] == kept_goal
            assert (
                runtime.get_session(kept_sid)["turns"][0]["assistant_text"]
                == "RETAINED_FACT_ADVICE_7824"
            )
            assert runtime._db.execute(
                "SELECT 1 FROM turn_match_goals WHERE turn_id=?", (kept_tid,)
            ).fetchone()
            assert runtime.matches.get(removed_sid, removed_mid)["goal"] == ""
            assert_erased(runtime, removed_sid, [removed_tid])
            assert_db_bytes_erased(root, removed_goal, "REMOVED_FACT_ADVICE_9463")
        finally:
            await runtime.close()

    asyncio.run(run())
