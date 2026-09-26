"""Actual playback receipts settle heard content separately from text display."""

import asyncio
import sqlite3
from contextlib import closing
from uuid import uuid4

import httpx
import pytest
from test_runtime import Model, Store, settled, text_ready

from ai_neko.app.server import Connection, create_app
from ai_neko.config.paths import initialize_data_root
from ai_neko.runtime import RuntimeConflictError, RuntimeInputError, SessionRuntime
from ai_neko.runtime.service import _speech_slice


@pytest.mark.parametrize("generating", [False, True])
def test_hidden_heard_prefix_survives_stop_restart_and_reaches_next_turn(tmp_path, generating):
    first = "先观察🙂。 https://example.com/guide \n"
    second = "第二句尚未听完。"
    paths = initialize_data_root(tmp_path / "heard")

    async def run():
        runtime = SessionRuntime(
            paths, Store(Model([first + second], gate=asyncio.Event() if generating else None))
        )
        sid = runtime.create_session()["id"]
        tid = (await runtime.start_turn(sid, "下一步怎么办？"))["id"]
        if generating:
            await text_ready(runtime, sid, tid)
        else:
            await settled(runtime, sid, tid)
            runtime.events(sid, tid)
        one, two = uuid4().hex, uuid4().hex
        runtime.audio_ack(sid, tid, one, "started", text_start=0, text_end=len(first))
        runtime.audio_ack(sid, tid, one, "completed")
        runtime.audio_ack(
            sid, tid, two, "started", text_start=len(first), text_end=len(first + second)
        )
        runtime.audio_ack(sid, tid, two, "stopped")
        # A delayed completion cannot turn a stopped sentence into heard content.
        assert runtime.audio_ack(sid, tid, two, "completed")["state"] == "stopped"
        if generating:
            await runtime.cancel_turn(sid, tid)
        row = runtime.get_session(sid)["turns"][0]
        assert row["ack_seq"] == 0 and row["confirmed_text"] == ""
        assert row["heard_text"] == "先观察🙂。"
        assert row["heard_characters"] == row["confirmed_characters"] == len(first)
        await runtime.close()
        store = Store()
        reopened = SessionRuntime(paths, store)
        try:
            following = (await reopened.start_turn(sid, "接着说。"))["id"]
            await settled(reopened, sid, following)
            heard = [m["content"] for m in store.adapter.messages[-1] if m["role"] == "assistant"]
            assert heard == ["先观察🙂。"]
            assert reopened.get_session(sid)["turns"][0]["heard_text"] == "先观察🙂。"
        finally:
            await reopened.close()

    asyncio.run(run())


def test_unplayed_gap_blocks_prefix_and_display_confirmation_is_independent(tmp_path):
    async def run():
        runtime = SessionRuntime(
            initialize_data_root(tmp_path / "gap"), Store(Model(["甲🙂。乙。"]))
        )
        try:
            sid = runtime.create_session()["id"]
            tid = (await runtime.start_turn(sid, "合成问题"))["id"]
            await settled(runtime, sid, tid)
            events = runtime.events(sid, tid)
            one, two = uuid4().hex, uuid4().hex
            runtime.audio_ack(sid, tid, two, "started", text_start=3, text_end=5)
            runtime.audio_ack(sid, tid, two, "completed")
            assert runtime.get_session(sid)["turns"][0]["heard_text"] == ""
            runtime.audio_ack(sid, tid, one, "started", text_start=0, text_end=3)
            runtime.audio_ack(sid, tid, one, "completed")
            row = runtime.get_session(sid)["turns"][0]
            assert row["heard_text"] == "甲🙂。乙。" and row["confirmed_text"] == ""
            assert runtime.audio_ack(sid, tid, one, "completed")["state"] == "completed"
            with pytest.raises(RuntimeConflictError):
                runtime.audio_ack(sid, tid, one, "completed", text_start=0, text_end=5)
            with pytest.raises(RuntimeConflictError):
                runtime.audio_ack(sid, tid, uuid4().hex, "started", text_start=1, text_end=4)
            runtime.ack(sid, tid, events["last_seq"])
            assert runtime.get_session(sid)["turns"][0]["confirmed_text"] == "甲🙂。乙。"
        finally:
            await runtime.close()

    asyncio.run(run())


@pytest.mark.parametrize("bounds", [(0, 999), (-1, 2), (True, 2), (0, False), (2, 2), (None, 2)])
def test_audio_ranges_cannot_confirm_foreign_or_invalid_content(tmp_path, bounds):
    async def run():
        runtime = SessionRuntime(initialize_data_root(tmp_path / "range"), Store())
        try:
            sid = runtime.create_session()["id"]
            tid = (await runtime.start_turn(sid, "合成问题"))["id"]
            await settled(runtime, sid, tid)
            runtime.events(sid, tid)
            with pytest.raises(RuntimeInputError):
                runtime.audio_ack(
                    sid, tid, uuid4().hex, "started", text_start=bounds[0], text_end=bounds[1]
                )
            assert not runtime.get_session(sid)["turns"][0]["audio_playback"]
        finally:
            await runtime.close()

    asyncio.run(run())


