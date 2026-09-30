"""G4/G3 integration through real Runtime acceptance, ACKs and model requests.

Only HTTP transport and model responses are synthetic. No match context,
retriever result, labels or advice records are injected into production code.
"""

import asyncio
import copy
import json
import runpy
from pathlib import Path
from uuid import uuid4

from test_match_runtime import ask, match_context

from ai_neko.config.paths import initialize_data_root
from ai_neko.runtime import SessionRuntime
from ai_neko.tools.web import WebTools, source

ROOT = Path(__file__).resolve().parents[1]
HARNESS = runpy.run_path(ROOT / "scripts" / "evaluate_guides.py")
LOADER = runpy.run_path(ROOT / "tests" / "fixtures" / "guides" / "fixture_loader.py")
GAME = {
    "game": "fog-harbor-tactics",
    "platform": "pc",
    "mode": "ranked",
    "game_version": "2.4",
}
DESCRIPTION = "我现在三灯阵已成形且仍是四人口并有18金币。"
GOAL = "潮卫三灯阵，目标升到五人口"
ADVICE = "建议下一步升到五人口并补入岸哨侦察员；这是建议，尚未执行。"


class CaptureProviders:
    def __init__(self):
        self.response = ADVICE + " [S1]"
        self.requests = []
        self.adapter_creations = 0
        self.web_calls = []

    def model(self):
        return self

    async def stream(self, messages, tools=None):
        self.requests.append({"messages": copy.deepcopy(messages), "tools": copy.deepcopy(tools)})
        yield {"type": "text", "text": self.response}

    def web_tools(self):
        self.adapter_creations += 1
        return self

    async def execute(self, name, arguments):
        self.web_calls.append((name, arguments))
        return {"status": "error", "sources": [], "error": "unexpected_test_network"}


async def load_g01(runtime):
    # Fresh synthetic reading uses the actual HTML extractor and current reader
    # timestamp. Frozen question labels and their old as-of clock are unused.
    fixture = next(item for item in LOADER["load_corpus"]()["guides"] if item["guide_id"] == "G01")
    with HARNESS["fixture_transport"]({"G01": fixture}) as calls:
        result = await WebTools({}, None).execute(
            "read_web_page", {"url": fixture["source"]["original_url"]}
        )
    assert calls == ["G01"] and result["status"] == "ok"
    saved = runtime.memory.guides.ingest(
        result["sources"][0],
        **GAME,
        version_basis=fixture["source"]["version_evidence"]["text"],
    )
    assert saved["completeness"] == "full"
    return saved, fixture


async def adopt(runtime, saved):
    return await runtime.guide_selection(
        {
            "request_id": uuid4().hex,
            "expected_revision": runtime.memory.guides.revision(),
            **{key: GAME[key] for key in ("game", "platform", "mode")},
            "guide_id": saved["guide_id"],
            "revision_id": saved["revision_id"],
        }
    )


async def begin(runtime, sid, *, old=None):
    return await runtime.match_control(
        sid,
        "new" if old else "start",
        {
            "request_id": uuid4().hex,
            "expected_revision": (await runtime.match_catalog(sid))["revision"],
            **GAME,
            "goal": GOAL,
            **({"match_id": old} if old else {}),
        },
    )


def actual_evidence(request):
    return HARNESS["injected_payload"](request["messages"])


def assert_local_original(runtime, saved, request, batch):
    evidence = actual_evidence(request)
    assert evidence["local_retrieval"]["status"] == "sufficient"
    assert evidence["sources"] and len(evidence["sources"]) <= 6
    assert sum(len(item["text"]) for item in evidence["sources"]) <= 8000
    document = runtime.memory.guides.get_document(saved["guide_id"], saved["revision_id"])
    for item in evidence["sources"]:
        assert item["guide_id"] == saved["guide_id"]
        assert item["revision_id"] == saved["revision_id"]
        assert item["local"] is True and item["chunk_id"]
        assert item["url"] == document["original_url"]
        assert document["text"][item["start"] : item["end"]] == item["text"]
    assert [event["source"] for event in batch["events"] if event["type"] == "source"] == evidence[
        "sources"
    ]
    assert not any(event.get("status") == "planning" for event in batch["events"])
    return evidence


