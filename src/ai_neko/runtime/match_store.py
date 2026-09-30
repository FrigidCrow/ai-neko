"""Scoped match evidence in the Runtime's existing conversation database.

The Runtime validates live request bindings and supplies observations; public
control calls cannot invent timestamps, frames, or evidence types. No model,
media bytes, personal-memory extraction, or assistant advice is stored here.
"""

from __future__ import annotations

import hashlib
import json
import math
import re
import sqlite3
import time
from contextlib import contextmanager
from uuid import uuid4

from ai_neko.memory.script_fold import fold_script

OBSERVATION_SECONDS = 120
_KINDS = {
    "user": "user_description",
    "user_description": "user_description",
    "explicit_user_description": "user_description",
    "user_correction": "user_correction",
    "user_confirmation": "user_correction",
    "vision": "vision",
    "screenshot": "vision",
}
_ACTION_FIELDS = {
    "start": {"game", "platform", "mode", "game_version", "goal"},
    "new": {"match_id", "game", "platform", "mode", "game_version", "goal"},
    "end": {"match_id"},
    "update": {"match_id", "game_version", "goal"},
    "observe": {"match_id", "turn_id", "text"},
    "close_observation": {"match_id"},
}


class MatchInputError(ValueError):
    """An invalid match or evidence value was supplied."""


class MatchConflictError(ValueError):
    """The request conflicts with a committed revision or request identity."""


class MatchAccessError(LookupError):
    """The opaque session, match, or turn does not belong to this scope."""


def conversation_matches_v5(db: sqlite3.Connection) -> None:
    """Idempotent migration; the caller owns the migration transaction."""
    statements = (
        "CREATE TABLE IF NOT EXISTS matches ("
        "match_id TEXT PRIMARY KEY,scope TEXT NOT NULL,session_id TEXT NOT NULL "
        "REFERENCES sessions(id),game TEXT NOT NULL,platform TEXT NOT NULL,mode TEXT NOT NULL,"
        "game_version TEXT,goal TEXT NOT NULL DEFAULT '',status TEXT NOT NULL "
        "CHECK(status IN ('active','needs_update','ended')),state_revision INTEGER NOT NULL,"
        "selection TEXT,evidence_after REAL NOT NULL,created_at REAL NOT NULL,updated_at REAL NOT NULL)",
        "CREATE INDEX IF NOT EXISTS matches_session ON matches(scope,session_id,created_at)",
        "CREATE UNIQUE INDEX IF NOT EXISTS matches_one_current ON matches(scope,session_id) "
        "WHERE status IN ('active','needs_update')",
        "CREATE TABLE IF NOT EXISTS match_control (scope TEXT NOT NULL,session_id TEXT NOT NULL "
        "REFERENCES sessions(id),revision INTEGER NOT NULL DEFAULT 0,current_match_id TEXT "
        "REFERENCES matches(match_id),PRIMARY KEY(scope,session_id))",
        "CREATE TABLE IF NOT EXISTS match_requests (scope TEXT NOT NULL,session_id TEXT NOT NULL "
        "REFERENCES sessions(id),request_id TEXT NOT NULL,action TEXT NOT NULL,"
        "fingerprint TEXT NOT NULL,match_id TEXT NOT NULL,revision INTEGER NOT NULL,"
        "created_at REAL NOT NULL,PRIMARY KEY(scope,session_id,request_id))",
        "CREATE TABLE IF NOT EXISTS match_observations (observation_id TEXT PRIMARY KEY,"
        "match_id TEXT NOT NULL REFERENCES matches(match_id) ON DELETE CASCADE,"
        "turn_id TEXT NOT NULL REFERENCES turns(id),source_kind TEXT NOT NULL "
        "CHECK(source_kind IN ('vision','user_description','user_correction')),text TEXT NOT NULL,"
        "fields TEXT NOT NULL,observed_at REAL NOT NULL,expires_at REAL NOT NULL,frame_id TEXT,"
        "active INTEGER NOT NULL DEFAULT 1 CHECK(active IN (0,1)))",
        "CREATE INDEX IF NOT EXISTS match_observations_current ON "
        "match_observations(match_id,active,observed_at)",
        "CREATE INDEX IF NOT EXISTS match_observations_turn ON match_observations(turn_id)",
    )
    for statement in statements:
        db.execute(statement)


