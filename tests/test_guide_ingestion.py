"""G1 product graph/runtime evidence; providers and transport are synthetic."""

import asyncio
import json
import sqlite3
import subprocess
import sys
import threading

import httpx
import pytest
from test_chat import ScriptModel, ScriptWeb, call, result, run_graph, source, text
from test_providers import BytesStream
from test_runtime import settled

from ai_neko.config.paths import initialize_data_root
from ai_neko.memory import MemoryService
from ai_neko.memory.guides import GuideCapacityError
from ai_neko.runtime import SessionRuntime
from ai_neko.tools import network
from ai_neko.tools.web import WebTools

URL = "https://example.com/guide?edition=2"
TAIL = "后半段独有规则：月石在第七回合合成护盾，不得用作刷新。"


def test_answer_budget_reports_its_own_truncation():
    model = ScriptModel([[call("read_web_page", url=URL)], [], [text("依据资料。[S1]")]])
    web = ScriptWeb([result(source(url=URL, body="范围测试文本" * 1000))])
    _, events, _ = run_graph(model, web)
    emitted = next(e["source"] for e in events if e["type"] == "source")
    assert len(emitted["text"]) == 6000 and emitted["prompt_truncated"] is False
    final_evidence = json.loads(model.calls[-1][0][-1]["content"].split("\n", 1)[1])["sources"][0]
    assert len(final_evidence["text"]) == 4000
    assert final_evidence["prompt_truncated"] is True


class Providers:
    def __init__(self, web):
        self.web = web
        self.adapter = ScriptModel(
            [[call("read_web_page", url=URL)], [], [text("依据已读取的攻略。[S1]")]]
        )

    def model(self):
        return self.adapter

    def web_tools(self):
        return self.web


def test_real_read_graph_saves_before_prompt_crop_and_new_process_can_read(tmp_path, monkeypatch):
    body = "# 开局\n" + "这一段是长攻略的普通说明。\n" * 1700 + "\n# 后半段\n" + TAIL
    assert 20000 < body.index(TAIL) < 50000
    requests = []

    async def pin(url):
        return httpx.URL(url), {}, {}

    def respond(request):
        requests.append(str(request.url))
        return httpx.Response(
            200,
            stream=BytesStream([body.encode()]),
            headers={"content-type": "text/plain; charset=utf-8"},
        )

    monkeypatch.setattr(network, "pin_url", pin)
    monkeypatch.setattr(
        network, "client", lambda: httpx.AsyncClient(transport=httpx.MockTransport(respond))
    )
    paths = initialize_data_root(tmp_path / "guide-product")

    async def run():
        providers = Providers(WebTools({}, None))
        runtime = SessionRuntime(paths, providers)
        sid = runtime.create_session()["id"]
        tid = (await runtime.start_turn(sid, "读取这份攻略", guide=True))["id"]
        await settled(runtime, sid, tid)
        assert runtime.get_session(sid)["turns"][-1]["status"] == "completed"
        sources = [e["source"] for e in runtime.events(sid, tid)["events"] if e["type"] == "source"]
        assert len(sources) == 1
        event = sources[0]
        assert event["storage"]["saved"] is True
        assert event["prompt_truncated"] is True
        assert len(event["text"]) == 6000 and TAIL not in event["text"]
        stored = runtime.memory.guides.get_document(event["storage"]["guide_id"])
        assert stored["text"] == body
        assert stored["game"] == "" and stored["game_version"] is None
        assert TAIL in "".join(
            c["text"] for c in runtime.memory.guides.chunks(stored["revision_id"])
        )
        assert not runtime.memory.list_facts()
        assert TAIL not in json.dumps(providers.adapter.calls, ensure_ascii=False)
        await runtime.close()
        return stored["guide_id"], stored["revision_id"]

    guide_id, revision_id = asyncio.run(run())
    assert requests == [URL]
    # A new Python process opens the actual Memory Service owned guide store.
    program = """
import json,sys
from ai_neko.config.paths import initialize_data_root
from ai_neko.memory import MemoryService
with MemoryService(initialize_data_root(sys.argv[1])) as memory:
    doc=memory.guides.get_document(sys.argv[2])
    print(json.dumps({'revision_id':doc['revision_id'],'text':doc['text']},ensure_ascii=False))
"""
    reopened = subprocess.run(
        [sys.executable, "-c", program, str(paths.root), guide_id],
        text=True,
        capture_output=True,
        check=True,
        timeout=30,
    )
    persisted = json.loads(reopened.stdout)
    assert persisted["revision_id"] == revision_id and TAIL in persisted["text"]
    with sqlite3.connect(paths.memory / "long-term.sqlite") as db:
        assert TAIL not in "\n".join(db.iterdump())


