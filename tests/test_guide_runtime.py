"""G2 real runtime/history/recovery checks with deterministic model/web adapters."""

import asyncio
import json
import sqlite3
from uuid import uuid4

import pytest
from test_chat import ScriptModel, ScriptWeb, call, result, text
from test_runtime import Model, Store, settled

from ai_neko.config.paths import initialize_data_root
from ai_neko.memory.guides import GuideAccessError, GuideConflictError
from ai_neko.runtime import RuntimeConflictError, SessionRuntime
from ai_neko.tools.web import source

OLD = "旧攻略建议：合成琥珀灯前绝不能使用风石。SYNTHETIC_OLD_GUIDE"
NEW = "新攻略建议：先收集潮汐石，再打开试炼。SYNTHETIC_NEW_GUIDE"
URL = "https://example.com/first?edition=1"


def page(body=OLD, url=URL):
    return source(url, status="read", title="合成攻略", text=body * 3)


class ResearchStore(Store):
    def __init__(self, body=OLD, web=None):
        super().__init__(ScriptModel([[call("read_web_page", url=URL)], [], [text(body + "[S1]")]]))
        self.web = web or ScriptWeb([result(page(body))])

    def web_tools(self):
        return self.web


def selection(runtime, doc=None):
    return {
        "request_id": uuid4().hex,
        "expected_revision": runtime.memory.guides.revision(),
        "game": "synthetic",
        "platform": "pc",
        "mode": "ranked",
        "guide_id": doc["guide_id"] if doc else None,
        "revision_id": doc["revision_id"] if doc else None,
    }


async def turn(runtime, sid, question, *, research=False, ack=True):
    tid = (await runtime.start_turn(sid, question, guide=research))["id"]
    await settled(runtime, sid, tid)
    events = runtime.events(sid, tid)
    assert events["status"] == "completed"
    if ack:
        runtime.ack(sid, tid, events["sent_seq"])
    return tid


async def remove(runtime, doc):
    return await runtime.guide_delete(
        doc["guide_id"],
        {
            "request_id": uuid4().hex,
            "expected_revision": runtime.memory.guides.revision(),
            "confirm": True,
        },
    )


def test_switch_removes_old_derived_advice_from_model_but_keeps_visible_history(tmp_path):
    async def run():
        runtime = SessionRuntime(initialize_data_root(tmp_path / "switch"), ResearchStore())
        a = runtime.memory.guides.ingest(page())
        b = runtime.memory.guides.ingest(page(NEW, "https://example.com/second"))
        await runtime.guide_selection(selection(runtime, a))
        sid = runtime.create_session()["id"]
        first = await turn(runtime, sid, "读取合成资料", research=True)
        runtime.providers.adapter = Model(["派生后续建议：仍然不要使用风石。"])
        second = await turn(runtime, sid, "下一步呢")
        assert OLD in json.dumps(runtime.providers.adapter.messages, ensure_ascii=False)
        assert runtime._db.execute(
            "SELECT 1 FROM turn_guides WHERE turn_id=? AND guide_id=?", (second, a["guide_id"])
        ).fetchone()
        await runtime.guide_selection(selection(runtime, b))
        history = runtime.get_session(sid)["turns"]
        assert OLD in history[0]["delivered_text"]
        runtime.providers.adapter = Model(["新的回答"])
        await turn(runtime, sid, "现在应该怎么做？")
        sent = json.dumps(runtime.providers.adapter.messages, ensure_ascii=False)
        assert OLD not in sent and "派生后续建议" not in sent
        assert "读取合成资料" in sent and "下一步呢" in sent
        assert (
            runtime.memory.guides.control_snapshot()["selections"][0]["guide_id"] == b["guide_id"]
        )
        assert (
            runtime._db.execute(
                "SELECT invalidated FROM turn_guides WHERE turn_id=?", (first,)
            ).fetchone()[0]
            == 1
        )
        await runtime.close()

    asyncio.run(run())


