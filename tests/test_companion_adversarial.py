"""Cross-store and media lifecycle regressions; every request uses synthetic data."""

import asyncio
import base64
import json
import mmap
import os
import sqlite3
import time
from uuid import uuid4

import httpx
import pytest
from filelock import FileLock
from test_runtime import Model, Store, settled, text_ready

from ai_neko.app.server import Connection, create_app
from ai_neko.config.paths import initialize_data_root
from ai_neko.media import VoiceService
from ai_neko.runtime import RuntimeConflictError, SessionRuntime
from ai_neko.runtime import service as runtime_module


def make_runtime(tmp_path, name="adversarial", model=None):
    return SessionRuntime(initialize_data_root(tmp_path / name), Store(model))


def assert_no_image_bytes_on_disk(root, forbidden, *, active):
    for path in root.rglob("*"):
        if not path.is_file():
            continue
        if active:
            # Windows byte-range locks can deny a second handle's read, even in
            # the same process. Read-only mapped views inspect the actual file
            # bytes without excluding lock files or SQLite DB/WAL/SHM files.
            with path.open("rb") as handle:
                if os.fstat(handle.fileno()).st_size == 0:
                    continue  # Empty files contain no image bytes and cannot be mapped.
                with mmap.mmap(handle.fileno(), 0, access=mmap.ACCESS_READ) as view:
                    contents = view[:]
        else:
            contents = path.read_bytes()
        for value in forbidden:
            assert value not in contents, f"Image bytes persisted in {path.relative_to(root)}"


@pytest.mark.parametrize(
    "filename",
    [
        "conversation.sqlite",
        "conversation.sqlite-wal",
        "conversation.sqlite-shm",
        ".conversation.lock",
    ],
)
@pytest.mark.parametrize("encoded", [False, True])
def test_image_disk_scan_detects_leaks_even_in_locked_files(tmp_path, filename, encoded):
    marker = b"SYNTHETIC_IMAGE_SCAN_MUST_DETECT_THIS_LEAK"
    payload = base64.b64encode(marker) if encoded else marker
    path = tmp_path / filename
    # Use the public borrowed-descriptor hook: the owning handle can write a
    # synthetic leak after acquisition on both Windows and POSIX.
    with (
        FileLock(path, on_acquired=lambda fd: os.write(fd, payload)),
        pytest.raises(AssertionError, match="Image bytes persisted"),
    ):
        assert_no_image_bytes_on_disk(tmp_path, (payload,), active=True)


@pytest.mark.parametrize("length", [2001, 8000])
def test_valid_long_turn_does_not_exceed_memory_query_contract(tmp_path, length):
    async def run():
        runtime = make_runtime(tmp_path)
        try:
            sid = runtime.create_session()["id"]
            text = "文" * length
            turn = await runtime.start_turn(sid, text)
            await settled(runtime, sid, turn["id"])
            saved = runtime.get_session(sid)["turns"][0]
            assert saved["status"] == "completed"
            assert saved["input"] == text
            assert runtime.providers.adapter.messages[-1][-1]["content"] == text
        finally:
            await runtime.close()

    asyncio.run(run())


def test_concurrent_close_is_idempotent_with_inflight_voice(tmp_path):
    async def run():
        runtime = make_runtime(tmp_path)
        started = asyncio.Event()

        async def voice():
            started.set()
            try:
                await asyncio.Event().wait()
            finally:
                await asyncio.sleep(0)

        task = asyncio.create_task(voice())
        runtime.voice_tasks[uuid4().hex] = task
        await started.wait()
        results = await asyncio.gather(runtime.close(), runtime.close(), return_exceptions=True)
        assert results == [None, None]
        assert task.done() and task.cancelled()
        # Releasing the lock once must leave the owned database reopenable.
        reopened = SessionRuntime(runtime.paths, Store())
        await reopened.close()

    asyncio.run(run())


def test_closing_rejects_new_turn_before_waiting_for_voice_cleanup(tmp_path):
    async def run():
        runtime = make_runtime(tmp_path)
        sid = runtime.create_session()["id"]
        started, cleanup_entered, release_cleanup = (asyncio.Event() for _ in range(3))

        async def voice():
            started.set()
            try:
                await asyncio.Event().wait()
            finally:
                cleanup_entered.set()
                await release_cleanup.wait()

        task = asyncio.create_task(voice())
        runtime.voice_tasks[uuid4().hex] = task
        await started.wait()
        closing = asyncio.create_task(runtime.close())
        try:
            await asyncio.wait_for(cleanup_entered.wait(), 1)
            with pytest.raises(RuntimeConflictError):
                await runtime.start_turn(sid, "A new turn must not outlive shutdown")
        finally:
            release_cleanup.set()
            await asyncio.wait_for(closing, 2)

    asyncio.run(run())


