"""Scope-bound public guide documents, immutable revisions and rebuildable chunks.

This database is independent of personal memory and checkpoints. The caller must
supply the successful public-page reader result before model-context clipping.
The store never fetches a URL, infers a game version, or adopts a guide for a user.
"""

from __future__ import annotations

import hashlib
import ipaddress
import json
import math
import re
import sqlite3
import time
from contextlib import contextmanager
from datetime import datetime
from threading import RLock
from uuid import uuid4

from ai_neko.config.paths import DataPaths, safe_child
from ai_neko.config.schema import apply_migrations, ensure_supported_schema
from ai_neko.tools.network import NetworkPolicyError, parse_url, public_ip

MAX_BODY_CHARACTERS = 50_000
MAX_STORAGE_BYTES = 100 * 1024 * 1024


class GuideInputError(ValueError):
    """A source is not a usable public page body or has invalid metadata."""


class GuideAccessError(ValueError):
    """No such document or revision exists in the current scope."""


class GuideCapacityError(ValueError):
    """The body remains usable this turn, but cannot be durably saved."""


class GuideConflictError(ValueError):
    """The guide store is closed or cannot accept this operation."""


def _guides_v2(db: sqlite3.Connection) -> None:
    # execute(), not executescript(): DDL and the version ledger share a transaction.
    statements = (
        """CREATE TABLE IF NOT EXISTS guide_documents (
            scope TEXT NOT NULL, guide_id TEXT NOT NULL, canonical_url TEXT NOT NULL,
            original_url TEXT NOT NULL, game TEXT NOT NULL, platform TEXT NOT NULL,
            mode TEXT NOT NULL, created_at REAL NOT NULL, last_accessed_at REAL NOT NULL,
            current_revision_id TEXT NOT NULL, protected INTEGER NOT NULL DEFAULT 0,
            PRIMARY KEY(scope,guide_id), UNIQUE(scope,canonical_url)
        )""",
        """CREATE TABLE IF NOT EXISTS guide_revisions (
            scope TEXT NOT NULL, revision_id TEXT NOT NULL, guide_id TEXT NOT NULL,
            content_hash TEXT NOT NULL, body TEXT NOT NULL, metadata TEXT NOT NULL,
            created_at REAL NOT NULL, PRIMARY KEY(scope,revision_id),
            UNIQUE(scope,guide_id,content_hash),
            FOREIGN KEY(scope,guide_id) REFERENCES guide_documents(scope,guide_id)
                ON DELETE CASCADE
        )""",
        """CREATE TABLE IF NOT EXISTS guide_revision_checks (
            scope TEXT NOT NULL, revision_id TEXT NOT NULL, last_checked_at TEXT NOT NULL,
            coverage TEXT NOT NULL,
            PRIMARY KEY(scope,revision_id),
            FOREIGN KEY(scope,revision_id) REFERENCES guide_revisions(scope,revision_id)
                ON DELETE CASCADE
        )""",
        """CREATE TABLE IF NOT EXISTS guide_chunks (
            scope TEXT NOT NULL, chunk_id TEXT NOT NULL, revision_id TEXT NOT NULL,
            ordinal INTEGER NOT NULL, start INTEGER NOT NULL, end INTEGER NOT NULL,
            body TEXT NOT NULL, headings TEXT NOT NULL,
            PRIMARY KEY(scope,chunk_id), UNIQUE(scope,revision_id,ordinal),
            FOREIGN KEY(scope,revision_id) REFERENCES guide_revisions(scope,revision_id)
                ON DELETE CASCADE
        )""",
        "CREATE INDEX IF NOT EXISTS guide_lru ON guide_documents(scope,protected,last_accessed_at)",
    )
    for statement in statements:
        db.execute(statement)
    columns = {row[1] for row in db.execute("PRAGMA table_info(guide_revision_checks)")}
    if "coverage" not in columns:
        db.execute(
            "ALTER TABLE guide_revision_checks ADD COLUMN coverage TEXT NOT NULL DEFAULT '{}'"
        )
    for row in db.execute(
        "SELECT c.scope,c.revision_id,r.metadata FROM guide_revision_checks c"
        " JOIN guide_revisions r ON r.scope=c.scope AND r.revision_id=c.revision_id"
        " WHERE c.coverage='{}'"
    ).fetchall():
        db.execute(
            "UPDATE guide_revision_checks SET coverage=? WHERE scope=? AND revision_id=?",
            (_json(_coverage(json.loads(row[2]))), row[0], row[1]),
        )