def test_delete_preserves_unrelated_same_session_history_and_checkpoint(tmp_path):
    async def run():
        paths = initialize_data_root(tmp_path / "delete")
        runtime = SessionRuntime(paths, ResearchStore())
        sid = runtime.create_session()["id"]
        first = await turn(runtime, sid, "读取攻略", research=True, ack=False)
        doc = runtime.memory.guides.list_documents()[0]
        runtime.providers.adapter = Model(["无关天气问候"])
        other = await turn(runtime, sid, "普通问候")
        assert not runtime._db.execute(
            "SELECT 1 FROM turn_guides WHERE turn_id=?", (other,)
        ).fetchone()
        snapshot = await runtime.backup_guides({"request_id": uuid4().hex})
        await remove(runtime, doc)
        rows = runtime.get_session(sid)["turns"]
        assert rows[0]["input"] == "读取攻略" and not rows[0]["delivered_text"]
        assert rows[1]["input"] == "普通问候" and "无关天气" in rows[1]["delivered_text"]
        assert not (paths.backups / snapshot["backup_id"]).exists()
        with sqlite3.connect(paths.checkpoints / "chat-graph.sqlite") as db:
            threads = {r[0] for r in db.execute("SELECT thread_id FROM checkpoints")}
            assert any(t.endswith(":" + other) for t in threads)
            assert not any(t.endswith(":" + first) for t in threads)
        await runtime.close()
        for path in [
            paths.memory / "conversation.sqlite",
            paths.checkpoints / "chat-graph.sqlite",
            paths.guides / "guides.sqlite",
        ]:
            assert OLD.encode() not in path.read_bytes()
        reopened = SessionRuntime(paths, Store())
        with pytest.raises(GuideConflictError):
            reopened.memory.guides.ingest(page())
        assert reopened.get_session(sid)["turns"][1]["input"] == "普通问候"
        await reopened.close()

    asyncio.run(run())


def test_delete_recovers_after_store_commit_and_before_history_cleanup(tmp_path, monkeypatch):
    async def run():
        paths = initialize_data_root(tmp_path / "recovery")
        runtime = SessionRuntime(paths, ResearchStore())
        sid = runtime.create_session()["id"]
        await turn(runtime, sid, "合成资料", research=True)
        doc = runtime.memory.guides.list_documents()[0]
        payload = {"request_id": uuid4().hex, "expected_revision": 0, "confirm": True}
        original = runtime._erase_history

        def failure(*args):
            raise OSError("synthetic cleanup interruption")

        monkeypatch.setattr(runtime, "_erase_history", failure)
        with pytest.raises(OSError):
            await runtime.guide_delete(doc["guide_id"], payload)
        intent = json.loads(runtime._db.execute("SELECT payload FROM memory_erasure").fetchone()[0])
        assert intent["kind"] == "guide" and intent["guide_ids"] == [doc["guide_id"]]
        assert intent["committed_result"]["revision"] == 1
        assert not runtime.memory.guides.list_documents()
        with pytest.raises(RuntimeConflictError):
            runtime.get_session(sid)
        monkeypatch.setattr(runtime, "_erase_history", original)
        await runtime.close()
        reopened = SessionRuntime(paths, Store())
        assert not reopened._db.execute("SELECT 1 FROM memory_erasure").fetchone()
        assert reopened.get_session(sid)["turns"][0]["input"] == "合成资料"
        assert not reopened.get_session(sid)["turns"][0]["delivered_text"]
        retry = await reopened.guide_delete(doc["guide_id"], payload)
        assert retry["replayed"] and reopened.memory.guides.revision() == 1
        await reopened.close()

    asyncio.run(run())


def test_deleted_guide_late_fetch_does_not_persist_or_call_answer(tmp_path):
    async def run():
        entered = asyncio.Event()

        class LateWeb:
            async def execute(self, *_):
                entered.set()
                try:
                    await asyncio.Event().wait()
                except asyncio.CancelledError:
                    return result(page())

        store = ResearchStore(web=LateWeb())
        runtime = SessionRuntime(initialize_data_root(tmp_path / "late-delete"), store)
        doc = runtime.memory.guides.ingest(page())
        sid = runtime.create_session()["id"]
        tid = (await runtime.start_turn(sid, "读取合成资料", guide=True))["id"]
        await asyncio.wait_for(entered.wait(), 2)
        await remove(runtime, doc)
        assert len(store.adapter.calls) == 1
        assert runtime.get_session(sid)["turns"][0]["status"] == "cancelled"
        assert not runtime.memory.guides.list_documents()
        assert not any(e["type"] == "source" for e in runtime.events(sid, tid)["events"])
        await runtime.close()

    asyncio.run(run())


