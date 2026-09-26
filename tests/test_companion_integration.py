"""Cross-layer companion checks with synthetic images and model responses only."""

import asyncio
import base64
import json
import sqlite3
import time
from uuid import uuid4

import pytest
from test_runtime import Model, Store, settled

from ai_neko.chat.extraction import extract_facts
from ai_neko.chat.vision import validate_image
from ai_neko.config.paths import initialize_data_root
from ai_neko.runtime import RuntimeConflictError, RuntimeInputError, SessionRuntime


def image():
    return {
        "frame_id": uuid4().hex,
        "source_id": "window:synthetic:0",
        "source_name": "Synthetic game window",
        "captured_at": time.time() * 1000,
        "data_url": "data:image/png;base64,"
        + base64.b64encode(b"\x89PNG\r\n\x1a\nSYNTHETIC_FRAME_DO_NOT_PERSIST").decode(),
    }


def test_persona_memory_image_reach_model_but_not_execution_checkpoint(tmp_path):
    paths = initialize_data_root(tmp_path / "companion")
    store = Store()
    picture = image()

    async def run():
        runtime = SessionRuntime(paths, store)
        runtime.memory.update_persona({"name": "雪团", "user_name": "旅行者"})
        runtime.memory.remember("喜欢青柠汽水", source_id="manual:preference", kind="preference")
        sid = runtime.create_session()["id"]
        tid = (await runtime.start_turn(sid, "我喜欢喝什么？看看这一帧", image=picture))["id"]
        await settled(runtime, sid, tid)
        messages = store.adapter.messages[-1]
        assert "雪团" in messages[0]["content"]
        assert "青柠汽水" in messages[0]["content"]
        assert messages[-1]["content"][1]["image_url"]["url"] == picture["data_url"]
        assert str(picture["source_id"]) in messages[0]["content"]
        runtime.events(sid, tid)
        await runtime.close()
        for path in [paths.checkpoints / "chat-graph.sqlite", paths.memory / "conversation.sqlite"]:
            with sqlite3.connect(path) as database:
                dump = "\n".join(database.iterdump())
            assert "青柠汽水" not in dump
            assert picture["data_url"] not in dump
            assert "雪团" not in dump
        reopened = SessionRuntime(paths, Store())
        second = reopened.create_session()["id"]
        tid = (await reopened.start_turn(second, "我喜欢喝什么？"))["id"]
        await settled(reopened, second, tid)
        assert "青柠汽水" in reopened.providers.adapter.messages[-1][0]["content"]
        await reopened.close()

    asyncio.run(run())


def test_forgetting_removes_sources_recalled_answers_and_later_context(tmp_path):
    paths = initialize_data_root(tmp_path / "forget")
    secret = "只喜欢柚子汽水EXACT_ERASURE_MARKER"

    async def run():
        runtime = SessionRuntime(paths, Store(Model([secret])))
        sid = runtime.create_session()["id"]
        tid = (await runtime.start_turn(sid, secret))["id"]
        await settled(runtime, sid, tid)
        runtime.events(sid, tid)
        fact = runtime.memory.remember(
            secret, source_id="turn:" + tid, source_text=secret, kind="preference"
        )
        other = runtime.create_session()["id"]
        tid2 = (await runtime.start_turn(other, "我的偏好是什么"))["id"]
        await settled(runtime, other, tid2)
        runtime.events(other, tid2)
        result = await runtime.forget_memory(fact["id"])
        assert fact["id"] in result["fact_ids"]
        assert not runtime.memory.list_facts()
        assert secret not in json.dumps(runtime.get_session(sid), ensure_ascii=False)
        assert secret not in json.dumps(runtime.get_session(other), ensure_ascii=False)
        with sqlite3.connect(paths.checkpoints / "chat-graph.sqlite") as db:
            assert not db.execute("SELECT count(*) FROM checkpoints").fetchone()[0]
        runtime.providers.adapter = Model(["不知道"])
        third = runtime.create_session()["id"]
        tid3 = (await runtime.start_turn(third, "我的偏好是什么"))["id"]
        await settled(runtime, third, tid3)
        assert secret not in json.dumps(runtime.providers.adapter.messages, ensure_ascii=False)
        await runtime.close()

    asyncio.run(run())


@pytest.mark.parametrize(
    "mutation",
    [
        {"captured_at": 1},
        {"captured_at": True},
        {"source_id": ""},
        {"data_url": "https://example.org/image.png"},
        {"data_url": "data:image/png;base64,AAAA"},
        {"captured_at": float("nan")},
    ],
)
def test_invalid_or_stale_image_is_rejected(mutation):
    with pytest.raises(ValueError):
        validate_image({**image(), **mutation})