def _guides_v3(db: sqlite3.Connection) -> None:
    for statement in (
        """CREATE TABLE IF NOT EXISTS guide_control (
            scope TEXT PRIMARY KEY, revision INTEGER NOT NULL DEFAULT 0
        )""",
        """CREATE TABLE IF NOT EXISTS guide_selections (
            scope TEXT NOT NULL, game TEXT NOT NULL, platform TEXT NOT NULL, mode TEXT NOT NULL,
            guide_id TEXT NOT NULL, revision_id TEXT NOT NULL,
            selection_revision INTEGER NOT NULL, selected_at REAL NOT NULL,
            PRIMARY KEY(scope,game,platform,mode),
            FOREIGN KEY(scope,guide_id) REFERENCES guide_documents(scope,guide_id)
                ON DELETE CASCADE,
            FOREIGN KEY(scope,revision_id) REFERENCES guide_revisions(scope,revision_id)
                ON DELETE CASCADE
        )""",
        """CREATE TABLE IF NOT EXISTS guide_requests (
            scope TEXT NOT NULL, request_id TEXT NOT NULL, action TEXT NOT NULL,
            fingerprint TEXT NOT NULL, result TEXT NOT NULL,
            PRIMARY KEY(scope,request_id)
        )""",
        """CREATE TABLE IF NOT EXISTS guide_tombstones (
            scope TEXT NOT NULL, guide_id TEXT NOT NULL, deleted_revision INTEGER NOT NULL,
            deleted_at REAL NOT NULL, PRIMARY KEY(scope,guide_id)
        )""",
        """CREATE TABLE IF NOT EXISTS guide_tombstone_hashes (
            scope TEXT NOT NULL, kind TEXT NOT NULL, value TEXT NOT NULL, guide_id TEXT NOT NULL,
            PRIMARY KEY(scope,kind,value,guide_id),
            FOREIGN KEY(scope,guide_id) REFERENCES guide_tombstones(scope,guide_id)
                ON DELETE CASCADE
        )""",
        """CREATE TABLE IF NOT EXISTS guide_url_hashes (
            scope TEXT NOT NULL, guide_id TEXT NOT NULL, value TEXT NOT NULL,
            PRIMARY KEY(scope,guide_id,value),
            FOREIGN KEY(scope,guide_id) REFERENCES guide_documents(scope,guide_id)
                ON DELETE CASCADE
        )""",
        """CREATE TABLE IF NOT EXISTS guide_backup_registry (
            backup_id TEXT PRIMARY KEY, scope TEXT NOT NULL, created_at REAL NOT NULL
        )""",
        "CREATE INDEX IF NOT EXISTS guide_backup_scope ON guide_backup_registry(scope)",
    ):
        db.execute(statement)
    # Immutable revision metadata holds the first observation. Persist hashes of
    # every observed redirect separately, including same-body refreshes, so a
    # later deletion can reject those aliases without retaining URL plaintext.
    for row in db.execute("SELECT scope,guide_id,metadata FROM guide_revisions").fetchall():
        metadata = json.loads(row[2])
        hashes = {_url_hash(metadata[key]) for key in ("original_url", "url", "final_url")}
        db.executemany(
            "INSERT OR IGNORE INTO guide_url_hashes VALUES (?,?,?)",
            [(row[0], row[1], value) for value in hashes],
        )
    # The live ledger, unlike a corrupt snapshot header, is scoped authority for
    # ownership of previously-created files. Registry entries are never exported
    # or restored from snapshots; local filenames do not confer deletion rights.
    for scope, encoded in db.execute(
        "SELECT scope,result FROM guide_requests WHERE action='backup'"
    ).fetchall():
        receipt = json.loads(encoded)
        backup_id, created_at = receipt.get("backup_id"), receipt.get("created_at")
        if (
            not isinstance(backup_id, str)
            or not re.fullmatch(r"guides-[0-9a-f]{32}\.sqlite", backup_id)
            or type(created_at) not in (int, float)
            or not math.isfinite(created_at)
            or created_at < 0
        ):
            raise GuideInputError("invalid_guide_backup_receipt")
        previous = db.execute(
            "SELECT scope FROM guide_backup_registry WHERE backup_id=?", (backup_id,)
        ).fetchone()
        if previous is not None and previous[0] != scope:
            raise GuideConflictError("guide_backup_ownership_conflict")
        db.execute(
            "INSERT OR IGNORE INTO guide_backup_registry VALUES (?,?,?)",
            (backup_id, scope, created_at),
        )


_MIGRATIONS = [(2, _guides_v2), (3, _guides_v3)]
# Quota is logical UTF-8 payload + 8 bytes per numeric value, including every
# duplicate key and the derived text index. SQLite page/journal overhead is not
# represented as application content; deleted pages are reused by SQLite.
_COLUMNS = {
    "guide_documents": (
        "scope guide_id canonical_url original_url game platform mode current_revision_id",
        "created_at last_accessed_at protected",
    ),
    "guide_revisions": (
        "scope revision_id guide_id content_hash body metadata",
        "created_at",
    ),
    "guide_revision_checks": ("scope revision_id last_checked_at coverage", ""),
    "guide_chunks": ("scope chunk_id revision_id body headings", "ordinal start end"),
    "guide_url_hashes": ("scope guide_id value", ""),
}


def _json(value) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def _coverage(metadata: dict) -> dict:
    return {
        key: metadata[key]
        for key in (
            "completeness",
            "completeness_reasons",
            "extracted_characters",
            "retained_characters",
        )
    }


def _merge_coverage(previous: dict, observed: dict) -> dict:
    """Identical retained text does not prove identical unread images or tail.

    Coverage is conservative across checks of one retained-text revision. A later
    full-text observation cannot establish that previously unread material was
    captured. Keep partial evidence until a distinct body creates a new revision.
    """
    return {
        "completeness": "partial"
        if "partial" in {previous["completeness"], observed["completeness"]}
        else "full",
        "completeness_reasons": list(
            dict.fromkeys(previous["completeness_reasons"] + observed["completeness_reasons"])
        ),
        "extracted_characters": max(
            previous["extracted_characters"], observed["extracted_characters"]
        ),
        "retained_characters": observed["retained_characters"],
    }


def _text(value, maximum: int, field: str, *, empty: bool = True) -> str:
    if (
        not isinstance(value, str)
        or len(value) > maximum
        or any(ord(char) < 32 and char not in "\n\t" for char in value)
        or any(0xD800 <= ord(char) <= 0xDFFF for char in value)
        or (not empty and not value.strip())
    ):
        raise GuideInputError(f"invalid_{field}")
    return value


def _date(value, field: str, *, required: bool = False) -> str | None:
    if value is None:
        if required:
            raise GuideInputError(f"invalid_{field}")
        return None
    clean = _text(value, 80, field, empty=False)
    try:
        parsed = datetime.fromisoformat(clean.replace("Z", "+00:00"))
        if required and parsed.tzinfo is None:
            raise ValueError
    except ValueError:
        raise GuideInputError(f"invalid_{field}") from None
    return clean


