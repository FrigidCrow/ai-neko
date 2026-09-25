"""Conversation journal/real-process recovery tests; all providers are synthetic."""

import asyncio
import json
import os
import subprocess
import sys
from pathlib import Path
from uuid import uuid4

import pytest

from ai_neko.config.paths import initialize_data_root
from ai_neko.runtime import (
    RuntimeAccessError,
    RuntimeConflictError,
    RuntimeInputError,
    SessionRuntime,
)


class Model:
    def __init__(self, chunks=None, gate=None, *, fail=False, late=False):
        self.chunks = chunks or ["你好", "，继续聊吧。"]
        self.gate = gate
        self.fail = fail
        self.late = late
        self.messages = []

    async def stream(self, messages, tools=None):
        self.messages.append(messages)
        yield {"type": "text", "text": self.chunks[0]}
        if self.gate:
            try:
                await self.gate.wait()
            except asyncio.CancelledError:
                if not self.late:
                    raise
                yield {"type": "text", "text": "LATE_RESULT"}
                return
        if self.fail:
            raise RuntimeError("SECRET API KEY should never persist")
        for chunk in self.chunks[1:]:
            yield {"type": "text", "text": chunk}


class Store:
    def __init__(self, model=None):
        self.adapter = model or Model()

    def model(self):
        return self.adapter

    def web_tools(self):
        raise AssertionError("Plain chat must not require a search configuration")


async def settled(runtime, sid, tid):
    for _ in range(400):
        if runtime.get_session(sid)["turns"][-1]["status"] not in {"accepted", "running"}:
            return
        await asyncio.sleep(0.005)
    raise AssertionError("turn did not settle")


async def text_ready(runtime, sid, tid):
    for _ in range(400):
        batch = runtime.events(sid, tid)
        if any(e["type"] == "text" for e in batch["events"]):
            return batch
        await asyncio.sleep(0.005)
    raise AssertionError("text did not arrive")


def paths(tmp_path):
    return initialize_data_root(tmp_path / "中文 space app")


def test_completed_delivery_and_ack_are_distinct_and_persistent(tmp_path):
    data = paths(tmp_path)

    async def run():
        runtime = SessionRuntime(data, Store())
        sid = runtime.create_session()["id"]
        turn = await runtime.start_turn(sid, "你好")
        tid = turn["turn_id"]
        await settled(runtime, sid, tid)
        before = runtime.get_session(sid)["turns"][0]
        assert before["status"] == "completed" and before["delivered_text"] == ""
        batch = runtime.events(sid, tid)
        sent = runtime.get_session(sid)["turns"][0]
        assert sent["delivered_text"] == "你好，继续聊吧。" and sent["confirmed_text"] == ""
        runtime.ack(sid, tid, batch["last_seq"])
        await runtime.close()
        reopened = SessionRuntime(data, Store())
        recovered = reopened.get_session(sid)["turns"][0]
        assert recovered["confirmed_text"] == sent["delivered_text"]
        assert recovered["status"] == "completed"
        assert len(reopened.list_sessions()) == 1
        await reopened.close()

    asyncio.run(run())
    assert (data.checkpoints / "chat-graph.sqlite").is_file()


@pytest.mark.parametrize("stage", ["accepted", "streaming", "completed"])
def test_cancel_before_during_after_completion_is_idempotent(tmp_path, stage):
    async def run():
        gate = asyncio.Event() if stage != "completed" else None
        runtime = SessionRuntime(paths(tmp_path), Store(Model(gate=gate)))
        sid = runtime.create_session()["id"]
        tid = (await runtime.start_turn(sid, "持久输入"))["turn_id"]
        if stage == "streaming":
            batch = await text_ready(runtime, sid, tid)
            text_seq = next(e["seq"] for e in batch["events"] if e["type"] == "text")
            runtime.ack(sid, tid, text_seq)
        elif stage == "completed":
            await settled(runtime, sid, tid)
        await runtime.cancel_turn(sid, tid)
        await runtime.cancel_turn(sid, tid)
        await asyncio.sleep(0.01)
        final = runtime.get_session(sid)["turns"][0]
        assert final["status"] == ("completed" if stage == "completed" else "cancelled")
        assert final["input"] == "持久输入"
        if stage == "streaming":
            assert final["confirmed_text"] == "你好"
        events = runtime.events(sid, tid)["events"]
        assert len([e for e in events if e["type"] == "done"]) == 1
        await runtime.close()

    asyncio.run(run())


