"""Deterministic lifecycle barriers; all models, memory and files are synthetic."""

import asyncio
import json
import sqlite3
import threading
from uuid import uuid4

import pytest
from test_runtime import Model, Store, settled

from ai_neko.config.paths import initialize_data_root
from ai_neko.config.schema import MigrationError
from ai_neko.runtime import RuntimeConflictError, SessionRuntime


class WorkerGate:
    """Pause a completed SQLite read without holding its memory transaction."""

    def __init__(self, original):
        self.original = original
        self.loop = asyncio.get_running_loop()
        self.entered = asyncio.Queue()
        self.release = threading.Event()

    def __call__(self, *args, **kwargs):
        result = self.original(*args, **kwargs)
        self.loop.call_soon_threadsafe(self.entered.put_nowait, True)
        if not self.release.wait(10):
            raise TimeoutError("synthetic worker barrier was not released")
        return result

    async def ready(self):
        await asyncio.wait_for(self.entered.get(), 3)


async def deliver(runtime, sid, text):
    turn = await runtime.start_turn(sid, text)
    await settled(runtime, sid, turn["id"])
    batch = runtime.events(sid, turn["id"])
    runtime.ack(sid, turn["id"], batch["last_seq"])
    return turn["id"]


def test_cancel_during_recall_never_accepts_or_contacts_model(tmp_path, monkeypatch):
    async def run():
        runtime = SessionRuntime(initialize_data_root(tmp_path / "cancel"), Store())
        sid, rid = runtime.create_session()["id"], uuid4().hex
        gate = WorkerGate(runtime.memory.recall_context)
        monkeypatch.setattr(runtime.memory, "recall_context", gate)
        pending = asyncio.create_task(runtime.start_turn(sid, "cancel me", request_id=rid))
        try:
            await gate.ready()
            assert (await runtime.cancel_request(sid, rid))["status"] == "cancelled"
            gate.release.set()
            with pytest.raises(RuntimeConflictError):
                await pending
            assert runtime.get_session(sid)["turns"] == []
            assert runtime.providers.adapter.messages == []
        finally:
            gate.release.set()
            await runtime.close()

    asyncio.run(run())


@pytest.mark.parametrize("same_request", [False, True])
def test_concurrent_recall_acceptance_is_atomic_and_retry_idempotent(
    tmp_path, monkeypatch, same_request
):
    async def run():
        runtime = SessionRuntime(
            initialize_data_root(tmp_path / "concurrent"), Store(Model(gate=asyncio.Event()))
        )
        sid, rid = runtime.create_session()["id"], uuid4().hex
        gate = WorkerGate(runtime.memory.recall_context)
        monkeypatch.setattr(runtime.memory, "recall_context", gate)
        first = asyncio.create_task(runtime.start_turn(sid, "same", request_id=rid))
        second = asyncio.create_task(
            runtime.start_turn(sid, "same", request_id=rid if same_request else uuid4().hex)
        )
        try:
            await gate.ready()
            await gate.ready()
            gate.release.set()
            results = await asyncio.gather(first, second, return_exceptions=True)
            accepted = [item for item in results if isinstance(item, dict)]
            if same_request:
                assert len(accepted) == 2 and accepted[0]["id"] == accepted[1]["id"]
            else:
                assert len(accepted) == 1
                assert sum(isinstance(item, RuntimeConflictError) for item in results) == 1
            assert len(runtime.get_session(sid)["turns"]) == 1
        finally:
            gate.release.set()
            await runtime.close()

    asyncio.run(run())


def test_changed_memory_revision_rejects_finished_old_snapshot(tmp_path, monkeypatch):
    async def run():
        runtime = SessionRuntime(initialize_data_root(tmp_path / "revision"), Store())
        sid = runtime.create_session()["id"]
        fact = runtime.memory.remember("喜欢无糖咖啡", source_id="manual:one")
        gate = WorkerGate(runtime.memory.recall_context)
        monkeypatch.setattr(runtime.memory, "recall_context", gate)
        pending = asyncio.create_task(runtime.start_turn(sid, "我喜欢什么"))
        try:
            await gate.ready()
            runtime.memory.correct(fact["id"], "喜欢白开水", source_id="manual:two")
            gate.release.set()
            with pytest.raises(RuntimeConflictError):
                await pending
            assert runtime.providers.adapter.messages == []
            assert runtime.get_session(sid)["turns"] == []
        finally:
            gate.release.set()
            await runtime.close()

    asyncio.run(run())


