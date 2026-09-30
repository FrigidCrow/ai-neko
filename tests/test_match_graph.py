"""Real graph checks for bounded observation and live scoped context injection.

Images and model events are synthetic. These checks establish data/control flow,
not whether a real vision model reads a game screen correctly.
"""

import asyncio
import base64
import copy
import json
from types import SimpleNamespace

import pytest
from langgraph.checkpoint.memory import InMemorySaver
from langgraph.errors import NodeCancelledError
from langsmith import tracing_context
from test_chat import ScriptModel, ScriptWeb, call, result, text

from ai_neko.chat import match_observation, vision
from ai_neko.chat.graph import build_chat_graph
from ai_neko.chat.match_observation import OBSERVATION_TOOLS, REPORT_TOOL, parse_fields


@pytest.fixture
def clock(monkeypatch):
    clock = SimpleNamespace(now=1_700_000_000.0)
    monkeypatch.setattr(vision, "time", SimpleNamespace(time=lambda: clock.now))
    monkeypatch.setattr(match_observation, "time", SimpleNamespace(time=lambda: clock.now))
    return clock


@pytest.fixture
def image(clock):
    return {
        "frame_id": "current-synthetic-frame",
        "source_id": "window:synthetic-current",
        "source_name": "Synthetic game screen",
        "captured_at": clock.now,
        "data_url": "data:image/png;base64,"
        + base64.b64encode(b"\x89PNG\r\n\x1a\nCURRENT_SYNTHETIC_FRAME_BYTES").decode(),
    }


def report(fields=None, **arguments):
    return {
        "type": "tool_call",
        "name": REPORT_TOOL,
        "arguments": {
            "fields": [{"name": "金币", "value": "17"}] if fields is None else fields,
            **arguments,
        },
    }


def current(**changes):
    return {
        "match_id": "match-current",
        "status": "active",
        "state_revision": 1,
        "game": "synthetic-game",
        "platform": "pc",
        "mode": "ranked",
        "observations": [],
        **changes,
    }


def initial(guide=False):
    return {
        "messages": [{"role": "user", "content": "CURRENT_QUESTION 下一步呢？"}],
        "guide": guide,
        "rounds": 0,
        "calls": [],
        "sources": [],
        "tool_messages": [],
        "output": "",
    }


async def invoke(graph, *, guide=False, config=None):
    with tracing_context(enabled=False):
        return await graph.ainvoke(initial(guide), config=config)


def context_from(messages):
    return json.loads(
        next(
            item["content"].split("\n", 1)[1]
            for item in messages
            if item["role"] == "user"
            and isinstance(item["content"], str)
            and item["content"].startswith("本地对局资料（")
        )
    )["match_context"]


@pytest.mark.parametrize(
    "value",
    [
        None,
        current(status="ended"),
        current(status="historical", history_only=True),
        current(history_only=True),
        {"status": "active"},
    ],
)
def test_no_active_match_does_not_add_observation_call_or_record(image, value):
    model, emitted, recorded = ScriptModel([[text("回答。")]]), [], []

    async def record(fields):
        recorded.append(fields)

    graph = build_chat_graph(
        model,
        None,
        emitted.append,
        None,
        image=image,
        match_context=lambda: value,
        record_observation=record,
    )
    output = asyncio.run(invoke(graph))
    assert output["output"] == "回答。"
    assert len(model.calls) == 1 and model.calls[0][1] is None
    assert recorded == []
    assert not any(
        event.get("status") in {"observing_match", "match_observation"} for event in emitted
    )


@pytest.mark.parametrize("missing", ["image", "recorder"])
def test_no_image_or_recorder_preserves_single_answer_call(image, missing):
    model = ScriptModel([[text("回答。")]])

    async def record(_fields):
        raise AssertionError("must not be called")

    graph = build_chat_graph(
        model,
        None,
        lambda _: None,
        None,
        image=None if missing == "image" else image,
        match_context=lambda: current(),
        record_observation=None if missing == "recorder" else record,
    )
    asyncio.run(invoke(graph))
    assert len(model.calls) == 1


