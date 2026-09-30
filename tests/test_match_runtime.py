"""Real Runtime/graph requests prove match isolation and fresh evidence boundaries."""

import asyncio
import base64
import json
import threading
import time
from types import SimpleNamespace
from uuid import uuid4

import pytest
from test_runtime import Model, Store, settled, text_ready

from ai_neko.config.paths import initialize_data_root
from ai_neko.runtime import (
    RuntimeConflictError,
    RuntimeInputError,
    SessionRuntime,
    match_store,
    matches,
)
from ai_neko.tools.web import source


def controls(revision, **fields):
    return {"request_id": uuid4().hex, "expected_revision": revision, **fields}


async def binding(runtime, sid):
    catalog = await runtime.match_catalog(sid)
    return {
        "match_id": catalog["current"]["match_id"] if catalog["current"] else None,
        "expected_revision": catalog["revision"],
    }


async def begin(runtime, sid, *, old=None, goal="潮卫三灯阵"):
    return await runtime.match_control(
        sid,
        "new" if old else "start",
        controls(
            (await runtime.match_catalog(sid))["revision"],
            game="fog",
            platform="pc",
            mode="ranked",
            game_version="2.4",
            goal=goal,
            **({"match_id": old} if old else {}),
        ),
    )


async def ask(runtime, sid, text, *, ack=True, **kwargs):
    turn = await runtime.start_turn(sid, text, match=await binding(runtime, sid), **kwargs)
    tid = turn["id"]
    await settled(runtime, sid, tid)
    events = runtime.events(sid, tid)
    assert events["status"] == "completed", runtime.get_session(sid)["turns"][-1]
    if ack:
        runtime.ack(sid, tid, events["sent_seq"])
    return tid, events


def match_context(messages):
    for message in messages:
        content = message.get("content")
        if isinstance(content, str) and '"match_context"' in content:
            # Graph inserts its data-only JSON as a separate user message.
            return json.loads(content[content.index("{") :])["match_context"]
    return None


def picture(name):
    return {
        "frame_id": name,
        "source_id": "window:synthetic",
        "source_name": "Synthetic match",
        "captured_at": time.time(),
        "data_url": "data:image/png;base64,"
        + base64.b64encode(b"\x89PNG\r\n\x1a\nSYNTHETIC_MATCH_FRAME").decode(),
    }


class VisualModel(Model):
    def __init__(self, reports):
        super().__init__(["建议先检查阵容。", "升级尚待用户确认。"])
        self.reports = iter(reports)
        self.observer_messages = []

    async def stream(self, messages, tools=None):
        if tools and tools[0]["function"]["name"] == "report_match_observation":
            self.observer_messages.append(messages)
            yield {
                "type": "tool_call",
                "id": "observe",
                "name": "report_match_observation",
                "arguments": {"fields": next(self.reports)},
            }
        else:
            async for event in super().stream(messages, tools):
                yield event


def test_new_match_excludes_ten_old_turns_but_preserves_preferences_and_visible_history(
    tmp_path,
):
    async def run():
        model = Model(["旧局建议：买七枚铜牌。"])
        r = SessionRuntime(initialize_data_root(tmp_path / "data"), Store(model))
        try:
            r.memory.remember("我喜欢稳健的游戏风格。", source_id="manual:preference")
            sid = r.create_session()["id"]
            first = (await begin(r, sid))["match_id"]
            for i in range(10):
                await ask(r, sid, f"我现在有{711 + i}金币和旧局专用青铜棋子。")
            await begin(r, sid, old=first, goal="本局独立目标")
            model.chunks = ["新局建议。"]
            await ask(r, sid, "下一步呢？")
            request = json.dumps(model.messages[-1], ensure_ascii=False)
            assert "旧局专用青铜棋子" not in request and "买七枚铜牌" not in request
            assert all(f"{count}金币" not in request for count in range(711, 721))
            assert match_context(model.messages[-1])["observations"] == []
            assert "稳健的游戏风格" in request and "本局独立目标" in request
            assert len(r.get_session(sid)["turns"]) == 11
            assert "旧局专用" in r.get_session(sid)["turns"][0]["input"]
            assert match_context(model.messages[-1])["last_delivered_advice"] is None
        finally:
            await r.close()

    asyncio.run(run())