@pytest.mark.parametrize("mutation", ["correct", "forget", "restore"])
def test_mutation_drains_pending_recall_and_rejects_its_stale_result(
    tmp_path, monkeypatch, mutation
):
    async def run():
        runtime = SessionRuntime(initialize_data_root(tmp_path / mutation), Store())
        sid = runtime.create_session()["id"]
        backup = await runtime.backup_memory()
        fact = runtime.memory.remember("喜欢无糖咖啡", source_id="manual:one")
        gate = WorkerGate(runtime.memory.recall_context)
        monkeypatch.setattr(runtime.memory, "recall_context", gate)
        began = asyncio.Event()
        original = runtime._stop_memory_work

        async def stop():
            began.set()
            await original()

        monkeypatch.setattr(runtime, "_stop_memory_work", stop)
        pending = asyncio.create_task(runtime.start_turn(sid, "我喜欢什么"))
        changing = None
        try:
            await gate.ready()
            if mutation == "correct":
                operation = runtime.correct_memory(fact["id"], "喜欢白开水")
            elif mutation == "forget":
                operation = runtime.forget_memory(fact["id"])
            else:
                operation = runtime.restore_memory(backup["id"], runtime.memory.revision())
            changing = asyncio.create_task(operation)
            await asyncio.wait_for(began.wait(), 3)
            assert not changing.done()
            gate.release.set()
            with pytest.raises(RuntimeConflictError):
                await pending
            await changing
            assert runtime.providers.adapter.messages == []
            assert runtime.get_session(sid)["turns"] == []
            assert "无糖咖啡" not in json.dumps(runtime.memory.list_facts(), ensure_ascii=False)
        finally:
            gate.release.set()
            if changing:
                await asyncio.gather(changing, return_exceptions=True)
            await runtime.close()

    asyncio.run(run())


def test_close_waits_for_physical_worker_even_after_repeated_request_cancellation(
    tmp_path, monkeypatch
):
    async def run():
        runtime = SessionRuntime(initialize_data_root(tmp_path / "close"), Store())
        sid = runtime.create_session()["id"]
        gate = WorkerGate(runtime.memory.recall_context)
        monkeypatch.setattr(runtime.memory, "recall_context", gate)
        began = asyncio.Event()
        original = runtime._close_impl

        async def closing():
            began.set()
            await original()

        monkeypatch.setattr(runtime, "_close_impl", closing)
        pending = asyncio.create_task(runtime.start_turn(sid, "close me"))
        try:
            await gate.ready()
            pending.cancel()
            closing_task = asyncio.create_task(runtime.close())
            await asyncio.wait_for(began.wait(), 3)
            pending.cancel()
            assert not closing_task.done() and not runtime._closed
            gate.release.set()
            with pytest.raises(asyncio.CancelledError):
                await pending
            await closing_task
            assert runtime._closed and not runtime._memory_io
            assert runtime.providers.adapter.messages == []
        finally:
            gate.release.set()
            await runtime.close()

    asyncio.run(run())