@pytest.mark.parametrize("unsolicited_tool", [False, True])
def test_explicit_historical_review_answers_once_without_search_or_supplement_tools(
    unsolicited_tool,
):
    events = [text("这是历史复盘。")]
    if unsolicited_tool:
        events.insert(0, call("search_web", query="must not execute"))
    model, web, emitted = ScriptModel([events]), ScriptWeb([]), []

    async def local():
        return {"status": "historical", "reason": "explicit_review", "sources": []}

    graph = build_chat_graph(
        model,
        web,
        emitted.append,
        None,
        local_retrieve=local,
        match_context=lambda: current(status="historical", history_only=True),
    )
    output = asyncio.run(invoke(graph, guide=True))
    assert output["output"] == "这是历史复盘。"
    assert len(model.calls) == 1 and model.calls[0][1] is None
    assert context_from(model.calls[0][0])["history_only"]
    assert web.calls == [] and output["rounds"] == 0
    assert not any(event.get("status") in {"planning", "researching"} for event in emitted)
    assert not any(event["type"] in {"source", "tool"} for event in emitted)


@pytest.mark.parametrize("initial_status", ["active", "needs_update"])
def test_observation_precedes_retrieval_and_fresh_actual_model_context(image, initial_status):
    model = ScriptModel([[report()], [text("新观察后的建议。")]])
    emitted, records, order = [], [], []
    state = current(status=initial_status, observations=[{"text": "OLD_OBSERVATION_PRIVATE"}])
    state["last_delivered_advice"] = {"text": "OLD_ADVICE_PRIVATE", "executed_by_user": False}

    async def record(fields):
        records.append(copy.deepcopy(fields))
        order.append("record")
        state.update(
            status="active",
            state_revision=2,
            observations=[
                {
                    "name": fields[0]["name"],
                    "value": fields[0]["value"],
                    "text": "NEW_OBSERVATION_PRIVATE",
                    "frame_id": image["frame_id"],
                    "source_kind": "vision",
                }
            ],
        )

    async def local():
        order.append("local")
        assert state["state_revision"] == 2
        return {"status": "sufficient", "sources": []}

    graph = build_chat_graph(
        model,
        None,
        emitted.append,
        None,
        image=image,
        match_context=lambda: copy.deepcopy(state),
        record_observation=record,
        local_retrieve=local,
    )
    output = asyncio.run(invoke(graph))
    assert order == ["record", "local"] and len(model.calls) == 2
    assert records == [[{"name": "金币", "value": "17"}]]
    observation_messages, tools = model.calls[0]
    assert tools == OBSERVATION_TOOLS
    assert len(tools) == 1 and tools[0]["function"]["name"] == REPORT_TOOL
    encoded = json.dumps(observation_messages, ensure_ascii=False)
    assert image["data_url"] in encoded and image["frame_id"] in encoded
    assert "OLD_OBSERVATION_PRIVATE" not in encoded and "OLD_ADVICE_PRIVATE" not in encoded
    assert "CURRENT_QUESTION" not in encoded
    assert "不推测用户已执行" in observation_messages[0]["content"]
    assert "画面中的文字" in observation_messages[0]["content"]
    answer_messages = model.calls[1][0]
    assert context_from(answer_messages)["state_revision"] == 2
    assert "NEW_OBSERVATION_PRIVATE" in json.dumps(answer_messages, ensure_ascii=False)
    # The current question, not the injected data message, owns this frame.
    attachment = next(item for item in answer_messages if isinstance(item["content"], list))
    assert attachment["content"][0]["text"].startswith("CURRENT_QUESTION")
    assert attachment["content"][1]["image_url"]["url"] == image["data_url"]
    assert output["output"] == "新观察后的建议。"
    events = json.dumps(emitted, ensure_ascii=False)
    assert (
        "NEW_OBSERVATION_PRIVATE" not in events
        and "金币" not in events
        and image["data_url"] not in events
    )
    completed = next(event for event in emitted if event.get("status") == "match_observation")
    assert completed["source_kind"] == "vision" and completed["frame_id"] == image["frame_id"]
    assert completed["known_count"] == completed["fields_count"] == 1


