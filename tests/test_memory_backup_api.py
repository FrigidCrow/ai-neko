"""Memory snapshot API and cross-store recovery; only synthetic local data is used."""

import asyncio
import json
import sqlite3
from contextlib import asynccontextmanager, closing
from uuid import uuid4

import httpx
import pytest
from test_runtime import Model, Store, settled, text_ready

from ai_neko.app.server import Connection, create_app
from ai_neko.config.paths import initialize_data_root
from ai_neko.media import VoiceService
from ai_neko.runtime import RuntimeConflictError, SessionRuntime

BACKUPS = "/api/memory/backups"
MARKER = "喜欢喝柚子汽水 SYNTHETIC_RESTORE_REMOVED_MANUAL_FACT"


def api_client(runtime):
    connection = Connection(9898, "synthetic-backup-api-token", uuid4().hex)
    app = create_app(connection, lambda: None, runtime=runtime, providers=runtime.providers)
    return httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app),
        base_url=connection.url,
        headers={"Authorization": "Bearer " + connection.token, "Origin": connection.url},
    )


@asynccontextmanager
async def api_runtime(tmp_path, model=None):
    runtime = SessionRuntime(initialize_data_root(tmp_path / "backup-api"), Store(model))
    try:
        async with api_client(runtime) as client:
            yield client, runtime
    finally:
        await runtime.close()


async def response_json(client, method, path, *, data=None, status=200):
    response = await client.request(method, path, **({"json": data} if data is not None else {}))
    assert response.status_code == status, response.text
    return response.json()


async def snapshot(client):
    return await response_json(client, "POST", BACKUPS, data={}, status=201)


async def restore(client, runtime, backup_id):
    return await response_json(
        client,
        "POST",
        f"{BACKUPS}/{backup_id}/restore",
        data={"confirm": True, "expected_revision": runtime.memory.revision()},
    )


async def recalled_manual_turn(client, runtime, confirmation):
    fact = await response_json(
        client, "POST", "/api/memories", data={"content": MARKER, "kind": "preference"}, status=201
    )
    sid = (await response_json(client, "POST", "/api/sessions", status=201))["id"]
    base = f"/api/sessions/{sid}"
    tid = (
        await response_json(
            client, "POST", base + "/turns", data={"text": "我喜欢喝什么？"}, status=202
        )
    )["id"]
    await settled(runtime, sid, tid)
    assert MARKER in runtime.providers.adapter.messages[-1][0]["content"]
    assert runtime._db.execute(
        "SELECT 1 FROM turn_memory WHERE turn_id=? AND fact_id=?", (tid, fact["id"])
    ).fetchone()
    turn_path = base + "/turns/" + tid
    events = await response_json(client, "GET", turn_path + "/events")
    segment = uuid4().hex
    await response_json(
        client,
        "POST",
        turn_path + "/audio",
        data={
            "segment_id": segment,
            "state": "started",
            "text_start": 0,
            "text_end": len(MARKER),
        },
    )
    await response_json(
        client,
        "POST",
        turn_path + "/audio",
        data={
            "segment_id": segment,
            "state": "completed" if confirmation == "audio" else "stopped",
        },
    )
    if confirmation == "display":
        await response_json(
            client, "POST", turn_path + "/ack", data={"sequence": events["last_seq"]}
        )
    row = (await response_json(client, "GET", base))["turns"][0]
    assert row["heard_text" if confirmation == "audio" else "confirmed_text"] == MARKER
    with closing(sqlite3.connect(runtime.paths.checkpoints / "chat-graph.sqlite")) as database:
        assert database.execute("SELECT COUNT(*) FROM checkpoints").fetchone()[0] > 0
    return sid, tid, fact["id"]


def assert_erased(runtime, sid, tid):
    row = runtime.get_session(sid)["turns"][0]
    assert row["input"] == "[已遗忘的对话]"
    assert row["delivered_text"] == row["confirmed_text"] == row["heard_text"] == ""
    assert row["audio_playback"] == []
    assert MARKER not in json.dumps(runtime.get_session(sid), ensure_ascii=False)
    assert not runtime.memory.list_facts()
    assert not runtime._db.execute("SELECT 1 FROM memory_erasure").fetchone()
    for table in ("turn_memory", "events", "audio_playback", "turn_images", "turn_metadata"):
        assert not runtime._db.execute(f"SELECT 1 FROM {table} WHERE turn_id=?", (tid,)).fetchone()
    with closing(sqlite3.connect(runtime.paths.checkpoints / "chat-graph.sqlite")) as database:
        for table in ("checkpoints", "writes"):
            assert database.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0] == 0


