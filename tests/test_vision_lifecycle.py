"""Freshness boundaries use a synthetic clock, never real desktop images."""

import asyncio
import base64
import json
from types import SimpleNamespace

import pytest
from langgraph.checkpoint.memory import InMemorySaver
from langsmith import tracing_context

from ai_neko.chat import vision
from ai_neko.chat.graph import build_chat_graph


@pytest.fixture
def clock(monkeypatch):
    clock = SimpleNamespace(now=1_700_000_000.0)
    monkeypatch.setattr(vision, "time", SimpleNamespace(time=lambda: clock.now))
    return clock


def picture(clock):
    return {
        "frame_id": "synthetic-frame",
        "source_id": "window:synthetic:0",
        "source_name": "Synthetic game",
        "captured_at": clock.now,
        "data_url": "data:image/png;base64,"
        + base64.b64encode(b"\x89PNG\r\n\x1a\nSYNTHETIC_FRESHNESS_FRAME").decode(),
    }


class TimedModel:
    def __init__(self, clock, scripts):
        self.clock = clock
        self.scripts = iter(scripts)
        self.calls = []
        self.closed_streams = 0

    async def stream(self, messages, tools=None):
        self.calls.append((messages, tools))
        try:
            for advance, event in next(self.scripts):
                self.clock.now += advance
                if event is not None:
                    yield event
        finally:
            self.closed_streams += 1


class SlowWeb:
    def __init__(self, clock, *, result=None):
        self.clock = clock
        self.calls = []
        self.result = result or {"status": "ok", "sources": []}

    async def execute(self, name, arguments):
        self.calls.append((name, arguments))
        self.clock.now += 121
        return self.result


def text(value):
    return {"type": "text", "text": value}


def search():
    return {"type": "tool_call", "name": "search_web", "arguments": {"query": "游戏规则"}}


def invoke(graph, *, guide, config=None):
    async def run():
        with tracing_context(enabled=False):
            return await graph.ainvoke(
                {
                    "messages": [{"role": "user", "content": "下一步怎么办？"}],
                    "guide": guide,
                    "rounds": 0,
                    "calls": [],
                    "sources": [],
                    "tool_messages": [],
                    "output": "",
                },
                config=config,
            )

    return asyncio.run(run())


def test_check_age_false_allows_old_retry_fingerprint_without_weakening_default(clock):
    image = picture(clock)
    image["captured_at"] = int(clock.now)
    accepted = vision.validate_image(image)
    clock.now += 121
    assert vision.validate_image(image, check_age=False) == accepted
    assert type(accepted["captured_at"]) is int
    with pytest.raises(vision.StaleImageError) as error:
        vision.validate_image(image)
    assert error.value.code == "stale_image"


@pytest.mark.parametrize(
    "change",
    [
        {"captured_at": True},
        {"captured_at": "1700000000"},
        {"captured_at": float("nan")},
        {"captured_at": float("inf")},
        {"captured_at": 10**1000},
        {"frame_id": ""},
        {"source_id": "bad\nsource"},
        {"data_url": "https://example.com/image.png"},
        {"data_url": "data:image/png;base64,AAAA"},
    ],
)
def test_disabling_age_check_still_rejects_invalid_image_structure(clock, change):
    with pytest.raises(ValueError):
        vision.validate_image({**picture(clock), **change}, check_age=False)


@pytest.mark.parametrize("age", [-5, 0, 120])
def test_freshness_inclusive_boundary_accepts_seconds_and_milliseconds(clock, age):
    image = picture(clock)
    image["captured_at"] = (clock.now - age) * 1000
    assert vision.validate_image(image)["captured_at"] == clock.now - age


@pytest.mark.parametrize("age", [-5.001, 120.001])
def test_freshness_rejects_future_or_expired_frame_before_attachment(clock, age):
    image = picture(clock)
    image["captured_at"] = clock.now - age
    with pytest.raises(vision.StaleImageError):
        vision.attach_image([{"role": "user", "content": "question"}], image)