def test_cancel_discards_late_generation_and_does_not_cross_turns(tmp_path):
    async def run():
        runtime = SessionRuntime(paths(tmp_path), Store(Model(gate=asyncio.Event(), late=True)))
        sid = runtime.create_session()["id"]
        first = (await runtime.start_turn(sid, "one"))["turn_id"]
        await text_ready(runtime, sid, first)
        await runtime.cancel_turn(sid, first)
        runtime.providers.adapter = Model(["NEW", " TURN"])
        second = (await runtime.start_turn(sid, "two"))["turn_id"]
        await settled(runtime, sid, second)
        assert "LATE_RESULT" not in json.dumps(runtime.events(sid, first))
        assert "LATE_RESULT" not in json.dumps(runtime.events(sid, second))
        assert runtime.get_session(sid)["turns"][1]["delivered_text"] == "NEW TURN"
        await runtime.close()

    asyncio.run(run())


def test_active_turn_serialization_and_retry_keys(tmp_path):
    async def run():
        runtime = SessionRuntime(paths(tmp_path), Store(Model(gate=asyncio.Event())))
        sid = runtime.create_session()["id"]
        rid = uuid4().hex
        first = await runtime.start_turn(sid, "same", request_id=rid)
        duplicate = await runtime.start_turn(sid, "same", request_id=rid)
        assert first["id"] == duplicate["id"]
        with pytest.raises(RuntimeConflictError):
            await runtime.start_turn(sid, "different", request_id=rid)
        with pytest.raises(RuntimeConflictError):
            await runtime.start_turn(sid, "second")
        await runtime.cancel_turn(sid, first["id"])
        again = await runtime.start_turn(sid, "same", request_id=rid)
        assert again["status"] == "cancelled"
        assert len(runtime.get_session(sid)["turns"]) == 1
        await runtime.close()

    asyncio.run(run())


def test_opaque_handles_scope_and_data_root_isolation(tmp_path):
    async def run():
        left = SessionRuntime(
            initialize_data_root(tmp_path / "left"), Store(Model(gate=asyncio.Event()))
        )
        right = SessionRuntime(initialize_data_root(tmp_path / "right"), Store())
        a, b = left.create_session()["id"], left.create_session()["id"]
        tid = (await left.start_turn(a, "private"))["id"]
        for handle in ["../conversation.sqlite", "not-a-session", a]:
            with pytest.raises(RuntimeAccessError):
                right.get_session(handle)
        with pytest.raises(RuntimeAccessError):
            left.events(b, tid)
        with pytest.raises(RuntimeAccessError):
            await left.cancel_turn(b, tid)
        with pytest.raises(RuntimeAccessError):
            left.ack(b, tid, 0)
        assert "internal_id" not in json.dumps(left.get_session(a))
        with left._db:
            left._db.execute("UPDATE sessions SET character_id='foreign' WHERE id=?", (b,))
        with pytest.raises(RuntimeAccessError):
            left.get_session(b)
        assert b not in [s["id"] for s in left.list_sessions()]
        await left.close()
        await right.close()

    asyncio.run(run())


def test_ack_cannot_claim_untransmitted_content_or_move_backward(tmp_path):
    async def run():
        runtime = SessionRuntime(paths(tmp_path), Store())
        sid = runtime.create_session()["id"]
        tid = (await runtime.start_turn(sid, "hello"))["id"]
        await settled(runtime, sid, tid)
        with pytest.raises(RuntimeInputError, match="不能跳过"):
            runtime.events(sid, tid, 1)
        for value in [1, True, -1, 10000]:
            with pytest.raises(RuntimeInputError):
                runtime.ack(sid, tid, value)
        batch = runtime.events(sid, tid)
        runtime.ack(sid, tid, batch["last_seq"])
        runtime.ack(sid, tid, 0)
        assert runtime.get_session(sid)["turns"][0]["ack_seq"] == batch["last_seq"]
        with pytest.raises(RuntimeInputError):
            runtime.events(sid, tid, batch["last_seq"] + 1)
        await runtime.close()

    asyncio.run(run())