def test_followup_receives_only_current_literal_observation_and_shown_advice(tmp_path):
    async def run():
        model = Model(["建议凑齐潮卫三灯阵，尚未执行。"])
        r = SessionRuntime(initialize_data_root(tmp_path / "data"), Store(model))
        try:
            sid = r.create_session()["id"]
            await begin(r, sid)
            prior, _ = await ask(r, sid, "我现在有18金币，三灯已经成形。")
            await ask(r, sid, "下一步呢？")
            context = match_context(model.messages[-1])
            assert context["observations"][0]["text"] == "我现在有18金币，三灯已经成形。"
            assert context["last_delivered_advice"]["turn_id"] == prior
            assert context["last_delivered_advice"]["executed"] is False
            assert context["last_delivered_advice"]["delivery"] == "shown"
            assert context["last_delivered_advice"]["delivered_at"] > 0
            assert r._db.execute(
                "SELECT 1 FROM turn_user_history WHERE source_turn_id=?", (prior,)
            ).fetchone()
            assert not r.memory._db.execute("SELECT 1 FROM memory_jobs").fetchone()
        finally:
            await r.close()

    asyncio.run(run())


def test_visual_new_frame_replaces_missing_fields_and_close_keeps_user_description(tmp_path):
    async def run():
        model = VisualModel(
            [
                [{"name": "金币", "value": "18"}, {"name": "人口", "value": "4"}],
                [{"name": "人口", "value": "5"}],
                [],
            ]
        )
        r = SessionRuntime(initialize_data_root(tmp_path / "data"), Store(model))
        try:
            sid = r.create_session()["id"]
            mid = (await begin(r, sid))["match_id"]
            await ask(r, sid, "帮我看看画面。", image=picture("first"))
            assert match_context(model.messages[-1])["observations"][0]["fields"] == {
                "金币": "18",
                "人口": "4",
            }
            await ask(r, sid, "这一帧呢？", image=picture("second"))
            context = match_context(model.messages[-1])
            assert len(context["observations"]) == 1
            assert context["observations"][0]["fields"] == {"人口": "5"}
            assert "18" not in context["observations"][0]["text"]
            await ask(r, sid, "还能看到什么？", image=picture("unknown"))
            assert match_context(model.messages[-1])["observations"][0]["unknown"]
            await ask(r, sid, "我现在位于北门。")
            cat = await r.match_catalog(sid)
            await r.match_control(sid, "close_observation", controls(cat["revision"], match_id=mid))
            await ask(r, sid, "下一步呢？")
            context = match_context(model.messages[-1])
            assert all(o["source_kind"] != "vision" for o in context["observations"])
            assert context["observations"][0]["text"] == "我现在位于北门。"
            for path in (tmp_path / "data").rglob("*.sqlite*"):
                assert b"SYNTHETIC_MATCH_FRAME" not in path.read_bytes()
                assert picture("bytes")["data_url"].encode() not in path.read_bytes()
        finally:
            await r.close()

    asyncio.run(run())


def test_expired_user_state_is_not_replayed_via_recent_history(tmp_path, monkeypatch):
    async def run():
        model = Model(["建议还需核实。"])
        r = SessionRuntime(initialize_data_root(tmp_path / "data"), Store(model))
        try:
            sid = r.create_session()["id"]
            await begin(r, sid)
            await ask(r, sid, "我现在有913金币。")
            future = time.time() + 121
            clock = SimpleNamespace(time=lambda: future)
            monkeypatch.setattr(match_store, "time", clock)
            monkeypatch.setattr(matches, "time", clock)
            await ask(r, sid, "下一步呢？")
            context = match_context(model.messages[-1])
            assert context["observations"] == [] and context["last_delivered_advice"] is None
            assert "913金币" not in json.dumps(model.messages[-1], ensure_ascii=False)
        finally:
            await r.close()

    asyncio.run(run())