def test_next_step_uses_real_adopted_revision_user_observation_and_acknowledged_advice(tmp_path):
    async def run():
        providers = CaptureProviders()
        runtime = SessionRuntime(initialize_data_root(tmp_path / "continuity"), providers)
        try:
            sid = runtime.create_session()["id"]
            saved, fixture = await load_g01(runtime)
            await adopt(runtime, saved)
            started = await begin(runtime, sid)
            first, first_events = await ask(runtime, sid, DESCRIPTION, guide=True)
            assert_local_original(runtime, saved, providers.requests[-1], first_events)
            assert runtime._turn_summary(runtime._turn(sid, first))["confirmed_text"]
            second, second_events = await ask(runtime, sid, "下一步呢？", guide=True)
            evidence = assert_local_original(runtime, saved, providers.requests[-1], second_events)
            context = match_context(providers.requests[-1]["messages"])
            assert context["match_id"] == started["match_id"] and context["goal"] == GOAL
            assert context["selection"]["guide_id"] == saved["guide_id"]
            assert context["selection"]["revision_id"] == saved["revision_id"]
            assert [(item["turn_id"], item["text"]) for item in context["observations"]] == [
                (first, DESCRIPTION)
            ]
            assert context["observations"][0]["source_kind"] == "user_description"
            assert context["observations"][0]["fields"] == {}
            advice = context["last_delivered_advice"]
            assert advice["turn_id"] == first and ADVICE in advice["text"]
            assert advice["delivery"] == "shown" and advice["delivered"] is True
            assert advice["executed"] is False
            assert advice["guide_id"] == saved["guide_id"]
            assert advice["revision_id"] == saved["revision_id"]
            # Goal/advice's five population never becomes an executed fact;
            # the only observation remains the user's actual four population.
            assert "五人口" not in json.dumps(context["observations"], ensure_ascii=False)
            expected = fixture["passage_spans"]["G01-P04"]["exact_text"]
            assert any(expected in item["text"] for item in evidence["sources"][:3])
            assert runtime._binding(second)["selection"]["revision_id"] == saved["revision_id"]
            assert len(providers.requests) == 2
            assert providers.adapter_creations == 0 and providers.web_calls == []
        finally:
            await runtime.close()

    asyncio.run(run())


def test_old_guide_review_survives_new_match_history_but_disappears_after_guide_deletion(tmp_path):
    async def run():
        providers = CaptureProviders()
        runtime = SessionRuntime(initialize_data_root(tmp_path / "review"), providers)
        try:
            sid = runtime.create_session()["id"]
            saved, _ = await load_g01(runtime)
            await adopt(runtime, saved)
            old = (await begin(runtime, sid))["match_id"]
            first, _ = await ask(runtime, sid, DESCRIPTION, guide=True)
            other_body = (
                "灯芯是后排中央的潮灯守卫。当前B专用紫色灯芯条款，只供当前采用资料使用。" * 4
            )
            other = runtime.memory.guides.ingest(
                source(
                    "https://guides.example.test/current-b",
                    status="read",
                    title="当前B",
                    text=other_body,
                ),
                **GAME,
            )
            await adopt(runtime, other)
            await begin(runtime, sid, old=old)
            providers.response = "当前B专属已确认建议。 [S1]"
            for _ in range(11):
                await ask(runtime, sid, "灯芯是什么意思？", guide=True)
            # More than the ordinary ten-turn window separates the old match.
            assert len(runtime.get_session(sid)["turns"]) == 12
            providers.response = "复盘中的原建议：" + ADVICE
            before = len(providers.requests)
            review, review_events = await ask(
                runtime, sid, "复盘上局你当时建议了什么？", guide=True, review_match_id=old
            )
            request = providers.requests[-1]
            assert len(providers.requests) == before + 1 and request["tools"] is None
            evidence = actual_evidence(request)
            assert evidence["local_retrieval"]["status"] == "historical"
            assert evidence["sources"] == []
            assert not any(event["type"] == "source" for event in review_events["events"])
            context = match_context(request["messages"])
            assert context["history_only"] and context["match_id"] == old
            sent = json.dumps(request["messages"], ensure_ascii=False)
            assert DESCRIPTION in sent and ADVICE in sent
            assert "当前B专属" not in sent and "紫色灯芯条款" not in sent
            assert other["guide_id"] not in sent
            assert runtime._db.execute(
                "SELECT 1 FROM turn_guides WHERE turn_id=? AND guide_id=? AND revision_id=?",
                (review, saved["guide_id"], saved["revision_id"]),
            ).fetchone()
            await runtime.guide_delete(
                saved["guide_id"],
                {"request_id": uuid4().hex, "expected_revision": runtime.memory.guides.revision()},
            )
            assert runtime._turn_summary(runtime._turn(sid, first))["confirmed_text"] == ""
            assert runtime._turn_summary(runtime._turn(sid, review))["confirmed_text"] == ""
            providers.response = "相关历史资料已删除，不能恢复旧建议。"
            before = len(providers.requests)
            await ask(runtime, sid, "再次复盘上局的建议", guide=True, review_match_id=old)
            request = providers.requests[-1]
            assert len(providers.requests) == before + 1 and request["tools"] is None
            assert actual_evidence(request)["sources"] == []
            sent = json.dumps(request["messages"], ensure_ascii=False)
            assert ADVICE not in sent and "当前B专属" not in sent and "紫色灯芯条款" not in sent
            assert providers.adapter_creations == 0 and providers.web_calls == []
        finally:
            await runtime.close()

    asyncio.run(run())