def test_disconnected_erasure_finishes_before_close_and_blocks_old_event_reads(
    tmp_path, monkeypatch
):
    async def run():
        runtime = SessionRuntime(initialize_data_root(tmp_path / "erase"), Store())
        sid = runtime.create_session()["id"]
        fact = runtime.memory.remember("喜欢无糖咖啡", source_id="manual:one")
        tid = await deliver(runtime, sid, "我喜欢什么")
        # Pause before physical erasure begins, leaving old records readable in SQL.
        gate = WorkerGate(lambda *args: args)
        original = runtime._complete_erasure

        def erasing(*args):
            return original(*gate(*args))

        monkeypatch.setattr(runtime, "_complete_erasure", erasing)
        erasure = asyncio.create_task(runtime.forget_memory(fact["id"]))
        closing = None
        try:
            await gate.ready()
            for read in (
                lambda: runtime.list_sessions(),
                lambda: runtime.get_session(sid),
                lambda: runtime.events(sid, tid),
                lambda: runtime.ack(sid, tid, 0),
            ):
                with pytest.raises(RuntimeConflictError):
                    read()
            # Cancellation remains durable during erasure, without sharing the
            # erasure worker's SQLite transaction.
            rid = uuid4().hex
            assert (await runtime.cancel_request(sid, rid))["status"] == "cancelled"
            erasure.cancel()
            closing = asyncio.create_task(runtime.close())
            await asyncio.sleep(0)  # schedule cancellation, not a timing assertion
            assert not erasure.done() and not closing.done()
            gate.release.set()
            with pytest.raises(asyncio.CancelledError):
                await erasure
            await closing
            reopened = SessionRuntime(runtime.paths, Store())
            assert reopened.get_session(sid)["turns"][0]["input"] == "[已遗忘的对话]"
            assert not reopened._db.execute("SELECT 1 FROM memory_erasure").fetchone()
            with pytest.raises(RuntimeConflictError):
                await reopened.start_turn(sid, "revoked", request_id=rid)
            await reopened.close()
        finally:
            gate.release.set()
            if closing:
                await asyncio.gather(closing, return_exceptions=True)
            await runtime.close()

    asyncio.run(run())


def test_paraphrased_history_dependency_is_erased_after_restart(tmp_path):
    async def run():
        paths = initialize_data_root(tmp_path / "dependencies")
        runtime = SessionRuntime(paths, Store(Model(["饮料偏好是无糖咖啡。"])))
        sid = runtime.create_session()["id"]
        independent = runtime.create_session()["id"]
        fact = runtime.memory.remember("喜欢无糖咖啡", source_id="manual:one")
        first = await deliver(runtime, sid, "我喜欢什么")
        runtime.providers.adapter = Model(["那种苦味饮品可以安排在早餐之后。"])
        second = await deliver(runtime, sid, "换种说法再说明")
        other = await deliver(runtime, independent, "无关的独立会话")
        assert not runtime._db.execute(
            "SELECT 1 FROM turn_memory WHERE turn_id=?", (second,)
        ).fetchone()
        assert runtime._db.execute(
            "SELECT 1 FROM turn_history WHERE turn_id=? AND source_turn_id=?", (second, first)
        ).fetchone()
        await runtime.close()
        reopened = SessionRuntime(paths, Store())
        await reopened.forget_memory(fact["id"])
        assert all(t["input"] == "[已遗忘的对话]" for t in reopened.get_session(sid)["turns"])
        assert reopened.get_session(independent)["turns"][0]["id"] == other
        assert reopened.get_session(independent)["turns"][0]["input"] == "无关的独立会话"
        await reopened.close()

    asyncio.run(run())


def test_future_conversation_schema_is_rejected_before_journal_changes(tmp_path):
    paths = initialize_data_root(tmp_path / "future")
    database = paths.memory / "conversation.sqlite"
    with sqlite3.connect(database) as connection:
        connection.execute("CREATE TABLE sentinel(value TEXT)")
        connection.execute("INSERT INTO sentinel VALUES ('untouched')")
        connection.execute("PRAGMA user_version=999")
    original = database.read_bytes()
    with pytest.raises(MigrationError):
        SessionRuntime(paths, Store())
    assert database.read_bytes() == original
    assert not (paths.memory / "conversation.sqlite-wal").exists()


def test_successful_conversation_upgrade_removes_only_automatic_safety_copies(tmp_path):
    async def run():
        paths = initialize_data_root(tmp_path / "upgrade")
        runtime = SessionRuntime(paths, Store())
        await runtime.close()
        with sqlite3.connect(paths.memory / "conversation.sqlite") as connection:
            connection.execute("PRAGMA user_version=2")
            connection.execute("DROP TABLE turn_history")
        old = paths.backups / "conversation-pre-migration-v2.sqlite"
        old.write_bytes(b"old synthetic conversation recovery copy")
        explicit = paths.backups / ("memory-" + uuid4().hex + ".sqlite")
        explicit.write_bytes(b"explicit user snapshot")
        runtime = SessionRuntime(paths, Store())
        assert runtime._db.execute("PRAGMA user_version").fetchone()[0] == 3
        assert not old.exists()
        assert not (paths.backups / "conversation-pre-migration-v3.sqlite").exists()
        assert explicit.read_bytes() == b"explicit user snapshot"
        await runtime.close()

    asyncio.run(run())