@pytest.mark.parametrize("error", [GuideCapacityError("quota"), sqlite3.OperationalError("SECRET")])
def test_write_failure_preserves_read_answer_and_reports_not_saved(tmp_path, monkeypatch, error):
    async def run():
        web = ScriptWeb([result(source(url=URL, body="公开攻略正文。" * 30))])
        providers = Providers(web)
        runtime = SessionRuntime(initialize_data_root(tmp_path / "write-failure"), providers)

        def fail(*args, **kwargs):
            raise error

        monkeypatch.setattr(runtime.memory.guides, "ingest", fail)
        sid = runtime.create_session()["id"]
        tid = (await runtime.start_turn(sid, "读取攻略", guide=True))["id"]
        await settled(runtime, sid, tid)
        events = runtime.events(sid, tid)
        assert events["status"] == "completed"
        evidence = next(e["source"] for e in events["events"] if e["type"] == "source")
        assert evidence["status"] == "read"
        assert evidence["storage"]["saved"] is False
        assert "SECRET" not in json.dumps(events)
        assert not runtime.memory.guides.list_documents()
        await runtime.close()

    asyncio.run(run())


def test_late_fetch_after_cancellation_never_saves_or_calls_answer_model(tmp_path):
    async def run():
        entered = asyncio.Event()

        class LateWeb:
            async def execute(self, *_):
                entered.set()
                try:
                    await asyncio.Event().wait()
                except asyncio.CancelledError:
                    return result(source(url=URL, body="迟到正文不能保存。" * 10))

        providers = Providers(LateWeb())
        runtime = SessionRuntime(initialize_data_root(tmp_path / "late"), providers)
        sid = runtime.create_session()["id"]
        tid = (await runtime.start_turn(sid, "读取攻略", guide=True))["id"]
        await asyncio.wait_for(entered.wait(), 2)
        task = runtime._tasks[tid]
        await runtime.cancel_turn(sid, tid)
        await asyncio.gather(task, return_exceptions=True)
        assert not runtime.memory.guides.list_documents()
        assert len(providers.adapter.calls) == 1
        await runtime.close()

    asyncio.run(run())


@pytest.mark.parametrize("write_fails", [False, True])
def test_close_waits_for_physical_guide_write_and_suppresses_late_events(
    tmp_path, monkeypatch, write_fails
):
    async def run():
        entered, release = threading.Event(), threading.Event()
        providers = Providers(ScriptWeb([result(source(url=URL, body="公开正文。" * 30))]))
        paths = initialize_data_root(tmp_path / "closing")
        runtime = SessionRuntime(paths, providers)
        ingest = runtime.memory.guides.ingest

        def slow(source, **kwargs):
            entered.set()
            assert release.wait(5)
            if write_fails:
                raise sqlite3.OperationalError("synthetic write failure after cancellation")
            return ingest({**source, "completeness": "full"}, **kwargs)

        monkeypatch.setattr(runtime.memory.guides, "ingest", slow)
        sid = runtime.create_session()["id"]
        tid = (await runtime.start_turn(sid, "读取攻略", guide=True))["id"]
        assert await asyncio.to_thread(entered.wait, 2)
        closing = asyncio.create_task(runtime.close())
        await asyncio.sleep(0.03)
        assert not closing.done() and not runtime._closed
        release.set()
        await asyncio.wait_for(closing, 3)
        assert not runtime._memory_io and len(providers.adapter.calls) == 1
        reopened = SessionRuntime(paths, providers)
        assert len(reopened.memory.guides.list_documents()) == (0 if write_fails else 1)
        events = reopened.events(sid, tid)["events"]
        assert not any(e["type"] == "source" for e in events)
        await reopened.close()

    asyncio.run(run())


def test_personal_snapshot_never_contains_or_replaces_external_guides(tmp_path):
    paths = initialize_data_root(tmp_path / "snapshot-boundary")
    with MemoryService(paths) as memory:
        memory.remember("喜欢合成测试汽水", source_id="manual:synthetic")
        snapshot = memory.backup()
        guide = memory.guides.ingest({**source(url=URL, body=TAIL * 10), "completeness": "full"})
        memory.restore(snapshot["id"])
        assert memory.guides.get_document(guide["guide_id"])["text"] == TAIL * 10
        with sqlite3.connect(memory._backup_path(snapshot["id"])) as db:
            assert TAIL not in "\n".join(db.iterdump())