@pytest.mark.parametrize(
    "events",
    [
        [],
        [text("金币是99。自由回答不可作为状态。")],
        [{"type": "tool_call", "name": REPORT_TOOL, "arguments": {}}],
        [report(extra="not allowed")],
        [report(), report()],
        [call("search_web", query="do not run")],
        [call("write_memory", text="do not run"), report()],
        [report(), call("search_web", query="do not run")],
        [report([{"name": "金币"}])],
        [report([{"name": "金币", "value": 99}])],
        [report([{"name": "金币", "value": "9", "frame_id": "forged"}])],
        [report([{"name": "金币", "value": " 9 "}])],
        [report([{"name": "金币", "value": "9\u0000"}])],
        [report([{"name": "金币", "value": "9\u007f"}])],
        [report([{"name": "金币", "value": "9\ud800"}])],
        [report([{"name": "金币\u007f", "value": "9"}])],
        [report([{"name": "金币\ud800", "value": "9"}])],
        [report([{"name": "金币", "value": "9"}, {"name": "金币", "value": "10"}])],
        [report([{"name": str(i), "value": "1"} for i in range(17)])],
    ],
)
def test_invalid_or_missing_tool_report_records_unknown_without_side_effect_tools(image, events):
    model = ScriptModel([events, [text("未知字段请补充。")]])
    web, emitted, recorded = ScriptWeb([]), [], []

    async def record(fields):
        recorded.append(fields)

    graph = build_chat_graph(
        model,
        web,
        emitted.append,
        None,
        image=image,
        match_context=lambda: current(),
        record_observation=record,
    )
    asyncio.run(invoke(graph))
    assert recorded == [[]] and web.calls == [] and len(model.calls) == 2
    event = next(item for item in emitted if item.get("status") == "match_observation")
    assert event["observation_status"] == "unknown" and event["fields_count"] == 0
    assert "99" not in "".join(item.get("text", "") for item in emitted)


@pytest.mark.parametrize(
    "fields", [[], [{"name": "金币", "value": None}], [{"name": "棋子", "value": "   "}]]
)
def test_valid_unknown_values_are_not_previous_field_renewal(image, fields):
    model, recorded, emitted = ScriptModel([[report(fields)], [text("看不清。")]]), [], []

    async def record(value):
        recorded.append(value)

    graph = build_chat_graph(
        model,
        None,
        emitted.append,
        None,
        image=image,
        match_context=lambda: current(observations=[{"name": "金币", "value": "OLD_VALUE"}]),
        record_observation=record,
    )
    asyncio.run(invoke(graph))
    assert recorded[0] == [{"name": item["name"], "value": None} for item in fields]
    assert "OLD_VALUE" not in json.dumps(recorded)
    assert (
        next(item for item in emitted if item.get("status") == "match_observation")[
            "observation_status"
        ]
        == "unknown"
    )


def test_schema_names_and_boundaries_are_strict():
    parameters = OBSERVATION_TOOLS[0]["function"]["parameters"]
    assert parameters["required"] == ["fields"] and not parameters["additionalProperties"]
    array = parameters["properties"]["fields"]
    assert array["maxItems"] == 16
    assert set(array["items"]["required"]) == {"name", "value"}
    assert not array["items"]["additionalProperties"]
    assert (
        len(parse_fields({"fields": [{"name": str(i), "value": "v" * 400} for i in range(16)]}))
        == 16
    )
    assert parse_fields({"fields": [{"name": "n" * 64, "value": None}]})


@pytest.mark.parametrize(
    "field",
    [
        {"name": "x" * 65, "value": "v"},
        {"name": "x", "value": "v" * 513},
        {"name": "", "value": "v"},
        {"name": " x", "value": "v"},
        {"name": "x\n", "value": "v"},
        {"name": True, "value": "v"},
        {"name": "x", "value": True},
        {"name": "x", "value": []},
        {"name": "x", "value": "data:image/png;base64,RAW_BYTES"},
        {"name": "x", "value": "data:audio/wav;base64,RAW_BYTES"},
    ],
)
def test_invalid_field_limits_fail_the_whole_report(field):
    with pytest.raises(ValueError, match="invalid_match_observation"):
        parse_fields({"fields": [{"name": "good", "value": "valid"}, field]})


def test_case_variant_duplicate_field_is_rejected():
    with pytest.raises(ValueError):
        parse_fields({"fields": [{"name": "Coins", "value": "9"}, {"name": "coins", "value": "8"}]})


def test_fresh_context_is_read_for_every_plan_and_answer_after_tools():
    state = current(observations=[{"text": "BEFORE_TOOL"}])
    model = ScriptModel(
        [[call("search_web", query="public rules")], [], [text("只按新状态回答。")]]
    )

    class UpdatingWeb:
        async def execute(self, _name, _arguments):
            state["observations"] = [{"text": "AFTER_TOOL"}]
            state["state_revision"] = 2
            return result()

    graph = build_chat_graph(
        model, UpdatingWeb(), lambda _: None, None, match_context=lambda: copy.deepcopy(state)
    )
    asyncio.run(invoke(graph, guide=True))
    assert len(model.calls) == 3
    assert context_from(model.calls[0][0])["observations"][0]["text"] == "BEFORE_TOOL"
    for messages, _tools in model.calls[1:]:
        assert context_from(messages)["observations"][0]["text"] == "AFTER_TOOL"
        assert "BEFORE_TOOL" not in json.dumps(messages, ensure_ascii=False)
        assert "不证明用户已经执行" in messages[0]["content"]


