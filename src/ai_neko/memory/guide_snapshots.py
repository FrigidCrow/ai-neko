"""Independent, scope-bound guide snapshots, with deletion-safe explicit restore.

Snapshots contain only public guide authority. Derived chunks, personal memory,
runtime state, credentials and the request ledger are deliberately not exported.
SQL read from a snapshot is never executed: we inspect its fixed schema, read
bounded rows, validate all values, then insert parameters into the live store.
Runtime owns the durable cross-database cleanup intent around restore/delete.
"""

from __future__ import annotations

import hashlib
import json
import math
import os
import re
import sqlite3
import time
from contextlib import closing
from datetime import datetime
from pathlib import Path
from uuid import uuid4

from ai_neko.config.paths import DataRootError, safe_child
from ai_neko.memory import guides
from ai_neko.memory.guides import (
    GuideAccessError,
    GuideCapacityError,
    GuideInputError,
    GuideStore,
    _coverage,
    _date,
    _headings,
    _json,
    _merge_coverage,
    _split_chunks,
    _text,
    _url,
    _url_hash,
)

FORMAT_VERSION = 1
MAX_SNAPSHOT_BYTES = 128 * 1024 * 1024
MAX_SNAPSHOT_ROWS = 100_000
MAX_METADATA_BYTES = 4 * 1024 * 1024
_NAME = re.compile(r"guides-[a-f0-9]{32}\.sqlite\Z")
_GUIDE_ID = re.compile(r"guide-[a-f0-9]{32}\Z")
_REVISION_ID = re.compile(r"revision-[a-f0-9]{32}\Z")
_HASH = re.compile(r"[a-f0-9]{64}\Z")
_SCHEMA = {
    "snapshot_meta": "scope TEXT NOT NULL,format_version INTEGER NOT NULL,"
    "created_at REAL NOT NULL,revision INTEGER NOT NULL,complete INTEGER NOT NULL",
    "guide_documents": "scope TEXT NOT NULL,guide_id TEXT NOT NULL,canonical_url TEXT NOT NULL,"
    "original_url TEXT NOT NULL,game TEXT NOT NULL,platform TEXT NOT NULL,mode TEXT NOT NULL,"
    "created_at REAL NOT NULL,last_accessed_at REAL NOT NULL,current_revision_id TEXT NOT NULL,"
    "protected INTEGER NOT NULL",
    "guide_revisions": "scope TEXT NOT NULL,revision_id TEXT NOT NULL,guide_id TEXT NOT NULL,"
    "content_hash TEXT NOT NULL,body TEXT NOT NULL,metadata TEXT NOT NULL,created_at REAL NOT NULL",
    "guide_revision_checks": "scope TEXT NOT NULL,revision_id TEXT NOT NULL,"
    "last_checked_at TEXT NOT NULL,coverage TEXT NOT NULL",
    "guide_selections": "scope TEXT NOT NULL,game TEXT NOT NULL,platform TEXT NOT NULL,"
    "mode TEXT NOT NULL,guide_id TEXT NOT NULL,revision_id TEXT NOT NULL,"
    "selection_revision INTEGER NOT NULL,selected_at REAL NOT NULL",
    "guide_tombstones": "scope TEXT NOT NULL,guide_id TEXT NOT NULL,deleted_revision INTEGER NOT NULL,"
    "deleted_at REAL NOT NULL",
    "guide_tombstone_hashes": "scope TEXT NOT NULL,kind TEXT NOT NULL,value TEXT NOT NULL,"
    "guide_id TEXT NOT NULL",
    "guide_url_hashes": "scope TEXT NOT NULL,guide_id TEXT NOT NULL,value TEXT NOT NULL",
}
_CREATE = {name: f"CREATE TABLE {name} ({columns})" for name, columns in _SCHEMA.items()}
_COLUMNS = {
    name: [column.split()[0] for column in columns.split(",")] for name, columns in _SCHEMA.items()
}


class _ForeignSnapshot(GuideAccessError):
    pass


def _integer(value, *, minimum=0) -> None:
    if type(value) is not int or not minimum <= value <= 2**63 - 1:
        raise GuideInputError("invalid_guide_snapshot_integer")


def _timestamp(value) -> None:
    if type(value) not in (int, float) or not math.isfinite(value) or value < 0:
        raise GuideInputError("invalid_guide_snapshot_timestamp")


