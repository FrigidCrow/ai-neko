"""Actual Runtime local-first injection, revalidation and cancellation boundaries."""

import asyncio
import json
import os
import subprocess
import sys
import threading
from datetime import UTC, datetime, timedelta
from pathlib import Path
from uuid import uuid4

import pytest
from test_chat import ScriptModel, ScriptWeb, call, result, text
from test_guide_runtime import OLD, ResearchStore, selection
from test_runtime import Model, Store, settled, text_ready
from test_web_tools import dns as dns
from test_web_tools import reply
from test_web_tools import web_http as web_http

from ai_neko.config.paths import initialize_data_root
from ai_neko.runtime import SessionRuntime
from ai_neko.tools.web import WebTools, source

BODY = "灯芯是放在后排中央的潮灯守卫。\n潮印是完成守卫训练获得的印记。\n雾塔是记录航行见闻的建筑。\n风帆是船上接收气流的装置。\n锚点是雾港记录航向的位置。"
URL = "https://example.com/local-guide"


def saved(runtime, body=BODY, url=URL, *, stale=False, version="2.4"):
    page = source(url, status="read", title="合成公开资料", text=body, etag='"v1"')
    if stale:
        page["retrieved_at"] = (datetime.now(UTC) - timedelta(days=2)).isoformat()
    return runtime.memory.guides.ingest(
        page, game="synthetic", platform="pc", mode="ranked", game_version=version
    )


def evidence(messages):
    return json.loads(
        next(
            message["content"].split("\n", 1)[1]
            for message in reversed(messages)
            if message["role"] == "user" and message["content"].startswith("以下 JSON")
        )
    )


async def ask(runtime, question, *, guide=False, sid=None):
    sid = sid or runtime.create_session()["id"]
    tid = (await runtime.start_turn(sid, question, guide=guide))["id"]
    await settled(runtime, sid, tid)
    events = runtime.events(sid, tid)
    assert events["status"] == "completed", events
    runtime.ack(sid, tid, events["sent_seq"])
    return sid, tid, events


def test_new_process_injects_adopted_guide_for_five_questions_without_search_config(tmp_path):
    data = tmp_path / "process-data"
    env = {
        **os.environ,
        "PYTHONPATH": os.pathsep.join([str(Path.cwd() / "tests"), str(Path.cwd() / "src")]),
    }
    writer = """
import asyncio,sys
from test_guide_local_runtime import saved
from test_guide_runtime import selection
from test_runtime import Store
from ai_neko.runtime import SessionRuntime
from ai_neko.config.paths import initialize_data_root
async def run():
 r=SessionRuntime(initialize_data_root(sys.argv[1]),Store())
 await r.guide_selection(selection(r,saved(r)))
 await r.close()
asyncio.run(run())
"""
    reader = """
import asyncio,json,sys
from test_guide_local_runtime import ask,evidence
from test_runtime import Store,Model
from ai_neko.runtime import SessionRuntime
from ai_neko.config.paths import initialize_data_root
async def run():
 model=Model(['source [S1]'])
 r=SessionRuntime(initialize_data_root(sys.argv[1]),Store(model))
 values=[]
 for q in ['灯芯是什么意思？','潮印是什么意思？','雾塔是什么意思？','风帆是什么意思？','锚点是什么意思？']:
  await ask(r,q,guide=True)
  e=evidence(model.messages[-1]);values.append(e)
 assert len(model.messages)==5
 assert all(x['local_retrieval']['status']=='sufficient' for x in values)
 assert all(x['sources'][0]['local'] for x in values)
 assert len({x['sources'][0]['guide_id'] for x in values})==1
 print(json.dumps({'calls':len(model.messages),'values':values}))
 await r.close()
asyncio.run(run())
"""
    for script in (writer, reader):
        completed = subprocess.run(
            [sys.executable, "-c", script, str(data)],
            env=env,
            capture_output=True,
            text=True,
            timeout=30,
        )
        assert completed.returncode == 0, completed.stderr
    report = json.loads(completed.stdout)
    assert report["calls"] == 5
    assert BODY in report["values"][0]["sources"][0]["text"]