def _text(value, name, limit=200, *, empty=False):
    if (
        not isinstance(value, str)
        or len(value) > limit
        or (not empty and not value.strip())
        or value != value.strip()
        or any(
            (ord(char) < 32 and char not in "\n\r\t")
            or ord(char) == 127
            or 0xD800 <= ord(char) <= 0xDFFF
            for char in value
        )
    ):
        raise MatchInputError("invalid_match_" + name)
    return value


def _identifier(value, *, prefix="", access=False):
    if not isinstance(value, str) or not re.fullmatch(prefix + r"[a-f0-9]{32}", value):
        error = MatchAccessError if access else MatchInputError
        raise error("match_resource_not_found" if access else "invalid_match_identifier")
    return value


def _timestamp(value):
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise MatchInputError("invalid_match_time")
    try:
        valid = math.isfinite(value) and value >= 0
    except (OverflowError, ValueError):
        valid = False
    if not valid:
        raise MatchInputError("invalid_match_time")
    return float(value)


def _now(value):
    return _timestamp(time.time() if value is None else value)


def _json(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def _fields(value):
    if value is None:
        return {}
    if not isinstance(value, dict) or len(value) > 32:
        raise MatchInputError("invalid_match_fields")

    def validate(item, depth=0):
        if depth > 4:
            raise MatchInputError("invalid_match_fields")
        if item is None or type(item) is bool:
            return
        if isinstance(item, str):
            _text(item, "field_value", 1000, empty=True)
        elif type(item) in {int, float}:
            try:
                valid = math.isfinite(item)
            except OverflowError:
                valid = False
            if not valid:
                raise MatchInputError("invalid_match_fields")
        elif isinstance(item, (dict, list)) and len(item) <= 32:
            for key, child in item.items() if isinstance(item, dict) else enumerate(item):
                if isinstance(item, dict):
                    _text(key, "field_name", 100)
                validate(child, depth + 1)
        else:
            raise MatchInputError("invalid_match_fields")

    validate(value)
    if len(_json(value)) > 8000:
        raise MatchInputError("invalid_match_fields")
    return value


def _selection(value, match=None):
    if value is None:
        return None
    if not isinstance(value, dict) or set(value) - {
        "guide_id",
        "revision_id",
        "selection_revision",
        "selected_at",
        "game",
        "platform",
        "mode",
    }:
        raise MatchInputError("invalid_match_selection")
    result = {
        "guide_id": _identifier(value.get("guide_id"), prefix="guide-"),
        "revision_id": _identifier(value.get("revision_id"), prefix="revision-"),
    }
    revision = value.get("selection_revision")
    if type(revision) is not int or revision < 0:
        raise MatchInputError("invalid_match_selection")
    result["selection_revision"] = revision
    for key in ("game", "platform", "mode"):
        if key in value:
            result[key] = _text(value[key], key)
            if (
                match is not None
                and fold_script(result[key].strip()).casefold()
                != fold_script(match[key].strip()).casefold()
            ):
                raise MatchConflictError("match_selection_dimensions_conflict")
    if "selected_at" in value:
        result["selected_at"] = _timestamp(value["selected_at"])
    return result


class MatchStore:
    """Synchronous operations on the Runtime connection, under its existing guard.

    Mutations start/commit their own transaction when no transaction exists.
    Inside an outer transaction they use a savepoint and never commit the caller's
    work. ``commit=False`` explicitly requires such an outer transaction. The
    Runtime must keep its guard over the complete outer transaction.
    """

    def __init__(self, db: sqlite3.Connection, scope: str):
        try:
            identity = json.loads(scope)
        except (TypeError, ValueError):
            raise MatchInputError("invalid_match_scope") from None
        if not isinstance(identity, list) or len(identity) != 2:
            raise MatchInputError("invalid_match_scope")
        self.user_id, self.character_id = (_text(item, "scope", 100) for item in identity)
        self.scope = json.dumps(identity)
        self._db = db

    def _one(self, sql, parameters=()):
        cursor = self._db.execute(sql, parameters)
        row = cursor.fetchone()
        return (
            dict(zip((item[0] for item in cursor.description), row, strict=True)) if row else None
        )

    def _all(self, sql, parameters=()):
        cursor = self._db.execute(sql, parameters)
        columns = [item[0] for item in cursor.description]
        return [dict(zip(columns, row, strict=True)) for row in cursor.fetchall()]

    @contextmanager
    def _mutation(self, commit=True):
        if type(commit) is not bool:
            raise MatchInputError("invalid_match_commit")
        nested = self._db.in_transaction
        if not commit and not nested:
            raise MatchConflictError("match_outer_transaction_required")
        marker = "match_" + uuid4().hex
        self._db.execute("SAVEPOINT " + marker if nested else "BEGIN IMMEDIATE")
        try:
            yield
            if nested:
                self._db.execute("RELEASE SAVEPOINT " + marker)
            else:
                self._db.commit()
        except BaseException:
            if nested:
                self._db.execute("ROLLBACK TO SAVEPOINT " + marker)
                self._db.execute("RELEASE SAVEPOINT " + marker)
            else:
                self._db.rollback()
            raise

    def _session(self, session_id):
        _identifier(session_id, access=True)
        if not self._one(
            "SELECT id FROM sessions WHERE id=? AND user_id=? AND character_id=?",
            (session_id, self.user_id, self.character_id),
        ):
            raise MatchAccessError("match_resource_not_found")

    def _turn(self, session_id, turn_id):
        _identifier(turn_id, access=True)
        result = self._one(
            "SELECT id,input FROM turns WHERE id=? AND session_id=?", (turn_id, session_id)
        )
        if result is None or result["input"] == "[已遗忘的对话]":
            raise MatchAccessError("match_resource_not_found")
        return result

    def revision(self, session_id):
        self._session(session_id)
        row = self._one(
            "SELECT revision FROM match_control WHERE scope=? AND session_id=?",
            (self.scope, session_id),
        )
        return row["revision"] if row else 0

    @staticmethod
    def _view(row):
        if row is None:
            return None
        result = {key: value for key, value in row.items() if key != "scope"}
        result["id"] = result["match_id"]
        result["selection"] = json.loads(result["selection"]) if result["selection"] else None
        return result

    def get(self, session_id, match_id):
        self._session(session_id)
        _identifier(match_id, prefix="match-", access=True)
        row = self._one(
            "SELECT * FROM matches WHERE scope=? AND session_id=? AND match_id=?",
            (self.scope, session_id, match_id),
        )
        if row is None:
            raise MatchAccessError("match_resource_not_found")
        return self._view(row)

    def current(self, session_id):
        self._session(session_id)
        row = self._one(
            "SELECT m.* FROM match_control c JOIN matches m ON m.match_id=c.current_match_id "
            "AND m.scope=c.scope AND m.session_id=c.session_id WHERE c.scope=? AND c.session_id=?",
            (self.scope, session_id),
        )
        return self._view(row)

    def catalog(self, session_id):
        self._session(session_id)
        return {
            "revision": self.revision(session_id),
            "current": self.current(session_id),
            "matches": [
                self._view(row)
                for row in self._all(
                    "SELECT * FROM matches WHERE scope=? AND session_id=? "
                    "ORDER BY created_at DESC,match_id DESC",
                    (self.scope, session_id),
                )
            ],
        }

    def _advance(self, session_id):
        if self.revision(session_id) >= 2**63 - 2:
            raise MatchConflictError("match_revision_exhausted")
        self._db.execute(
            "INSERT INTO match_control(scope,session_id,revision) VALUES (?,?,1) "
            "ON CONFLICT(scope,session_id) DO UPDATE SET revision=revision+1",
            (self.scope, session_id),
        )
        return self.revision(session_id)

    def _live(self, session_id, match_id):
        match = self.get(session_id, match_id)
        current = self.current(session_id)
        if not current or current["match_id"] != match_id or match["status"] == "ended":
            raise MatchConflictError("match_no_longer_current")
        return match

    def _result(self, session_id, match_id, revision, replayed):
        current_revision = self.revision(session_id)
        return {
            "revision": current_revision,
            "committed_revision": revision,
            "match_id": match_id,
            "match": self.get(session_id, match_id),
            "current": self.current(session_id),
            "replayed": replayed,
            "superseded": revision != current_revision,
        }

    @staticmethod
    def _control_request(action, value):
        if (
            not isinstance(action, str)
            or action not in _ACTION_FIELDS
            or not isinstance(value, dict)
        ):
            raise MatchInputError("invalid_match_control")
        required = {"request_id", "expected_revision"}
        if not required <= value.keys() or set(value) - required - _ACTION_FIELDS[action]:
            raise MatchInputError("invalid_match_control_fields")
        request_id = _identifier(value["request_id"])
        expected = value["expected_revision"]
        if type(expected) is not int or expected < 0:
            raise MatchInputError("invalid_match_expected_revision")
        payload = {key: item for key, item in value.items() if key not in required}
        if action in {"start", "new"}:
            for key in ("game", "platform", "mode"):
                payload[key] = _text(payload.get(key), key)
            payload["goal"] = _text(payload.get("goal", ""), "goal", 2000, empty=True)
            payload["game_version"] = (
                _text(payload["game_version"], "game_version")
                if payload.get("game_version") is not None
                else None
            )
        if action != "start":
            _identifier(payload.get("match_id"), prefix="match-")
        if action == "update":
            if not {"goal", "game_version"}.intersection(payload):
                raise MatchInputError("empty_match_update")
            if "goal" in payload:
                _text(payload["goal"], "goal", 2000, empty=True)
            if payload.get("game_version") is not None:
                _text(payload["game_version"], "game_version")
        if action == "observe":
            _identifier(payload.get("turn_id"))
            _text(payload.get("text"), "observation", 8000)
        fingerprint = hashlib.sha256(
            _json({"action": action, "payload": payload, "expected_revision": expected}).encode()
        ).hexdigest()
        return payload, request_id, expected, fingerprint

    def _check_request(self, session_id, request_id, expected, fingerprint):
        old = self._one(
            "SELECT * FROM match_requests WHERE scope=? AND session_id=? AND request_id=?",
            (self.scope, session_id, request_id),
        )
        if old:
            if old["fingerprint"] != fingerprint:
                raise MatchConflictError("match_request_conflict")
            return self._result(session_id, old["match_id"], old["revision"], True)
        if self.revision(session_id) != expected:
            raise MatchConflictError("match_revision_changed")
        return None

    def preflight(self, session_id, action, value):
        """Read-only shape/replay/CAS gate, before Runtime checks live turn bindings.

        A committed request can replay after erasure, end, or restart. A new
        request still needs Runtime binding validation and ``control`` repeats
        this same gate inside its mutation transaction.
        """
        self._session(session_id)
        _, request_id, expected, fingerprint = self._control_request(action, value)
        return self._check_request(session_id, request_id, expected, fingerprint)

    def control(self, session_id, action, value, *, selection=None, now=None, commit=True):
        self._session(session_id)
        payload, request_id, expected, fingerprint = self._control_request(action, value)
        timestamp = _now(now)
        with self._mutation(commit):
            replay = self._check_request(session_id, request_id, expected, fingerprint)
            if replay is not None:
                return replay
            if action == "start":
                if self.current(session_id):
                    raise MatchConflictError("match_already_active")
                match = None
            else:
                match = self._live(session_id, payload["match_id"])
            if action in {"start", "new"}:
                selected = _selection(selection, payload)
                revision = self._advance(session_id)
                if match:
                    self._db.execute(
                        "UPDATE matches SET status='ended',state_revision=?,updated_at=? "
                        "WHERE match_id=?",
                        (revision, timestamp, match["match_id"]),
                    )
                    self._deactivate(match["match_id"])
                match_id = "match-" + uuid4().hex
                self._db.execute(
                    "INSERT INTO matches VALUES (?,?,?,?,?,?,?,?,'active',?,?,?,?,?)",
                    (
                        match_id,
                        self.scope,
                        session_id,
                        payload["game"],
                        payload["platform"],
                        payload["mode"],
                        payload["game_version"],
                        payload["goal"],
                        revision,
                        _json(selected) if selected else None,
                        timestamp,
                        timestamp,
                        timestamp,
                    ),
                )
                self._db.execute(
                    "UPDATE match_control SET current_match_id=? WHERE scope=? AND session_id=?",
                    (match_id, self.scope, session_id),
                )
            else:
                match_id = match["match_id"]
                if action == "observe":
                    revision = self.add_observation(
                        session_id,
                        match_id,
                        payload["turn_id"],
                        payload["text"],
                        source_kind="user_description",
                        now=timestamp,
                        commit=False,
                    )
                else:
                    revision = self._advance(session_id)
                    self._db.execute(
                        "UPDATE matches SET state_revision=?,updated_at=? WHERE match_id=?",
                        (revision, timestamp, match_id),
                    )
                    if action == "end":
                        self._db.execute(
                            "UPDATE matches SET status='ended' WHERE match_id=?", (match_id,)
                        )
                        self._deactivate(match_id)
                        self._db.execute(
                            "UPDATE match_control SET current_match_id=NULL WHERE scope=? AND session_id=?",
                            (self.scope, session_id),
                        )
                    elif action == "update":
                        for key in ("goal", "game_version"):
                            if key in payload:
                                self._db.execute(
                                    f"UPDATE matches SET {key}=? WHERE match_id=?",
                                    (payload[key], match_id),
                                )
                    elif action == "close_observation":
                        self._deactivate(match_id, vision=True)
            self._db.execute(
                "INSERT INTO match_requests VALUES (?,?,?,?,?,?,?,?)",
                (
                    self.scope,
                    session_id,
                    request_id,
                    action,
                    fingerprint,
                    match_id,
                    revision,
                    timestamp,
                ),
            )
            return self._result(session_id, match_id, revision, False)

    def _deactivate(self, match_id, *, vision=None):
        condition = (
            "" if vision is None else " AND source_kind" + ("=" if vision else "!=") + "'vision'"
        )
        self._db.execute(
            "UPDATE match_observations SET active=0 WHERE match_id=?" + condition, (match_id,)
        )

    def add_observation(
        self,
        session_id,
        match_id,
        turn_id,
        text,
        *,
        source_kind,
        observed_at=None,
        frame_id=None,
        fields=None,
        now=None,
        commit=True,
    ):
        self._session(session_id)
        if not isinstance(source_kind, str) or source_kind not in _KINDS:
            raise MatchInputError("invalid_match_observation_kind")
        kind = _KINDS[source_kind]
        timestamp = _now(now)
        observed = timestamp if observed_at is None else _timestamp(observed_at)
        if not 0 <= timestamp - observed <= OBSERVATION_SECONDS:
            raise MatchInputError("stale_match_observation")
        text = _text(text, "observation", 8000, empty=kind == "vision")
        values = _fields(fields)
        if kind == "vision":
            _text(frame_id, "frame", 128)
            text = text or "本轮画面未识别出可确认的动态字段。"
        elif frame_id is not None:
            raise MatchInputError("invalid_match_frame")
        with self._mutation(commit):
            match = self._live(session_id, match_id)
            turn = self._turn(session_id, turn_id)
            if kind != "vision" and text not in turn["input"]:
                raise MatchInputError("match_observation_requires_user_quote")
            if observed < max(match["created_at"], match["evidence_after"]):
                raise MatchInputError("match_observation_predates_current_context")
            latest = self._one(
                "SELECT MAX(observed_at) AS time FROM match_observations WHERE match_id=? "
                "AND active=1 AND "
                + ("source_kind='vision'" if kind == "vision" else "source_kind!='vision'"),
                (match_id,),
            )["time"]
            if latest is not None and observed < latest:
                raise MatchConflictError("match_observation_superseded")
            revision = self._advance(session_id)
            self._deactivate(match_id, vision=kind == "vision")
            self._db.execute(
                "INSERT INTO match_observations VALUES (?,?,?,?,?,?,?,?,?,1)",
                (
                    "observation-" + uuid4().hex,
                    match_id,
                    turn_id,
                    kind,
                    text,
                    _json(values),
                    observed,
                    observed + OBSERVATION_SECONDS,
                    frame_id,
                ),
            )
            self._db.execute(
                "UPDATE matches SET status='active',state_revision=?,updated_at=? WHERE match_id=?",
                (revision, timestamp, match_id),
            )
            return revision

    def read_context(self, session_id, match_id, now=None):
        match = self.get(session_id, match_id)
        timestamp = _now(now)
        observations = []
        current = self.current(session_id)
        if match["status"] == "active" and current and current["match_id"] == match_id:
            for row in self._all(
                "SELECT * FROM match_observations WHERE match_id=? AND active=1 "
                "AND observed_at<=? AND observed_at>=? AND expires_at>=? "
                "ORDER BY observed_at,observation_id",
                (match_id, timestamp, timestamp - OBSERVATION_SECONDS, timestamp),
            ):
                row["fields"] = json.loads(row["fields"])
                row["active"] = bool(row["active"])
                row["unknown"] = row["source_kind"] == "vision" and not any(
                    value is not None and value != "" and value != [] and value != {}
                    for value in row["fields"].values()
                )
                row["source"] = {
                    "kind": row["source_kind"],
                    "turn_id": row["turn_id"],
                    "frame_id": row["frame_id"],
                }
                observations.append(row)
        return match | {"observations": observations}

    def restart(self, *, now=None, commit=True):
        timestamp = _now(now)
        with self._mutation(commit):
            rows = self._all(
                "SELECT match_id,session_id FROM matches WHERE scope=? AND status!='ended'",
                (self.scope,),
            )
            for row in rows:
                revision = self._advance(row["session_id"])
                self._db.execute(
                    "UPDATE matches SET status='needs_update',state_revision=?,evidence_after=?,"
                    "updated_at=? WHERE match_id=?",
                    (revision, timestamp, timestamp, row["match_id"]),
                )
                self._deactivate(row["match_id"])
        return len(rows)

    def sync_selection(self, session_id, selection, *, now=None, commit=True):
        self._session(session_id)
        timestamp = _now(now)
        with self._mutation(commit):
            match = self.current(session_id)
            selected = _selection(selection, match)
            if match is None or match["selection"] == selected:
                return self.revision(session_id)
            revision = self._advance(session_id)
            self._db.execute(
                "UPDATE matches SET selection=?,state_revision=?,evidence_after=?,updated_at=? "
                "WHERE match_id=?",
                (
                    _json(selected) if selected else None,
                    revision,
                    timestamp,
                    timestamp,
                    match["match_id"],
                ),
            )
            return revision

    def erase_observations(self, turn_ids, *, now=None, commit=True):
        if not isinstance(turn_ids, (list, tuple, set, frozenset)):
            raise MatchInputError("invalid_match_turn_ids")
        for turn_id in turn_ids:
            _identifier(turn_id)
        timestamp = _now(now)
        with self._mutation(commit):
            affected = {}
            removed = 0
            for turn_id in set(turn_ids):
                rows = self._all(
                    "SELECT DISTINCT m.match_id,m.session_id FROM match_observations o "
                    "JOIN matches m ON m.match_id=o.match_id WHERE m.scope=? AND o.turn_id=?",
                    (self.scope, turn_id),
                )
                affected.update((row["match_id"], row["session_id"]) for row in rows)
                removed += self._db.execute(
                    "DELETE FROM match_observations WHERE turn_id=? AND match_id IN "
                    "(SELECT match_id FROM matches WHERE scope=?)",
                    (turn_id, self.scope),
                ).rowcount
            revisions = {
                session_id: self._advance(session_id) for session_id in set(affected.values())
            }
            for match_id, session_id in affected.items():
                self._db.execute(
                    "UPDATE matches SET state_revision=?,updated_at=? WHERE match_id=?",
                    (revisions[session_id], timestamp, match_id),
                )
        return removed

    def forget_goals(self, match_ids, *, now=None, commit=True):
        """Erase goal copies without reviving ended matches or retaining receipts.

        Like ``erase_observations``, well-formed foreign or absent identities are
        ignored. Each affected session advances once; its current match also
        gains a new advice boundary even when only a historical goal was erased.
        Replaying a durable erasure intent after success is a no-op.
        """
        if not isinstance(match_ids, (list, tuple, set, frozenset)):
            raise MatchInputError("invalid_match_ids")
        for match_id in match_ids:
            _identifier(match_id, prefix="match-")
        timestamp = _now(now)
        with self._mutation(commit):
            affected = {}
            for match_id in set(match_ids):
                row = self._one(
                    "SELECT match_id,session_id FROM matches "
                    "WHERE scope=? AND match_id=? AND goal!=''",
                    (self.scope, match_id),
                )
                if row is not None:
                    affected[row["match_id"]] = row["session_id"]
            revisions = {
                session_id: self._advance(session_id) for session_id in set(affected.values())
            }
            for match_id, session_id in affected.items():
                self._db.execute(
                    "UPDATE matches SET goal='',state_revision=?,evidence_after=?,updated_at=? "
                    "WHERE scope=? AND match_id=?",
                    (revisions[session_id], timestamp, timestamp, self.scope, match_id),
                )
            for session_id, revision in revisions.items():
                self._db.execute(
                    "UPDATE matches SET state_revision=?,evidence_after=?,updated_at=? "
                    "WHERE scope=? AND session_id=? AND match_id="
                    "(SELECT current_match_id FROM match_control WHERE scope=? AND session_id=?)",
                    (
                        revision,
                        timestamp,
                        timestamp,
                        self.scope,
                        session_id,
                        self.scope,
                        session_id,
                    ),
                )
        return len(affected)