def _identifier(value, pattern) -> None:
    if not isinstance(value, str) or pattern.fullmatch(value) is None:
        raise GuideInputError("invalid_guide_snapshot_identifier")


def _object(raw: str, maximum=MAX_METADATA_BYTES) -> dict:
    if not isinstance(raw, str) or len(raw.encode("utf-8")) > maximum:
        raise GuideInputError("invalid_guide_snapshot_metadata")
    try:
        value = json.loads(raw)
    except (ValueError, RecursionError):
        raise GuideInputError("invalid_guide_snapshot_metadata") from None
    if not isinstance(value, dict):
        raise GuideInputError("invalid_guide_snapshot_metadata")
    return value


def _validate_coverage(value: dict, body: str) -> None:
    if set(value) != {
        "completeness",
        "completeness_reasons",
        "retained_characters",
        "extracted_characters",
    }:
        raise GuideInputError("invalid_guide_snapshot_coverage")
    if value["completeness"] not in ("full", "partial"):
        raise GuideInputError("invalid_guide_snapshot_coverage")
    reasons = value["completeness_reasons"]
    if not isinstance(reasons, list) or len(reasons) > 33:
        raise GuideInputError("invalid_guide_snapshot_coverage")
    for reason in reasons:
        _text(reason, 100, "completeness_reason")
    if reasons and value["completeness"] != "partial":
        raise GuideInputError("invalid_guide_snapshot_coverage")
    _integer(value["retained_characters"])
    _integer(value["extracted_characters"])
    if value["retained_characters"] != len(body) or value["extracted_characters"] < len(body):
        raise GuideInputError("invalid_guide_snapshot_coverage")


def _metadata(row: dict, document: dict) -> dict:
    value = _object(row["metadata"])
    expected = {
        "original_url",
        "url",
        "final_url",
        "title",
        "status",
        "completeness",
        "completeness_reasons",
        "retrieved_at",
        "content_date",
        "game_version",
        "version_basis",
        "etag",
        "last_modified",
        "headings",
        "retained_characters",
        "extracted_characters",
    }
    if set(value) != expected or value["status"] != "read":
        raise GuideInputError("invalid_guide_snapshot_metadata")
    if (
        _url(value["original_url"]) != document["canonical_url"]
        or _url(value["url"]) != value["final_url"]
        or _url(value["final_url"]) != value["final_url"]
    ):
        raise GuideInputError("invalid_guide_snapshot_url")
    _text(value["title"], 1000, "title")
    _date(value["retrieved_at"], "retrieved_at", required=True)
    _date(value["content_date"], "content_date")
    for key, maximum in (
        ("game_version", 200),
        ("version_basis", 2000),
        ("etag", 1024),
        ("last_modified", 1024),
    ):
        if value[key] is not None:
            _text(value[key], maximum, key)
    if _headings(value["headings"], row["body"]) != value["headings"]:
        raise GuideInputError("invalid_guide_snapshot_headings")
    _validate_coverage(_coverage(value), row["body"])
    return value


