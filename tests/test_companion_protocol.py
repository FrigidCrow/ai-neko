"""Model protocol and media lifecycle evidence, independent of cloud accounts."""

import asyncio
import json

import pytest
from test_chat import ScriptModel, ScriptWeb, call, result, run_graph, text
from test_providers import adapter, collect, delta, event, provider_http  # noqa: F401
from test_runtime import Store, settled

from ai_neko.config.paths import initialize_data_root
from ai_neko.runtime import RuntimeConflictError, SessionRuntime


def test_reasoning_protocol_is_preserved_without_becoming_answer_or_checkpoint():
    secret = "SYNTHETIC_PROTOCOL_CONTEXT_NOT_DISPLAYABLE"
    model = ScriptModel(
        [
            [
                {"type": "assistant_context", "reasoning_content": secret},
                call("search_web", query="game"),
            ],
            [],
            [text("资料不足。")],
        ]
    )
    state, emitted, _ = run_graph(model, ScriptWeb([result()]))
    tool_messages = model.calls[1][0]
    assistant = next(message for message in tool_messages if message["role"] == "assistant")
    assert assistant["reasoning_content"] == secret
    assert secret not in json.dumps(state)
    assert secret not in json.dumps(emitted)
    assert secret not in json.dumps(model.calls[-1])


def test_adapter_keeps_fragmented_reasoning_only_as_internal_context(provider_http):  # noqa: F811
    provider_http(
        [
            delta({"reasoning_content": "PROTOCOL_"}),
            delta({"reasoning_content": "CONTEXT"}),
            delta(
                {
                    "tool_calls": [
                        {
                            "index": 0,
                            "id": "id1",
                            "function": {"name": "search_web", "arguments": '{"query":"x"}'},
                        }
                    ]
                }
            ),
            event("[DONE]"),
        ]
    )
    response = collect(adapter(), tools=[{"type": "function"}])
    assert response[0] == {"type": "assistant_context", "reasoning_content": "PROTOCOL_CONTEXT"}
    assert response[1]["type"] == "tool_call"
    assert not any(item["type"] == "text" for item in response)


def test_actual_playback_ack_is_independent_of_text_delivery_and_recovers(tmp_path, monkeypatch):
    paths = initialize_data_root(tmp_path / "playback")

    async def run():
        runtime = SessionRuntime(paths, Store())
        sid = runtime.create_session()["id"]
        tid = (await runtime.start_turn(sid, "你好"))["id"]
        await settled(runtime, sid, tid)
        first, second = "f" * 32, "a" * 32
        # Clock readings need not be unique. Equal timestamps sort by ID, so
        # identify receipts by their stable segment ID rather than list order.
        with monkeypatch.context() as clock:
            clock.setattr("ai_neko.runtime.service.time.time", lambda: 1_000_000_000)
            with pytest.raises(RuntimeConflictError):
                runtime.audio_ack(sid, tid, first, "completed")
            runtime.audio_ack(sid, tid, first, "started")
            runtime.audio_ack(sid, tid, first, "completed")
            assert runtime.audio_ack(sid, tid, first, "stopped")["state"] == "completed"
            runtime.audio_ack(sid, tid, second, "started")
        row = runtime.get_session(sid)["turns"][0]
        assert row["ack_seq"] == 0 and row["confirmed_text"] == ""
        assert {item["segment_id"]: item["state"] for item in row["audio_playback"]} == {
            first: "completed",
            second: "started",
        }
        await runtime.close()
        reopened = SessionRuntime(paths, Store())
        assert {
            item["segment_id"]: item["state"]
            for item in reopened.get_session(sid)["turns"][0]["audio_playback"]
        } == {first: "completed", second: "interrupted"}
        await reopened.close()

    asyncio.run(run())


def test_deepseek_official_chat_uses_documented_output_limit_field(provider_http):  # noqa: F811
    requests = []
    provider_http([delta({"content": "synthetic"}), event("[DONE]")], inspect=requests.append)
    collect(adapter(base="https://api.deepseek.com"))
    payload = json.loads(requests[0].content)
    assert payload["max_tokens"] == 4096 and "max_completion_tokens" not in payload


def test_search_enabled_greeting_neither_fetches_nor_injects_empty_evidence():
    model = ScriptModel([[], [text("你好，今天想聊什么？")]])
    web = ScriptWeb([])
    state, events, _ = run_graph(model, web, guide=True, question="你好")
    assert not web.calls
    assert model.calls[0][1]
    assert model.calls[-1][1] is None
    assert model.calls[-1][0][-1]["content"] == "你好"
    assert not any(event["type"] == "tool" for event in events)
    assert not any(event.get("status") == "researching" for event in events)


def test_missing_search_configuration_answers_without_futile_retry():
    model = ScriptModel(
        [[call("search_web", query="最新游戏规则")], [text("需要先配置搜索服务。")]]
    )
    web = ScriptWeb([result(status="error", error="search_key_missing")])
    state, events, _ = run_graph(model, web)
    assert len(web.calls) == 1 and len(model.calls) == 2
    assert "search_key_missing" in model.calls[-1][0][-1]["content"]
    assert any(event.get("status") == "researching" for event in events)
    assert any(event.get("error") == "search_key_missing" for event in events)
    assert state["output"] == "需要先配置搜索服务。"