def test_chat_only_local_sources_relabel_each_turn_and_switch_filters_old_answer(tmp_path):
    async def run():
        runtime = SessionRuntime(
            initialize_data_root(tmp_path / "labels"), Store(Model(["OLD_SOURCE_ADVICE [S1]"]))
        )
        old = saved(runtime)
        await runtime.guide_selection(selection(runtime, old))
        sid, _, first = await ask(runtime, "灯芯是什么意思？")
        a = next(event["source"] for event in first["events"] if event["type"] == "source")
        assert a["id"] == "S1" and a["guide_id"] == old["guide_id"]
        new = saved(runtime, "灯芯是另一份资料对红色工匠的称呼。", URL + "/alternate")
        await runtime.guide_selection(selection(runtime, new))
        runtime.providers.adapter = Model(["new [S1]"])
        _, _, second = await ask(runtime, "灯芯是什么意思？", sid=sid)
        b = next(event["source"] for event in second["events"] if event["type"] == "source")
        assert b["id"] == "S1" and b["guide_id"] == new["guide_id"]
        request = json.dumps(runtime.providers.adapter.messages, ensure_ascii=False)
        assert "OLD_SOURCE_ADVICE" not in request and "潮灯守卫" not in request
        assert (
            b["text"]
            == runtime.memory.guides.get_document(new["guide_id"])["text"][b["start"] : b["end"]]
        )
        await runtime.close()

    asyncio.run(run())


def test_local_model_can_request_bounded_supplement_without_leaking_provisional_text(tmp_path):
    async def run():
        model = ScriptModel(
            [
                [
                    call("search_web", query="additional public details"),
                    text("PROVISIONAL_UNSUPPORTED"),
                ],
                [],
                [text("已有本地依据 [S1]。")],
            ]
        )
        provider = Store(model)
        provider.web_tools = lambda: web
        web = ScriptWeb([result()])
        runtime = SessionRuntime(initialize_data_root(tmp_path / "supplement"), provider)
        await runtime.guide_selection(selection(runtime, saved(runtime)))
        _, _, events = await ask(runtime, "灯芯是什么意思？", guide=True)
        assert len(web.calls) == 1 and web.calls[0][0] == "search_web"
        assert len(model.calls) == 3
        assert "PROVISIONAL_UNSUPPORTED" not in json.dumps(events)
        assert any(event.get("text") == "已有本地依据 [S1]。" for event in events["events"])
        await runtime.close()

    asyncio.run(run())


@pytest.mark.parametrize("status", [304, 200, 503])
def test_stale_selected_page_revalidation_keeps_fixed_version(status, tmp_path, web_http):
    requests = []

    def handle(request):
        requests.append(request)
        assert request.headers.get("If-None-Match") == '"v1"'
        return reply(
            "灯芯是红色工匠的新称呼。" * 8 if status == 200 else "",
            status,
            {"content-type": "text/plain", "etag": '"v2"'},
        )

    web_http(handle)

    async def run():
        provider = Store(Model(["原文内容 [S1]。"]))
        provider.web_tools = lambda: WebTools({}, None)
        runtime = SessionRuntime(initialize_data_root(tmp_path / f"check-{status}"), provider)
        old = saved(runtime, stale=True)
        await runtime.guide_selection(selection(runtime, old))
        await ask(runtime, "灯芯是什么意思？", guide=True)
        assert len(requests) == 1
        selected = runtime.memory.guides.control_snapshot()["selections"][0]
        assert selected["revision_id"] == old["revision_id"]
        actual = evidence(provider.adapter.messages[-1])
        check = actual["local_retrieval"]["revalidation"]
        assert (
            check["status"] == {304: "not_modified", 200: "update_available", 503: "failed"}[status]
        )
        document = runtime.memory.guides.get_document(old["guide_id"])
        if status == 304:
            assert len(provider.adapter.messages) == 1
            assert document["revision_id"] == old["revision_id"] and document["text"] == BODY
            assert document["retrieved_at"] == old["retrieved_at"]
            assert document["last_checked_at"] != old["last_checked_at"]
            assert document["game_version"] == "2.4" and document["content_date"] is None
        else:
            assert actual["local_retrieval"]["status"] == "needs_check"
            assert len(provider.adapter.messages) == 2
            if status == 200:
                assert (
                    document["revision_id"] != old["revision_id"]
                    and document["game_version"] is None
                )
            else:
                assert document["revision_id"] == old["revision_id"]
        await runtime.close()

    asyncio.run(run())


def test_explicit_refresh_304_is_idempotent_and_preserves_content_evidence(tmp_path, web_http):
    requests = []
    web_http(lambda request: requests.append(request) or reply("", 304))

    async def run():
        provider = Store()
        provider.web_tools = lambda: WebTools({}, None)
        runtime = SessionRuntime(initialize_data_root(tmp_path / "refresh"), provider)
        old = saved(runtime, stale=True)
        request = {"request_id": uuid4().hex, "expected_revision": 0}
        await runtime.guide_fetch(request, old["guide_id"])
        await asyncio.gather(*tuple(runtime._guide_tasks.values()))
        operation = await runtime.guide_operation(request["request_id"])
        assert operation["status"] == "completed" and operation["result"]["not_modified"]
        assert (await runtime.guide_fetch(request, old["guide_id"])) == operation
        assert len(requests) == 1
        current = runtime.memory.guides.get_document(old["guide_id"])
        assert current["text"] == BODY and current["revision_id"] == old["revision_id"]
        assert current["last_checked_at"] != old["last_checked_at"]
        await runtime.close()

    asyncio.run(run())