class GuideSnapshots:
    def __init__(self, store: GuideStore):
        self.store = store

    def _directory(self) -> Path:
        directory = safe_child(self.store.paths.root, "backups")
        if directory != self.store.paths.backups or not directory.is_dir():
            raise GuideInputError("invalid_guides_backup_directory")
        return directory

    def _path(self, backup_id: str, *, exists=True, allow_sidecars=False) -> Path:
        if not isinstance(backup_id, str) or _NAME.fullmatch(backup_id) is None:
            raise GuideInputError("invalid_guide_backup_id")
        directory = self._directory()
        path = safe_child(directory, backup_id)
        if exists and not path.is_file():
            raise GuideAccessError("guide_backup_not_found")
        if path.exists() and not path.is_file():
            raise GuideInputError("invalid_guide_backup_file")
        for suffix in ("-wal", "-shm", "-journal"):
            sidecar = safe_child(directory, backup_id + suffix)
            if sidecar.exists() and (not allow_sidecars or not sidecar.is_file()):
                raise GuideInputError("invalid_guide_backup_sidecar")
        return path

    def _owner(self, backup_id: str) -> dict | None:
        if not isinstance(backup_id, str) or _NAME.fullmatch(backup_id) is None:
            raise GuideInputError("invalid_guide_backup_id")
        row = self.store._db.execute(
            "SELECT scope,created_at FROM guide_backup_registry WHERE backup_id=?", (backup_id,)
        ).fetchone()
        return dict(row) if row is not None else None

    def _register(self, backup_id: str, created_at: float) -> None:
        """Commit ownership separately, before an interrupted writer can leave bytes."""
        _timestamp(created_at)
        with self.store._transaction():
            owner = self._owner(backup_id)
            if owner is not None and owner["scope"] != self.store.scope:
                raise _ForeignSnapshot("guide_backup_wrong_scope")
            self.store._db.execute(
                "INSERT OR IGNORE INTO guide_backup_registry VALUES (?,?,?)",
                (backup_id, self.store.scope, created_at),
            )

    def _claim_existing(self, backup_id: str) -> None:
        """Legacy valid headers may prove scope; unreadable unregistered files may not."""
        owner = self._owner(backup_id)
        if owner is not None:
            if owner["scope"] != self.store.scope:
                raise _ForeignSnapshot("guide_backup_wrong_scope")
            return
        try:
            metadata = self._read(backup_id, metadata_only=True)
        except GuideInputError as exc:
            # Normal snapshots cannot enter this state: registration commits
            # first. An external/unregistered damaged file needs manual recovery
            # outside this fixed-scope API; its filename never authorizes erasure.
            raise GuideInputError("unregistered_guide_backup_requires_manual_recovery") from exc
        self._register(backup_id, metadata["created_at"])

    def _discard_owned(self, backup_id: str) -> bool:
        owner = self._owner(backup_id)
        if owner is None or owner["scope"] != self.store.scope:
            raise _ForeignSnapshot("guide_backup_wrong_scope")
        path = self._path(backup_id, exists=False, allow_sidecars=True)
        # Validate every path before unlinking any of them. Do not follow a
        # redirected WAL/journal or leave known crash-time body copies behind.
        files = [
            path,
            *(
                safe_child(path.parent, backup_id + suffix)
                for suffix in ("-wal", "-shm", "-journal")
            ),
        ]
        found = any(item.exists() for item in files)
        for item in reversed(files):
            item.unlink(missing_ok=True)
        return found

    def _read(self, backup_id: str, *, metadata_only=False) -> dict:
        owner = self._owner(backup_id)
        if owner is not None and owner["scope"] != self.store.scope:
            raise _ForeignSnapshot("guide_backup_wrong_scope")
        path = self._path(backup_id)
        size = path.stat().st_size
        if size > MAX_SNAPSHOT_BYTES:
            raise GuideInputError("guide_backup_too_large")
        try:
            # immutable prevents SQLite from consuming an adjacent untrusted WAL
            # or making any journal/lock writes in the managed backup directory.
            with closing(sqlite3.connect(path.as_uri() + "?mode=ro&immutable=1", uri=True)) as db:
                db.row_factory = sqlite3.Row
                db.execute("PRAGMA trusted_schema=OFF")
                db.setlimit(sqlite3.SQLITE_LIMIT_LENGTH, MAX_METADATA_BYTES + 200_000)
                db.setlimit(sqlite3.SQLITE_LIMIT_COLUMN, 64)
                db.setlimit(sqlite3.SQLITE_LIMIT_EXPR_DEPTH, 64)
                deadline = time.monotonic() + 5
                db.set_progress_handler(lambda: int(time.monotonic() > deadline), 1000)
                schema = db.execute("SELECT type,name,sql FROM sqlite_master").fetchall()
                if len(schema) != len(_CREATE) or any(
                    row["type"] != "table"
                    or row["name"] not in _CREATE
                    or row["sql"] != _CREATE[row["name"]]
                    for row in schema
                ):
                    raise GuideInputError("invalid_guide_snapshot_schema")
                meta_rows = db.execute("SELECT * FROM snapshot_meta LIMIT 2").fetchall()
                if len(meta_rows) != 1:
                    raise GuideInputError("invalid_guide_snapshot_metadata")
                meta = dict(meta_rows[0])
                if meta["scope"] != self.store.scope:
                    raise _ForeignSnapshot("guide_backup_wrong_scope")
                if metadata_only:
                    return meta
                if db.execute("PRAGMA user_version").fetchone()[0] != FORMAT_VERSION:
                    raise GuideInputError("unsupported_guide_snapshot_version")
                if (
                    type(meta["format_version"]) is not int
                    or meta["format_version"] != FORMAT_VERSION
                ):
                    raise GuideInputError("unsupported_guide_snapshot_version")
                if type(meta["complete"]) is not int or meta["complete"] != 1:
                    raise GuideInputError("incomplete_guide_snapshot")
                _timestamp(meta["created_at"])
                _integer(meta["revision"])
                if meta["revision"] >= 2**63 - 2:
                    raise GuideInputError("guide_snapshot_revision_exhausted")
                result, total = {"snapshot_meta": meta, "bytes": size}, 0
                for table in _CREATE:
                    if table == "snapshot_meta":
                        continue
                    count = db.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0]
                    total += count
                    if total > MAX_SNAPSHOT_ROWS:
                        raise GuideInputError("guide_backup_too_many_rows")
                    result[table] = [dict(row) for row in db.execute(f"SELECT * FROM {table}")]
                self._validate(result)
                return result
        except (sqlite3.Error, OSError, UnicodeError, OverflowError, RecursionError) as exc:
            raise GuideInputError("invalid_guide_snapshot") from exc

    def _validate(self, data: dict) -> None:
        for table, columns in _COLUMNS.items():
            if table == "snapshot_meta":
                continue
            for row in data[table]:
                if set(row) != set(columns) or row["scope"] != self.store.scope:
                    raise GuideInputError("invalid_guide_snapshot_scope")
        documents = {}
        urls = set()
        for row in data["guide_documents"]:
            _identifier(row["guide_id"], _GUIDE_ID)
            _identifier(row["current_revision_id"], _REVISION_ID)
            if row["guide_id"] in documents or row["canonical_url"] in urls:
                raise GuideInputError("duplicate_guide_snapshot_document")
            if (
                _url(row["canonical_url"]) != row["canonical_url"]
                or _url(row["original_url"]) != row["canonical_url"]
            ):
                raise GuideInputError("invalid_guide_snapshot_url")
            for key in ("game", "platform", "mode"):
                _text(row[key], 200, key)
            _timestamp(row["created_at"])
            _timestamp(row["last_accessed_at"])
            if type(row["protected"]) is not int or row["protected"] not in (0, 1):
                raise GuideInputError("invalid_guide_snapshot_protection")
            documents[row["guide_id"]] = row
            urls.add(row["canonical_url"])
        revisions, hashes = {}, set()
        for row in data["guide_revisions"]:
            _identifier(row["revision_id"], _REVISION_ID)
            _identifier(row["content_hash"], _HASH)
            if row["revision_id"] in revisions or (row["guide_id"], row["content_hash"]) in hashes:
                raise GuideInputError("duplicate_guide_snapshot_revision")
            if row["guide_id"] not in documents:
                raise GuideInputError("orphan_guide_snapshot_revision")
            _text(row["body"], guides.MAX_BODY_CHARACTERS, "guide_body", empty=False)
            if hashlib.sha256(row["body"].encode("utf-8")).hexdigest() != row["content_hash"]:
                raise GuideInputError("guide_snapshot_hash_mismatch")
            _timestamp(row["created_at"])
            _metadata(row, documents[row["guide_id"]])
            revisions[row["revision_id"]] = row
            hashes.add((row["guide_id"], row["content_hash"]))
        for row in documents.values():
            current = revisions.get(row["current_revision_id"])
            if current is None or current["guide_id"] != row["guide_id"]:
                raise GuideInputError("invalid_guide_snapshot_current_revision")
        aliases = set()
        for row in data["guide_url_hashes"]:
            key = row["guide_id"], row["value"]
            if key in aliases or row["guide_id"] not in documents:
                raise GuideInputError("invalid_guide_snapshot_url_hash")
            _identifier(row["value"], _HASH)
            aliases.add(key)
        for row in documents.values():
            if (row["guide_id"], _url_hash(row["canonical_url"])) not in aliases:
                raise GuideInputError("missing_guide_snapshot_url_hash")
        checks = set()
        for row in data["guide_revision_checks"]:
            if row["revision_id"] in checks or row["revision_id"] not in revisions:
                raise GuideInputError("invalid_guide_snapshot_check")
            _date(row["last_checked_at"], "last_checked_at", required=True)
            coverage = _object(row["coverage"], 8192)
            revision = revisions[row["revision_id"]]
            metadata = _object(revision["metadata"])
            _validate_coverage(coverage, revision["body"])
            if _merge_coverage(_coverage(metadata), coverage) != coverage or (
                datetime.fromisoformat(row["last_checked_at"].replace("Z", "+00:00"))
                < datetime.fromisoformat(metadata["retrieved_at"].replace("Z", "+00:00"))
            ):
                raise GuideInputError("regressed_guide_snapshot_coverage")
            checks.add(row["revision_id"])
        if checks != set(revisions):
            raise GuideInputError("missing_guide_snapshot_check")
        selections = set()
        for row in data["guide_selections"]:
            key = tuple(row[item] for item in ("game", "platform", "mode"))
            for name in ("game", "platform", "mode"):
                _text(row[name], 200, name, empty=False)
                if row[name] != row[name].strip():
                    raise GuideInputError("invalid_guide_snapshot_selection")
            if key in selections or row["guide_id"] not in documents:
                raise GuideInputError("invalid_guide_snapshot_selection")
            if any(
                documents[row["guide_id"]][name] != row[name]
                for name in ("game", "platform", "mode")
            ):
                raise GuideInputError("invalid_guide_snapshot_selection_dimensions")
            revision = revisions.get(row["revision_id"])
            if revision is None or revision["guide_id"] != row["guide_id"]:
                raise GuideInputError("invalid_guide_snapshot_selection")
            _integer(row["selection_revision"], minimum=1)
            if row["selection_revision"] > data["snapshot_meta"]["revision"]:
                raise GuideInputError("invalid_guide_snapshot_selection_revision")
            _timestamp(row["selected_at"])
            selections.add(key)
        tombstones = set()
        for row in data["guide_tombstones"]:
            _identifier(row["guide_id"], _GUIDE_ID)
            if row["guide_id"] in tombstones:
                raise GuideInputError("duplicate_guide_snapshot_tombstone")
            _integer(row["deleted_revision"], minimum=1)
            if row["deleted_revision"] > data["snapshot_meta"]["revision"]:
                raise GuideInputError("invalid_guide_snapshot_tombstone_revision")
            _timestamp(row["deleted_at"])
            tombstones.add(row["guide_id"])
        seen_hashes = set()
        for row in data["guide_tombstone_hashes"]:
            key = row["kind"], row["value"], row["guide_id"]
            if (
                row["kind"] not in ("url", "content")
                or key in seen_hashes
                or row["guide_id"] not in tombstones
            ):
                raise GuideInputError("invalid_guide_snapshot_tombstone_hash")
            _identifier(row["value"], _HASH)
            seen_hashes.add(key)

    @staticmethod
    def _descriptor(backup_id: str, data: dict) -> dict:
        return {
            "backup_id": backup_id,
            "created_at": data["snapshot_meta"]["created_at"],
            "revision": data["snapshot_meta"]["revision"],
            "format_version": FORMAT_VERSION,
            "bytes": data["bytes"],
            "documents": len(data["guide_documents"]),
            "revisions": len(data["guide_revisions"]),
            "selections": len(data["guide_selections"]),
        }

    def list_backups(self) -> list[dict]:
        with self.store._guard:
            self.store._open()
            result = []
            for path in self._directory().glob("guides-*.sqlite"):
                if not _NAME.fullmatch(path.name):
                    continue
                try:
                    result.append(self._descriptor(path.name, self._read(path.name)))
                except GuideInputError:
                    # Registered damaged snapshots remain discoverable and can
                    # be explicitly deleted without interpreting their SQLite.
                    owner = self._owner(path.name)
                    if owner is not None and owner["scope"] == self.store.scope:
                        result.append(
                            {
                                "backup_id": path.name,
                                "created_at": owner["created_at"],
                                "status": "corrupt",
                                "restorable": False,
                            }
                        )
                except (GuideAccessError, DataRootError):
                    continue
            return sorted(
                result, key=lambda item: (item["created_at"], item["backup_id"]), reverse=True
            )

    def backup(self, *, request_id: str | None = None) -> dict:
        with self.store._guard:
            self.store._open()
            if request_id is not None:
                replay = self.store.preflight("backup", {}, request_id, None)
                if replay is not None:
                    return replay
            token = (
                hashlib.sha256((self.store.scope + "\0" + request_id).encode()).hexdigest()[:32]
                if request_id is not None
                else uuid4().hex
            )
            backup_id = f"guides-{token}.sqlite"
            path = self._path(backup_id, exists=False, allow_sidecars=True)
            owner = self._owner(backup_id)
            if owner is not None and owner["scope"] != self.store.scope:
                raise _ForeignSnapshot("guide_backup_wrong_scope")
            # A crash after a completed file but before the request ledger commit
            # can replay that same file; no second, later snapshot is substituted.
            result = None
            if path.exists():
                self._claim_existing(backup_id)
                try:
                    result = self._descriptor(backup_id, self._read(backup_id))
                except GuideInputError:
                    # A previously registered, unfinished write never produced
                    # a receipt. Remove its partial bytes before retrying it.
                    self._discard_owned(backup_id)
            self._register(backup_id, time.time())
            with self.store._transaction():
                if result is None:
                    result = self._write_backup(backup_id)
                if request_id is not None:
                    self.store._record_request("backup", {}, request_id, result, None)
                return result

    def _write_backup(self, backup_id: str) -> dict:
        """Caller owns the store transaction; ownership already committed."""
        path = self._path(backup_id, exists=False, allow_sidecars=True)
        self._discard_owned(backup_id)  # Clear orphaned registered sidecars too.
        try:
            fd = os.open(path, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
            os.close(fd)
            with closing(sqlite3.connect(path)) as target:
                target.execute("PRAGMA journal_mode=DELETE")
                target.execute("PRAGMA secure_delete=ON")
                for statement in _CREATE.values():
                    target.execute(statement)
                target.execute(f"PRAGMA user_version={FORMAT_VERSION}")
                target.execute(
                    "INSERT INTO snapshot_meta VALUES (?,?,?,?,0)",
                    (self.store.scope, FORMAT_VERSION, time.time(), self.store.revision()),
                )
                target.commit()
                for table, columns in _COLUMNS.items():
                    if table == "snapshot_meta":
                        continue
                    rows = self.store._db.execute(
                        f"SELECT {','.join(columns)} FROM {table} WHERE scope=?",
                        (self.store.scope,),
                    )
                    target.executemany(
                        f"INSERT INTO {table} VALUES ({','.join('?' for _ in columns)})",
                        rows,
                    )
                target.execute("UPDATE snapshot_meta SET complete=1")
                target.commit()
            return self._descriptor(backup_id, self._read(backup_id))
        except BaseException:
            self._discard_owned(backup_id)
            raise

    def inspect_restore(
        self, backup_id: str, *, expected_revision: int | None = None, request_id: str | None = None
    ) -> dict:
        with self.store._guard:
            self.store._open()
            if request_id is not None:
                replay = self.store.preflight(
                    "restore", {"backup_id": backup_id}, request_id, expected_revision
                )
                if replay is not None:
                    return {**replay, "replayed": True}
            elif expected_revision is not None and self.store.revision() != expected_revision:
                raise guides.GuideConflictError("guide_revision_conflict")
            data = self._read(backup_id)
            self._check_identity(data)
            self._check_capacity(data, self._blocked(data))
            current = self.store._db.execute(
                "SELECT guide_id FROM guide_documents WHERE scope=?", (self.store.scope,)
            )
            ids = {row[0] for row in current} | {row["guide_id"] for row in data["guide_documents"]}
            return {
                "backup_id": backup_id,
                "revision": self.store.revision(),
                "guide_ids": sorted(ids),
            }

    def restore(self, backup_id: str, *, expected_revision: int, request_id: str) -> dict:
        with self.store._guard:
            self.store._open()
            payload = {"backup_id": backup_id}
            replay = self.store.preflight("restore", payload, request_id, expected_revision)
            if replay is not None:
                return replay
            data = self._read(backup_id)  # All validation precedes any live mutation.
            with self.store._transaction():
                replay = self.store.preflight("restore", payload, request_id, expected_revision)
                if replay is not None:
                    return replay
                self._check_identity(data)
                blocked = self._blocked(data)
                self._check_capacity(data, blocked)
                current = self.store._db.execute(
                    "SELECT guide_id FROM guide_documents WHERE scope=?", (self.store.scope,)
                )
                affected = {row[0] for row in current} | {
                    row["guide_id"] for row in data["guide_documents"]
                }
                # Tombstones never roll back, including when this snapshot carries
                # a deletion from a newer store lineage than the current process.
                for row in data["guide_tombstones"]:
                    self.store._db.execute(
                        "INSERT INTO guide_tombstones VALUES (?,?,?,?) ON CONFLICT(scope,guide_id) DO UPDATE SET "
                        "deleted_revision=MAX(deleted_revision,excluded.deleted_revision),"
                        "deleted_at=MAX(deleted_at,excluded.deleted_at)",
                        tuple(row[key] for key in _COLUMNS["guide_tombstones"]),
                    )
                for row in data["guide_tombstone_hashes"]:
                    self.store._db.execute(
                        "INSERT OR IGNORE INTO guide_tombstone_hashes VALUES (?,?,?,?)",
                        tuple(row[key] for key in _COLUMNS["guide_tombstone_hashes"]),
                    )
                self.store._db.execute(
                    "DELETE FROM guide_selections WHERE scope=?", (self.store.scope,)
                )
                self.store._db.execute(
                    "DELETE FROM guide_documents WHERE scope=?", (self.store.scope,)
                )
                # Keep global scope revisions monotonic even across restored snapshots.
                self.store._db.execute(
                    "INSERT INTO guide_control VALUES (?,?) ON CONFLICT(scope) "
                    "DO UPDATE SET revision=MAX(revision,excluded.revision)",
                    (self.store.scope, data["snapshot_meta"]["revision"]),
                )
                revision = self.store._advance_revision()
                revision_guides = {
                    row["revision_id"]: row["guide_id"] for row in data["guide_revisions"]
                }
                for table in (
                    "guide_documents",
                    "guide_revisions",
                    "guide_revision_checks",
                    "guide_selections",
                    "guide_url_hashes",
                ):
                    for original in data[table]:
                        row = dict(original)
                        guide_id = row.get("guide_id")
                        if table == "guide_revision_checks":
                            guide_id = revision_guides[row["revision_id"]]
                        if guide_id in blocked:
                            continue
                        if table == "guide_selections":
                            row["selection_revision"] = revision
                            row["selected_at"] = time.time()
                        columns = _COLUMNS[table]
                        self.store._db.execute(
                            f"INSERT INTO {table} ({','.join(columns)}) VALUES ({','.join('?' for _ in columns)})",
                            tuple(row[key] for key in columns),
                        )
                        if table == "guide_revisions":
                            self.store._chunks_write(
                                row["revision_id"],
                                row["body"],
                                json.loads(row["metadata"])["headings"],
                            )
                if self.store._bytes() > guides.MAX_STORAGE_BYTES:
                    raise GuideCapacityError("guide_capacity_exhausted")
                result = {
                    "backup_id": backup_id,
                    "revision": revision,
                    "guide_ids": sorted(affected),
                }
                self.store._record_request(
                    "restore", payload, request_id, result, expected_revision
                )
                return result

    def _blocked(self, data: dict) -> set[str]:
        # Include incoming deletion evidence before any mutation, both for the
        # restore preflight and for fail-closed deletion of inconsistent snapshots.
        blocked = {
            row[0]
            for row in self.store._db.execute(
                "SELECT guide_id FROM guide_tombstones WHERE scope=?", (self.store.scope,)
            )
        }
        blocked.update(row["guide_id"] for row in data["guide_tombstones"])
        hashes = {
            (row[0], row[1])
            for row in self.store._db.execute(
                "SELECT kind,value FROM guide_tombstone_hashes WHERE scope=?", (self.store.scope,)
            )
        }
        hashes.update((row["kind"], row["value"]) for row in data["guide_tombstone_hashes"])
        for row in data["guide_url_hashes"]:
            if ("url", row["value"]) in hashes:
                blocked.add(row["guide_id"])
        for row in data["guide_documents"]:
            if ("url", _url_hash(row["canonical_url"])) in hashes:
                blocked.add(row["guide_id"])
        for row in data["guide_revisions"]:
            metadata = json.loads(row["metadata"])
            if ("url", _url_hash(metadata["final_url"])) in hashes or (
                "content",
                row["content_hash"],
            ) in hashes:
                blocked.add(row["guide_id"])
        return blocked & {row["guide_id"] for row in data["guide_documents"]}

    def _check_identity(self, data: dict) -> None:
        """A restore may select an old revision, never redefine a known ID."""
        documents = {
            row["guide_id"]: row["canonical_url"]
            for row in self.store._db.execute(
                "SELECT guide_id,canonical_url FROM guide_documents WHERE scope=?",
                (self.store.scope,),
            )
        }
        for row in data["guide_documents"]:
            if row["guide_id"] in documents and documents[row["guide_id"]] != row["canonical_url"]:
                raise GuideInputError("guide_snapshot_document_identity_conflict")
        for row in data["guide_revisions"]:
            current = self.store._db.execute(
                "SELECT * FROM guide_revisions WHERE scope=? AND revision_id=?",
                (self.store.scope, row["revision_id"]),
            ).fetchone()
            if current is not None and any(
                current[key] != row[key] for key in _COLUMNS["guide_revisions"]
            ):
                raise GuideInputError("guide_snapshot_revision_identity_conflict")

    def _check_capacity(self, data: dict, blocked: set[str]) -> None:
        total = 0
        revisions = {row["revision_id"]: row["guide_id"] for row in data["guide_revisions"]}
        # Match the store's documented logical quota, including freshly derived
        # chunks and every other scope, without first deleting the live documents.
        for table, (strings, numbers) in guides._COLUMNS.items():
            expression = "+".join(
                [f"length(CAST({name} AS BLOB))" for name in strings.split()]
                + [str(8 * len(numbers.split()))]
            )
            total += self.store._db.execute(
                f"SELECT COALESCE(SUM({expression}),0) FROM {table} WHERE scope<>?",
                (self.store.scope,),
            ).fetchone()[0]
            if table == "guide_chunks":
                for revision in data["guide_revisions"]:
                    if revision["guide_id"] in blocked:
                        continue
                    for chunk in _split_chunks(
                        revision["revision_id"],
                        revision["body"],
                        json.loads(revision["metadata"])["headings"],
                    ):
                        total += (
                            sum(
                                len(value.encode("utf-8"))
                                for value in (
                                    self.store.scope,
                                    chunk["chunk_id"],
                                    chunk["revision_id"],
                                    chunk["text"],
                                    _json(chunk["headings"]),
                                )
                            )
                            + 24
                        )
            else:
                for row in data[table]:
                    guide_id = row.get("guide_id", revisions.get(row.get("revision_id")))
                    if guide_id not in blocked:
                        total += sum(
                            len(row[key].encode("utf-8")) for key in strings.split()
                        ) + 8 * len(numbers.split())
            if total > guides.MAX_STORAGE_BYTES:
                raise GuideCapacityError("guide_capacity_exhausted")

    def delete_backup(self, backup_id: str) -> bool:
        with self.store._guard:
            self.store._open()
            self._claim_existing(backup_id)
            if not self._discard_owned(backup_id):
                raise GuideAccessError("guide_backup_not_found")
            return True

    def purge_deleted(self) -> list[str]:
        """Remove whole affected snapshots; Runtime keeps its intent on failures."""
        with self.store._guard:
            self.store._open()
            if not self.store.export_tombstones():
                return []
            removed = []
            # Registry entries also locate orphaned WAL/journal bytes when the
            # main file disappeared during an interrupted write or cleanup.
            candidates = {
                path.name
                for path in self._directory().glob("guides-*.sqlite")
                if _NAME.fullmatch(path.name)
            }
            candidates.update(
                row[0]
                for row in self.store._db.execute(
                    "SELECT backup_id FROM guide_backup_registry WHERE scope=?", (self.store.scope,)
                )
            )
            for backup_id in sorted(candidates):
                try:
                    self._claim_existing(backup_id)
                except _ForeignSnapshot:
                    continue
                try:
                    data = self._read(backup_id)
                    delete = bool(self._blocked(data))
                except (GuideInputError, GuideAccessError):
                    delete = True  # Registry proves scope even with a bad header.
                if delete and self._discard_owned(backup_id):
                    removed.append(backup_id)
            return sorted(removed)
