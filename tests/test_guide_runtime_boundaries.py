"""Guide cleanup and job settlement races against the actual scoped Runtime.

Only model/network results and explicit I/O failure/worker gates are synthetic.
The product Runtime, GuideStore, Memory Service and checkpoint files remain real.
"""

import asyncio
import json
import sqlite3
import threading
from uuid import uuid4

import pytest
from test_chat import ScriptWeb, result
from test_guide_runtime import NEW, OLD, URL, ResearchStore, page, remove, selection, turn
from test_runtime import Model, Store

from ai_neko.config.paths import initialize_data_root
from ai_neko.memory import guides
from ai_neko.memory.guides import GuideAccessError, GuideCapacityError
from ai_neko.runtime import SessionRuntime


def fetch_request(runtime, *, url=URL):
    return {
        "request_id": uuid4().hex,
        "expected_revision": runtime.memory.guides.revision(),
        "url": url,
    }


@pytest.mark.parametrize("action", ["delete", "restore"])
def test_guide_cleanup_preserves_user_preference_from_same_research_turn(tmp_path, action):
    async def run():
        runtime = SessionRuntime(initialize_data_root(tmp_path / action), ResearchStore())
        try:
            doc = runtime.memory.guides.ingest(page())
            snapshot = await runtime.backup_guides({"request_id": uuid4().hex})
            sid = runtime.create_session()["id"]
            question = "我喜欢合成红茶，请读取这份攻略。"
            tid = await turn(runtime, sid, question, research=True)
            preference = runtime.memory.remember(
                "喜欢合成红茶", source_id="turn:" + tid, source_text=question, kind="preference"
            )
            assert OLD in runtime.get_session(sid)["turns"][0]["confirmed_text"]
            if action == "delete":
                await remove(runtime, doc)
            else:
                await runtime.restore_guides(
                    snapshot["backup_id"],
                    {
                        "request_id": uuid4().hex,
                        "expected_revision": runtime.memory.guides.revision(),
                        "confirm": True,
                    },
                )
            facts = runtime.memory.list_facts()
            assert [(fact["id"], fact["content"]) for fact in facts] == [
                (preference["id"], "喜欢合成红茶")
            ]
            sources = runtime.memory.sources(preference["id"])
            assert any(source["source_id"] == "turn:" + tid for source in sources)
            row = runtime.get_session(sid)["turns"][0]
            assert row["input"] == question
            assert OLD not in json.dumps(row, ensure_ascii=False)
            assert row["confirmed_text"] == row["heard_text"] == ""
            runtime.providers.adapter = Model(["你喜欢合成红茶。"])
            await turn(runtime, sid, "我喜欢什么茶？")
            sent = json.dumps(runtime.providers.adapter.messages, ensure_ascii=False)
            assert "喜欢合成红茶" in sent
            assert OLD not in sent
        finally:
            await runtime.close()

    asyncio.run(run())


def test_cleanup_intent_write_failure_leaves_live_state_retryable(tmp_path, monkeypatch):
    async def run():
        runtime = SessionRuntime(initialize_data_root(tmp_path / "intent-failure"), Store())
        try:
            document = runtime.memory.guides.ingest(page())
            request = selection(runtime, document)
            before_revision = runtime.memory.guides.revision()

            def fail_before_intent(*_args):
                raise OSError("synthetic intent write failure")

            with monkeypatch.context() as failure:
                failure.setattr(runtime, "_write_guide_intent", fail_before_intent)
                with pytest.raises(OSError, match="synthetic intent write failure"):
                    await runtime.guide_selection(request)
            assert not runtime._db.execute("SELECT 1 FROM memory_erasure").fetchone()
            assert not runtime._memory_mutating
            assert not runtime._erasure_failed
            assert runtime.memory.guides.revision() == before_revision
            assert runtime.memory.guides.control_snapshot()["selections"] == []
            assert len((await runtime.guide_catalog())["guides"]) == 1
            retried = await runtime.guide_selection(request)
            assert retried["selection"]["guide_id"] == document["guide_id"]
            assert retried["revision"] > before_revision
            assert runtime.create_session()["id"]
        finally:
            await runtime.close()

    asyncio.run(run())