def test_failed_conversation_upgrade_preserves_recovery_copy(tmp_path, monkeypatch):
    import ai_neko.runtime.service as service

    async def prepare():
        paths = initialize_data_root(tmp_path / "upgrade-failure")
        runtime = SessionRuntime(paths, Store())
        await runtime.close()
        with sqlite3.connect(paths.memory / "conversation.sqlite") as connection:
            connection.execute("PRAGMA user_version=2")
            connection.execute("DROP TABLE turn_history")
        return paths

    paths = asyncio.run(prepare())
    old = paths.backups / "conversation-pre-migration-v2.sqlite"
    old.write_bytes(b"old synthetic recovery copy")

    def fail(_database):
        raise RuntimeError("synthetic migration failure")

    monkeypatch.setattr(service, "_MIGRATIONS", [(2, service._conversation_v2), (3, fail)])
    with pytest.raises(MigrationError):
        SessionRuntime(paths, Store())
    assert old.read_bytes() == b"old synthetic recovery copy"
    assert (paths.backups / "conversation-pre-migration-v3.sqlite").exists()
    with sqlite3.connect(paths.memory / "conversation.sqlite") as connection:
        assert connection.execute("PRAGMA user_version").fetchone()[0] == 2


def test_first_acceptance_does_not_wait_on_second_recall_memory_lock(tmp_path, monkeypatch):
    async def run():
        runtime = SessionRuntime(initialize_data_root(tmp_path / "recall-lock"), Store())
        first_sid = runtime.create_session()["id"]
        second_sid = runtime.create_session()["id"]
        snapshot_gate = WorkerGate(runtime.memory.recall_context)
        locked_gate = WorkerGate(runtime.memory._recall)
        original_context = runtime.memory.recall_context
        original_recall = runtime.memory._recall

        def recall(query, **kwargs):
            if query == "second":
                return locked_gate(query, **kwargs)
            return original_recall(query, **kwargs)

        def context(query, **kwargs):
            if query == "first":
                return snapshot_gate(query, **kwargs)
            return original_context(query, **kwargs)

        monkeypatch.setattr(runtime.memory, "_recall", recall)
        monkeypatch.setattr(runtime.memory, "recall_context", context)
        first = asyncio.create_task(runtime.start_turn(first_sid, "first"))
        second = None
        try:
            await snapshot_gate.ready()
            second = asyncio.create_task(runtime.start_turn(second_sid, "second"))
            await locked_gate.ready()
            snapshot_gate.release.set()
            accepted = await asyncio.wait_for(first, 2)
            assert accepted["status"] == "accepted"
            assert not second.done() and not locked_gate.release.is_set()
            locked_gate.release.set()
            await second
        finally:
            snapshot_gate.release.set()
            locked_gate.release.set()
            if second:
                await asyncio.gather(second, return_exceptions=True)
            await runtime.close()

    asyncio.run(run())