def test_failure_settles_once_without_persisting_raw_provider_exception(tmp_path):
    async def run():
        runtime = SessionRuntime(paths(tmp_path), Store(Model(fail=True)))
        sid = runtime.create_session()["id"]
        tid = (await runtime.start_turn(sid, "hello"))["id"]
        await settled(runtime, sid, tid)
        batch = runtime.events(sid, tid)
        assert batch["status"] == "error"
        assert "SECRET" not in json.dumps(batch)
        assert len([e for e in batch["events"] if e["type"] == "done"]) == 1
        await runtime.close()

    asyncio.run(run())


def test_last_ten_rounds_reach_next_model_but_unacknowledged_cancelled_text_does_not(tmp_path):
    async def run():
        store = Store()
        runtime = SessionRuntime(paths(tmp_path), store)
        sid = runtime.create_session()["id"]
        for index in range(11):
            tid = (await runtime.start_turn(sid, f"message-{index}"))["id"]
            await settled(runtime, sid, tid)
            batch = runtime.events(sid, tid)
            runtime.ack(sid, tid, batch["last_seq"])
        tid = (await runtime.start_turn(sid, "twelve"))["id"]
        await settled(runtime, sid, tid)
        last = store.adapter.messages[-1]
        assert len([m for m in last if m["role"] == "user"]) == 11
        assert not any(m.get("content") == "message-0" for m in last)
        assert any(m.get("content") == "message-1" for m in last)
        await runtime.close()

    asyncio.run(run())


def test_exclusive_runtime_lock_and_invalid_input(tmp_path):
    async def run():
        data = paths(tmp_path)
        runtime = SessionRuntime(data, Store())
        with pytest.raises(RuntimeConflictError):
            SessionRuntime(data, Store())
        sid = runtime.create_session()["id"]
        for value in [None, "", "   ", "a" * 8001]:
            with pytest.raises(RuntimeInputError):
                await runtime.start_turn(sid, value)
        with pytest.raises(RuntimeInputError):
            await runtime.start_turn(sid, "hello", guide="true")
        await runtime.close()

    asyncio.run(run())


def test_real_process_crash_preserves_input_and_confirmed_prefix_without_network_replay(tmp_path):
    data_dir = tmp_path / "crash 中文"
    program = r"""
import asyncio, json, os, sys
from pathlib import Path
from ai_neko.config.paths import initialize_data_root
from ai_neko.runtime import SessionRuntime
class Model:
    async def stream(self, messages, tools=None):
        yield {"type":"text", "text":"CONFIRMED"}
        await asyncio.Event().wait()
class Store:
    def model(self): return Model()
async def main():
    runtime=SessionRuntime(initialize_data_root(Path(sys.argv[1])), Store())
    sid=runtime.create_session()["id"]
    tid=(await runtime.start_turn(sid,"ACCEPTED INPUT"))["id"]
    for _ in range(1000):
        events=runtime.events(sid,tid)["events"]
        if any(e["type"]=="text" for e in events): break
        await asyncio.sleep(.005)
    else: raise RuntimeError("no child text")
    seq=next(e["seq"] for e in events if e["type"]=="text")
    runtime.ack(sid,tid,seq)
    runtime._emit(sid,tid,{"type":"text","text":"UNSENT DRAFT"})
    print(json.dumps({"sid":sid,"tid":tid}),flush=True)
    os._exit(86)
asyncio.run(main())
"""
    env = {
        name: value
        for name, value in os.environ.items()
        if name.upper()
        in {"PATH", "SYSTEMROOT", "WINDIR", "TEMP", "TMP", "HOME", "USERPROFILE", "LOCALAPPDATA"}
    }
    env["PYTHONPATH"] = str(Path(__file__).resolve().parents[1] / "src")
    completed = subprocess.run(
        [sys.executable, "-c", program, str(data_dir)],
        capture_output=True,
        text=True,
        env=env,
        timeout=20,
    )
    assert completed.returncode == 86, completed.stderr
    ids = json.loads(completed.stdout.strip().splitlines()[-1])

    class NeverReplay:
        def model(self):
            raise AssertionError("crash recovery must never call a provider")

    async def run():
        runtime = SessionRuntime(initialize_data_root(data_dir), NeverReplay())
        turn = runtime.get_session(ids["sid"])["turns"][0]
        assert turn["status"] == "interrupted" and turn["input"] == "ACCEPTED INPUT"
        assert turn["confirmed_text"] == "CONFIRMED" and turn["delivered_text"] == "CONFIRMED"
        # A reconnect can never promote the previously unsent draft into a reply.
        assert turn["ack_seq"] < turn["last_seq"]
        assert len(runtime.get_session(ids["sid"])["turns"]) == 1
        await runtime.close()
        again = SessionRuntime(initialize_data_root(data_dir), NeverReplay())
        events = again.events(ids["sid"], ids["tid"])["events"]
        assert "UNSENT DRAFT" not in json.dumps(events)
        assert len([e for e in events if e["type"] == "done"]) == 1
        await again.close()

    asyncio.run(run())