def test_delete_erases_unsaved_new_revision_and_derived_reply_but_keeps_user_words(
    tmp_path, monkeypatch
):
    marker = "UNSAVED_NEW_REVISION_BODY：第九回合使用合成银月石。"
    derived = "DERIVED_UNSAVED_ADVICE：所以你现在先拿银月石。"

    async def run():
        paths = initialize_data_root(tmp_path / "unsaved-revision")
        runtime = SessionRuntime(paths, ResearchStore(body=marker))
        try:
            old = runtime.memory.guides.ingest(page("合成旧正文：优先使用珊瑚石。"))
            sid = runtime.create_session()["id"]
            first_question = "请读取这份攻略的新版本。"
            with monkeypatch.context() as capacity:
                capacity.setattr(guides, "MAX_STORAGE_BYTES", runtime.memory.guides.logical_bytes())
                first = await turn(runtime, sid, first_question, research=True)
            first_events = runtime.events(sid, first)["events"]
            source_event = next(event for event in first_events if event["type"] == "source")
            assert source_event["source"]["storage"]["saved"] is False
            assert source_event["source"]["storage"]["error"] == "guide_capacity_exceeded"
            assert marker in source_event["source"]["text"]
            assert marker in runtime.get_session(sid)["turns"][0]["confirmed_text"]
            assert marker not in runtime.memory.guides.get_document(old["guide_id"])["text"]
            runtime.providers.adapter = Model([derived])
            second_question = "接下来我该做什么？"
            second = await turn(runtime, sid, second_question)
            assert marker in json.dumps(runtime.providers.adapter.messages, ensure_ascii=False)
            assert derived in runtime.get_session(sid)["turns"][1]["confirmed_text"]

            await remove(runtime, old)
            rows = runtime.get_session(sid)["turns"]
            assert [row["input"] for row in rows] == [first_question, second_question]
            for tid in (first, second):
                assert runtime.events(sid, tid)["events"] == []
                assert not runtime._db.execute(
                    "SELECT 1 FROM events WHERE turn_id=?", (tid,)
                ).fetchone()
            for row in rows:
                assert marker not in json.dumps(row, ensure_ascii=False)
                assert derived not in json.dumps(row, ensure_ascii=False)
                assert row["confirmed_text"] == row["heard_text"] == ""
            with sqlite3.connect(paths.checkpoints / "chat-graph.sqlite") as database:
                for table in ("checkpoints", "writes"):
                    threads = {row[0] for row in database.execute(f"SELECT thread_id FROM {table}")}
                    assert not any(
                        thread.endswith(":" + tid) for thread in threads for tid in (first, second)
                    )
            runtime.providers.adapter = Model(["请提供新的可用资料。"])
            await turn(runtime, sid, "还有依据吗？")
            sent = json.dumps(runtime.providers.adapter.messages, ensure_ascii=False)
            assert marker not in sent and derived not in sent
        finally:
            await runtime.close()
        for path in (
            paths.memory / "conversation.sqlite",
            paths.checkpoints / "chat-graph.sqlite",
            paths.guides / "guides.sqlite",
        ):
            assert marker.encode() not in path.read_bytes()
            assert derived.encode() not in path.read_bytes()

    asyncio.run(run())


def test_fetch_cancel_waits_for_inflight_sqlite_commit_and_reports_saved_result(
    tmp_path, monkeypatch
):
    async def run():
        entered, release = threading.Event(), threading.Event()
        runtime = SessionRuntime(
            initialize_data_root(tmp_path / "commit-cancel"),
            ResearchStore(web=ScriptWeb([result(page(NEW))])),
        )
        original_ingest = runtime.memory.guides.ingest
        cancellation = None
        worker = None

        def blocked_ingest(*args, **kwargs):
            entered.set()
            if not release.wait(5):
                raise TimeoutError("synthetic ingest worker was not released")
            return original_ingest(*args, **kwargs)

        monkeypatch.setattr(runtime.memory.guides, "ingest", blocked_ingest)
        try:
            request = fetch_request(runtime)
            accepted = await runtime.guide_fetch(request)
            assert accepted["status"] == "running"
            worker = runtime._guide_tasks[request["request_id"]]
            assert await asyncio.to_thread(entered.wait, 2)
            cancellation = asyncio.create_task(
                runtime.cancel_guide_operation(request["request_id"])
            )
            done, _ = await asyncio.wait({cancellation}, timeout=0.05)
            assert not done, "cancel returned before the already-started SQLite worker settled"
            assert (await runtime.guide_operation(request["request_id"]))["status"] == "running"
            release.set()
            completed = await asyncio.wait_for(cancellation, 2)
            await asyncio.wait_for(asyncio.shield(worker), 2)
            assert completed["status"] == "completed"
            assert completed["result"]["saved"] is True
            assert completed["error"] is None
            document = runtime.memory.guides.get_document(completed["result"]["guide_id"])
            assert NEW in document["text"]
            assert document["revision_id"] == completed["result"]["revision_id"]
            assert await runtime.guide_operation(request["request_id"]) == completed
            assert len(runtime.memory.guides.list_documents()) == 1
        finally:
            release.set()
            if cancellation is not None:
                await asyncio.gather(cancellation, return_exceptions=True)
            if worker is not None:
                await asyncio.gather(worker, return_exceptions=True)
            await runtime.close()

    asyncio.run(run())