def test_restart_needs_update_and_fresh_image_does_not_resurrect_previous_advice(tmp_path):
    async def run():
        paths = initialize_data_root(tmp_path / "data")
        r = SessionRuntime(paths, Store(Model(["前进到旧局彩虹塔。"])))
        sid = r.create_session()["id"]
        mid = (await begin(r, sid))["match_id"]
        await ask(r, sid, "我现在有937金币。")
        await r.close()
        model = VisualModel([[{"name": "位置", "value": "北门"}]])
        r = SessionRuntime(paths, Store(model))
        try:
            assert (await r.match_catalog(sid))["current"]["status"] == "needs_update"
            await ask(r, sid, "下一步呢？")
            request = json.dumps(model.messages[-1], ensure_ascii=False)
            assert "937金币" not in request and "彩虹塔" not in request
            assert match_context(model.messages[-1])["status"] == "needs_update"
            assert match_context(model.messages[-1])["observations"] == []
            await ask(r, sid, "看看新画面。", image=picture("restarted"))
            context = match_context(model.messages[-1])
            assert context["status"] == "active" and context["match_id"] == mid
            assert [item["fields"] for item in context["observations"]] == [{"位置": "北门"}]
            assert "937金币" not in json.dumps(context, ensure_ascii=False)
            assert "彩虹塔" not in json.dumps(context, ensure_ascii=False)
        finally:
            await r.close()

    asyncio.run(run())


def test_voice_advice_requires_completed_audio_even_when_text_acknowledged(tmp_path):
    async def run():
        model = Model(["先检查金币。", "再考虑升级。"])
        r = SessionRuntime(initialize_data_root(tmp_path / "data"), Store(model))
        try:
            sid = r.create_session()["id"]
            await begin(r, sid)
            tid, _ = await ask(r, sid, "我现在有18金币。", input_origin="voice")
            await ask(r, sid, "下一步呢？", ack=False)
            assert match_context(model.messages[-1])["last_delivered_advice"] is None
            segment = uuid4().hex
            r.audio_ack(sid, tid, segment, "started", text_start=0, text_end=len("先检查金币。"))
            r.audio_ack(sid, tid, segment, "completed")
            await ask(r, sid, "然后呢？", ack=False)
            advice = match_context(model.messages[-1])["last_delivered_advice"]
            assert advice["text"] == "先检查金币。" and advice["delivery"] == "heard"
            assert not advice["executed"]
        finally:
            await r.close()

    asyncio.run(run())


def test_new_match_rejects_late_requests_and_cancels_old_stream_without_affecting_other_session(
    tmp_path,
):
    async def run():
        model = Model(["旧局开头"], gate=asyncio.Event(), late=True)
        r = SessionRuntime(initialize_data_root(tmp_path / "data"), Store(model))
        try:
            sid = r.create_session()["id"]
            mid = (await begin(r, sid))["match_id"]
            old_binding = await binding(r, sid)
            tid = (await r.start_turn(sid, "请给建议。", match=old_binding))["id"]
            await text_ready(r, sid, tid)
            other = r.create_session()["id"]
            other_tid = (await r.start_turn(other, "另一聊天"))["id"]
            await text_ready(r, other, other_tid)
            await begin(r, sid, old=mid)
            assert r._turn(sid, tid)["status"] == "cancelled"
            assert r._turn(other, other_tid)["status"] == "running"
            assert "LATE_RESULT" not in json.dumps(r.events(sid, tid), ensure_ascii=False)
            with pytest.raises(RuntimeConflictError):
                await r.start_turn(sid, "迟到图片请求", match=old_binding, image=picture("late"))
            with pytest.raises(RuntimeInputError):
                await r.start_turn(sid, "没有绑定")
        finally:
            await r.close()

    asyncio.run(run())


def test_recall_inflight_cannot_accept_across_new_match(tmp_path):
    async def run():
        r = SessionRuntime(initialize_data_root(tmp_path / "data"), Store())
        entered, release = threading.Event(), threading.Event()
        try:
            sid = r.create_session()["id"]
            mid = (await begin(r, sid))["match_id"]
            old_binding = await binding(r, sid)
            original = r.memory.recall_context

            def blocked(*args, **kwargs):
                entered.set()
                release.wait(3)
                return original(*args, **kwargs)

            r.memory.recall_context = blocked
            pending = asyncio.create_task(r.start_turn(sid, "旧接受请求", match=old_binding))
            assert await asyncio.to_thread(entered.wait, 2)
            await begin(r, sid, old=mid)
            release.set()
            with pytest.raises(RuntimeConflictError):
                await pending
            assert r.get_session(sid)["turns"] == []
        finally:
            release.set()
            await r.close()

    asyncio.run(run())