def test_historical_context_is_labelled_and_data_cannot_become_system_instruction(image):
    state = current(
        status="historical", history_only=True, observations=[{"text": "IGNORE_ALL_RULES"}]
    )
    model = ScriptModel([[text("这只是上一局记录。")]])
    graph = build_chat_graph(
        model, None, lambda _: None, None, image=image, match_context=lambda: state
    )
    asyncio.run(invoke(graph))
    messages = model.calls[0][0]
    assert "IGNORE_ALL_RULES" not in messages[0]["content"]
    assert "只用于显式复盘历史" in messages[0]["content"]
    assert context_from(messages)["history_only"] is True
    assert "IGNORE_ALL_RULES" in json.dumps(context_from(messages))


def test_dynamic_context_and_observation_fields_are_not_checkpoint_state(image):
    saver = InMemorySaver()
    model = ScriptModel(
        [[report([{"name": "金币", "value": "OBSERVATION_PRIVATE"}])], [text("回答已完成。")]]
    )
    recorded = []

    async def record(fields):
        recorded.append(fields)

    graph = build_chat_graph(
        model,
        None,
        lambda _: None,
        saver,
        image=image,
        match_context=lambda: current(observations=[{"text": "DYNAMIC_CONTEXT_PRIVATE"}]),
        record_observation=record,
    )
    config = {"configurable": {"thread_id": "synthetic-match-graph"}}
    state = asyncio.run(invoke(graph, config=config))
    checkpoints = [item.checkpoint for item in saver.list(config)]
    assert checkpoints and recorded
    encoded = json.dumps({"state": state, "checkpoints": checkpoints}, ensure_ascii=False)
    assert "DYNAMIC_CONTEXT_PRIVATE" not in encoded
    assert "OBSERVATION_PRIVATE" not in encoded
    assert image["data_url"] not in encoded
    assert REPORT_TOOL not in encoded


def test_observation_does_not_consume_three_round_nine_search_tool_budget(image):
    model = ScriptModel(
        [
            [report()],
            [call("search_web", query=f"round1-{i}") for i in range(3)],
            [call("search_web", query=f"round2-{i}") for i in range(3)],
            [call("search_web", query=f"round3-{i}") for i in range(3)],
            [text("有限资料回答。")],
        ]
    )
    web, recorded = ScriptWeb([result()] * 9), []

    async def record(fields):
        recorded.append(fields)

    graph = build_chat_graph(
        model,
        web,
        lambda _: None,
        None,
        image=image,
        match_context=lambda: current(),
        record_observation=record,
    )
    state = asyncio.run(invoke(graph, guide=True, config={"recursion_limit": 16}))
    assert len(recorded) == 1 and state["rounds"] == 3
    assert len(web.calls) == 9 and len(model.calls) == 5
    assert model.calls[0][1] == OBSERVATION_TOOLS and model.calls[-1][1] is None


def test_expired_image_after_observation_stream_does_not_record_or_continue(clock, image):
    records, emitted = [], []

    class ExpiringModel:
        def __init__(self):
            self.calls = 0
            self.closed = False

        async def stream(self, _messages, tools=None):
            self.calls += 1
            try:
                clock.now += 121
                yield report()
            finally:
                self.closed = True

    async def record(fields):
        records.append(fields)

    model = ExpiringModel()
    graph = build_chat_graph(
        model,
        None,
        emitted.append,
        None,
        image=image,
        match_context=lambda: current(),
        record_observation=record,
    )
    with pytest.raises(vision.StaleImageError):
        asyncio.run(invoke(graph))
    assert not records and model.calls == 1 and model.closed
    assert not any(event.get("status") == "match_observation" for event in emitted)