def test_fetch_concurrency_limit_has_no_orphan_and_cancel_releases_slot(tmp_path):
    async def run():
        started = asyncio.Queue()

        class HeldWeb:
            async def execute(self, name, arguments):
                assert name == "read_web_page"
                started.put_nowait(arguments["url"])
                await asyncio.Event().wait()

        runtime = SessionRuntime(
            initialize_data_root(tmp_path / "concurrency"), ResearchStore(web=HeldWeb())
        )
        try:
            requests = [
                fetch_request(runtime, url=f"https://example.com/parallel-{i}") for i in range(5)
            ]
            for request in requests[:4]:
                assert (await runtime.guide_fetch(request))["status"] == "running"
                assert await asyncio.wait_for(started.get(), 2) == request["url"]
            workers = tuple(runtime._guide_tasks.values())
            assert len(workers) == 4
            with pytest.raises(GuideCapacityError):
                await runtime.guide_fetch(requests[4])
            with pytest.raises(GuideAccessError):
                await runtime.guide_operation(requests[4]["request_id"])
            assert len(runtime._guide_tasks) == 4
            assert (
                runtime._db.execute(
                    "SELECT COUNT(*) FROM guide_operations WHERE status='running'"
                ).fetchone()[0]
                == 4
            )
            assert (await runtime.cancel_guide_operation(requests[0]["request_id"]))[
                "status"
            ] == "cancelled"
            await asyncio.gather(workers[0], return_exceptions=True)
            assert requests[0]["request_id"] not in runtime._guide_tasks
            assert (await runtime.guide_fetch(requests[4]))["status"] == "running"
            assert await asyncio.wait_for(started.get(), 2) == requests[4]["url"]
            assert len(runtime._guide_tasks) == 4
            assert runtime.memory.guides.list_documents() == []
        finally:
            await runtime.close()

    asyncio.run(run())


def test_cancelling_scheduled_fetch_before_coroutine_starts_releases_capacity(tmp_path):
    async def run():
        web = ScriptWeb([])
        runtime = SessionRuntime(
            initialize_data_root(tmp_path / "prestart-cancel"), ResearchStore(web=web)
        )
        try:
            # No await between the returned acceptance and the synchronous owned
            # cancel helper: this hits Task.cancel() before its coroutine enters
            # try/finally, as shutdown can do to a just-created task.
            for index in range(6):
                request = fetch_request(runtime, url=f"https://example.com/not-started-{index}")
                assert (await runtime.guide_fetch(request))["status"] == "running"
                worker = runtime._guide_tasks[request["request_id"]]
                cancelled = runtime._cancel_guide_operation(request["request_id"])
                assert cancelled["status"] == "cancelled"
                await asyncio.gather(worker, return_exceptions=True)
                assert request["request_id"] not in runtime._guide_tasks
                assert await runtime.guide_operation(request["request_id"]) == cancelled
            assert web.calls == []
            assert runtime._guide_tasks == {}
            assert not runtime._db.execute(
                "SELECT 1 FROM guide_operations WHERE status='running'"
            ).fetchone()
            assert runtime.memory.guides.list_documents() == []
        finally:
            await runtime.close()

    asyncio.run(run())