def test_api_memory_operations_do_not_block_cancel_while_recall_holds_lock(tmp_path, monkeypatch):
    from test_memory_backup_api import api_client

    async def run():
        runtime = SessionRuntime(initialize_data_root(tmp_path / "api-lock"), Store())
        sid, rid = runtime.create_session()["id"], uuid4().hex
        gate = WorkerGate(runtime.memory._recall)
        monkeypatch.setattr(runtime.memory, "_recall", gate)
        entered = asyncio.Queue()
        original = runtime.memory_api

        async def memory_api(function, *args, **kwargs):
            entered.put_nowait(function.__name__)
            return await original(function, *args, **kwargs)

        monkeypatch.setattr(runtime, "memory_api", memory_api)
        async with api_client(runtime) as client:
            pending = asyncio.create_task(
                client.post(
                    f"/api/sessions/{sid}/turns",
                    json={"text": "slow recall", "request_id": rid},
                )
            )
            persona = remember = None
            try:
                await gate.ready()
                persona = asyncio.create_task(client.get("/api/persona"))
                assert await asyncio.wait_for(entered.get(), 2) == "get_persona"
                remember = asyncio.create_task(
                    client.post("/api/memories", json={"content": "明确保存的新事实"})
                )
                assert await asyncio.wait_for(entered.get(), 2) == "remember"
                cancellation = await asyncio.wait_for(
                    client.post(f"/api/sessions/{sid}/requests/{rid}/cancel", json={}), 2
                )
                assert cancellation.status_code == 200
                assert cancellation.json()["status"] == "cancelled"
                assert not gate.release.is_set()
                gate.release.set()
                assert (await pending).status_code == 409
                assert (await persona).status_code in {200, 409}
                assert (await remember).status_code == 201
                assert runtime.providers.adapter.messages == []
            finally:
                gate.release.set()
                await asyncio.gather(
                    *(task for task in (pending, persona, remember) if task is not None),
                    return_exceptions=True,
                )
                await runtime.close()

    asyncio.run(run())


def test_raw_user_history_dependency_is_erased_without_assistant_ack(tmp_path):
    async def run():
        paths = initialize_data_root(tmp_path / "user-history")
        runtime = SessionRuntime(paths, Store(Model(["未确认的回复"])))
        sid = runtime.create_session()["id"]
        raw = "我的私密饮品偏好是柚子汽水"
        first = (await runtime.start_turn(sid, raw))["id"]
        await settled(runtime, sid, first)  # Never poll/ACK assistant content.
        fact = runtime.memory.remember(raw, source_id="turn:" + first, source_text=raw)
        runtime.providers.adapter = Model(["那款酸甜饮品适合作为偶尔的选择。"])
        second = await deliver(runtime, sid, "换种说法描述一下")
        assert not runtime._db.execute(
            "SELECT 1 FROM turn_history WHERE turn_id=? AND source_turn_id=?", (second, first)
        ).fetchone()
        assert runtime._db.execute(
            "SELECT 1 FROM turn_user_history WHERE turn_id=? AND source_turn_id=?", (second, first)
        ).fetchone()
        await runtime.close()
        runtime = SessionRuntime(paths, Store())
        await runtime.forget_memory(fact["id"])
        assert all(t["input"] == "[已遗忘的对话]" for t in runtime.get_session(sid)["turns"])
        await runtime.close()

    asyncio.run(run())


def test_forgotten_placeholder_cannot_taint_new_turns_or_other_erasure(tmp_path):
    async def run():
        runtime = SessionRuntime(initialize_data_root(tmp_path / "placeholder"), Store())
        sid = runtime.create_session()["id"]
        fact = runtime.memory.remember("喜欢柚子汽水", source_id="manual:first", kind="preference")
        first = await deliver(runtime, sid, "我喜欢什么")
        await runtime.forget_memory(fact["id"])
        model = Model(["太阳系有八颗行星。"])
        runtime.providers.adapter = model
        second = await deliver(runtime, sid, "太阳系有几大行星")
        assert "已遗忘的对话" not in json.dumps(model.messages, ensure_ascii=False)
        for table in ("turn_history", "turn_user_history"):
            assert not runtime._db.execute(
                f"SELECT 1 FROM {table} WHERE turn_id=? AND source_turn_id=?", (second, first)
            ).fetchone()
        unrelated = runtime.memory.remember("喜欢星空蓝", source_id="manual:unrelated")
        await runtime.forget_memory(unrelated["id"])
        turns = runtime.get_session(sid)["turns"]
        assert turns[1]["input"] == "太阳系有几大行星"
        assert turns[1]["confirmed_text"] == "太阳系有八颗行星。"
        await runtime.close()

    asyncio.run(run())