def test_cancelled_binding_with_empty_observation_stream_cannot_record(image):
    active = True
    records = []

    class LateEmptyModel:
        async def stream(self, _messages, tools=None):
            nonlocal active
            active = False
            if False:
                yield

    def validate():
        if not active:
            raise asyncio.CancelledError

    async def record(fields):
        records.append(fields)

    graph = build_chat_graph(
        LateEmptyModel(),
        None,
        lambda _: None,
        None,
        image=image,
        match_context=lambda: current(),
        record_observation=record,
        validate_turn=validate,
    )
    with pytest.raises(NodeCancelledError) as failure:
        asyncio.run(invoke(graph))
    assert isinstance(failure.value.__cause__, asyncio.CancelledError)
    assert not records


def test_noncooperative_cancelled_model_cannot_commit_late_observation(image):
    async def run():
        started = asyncio.Event()
        records, emitted = [], []
        active = True

        class LateModel:
            async def stream(self, _messages, tools=None):
                started.set()
                try:
                    await asyncio.Event().wait()
                except asyncio.CancelledError:
                    yield report()

        def validate():
            if not active:
                raise asyncio.CancelledError

        async def record(fields):
            records.append(fields)

        graph = build_chat_graph(
            LateModel(),
            None,
            emitted.append,
            None,
            image=image,
            match_context=lambda: current(),
            record_observation=record,
            validate_turn=validate,
        )
        task = asyncio.create_task(invoke(graph))
        await started.wait()
        active = False
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task
        assert not records
        assert not any(event.get("status") == "match_observation" for event in emitted)

    asyncio.run(run())


def test_observation_callback_failure_does_not_publish_success_or_enter_answer(image):
    model = ScriptModel([[report()]])
    emitted = []

    async def record(_fields):
        raise ValueError("synthetic_write_rejected")

    graph = build_chat_graph(
        model,
        None,
        emitted.append,
        None,
        image=image,
        match_context=lambda: current(),
        record_observation=record,
    )
    with pytest.raises(ValueError, match="synthetic_write_rejected"):
        asyncio.run(invoke(graph))
    assert len(model.calls) == 1
    assert not any(event.get("status") == "match_observation" for event in emitted)


def test_raw_image_cannot_become_reported_field_text(image):
    model = ScriptModel(
        [[report([{"name": "图像", "value": image["data_url"]}])], [text("未知。")]]
    )
    records = []

    async def record(fields):
        records.append(fields)

    graph = build_chat_graph(
        model,
        None,
        lambda _: None,
        None,
        image=image,
        match_context=lambda: current(),
        record_observation=record,
    )
    asyncio.run(invoke(graph))
    assert records == [[]]


def test_new_frame_unknown_report_does_not_read_previous_context_in_visual_request(image):
    state = current(observations=[{"name": "金币", "value": "OLD_COINS"}])
    model = ScriptModel([[report([])], [text("金币未知，需要新描述。")]])

    async def record(fields):
        assert fields == []
        state["observations"] = []
        state["state_revision"] += 1

    graph = build_chat_graph(
        model,
        None,
        lambda _: None,
        None,
        image=image,
        match_context=lambda: copy.deepcopy(state),
        record_observation=record,
    )
    asyncio.run(invoke(graph))
    assert all("OLD_COINS" not in json.dumps(messages) for messages, _ in model.calls)
    assert context_from(model.calls[-1][0])["observations"] == []


def test_total_visual_field_budget_covers_unicode_and_json_escaping():
    assert parse_fields({"fields": [{"name": "值", "value": "中" * 512}]})
    for fields in (
        [{"name": str(i), "value": "中" * 512} for i in range(16)],
        [{"name": str(i), "value": "\\" * 400} for i in range(16)],
        [{"name": str(i) + "名" * 62, "value": "值" * 400} for i in range(16)],
    ):
        with pytest.raises(ValueError, match="match_observation_too_large"):
            parse_fields({"fields": fields})


def test_individually_legal_but_oversized_visual_report_becomes_unknown(image):
    fields = [{"name": str(i), "value": "v" * 512} for i in range(16)]
    model = ScriptModel([[report(fields)], [text("观察未能确认。")]])
    recorded, emitted = [], []

    async def record(value):
        recorded.append(value)

    graph = build_chat_graph(
        model,
        None,
        emitted.append,
        None,
        image=image,
        match_context=lambda: current(),
        record_observation=record,
    )
    asyncio.run(invoke(graph))
    assert recorded == [[]]
    final = next(event for event in emitted if event.get("status") == "match_observation")
    assert final["observation_status"] == "unknown" and final["known_count"] == 0