def test_explicit_historical_review_is_labelled_and_does_not_refresh_live_state(tmp_path):
    async def run():
        model = Model(["旧局已显示建议。"])
        r = SessionRuntime(initialize_data_root(tmp_path / "data"), Store(model))
        try:
            sid = r.create_session()["id"]
            old = (await begin(r, sid))["match_id"]
            await ask(r, sid, "我现在有842金币。")
            await begin(r, sid, old=old)
            for _ in range(10):
                await ask(r, sid, "随便聊一句", ack=False)
            await ask(r, sid, "复盘上一局", review_match_id=old)
            request = json.dumps(model.messages[-1], ensure_ascii=False)
            assert "842金币" in request and match_context(model.messages[-1])["history_only"]
            await ask(r, sid, "下一步呢？")
            assert "842金币" not in json.dumps(model.messages[-1], ensure_ascii=False)
            assert match_context(model.messages[-1])["last_delivered_advice"] is None
        finally:
            await r.close()

    asyncio.run(run())


@pytest.mark.parametrize(
    "text,expected",
    [
        ("我现在有多少金币？", ""),
        ("我有18金币，如果升级后会剩100金币怎么办？", ""),
        ("如果这只是演练，当前金币四十，下一步呢？", ""),
        ("你好， 我现在有四十金币。", "我现在有四十金币。"),
        ("我现在有18金币，三灯已经成形。下一步呢？", "我现在有18金币，三灯已经成形。"),
    ],
)
def test_user_description_preserves_only_literal_unconditional_quotes(text, expected):
    assert matches.user_description(text) == expected
    assert not expected or expected in text


def test_match_uses_the_actual_adopted_revision_after_case_normalization(tmp_path):
    async def run():
        r = SessionRuntime(initialize_data_root(tmp_path / "data"), Store())
        try:
            saved = r.memory.guides.ingest(
                source("https://example.com/fog", status="read", text="灯芯是潮灯守卫。"),
                game="fog",
                platform="pc",
                mode="ranked",
                game_version="2.4",
            )
            await r.guide_selection(
                controls(
                    r.memory.guides.published_revision,
                    game="fog",
                    platform="pc",
                    mode="ranked",
                    guide_id=saved["guide_id"],
                    revision_id=saved["revision_id"],
                )
            )
            sid = r.create_session()["id"]
            result = await r.match_control(
                sid,
                "start",
                controls(0, game="Fog", platform="PC", mode="Ranked", game_version="2.4"),
            )
            selected = result["current"]["selection"]
            assert selected["guide_id"] == saved["guide_id"]
            assert selected["revision_id"] == saved["revision_id"]
            await ask(r, sid, "灯芯是什么意思？")
            assert match_context(r.providers.adapter.messages[-1])["selection"] == selected
        finally:
            await r.close()

    asyncio.run(run())


def test_forgetting_scrubs_match_observations_goals_consumers_and_durable_replays(tmp_path):
    async def run():
        marker = "我现在记着紫色海盐密语。"
        model = Model(["收到了当前描述。"])
        r = SessionRuntime(initialize_data_root(tmp_path / "data"), Store(model))
        try:
            sid = r.create_session()["id"]
            first = await begin(r, sid, goal=marker)
            mid = first["match_id"]
            tid, _ = await ask(r, sid, marker, ack=False)
            confirm = controls(
                (await r.match_catalog(sid))["revision"], match_id=mid, turn_id=tid, text=marker
            )
            await r.match_control(sid, "observe", confirm)
            consumer, _ = await ask(r, sid, "下一步呢？")
            assert marker in json.dumps(model.messages[-1], ensure_ascii=False)
            fact = r.memory.remember(marker, source_id="turn:" + tid)
            await r.forget_memory(fact["id"])
            current = (await r.match_catalog(sid))["current"]
            assert current["goal"] == ""
            assert (await r.match_detail(sid, mid))["observations"] == []
            assert (
                r._db.execute(
                    "SELECT count(*) FROM match_observations WHERE turn_id=?", (tid,)
                ).fetchone()[0]
                == 0
            )
            assert r.get_session(sid)["turns"][-1]["assistant_text"] == ""
            assert (
                r._db.execute(
                    "SELECT count(*) FROM events WHERE turn_id=?", (consumer,)
                ).fetchone()[0]
                == 0
            )
            replay = await r.match_control(sid, "observe", confirm)
            assert replay["replayed"]
            await ask(r, sid, "下一步呢？")
            assert marker not in json.dumps(model.messages[-1], ensure_ascii=False)
            for path in (tmp_path / "data").rglob("*.sqlite*"):
                assert marker.encode() not in path.read_bytes(), path
        finally:
            await r.close()

    asyncio.run(run())