def test_cancel_requested_during_sqlite_commit_preserves_first_terminal_state(tmp_path):
    async def run():
        runtime = SessionRuntime(paths(tmp_path), Store())
        sid = runtime.create_session()["id"]
        gate = asyncio.Event()
        runtime.providers.adapter = Model(gate=gate)
        tid = (await runtime.start_turn(sid, "commit race"))["id"]
        await text_ready(runtime, sid, tid)
        injected = []
        loop = asyncio.get_running_loop()

        def on_statement(statement):
            if "UPDATE turns SET status='completed'" in statement and not injected:
                injected.append(True)
                # Public cancellation arriving during a synchronous transaction is
                # queued until the atomic commit has completed, never half-applied.
                loop.call_soon(lambda: asyncio.create_task(runtime.cancel_turn(sid, tid)))

        runtime._db.set_trace_callback(on_statement)
        gate.set()
        await settled(runtime, sid, tid)
        await asyncio.sleep(0.02)
        runtime._db.set_trace_callback(None)
        assert injected
        assert runtime.get_session(sid)["turns"][0]["status"] == "completed"
        events = runtime.events(sid, tid)["events"]
        assert len([e for e in events if e["type"] == "done"]) == 1
        await runtime.close()

    asyncio.run(run())


def test_cancel_freezes_untransmitted_text_and_context_uses_only_confirmed_prefix(tmp_path):
    async def run():
        runtime = SessionRuntime(paths(tmp_path), Store(Model(gate=asyncio.Event())))
        sid = runtime.create_session()["id"]
        tid = (await runtime.start_turn(sid, "partial"))["id"]
        batch = await text_ready(runtime, sid, tid)
        runtime.ack(sid, tid, batch["last_seq"])
        runtime._emit(sid, tid, {"type": "text", "text": "UNSENT_SECRET"})
        await runtime.cancel_turn(sid, tid)
        events = runtime.events(sid, tid)
        assert "UNSENT_SECRET" not in json.dumps(events)
        runtime.ack(sid, tid, events["last_seq"])
        assert runtime.get_session(sid)["turns"][0]["confirmed_text"] == "你好"
        model = Model()
        runtime.providers.adapter = model
        next_id = (await runtime.start_turn(sid, "next"))["id"]
        await settled(runtime, sid, next_id)
        assert {"role": "assistant", "content": "你好"} in model.messages[0]
        assert "UNSENT_SECRET" not in json.dumps(model.messages)
        await runtime.close()

    asyncio.run(run())