def test_restore_replaces_guide_context_but_keeps_personal_memory(tmp_path):
    async def run():
        runtime = SessionRuntime(initialize_data_root(tmp_path / "restore"), ResearchStore())
        doc = runtime.memory.guides.ingest(page())
        runtime.memory.remember("喜欢合成柚子汽水", source_id="manual:independent")
        await runtime.guide_selection(selection(runtime, doc))
        backup = await runtime.backup_guides({"request_id": uuid4().hex})
        sid = runtime.create_session()["id"]
        await turn(runtime, sid, "请读攻略", research=True)
        later = runtime.memory.guides.ingest(page(NEW, "https://example.com/later"))
        await runtime.guide_selection(selection(runtime, later))
        payload = {
            "request_id": uuid4().hex,
            "expected_revision": runtime.memory.guides.revision(),
            "confirm": True,
        }
        restored = await runtime.restore_guides(backup["backup_id"], payload)
        assert restored["revision"] > payload["expected_revision"]
        assert (
            runtime.memory.guides.control_snapshot()["selections"][0]["guide_id"] == doc["guide_id"]
        )
        assert runtime.get_session(sid)["turns"][0]["input"] == "请读攻略"
        assert not runtime.get_session(sid)["turns"][0]["delivered_text"]
        assert runtime.memory.list_facts()[0]["content"] == "喜欢合成柚子汽水"
        assert (await runtime.restore_guides(backup["backup_id"], payload))["replayed"]
        await runtime.close()

    asyncio.run(run())


def test_invalid_control_does_not_write_cleanup_intent(tmp_path):
    async def run():
        runtime = SessionRuntime(initialize_data_root(tmp_path / "invalid"), Store())
        with pytest.raises(GuideAccessError):
            await runtime.guide_delete(
                "guide-" + "a" * 32, {"expected_revision": 0, "request_id": uuid4().hex}
            )
        assert not runtime._db.execute("SELECT 1 FROM memory_erasure").fetchone()
        assert not runtime._memory_mutating
        await runtime.close()

    asyncio.run(run())


def test_delete_recovers_after_events_erased_before_checkpoint_cleanup(tmp_path, monkeypatch):
    async def run():
        paths = initialize_data_root(tmp_path / "checkpoint-recovery")
        runtime = SessionRuntime(paths, ResearchStore())
        sid = runtime.create_session()["id"]
        tid = await turn(runtime, sid, "读取资料", research=True)
        doc = runtime.memory.guides.list_documents()[0]
        checkpoint = paths.checkpoints / "chat-graph.sqlite"
        original_connect = sqlite3.connect

        def interrupted(path, *args, **kwargs):
            if str(path) == str(checkpoint):
                raise OSError("synthetic checkpoint interruption")
            return original_connect(path, *args, **kwargs)

        monkeypatch.setattr(sqlite3, "connect", interrupted)
        with pytest.raises(OSError):
            await remove(runtime, doc)
        intent = json.loads(runtime._db.execute("SELECT payload FROM memory_erasure").fetchone()[0])
        assert intent["kind"] == "guide" and intent["committed_result"]["revision"] == 1
        assert "turn:" + tid in intent["source_ids"]
        assert not runtime._db.execute("SELECT 1 FROM events WHERE turn_id=?", (tid,)).fetchone()
        assert not runtime._db.execute(
            "SELECT 1 FROM turn_guides WHERE turn_id=?", (tid,)
        ).fetchone()
        monkeypatch.setattr(sqlite3, "connect", original_connect)
        await runtime.close()
        reopened = SessionRuntime(paths, Store())
        assert not reopened._db.execute("SELECT 1 FROM memory_erasure").fetchone()
        assert reopened.get_session(sid)["turns"][0]["input"] == "读取资料"
        with sqlite3.connect(checkpoint) as db:
            assert not db.execute(
                "SELECT 1 FROM checkpoints WHERE thread_id LIKE ?", ("%:" + tid,)
            ).fetchone()
        await reopened.close()

    asyncio.run(run())


def test_fetch_restart_recovers_committed_receipt_without_network_replay(tmp_path):
    async def run():
        paths = initialize_data_root(tmp_path / "fetch-restart")
        runtime = SessionRuntime(paths, ResearchStore())
        value = {"request_id": uuid4().hex, "expected_revision": 0, "url": URL}
        await runtime.guide_fetch(value)
        await asyncio.gather(*tuple(runtime._guide_tasks.values()))
        saved = await runtime.guide_operation(value["request_id"])
        assert saved["status"] == "completed"
        # Model a crash after the authority committed but before the operation
        # journal received the safe completion receipt.
        with runtime._db:
            runtime._db.execute(
                "UPDATE guide_operations SET status='running',result=NULL,guide_id=NULL WHERE request_id=?",
                (value["request_id"],),
            )
        await runtime.close()
        reopened = SessionRuntime(paths, Store())
        restored = await reopened.guide_fetch(value)
        assert restored["status"] == "completed" and restored["result"]["replayed"]
        assert restored["result"]["guide_id"] == saved["result"]["guide_id"]
        assert len(reopened.memory.guides.list_documents()) == 1
        assert not reopened._guide_tasks
        await reopened.close()

    asyncio.run(run())