def _url(value) -> str:
    try:
        url = parse_url(value)
    except NetworkPolicyError:
        raise GuideInputError("invalid_guide_url") from None
    host = url.host.casefold()
    if "." not in host and ":" not in host or host.endswith((".localhost", ".local", ".internal")):
        raise GuideInputError("invalid_guide_url")
    try:
        ipaddress.ip_address(host)
    except ValueError:
        pass  # DNS was validated and pinned by the public reader; no network in storage.
    else:
        if not public_ip(host):
            raise GuideInputError("invalid_guide_url")
    return str(url)


def _url_hash(value) -> str:
    return hashlib.sha256(_url(value).encode("utf-8")).hexdigest()


def _object_id(value, prefix: str) -> str:
    if not isinstance(value, str) or not re.fullmatch(prefix + r"-[0-9a-f]{32}", value):
        raise GuideInputError(f"invalid_{prefix}_id")
    return value


def _headings(value, text: str) -> list[dict]:
    if not isinstance(value, list) or len(value) > MAX_BODY_CHARACTERS:
        raise GuideInputError("invalid_headings")
    result = []
    for heading in value:
        if not isinstance(heading, dict):
            raise GuideInputError("invalid_headings")
        level, start, end = (heading.get(key) for key in ("level", "start", "end"))
        if (
            type(level) is not int
            or not 1 <= level <= 6
            or type(start) is not int
            or type(end) is not int
            or not 0 <= start < end
        ):
            raise GuideInputError("invalid_headings")
        title = _text(heading.get("title"), MAX_BODY_CHARACTERS, "heading", empty=False)
        if start >= len(text):
            continue  # Defensive body clipping can drop later headings.
        end = min(end, len(text))
        if text[start:end] != title[: end - start]:
            raise GuideInputError("invalid_heading_offsets")
        result.append({"level": level, "title": text[start:end], "start": start, "end": end})
    return sorted(result, key=lambda item: (item["start"], item["level"]))


def _split_chunks(revision_id: str, text: str, headings: list[dict]) -> list[dict]:
    result, active = [], []
    start, heading_index = 0, 0
    while start < len(text):
        end = min(start + 1000, len(text))
        if end < len(text):
            newline = text.rfind("\n", start + 500, end)
            if newline >= 0:
                end = newline + 1
        while heading_index < len(headings) and headings[heading_index]["start"] <= start:
            heading = headings[heading_index]
            active = [item for item in active if item["level"] < heading["level"]]
            active.append(heading)
            heading_index += 1
        # A heading within this chunk is already in its body; its hierarchy becomes
        # active in subsequent chunks. Offsets always address the immutable body.
        ordinal = len(result)
        token = f"{revision_id}:{ordinal}:{start}:{end}"
        result.append(
            {
                "chunk_id": "chunk-" + hashlib.sha256(token.encode()).hexdigest()[:32],
                "revision_id": revision_id,
                "ordinal": ordinal,
                "start": start,
                "end": end,
                "text": text[start:end],
                "headings": [dict(item) for item in active],
            }
        )
        start = end
    return result