@pytest.mark.parametrize(
    "code,expected",
    [
        ("model_key_missing", "模型 API Key"),
        ("search_key_missing", "搜索 API Key"),
        ("authentication_failed", "鉴权失败"),
        ("rate_limited", "受限"),
        ("timeout", "超时"),
        ("network_error", "网络"),
        ("blocked_endpoint", "地址不可访问"),
        ("output_limit", "长度上限"),
        ("stream_interrupted", "连接中断"),
    ],
)
def test_provider_errors_have_specific_safe_user_messages(tmp_path, code, expected):
    from ai_neko.providers import ProviderError

    class FailingModel:
        async def stream(self, messages, tools=None):
            raise ProviderError(code, "SECRET from adapter must not be emitted")
            yield  # make this an async iterator

    async def run():
        runtime = SessionRuntime(paths(tmp_path), Store(FailingModel()))
        sid = runtime.create_session()["id"]
        tid = (await runtime.start_turn(sid, "test"))["id"]
        await settled(runtime, sid, tid)
        batch = runtime.events(sid, tid)
        error = next(e for e in batch["events"] if e["type"] == "error")
        assert error["code"] == code and expected in error["message"]
        assert "SECRET" not in json.dumps(batch)
        await runtime.close()

    asyncio.run(run())


def test_external_langsmith_environment_cannot_enable_chat_tracing(tmp_path, monkeypatch):
    from langsmith import utils

    class TraceCheckingModel(Model):
        async def stream(self, messages, tools=None):
            assert utils.tracing_is_enabled() is False
            yield {"type": "text", "text": "local trace disabled"}

    monkeypatch.setenv("LANGSMITH_TRACING", "true")
    monkeypatch.setenv("LANGCHAIN_TRACING_V2", "true")
    monkeypatch.setenv("LANGSMITH_API_KEY", "SYNTHETIC_NEVER_SEND")
    monkeypatch.setenv("LANGSMITH_ENDPOINT", "http://127.0.0.1:1")

    async def run():
        runtime = SessionRuntime(paths(tmp_path), Store(TraceCheckingModel()))
        sid = runtime.create_session()["id"]
        tid = (await runtime.start_turn(sid, "hello"))["id"]
        await settled(runtime, sid, tid)
        assert runtime.events(sid, tid)["status"] == "completed"
        await runtime.close()

    asyncio.run(run())


@pytest.mark.parametrize("second_guide", [False, True])
def test_source_ids_from_previous_turn_cannot_be_reassigned_to_new_sources(tmp_path, second_guide):
    class GuideModel:
        def __init__(self, url):
            self.url = url
            self.planned = False
            self.calls = []

        async def stream(self, messages, tools=None):
            self.calls.append(messages)
            if tools:
                if not self.planned:
                    self.planned = True
                    yield {
                        "type": "tool_call",
                        "id": "provider-id",
                        "name": "read_web_page",
                        "arguments": {"url": self.url},
                    }
            else:
                yield {"type": "text", "text": "本轮攻略 [S1]"}

    class Web:
        async def execute(self, name, arguments):
            return {
                "status": "ok",
                "sources": [
                    {
                        "url": arguments["url"],
                        "status": "read",
                        "title": "version guide",
                        "text": "read evidence",
                    }
                ],
            }

    class GuideStore:
        def __init__(self):
            self.adapter = GuideModel("https://example.com/old-version")

        def model(self):
            return self.adapter

        def web_tools(self):
            return Web()

    async def run():
        store = GuideStore()
        runtime = SessionRuntime(paths(tmp_path), store)
        sid = runtime.create_session()["id"]
        first = (await runtime.start_turn(sid, "旧版", guide=True))["id"]
        await settled(runtime, sid, first)
        first_batch = runtime.events(sid, first)
        runtime.ack(sid, first, first_batch["last_seq"])
        assert runtime.get_session(sid)["turns"][0]["sources"][0]["id"] == "S1"
        store.adapter = GuideModel("https://example.com/new-version")
        second = (await runtime.start_turn(sid, "新版", guide=second_guide))["id"]
        await settled(runtime, sid, second)
        second_batch = runtime.events(sid, second)
        history = [m["content"] for m in store.adapter.calls[-1] if m["role"] == "assistant"]
        assert history == ["本轮攻略 [历史来源：需重新检索]"]
        second_turn = runtime.get_session(sid)["turns"][1]
        if second_guide:
            assert second_turn["sources"][0]["url"] == "https://example.com/new-version"
            assert second_turn["delivered_text"] == "本轮攻略 [S1]"
        else:
            assert second_turn["sources"] == []
            assert second_turn["delivered_text"] == "本轮攻略 [来源未核实]"
            assert any(e.get("status") == "citation_rejected" for e in second_batch["events"])
        await runtime.close()

    asyncio.run(run())
