"""Small SQLite memory authority, independent of graph checkpoints and model SDKs.

Only current facts are recalled. Corrections retain provenance; erasure removes
the source and *all* facts derived from it. Tombstones contain identifiers, never
the erased text. Extraction jobs are durable and leased, and commit only against
the erasure/correction barrier they observed. Backups are restored explicitly into a
single scope while retaining that scope's newer erasure/correction decisions.
"""

from __future__ import annotations

import hashlib
import json
import math
import re
import sqlite3
import time
from contextlib import closing, contextmanager
from pathlib import Path
from threading import RLock
from typing import Any
from uuid import uuid4

from ai_neko.config.paths import DataPaths, DataRootError, safe_child

from .recall import bm25_rank, tokenize
from .script_fold import fold_script


class MemoryInputError(ValueError):
    """Invalid memory or persona input."""


class MemoryConflictError(ValueError):
    """A stale revision, erased source, or conflicting retry cannot be written."""


class MemoryAccessError(ValueError):
    """The requested object does not exist in this service's scope."""


DEFAULT_PERSONA = {
    "name": "小猫",
    "user_name": "你",
    "traits": ["温柔", "好奇", "坦诚"],
    "speaking_style": "自然亲切，简洁中文；适度猫娘口吻，不每句加喵。",
    "catchphrase": "",
    "game_style": "先给最有用的一步和简短理由；看不清或资料不足就说明，不编造局势。",
}
_LIMITS = {"name": 40, "user_name": 40, "speaking_style": 400, "catchphrase": 60, "game_style": 400}
_KINDS = {"preference", "event", "fact"}
_BACKUP_NAME = re.compile(r"memory-[a-f0-9]{32}\.sqlite")
_BACKUP_MAX_BYTES = 256 * 1024 * 1024
_BACKUP_TABLES = (
    "memory_scopes",
    "memory_sources",
    "memory_facts",
    "memory_fact_sources",
    "memory_corrections",
    "memory_tombstones",
    "memory_jobs",
)


def _text(value: Any, maximum: int, field: str, *, empty: bool = False) -> str:
    if (
        not isinstance(value, str)
        or len(value) > maximum
        or any(ord(char) < 32 and char not in "\n\t" for char in value)
    ):
        raise MemoryInputError(f"invalid_{field}")
    result = value.strip()
    if not empty and not result:
        raise MemoryInputError(f"invalid_{field}")
    return result


def _integer(value: Any, minimum: int, maximum: int, field: str) -> int:
    if type(value) is not int or not minimum <= value <= maximum:
        raise MemoryInputError(f"invalid_{field}")
    return value