def test_completed_generation_without_seen_or_heard_content_is_not_history(tmp_path):
    async def run():
        store = Store(Model(["未显示也未播放的句子。 "]))
        runtime = SessionRuntime(initialize_data_root(tmp_path / "unseen"), store)
        try:
            sid = runtime.create_session()["id"]
            tid = (await runtime.start_turn(sid, "合成问题"))["id"]
            await settled(runtime, sid, tid)
            with pytest.raises(RuntimeInputError):
                runtime.audio_ack(sid, tid, uuid4().hex, "started", text_start=0, text_end=2)
            runtime.events(sid, tid)
            legacy = uuid4().hex
            runtime.audio_ack(sid, tid, legacy, "started")
            runtime.audio_ack(sid, tid, legacy, "completed")
            following = (await runtime.start_turn(sid, "另一个问题"))["id"]
            await settled(runtime, sid, following)
            assert not [m for m in store.adapter.messages[-1] if m["role"] == "assistant"]
        finally:
            await runtime.close()

    asyncio.run(run())


def test_previous_audio_schema_migrates_without_fabricating_heard_ranges(tmp_path):
    paths = initialize_data_root(tmp_path / "upgrade")

    async def run():
        runtime = SessionRuntime(paths, Store())
        sid = runtime.create_session()["id"]
        tid = (await runtime.start_turn(sid, "合成问题"))["id"]
        await settled(runtime, sid, tid)
        runtime.events(sid, tid)
        segment = uuid4().hex
        runtime.audio_ack(sid, tid, segment, "started")
        runtime.audio_ack(sid, tid, segment, "completed")
        await runtime.close()
        with closing(sqlite3.connect(paths.memory / "conversation.sqlite")) as db, db:
            db.execute("ALTER TABLE audio_playback DROP COLUMN text_start")
            db.execute("ALTER TABLE audio_playback DROP COLUMN text_end")
        reopened = SessionRuntime(paths, Store())
        try:
            row = reopened.get_session(sid)["turns"][0]
            assert row["heard_text"] == ""
            assert row["audio_playback"][0]["text_start"] is None
            assert row["audio_playback"][0]["state"] == "completed"
        finally:
            await reopened.close()

    asyncio.run(run())


def test_partial_display_ack_inside_url_does_not_credit_unspoken_url_suffix(tmp_path):
    first, second = "说明 https://exa", "mple.com/path\n下一步先守住。"

    async def run():
        store = Store(Model([first, second]))
        runtime = SessionRuntime(initialize_data_root(tmp_path / "mixed"), store)
        try:
            sid = runtime.create_session()["id"]
            tid = (await runtime.start_turn(sid, "合成问题"))["id"]
            await settled(runtime, sid, tid)
            batch = runtime.events(sid, tid)
            partial = next(event for event in batch["events"] if event["type"] == "text")
            assert partial["text"] == first
            runtime.ack(sid, tid, partial["seq"])
            segment = uuid4().hex
            runtime.audio_ack(
                sid, tid, segment, "started", text_start=0, text_end=len(first + second)
            )
            runtime.audio_ack(sid, tid, segment, "completed")
            following = (await runtime.start_turn(sid, "接着说"))["id"]
            await settled(runtime, sid, following)
            history = [m["content"] for m in store.adapter.messages[-1] if m["role"] == "assistant"]
            assert history == [first + "\n下一步先守住。"]
            row = runtime.get_session(sid)["turns"][0]
            assert row["confirmed_text"] == first
            assert row["heard_text"] == "说明 \n下一步先守住。"
        finally:
            await runtime.close()

    asyncio.run(run())


@pytest.mark.parametrize(
    ("raw", "split", "expected"),
    [("建议[S123]下一步。", 5, "下一步。"), ("看🙂https://例子.com/a 下一步。", 10, " 下一步。")],
)
def test_speech_projection_recognizes_complete_silent_tokens(raw, split, expected):
    assert _speech_slice(raw, split, len(raw)) == expected


def test_audio_api_rejects_ambiguous_ranges_and_confirms_exact_original_text(tmp_path):
    async def run():
        runtime = SessionRuntime(initialize_data_root(tmp_path / "api"), Store(Model(["你好🙂。"])))
        connection = Connection(port=9898, token="synthetic-token", instance_id=uuid4().hex)
        app = create_app(connection, lambda: None, runtime=runtime, providers=runtime.providers)
        try:
            sid = runtime.create_session()["id"]
            tid = (await runtime.start_turn(sid, "你好"))["id"]
            await settled(runtime, sid, tid)
            runtime.events(sid, tid)
            path = f"/api/sessions/{sid}/turns/{tid}/audio"
            async with httpx.AsyncClient(
                transport=httpx.ASGITransport(app=app),
                base_url=connection.url,
                headers={"Authorization": "Bearer " + connection.token},
            ) as client:
                for bounds in (
                    {"text_start": None, "text_end": None},
                    {"text_start": 0},
                    {"text_start": False, "text_end": 4},
                    {"text_start": 0, "text_end": 5},
                ):
                    response = await client.post(
                        path, json={"segment_id": uuid4().hex, "state": "started", **bounds}
                    )
                    assert response.status_code == 400
                segment = {"segment_id": uuid4().hex, "text_start": 0, "text_end": 4}
                for state in ("started", "completed"):
                    assert (
                        await client.post(path, json={**segment, "state": state})
                    ).status_code == 200
                row = runtime.get_session(sid)["turns"][0]
                assert row["heard_text"] == "你好🙂。" and row["ack_seq"] == 0
        finally:
            await runtime.close()

    asyncio.run(run())