def test_forgetting_serializes_while_waiting_for_active_turn_cancellation(tmp_path):
    async def run():
        entered, release = asyncio.Event(), asyncio.Event()

        class CancelBoundaryModel:
            async def stream(self, messages, tools=None):
                yield {"type": "text", "text": "Synthetic in-flight answer"}
                try:
                    await asyncio.Event().wait()
                except asyncio.CancelledError:
                    entered.set()
                    await release.wait()
                    raise

        runtime = make_runtime(tmp_path, model=CancelBoundaryModel())
        sid = runtime.create_session()["id"]
        first = runtime.memory.remember("synthetic first fact", source_id="manual:first")
        second = runtime.memory.remember("synthetic second fact", source_id="manual:second")
        active = await runtime.start_turn(sid, "Wait while a fact is forgotten")
        await text_ready(runtime, sid, active["id"])
        first_task = asyncio.create_task(runtime.forget_memory(first["id"]))
        second_task = None
        try:
            await asyncio.wait_for(entered.wait(), 2)
            second_task = asyncio.create_task(runtime.forget_memory(second["id"]))
            # Give the second caller execution opportunity while the first caller
            # has explicitly paused waiting for an owned model task to stop.
            await asyncio.sleep(0)
            assert second["id"] in {fact["id"] for fact in runtime.memory.list_facts()}
            assert not second_task.done()
            with pytest.raises(RuntimeConflictError):
                await runtime.start_turn(sid, "Must wait until all forgetting finishes")
        finally:
            release.set()
            tasks = [first_task] + ([second_task] if second_task else [])
            results = await asyncio.wait_for(asyncio.gather(*tasks, return_exceptions=True), 3)
            await runtime.close()
        assert len(results) == 2
        assert all(isinstance(result, dict) and result["fact_ids"] for result in results)

    asyncio.run(run())


def test_forgetting_erases_later_pending_extraction_source_and_journal(tmp_path):
    marker = "SYNTHETIC_FORGOTTEN_PREFERENCE_BLUE"

    async def run():
        runtime = make_runtime(tmp_path)
        try:
            sid = runtime.create_session()["id"]
            first = await runtime.start_turn(sid, marker)
            await runtime._tasks[first["id"]]
            fact = runtime.memory.remember(
                marker, source_id="turn:" + first["id"], source_text=marker
            )
            second = await runtime.start_turn(sid, marker + " repeated")
            await runtime._tasks[second["id"]]
            job = runtime.memory.enqueue_extraction(
                "turn:" + second["id"], marker + " repeated", turn_id=second["id"]
            )
            await runtime.forget_memory(fact["id"])
            assert marker not in json.dumps(runtime.get_session(sid))
            assert not runtime.memory.list_facts()
            assert all(item["id"] != job["id"] for item in runtime.memory.pending_jobs())
            for path in (
                runtime.paths.memory / "long-term.sqlite",
                runtime.paths.memory / "conversation.sqlite",
                runtime.paths.checkpoints / "chat-graph.sqlite",
            ):
                with sqlite3.connect(path) as db:
                    assert marker not in "\n".join(db.iterdump())
        finally:
            await runtime.close()

    asyncio.run(run())


def test_raw_screenshot_stays_out_of_disk_during_cancel_and_next_turn(tmp_path):
    marker = b"SYNTHETIC_IMAGE_BYTES_MUST_NEVER_REACH_DISK"
    picture = {
        "frame_id": uuid4().hex,
        "source_id": "window:synthetic-test:0",
        "source_name": "Synthetic game",
        "captured_at": time.time() * 1000,
        "data_url": "data:image/png;base64,"
        + base64.b64encode(b"\x89PNG\r\n\x1a\n" + marker).decode(),
    }
    forbidden = (
        marker,
        picture["data_url"].encode(),
        picture["data_url"].split(",", 1)[1].encode(),
    )

    async def run():
        runtime = make_runtime(tmp_path, model=Model(gate=asyncio.Event()))
        try:
            sid = runtime.create_session()["id"]
            first = await runtime.start_turn(sid, "Inspect this synthetic frame", image=picture)
            await text_ready(runtime, sid, first["id"])
            assert picture["data_url"] in json.dumps(runtime.providers.adapter.messages)
            assert_no_image_bytes_on_disk(runtime.paths.root, forbidden, active=True)
            await runtime.cancel_turn(sid, first["id"])
            assert_no_image_bytes_on_disk(runtime.paths.root, forbidden, active=True)
            runtime.providers.adapter = Model(["Only the new question"])
            second = await runtime.start_turn(sid, "No image for this turn")
            await settled(runtime, sid, second["id"])
            assert picture["data_url"] not in json.dumps(runtime.providers.adapter.messages)
            assert_no_image_bytes_on_disk(runtime.paths.root, forbidden, active=True)
        finally:
            await runtime.close()
        # Also inspect ordinary reads after all handles close and SQLite flushes.
        assert_no_image_bytes_on_disk(runtime.paths.root, forbidden, active=False)

    asyncio.run(run())