class GuideStore:
    """Memory Service owns this resource and waits for its workers before closing."""

    def __init__(self, paths: DataPaths, scope: str):
        self.paths = paths
        self.scope = _text(scope, 1000, "scope", empty=False)
        self._guard = RLock()
        self._closed = False
        directory = safe_child(paths.root, "guides")
        if directory != paths.guides or not directory.is_dir():
            raise GuideInputError("invalid_guides_directory")
        for suffix in ("", "-wal", "-shm", "-journal"):
            path = safe_child(directory, "guides.sqlite" + suffix)
            if path.exists() and not path.is_file():
                raise GuideInputError("invalid_guides_file")
        backups = safe_child(paths.root, "backups")
        if backups != paths.backups or not backups.is_dir():
            raise GuideInputError("invalid_guides_backup_directory")
        migration_backup = safe_child(backups, "guides-pre-migration-v2.sqlite")
        if migration_backup.exists() and not migration_backup.is_file():
            raise GuideInputError("invalid_guides_backup_file")
        self._db = sqlite3.connect(
            directory / "guides.sqlite", timeout=5, check_same_thread=False, isolation_level=None
        )
        self._db.row_factory = sqlite3.Row
        try:
            ensure_supported_schema(self._db, _MIGRATIONS[-1][0])
            self._db.execute("PRAGMA foreign_keys=ON")
            self._db.execute("PRAGMA secure_delete=ON")
            self._db.execute("PRAGMA journal_mode=DELETE")
            apply_migrations(self._db, steps=_MIGRATIONS, backup_path=migration_backup)
            migration_backup.unlink(missing_ok=True)
            self._published_revision = self.revision()
        except BaseException:
            self._db.close()
            raise

    def _open(self) -> None:
        if self._closed:
            raise GuideConflictError("guides_closed")

    @contextmanager
    def _transaction(self):
        with self._guard:
            self._open()
            self._db.execute("BEGIN IMMEDIATE")
            try:
                yield
                self._db.commit()
                self._published_revision = self.revision()
            except BaseException:
                self._db.rollback()
                raise

    @property
    def published_revision(self) -> int:
        """Last locally observed committed revision, safe during an in-flight writer.

        The Runtime owns one store; independent processes must use revision() to
        refresh their view. Never publish a revision from an uncommitted write.
        """
        return self._published_revision

    def revision(self) -> int:
        with self._guard:
            self._open()
            row = self._db.execute(
                "SELECT revision FROM guide_control WHERE scope=?", (self.scope,)
            ).fetchone()
            return row[0] if row else 0

    def _advance_revision(self) -> int:
        if self.revision() >= 2**63 - 2:
            raise GuideConflictError("guide_revision_exhausted")
        self._db.execute(
            "INSERT INTO guide_control(scope,revision) VALUES (?,1)"
            " ON CONFLICT(scope) DO UPDATE SET revision=revision+1",
            (self.scope,),
        )
        return self.revision()

    def control_snapshot(self) -> dict:
        with self._transaction():
            return {
                "revision": self.revision(),
                "selections": [
                    dict(row)
                    for row in self._db.execute(
                        "SELECT game,platform,mode,guide_id,revision_id,selection_revision,selected_at"
                        " FROM guide_selections WHERE scope=? ORDER BY game,platform,mode",
                        (self.scope,),
                    ).fetchall()
                ],
            }

    def _request_fingerprint(
        self, action: str, payload: dict, request_id: str, expected_revision: int | None
    ) -> str:
        if not isinstance(request_id, str) or not re.fullmatch(r"[0-9a-f]{32}", request_id):
            raise GuideInputError("invalid_guide_request_id")
        if not (action == "backup" and expected_revision is None) and (
            type(expected_revision) is not int or not 0 <= expected_revision < 2**63 - 1
        ):
            raise GuideInputError("invalid_guide_expected_revision")
        if not isinstance(action, str) or not re.fullmatch(r"[a-z_]{1,32}", action):
            raise GuideInputError("invalid_guide_action")
        if not isinstance(payload, dict):
            raise GuideInputError("invalid_guide_payload")
        try:
            encoded = _json([action, payload, expected_revision]).encode("utf-8")
        except (TypeError, ValueError, UnicodeError):
            raise GuideInputError("invalid_guide_payload") from None
        return hashlib.sha256(encoded).hexdigest()

    def preflight(
        self, action: str, payload: dict, request_id: str, expected_revision: int | None
    ) -> dict | None:
        """Read-only request check. Mutations MUST repeat it in their transaction."""
        fingerprint = self._request_fingerprint(action, payload, request_id, expected_revision)
        with self._guard:
            self._open()
            row = self._db.execute(
                "SELECT * FROM guide_requests WHERE scope=? AND request_id=?",
                (self.scope, request_id),
            ).fetchone()
            current = self.revision()
            if row is not None:
                if row["action"] != action or row["fingerprint"] != fingerprint:
                    raise GuideConflictError("guide_request_conflict")
                result = json.loads(row["result"])
                result.update(
                    replayed=True,
                    current_revision=current,
                    superseded=current != result["revision"],
                )
                if action == "selection":
                    for key in ("selection", "previous_selection"):
                        if result[key] is not None:
                            result[key].update(
                                {name: payload[name] for name in ("game", "platform", "mode")}
                            )
                return result
            if expected_revision is not None and expected_revision != current:
                raise GuideConflictError("guide_revision_conflict")
            if action == "delete":
                if set(payload) != {"guide_id"}:
                    raise GuideInputError("invalid_guide_payload")
                self._descriptor(_object_id(payload["guide_id"], "guide"))
            elif action == "selection":
                self._validate_selection(payload)
            return None

    def _record_request(
        self,
        action: str,
        payload: dict,
        request_id: str,
        result: dict,
        expected_revision: int | None = None,
    ) -> None:
        # Enforce the content-free ledger boundary, rather than relying on every
        # caller to strip source descriptors or user-provided labels correctly.
        fingerprint = self._request_fingerprint(action, payload, request_id, expected_revision)
        self._validate_receipt(result)
        self._db.execute(
            "INSERT INTO guide_requests VALUES (?,?,?,?,?)",
            (self.scope, request_id, action, fingerprint, _json(result)),
        )

    @staticmethod
    def _validate_receipt(result: dict) -> None:
        identifiers = {"guide_id": "guide", "revision_id": "revision"}
        lists = {"guide_ids": "guide", "revision_ids": "revision", "evicted_guide_ids": "guide"}
        flags = {"saved", "deduplicated", "replayed", "superseded", "deleted", "not_modified"}
        numbers = {
            "revision",
            "current_revision",
            "selection_revision",
            "selected_at",
            "created_at",
            "format_version",
            "bytes",
            "documents",
            "revisions",
            "selections",
        }
        for key, value in result.items():
            if key in identifiers:
                _object_id(value, identifiers[key])
            elif key in lists:
                if not isinstance(value, list):
                    raise GuideInputError("invalid_guide_request_result")
                for item in value:
                    _object_id(item, lists[key])
            elif key in flags:
                if type(value) is not bool:
                    raise GuideInputError("invalid_guide_request_result")
            elif key in numbers:
                if type(value) not in (int, float) or not math.isfinite(value) or value < 0:
                    raise GuideInputError("invalid_guide_request_result")
            elif key == "backup_id":
                if not isinstance(value, str) or not re.fullmatch(
                    r"guides-[0-9a-f]{32}\.sqlite", value
                ):
                    raise GuideInputError("invalid_guide_request_result")
            elif key in {"selection", "previous_selection"}:
                if value is not None:
                    if not isinstance(value, dict):
                        raise GuideInputError("invalid_guide_request_result")
                    GuideStore._validate_receipt(value)
            elif key == "selection_changes":
                if not isinstance(value, list):
                    raise GuideInputError("invalid_guide_request_result")
                for item in value:
                    if not isinstance(item, dict):
                        raise GuideInputError("invalid_guide_request_result")
                    GuideStore._validate_receipt(item)
            else:
                raise GuideInputError("invalid_guide_request_result")

    def _is_tombstoned(
        self, guide_id: str | None = None, *, url_hashes=(), content_hash: str | None = None
    ) -> bool:
        if (
            guide_id is not None
            and self._db.execute(
                "SELECT 1 FROM guide_tombstones WHERE scope=? AND guide_id=?",
                (self.scope, guide_id),
            ).fetchone()
        ):
            return True
        for kind, value in [
            *(("url", item) for item in url_hashes),
            *([("content", content_hash)] if content_hash else []),
        ]:
            if self._db.execute(
                "SELECT 1 FROM guide_tombstone_hashes WHERE scope=? AND kind=? AND value=?",
                (self.scope, kind, value),
            ).fetchone():
                return True
        return False

    def export_tombstones(self) -> list[dict]:
        with self._guard:
            self._open()
            result = []
            for row in self._db.execute(
                "SELECT guide_id,deleted_revision,deleted_at FROM guide_tombstones"
                " WHERE scope=? ORDER BY guide_id",
                (self.scope,),
            ).fetchall():
                hashes = self._db.execute(
                    "SELECT kind,value FROM guide_tombstone_hashes"
                    " WHERE scope=? AND guide_id=? ORDER BY kind,value",
                    (self.scope, row[0]),
                ).fetchall()
                result.append(
                    dict(row)
                    | {
                        "url_hashes": [item[1] for item in hashes if item[0] == "url"],
                        "content_hashes": [item[1] for item in hashes if item[0] == "content"],
                    }
                )
            return result

    @staticmethod
    def _selection_ids(row) -> dict | None:
        if row is None:
            return None
        return {
            key: row[key]
            for key in ("guide_id", "revision_id", "selection_revision", "selected_at")
        }

    def _validate_selection(self, payload: dict) -> None:
        if set(payload) != {"game", "platform", "mode", "guide_id", "revision_id"}:
            raise GuideInputError("invalid_guide_payload")
        for name in ("game", "platform", "mode"):
            value = _text(payload[name], 200, name, empty=False)
            if value != value.strip():
                raise GuideInputError(f"invalid_{name}")
        guide_id, revision_id = payload["guide_id"], payload["revision_id"]
        if guide_id is None and revision_id is None:
            return
        _object_id(guide_id, "guide")
        _object_id(revision_id, "revision")
        document = self._descriptor(guide_id, revision_id)
        for name in ("game", "platform", "mode"):
            if document[name] and document[name] != payload[name]:
                raise GuideConflictError("guide_dimensions_conflict")

    def set_selection(
        self,
        game: str,
        platform: str,
        mode: str,
        guide_id: str | None,
        revision_id: str | None,
        *,
        expected_revision: int,
        request_id: str,
    ) -> dict:
        payload = {
            "game": game,
            "platform": platform,
            "mode": mode,
            "guide_id": guide_id,
            "revision_id": revision_id,
        }
        with self._transaction():
            replay = self.preflight("selection", payload, request_id, expected_revision)
            if replay is not None:
                result = replay
            else:
                previous = self._db.execute(
                    "SELECT * FROM guide_selections"
                    " WHERE scope=? AND game=? AND platform=? AND mode=?",
                    (self.scope, game, platform, mode),
                ).fetchone()
                control_revision = self._advance_revision()
                selection = None
                if guide_id is None:
                    self._db.execute(
                        "DELETE FROM guide_selections"
                        " WHERE scope=? AND game=? AND platform=? AND mode=?",
                        (self.scope, game, platform, mode),
                    )
                else:
                    self._db.execute(
                        "UPDATE guide_documents SET game=?,platform=?,mode=?"
                        " WHERE scope=? AND guide_id=?",
                        (game, platform, mode, self.scope, guide_id),
                    )
                    selection = {
                        "guide_id": guide_id,
                        "revision_id": revision_id,
                        "selection_revision": control_revision,
                        "selected_at": time.time(),
                    }
                    self._db.execute(
                        "INSERT INTO guide_selections VALUES (?,?,?,?,?,?,?,?)"
                        " ON CONFLICT(scope,game,platform,mode) DO UPDATE SET"
                        " guide_id=excluded.guide_id,revision_id=excluded.revision_id,"
                        " selection_revision=excluded.selection_revision,"
                        " selected_at=excluded.selected_at",
                        (
                            self.scope,
                            game,
                            platform,
                            mode,
                            guide_id,
                            revision_id,
                            control_revision,
                            selection["selected_at"],
                        ),
                    )
                result = {
                    "revision": control_revision,
                    "selection": selection,
                    "previous_selection": self._selection_ids(previous),
                    "replayed": False,
                    "superseded": False,
                }
                self._record_request("selection", payload, request_id, result, expected_revision)
            # Dimensions are replayable from the hash-verified request, without
            # keeping these user-provided labels in the permanent request ledger.
            return result | {
                key: result[key] | {"game": game, "platform": platform, "mode": mode}
                if result[key] is not None
                else None
                for key in ("selection", "previous_selection")
            }

    def delete_document(self, guide_id: str, *, expected_revision: int, request_id: str) -> dict:
        payload = {"guide_id": guide_id}
        with self._transaction():
            replay = self.preflight("delete", payload, request_id, expected_revision)
            if replay is not None:
                return replay
            document = self._descriptor(guide_id)
            rows = self._db.execute(
                "SELECT revision_id,content_hash,metadata FROM guide_revisions"
                " WHERE scope=? AND guide_id=? ORDER BY revision_id",
                (self.scope, guide_id),
            ).fetchall()
            selections = self._db.execute(
                "SELECT * FROM guide_selections WHERE scope=? AND guide_id=?",
                (self.scope, guide_id),
            ).fetchall()
            control_revision = self._advance_revision()
            self._db.execute(
                "INSERT INTO guide_tombstones VALUES (?,?,?,?)",
                (self.scope, guide_id, control_revision, time.time()),
            )
            hashes = {("url", _url_hash(document["original_url"]))}
            hashes.update(
                ("url", row[0])
                for row in self._db.execute(
                    "SELECT value FROM guide_url_hashes WHERE scope=? AND guide_id=?",
                    (self.scope, guide_id),
                ).fetchall()
            )
            for row in rows:
                hashes.add(("content", row["content_hash"]))
                metadata = json.loads(row["metadata"])
                for key in ("original_url", "url", "final_url"):
                    hashes.add(("url", _url_hash(metadata[key])))
            self._db.executemany(
                "INSERT INTO guide_tombstone_hashes VALUES (?,?,?,?)",
                [(self.scope, kind, value, guide_id) for kind, value in sorted(hashes)],
            )
            self._db.execute(
                "DELETE FROM guide_documents WHERE scope=? AND guide_id=?", (self.scope, guide_id)
            )
            result = {
                "revision": control_revision,
                "guide_ids": [guide_id],
                "revision_ids": [row["revision_id"] for row in rows],
                "selection_changes": [
                    {"previous_selection": self._selection_ids(row), "selection": None}
                    for row in selections
                ],
                "replayed": False,
                "superseded": False,
            }
            self._record_request("delete", payload, request_id, result, expected_revision)
            return result

    def _bytes(self, guide_id: str | None = None) -> int:
        total = 0
        for table, (strings, numbers) in _COLUMNS.items():
            expression = "+".join(
                [f"length(CAST({name} AS BLOB))" for name in strings.split()]
                + [str(8 * len(numbers.split()))]
            )
            parameters: tuple = ()
            where = ""
            if guide_id is not None:
                parameters = (self.scope, guide_id)
                if table in {"guide_documents", "guide_revisions", "guide_url_hashes"}:
                    where = " WHERE scope=? AND guide_id=?"
                else:
                    where = (
                        " WHERE scope=? AND revision_id IN (SELECT revision_id FROM guide_revisions"
                        " WHERE scope=? AND guide_id=?)"
                    )
                    parameters = (self.scope, self.scope, guide_id)
            total += self._db.execute(
                f"SELECT COALESCE(SUM({expression}),0) FROM {table}{where}", parameters
            ).fetchone()[0]
        return total

    def logical_bytes(self) -> int:
        """Total logical usage across scopes; no foreign-scope content is exposed."""
        with self._guard:
            self._open()
            return self._bytes()

    def _enforce_quota(self, guide_id: str) -> list[str]:
        total = self._bytes()
        if total <= MAX_STORAGE_BYTES:
            return []
        if self._bytes(guide_id) > MAX_STORAGE_BYTES:
            raise GuideCapacityError("guide_too_large")
        candidates = self._db.execute(
            "SELECT guide_id FROM guide_documents WHERE scope=? AND guide_id<>? AND protected=0"
            " AND NOT EXISTS (SELECT 1 FROM guide_selections s"
            " WHERE s.scope=guide_documents.scope AND s.guide_id=guide_documents.guide_id)"
            " ORDER BY last_accessed_at,created_at,guide_id",
            (self.scope, guide_id),
        ).fetchall()
        evicted = []
        for row in candidates:
            total -= self._bytes(row["guide_id"])
            evicted.append(row["guide_id"])
            if total <= MAX_STORAGE_BYTES:
                break
        if total > MAX_STORAGE_BYTES:
            raise GuideCapacityError("guide_capacity_exhausted")
        # Only perform deletes once sufficient capacity has been proven. The
        # enclosing write transaction also rolls back both eviction and ingestion.
        self._db.executemany(
            "DELETE FROM guide_documents WHERE scope=? AND guide_id=?",
            [(self.scope, item) for item in evicted],
        )
        return evicted

    def _chunks_write(self, revision_id: str, body: str, headings: list[dict]) -> list[dict]:
        chunks = _split_chunks(revision_id, body, headings)
        self._db.execute(
            "DELETE FROM guide_chunks WHERE scope=? AND revision_id=?", (self.scope, revision_id)
        )
        self._db.executemany(
            "INSERT INTO guide_chunks VALUES (?,?,?,?,?,?,?,?)",
            [
                (
                    self.scope,
                    chunk["chunk_id"],
                    revision_id,
                    chunk["ordinal"],
                    chunk["start"],
                    chunk["end"],
                    chunk["text"],
                    _json(chunk["headings"]),
                )
                for chunk in chunks
            ],
        )
        return chunks

    def ingest(
        self,
        source: dict,
        *,
        game: str = "",
        platform: str = "",
        mode: str = "",
        game_version: str | None = None,
        version_basis: str | None = None,
        expected_revision: int | None = None,
        request_id: str | None = None,
        operation: str = "ingest",
        operation_payload: dict | None = None,
    ) -> dict:
        if not isinstance(source, dict) or source.get("status") != "read":
            raise GuideInputError("guide_body_unreadable")
        completeness = source.get("completeness")
        if completeness not in {"full", "partial"}:
            raise GuideInputError("guide_completeness_required")
        raw = source.get("text")
        if not isinstance(raw, str) or not raw.strip():
            raise GuideInputError("guide_body_empty")
        body = _text(raw[:MAX_BODY_CHARACTERS], MAX_BODY_CHARACTERS, "guide_body", empty=False)
        original_url = source.get("original_url", source.get("url"))
        canonical_url = _url(original_url)
        final_url = _url(source.get("final_url", source.get("url")))
        reasons = source.get("completeness_reasons", [])
        if not isinstance(reasons, list) or len(reasons) > 32:
            raise GuideInputError("invalid_completeness_reasons")
        reasons = list(dict.fromkeys(_text(item, 100, "completeness_reason") for item in reasons))
        if len(raw) > MAX_BODY_CHARACTERS:
            reasons = list(dict.fromkeys([*reasons, "body_limit"]))
        if reasons:
            completeness = "partial"
        game, platform, mode = (
            _text(value, 200, name)
            for value, name in ((game, "game"), (platform, "platform"), (mode, "mode"))
        )
        metadata = {
            "original_url": original_url,
            "url": final_url,
            "final_url": final_url,
            "title": _text(source.get("title", ""), 1000, "title"),
            "status": "read",
            "completeness": completeness,
            "completeness_reasons": reasons,
            "retrieved_at": _date(source.get("retrieved_at"), "retrieved_at", required=True),
            "content_date": _date(source.get("content_date"), "content_date"),
            "game_version": _text(game_version, 200, "game_version")
            if game_version is not None
            else None,
            "version_basis": _text(version_basis, 2000, "version_basis")
            if version_basis is not None
            else None,
            "etag": _text(source["etag"], 1024, "etag") if source.get("etag") is not None else None,
            "last_modified": _text(source["last_modified"], 1024, "last_modified")
            if source.get("last_modified") is not None
            else None,
            "headings": _headings(source.get("headings", []), body),
            "retained_characters": len(body),
            "extracted_characters": max(len(raw), source.get("extracted_characters", len(raw)))
            if type(source.get("extracted_characters", len(raw))) is int
            else len(raw),
        }
        content_hash = hashlib.sha256(body.encode("utf-8")).hexdigest()
        payload = (
            operation_payload
            if operation_payload is not None
            else {
                "content_hash": content_hash,
                "metadata": metadata,
                "game": game,
                "platform": platform,
                "mode": mode,
            }
        )
        now = time.time()
        with self._transaction():
            if request_id is not None:
                replay = self.preflight(operation, payload, request_id, expected_revision)
                if replay is not None:
                    try:
                        descriptor = self._descriptor(replay["guide_id"], replay["revision_id"])
                    except GuideAccessError:
                        return replay | {"saved": False, "deleted": True, "superseded": True}
                    return descriptor | replay
            elif expected_revision is not None:
                if type(expected_revision) is not int or not 0 <= expected_revision < 2**63 - 1:
                    raise GuideInputError("invalid_guide_expected_revision")
                if expected_revision != self.revision():
                    raise GuideConflictError("guide_revision_conflict")
            if self._is_tombstoned(
                url_hashes=(_url_hash(canonical_url), _url_hash(final_url)),
                content_hash=content_hash,
            ):
                raise GuideConflictError("guide_deleted")
            document = self._db.execute(
                "SELECT * FROM guide_documents WHERE scope=? AND canonical_url=?",
                (self.scope, canonical_url),
            ).fetchone()
            guide_id = document["guide_id"] if document else "guide-" + uuid4().hex
            revision = self._db.execute(
                "SELECT * FROM guide_revisions WHERE scope=? AND guide_id=? AND content_hash=?",
                (self.scope, guide_id, content_hash),
            ).fetchone()
            revision_id = revision["revision_id"] if revision else "revision-" + uuid4().hex
            if document is None:
                self._db.execute(
                    "INSERT INTO guide_documents VALUES (?,?,?,?,?,?,?,?,?,?,0)",
                    (
                        self.scope,
                        guide_id,
                        canonical_url,
                        original_url,
                        game,
                        platform,
                        mode,
                        now,
                        now,
                        revision_id,
                    ),
                )
            else:
                self._db.execute(
                    "UPDATE guide_documents SET current_revision_id=?, last_accessed_at=?"
                    " WHERE scope=? AND guide_id=?",
                    (revision_id, now, self.scope, guide_id),
                )
            self._db.executemany(
                "INSERT OR IGNORE INTO guide_url_hashes VALUES (?,?,?)",
                [
                    (self.scope, guide_id, value)
                    for value in {_url_hash(canonical_url), _url_hash(final_url)}
                ],
            )
            if revision is None:
                self._db.execute(
                    "INSERT INTO guide_revisions VALUES (?,?,?,?,?,?,?)",
                    (self.scope, revision_id, guide_id, content_hash, body, _json(metadata), now),
                )
                self._chunks_write(revision_id, body, metadata["headings"])
            previous_check = self._db.execute(
                "SELECT last_checked_at,coverage FROM guide_revision_checks"
                " WHERE scope=? AND revision_id=?",
                (self.scope, revision_id),
            ).fetchone()
            checked_at = metadata["retrieved_at"]
            coverage = _coverage(metadata)
            if previous_check is not None:
                coverage = _merge_coverage(json.loads(previous_check["coverage"]), coverage)
                checked_at = max(
                    checked_at,
                    previous_check["last_checked_at"],
                    key=lambda value: datetime.fromisoformat(value.replace("Z", "+00:00")),
                )
            self._db.execute(
                "INSERT INTO guide_revision_checks VALUES (?,?,?,?) ON CONFLICT(scope,revision_id)"
                " DO UPDATE SET last_checked_at=excluded.last_checked_at,coverage=excluded.coverage",
                (self.scope, revision_id, checked_at, _json(coverage)),
            )
            evicted = self._enforce_quota(guide_id)
            result = self._descriptor(guide_id, revision_id)
            receipt = {
                "guide_id": guide_id,
                "revision_id": revision_id,
                "revision": self.revision(),
                "saved": True,
                "deduplicated": revision is not None,
                "evicted_guide_ids": evicted,
                "replayed": False,
                "superseded": False,
            }
            if request_id is not None:
                self._record_request(operation, payload, request_id, receipt, expected_revision)
            result.update(receipt)
            return result

    def mark_checked(
        self,
        guide_id: str,
        revision_id: str,
        *,
        checked_at: str,
        expected_revision: int | None = None,
        request_id: str | None = None,
        operation_payload: dict | None = None,
    ) -> dict:
        """Record an authenticated conditional 304 without rewriting content evidence."""
        checked_at = _date(checked_at, "checked_at", required=True)
        payload = operation_payload if operation_payload is not None else {"guide_id": guide_id}
        with self._transaction():
            if request_id is not None:
                replay = self.preflight("refresh", payload, request_id, expected_revision)
                if replay is not None:
                    return replay
            if expected_revision is not None and expected_revision != self.revision():
                raise GuideConflictError("guide_revision_conflict")
            document = self._descriptor(guide_id, revision_id)
            self._db.execute(
                "UPDATE guide_revision_checks SET last_checked_at=? WHERE scope=? AND revision_id=?",
                (checked_at, self.scope, revision_id),
            )
            receipt = {
                "guide_id": guide_id,
                "revision_id": revision_id,
                "revision": self.revision(),
                "saved": True,
                "deduplicated": True,
                "not_modified": True,
                "replayed": False,
                "superseded": False,
            }
            if request_id is not None:
                self._record_request("refresh", payload, request_id, receipt, expected_revision)
            return {**document, **receipt, "last_checked_at": checked_at}

    def _descriptor(self, guide_id: str, revision_id: str | None = None) -> dict:
        document = self._db.execute(
            "SELECT * FROM guide_documents WHERE scope=? AND guide_id=?", (self.scope, guide_id)
        ).fetchone()
        if document is None:
            raise GuideAccessError("guide_not_found")
        revision_id = revision_id or document["current_revision_id"]
        revision = self._revision(revision_id, guide_id=guide_id)
        metadata = json.loads(revision["metadata"])
        metadata.pop("headings", None)
        checked = self._db.execute(
            "SELECT last_checked_at,coverage FROM guide_revision_checks"
            " WHERE scope=? AND revision_id=?",
            (self.scope, revision_id),
        ).fetchone()
        return {
            **metadata,
            **(json.loads(checked["coverage"]) if checked else _coverage(metadata)),
            "revision_coverage": _coverage(metadata),
            "guide_id": guide_id,
            "revision_id": revision_id,
            "current_revision_id": document["current_revision_id"],
            "content_hash": revision["content_hash"],
            "game": document["game"],
            "platform": document["platform"],
            "mode": document["mode"],
            "created_at": document["created_at"],
            "revision_created_at": revision["created_at"],
            "last_checked_at": checked[0] if checked else metadata["retrieved_at"],
            "protected": bool(document["protected"])
            or bool(
                self._db.execute(
                    "SELECT 1 FROM guide_selections WHERE scope=? AND guide_id=? LIMIT 1",
                    (self.scope, guide_id),
                ).fetchone()
            ),
        }

    def _revision(self, revision_id: str, *, guide_id: str | None = None):
        row = self._db.execute(
            "SELECT * FROM guide_revisions WHERE scope=? AND revision_id=?",
            (self.scope, revision_id),
        ).fetchone()
        if row is None or (guide_id is not None and row["guide_id"] != guide_id):
            raise GuideAccessError("guide_revision_not_found")
        return row

    def list_documents(self) -> list[dict]:
        with self._guard:
            self._open()
            rows = self._db.execute(
                "SELECT guide_id FROM guide_documents WHERE scope=? ORDER BY created_at,guide_id",
                (self.scope,),
            ).fetchall()
            return [self._descriptor(row[0]) for row in rows]

    def get_document(self, guide_id: str, revision_id: str | None = None) -> dict:
        with self._transaction():
            result = self._descriptor(guide_id, revision_id)
            revision = self._revision(result["revision_id"])
            result["text"] = revision["body"]
            result["headings"] = json.loads(revision["metadata"])["headings"]
            rows = self._db.execute(
                "SELECT revision_id FROM guide_revisions WHERE scope=? AND guide_id=?"
                " ORDER BY created_at,revision_id",
                (self.scope, guide_id),
            ).fetchall()
            result["revisions"] = [self._descriptor(guide_id, row[0]) for row in rows]
            self._db.execute(
                "UPDATE guide_documents SET last_accessed_at=? WHERE scope=? AND guide_id=?",
                (time.time(), self.scope, guide_id),
            )
            return result

    def chunks(self, revision_id: str) -> list[dict]:
        with self._transaction():
            revision = self._revision(revision_id)
            rows = self._db.execute(
                "SELECT * FROM guide_chunks WHERE scope=? AND revision_id=? ORDER BY ordinal",
                (self.scope, revision_id),
            ).fetchall()
            self._db.execute(
                "UPDATE guide_documents SET last_accessed_at=? WHERE scope=? AND guide_id=?",
                (time.time(), self.scope, revision["guide_id"]),
            )
            return [
                {
                    "chunk_id": row["chunk_id"],
                    "revision_id": revision_id,
                    "ordinal": row["ordinal"],
                    "start": row["start"],
                    "end": row["end"],
                    "text": row["body"],
                    "headings": json.loads(row["headings"]),
                }
                for row in rows
            ]

    def rebuild_index(self, revision_id: str) -> list[dict]:
        with self._transaction():
            revision = self._revision(revision_id)
            chunks = self._chunks_write(
                revision_id, revision["body"], json.loads(revision["metadata"])["headings"]
            )
            self._enforce_quota(revision["guide_id"])
            return chunks

    def set_protected(self, guide_id: str, protected: bool = True) -> None:
        """Internal ownership seam: G2 must protect adopted documents before success."""
        if type(protected) is not bool:
            raise GuideInputError("invalid_guide_protection")
        with self._transaction():
            self._descriptor(guide_id)
            self._db.execute(
                "UPDATE guide_documents SET protected=? WHERE scope=? AND guide_id=?",
                (int(protected), self.scope, guide_id),
            )

    def close(self) -> None:
        with self._guard:
            if not self._closed:
                self._closed = True
                self._db.close()

    def __enter__(self):
        return self

    def __exit__(self, *_):
        self.close()