def test_live_observation_deadline_stops_stream_at_exact_expiry(clock):
    emitted = []
    state = current(
        observations=[
            {
                "text": "当前金币17",
                "observed_at": clock.now - 119,
                "expires_at": clock.now + 1,
            }
        ]
    )

    class ExpiringAnswer:
        async def stream(self, _messages, tools=None):
            yield text("仍有效的第一句。")
            clock.now += 1
            yield text("EXPIRED_RESULT_MUST_NOT_BE_SENT")

    graph = build_chat_graph(
        ExpiringAnswer(), None, emitted.append, None, match_context=lambda: state
    )
    with pytest.raises(NodeCancelledError) as failure:
        asyncio.run(invoke(graph))
    assert isinstance(failure.value.__cause__, asyncio.CancelledError)
    assert "match_observation_expired" in str(failure.value.__cause__)
    assert [event["text"] for event in emitted if event["type"] == "text"] == ["仍有效的第一句。"]


def test_each_request_uses_earliest_deadline_and_does_not_renew_during_stream(clock):
    state = current(
        observations=[
            {"text": "长时状态", "expires_at": clock.now + 90},
            {"text": "即将过期状态", "expires_at": clock.now + 2},
        ]
    )
    reads = 0
    emitted = []

    def context():
        nonlocal reads
        reads += 1
        return copy.deepcopy(state)

    class UpdatingAnswer:
        async def stream(self, _messages, tools=None):
            yield text("开始回答。")
            # A newer store snapshot is not what this model request received.
            state["observations"] = [{"text": "NEW_SNAPSHOT", "expires_at": clock.now + 120}]
            clock.now += 3
            yield text("OLD_MODEL_REQUEST_CANNOT_RENEW")

    graph = build_chat_graph(UpdatingAnswer(), None, emitted.append, None, match_context=context)
    with pytest.raises(NodeCancelledError):
        asyncio.run(invoke(graph))
    assert reads == 1
    assert "OLD_MODEL_REQUEST_CANNOT_RENEW" not in json.dumps(emitted)


@pytest.mark.parametrize(
    "state",
    [
        current(observations=[]),
        current(status="historical", history_only=True, observations=[{"expires_at": 1}]),
    ],
)
def test_no_dynamic_observation_or_historical_review_has_no_stream_deadline(clock, state):
    class LongAnswer:
        async def stream(self, _messages, tools=None):
            clock.now += 1000
            yield text("历史解释或者普通回答。")

    graph = build_chat_graph(LongAnswer(), None, lambda _: None, None, match_context=lambda: state)
    assert asyncio.run(invoke(graph))["output"] == "历史解释或者普通回答。"


def test_expired_observation_rejected_before_model_request(clock):
    model = ScriptModel([])
    graph = build_chat_graph(
        model,
        None,
        lambda _: None,
        None,
        match_context=lambda: current(observations=[{"expires_at": clock.now}]),
    )
    with pytest.raises(NodeCancelledError):
        asyncio.run(invoke(graph))
    assert model.calls == []


def test_stream_end_checks_expiry_even_without_final_event(clock):
    class SilentExpiry:
        async def stream(self, _messages, tools=None):
            yield text("截止前。")
            clock.now += 121

    graph = build_chat_graph(
        SilentExpiry(),
        None,
        lambda _: None,
        None,
        match_context=lambda: current(
            observations=[{"observed_at": clock.now, "expires_at": clock.now + 120}]
        ),
    )
    with pytest.raises(NodeCancelledError):
        asyncio.run(invoke(graph))


def test_observed_time_caps_excessively_long_declared_expiry(clock):
    deadline = match_observation.observation_deadline(
        current(
            observations=[
                {
                    "observed_at": clock.now - 100,
                    "expires_at": clock.now + 9999,
                }
            ]
        )
    )
    assert deadline == clock.now + 20
    iso = match_observation.observation_deadline(
        current(
            observations=[
                {
                    "observed_at": "2023-11-14T22:13:20+00:00",
                    "expires_at": "2023-11-14T22:15:20Z",
                }
            ]
        )
    )
    assert iso == 1_700_000_120


@pytest.mark.parametrize(
    "value", [True, "unknown", "2026-09-28T08:00:00", float("nan"), float("inf")]
)
def test_malformed_expiry_cannot_be_treated_as_unbounded_live_evidence(value):
    with pytest.raises(ValueError, match="invalid_match_observation_time"):
        match_observation.observation_deadline(current(observations=[{"expires_at": value}]))