@pytest.mark.parametrize("cancel_action", ["explicit", "forget"])
def test_voice_api_cancellation_finishes_task_and_rejects_late_audio(
    tmp_path, monkeypatch, cancel_action
):
    async def run():
        started, stopped = asyncio.Event(), asyncio.Event()

        async def slow_speech(self, text):
            started.set()
            try:
                await asyncio.Event().wait()
            finally:
                stopped.set()

        monkeypatch.setattr(VoiceService, "synthesize", slow_speech)
        runtime = make_runtime(tmp_path)
        connection = Connection(port=9898, token="synthetic-local-token", instance_id=uuid4().hex)
        app = create_app(connection, lambda: None, runtime=runtime, providers=runtime.providers)
        identifier = uuid4().hex
        request = None
        try:
            async with httpx.AsyncClient(
                transport=httpx.ASGITransport(app=app),
                base_url=connection.url,
                headers={"Authorization": "Bearer " + connection.token},
            ) as client:
                request = asyncio.create_task(
                    client.post(
                        "/api/voice/synthesize",
                        json={"request_id": identifier, "text": "synthetic"},
                    )
                )
                await asyncio.wait_for(started.wait(), 1)
                if cancel_action == "explicit":
                    response = await client.post(
                        "/api/voice/cancel", json={"request_id": identifier}
                    )
                    assert response.status_code == 200
                    assert response.json()["cancelled"] is True
                else:
                    fact = runtime.memory.remember("synthetic voice fact", source_id="manual:voice")
                    response = await client.delete("/api/memories/" + fact["id"])
                    assert response.status_code == 200
                await asyncio.wait_for(stopped.wait(), 1)
                result = await asyncio.wait_for(request, 1)
                assert result.status_code == 409
                assert "audio_base64" not in result.text
                assert identifier not in runtime.voice_tasks
        finally:
            if request is not None and not request.done():
                request.cancel()
                await asyncio.gather(request, return_exceptions=True)
            await runtime.close()

    asyncio.run(run())


@pytest.mark.parametrize(
    "path,method",
    [
        ("/api/voice/config", "GET"),
        ("/api/voice/transcribe", "POST"),
        ("/api/voice/synthesize", "POST"),
        ("/api/voice/cancel", "POST"),
        ("/api/memories", "GET"),
        ("/api/persona", "GET"),
    ],
)
def test_companion_endpoints_authenticate_before_processing(tmp_path, path, method):
    async def run():
        runtime = make_runtime(tmp_path)
        connection = Connection(port=9898, token="synthetic-local-token", instance_id=uuid4().hex)
        app = create_app(connection, lambda: None, runtime=runtime, providers=runtime.providers)
        try:
            async with httpx.AsyncClient(
                transport=httpx.ASGITransport(app=app), base_url=connection.url
            ) as client:
                unauthenticated = await client.request(method, path, json={})
                assert unauthenticated.status_code == 401
                wrong_origin = await client.request(
                    method,
                    path,
                    json={},
                    headers={
                        "Authorization": "Bearer " + connection.token,
                        "Origin": "https://untrusted.example",
                    },
                )
                assert wrong_origin.status_code == 403
                assert not runtime.voice_tasks
        finally:
            await runtime.close()

    asyncio.run(run())