async def assert_next_turn_clean(runtime, sid):
    runtime.providers.adapter = Model(["目前没有保存你的饮料偏好。"])
    tid = (await runtime.start_turn(sid, "我喜欢喝什么？"))["id"]
    await settled(runtime, sid, tid)
    assert MARKER not in json.dumps(runtime.providers.adapter.messages[-1], ensure_ascii=False)
    assert not runtime.memory.list_facts()


def test_snapshot_api_create_list_restore_and_delete(tmp_path):
    async def run():
        async with api_runtime(tmp_path) as (client, runtime):
            first = await response_json(
                client, "POST", "/api/memories", data={"content": "合成备份中的偏好"}, status=201
            )
            backup = await snapshot(client)
            listing = await response_json(client, "GET", BACKUPS)
            assert listing["revision"] == backup["revision"] == runtime.memory.revision()
            assert [item["id"] for item in listing["backups"]] == [backup["id"]]
            assert listing["backups"][0]["restorable"] is True
            assert listing["backups"][0]["facts_count"] == 1
            assert str(runtime.paths.root) not in json.dumps(listing)
            extra = await response_json(
                client,
                "POST",
                "/api/memories",
                data={"content": "备份之后新增的合成事实"},
                status=201,
            )
            prior_revision = runtime.memory.revision()
            restored = await restore(client, runtime, backup["id"])
            assert restored["revision"] > prior_revision
            assert extra["id"] in restored["fact_ids"]
            assert [fact["id"] for fact in runtime.memory.list_facts()] == [first["id"]]
            assert await response_json(client, "DELETE", BACKUPS + "/" + backup["id"]) == {
                "id": backup["id"],
                "deleted": True,
            }
            assert (await response_json(client, "GET", BACKUPS))["backups"] == []
            assert not (runtime.paths.backups / backup["id"]).exists()
            assert (await client.delete(BACKUPS + "/" + backup["id"])).status_code == 404

    asyncio.run(run())


@pytest.mark.parametrize("operation", ["list", "create", "restore", "delete"])
def test_snapshot_api_authenticates_before_reading_or_mutating(tmp_path, operation):
    async def run():
        async with api_runtime(tmp_path) as (client, runtime):
            backup = await snapshot(client)
            method, path = {
                "list": ("GET", BACKUPS),
                "create": ("POST", BACKUPS),
                "restore": ("POST", f"{BACKUPS}/{backup['id']}/restore"),
                "delete": ("DELETE", f"{BACKUPS}/{backup['id']}"),
            }[operation]
            before = runtime.memory.revision()
            for headers, expected in (
                ({"Authorization": ""}, 401),
                ({"Authorization": "Bearer wrong-synthetic-token"}, 401),
                ({"Origin": "https://untrusted.example"}, 403),
            ):
                # Deliberately malformed JSON proves authentication happens first.
                response = await client.request(method, path, headers=headers, content=b"bad")
                assert response.status_code == expected
                assert runtime.memory.revision() == before
                assert [item["id"] for item in runtime.memory.list_backups()] == [backup["id"]]

    asyncio.run(run())


@pytest.mark.parametrize(
    "body",
    [
        {},
        {"confirm": True},
        {"confirm": False, "expected_revision": 0},
        {"confirm": 1, "expected_revision": 0},
        {"confirm": "true", "expected_revision": 0},
        {"confirm": True, "expected_revision": True},
        {"confirm": True, "expected_revision": -1},
        {"confirm": True, "expected_revision": "0"},
        {"confirm": True, "expected_revision": 0, "extra": "ignored?"},
        [],
    ],
)
def test_restore_requires_explicit_confirmation_and_integer_revision(tmp_path, body):
    async def run():
        async with api_runtime(tmp_path) as (client, runtime):
            backup = await snapshot(client)
            revision = runtime.memory.revision()
            response = await client.post(f"{BACKUPS}/{backup['id']}/restore", json=body)
            assert response.status_code == 400
            assert runtime.memory.revision() == revision
            assert not runtime._db.execute("SELECT 1 FROM memory_erasure").fetchone()

    asyncio.run(run())


def test_snapshot_create_rejects_unknown_fields_and_invalid_body(tmp_path):
    async def run():
        async with api_runtime(tmp_path) as (client, runtime):
            assert (await client.post(BACKUPS, json={"path": "/arbitrary"})).status_code == 400
            assert (await client.post(BACKUPS, json=[])).status_code == 400
            assert (await client.post(BACKUPS, content=b"bad")).status_code == 415
            assert (
                await client.post(
                    BACKUPS, content=b"{", headers={"Content-Type": "application/json"}
                )
            ).status_code == 400
            assert runtime.memory.list_backups() == []

    asyncio.run(run())