def test_switch_during_retrieval_cannot_inject_old_chunks(tmp_path, monkeypatch):
    async def run():
        runtime = SessionRuntime(
            initialize_data_root(tmp_path / "retrieval-cancel"), Store(Model(["should not answer"]))
        )
        old = saved(runtime)
        new = saved(runtime, "灯芯是新的名称。", URL + "/new")
        await runtime.guide_selection(selection(runtime, old))
        entered, release = threading.Event(), threading.Event()
        retrieve = runtime.guide_retriever.retrieve

        def blocked(*args, **kwargs):
            result = retrieve(*args, **kwargs)
            entered.set()
            assert release.wait(5)
            return result

        monkeypatch.setattr(runtime.guide_retriever, "retrieve", blocked)
        sid = runtime.create_session()["id"]
        tid = (await runtime.start_turn(sid, "灯芯是什么意思？"))["id"]
        assert await asyncio.to_thread(entered.wait, 2)
        control = asyncio.create_task(runtime.guide_selection(selection(runtime, new)))
        for _ in range(100):
            if runtime._memory_mutating:
                break
            await asyncio.sleep(0.001)
        assert runtime._memory_mutating
        release.set()
        await asyncio.wait_for(control, 3)
        assert not runtime.providers.adapter.messages
        assert not any(
            event["type"] in {"source", "text"} for event in runtime.events(sid, tid)["events"]
        )
        assert runtime.get_session(sid)["turns"][0]["status"] == "cancelled"
        await runtime.close()

    asyncio.run(run())


def test_local_hit_streams_before_model_finishes_and_late_tool_does_not_execute(tmp_path):
    async def run():
        gate = asyncio.Event()
        model = Model(["本地原文说明", "结束 [S1]。"], gate)
        runtime = SessionRuntime(initialize_data_root(tmp_path / "stream"), Store(model))
        await runtime.guide_selection(selection(runtime, saved(runtime)))
        sid = runtime.create_session()["id"]
        tid = (await runtime.start_turn(sid, "灯芯是什么意思？", guide=True))["id"]
        batch = await text_ready(runtime, sid, tid)
        assert batch["status"] == "running" and not gate.is_set()
        gate.set()
        await settled(runtime, sid, tid)
        runtime.events(sid, tid)
        runtime.providers.adapter = ScriptModel(
            [[text("原文给出了说明 [S1]。"), call("search_web", query="too late")]]
        )
        _, _, second = await ask(runtime, "灯芯是什么意思？", guide=True)
        assert not any(event["type"] == "tool" for event in second["events"])
        assert any(event.get("status") == "supplement_deferred" for event in second["events"])
        await runtime.close()

    asyncio.run(run())


def test_first_adoption_excludes_old_unadopted_web_advice_from_next_input(tmp_path):
    async def run():
        runtime = SessionRuntime(initialize_data_root(tmp_path / "first-adoption"), ResearchStore())
        sid, _, _ = await ask(runtime, "先读取资料", guide=True)
        assert OLD in runtime.get_session(sid)["turns"][0]["confirmed_text"]
        await runtime.guide_selection(selection(runtime, saved(runtime)))
        runtime.providers.adapter = Model(["采用资料 [S1]"])
        await ask(runtime, "灯芯是什么意思？", sid=sid)
        assert OLD not in json.dumps(runtime.providers.adapter.messages, ensure_ascii=False)
        assert OLD in runtime.get_session(sid)["turns"][0]["confirmed_text"]
        await runtime.close()

    asyncio.run(run())


def test_latest_304_does_not_upgrade_unknown_version_operation_to_supported(tmp_path, web_http):
    requests = []
    web_http(lambda request: requests.append(request) or reply("", 304))

    async def run():
        provider = Store(Model(["版本仍未核实，请确认当前版本。"]))
        provider.web_tools = lambda: WebTools({}, None)
        runtime = SessionRuntime(initialize_data_root(tmp_path / "unknown-latest"), provider)
        document = saved(runtime, "本攻略的升级门槛是二十枚印记。", version=None)
        await runtime.guide_selection(selection(runtime, document))
        await ask(runtime, "最新升级门槛是多少？", guide=True)
        context = evidence(provider.adapter.messages[-1])["local_retrieval"]
        assert context["status"] == "needs_check" and context["reason"] == "version_unverified"
        assert (
            context["version_status"] == "unknown"
            and context["revalidation"]["status"] == "not_modified"
        )
        assert len(requests) == 1
        await runtime.close()

    asyncio.run(run())