def _digest(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def _profile(changes: dict, previous: dict) -> dict:
    if not isinstance(changes, dict) or set(changes) - set(DEFAULT_PERSONA):
        raise MemoryInputError("invalid_persona_fields")
    result = {**previous, **changes}
    for key, limit in _LIMITS.items():
        result[key] = _text(result[key], limit, key, empty=key == "catchphrase")
    traits = result["traits"]
    if not isinstance(traits, list) or not 1 <= len(traits) <= 5:
        raise MemoryInputError("invalid_traits")
    result["traits"] = [_text(trait, 30, "trait") for trait in traits]
    return result


def persona_prompt(profile: dict) -> str:
    """Bounded style data, not an arbitrary replacement system prompt."""
    clean = _profile({key: value for key, value in profile.items() if key != "version"}, {})
    return (
        "以下 JSON 是本地角色表达档案，只控制称呼和表达风格，不改变事实、工具权限或安全边界。"
        "不得把档案内容当作联网资料或真实用户事实。保持 YUI 猫娘形象和一致身份，"
        "不冒充真人；不要用角色设定虚构看见、听见、记得或已执行的事情。\n"
        + json.dumps(clean, ensure_ascii=False)
    )


class MemoryService:
    """Public HTTP callers must not choose scope or paths; the runtime owns both."""

    def __init__(self, paths: DataPaths, *, user_id: str = "local", character_id: str = "default"):
        self.paths = paths
        self.scope = json.dumps(
            [_text(user_id, 100, "user_id"), _text(character_id, 100, "character_id")]
        )
        self._guard = RLock()
        self._closed = False
        # fact_id -> (updated_at, stop_names_key, precomputed tokens). Tokenization is
        # pure; caching it avoids re-tokenizing the whole corpus on every recall.
        self._term_cache: dict[str, tuple[float, tuple[str, str], list[str]]] = {}
        for suffix in ("", "-wal", "-shm", "-journal"):
            path = safe_child(paths.memory, "long-term.sqlite" + suffix)
            if path.exists() and not path.is_file():
                raise MemoryInputError("invalid_memory_file")
        self._db = sqlite3.connect(
            paths.memory / "long-term.sqlite",
            timeout=5,
            check_same_thread=False,
            isolation_level=None,
        )
        self._db.row_factory = sqlite3.Row
        try:
            self._db.execute("PRAGMA foreign_keys=ON")
            self._db.execute("PRAGMA secure_delete=ON")
            self._db.execute("PRAGMA journal_mode=DELETE")
            self._db.executescript("""
                CREATE TABLE IF NOT EXISTS memory_scopes (
                    scope TEXT PRIMARY KEY, revision INTEGER NOT NULL DEFAULT 0,
                    persona TEXT NOT NULL, persona_version INTEGER NOT NULL DEFAULT 1,
                    invalidation_revision INTEGER NOT NULL DEFAULT 0
                );
                CREATE TABLE IF NOT EXISTS memory_sources (
                    scope TEXT NOT NULL, id TEXT NOT NULL, body TEXT NOT NULL,
                    turn_id TEXT, created_at REAL NOT NULL,
                    PRIMARY KEY(scope,id)
                );
                CREATE TABLE IF NOT EXISTS memory_facts (
                    scope TEXT NOT NULL, id TEXT NOT NULL, fact_key TEXT NOT NULL,
                    content TEXT NOT NULL, kind TEXT NOT NULL, revision INTEGER NOT NULL,
                    created_at REAL NOT NULL, updated_at REAL NOT NULL,
                    PRIMARY KEY(scope,id), UNIQUE(scope,fact_key)
                );
                CREATE TABLE IF NOT EXISTS memory_fact_sources (
                    scope TEXT NOT NULL, fact_id TEXT NOT NULL, source_id TEXT NOT NULL,
                    PRIMARY KEY(scope,fact_id,source_id),
                    FOREIGN KEY(scope,fact_id) REFERENCES memory_facts(scope,id)
                        ON DELETE CASCADE
                );
                CREATE TABLE IF NOT EXISTS memory_corrections (
                    scope TEXT NOT NULL, fact_id TEXT NOT NULL, revision INTEGER NOT NULL,
                    previous_content TEXT NOT NULL, source_id TEXT NOT NULL,
                    PRIMARY KEY(scope,fact_id,revision),
                    FOREIGN KEY(scope,fact_id) REFERENCES memory_facts(scope,id)
                        ON DELETE CASCADE
                );
                CREATE TABLE IF NOT EXISTS memory_tombstones (
                    scope TEXT NOT NULL, kind TEXT NOT NULL, token TEXT NOT NULL,
                    revision INTEGER NOT NULL, PRIMARY KEY(scope,kind,token)
                );
                CREATE TABLE IF NOT EXISTS memory_jobs (
                    scope TEXT NOT NULL, id TEXT NOT NULL, source_id TEXT NOT NULL,
                    expected_revision INTEGER NOT NULL, status TEXT NOT NULL,
                    lease_token TEXT, lease_until REAL, attempts INTEGER NOT NULL DEFAULT 0,
                    created_at REAL NOT NULL, PRIMARY KEY(scope,id), UNIQUE(scope,source_id)
                );
            """)
            with self._transaction():
                columns = {row[1] for row in self._db.execute("PRAGMA table_info(memory_scopes)")}
                if "invalidation_revision" not in columns:
                    self._db.execute(
                        "ALTER TABLE memory_scopes ADD COLUMN "
                        "invalidation_revision INTEGER NOT NULL DEFAULT 0"
                    )
                self._db.execute(
                    "INSERT OR IGNORE INTO memory_scopes "
                    "(scope,revision,persona,persona_version) VALUES (?,0,?,1)",
                    (self.scope, json.dumps(DEFAULT_PERSONA, ensure_ascii=False)),
                )
        except BaseException:
            self._db.close()
            raise

    @contextmanager
    def _transaction(self):
        with self._guard:
            if self._closed:
                raise MemoryConflictError("memory_closed")
            self._db.execute("BEGIN IMMEDIATE")
            try:
                yield
            except BaseException:
                self._db.rollback()
                raise
            else:
                self._db.commit()

    def _revision(self) -> int:
        return self._db.execute(
            "SELECT revision FROM memory_scopes WHERE scope=?", (self.scope,)
        ).fetchone()[0]

    def revision(self) -> int:
        with self._transaction():
            return self._revision()

    def _check_revision(self, expected: int | None):
        if expected is not None:
            _integer(expected, 0, 2**63 - 1, "revision")
            if expected != self._revision():
                raise MemoryConflictError("stale_memory_revision")

    def _job_revision_valid(self, expected: int) -> bool:
        row = self._db.execute(
            "SELECT invalidation_revision,revision FROM memory_scopes WHERE scope=?", (self.scope,)
        ).fetchone()
        return row[0] <= expected <= row[1]

    def _bump(self, *, invalidate: bool = False) -> int:
        self._db.execute(
            "UPDATE memory_scopes SET revision=revision+1 WHERE scope=?", (self.scope,)
        )
        revision = self._revision()
        if invalidate:
            self._db.execute(
                "UPDATE memory_scopes SET invalidation_revision=? WHERE scope=?",
                (revision, self.scope),
            )
        return revision

    def get_persona(self) -> dict:
        with self._transaction():
            row = self._db.execute(
                "SELECT persona,persona_version FROM memory_scopes WHERE scope=?", (self.scope,)
            ).fetchone()
            return {**json.loads(row[0]), "version": row[1]}

    def update_persona(self, changes: dict, *, expected_version: int | None = None) -> dict:
        with self._transaction():
            row = self._db.execute(
                "SELECT persona,persona_version FROM memory_scopes WHERE scope=?", (self.scope,)
            ).fetchone()
            if expected_version is not None:
                _integer(expected_version, 1, 2**63 - 1, "persona_version")
                if expected_version != row[1]:
                    raise MemoryConflictError("stale_persona_version")
            profile = _profile(changes, json.loads(row[0]))
            version = row[1] + 1
            self._db.execute(
                "UPDATE memory_scopes SET persona=?,persona_version=? WHERE scope=?",
                (json.dumps(profile, ensure_ascii=False), version, self.scope),
            )
            return {**profile, "version": version}

    def _blocked(self, kind: str, token: str) -> bool:
        return (
            self._db.execute(
                "SELECT 1 FROM memory_tombstones WHERE scope=? AND kind=? AND token=?",
                (self.scope, kind, token),
            ).fetchone()
            is not None
        )

    def _source(self, source_id: str, body: str, turn_id: str | None = None):
        if self._blocked("source", source_id):
            raise MemoryConflictError("erased_memory_source")
        row = self._db.execute(
            "SELECT body FROM memory_sources WHERE scope=? AND id=?", (self.scope, source_id)
        ).fetchone()
        if row and row[0] != body:
            raise MemoryConflictError("conflicting_memory_source")
        self._db.execute(
            "INSERT OR IGNORE INTO memory_sources VALUES (?,?,?,?,?)",
            (self.scope, source_id, body, turn_id, time.time()),
        )

    def _fact(self, fact_id: str) -> dict:
        row = self._db.execute(
            "SELECT * FROM memory_facts WHERE scope=? AND id=?", (self.scope, fact_id)
        ).fetchone()
        if row is None:
            raise MemoryAccessError("memory_not_found")
        sources = self._db.execute(
            "SELECT source_id FROM memory_fact_sources WHERE scope=? AND fact_id=? "
            "ORDER BY source_id",
            (self.scope, fact_id),
        ).fetchall()
        return {key: row[key] for key in row.keys() if key != "scope"} | {  # noqa: SIM118 (Row.keys)
            "source_ids": [source[0] for source in sources]
        }

    def _validate_fact(self, content: str, fact_key: str | None, kind: str) -> tuple[str, str, str]:
        content = _text(content, 1000, "content")
        key = (
            _text(fact_key, 200, "fact_key")
            if fact_key is not None
            else _digest(content.casefold())
        )
        if not isinstance(kind, str) or kind not in _KINDS:
            raise MemoryInputError("invalid_memory_kind")
        return content, key, kind

    def _write_fact(self, content: str, key: str, kind: str, source_id: str, revision: int) -> dict:
        old = self._db.execute(
            "SELECT id,content FROM memory_facts WHERE scope=? AND fact_key=?", (self.scope, key)
        ).fetchone()
        if old and old[1] != content:
            raise MemoryConflictError("memory_requires_correction")
        fact_id = old[0] if old else uuid4().hex
        now = time.time()
        self._db.execute(
            "INSERT OR IGNORE INTO memory_facts VALUES (?,?,?,?,?,?,?,?)",
            (self.scope, fact_id, key, content, kind, revision, now, now),
        )
        self._db.execute(
            "INSERT OR IGNORE INTO memory_fact_sources VALUES (?,?,?)",
            (self.scope, fact_id, source_id),
        )
        return self._fact(fact_id)

    def remember(
        self,
        content: str,
        *,
        source_id: str,
        source_text: str | None = None,
        fact_key: str | None = None,
        kind: str = "preference",
        expected_revision: int | None = None,
    ) -> dict:
        content, key, kind = self._validate_fact(content, fact_key, kind)
        source_id = _text(source_id, 200, "source_id")
        body = _text(source_text if source_text is not None else content, 16000, "source_text")
        with self._transaction():
            self._check_revision(expected_revision)
            self._source(source_id, body)
            old = self._db.execute(
                "SELECT f.id FROM memory_facts f JOIN memory_fact_sources s "
                "ON f.scope=s.scope AND f.id=s.fact_id WHERE f.scope=? AND f.fact_key=? "
                "AND f.content=? AND s.source_id=?",
                (self.scope, key, content, source_id),
            ).fetchone()
            if old:
                return self._fact(old[0])
            return self._write_fact(content, key, kind, source_id, self._bump())

    def correct(
        self,
        fact_id: str,
        content: str,
        *,
        source_id: str,
        source_text: str | None = None,
        expected_revision: int | None = None,
    ) -> dict:
        content = _text(content, 1000, "content")
        source_id = _text(source_id, 200, "source_id")
        body = _text(source_text if source_text is not None else content, 16000, "source_text")
        with self._transaction():
            self._check_revision(expected_revision)
            fact = self._fact(fact_id)
            self._source(source_id, body)
            if fact["content"] == content and source_id in fact["source_ids"]:
                return fact
            revision = self._bump(invalidate=True)
            self._db.execute(
                "INSERT INTO memory_corrections VALUES (?,?,?,?,?)",
                (self.scope, fact_id, revision, fact["content"], source_id),
            )
            self._db.execute(
                "UPDATE memory_facts SET content=?,revision=?,updated_at=? WHERE scope=? AND id=?",
                (content, revision, time.time(), self.scope, fact_id),
            )
            self._db.execute(
                "INSERT OR IGNORE INTO memory_fact_sources VALUES (?,?,?)",
                (self.scope, fact_id, source_id),
            )
            # An older backup must not restore the superseded value of this fact.
            self._tombstone("superseded", fact_id, revision)
            return self._fact(fact_id)

    def _tombstone(self, kind: str, token: str, revision: int):
        self._db.execute(
            "INSERT INTO memory_tombstones VALUES (?,?,?,?) "
            "ON CONFLICT(scope,kind,token) DO UPDATE SET revision="
            "MAX(revision,excluded.revision)",
            (self.scope, kind, token, revision),
        )

    def _erase(
        self,
        fact_ids: set[str],
        source_ids: set[str],
        revision: int,
        *,
        with_evidence: bool = False,
    ) -> dict:
        # Shared raw evidence can include several facts. Erase the whole provenance
        # component rather than retaining a hidden copy of an erased fact in it.
        changed = True
        while changed:
            old_sizes = len(fact_ids), len(source_ids)
            for row in self._db.execute(
                "SELECT fact_id,source_id FROM memory_fact_sources WHERE scope=?", (self.scope,)
            ):
                if row[0] in fact_ids or row[1] in source_ids:
                    fact_ids.add(row[0])
                    source_ids.add(row[1])
            changed = old_sizes != (len(fact_ids), len(source_ids))
        # Contents are returned only when the caller opts in (the runtime's live
        # history cleanup), and are never persisted. Tombstones and erasure
        # intents remain identifier-only.
        evidence = (
            {
                "fact_contents": self._contents("memory_facts", "content", fact_ids),
                "source_bodies": self._contents("memory_sources", "body", source_ids),
            }
            if with_evidence
            else {}
        )
        for fact_id in fact_ids:
            self._tombstone("fact", fact_id, revision)
            self._db.execute(
                "DELETE FROM memory_facts WHERE scope=? AND id=?", (self.scope, fact_id)
            )
        for source_id in source_ids:
            self._tombstone("source", source_id, revision)
            self._db.execute(
                "DELETE FROM memory_sources WHERE scope=? AND id=?", (self.scope, source_id)
            )
            self._db.execute(
                "UPDATE memory_jobs SET status='cancelled',lease_token=NULL,"
                "lease_until=NULL WHERE scope=? AND source_id=?",
                (self.scope, source_id),
            )
        return {
            "revision": revision,
            "fact_ids": sorted(fact_ids),
            "source_ids": sorted(source_ids),
            **evidence,
        }

    def _contents(self, table: str, column: str, ids: set[str]) -> list[str]:
        if not ids:
            return []
        marks = ",".join("?" for _ in ids)
        return [
            row[0]
            for row in self._db.execute(
                f"SELECT {column} FROM {table} WHERE scope=? AND id IN ({marks})",
                (self.scope, *sorted(ids)),
            )
        ]

    def forget(
        self, fact_id: str, *, expected_revision: int | None = None, with_evidence: bool = False
    ) -> dict:
        with self._transaction():
            self._check_revision(expected_revision)
            self._fact(fact_id)
            return self._erase(
                {fact_id}, set(), self._bump(invalidate=True), with_evidence=with_evidence
            )

    def forget_source(
        self, source_id: str, *, expected_revision: int | None = None, with_evidence: bool = False
    ) -> dict:
        source_id = _text(source_id, 200, "source_id")
        with self._transaction():
            self._check_revision(expected_revision)
            return self._erase(
                set(), {source_id}, self._bump(invalidate=True), with_evidence=with_evidence
            )

    def erasure_state(self) -> dict:
        """Replayable cleanup evidence for separately stored journals/checkpoints.

        Runtime startup must reconcile these markers before reading old history:
        a crash can fall between the memory transaction and journal cleanup.
        """
        with self._transaction():
            rows = self._db.execute(
                "SELECT kind,token FROM memory_tombstones WHERE scope=? ORDER BY kind,token",
                (self.scope,),
            ).fetchall()
            return {
                "revision": self._revision(),
                "source_ids": [row[1] for row in rows if row[0] == "source"],
                "fact_ids": [row[1] for row in rows if row[0] == "fact"],
            }

    def list_facts(self) -> list[dict]:
        with self._transaction():
            rows = self._db.execute(
                "SELECT f.*,s.source_id AS link_source_id FROM memory_facts f "
                "LEFT JOIN memory_fact_sources s ON f.scope=s.scope AND f.id=s.fact_id "
                "WHERE f.scope=? ORDER BY f.updated_at DESC,f.id,s.source_id",
                (self.scope,),
            ).fetchall()
            facts: list[dict] = []
            current: dict | None = None
            for row in rows:
                if current is None or current["id"] != row["id"]:
                    current = {
                        key: row[key]
                        for key in row.keys()  # noqa: SIM118 (sqlite3.Row columns)
                        if key not in ("scope", "link_source_id")
                    } | {"source_ids": []}
                    facts.append(current)
                if row["link_source_id"] is not None:
                    current["source_ids"].append(row["link_source_id"])
            return facts

    def recall(self, query: str = "", *, limit: int = 10) -> list[dict]:
        query = fold_script(_text(query, 2000, "query", empty=True)).casefold()
        _integer(limit, 1, 50, "limit")
        facts = self.list_facts()
        if not query:
            return facts[:limit]
        clauses = [re.sub(r"\s", "", clause) for clause in re.split(r"[，。！？,.!?;；\n]", query)]
        if any(
            re.fullmatch(
                r"(?:说说|告诉我|你知道)?(?:我的(?:偏好|喜好|爱好|习惯)(?:是什么|有哪些|呢)?"
                r"|我(?:喜欢|偏爱)(?:喝|吃|玩|看|听|做)?(?:什么|啥))",
                clause,
            )
            for clause in clauses
        ):
            return [fact for fact in facts if fact["kind"] == "preference"][:limit]
        if any(
            re.fullmatch(r"(?:你)?(?:还)?记得我(?:什么|哪些事情|吗)?|关于我的(?:事情|信息)", clause)
            for clause in clauses
        ):
            return facts[:limit]
        if any(
            re.fullmatch(r"我(?:最近|以前|之前)(?:做过什么|做了什么|发生了什么)", clause)
            for clause in clauses
        ):
            return [fact for fact in facts if fact["kind"] == "event"][:limit]
        # The authority filtered the scope BEFORE ranking. Only these current
        # fact texts contribute document frequencies; ids/hashes are not terms.
        stop_terms = frozenset(
            {
                "我的",
                "什么",
                "喜欢",
                "我喜",
                "我偏",
                "偏爱",
                "我是",
                "的是",
                "现在",
                "最近",
                "之前",
                "以前",
                "今天",
                "昨天",
                "爱好",
                "偏好",
                "一下",
                "怎么",
                "如何",
                "知道",
                "记得",
                "告诉",
                "说说",
                "还有",
                "有什",
                "是否",
                "哪里",
                "哪个",
                "哪些",
                "多少",
                "我喜欢",
                "你喜欢",
                "喜欢什",
                "有什么",
            }
        )
        profile = self.get_persona()
        stop_names = (profile["name"], profile["user_name"])
        live = {fact["id"] for fact in facts}
        for cached_id in list(self._term_cache):
            if cached_id not in live:
                del self._term_cache[cached_id]
        rows = []
        for fact in facts:
            text = fact["content"] + " " + (
                fact["fact_key"] if not re.fullmatch(r"[a-f0-9]{64}", fact["fact_key"]) else ""
            )
            cached = self._term_cache.get(fact["id"])
            if cached is None or cached[0] != fact["updated_at"] or cached[1] != stop_names:
                cached = (
                    fact["updated_at"],
                    stop_names,
                    tokenize(text, stop_names, stop_terms=stop_terms),
                )
                self._term_cache[fact["id"]] = cached
            rows.append({"text": text, "_terms": cached[2], "fact": fact})
        ranked = bm25_rank(
            query, rows, stop_terms=stop_terms, stop_names=[profile["name"], profile["user_name"]]
        )
        return [row["fact"] for row, _ in ranked[:limit]]

    def sources(self, fact_id: str) -> list[dict]:
        with self._transaction():
            fact = self._fact(fact_id)
            return [
                dict(row)
                for source_id in fact["source_ids"]
                if (
                    row := self._db.execute(
                        "SELECT id AS source_id,body AS source_text,turn_id,created_at FROM memory_sources "
                        "WHERE scope=? AND id=?",
                        (self.scope, source_id),
                    ).fetchone()
                )
                is not None
            ]

    def enqueue_extraction(
        self,
        source_id: str,
        source_text: str,
        *,
        turn_id: str | None = None,
        expected_revision: int | None = None,
    ) -> dict:
        source_id = _text(source_id, 200, "source_id")
        body = _text(source_text, 16000, "source_text")
        if turn_id is not None:
            turn_id = _text(turn_id, 200, "turn_id")
        with self._transaction():
            if expected_revision is not None:
                _integer(expected_revision, 0, 2**63 - 1, "revision")
                if not self._job_revision_valid(expected_revision):
                    raise MemoryConflictError("stale_memory_revision")
            self._source(source_id, body, turn_id)
            self._db.execute(
                "INSERT OR IGNORE INTO memory_jobs VALUES (?,?,?,?, 'pending',NULL,NULL,0,?)",
                (
                    self.scope,
                    _digest(self.scope + "\0" + source_id),
                    source_id,
                    self._revision(),
                    time.time(),
                ),
            )
            return self._job(_digest(self.scope + "\0" + source_id))

    def _job(self, job_id: str) -> dict:
        row = self._db.execute(
            "SELECT j.*,s.body AS source_text,s.turn_id FROM memory_jobs j "
            "LEFT JOIN memory_sources s ON j.scope=s.scope AND j.source_id=s.id "
            "WHERE j.scope=? AND j.id=?",
            (self.scope, job_id),
        ).fetchone()
        if row is None:
            raise MemoryAccessError("memory_job_not_found")
        return {key: row[key] for key in row.keys() if key != "scope"}  # noqa: SIM118 (Row)

    def pending_jobs(self) -> list[dict]:
        with self._transaction():
            rows = self._db.execute(
                "SELECT id FROM memory_jobs WHERE scope=? AND "
                "(status='pending' OR (status='running' AND lease_until<=?)) "
                "ORDER BY created_at",
                (self.scope, time.time()),
            ).fetchall()
            return [self._job(row[0]) for row in rows]

    def claim_extraction(self, job_id: str, *, lease_seconds: int = 60) -> dict | None:
        _integer(lease_seconds, 1, 3600, "lease_seconds")
        with self._transaction():
            job = self._job(job_id)
            if job["status"] not in {"pending", "running"}:
                return None
            if job["status"] == "running" and job["lease_until"] > time.time():
                return None
            if self._blocked("source", job["source_id"]) or (
                not self._job_revision_valid(job["expected_revision"])
            ):
                self._cancel_job(job_id)
                return None
            self._db.execute(
                "UPDATE memory_jobs SET status='running',lease_token=?,lease_until=?,"
                "attempts=attempts+1 WHERE scope=? AND id=?",
                (uuid4().hex, time.time() + lease_seconds, self.scope, job_id),
            )
            return self._job(job_id)

    def _cancel_job(self, job_id: str):
        self._db.execute(
            "UPDATE memory_jobs SET status='cancelled',lease_token=NULL,lease_until=NULL "
            "WHERE scope=? AND id=?",
            (self.scope, job_id),
        )

    def complete_extraction(self, job_id: str, facts: list[dict], *, lease_token: str) -> dict:
        if not isinstance(facts, list) or len(facts) > 10:
            raise MemoryInputError("invalid_extracted_facts")
        validated = []
        for fact in facts:
            if not isinstance(fact, dict) or set(fact) - {"content", "fact_key", "kind"}:
                raise MemoryInputError("invalid_extracted_fact")
            validated.append(
                self._validate_fact(
                    fact.get("content"), fact.get("fact_key"), fact.get("kind", "preference")
                )
            )
        with self._transaction():
            job = self._job(job_id)
            if job["status"] in {"completed", "cancelled"}:
                return {"status": job["status"], "facts": [], "revision": self._revision()}
            if job["status"] != "running" or job["lease_token"] != lease_token:
                raise MemoryConflictError("invalid_memory_job_lease")
            if (
                job["lease_until"] <= time.time()
                or self._blocked("source", job["source_id"])
                or not self._job_revision_valid(job["expected_revision"])
            ):
                self._cancel_job(job_id)
                return {"status": "cancelled", "facts": [], "revision": self._revision()}
            revision = self._bump() if validated else self._revision()
            written = [
                self._write_fact(content, key, kind, job["source_id"], revision)
                for content, key, kind in validated
            ]
            self._db.execute(
                "UPDATE memory_jobs SET status='completed',lease_token=NULL,"
                "lease_until=NULL WHERE scope=? AND id=?",
                (self.scope, job_id),
            )
            return {"status": "completed", "facts": written, "revision": revision}

    def fail_extraction(self, job_id: str, *, lease_token: str, retry: bool = True) -> dict:
        if type(retry) is not bool:
            raise MemoryInputError("invalid_retry")
        with self._transaction():
            job = self._job(job_id)
            if job["status"] == "cancelled":
                return job
            if job["status"] != "running" or job["lease_token"] != lease_token:
                raise MemoryConflictError("invalid_memory_job_lease")
            self._db.execute(
                "UPDATE memory_jobs SET status=?,lease_token=NULL,lease_until=NULL "
                "WHERE scope=? AND id=?",
                ("pending" if retry else "failed", self.scope, job_id),
            )
            return self._job(job_id)

    def _backup_directory(self) -> Path:
        try:
            directory = safe_child(self.paths.root, "backups")
        except DataRootError as exc:
            raise MemoryInputError("invalid_memory_backup") from exc
        if not directory.is_dir():
            raise MemoryInputError("invalid_memory_backup")
        return directory

    def _backup_path(self, backup_id: str) -> Path:
        if not isinstance(backup_id, str) or not _BACKUP_NAME.fullmatch(backup_id):
            raise MemoryInputError("invalid_memory_backup")
        try:
            path = safe_child(self._backup_directory(), backup_id)
        except DataRootError as exc:
            raise MemoryInputError("invalid_memory_backup") from exc
        if not path.is_file():
            raise MemoryAccessError("memory_backup_not_found")
        return path

    def _backup_owner(self, source: sqlite3.Connection, path: Path) -> dict:
        """New snapshots have an owner; old single-scope snapshots are unambiguous.

        Older multi-scope files can still be explicitly restored, but are not
        exposed for management: their creator cannot be reliably determined.
        """
        table = source.execute(
            "SELECT type FROM sqlite_master WHERE name='memory_backup_info'"
        ).fetchone()
        if table is None:
            scopes = source.execute("SELECT scope FROM memory_scopes LIMIT 2").fetchall()
            if len(scopes) != 1 or scopes[0][0] != self.scope:
                raise MemoryAccessError("memory_backup_scope_not_found")
            created_at = path.stat().st_mtime
        else:
            if table[0] != "table":
                raise MemoryInputError("invalid_memory_backup")
            rows = source.execute(
                "SELECT id,owner_scope,created_at FROM memory_backup_info LIMIT 2"
            ).fetchall()
            if len(rows) != 1 or rows[0][0] != path.name or rows[0][1] != self.scope:
                raise MemoryAccessError("memory_backup_scope_not_found")
            created_at = rows[0][2]
        if type(created_at) not in (int, float) or not math.isfinite(created_at) or created_at <= 0:
            raise MemoryInputError("invalid_memory_backup")
        return {"id": path.name, "created_at": created_at, "bytes": path.stat().st_size}

    @contextmanager
    def _open_backup(self, path: Path):
        if path.stat().st_size > _BACKUP_MAX_BYTES:
            raise MemoryInputError("memory_backup_too_large")
        # Immutable snapshots never need WAL/journal files or a write lock. The
        # app creates them with SQLite backup and closes them before exposing them.
        try:
            with closing(
                sqlite3.connect(path.as_uri() + "?mode=ro&immutable=1", uri=True)
            ) as source:
                source.row_factory = sqlite3.Row
                source.execute("PRAGMA trusted_schema=OFF")
                yield source
        except sqlite3.DatabaseError as exc:
            raise MemoryInputError("invalid_memory_backup") from exc

    def _read_backup(self, source: sqlite3.Connection, *, verify: bool = True) -> dict:
        if verify and source.execute("PRAGMA integrity_check").fetchone()[0] != "ok":
            raise MemoryInputError("invalid_memory_backup")
        tables = {}
        for table in _BACKUP_TABLES:
            schema = source.execute(
                "SELECT type FROM sqlite_master WHERE name=?", (table,)
            ).fetchone()
            if schema is None or schema[0] != "table":
                raise MemoryInputError("invalid_memory_backup")
            # Fixed table/column names only. Never interpolate schema identifiers
            # taken from a corrupt or replaced snapshot into write statements.
            expected = {row[1] for row in self._db.execute(f"PRAGMA table_info({table})")}
            actual = {row[1] for row in source.execute(f"PRAGMA table_info({table})")}
            if actual != expected and not (
                table == "memory_scopes" and actual == expected - {"invalidation_revision"}
            ):
                raise MemoryInputError("invalid_memory_backup")
            tables[table] = [
                dict(row)
                for row in source.execute(f"SELECT * FROM {table} WHERE scope=?", (self.scope,))
            ]
        if not tables["memory_scopes"]:
            raise MemoryAccessError("memory_backup_scope_not_found")
        try:
            if len(tables["memory_scopes"]) != 1:
                raise MemoryInputError("invalid_memory_backup")
            scope = tables["memory_scopes"][0]
            _integer(scope["revision"], 0, 2**63 - 2, "revision")
            _integer(scope["persona_version"], 1, 2**63 - 2, "persona_version")
            _profile(json.loads(scope["persona"]), {})
            source_ids = {row["id"] for row in tables["memory_sources"]}
            fact_ids = {row["id"] for row in tables["memory_facts"]}
            for row in tables["memory_sources"]:
                _text(row["id"], 200, "source_id")
                _text(row["body"], 16000, "source_text")
            for row in tables["memory_facts"]:
                _text(row["id"], 200, "fact_id")
                self._validate_fact(row["content"], row["fact_key"], row["kind"])
            for row in tables["memory_fact_sources"]:
                if row["fact_id"] not in fact_ids or row["source_id"] not in source_ids:
                    raise MemoryInputError("invalid_memory_backup")
            if {row["fact_id"] for row in tables["memory_fact_sources"]} != fact_ids:
                raise MemoryInputError("invalid_memory_backup")
        except (ValueError, TypeError, KeyError) as exc:
            raise MemoryInputError("invalid_memory_backup") from exc
        return tables

    def list_backups(self) -> list[dict]:
        """At most 100 owned snapshots, newest first; unidentifiable files are skipped.

        A readable owner with invalid memory tables is returned as non-restorable
        and remains deletable. A fully corrupt file has no trustworthy owner, so
        the application deliberately neither lists nor deletes it.
        """
        with self._guard:
            if self._closed:
                raise MemoryConflictError("memory_closed")
            directory = self._backup_directory()
            candidates = sorted(
                (path for path in directory.iterdir() if _BACKUP_NAME.fullmatch(path.name)),
                key=lambda path: path.name,
            )
            results = []
            for candidate in candidates:
                try:
                    path = self._backup_path(candidate.name)
                    with self._open_backup(path) as source:
                        metadata = self._backup_owner(source, path)
                        try:
                            # Listing validates schema and row content but skips
                            # the full-B-tree integrity scan; that deeper check
                            # runs on the explicit restore path instead.
                            tables = self._read_backup(source, verify=False)
                        except (MemoryInputError, MemoryAccessError, sqlite3.DatabaseError):
                            metadata.update(restorable=False, error="invalid_memory_backup")
                        else:
                            metadata.update(
                                restorable=True, facts_count=len(tables["memory_facts"])
                            )
                        results.append(metadata)
                except (MemoryInputError, MemoryAccessError, OSError):
                    continue
            return sorted(results, key=lambda item: (item["created_at"], item["id"]), reverse=True)[
                :100
            ]

    def delete_backup(self, backup_id: str) -> dict:
        with self._guard:
            if self._closed:
                raise MemoryConflictError("memory_closed")
            path = self._backup_path(backup_id)
            with self._open_backup(path) as source:
                self._backup_owner(source, path)
            path.unlink()
            return {"id": backup_id, "deleted": True}

    def backup(self) -> dict:
        # SQLite online backup includes all scopes; restore only imports this
        # service's scope. No provider credentials or external directories.
        with self._guard:
            if self._closed:
                raise MemoryConflictError("memory_closed")
            backup_id = "memory-" + uuid4().hex + ".sqlite"
            path = safe_child(self._backup_directory(), backup_id)
            created_at = time.time()
            page_bytes = self._db.execute("PRAGMA page_size").fetchone()[0]
            page_count = self._db.execute("PRAGMA page_count").fetchone()[0]
            if (page_count + 4) * page_bytes > _BACKUP_MAX_BYTES:
                raise MemoryInputError("memory_backup_too_large")
            # Reserve a new file without overwriting a pre-existing path, and
            # remove partial snapshots if writing fails.
            path.touch(mode=0o600, exist_ok=False)
            try:
                with closing(sqlite3.connect(path)) as target:
                    self._db.backup(target)
                    target.execute(
                        "CREATE TABLE memory_backup_info "
                        "(id TEXT PRIMARY KEY, owner_scope TEXT NOT NULL, created_at REAL NOT NULL)"
                    )
                    target.execute(
                        "INSERT INTO memory_backup_info VALUES (?,?,?)",
                        (backup_id, self.scope, created_at),
                    )
                    target.commit()
                    revision = target.execute(
                        "SELECT revision FROM memory_scopes WHERE scope=?", (self.scope,)
                    ).fetchone()[0]
                if path.stat().st_size > _BACKUP_MAX_BYTES:
                    raise MemoryInputError("memory_backup_too_large")
            except BaseException:
                path.unlink(missing_ok=True)
                raise
            return {"id": backup_id, "created_at": created_at, "revision": revision}

    def restore(
        self, backup_id: str, *, expected_revision: int | None = None, with_evidence: bool = False
    ) -> dict:
        with self._transaction():
            self._check_revision(expected_revision)
            path = self._backup_path(backup_id)
            with self._open_backup(path) as source:
                tables = self._read_backup(source)
            revision = max(self._revision(), tables["memory_scopes"][0]["revision"]) + 1
            current_persona_version = self._db.execute(
                "SELECT persona_version FROM memory_scopes WHERE scope=?", (self.scope,)
            ).fetchone()[0]
            persona_version = (
                max(current_persona_version, tables["memory_scopes"][0]["persona_version"]) + 1
            )
            old_source_bodies = {
                row[0]: row[1]
                for row in self._db.execute(
                    "SELECT id,body FROM memory_sources WHERE scope=?", (self.scope,)
                )
            }
            old_sources = set(old_source_bodies)
            # Keep current correction values: an explicit old backup must never
            # override a more recent correction or revive an erased fact/source.
            current_facts = {
                row["id"]: dict(row)
                for row in self._db.execute(
                    "SELECT * FROM memory_facts WHERE scope=?", (self.scope,)
                )
            }
            tombstones = [
                dict(row)
                for row in self._db.execute(
                    "SELECT * FROM memory_tombstones WHERE scope=?", (self.scope,)
                )
            ]
            corrected = {row["token"] for row in tombstones if row["kind"] == "superseded"}
            preserved_sources = [
                dict(row)
                for row in self._db.execute(
                    "SELECT * FROM memory_sources WHERE scope=?", (self.scope,)
                )
            ]
            preserved_links = [
                dict(row)
                for row in self._db.execute(
                    "SELECT * FROM memory_fact_sources WHERE scope=?", (self.scope,)
                )
                if row["fact_id"] in corrected
            ]
            preserved_corrections = [
                dict(row)
                for row in self._db.execute(
                    "SELECT * FROM memory_corrections WHERE scope=?", (self.scope,)
                )
                if row["fact_id"] in corrected
            ]
            for table in (
                "memory_fact_sources",
                "memory_corrections",
                "memory_jobs",
                "memory_facts",
                "memory_sources",
                "memory_tombstones",
                "memory_scopes",
            ):
                self._db.execute(f"DELETE FROM {table} WHERE scope=?", (self.scope,))
            # Restore with the *current* schema's explicit column order; snapshot
            # row dicts are only a value source. The single tolerated legacy gap
            # (memory_scopes without invalidation_revision) gets its default.
            schema_columns = {
                table: [row[1] for row in self._db.execute(f"PRAGMA table_info({table})")]
                for table in _BACKUP_TABLES
            }
            for table, rows in tables.items():
                columns = schema_columns[table]
                placeholders = ",".join("?" for _ in columns)
                for row in rows:
                    self._db.execute(
                        f"INSERT INTO {table} ({','.join(columns)}) VALUES ({placeholders})",
                        tuple(row.get(column, 0) for column in columns),
                    )
            for fact_id in corrected:
                if fact_id in current_facts:
                    row = current_facts[fact_id]
                    self._db.execute(
                        "INSERT INTO memory_facts VALUES (?,?,?,?,?,?,?,?) "
                        "ON CONFLICT(scope,id) DO UPDATE SET content=excluded.content,"
                        "revision=excluded.revision,updated_at=excluded.updated_at",
                        tuple(row.values()),
                    )
            for table, rows in (
                ("memory_sources", preserved_sources),
                ("memory_fact_sources", preserved_links),
                ("memory_corrections", preserved_corrections),
            ):
                for row in rows:
                    if table == "memory_sources" and row["id"] not in {
                        link["source_id"] for link in preserved_links
                    }:
                        continue
                    self._db.execute(
                        f"INSERT OR IGNORE INTO {table} VALUES ({','.join('?' for _ in row)})",
                        tuple(row.values()),
                    )
            for row in tombstones:
                self._tombstone(row["kind"], row["token"], row["revision"])
            all_tombstones = self._db.execute(
                "SELECT kind,token FROM memory_tombstones WHERE scope=?", (self.scope,)
            ).fetchall()
            erased = self._erase(
                {row[1] for row in all_tombstones if row[0] == "fact"},
                {row[1] for row in all_tombstones if row[0] == "source"},
                revision,
                with_evidence=with_evidence,
            )
            remaining_facts = {
                row["id"]: dict(row)
                for row in self._db.execute(
                    "SELECT * FROM memory_facts WHERE scope=?", (self.scope,)
                )
            }
            for fact_id in current_facts.keys() & remaining_facts.keys():
                # Normal edits are corrections and were preserved above. A
                # replaced snapshot must not reinterpret existing turn_memory
                # references as different facts under the same stable ID.
                if any(
                    current_facts[fact_id][field] != remaining_facts[fact_id][field]
                    for field in ("content", "fact_key", "kind")
                ):
                    raise MemoryInputError("invalid_memory_backup")
            removed_facts = current_facts.keys() - remaining_facts.keys()
            for fact_id in removed_facts:
                self._tombstone("fact", fact_id, revision)
            erased["fact_ids"] = sorted(set(erased["fact_ids"]) | removed_facts)
            if with_evidence:
                erased["fact_contents"] = erased.get("fact_contents", []) + [
                    current_facts[fact_id]["content"] for fact_id in removed_facts
                ]
            self._db.execute(
                "UPDATE memory_scopes SET revision=?,invalidation_revision=?,persona_version=? "
                "WHERE scope=?",
                (revision, revision, persona_version, self.scope),
            )
            self._db.execute(
                "UPDATE memory_jobs SET status='cancelled',lease_token=NULL,"
                "lease_until=NULL WHERE scope=? AND status!='completed'",
                (self.scope,),
            )
            remaining = {
                row[0]
                for row in self._db.execute(
                    "SELECT id FROM memory_sources WHERE scope=?", (self.scope,)
                )
            }
            removed_sources = old_sources - remaining
            for source_id in removed_sources:
                self._tombstone("source", source_id, revision)
            erased["source_ids"] = sorted(set(erased["source_ids"]) | removed_sources)
            if with_evidence:
                erased["source_bodies"] = erased.get("source_bodies", []) + [
                    old_source_bodies[source_id]
                    for source_id in removed_sources
                    if source_id in old_source_bodies
                ]
            return erased

    def close(self):
        with self._guard:
            if not self._closed:
                self._db.close()
                self._closed = True

    def __enter__(self):
        return self

    def __exit__(self, *_):
        self.close()