@pytest.mark.parametrize("guide", [False, True])
def test_expired_frame_never_starts_initial_model_request(clock, guide):
    image = picture(clock)
    clock.now += 121
    model = TimedModel(clock, [[(0, text("must not be requested"))]])
    graph = build_chat_graph(model, SlowWeb(clock), lambda _event: None, None, image=image)
    with pytest.raises(vision.StaleImageError):
        invoke(graph, guide=guide)
    assert model.calls == []


@pytest.mark.parametrize("tool_result", [None, {"status": "error", "error": "search_key_missing"}])
def test_slow_tool_prevents_followup_planning_or_answer_from_resending_old_image(
    clock, tool_result
):
    image = picture(clock)
    model = TimedModel(clock, [[(0, search())], [(0, text("must not be requested"))]])
    web = SlowWeb(clock, result=tool_result)
    emitted = []
    graph = build_chat_graph(model, web, emitted.append, InMemorySaver(), image=image)
    config = {"configurable": {"thread_id": "synthetic-freshness-turn"}}
    with pytest.raises(vision.StaleImageError):
        invoke(graph, guide=True, config=config)
    assert len(model.calls) == len(web.calls) == 1
    assert model.calls[0][0][-1]["content"][1]["image_url"]["url"] == image["data_url"]
    assert not any(event["type"] == "text" for event in emitted)
    for snapshot in graph.get_state_history(config):
        assert image["data_url"] not in json.dumps(snapshot.values)


def test_planning_stream_expiry_discards_late_tool_calls_and_closes_stream(clock):
    model = TimedModel(clock, [[(121, search())]])
    web = SlowWeb(clock)
    graph = build_chat_graph(model, web, lambda _event: None, None, image=picture(clock))
    with pytest.raises(vision.StaleImageError):
        invoke(graph, guide=True)
    assert len(model.calls) == model.closed_streams == 1
    assert web.calls == []


def test_answer_stream_expiry_keeps_only_already_delivered_fresh_text(clock):
    model = TimedModel(clock, [[(0, text("先确认棋盘。")), (121, text("过期局面的具体建议。"))]])
    emitted = []
    graph = build_chat_graph(model, None, emitted.append, None, image=picture(clock))
    with pytest.raises(vision.StaleImageError):
        invoke(graph, guide=False)
    assert [event["text"] for event in emitted if event["type"] == "text"] == ["先确认棋盘。"]
    assert len(model.calls) == model.closed_streams == 1


def test_silent_end_after_expiry_does_not_complete_an_answer_or_flush_pending_citation(clock):
    model = TimedModel(clock, [[(0, text("[S")), (121, None)]])
    emitted = []
    graph = build_chat_graph(model, None, emitted.append, None, image=picture(clock))
    with pytest.raises(vision.StaleImageError):
        invoke(graph, guide=False)
    assert not any(event["type"] == "text" for event in emitted)
    assert model.closed_streams == 1


def test_fresh_planning_and_answer_keep_the_same_frame_outside_state(clock):
    image = picture(clock)
    model = TimedModel(clock, [[(0, text("hidden planning"))], [(2, text("先看看当前棋盘。"))]])
    graph = build_chat_graph(model, SlowWeb(clock), lambda _event: None, None, image=image)
    state = invoke(graph, guide=True)
    assert state["output"] == "先看看当前棋盘。"
    assert len(model.calls) == 2
    assert all(
        messages[-1]["content"][1]["image_url"]["url"] == image["data_url"]
        for messages, _tools in model.calls
    )
    assert image["data_url"] not in json.dumps(state)


def test_no_image_chat_is_unaffected_by_elapsed_vision_deadline(clock):
    model = TimedModel(clock, [[(0, search())], [], [(121, text("这是规则资料回答。"))]])
    graph = build_chat_graph(model, SlowWeb(clock), lambda _event: None, None)
    state = invoke(graph, guide=True)
    assert state["output"] == "这是规则资料回答。"
    assert len(model.calls) == 3
