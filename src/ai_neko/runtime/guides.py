"""Guide control operations owned by the existing session runtime.

Public-page fetches are bounded jobs, never an additional model/tool loop.
All durable content remains in the Memory Service's scoped GuideStore.
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import re
import time
from contextlib import suppress

from ai_neko.memory.guides import (
    MAX_BODY_CHARACTERS,
    GuideAccessError,
    GuideCapacityError,
    GuideConflictError,
    GuideInputError,
    _url_hash,
)
from ai_neko.runtime.lifecycle import _settled_mutation


def conversation_guides_v4(db):
    db.execute(
        "CREATE TABLE IF NOT EXISTS turn_guides (turn_id TEXT NOT NULL, guide_id TEXT NOT NULL, "
        "revision_id TEXT NOT NULL, invalidated INTEGER NOT NULL DEFAULT 0, "
        "PRIMARY KEY(turn_id,guide_id,revision_id))"
    )
    db.execute("CREATE INDEX IF NOT EXISTS turn_guides_document ON turn_guides(guide_id)")
    db.execute(
        "CREATE TABLE IF NOT EXISTS turn_guide_sources (turn_id TEXT NOT NULL,kind TEXT NOT NULL,"
        "value TEXT NOT NULL,PRIMARY KEY(turn_id,kind,value))"
    )
    db.execute(
        "CREATE TABLE IF NOT EXISTS guide_operations (request_id TEXT PRIMARY KEY, "
        "fingerprint TEXT, kind TEXT NOT NULL, guide_id TEXT, status TEXT NOT NULL, "
        "result TEXT, error TEXT, created_at REAL NOT NULL, updated_at REAL NOT NULL)"
    )
    # G1 saved identities in events. Record them before later deletions can erase
    # those events. Only source identities are copied, never page text.
    for row in db.execute("SELECT turn_id,payload FROM events").fetchall():
        try:
            event = json.loads(row[1])
            source = event.get("source", {})
            if source.get("status") == "read":
                db.executemany(
                    "INSERT OR IGNORE INTO turn_guide_sources VALUES (?,?,?)",
                    [(row[0], kind, value) for kind, value in guide_source_hashes(source)],
                )
            storage = source.get("storage", {})
            guide, revision = storage.get("guide_id", ""), storage.get("revision_id", "")
            if (
                storage.get("saved")
                and re.fullmatch(r"guide-[a-f0-9]{32}", guide)
                and re.fullmatch(r"revision-[a-f0-9]{32}", revision)
            ):
                db.execute(
                    "INSERT OR IGNORE INTO turn_guides VALUES (?,?,?,0)", (row[0], guide, revision)
                )
        except (ValueError, TypeError, AttributeError):
            continue
    while True:
        before = db.total_changes
        db.execute(
            "INSERT OR IGNORE INTO turn_guides SELECT h.turn_id,g.guide_id,g.revision_id,0 "
            "FROM turn_history h JOIN turn_guides g ON h.source_turn_id=g.turn_id"
        )
        if db.total_changes == before:
            break


def guide_source_hashes(source):
    """Track evidence even if retaining its body fails; these are not content copies."""
    values = set()
    for key in ("original_url", "url", "final_url"):
        if source.get(key):
            with suppress(GuideInputError):
                values.add(("url", _url_hash(source[key])))
    if isinstance(source.get("text"), str) and source["text"].strip():
        values.add(
            (
                "content",
                hashlib.sha256(source["text"][:MAX_BODY_CHARACTERS].encode()).hexdigest(),
            )
        )
    return values


def _request_id(value):
    if not isinstance(value, str) or not re.fullmatch(r"[a-f0-9]{32}", value):
        raise GuideInputError("invalid_request_id")
    return value


class GuideRuntimeMixin:
    def _initialize_guide_runtime(self):
        from ai_neko.memory.guide_retrieval import GuideRetriever
        from ai_neko.memory.guide_snapshots import GuideSnapshots

        self.guide_snapshots = GuideSnapshots(self.memory.guides)
        self.guide_retriever = GuideRetriever(self.memory.guides)
        self._guide_tasks = {}
        self._guide_turn_revisions = {}
        with self._db:
            for row in self._db.execute(
                "SELECT request_id,kind FROM guide_operations WHERE status='running'"
            ).fetchall():
                store = self.memory.guides
                receipt = store._db.execute(
                    "SELECT result FROM guide_requests WHERE scope=? AND request_id=? AND action=?",
                    (store.scope, row["request_id"], row["kind"]),
                ).fetchone()
                status, error, result, guide_id = "interrupted", "application_restarted", None, None
                if receipt:
                    result = self._guide_result(json.loads(receipt[0]))
                    guide_id = result.get("guide_id")
                    try:
                        store.get_document(guide_id, result.get("revision_id"))
                        status, error = "completed", None
                        result["replayed"] = True
                        result["superseded"] = result["revision"] != store.published_revision
                    except GuideAccessError:
                        status, error, result = "cancelled", "guide_removed", None
                self._db.execute(
                    "UPDATE guide_operations SET status=?,error=?,result=?,guide_id=?,updated_at=? "
                    "WHERE request_id=?",
                    (
                        status,
                        error,
                        json.dumps(result) if result else None,
                        guide_id,
                        time.time(),
                        row["request_id"],
                    ),
                )

    def _assert_guide_turn(self, session_id, turn_id):
        if self._closed or self._closing or self._memory_mutating or self._erasure_failed:
            raise asyncio.CancelledError
        self._assert_match_turn(session_id, turn_id)
        expected = self._guide_turn_revisions.get(turn_id)
        if expected is not None and expected != self.memory.guides.published_revision:
            raise asyncio.CancelledError
        if self._turn(session_id, turn_id)["status"] not in {"accepted", "running"}:
            raise asyncio.CancelledError

    async def guide_catalog(self):
        self._check_memory_available()

        def read():
            with self.memory.guides._guard:
                return {
                    "guides": self.memory.guides.list_documents(),
                    **self.memory.guides.control_snapshot(),
                }

        return await self.memory_api(read)

    async def guide_detail(self, guide_id, revision_id=None):
        return await self.memory_api(
            lambda: {
                "guide": self.memory.guides.get_document(guide_id, revision_id),
                "revision": self.memory.guides.revision(),
            }
        )

    def _guide_operation(self, request_id):
        row = self._db.execute(
            "SELECT * FROM guide_operations WHERE request_id=?", (request_id,)
        ).fetchone()
        if row is None:
            raise GuideAccessError("guide_operation_not_found")
        return {
            "request_id": request_id,
            "status": row["status"],
            "result": json.loads(row["result"]) if row["result"] else None,
            "error": row["error"],
        }

    async def guide_operation(self, request_id):
        self._check_memory_available()
        return self._guide_operation(_request_id(request_id))

    @_settled_mutation
    async def cancel_guide_operation(self, request_id):
        # Commit and cancellation share one linearization boundary. If saving
        # already began, wait for its real result instead of reporting cancelled
        # while a non-cancellable SQLite worker can still commit behind us.
        async with self._mutation_lock:
            return self._cancel_guide_operation(request_id)

    def _cancel_guide_operation(self, request_id):
        self._check_open()
        _request_id(request_id)
        now = time.time()
        with self._db:
            self._db.execute(
                "INSERT OR IGNORE INTO guide_operations VALUES (?,NULL,'cancel',NULL,'cancelled',NULL,NULL,?,?)",
                (request_id, now, now),
            )
            self._db.execute(
                "UPDATE guide_operations SET status='cancelled',updated_at=? WHERE request_id=? AND status='running'",
                (now, request_id),
            )
        task = self._guide_tasks.get(request_id)
        if task is not None:
            task.cancel()
        return self._guide_operation(request_id)

    async def _stop_guide_operations(self):
        tasks = tuple(self._guide_tasks.values())
        for request_id in tuple(self._guide_tasks):
            self._cancel_guide_operation(request_id)
        if tasks:
            await asyncio.gather(*tasks, return_exceptions=True)

    async def guide_fetch(self, value, guide_id=None):
        """Persist request identity before returning 202; polling is safe after disconnect."""
        self._check_memory_available()
        request_id = _request_id(value["request_id"])
        expected = value["expected_revision"]
        action = "refresh" if guide_id else "import"
        payload = {k: v for k, v in value.items() if k not in {"request_id", "expected_revision"}}
        if guide_id:
            payload["guide_id"] = guide_id
        fingerprint = hashlib.sha256(
            json.dumps([action, payload, expected], sort_keys=True).encode()
        ).hexdigest()
        async with self._mutation_lock:
            self._check_memory_available()
            existing = self._db.execute(
                "SELECT * FROM guide_operations WHERE request_id=?", (request_id,)
            ).fetchone()
            if existing:
                if existing["fingerprint"] is not None and existing["fingerprint"] != fingerprint:
                    raise GuideConflictError("request_payload_changed")
                return self._guide_operation(request_id)
            replay = await self._memory_call(
                self.memory.guides.preflight, action, payload, request_id, expected
            )
            source = (
                await self._memory_call(self.memory.guides.get_document, guide_id)
                if guide_id
                else None
            )
            if not replay:
                if len(self._guide_tasks) >= 4:
                    raise GuideCapacityError("guide_operations_limit")
                # Validate configuration before recording a running job: a
                # rejected adapter must never leave an operation with no task.
                from ai_neko.runtime.guide_query import web_adapter

                adapter = web_adapter(self)
            now = time.time()
            with self._db:
                self._db.execute(
                    "INSERT INTO guide_operations VALUES (?,?,?,?,?, ?,NULL,?,?)",
                    (
                        request_id,
                        fingerprint,
                        action,
                        guide_id,
                        "completed" if replay else "running",
                        json.dumps(self._guide_result(replay)) if replay else None,
                        now,
                        now,
                    ),
                )
            if not replay:
                task = asyncio.create_task(
                    self._run_guide_fetch(request_id, action, payload, expected, source, adapter)
                )
                self._guide_tasks[request_id] = task
                task.add_done_callback(lambda _, key=request_id: self._guide_tasks.pop(key, None))
            return self._guide_operation(request_id)

    @staticmethod
    def _guide_result(result):
        # Do not leave a second copy of body, titles, version quotations or URLs
        # inside request replay records. UI fetches current scoped details by ID.
        return {
            key: result[key]
            for key in (
                "saved",
                "guide_id",
                "revision_id",
                "revision",
                "deduplicated",
                "replayed",
                "superseded",
                "deleted",
                "not_modified",
            )
            if key in result
        }

    async def _run_guide_fetch(self, request_id, action, payload, expected, original, adapter):
        try:
            url = original["original_url"] if original else payload["url"]
            if original:
                from ai_neko.runtime.guide_query import read_selected

                result = await read_selected(adapter, original)
            else:
                result = await adapter.execute("read_web_page", {"url": url})
            async with self._mutation_lock:
                await self._save_fetched_guide(
                    request_id, action, payload, expected, original, result
                )
        except asyncio.CancelledError:
            if not self._closed:
                with self._db:
                    self._db.execute(
                        "UPDATE guide_operations SET status='cancelled',updated_at=? WHERE request_id=? AND status='running'",
                        (time.time(), request_id),
                    )
        except Exception as exc:
            if not self._closed:
                code = (
                    "guide_request_conflict"
                    if isinstance(exc, GuideConflictError)
                    else "guide_fetch_failed"
                )
                with self._db:
                    self._db.execute(
                        "UPDATE guide_operations SET status='error',error=?,updated_at=? WHERE request_id=? AND status='running'",
                        (code, time.time(), request_id),
                    )
        finally:
            self._guide_tasks.pop(request_id, None)

    async def _save_fetched_guide(self, request_id, action, payload, expected, original, result):
        self._check_memory_available()
        if self._guide_operation(request_id)["status"] != "running":
            raise asyncio.CancelledError
        if result.get("status") == "not_modified" and original:
            if result.get("final_url") != original["final_url"] or not (
                original.get("etag") or original.get("last_modified")
            ):
                raise GuideInputError("invalid_conditional_response")
            saved = await self._memory_call(
                self.memory.guides.mark_checked,
                original["guide_id"],
                original["revision_id"],
                checked_at=result["checked_at"],
                expected_revision=expected,
                request_id=request_id,
                operation_payload=payload,
            )
            with self._db:
                self._db.execute(
                    "UPDATE guide_operations SET status='completed',guide_id=?,result=?,updated_at=? WHERE request_id=?",
                    (
                        saved["guide_id"],
                        json.dumps(self._guide_result(saved)),
                        time.time(),
                        request_id,
                    ),
                )
            return
        sources = result.get("sources", [])
        if result.get("status") != "ok" or not sources or sources[0].get("status") != "read":
            raise GuideInputError("guide_page_unreadable")
        fields = ("game", "platform", "mode", "game_version", "version_basis")
        # A fetched new revision must not inherit an old asserted game version.
        metadata = (
            {k: payload[k] for k in fields if k in payload}
            if not original
            else {k: original[k] for k in ("game", "platform", "mode")}
        )
        saved = await self._memory_call(
            self.memory.guides.ingest,
            sources[0],
            expected_revision=expected,
            request_id=request_id,
            operation=action,
            operation_payload=payload,
            **metadata,
        )
        if self._guide_operation(request_id)["status"] == "running":
            with self._db:
                self._db.execute(
                    "UPDATE guide_operations SET status='completed',guide_id=?,result=?,updated_at=? WHERE request_id=?",
                    (
                        saved["guide_id"],
                        json.dumps(self._guide_result(saved)),
                        time.time(),
                        request_id,
                    ),
                )

    async def _recover_pending_guide_control(self):
        self._check_open()
        if self._closing:
            raise GuideConflictError("application_closing")
        row = self._db.execute("SELECT payload FROM memory_erasure WHERE id=1").fetchone()
        if row:
            self._begin_memory_mutation()
            invalidate = getattr(self.providers, "invalidate_search_cache", None)
            if invalidate:
                invalidate(self.memory.guides.scope)
            await self._stop_guide_operations()
            await self._stop_memory_work()
            await self._memory_call(self._complete_erasure, json.loads(row[0]))
            self._memory_mutating = self._erasure_failed = False
        self._check_memory_available()

    def _write_guide_intent(self, intent):
        with self._db:
            self._db.execute(
                "INSERT OR REPLACE INTO memory_erasure VALUES (1,?)", (json.dumps(intent),)
            )

    def _persist_guide_result(self, intent, result):
        # Persist committed result before cleanup so a retry after a crash does
        # not require a snapshot file already removed by the cleanup itself.
        intent = {**intent, "committed_result": result}
        self._write_guide_intent(intent)
        return intent

    def _guide_cleanup_hashes(self, guide_ids):
        values = set()
        store = self.memory.guides
        with store._guard:
            for guide_id in guide_ids:
                values.update(
                    ("url", row[0])
                    for row in store._db.execute(
                        "SELECT value FROM guide_url_hashes WHERE scope=? AND guide_id=?",
                        (store.scope, guide_id),
                    )
                )
                values.update(
                    ("content", row[0])
                    for row in store._db.execute(
                        "SELECT content_hash FROM guide_revisions WHERE scope=? AND guide_id=?",
                        (store.scope, guide_id),
                    )
                )
                values.update(
                    (row[0], row[1])
                    for row in store._db.execute(
                        "SELECT kind,value FROM guide_tombstone_hashes WHERE scope=? AND guide_id=?",
                        (store.scope, guide_id),
                    )
                )
        return sorted(values)

    @_settled_mutation
    async def guide_selection(self, value, *, _before_commit=None):
        return await self._guide_control("selection", value, _before_commit=_before_commit)

    @_settled_mutation
    async def guide_delete(self, guide_id, value):
        return await self._guide_control("delete", {**value, "guide_id": guide_id})

    @_settled_mutation
    async def restore_guides(self, backup_id, value):
        return await self._guide_control("restore", {**value, "backup_id": backup_id})

    async def _guide_control(self, action, value, *, _before_commit=None):
        async with self._mutation_lock:
            await self._recover_pending_guide_control()
            expected, request_id = value["expected_revision"], _request_id(value["request_id"])
            payload = {
                k: v
                for k, v in value.items()
                if k not in {"expected_revision", "request_id", "confirm"}
            }
            if action == "restore":
                checked = await self._memory_call(
                    self.guide_snapshots.inspect_restore,
                    payload["backup_id"],
                    expected_revision=expected,
                    request_id=request_id,
                )
                replay = checked if checked.get("replayed") else None
                affected = checked.get("guide_ids", [])
            else:
                replay = await self._memory_call(
                    self.memory.guides.preflight, action, payload, request_id, expected
                )
                affected = [payload["guide_id"]] if action == "delete" else []
            if replay:
                return replay
            if _before_commit is not None:
                _before_commit()
            intent = {
                "kind": "guide",
                "action": action,
                "payload": payload,
                "expected_revision": expected,
                "request_id": request_id,
                "guide_ids": affected,
                "guide_hashes": await self._memory_call(self._guide_cleanup_hashes, affected),
                "fact_ids": [],
                "source_ids": [],
                "user_turn_ids": [],
            }
            self._begin_memory_mutation()
            try:
                self._write_guide_intent(intent)
                invalidate = getattr(self.providers, "invalidate_search_cache", None)
                if invalidate:
                    invalidate(self.memory.guides.scope)
                try:
                    result = await self._memory_call(self._commit_guide_control, intent)
                except (GuideAccessError, GuideConflictError, GuideInputError, GuideCapacityError):
                    # Transactional rejection leaves the store unchanged. There
                    # is no committed deletion to replay or history to erase.
                    with self._db:
                        self._db.execute("DELETE FROM memory_erasure WHERE id=1")
                    raise
                intent = self._persist_guide_result(intent, result)
                await self._stop_guide_operations()
                await self._stop_memory_work()
                return await self._memory_call(self._complete_erasure, intent)
            except BaseException:
                self._erasure_failed = bool(
                    self._db.execute("SELECT 1 FROM memory_erasure").fetchone()
                )
                self._log_event("guide_control_failed", kind=action)
                raise
            finally:
                self._memory_mutating = self._erasure_failed
                if not self._memory_mutating:
                    self.start_memory_worker()

    def _commit_guide_control(self, intent):
        if "committed_result" in intent:
            return intent["committed_result"]
        fields = {
            "expected_revision": intent["expected_revision"],
            "request_id": intent["request_id"],
        }
        if intent["action"] == "selection":
            return self.memory.guides.set_selection(**intent["payload"], **fields)
        if intent["action"] == "delete":
            return self.memory.guides.delete_document(intent["payload"]["guide_id"], **fields)
        return self.guide_snapshots.restore(intent["payload"]["backup_id"], **fields)

    def _complete_guide_control(self, intent, database):
        result = self._commit_guide_control(intent)
        intent = {**intent, "committed_result": result}
        if intent["action"] == "restore":
            # Include documents unique to the incoming snapshot as well as the
            # pre-restore authority. Persist before removing any dependencies.
            intent["guide_hashes"] = sorted(
                {tuple(item) for item in intent.get("guide_hashes", [])}
                | set(self._guide_cleanup_hashes(result.get("guide_ids", [])))
            )
        with database:
            database.execute(
                "UPDATE memory_erasure SET payload=? WHERE id=1", (json.dumps(intent),)
            )
        self._sync_match_selections(database)
        if intent["action"] == "selection":
            previous = result.get("previous_selection")
            if previous and previous.get("guide_id"):
                with database:
                    database.execute(
                        "UPDATE turn_guides SET invalidated=1 WHERE guide_id=? AND revision_id=?",
                        (previous["guide_id"], previous["revision_id"]),
                    )
            with database:
                database.execute("DELETE FROM memory_erasure WHERE id=1")
            return result
        guide_ids = set(intent["guide_ids"]) | set(result.get("guide_ids", []))
        affected = set()
        for kind, value in intent.get("guide_hashes", []):
            affected.update(
                row[0]
                for row in database.execute(
                    "SELECT turn_id FROM turn_guide_sources WHERE kind=? AND value=?", (kind, value)
                )
            )
        for guide_id in guide_ids:
            affected.update(
                row[0]
                for row in database.execute(
                    "SELECT turn_id FROM turn_guides WHERE guide_id=?", (guide_id,)
                )
            )
            # Request records retain only IDs, but deleted documents must not
            # leave an apparently successful saved result in the operation UI.
            with database:
                database.execute(
                    "UPDATE guide_operations SET result=NULL,error='guide_removed',status='cancelled' WHERE guide_id=?",
                    (guide_id,),
                )
        intent["source_ids"] = sorted(
            set(intent.get("source_ids", [])) | {"turn:" + tid for tid in affected}
        )
        self.guide_snapshots.purge_deleted()
        self._erase_history(database, intent, None)
        return result

    async def guide_backups(self):
        async with self._mutation_lock:
            self._check_memory_available()
            return {
                "backups": await self._memory_call(self.guide_snapshots.list_backups),
                "revision": await self._memory_call(self.memory.guides.revision),
            }

    @_settled_mutation
    async def backup_guides(self, value):
        async with self._mutation_lock:
            self._check_memory_available()
            return await self._memory_call(
                self.guide_snapshots.backup, request_id=_request_id(value["request_id"])
            )

    async def delete_guide_backup(self, backup_id):
        async with self._mutation_lock:
            self._check_memory_available()
            return {
                "deleted": await self._memory_call(self.guide_snapshots.delete_backup, backup_id)
            }