def test_stale_restore_revision_preserves_newer_memory(tmp_path):
    async def run():
        async with api_runtime(tmp_path) as (client, runtime):
            backup = await snapshot(client)
            fact = await response_json(
                client,
                "POST",
                "/api/memories",
                data={"content": "刷新确认之前新增的事实"},
                status=201,
            )
            revision = runtime.memory.revision()
            response = await client.post(
                f"{BACKUPS}/{backup['id']}/restore",
                json={"confirm": True, "expected_revision": backup["revision"]},
            )
            assert response.status_code == 409
            assert runtime.memory.revision() == revision
            assert [item["id"] for item in runtime.memory.list_facts()] == [fact["id"]]
            assert not runtime._db.execute("SELECT 1 FROM memory_erasure").fetchone()

    asyncio.run(run())


def test_restore_api_keeps_later_correction_and_explicit_forgetting(tmp_path):
    async def run():
        async with api_runtime(tmp_path) as (client, runtime):
            corrected = await response_json(
                client, "POST", "/api/memories", data={"content": "原先喜欢合成红茶"}, status=201
            )
            forgotten = await response_json(
                client, "POST", "/api/memories", data={"content": "必须遗忘的合成偏好"}, status=201
            )
            backup = await snapshot(client)
            await response_json(
                client,
                "PUT",
                "/api/memories/" + corrected["id"],
                data={"content": "纠正为喜欢合成绿茶"},
            )
            await response_json(client, "DELETE", "/api/memories/" + forgotten["id"])
            await restore(client, runtime, backup["id"])
            facts = (await response_json(client, "GET", "/api/memories"))["memories"]
            assert [(fact["id"], fact["content"]) for fact in facts] == [
                (corrected["id"], "纠正为喜欢合成绿茶")
            ]
            assert forgotten["id"] in runtime.memory.erasure_state()["fact_ids"]

    asyncio.run(run())


@pytest.mark.parametrize("failure", ["missing", "malformed_id", "corrupt"])
def test_invalid_snapshot_restore_does_not_change_memory(tmp_path, failure):
    async def run():
        async with api_runtime(tmp_path) as (client, runtime):
            await response_json(
                client,
                "POST",
                "/api/memories",
                data={"content": "无效备份不能破坏的合成事实"},
                status=201,
            )
            backup = await snapshot(client)
            backup_id = backup["id"]
            if failure == "missing":
                backup_id = "memory-" + uuid4().hex + ".sqlite"
            elif failure == "malformed_id":
                backup_id = "not-a-memory-snapshot.sqlite"
            else:
                (runtime.paths.backups / backup_id).write_bytes(b"synthetic invalid SQLite backup")
            before = runtime.memory.list_facts()
            revision = runtime.memory.revision()
            response = await client.post(
                f"{BACKUPS}/{backup_id}/restore",
                json={"confirm": True, "expected_revision": revision},
            )
            assert response.status_code == (404 if failure == "missing" else 400)
            assert runtime.memory.list_facts() == before
            assert runtime.memory.revision() == revision
            assert not runtime._db.execute("SELECT 1 FROM memory_erasure").fetchone()
            assert not runtime._memory_mutating and not runtime._erasure_failed

    asyncio.run(run())


@pytest.mark.parametrize("confirmation", ["display", "audio"])
def test_restore_erases_recalled_manual_fact_from_history_audio_and_checkpoint(
    tmp_path, confirmation
):
    async def run():
        async with api_runtime(tmp_path, Model([MARKER])) as (client, runtime):
            backup = await snapshot(client)
            sid, tid, fact_id = await recalled_manual_turn(client, runtime, confirmation)
            result = await restore(client, runtime, backup["id"])
            assert fact_id in result["fact_ids"]
            assert "turn:" + tid in result["source_ids"]
            assert_erased(runtime, sid, tid)
            await assert_next_turn_clean(runtime, sid)

    asyncio.run(run())