@pytest.mark.parametrize(
    "path,size",
    [
        ("/api/voice/transcribe", 12 * 1024 * 1024 + 1),
        ("/api/voice/synthesize", 65_537),
    ],
)
def test_voice_api_body_limit_precedes_provider_task(tmp_path, path, size):
    async def run():
        runtime = make_runtime(tmp_path)
        connection = Connection(port=9898, token="synthetic-local-token", instance_id=uuid4().hex)
        app = create_app(connection, lambda: None, runtime=runtime, providers=runtime.providers)
        try:
            async with httpx.AsyncClient(
                transport=httpx.ASGITransport(app=app), base_url=connection.url
            ) as client:

                async def large_body():
                    for offset in range(0, size, 64 * 1024):
                        yield b" " * min(64 * 1024, size - offset)

                response = await client.post(
                    path,
                    content=large_body(),
                    headers={
                        "Authorization": "Bearer " + connection.token,
                        "Content-Type": "application/json",
                    },
                )
                assert response.status_code == 413
                assert not runtime.voice_tasks
        finally:
            await runtime.close()

    asyncio.run(run())


def test_voice_upload_started_before_shutdown_cannot_launch_after_close(tmp_path, monkeypatch):
    async def run():
        upload_waiting, release_upload = asyncio.Event(), asyncio.Event()
        provider_calls = []

        async def speech(self, text):
            provider_calls.append(text)
            return {"audio_base64": "synthetic", "mime_type": "audio/mpeg"}

        monkeypatch.setattr(VoiceService, "synthesize", speech)
        runtime = make_runtime(tmp_path)
        connection = Connection(port=9898, token="synthetic-local-token", instance_id=uuid4().hex)
        app = create_app(connection, lambda: None, runtime=runtime, providers=runtime.providers)
        request = None
        try:
            async with httpx.AsyncClient(
                transport=httpx.ASGITransport(app=app), base_url=connection.url
            ) as client:

                async def delayed_body():
                    yield b'{"text":'
                    upload_waiting.set()
                    await release_upload.wait()
                    yield b'"synthetic late speech"}'

                request = asyncio.create_task(
                    client.post(
                        "/api/voice/synthesize",
                        content=delayed_body(),
                        headers={
                            "Authorization": "Bearer " + connection.token,
                            "Content-Type": "application/json",
                        },
                    )
                )
                await asyncio.wait_for(upload_waiting.wait(), 1)
                await runtime.close()
                release_upload.set()
                result = await asyncio.wait_for(request, 1)
                assert result.status_code == 409
                assert not provider_calls
                assert not runtime.voice_tasks
        finally:
            release_upload.set()
            if request is not None and not request.done():
                request.cancel()
                await asyncio.gather(request, return_exceptions=True)
            await runtime.close()

    asyncio.run(run())


def test_voice_cancel_before_upload_finishes_prevents_late_task_registration(tmp_path, monkeypatch):
    async def run():
        upload_waiting, release_upload = asyncio.Event(), asyncio.Event()
        provider_calls = []

        async def speech(self, text):
            provider_calls.append(text)
            return {"audio_base64": "synthetic", "mime_type": "audio/mpeg"}

        monkeypatch.setattr(VoiceService, "synthesize", speech)
        runtime = make_runtime(tmp_path)
        connection = Connection(port=9898, token="synthetic-local-token", instance_id=uuid4().hex)
        app = create_app(connection, lambda: None, runtime=runtime, providers=runtime.providers)
        identifier = uuid4().hex
        request = None
        try:
            async with httpx.AsyncClient(
                transport=httpx.ASGITransport(app=app),
                base_url=connection.url,
                headers={"Authorization": "Bearer " + connection.token},
            ) as client:

                async def delayed_body():
                    yield ('{"request_id":"' + identifier + '","text":').encode()
                    upload_waiting.set()
                    await release_upload.wait()
                    yield b'"synthetic cancelled before provider"}'

                request = asyncio.create_task(
                    client.post(
                        "/api/voice/synthesize",
                        content=delayed_body(),
                        headers={"Content-Type": "application/json"},
                    )
                )
                await asyncio.wait_for(upload_waiting.wait(), 1)
                assert identifier not in runtime.voice_tasks
                cancelled = await client.post("/api/voice/cancel", json={"request_id": identifier})
                assert cancelled.status_code == 200
                release_upload.set()
                result = await asyncio.wait_for(request, 1)
                assert result.status_code == 409
                assert not provider_calls
                assert not runtime.voice_tasks
        finally:
            release_upload.set()
            if request is not None and not request.done():
                request.cancel()
                await asyncio.gather(request, return_exceptions=True)
            await runtime.close()

    asyncio.run(run())