def test_picture_retry_must_match_original(tmp_path):
    async def run():
        runtime = SessionRuntime(initialize_data_root(tmp_path / "retry"), Store())
        sid = runtime.create_session()["id"]
        request = uuid4().hex
        picture = image()
        first = await runtime.start_turn(sid, "图", request_id=request, image=picture)
        assert (await runtime.start_turn(sid, "图", request_id=request, image=picture))[
            "id"
        ] == first["id"]
        with pytest.raises(RuntimeConflictError):
            await runtime.start_turn(sid, "图", request_id=request, image=image())
        with pytest.raises(RuntimeInputError):
            await runtime.start_turn(sid, "图", image={**picture, "captured_at": 1})
        await runtime.close()

    asyncio.run(run())


def test_extraction_requires_literal_user_evidence():
    good = {
        "facts": [
            {"content": "喜欢茶", "quote": "喜欢茶", "fact_key": "drink", "kind": "preference"}
        ]
    }
    assert (
        asyncio.run(extract_facts(Model([json.dumps(good)]), "我喜欢茶"))[0]["content"] == "喜欢茶"
    )
    with pytest.raises(ValueError):
        asyncio.run(extract_facts(Model([json.dumps(good)]), "我没有说过我的偏好"))


def test_durable_automatic_extraction_resumes_in_new_runtime(tmp_path):
    paths = initialize_data_root(tmp_path / "jobs")
    result = {
        "facts": [
            {"content": "喜欢茶", "quote": "喜欢茶", "fact_key": "drink", "kind": "preference"}
        ]
    }

    async def run():
        runtime = SessionRuntime(paths, Store())
        runtime.memory_preferences.update({"auto_extract": True})
        runtime.memory.enqueue_extraction("turn:stable-source", "我喜欢茶")
        await runtime.close()
        reopened = SessionRuntime(paths, Store(Model([json.dumps(result)])))
        reopened.start_memory_worker()
        await reopened._memory_task
        assert reopened.memory.list_facts()[0]["content"] == "喜欢茶"
        reopened.start_memory_worker()
        await reopened._memory_task
        assert len(reopened.memory.list_facts()) == 1
        await reopened.close()

    asyncio.run(run())


def test_forgetting_one_fact_preserves_unrelated_turns_in_same_session(tmp_path):
    """Regression: one forgotten fact must not erase the whole conversation tail."""
    paths = initialize_data_root(tmp_path / "forget-scoped")
    secret = "只喜欢柚子汽水SCOPED_ERASURE_MARKER"

    async def run():
        runtime = SessionRuntime(paths, Store(Model(["好的，我记住了。"])))
        sid = runtime.create_session()["id"]
        turn_before = (await runtime.start_turn(sid, "我喜欢喝什么"))["id"]
        await settled(runtime, sid, turn_before)
        fact = runtime.memory.remember(
            secret, source_id="manual:scoped", source_text=secret, kind="preference"
        )
        # This turn actually recalls the fact into its context.
        turn_recalls = (await runtime.start_turn(sid, "我的偏好是什么"))["id"]
        await settled(runtime, sid, turn_recalls)
        # This turn neither recalls the fact nor quotes it.
        turn_unrelated = (await runtime.start_turn(sid, "太阳系有几大行星"))["id"]
        await settled(runtime, sid, turn_unrelated)
        runtime.events(sid, turn_recalls)
        runtime.events(sid, turn_unrelated)
        unrelated_text = runtime.get_session(sid)["turns"][2]["delivered_text"]
        assert unrelated_text and secret not in unrelated_text
        await runtime.forget_memory(fact["id"])
        turns = runtime.get_session(sid)["turns"]
        # Turns that referenced the fact are erased; the unrelated turn survives.
        assert turns[0]["input"] == "我喜欢喝什么"  # before the fact existed
        assert turns[1]["input"] == "[已遗忘的对话]"
        assert turns[2]["input"] == "太阳系有几大行星"
        assert turns[2]["delivered_text"] == unrelated_text
        assert secret not in json.dumps(turns[1], ensure_ascii=False)
        # Per-thread checkpoint cleanup: only the erased turn's record is gone.
        internal = runtime._db.execute(
            "SELECT internal_id FROM sessions WHERE id=?", (sid,)
        ).fetchone()[0]
        with sqlite3.connect(paths.checkpoints / "chat-graph.sqlite") as db:
            threads = {row[0] for row in db.execute("SELECT DISTINCT thread_id FROM checkpoints")}
        assert f"{internal}:{turn_recalls}" not in threads
        assert f"{internal}:{turn_unrelated}" in threads
        await runtime.close()

    asyncio.run(run())