def test_restore_recovers_when_memory_commits_before_history_cleanup(tmp_path, monkeypatch):
    async def run():
        async with api_runtime(tmp_path, Model([MARKER])) as (client, runtime):
            backup = await snapshot(client)
            sid, tid, fact_id = await recalled_manual_turn(client, runtime, "audio")

            def crash_before_history_cleanup(_intent):
                raise RuntimeError("synthetic crash after Memory commit")

            monkeypatch.setattr(runtime, "_complete_erasure", crash_before_history_cleanup)
            with pytest.raises(RuntimeError, match="synthetic crash after Memory commit"):
                await runtime.restore_memory(backup["id"], runtime.memory.revision())
            assert not runtime.memory.list_facts()
            assert fact_id in runtime.memory.erasure_state()["fact_ids"]
            assert runtime._db.execute("SELECT 1 FROM memory_erasure").fetchone()
            assert MARKER in json.dumps(runtime._payloads(tid), ensure_ascii=False)
            with pytest.raises(RuntimeConflictError):
                runtime.get_session(sid)
            with pytest.raises(RuntimeConflictError):
                await runtime.start_turn(sid, "清理失败时不能继续对话")
            base = f"/api/sessions/{sid}"
            turn_path = f"{base}/turns/{tid}"
            for method, path, body in (
                ("GET", base, None),
                ("GET", turn_path + "/events", None),
                ("GET", "/api/sessions", None),
                ("POST", "/api/sessions", None),
                ("POST", turn_path + "/cancel", None),
                ("POST", turn_path + "/ack", {"sequence": 0}),
            ):
                response = await client.request(
                    method, path, **({"json": body} if body is not None else {})
                )
                assert response.status_code == 409, (method, path, response.text)
                assert MARKER not in response.text
            await runtime.close()
            reopened = SessionRuntime(runtime.paths, Store())
            try:
                assert_erased(reopened, sid, tid)
                async with api_client(reopened) as recovered_client:
                    for path in (base, turn_path + "/events", "/api/sessions"):
                        response = await recovered_client.get(path)
                        assert response.status_code == 200, (path, response.text)
                        assert MARKER not in response.text
                    row = (await recovered_client.get(base)).json()["turns"][0]
                    assert row["audio_playback"] == [] and row["heard_text"] == ""
                    events = (await recovered_client.get(turn_path + "/events")).json()
                    assert events["events"] == []
                await assert_next_turn_clean(reopened, sid)
            finally:
                await reopened.close()

    asyncio.run(run())


def test_restore_cancels_active_generation_and_voice_before_cleaning_memory(tmp_path, monkeypatch):
    async def run():
        voice_started, voice_stopping, release_voice, model_stopped = (
            asyncio.Event() for _ in range(4)
        )

        async def slow_speech(self, text):
            voice_started.set()
            try:
                await asyncio.Event().wait()
            finally:
                voice_stopping.set()
                await release_voice.wait()

        class ActiveModel:
            async def stream(self, messages, tools=None):
                yield {"type": "text", "text": "合成生成尚未完成"}
                try:
                    await asyncio.Event().wait()
                finally:
                    model_stopped.set()

        monkeypatch.setattr(VoiceService, "synthesize", slow_speech)
        async with api_runtime(tmp_path, ActiveModel()) as (client, runtime):
            backup = await snapshot(client)
            sid = runtime.create_session()["id"]
            tid = (await runtime.start_turn(sid, "仍在生成的合成问题"))["id"]
            await text_ready(runtime, sid, tid)
            identifier = uuid4().hex
            voice_request = asyncio.create_task(
                client.post(
                    "/api/voice/synthesize", json={"request_id": identifier, "text": "合成语音"}
                )
            )
            restoring = None
            try:
                await asyncio.wait_for(voice_started.wait(), 2)
                restoring = asyncio.create_task(restore(client, runtime, backup["id"]))
                await asyncio.wait_for(voice_stopping.wait(), 2)
                assert not restoring.done()
                assert (
                    await client.post(f"/api/sessions/{sid}/turns", json={"text": "不应插入新回合"})
                ).status_code == 409
                assert (
                    await client.post("/api/voice/synthesize", json={"text": "不应启动新语音"})
                ).status_code == 409
                release_voice.set()
                await asyncio.wait_for(restoring, 3)
                await asyncio.wait_for(model_stopped.wait(), 2)
                voice_response = await asyncio.wait_for(voice_request, 2)
                assert voice_response.status_code == 409
                assert "audio_base64" not in voice_response.text
                assert identifier not in runtime.voice_tasks
                assert runtime.get_session(sid)["turns"][0]["status"] == "cancelled"
                assert tid not in runtime._tasks or runtime._tasks[tid].done()
            finally:
                release_voice.set()
                tasks = [voice_request] + ([restoring] if restoring is not None else [])
                for task in tasks:
                    if not task.done():
                        task.cancel()
                await asyncio.gather(*tasks, return_exceptions=True)

    asyncio.run(run())