@pytest.mark.parametrize("failure", [False, True])
def test_erasure_connections_close_on_success_and_checkpoint_failure(
    tmp_path, monkeypatch, failure
):
    import ai_neko.runtime.service as service

    async def run():
        runtime = SessionRuntime(initialize_data_root(tmp_path / "connections"), Store())
        sid = runtime.create_session()["id"]
        fact = runtime.memory.remember("喜欢柚子汽水", source_id="manual:one", kind="preference")
        await deliver(runtime, sid, "我喜欢什么")
        opened = []
        original_connect = sqlite3.connect

        class TrackedConnection(sqlite3.Connection):
            checkpoint = False

            def execute(self, sql, *args, **kwargs):
                if failure and self.checkpoint and sql == "PRAGMA secure_delete=ON":
                    raise OSError("synthetic checkpoint failure")
                return super().execute(sql, *args, **kwargs)

        def connect(path, *args, **kwargs):
            # Inspect physical closure from the test's event-loop thread.
            kwargs["check_same_thread"] = False
            connection = original_connect(path, *args, factory=TrackedConnection, **kwargs)
            connection.checkpoint = str(path).endswith("chat-graph.sqlite")
            opened.append(connection)
            return connection

        monkeypatch.setattr(service.sqlite3, "connect", connect)
        try:
            if failure:
                with pytest.raises(OSError, match="synthetic checkpoint failure"):
                    await runtime.forget_memory(fact["id"])
            else:
                await runtime.forget_memory(fact["id"])
            assert len(opened) == 2
            for connection in opened:
                with pytest.raises(sqlite3.ProgrammingError, match="closed"):
                    connection.execute("SELECT 1")
        finally:
            await runtime.close()

    asyncio.run(run())


@pytest.mark.parametrize("crash_after_memory_commit", [False, True])
def test_shared_source_cascade_preserves_raw_history_role_across_crash(
    tmp_path, monkeypatch, crash_after_memory_commit
):
    async def run():
        paths = initialize_data_root(tmp_path / "shared-source-role")
        runtime = SessionRuntime(paths, Store(Model(["未投递的助手回复"])))
        sid = runtime.create_session()["id"]
        raw = "我想描述一次荔枝饮料体验。" * 180  # >2,000; not a literal evidence needle.
        first = (await runtime.start_turn(sid, raw))["id"]
        await settled(runtime, sid, first)
        a = runtime.memory.remember(
            "抽象标签甲", source_id="manual:shared", source_text="共同原始证据"
        )
        b = runtime.memory.remember(
            "抽象标签乙", source_id="manual:shared", source_text="共同原始证据"
        )
        linked = runtime.memory.remember("抽象标签乙", source_id="turn:" + first, source_text=raw)
        assert linked["id"] == b["id"]
        runtime.providers.adapter = Model(["那种果香口味可以偶尔尝试。"])
        second = await deliver(runtime, sid, "继续刚才的话题")
        assert not runtime._db.execute(
            "SELECT 1 FROM turn_memory WHERE turn_id=?", (second,)
        ).fetchone()
        assert not runtime._db.execute(
            "SELECT 1 FROM turn_history WHERE turn_id=? AND source_turn_id=?", (second, first)
        ).fetchone()
        assert runtime._db.execute(
            "SELECT 1 FROM turn_user_history WHERE turn_id=? AND source_turn_id=?", (second, first)
        ).fetchone()
        if crash_after_memory_commit:
            original = runtime.memory.forget_source

            def fail_after_commit(*args, **kwargs):
                # The user-source role must already be durable before Memory
                # erases provenance, even if it was discovered via another fact.
                intent = json.loads(
                    runtime._db.execute("SELECT payload FROM memory_erasure").fetchone()[0]
                )
                assert first in intent["user_turn_ids"]
                original(*args, **kwargs)
                raise OSError("synthetic loss after Memory commit")

            monkeypatch.setattr(runtime.memory, "forget_source", fail_after_commit)
            with pytest.raises(OSError, match="synthetic loss"):
                await runtime.forget_memory(a["id"])
            await runtime.close()
            runtime = SessionRuntime(paths, Store())
        else:
            await runtime.forget_memory(a["id"])
        assert runtime.memory.list_facts() == []
        assert all(turn["input"] == "[已遗忘的对话]" for turn in runtime.get_session(sid)["turns"])
        assert not runtime._db.execute("SELECT 1 FROM memory_erasure").fetchone()
        await runtime.close()

    asyncio.run(run())
