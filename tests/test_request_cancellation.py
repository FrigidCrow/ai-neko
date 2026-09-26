"""Revocation wins delayed image acceptance; all media and providers are synthetic."""

import asyncio
import json
import time
from uuid import uuid4

import pytest
from test_chat import ScriptModel, call, result
from test_companion_integration import image
from test_memory_backup_api import api_client
from test_runtime import Store, settled

from ai_neko.config.paths import initialize_data_root
from ai_neko.runtime import RuntimeConflictError, RuntimeInputError, SessionRuntime


def test_cancel_before_acceptance_persists_and_is_scoped_to_session(tmp_path):
    paths = initialize_data_root(tmp_path / "cancel-before")

    async def run():
        runtime = SessionRuntime(paths, Store())
        try:
            sid = runtime.create_session()["id"]
            request_id = uuid4().hex
            expected = {"request_id": request_id, "status": "cancelled"}
            assert await runtime.cancel_request(sid, request_id) == expected
            assert await runtime.cancel_request(sid, request_id) == expected
            with pytest.raises(RuntimeConflictError):
                await runtime.start_turn(sid, "synthetic", request_id=request_id, image=image())
            assert not runtime.get_session(sid)["turns"]
            assert not runtime.providers.adapter.messages
            other = runtime.create_session()["id"]
            tid = (await runtime.start_turn(other, "synthetic", request_id=request_id))["id"]
            await settled(runtime, other, tid)
        finally:
            await runtime.close()
        reopened = SessionRuntime(paths, Store())
        try:
            with pytest.raises(RuntimeConflictError):
                await reopened.start_turn(sid, "synthetic", request_id=request_id, image=image())
            fresh = (await reopened.start_turn(sid, "new question", request_id=uuid4().hex))["id"]
            await settled(reopened, sid, fresh)
            assert len(reopened.providers.adapter.messages) == 1
        finally:
            await reopened.close()

    asyncio.run(run())


def test_cancel_accepted_visual_request_during_tools_prevents_later_image_upload(tmp_path):
    async def run():
        started, stopped = asyncio.Event(), asyncio.Event()

        class SlowWeb:
            async def execute(self, name, arguments):
                started.set()
                try:
                    await asyncio.Event().wait()
                finally:
                    stopped.set()
                return result()

        class Providers(Store):
            def web_tools(self):
                return SlowWeb()

        store = Providers(ScriptModel([[call("search_web", query="synthetic game")]]))
        runtime = SessionRuntime(initialize_data_root(tmp_path / "during-tools"), store)
        try:
            sid, rid = runtime.create_session()["id"], uuid4().hex
            tid = (
                await runtime.start_turn(
                    sid, "synthetic game", guide=True, request_id=rid, image=image()
                )
            )["id"]
            await asyncio.wait_for(started.wait(), 3)
            # Only the caller's request ID is needed if the acceptance response was lost.
            canceled = await runtime.cancel_request(sid, rid)
            assert canceled == {"request_id": rid, "turn_id": tid, "status": "cancelled"}
            await asyncio.wait_for(stopped.wait(), 3)
            task = runtime._tasks.get(tid)
            if task:
                await asyncio.gather(task, return_exceptions=True)
            assert len(store.adapter.calls) == 1
            assert runtime.get_session(sid)["turns"][0]["status"] == "cancelled"
            assert await runtime.cancel_request(sid, rid) == canceled
            assert not any(event["type"] == "text" for event in runtime.events(sid, tid)["events"])
        finally:
            await runtime.close()

    asyncio.run(run())


def test_revocation_during_slow_http_upload_rejects_late_acceptance(tmp_path):
    async def run():
        runtime = SessionRuntime(initialize_data_root(tmp_path / "upload"), Store())
        begun, release = asyncio.Event(), asyncio.Event()
        request = None
        try:
            async with api_client(runtime) as client:
                sid, rid = runtime.create_session()["id"], uuid4().hex
                payload = json.dumps(
                    {"text": "synthetic image", "request_id": rid, "image": image()}
                ).encode()

                async def slow_body():
                    yield payload[:20]
                    begun.set()
                    await release.wait()
                    yield payload[20:]

                request = asyncio.create_task(
                    client.post(
                        f"/api/sessions/{sid}/turns",
                        content=slow_body(),
                        headers={"Content-Type": "application/json"},
                    )
                )
                await asyncio.wait_for(begun.wait(), 3)
                response = await client.post(f"/api/sessions/{sid}/requests/{rid}/cancel", json={})
                assert response.status_code == 200 and response.json()["status"] == "cancelled"
                release.set()
                assert (await asyncio.wait_for(request, 3)).status_code == 409
                assert not runtime.get_session(sid)["turns"]
                assert not runtime.providers.adapter.messages
        finally:
            release.set()
            if request and not request.done():
                request.cancel()
                await asyncio.gather(request, return_exceptions=True)
            await runtime.close()

    asyncio.run(run())