def test_late_graph_completion_cannot_enqueue_memory_from_cancelled_turn(tmp_path, monkeypatch):
    async def run():
        entered, cancellation_seen = asyncio.Event(), asyncio.Event()

        class NonCooperativeGraph:
            async def ainvoke(self, state, config):
                entered.set()
                try:
                    await asyncio.Event().wait()
                except asyncio.CancelledError:
                    cancellation_seen.set()
                    return {"output": "Synthetic late answer after cancellation"}

        monkeypatch.setattr(
            runtime_module, "build_chat_graph", lambda *a, **k: NonCooperativeGraph()
        )
        runtime = make_runtime(tmp_path)
        runtime.memory_preferences.update({"auto_extract": True})
        # Keep the durable queue visible, independent of extraction-provider timing.
        monkeypatch.setattr(runtime, "start_memory_worker", lambda: None)
        try:
            sid = runtime.create_session()["id"]
            turn = await runtime.start_turn(sid, "Synthetic cancelled preference")
            running = runtime._tasks[turn["id"]]
            await asyncio.wait_for(entered.wait(), 1)
            await runtime.cancel_turn(sid, turn["id"])
            await asyncio.wait_for(running, 1)
            assert cancellation_seen.is_set()
            assert runtime.get_session(sid)["turns"][0]["status"] == "cancelled"
            assert not runtime.memory.pending_jobs()
            with sqlite3.connect(runtime.paths.memory / "long-term.sqlite") as db:
                assert "Synthetic cancelled preference" not in "\n".join(db.iterdump())
        finally:
            await runtime.close()

    asyncio.run(run())


def test_erasure_resumes_after_memory_commit_before_journal_closure_save(tmp_path, monkeypatch):
    first_secret = "SYNTHETIC_ERASURE_A_ORIGINAL_SOURCE"
    cross_secret = "SYNTHETIC_ERASURE_B_CROSS_SESSION_SOURCE"

    async def run():
        runtime = make_runtime(tmp_path)
        paths = runtime.paths
        original_session = runtime.create_session()["id"]
        first = await runtime.start_turn(original_session, first_secret)
        await runtime._tasks[first["id"]]
        first_fact = runtime.memory.remember(
            first_secret, source_id="turn:" + first["id"], source_text=first_secret
        )
        # Two facts share one source, while the second fact has another source
        # in a different conversation. Erasing A discovers B in memory's transaction.
        second_fact = runtime.memory.remember(
            cross_secret, source_id="turn:" + first["id"], source_text=first_secret
        )
        cross_session = runtime.create_session()["id"]
        second = await runtime.start_turn(cross_session, cross_secret)
        await runtime._tasks[second["id"]]
        linked = runtime.memory.remember(
            cross_secret, source_id="turn:" + second["id"], source_text=cross_secret
        )
        assert linked["id"] == second_fact["id"]
        original_forget_source = runtime.memory.forget_source
        crossed_commit_boundary = False

        def fail_after_memory_commit(source_id, **kwargs):
            nonlocal crossed_commit_boundary
            result = original_forget_source(source_id, **kwargs)
            crossed_commit_boundary = True
            # Simulate loss after memory's transaction commits, before runtime
            # can save the returned expanded fact/source closure in its own DB.
            assert first_fact["id"] in result["fact_ids"]
            assert second_fact["id"] in result["fact_ids"]
            raise OSError("synthetic erasure interruption after memory commit")

        monkeypatch.setattr(runtime.memory, "forget_source", fail_after_memory_commit)
        try:
            with pytest.raises(OSError, match="synthetic erasure interruption"):
                await runtime.forget_memory(first_fact["id"])
            assert crossed_commit_boundary
            # The interrupted journal has not yet been scrubbed, but it must be
            # unavailable to callers until recovery completes.
            with pytest.raises(RuntimeConflictError):
                runtime.get_session(original_session)
            with sqlite3.connect(paths.memory / "conversation.sqlite") as db:
                interrupted_dump = "\n".join(db.iterdump())
                assert first_secret in interrupted_dump and cross_secret in interrupted_dump
        finally:
            await runtime.close()

        reopened = SessionRuntime(paths, Store())
        try:
            assert not reopened.memory.list_facts()
            for sid in (original_session, cross_session):
                serialized = json.dumps(reopened.get_session(sid))
                assert first_secret not in serialized and cross_secret not in serialized
            for path in (
                paths.memory / "long-term.sqlite",
                paths.memory / "conversation.sqlite",
                paths.checkpoints / "chat-graph.sqlite",
            ):
                with sqlite3.connect(path) as db:
                    dump = "\n".join(db.iterdump())
                    assert first_secret not in dump and cross_secret not in dump
            with sqlite3.connect(paths.memory / "conversation.sqlite") as db:
                assert db.execute("SELECT count(*) FROM memory_erasure").fetchone()[0] == 0
        finally:
            await reopened.close()

    asyncio.run(run())