def test_revocation_during_restore_survives_until_delayed_image_upload_finishes(
    tmp_path, monkeypatch
):
    async def run():
        runtime = SessionRuntime(initialize_data_root(tmp_path / "restore-upload"), Store())
        upload_started, release_upload, restore_stopping, release_restore = (
            asyncio.Event() for _ in range(4)
        )
        tasks = []
        original_stop = runtime._stop_memory_work

        async def held_stop():
            # A cooperative ASR/model/extraction task can take time to finish
            # cancellation. Hold that real restore boundary deterministically.
            restore_stopping.set()
            await release_restore.wait()
            await original_stop()

        monkeypatch.setattr(runtime, "_stop_memory_work", held_stop)
        try:
            snapshot = runtime.memory.backup()
            sid, rid = runtime.create_session()["id"], uuid4().hex
            payload = json.dumps(
                {"text": "synthetic revoked image", "request_id": rid, "image": image()}
            ).encode()

            async def slow_body():
                yield payload[:20]
                upload_started.set()
                await release_upload.wait()
                yield payload[20:]

            async with api_client(runtime) as client:
                uploading = asyncio.create_task(
                    client.post(
                        f"/api/sessions/{sid}/turns",
                        content=slow_body(),
                        headers={"Content-Type": "application/json"},
                    )
                )
                tasks.append(uploading)
                await asyncio.wait_for(upload_started.wait(), 3)
                restoring = asyncio.create_task(
                    runtime.restore_memory(snapshot["id"], runtime.memory.revision())
                )
                tasks.append(restoring)
                await asyncio.wait_for(restore_stopping.wait(), 3)
                assert runtime._memory_mutating
                canceled = await client.post(f"/api/sessions/{sid}/requests/{rid}/cancel", json={})
                release_restore.set()
                await asyncio.wait_for(restoring, 3)
                assert not runtime._memory_mutating
                release_upload.set()
                accepted = await asyncio.wait_for(uploading, 3)
                if accepted.status_code == 202:
                    await settled(runtime, sid, accepted.json()["id"])
                assert {
                    "cancel_status": canceled.status_code,
                    "late_upload_status": accepted.status_code,
                    "model_calls": len(runtime.providers.adapter.messages),
                } == {"cancel_status": 200, "late_upload_status": 409, "model_calls": 0}
                assert runtime._db.execute(
                    "SELECT 1 FROM cancelled_requests WHERE session_id=? AND request_id=?",
                    (sid, rid),
                ).fetchone()
                assert not runtime.get_session(sid)["turns"]
        finally:
            release_restore.set()
            release_upload.set()
            if tasks:
                await asyncio.wait_for(asyncio.gather(*tasks, return_exceptions=True), 3)
            await runtime.close()

    asyncio.run(run())


def test_request_cancellation_api_keeps_authentication_and_validates_scope(tmp_path):
    async def run():
        runtime = SessionRuntime(initialize_data_root(tmp_path / "scope"), Store())
        try:
            async with api_client(runtime) as client:
                sid, rid = runtime.create_session()["id"], uuid4().hex
                path = f"/api/sessions/{sid}/requests/{rid}/cancel"
                assert (
                    await client.post(path, json={}, headers={"Authorization": ""})
                ).status_code == 401
                assert (
                    await client.post(path, json={}, headers={"Origin": "https://foreign.invalid"})
                ).status_code == 403
                assert (await client.post(path, json={"arbitrary": True})).status_code == 400
                assert (
                    await client.post(path.replace(sid, uuid4().hex), json={})
                ).status_code == 404
                assert (await client.post(path.replace(rid, "bad"), json={})).status_code == 404
                assert not runtime._db.execute("SELECT 1 FROM cancelled_requests").fetchone()
                assert (await client.post(path, json={})).status_code == 200
                assert (await client.post(path, json={})).status_code == 200
                assert (
                    runtime._db.execute("SELECT COUNT(*) FROM cancelled_requests").fetchone()[0]
                    == 1
                )
        finally:
            await runtime.close()

    asyncio.run(run())


def test_accepted_image_retry_uses_original_after_age_limit_and_restart(tmp_path, monkeypatch):
    paths = initialize_data_root(tmp_path / "retry-image")
    picture, rid = image(), uuid4().hex

    async def run():
        runtime = SessionRuntime(paths, Store())
        try:
            sid = runtime.create_session()["id"]
            tid = (await runtime.start_turn(sid, "synthetic", request_id=rid, image=picture))["id"]
            await settled(runtime, sid, tid)
        finally:
            await runtime.close()
        reopened = SessionRuntime(paths, Store())
        try:
            now = time.time()
            with monkeypatch.context() as clock:
                clock.setattr("ai_neko.chat.vision.time.time", lambda: now + 121)
                retry = await reopened.start_turn(sid, "synthetic", request_id=rid, image=picture)
                assert retry["id"] == tid and not reopened.providers.adapter.messages
                with pytest.raises(RuntimeInputError):
                    await reopened.start_turn(
                        sid, "synthetic", request_id=uuid4().hex, image=picture
                    )
                with pytest.raises(RuntimeConflictError):
                    await reopened.start_turn(sid, "synthetic", request_id=rid, image=image())
            assert len(reopened.get_session(sid)["turns"]) == 1
        finally:
            await reopened.close()

    asyncio.run(run())
