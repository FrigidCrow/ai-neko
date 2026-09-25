"""Durable M1 conversation journal, separate from future long-term fact memory.

SQLite transactions are short synchronous critical sections. Cancellation cannot
interrupt settlement mid-transaction; first terminal transition wins. UI polling
marks sent sequences; only explicit acknowledgements mark visible sequences.
"""

from __future__ import annotations

import asyncio
import json
import re
import sqlite3
import time
from threading import RLock
from typing import Any
from uuid import uuid4

from filelock import FileLock, Timeout
from langgraph.checkpoint.sqlite.aio import AsyncSqliteSaver
from langsmith import tracing_context

from ai_neko.chat import build_chat_graph
from ai_neko.config.paths import DataPaths, safe_child

TERMINAL = {"completed", "cancelled", "error", "interrupted"}
ACTIVE = {"accepted", "running"}


class RuntimeAccessError(LookupError):
    """Opaque session/turn does not belong to this application scope."""


class RuntimeConflictError(ValueError):
    """A second active turn or a conflicting retry was requested."""


class RuntimeInputError(ValueError):
    """The public input or delivery receipt is invalid."""


def _identifier(value: Any) -> str:
    if not isinstance(value, str) or not re.fullmatch(r"[a-f0-9]{32}", value):
        raise RuntimeAccessError("会话或回合不存在。")
    return value


class SessionRuntime:
    """Single local user/default persona runtime; no caller-supplied scope or DB paths."""

    def __init__(self, paths: DataPaths, provider_store: Any):
        self.paths = paths
        self.providers = provider_store
        self._guard = RLock()
        self._tasks: dict[str, asyncio.Task] = {}
        self._closed = False
        for folder, name in (
            (paths.memory, "conversation.sqlite"),
            (paths.checkpoints, "chat-graph.sqlite"),
        ):
            for suffix in ("", "-wal", "-shm", "-journal"):
                candidate = safe_child(folder, name + suffix)
                if candidate.exists() and not candidate.is_file():
                    raise RuntimeInputError("会话文件必须是普通文件。")
        lock_path = safe_child(paths.runtime, ".conversation.lock")
        self._file_lock = FileLock(lock_path, timeout=0)
        try:
            self._file_lock.acquire()
        except Timeout as exc:
            raise RuntimeConflictError("会话数据正在使用。") from exc
        try:
            self._db = sqlite3.connect(
                paths.memory / "conversation.sqlite", check_same_thread=False
            )
            self._db.row_factory = sqlite3.Row
            self._db.execute("PRAGMA foreign_keys = ON")
            self._db.execute("PRAGMA journal_mode = WAL")
            self._db.execute("PRAGMA busy_timeout = 3000")
            self._db.executescript("""
                CREATE TABLE IF NOT EXISTS sessions (
                    id TEXT PRIMARY KEY, internal_id TEXT UNIQUE NOT NULL,
                    user_id TEXT NOT NULL, character_id TEXT NOT NULL,
                    title TEXT NOT NULL, created_at REAL NOT NULL, updated_at REAL NOT NULL
                );
                CREATE TABLE IF NOT EXISTS turns (
                    id TEXT PRIMARY KEY, session_id TEXT NOT NULL REFERENCES sessions(id),
                    request_id TEXT, input TEXT NOT NULL, guide INTEGER NOT NULL,
                    status TEXT NOT NULL, created_at REAL NOT NULL, settled_at REAL,
                    sent_seq INTEGER NOT NULL DEFAULT 0, ack_seq INTEGER NOT NULL DEFAULT 0,
                    next_seq INTEGER NOT NULL DEFAULT 1, error TEXT,
                    UNIQUE(session_id, request_id)
                );
                CREATE TABLE IF NOT EXISTS events (
                    turn_id TEXT NOT NULL REFERENCES turns(id), seq INTEGER NOT NULL,
                    payload TEXT NOT NULL, PRIMARY KEY(turn_id, seq)
                );
                CREATE UNIQUE INDEX IF NOT EXISTS one_active_turn
                    ON turns(session_id) WHERE status IN ('accepted', 'running');
            """)
            self._recover()
        except BaseException:
            if hasattr(self, "_db"):
                self._db.close()
            self._file_lock.release()
            raise

    def _check_open(self):
        if self._closed:
            raise RuntimeConflictError("会话服务已关闭。")

    def _session(self, session_id: str):
        self._check_open()
        _identifier(session_id)
        row = self._db.execute(
            "SELECT * FROM sessions WHERE id=? AND user_id='local' AND character_id='default'",
            (session_id,),
        ).fetchone()
        if row is None:
            raise RuntimeAccessError("会话或回合不存在。")
        return row

    def _turn(self, session_id: str, turn_id: str):
        self._session(session_id)
        _identifier(turn_id)
        row = self._db.execute(
            "SELECT * FROM turns WHERE id=? AND session_id=?", (turn_id, session_id)
        ).fetchone()
        if row is None:
            raise RuntimeAccessError("会话或回合不存在。")
        return row

    def _insert_event(self, turn_id: str, event: dict[str, Any]):
        row = self._db.execute("SELECT next_seq FROM turns WHERE id=?", (turn_id,)).fetchone()
        payload = {**event, "seq": row[0], "turn_id": turn_id}
        self._db.execute(
            "INSERT INTO events VALUES (?, ?, ?)",
            (turn_id, row[0], json.dumps(payload, ensure_ascii=False)),
        )
        self._db.execute("UPDATE turns SET next_seq=next_seq+1 WHERE id=?", (turn_id,))

    def _emit(self, session_id: str, turn_id: str, event: dict[str, Any]):
        with self._guard:
            if self._closed:
                return
            row = self._turn(session_id, turn_id)
            if row["status"] not in ACTIVE:
                return  # Invalidate old generation before task cancellation is delivered.
            with self._db:
                self._insert_event(turn_id, event)

    def _settle(self, session_id: str, turn_id: str, status: str, error: str | None = None):
        if status not in TERMINAL:
            raise ValueError("invalid terminal status")
        with self._guard:
            row = self._turn(session_id, turn_id)
            if row["status"] in TERMINAL:
                return self._turn_summary(row)
            with self._db:
                if status != "completed":
                    # Generated but never transmitted content is not a delivered
                    # partial answer. Freeze this boundary before reconnect/ACK.
                    self._db.execute(
                        "DELETE FROM events WHERE turn_id=? AND seq>?",
                        (turn_id, row["sent_seq"]),
                    )
                self._db.execute(
                    "UPDATE turns SET status=?, settled_at=?, error=? WHERE id=? AND status IN ('accepted','running')",
                    (status, time.time(), error, turn_id),
                )
                if error:
                    self._insert_event(
                        turn_id, {"type": "error", "code": error, "message": _error_message(error)}
                    )
                self._insert_event(turn_id, {"type": "done", "status": status})
            return self._turn_summary(self._turn(session_id, turn_id))

    def _recover(self):
        # Constructor holds the exclusive process lock. Never replay network calls.
        rows = self._db.execute(
            "SELECT id,session_id FROM turns WHERE status IN ('accepted','running')"
        ).fetchall()
        for row in rows:
            self._settle(row["session_id"], row["id"], "interrupted", "process_interrupted")

    def create_session(self) -> dict[str, Any]:
        with self._guard:
            self._check_open()
            identifier, internal = uuid4().hex, uuid4().hex
            now = time.time()
            with self._db:
                self._db.execute(
                    "INSERT INTO sessions VALUES (?,?,?,?,?,?,?)",
                    (identifier, internal, "local", "default", "新对话", now, now),
                )
            return self._session_summary(self._session(identifier))

    @staticmethod
    def _session_summary(row) -> dict[str, Any]:
        return {
            "id": row["id"],
            "session_id": row["id"],
            "title": row["title"],
            "created_at": row["created_at"],
            "updated_at": row["updated_at"],
        }

    def list_sessions(self) -> list[dict[str, Any]]:
        with self._guard:
            self._check_open()
            rows = self._db.execute(
                "SELECT * FROM sessions WHERE user_id='local' AND character_id='default' ORDER BY updated_at DESC LIMIT 200"
            ).fetchall()
            return [self._session_summary(row) for row in rows]

    def _payloads(self, turn_id: str, *, through: int | None = None) -> list[dict[str, Any]]:
        sql = "SELECT payload FROM events WHERE turn_id=?"
        args: tuple = (turn_id,)
        if through is not None:
            sql += " AND seq<=?"
            args += (through,)
        return [json.loads(row[0]) for row in self._db.execute(sql + " ORDER BY seq", args)]

    def _turn_summary(self, row) -> dict[str, Any]:
        events = self._payloads(row["id"], through=row["sent_seq"])
        delivered = "".join(e.get("text", "") for e in events if e["type"] == "text")
        confirmed = "".join(
            e.get("text", "") for e in events if e["type"] == "text" and e["seq"] <= row["ack_seq"]
        )
        sources = {}
        for event in events:
            if event["type"] == "source":
                sources[event["source"]["id"]] = event["source"]
        return {
            "id": row["id"],
            "turn_id": row["id"],
            "session_id": row["session_id"],
            "input": row["input"],
            "text": row["input"],
            "guide": bool(row["guide"]),
            "status": row["status"],
            "created_at": row["created_at"],
            "settled_at": row["settled_at"],
            "delivered_text": delivered,
            "assistant_text": delivered,
            "output": delivered,
            "confirmed_text": confirmed,
            "sources": list(sources.values()),
            "ack_seq": row["ack_seq"],
            "sent_seq": row["sent_seq"],
            "last_sequence": row["sent_seq"],
            "last_seq": row["next_seq"] - 1,
            "error": row["error"],
        }

    def get_session(self, session_id: str) -> dict[str, Any]:
        with self._guard:
            session = self._session_summary(self._session(session_id))
            rows = self._db.execute(
                "SELECT * FROM turns WHERE session_id=? ORDER BY created_at,id", (session_id,)
            ).fetchall()
            session["turns"] = [self._turn_summary(row) for row in rows]
            return session

    def _history(self, session_id: str, before: str) -> list[dict[str, str]]:
        rows = self._db.execute(
            "SELECT * FROM turns WHERE session_id=? AND id<>? AND status NOT IN ('accepted','running') ORDER BY created_at DESC,id DESC LIMIT 10",
            (session_id, before),
        ).fetchall()
        history = []
        for row in reversed(rows):
            history.append({"role": "user", "content": _clip(row["input"], 1600)})
            # Confirmed partial prefixes are the only assistant context after interruption.
            through = row["sent_seq"] if row["status"] == "completed" else row["ack_seq"]
            text = "".join(
                e.get("text", "")
                for e in self._payloads(row["id"], through=through)
                if e["type"] == "text"
            )
            if text:
                # Source identifiers belong to one turn. Reusing an old [S1]
                # beside this turn's S1 would silently attribute the old claim
                # to a different page; historical evidence must be searched again.
                text = re.sub(r"\[[Ss]\d+\]", "[历史来源：需重新检索]", text)
                history.append({"role": "assistant", "content": _clip(text, 2400)})
        return history

    async def start_turn(
        self, session_id: str, text: str, guide: bool = False, request_id: str | None = None
    ) -> dict[str, Any]:
        if not isinstance(text, str) or not text.strip() or len(text) > 8000:
            raise RuntimeInputError("请输入 1 到 8000 个字符。")
        if type(guide) is not bool:
            raise RuntimeInputError("查询模式必须为布尔值。")
        if request_id is not None:
            try:
                _identifier(request_id)
            except RuntimeAccessError as exc:
                raise RuntimeInputError("请求编号无效。") from exc
        text = text.strip()
        with self._guard:
            session = self._session(session_id)
            if request_id:
                previous = self._db.execute(
                    "SELECT * FROM turns WHERE session_id=? AND request_id=?",
                    (session_id, request_id),
                ).fetchone()
                if previous:
                    if previous["input"] != text or bool(previous["guide"]) != guide:
                        raise RuntimeConflictError("重试内容与已接受请求不一致。")
                    return self._turn_summary(previous)
            active = self._db.execute(
                "SELECT 1 FROM turns WHERE session_id=? AND status IN ('accepted','running')",
                (session_id,),
            ).fetchone()
            if active:
                raise RuntimeConflictError("这个会话仍在回复，请等待或先停止。")
            turn_id = uuid4().hex
            with self._db:
                self._db.execute(
                    "INSERT INTO turns(id,session_id,request_id,input,guide,status,created_at) VALUES(?,?,?,?,?,'accepted',?)",
                    (turn_id, session_id, request_id, text, int(guide), time.time()),
                )
                self._db.execute(
                    "UPDATE sessions SET title=CASE WHEN title='新对话' THEN ? ELSE title END, updated_at=? WHERE id=?",
                    (text[:50], time.time(), session_id),
                )
                self._insert_event(turn_id, {"type": "status", "status": "accepted"})
            messages = self._history(session_id, turn_id) + [{"role": "user", "content": text}]
            self._tasks[turn_id] = asyncio.create_task(
                self._run(session_id, session["internal_id"], turn_id, messages, guide),
                name=f"ai-neko-turn-{turn_id}",
            )
            self._tasks[turn_id].add_done_callback(lambda task: self._task_done(turn_id, task))
            return self._turn_summary(self._turn(session_id, turn_id))

    def _task_done(self, turn_id: str, task: asyncio.Task):
        self._tasks.pop(turn_id, None)
        if not task.cancelled():
            task.exception()

    async def _run(
        self,
        session_id: str,
        internal_id: str,
        turn_id: str,
        messages: list[dict[str, str]],
        guide: bool,
    ):
        try:
            with self._guard:
                if self._turn(session_id, turn_id)["status"] not in ACTIVE:
                    return
                with self._db:
                    self._db.execute("UPDATE turns SET status='running' WHERE id=?", (turn_id,))
            # Create independent adapters per turn: configuration changes affect future turns.
            model = self.providers.model()
            web_tools = self.providers.web_tools() if guide else None
            async with AsyncSqliteSaver.from_conn_string(
                str(self.paths.checkpoints / "chat-graph.sqlite")
            ) as saver:
                graph = build_chat_graph(
                    model, web_tools, lambda event: self._emit(session_id, turn_id, event), saver
                )
                with tracing_context(enabled=False):
                    await graph.ainvoke(
                        {
                            "messages": messages,
                            "guide": guide,
                            "rounds": 0,
                            "calls": [],
                            "sources": [],
                            "tool_messages": [],
                            "output": "",
                        },
                        config={
                            # Per-turn execution namespaces prevent a late cancelled
                            # node checkpoint from replacing a newer turn's state.
                            # Both components originate in the trusted runtime.
                            "configurable": {"thread_id": f"{internal_id}:{turn_id}"},
                            "callbacks": [],
                            "recursion_limit": 16,
                        },
                    )
            self._settle(session_id, turn_id, "completed")
        except asyncio.CancelledError:
            if not self._closed:
                self._settle(session_id, turn_id, "cancelled")
            raise
        except Exception as exc:
            code = getattr(exc, "code", "generation_failed")
            safe = (
                code
                if isinstance(code, str) and re.fullmatch(r"[a-z_]{1,60}", code)
                else "generation_failed"
            )
            if not self._closed:
                self._settle(session_id, turn_id, "error", safe)
        finally:
            self._tasks.pop(turn_id, None)

    async def cancel_turn(self, session_id: str, turn_id: str) -> dict[str, Any]:
        # Settlement is not part of the cancellable graph task. Invalidate first.
        result = self._settle(session_id, turn_id, "cancelled")
        task = self._tasks.get(turn_id)
        if task is not None and not task.done():
            task.cancel()
        return result

    def events(self, session_id: str, turn_id: str, after: int = 0) -> dict[str, Any]:
        if type(after) is not int or after < 0:
            raise RuntimeInputError("事件序号无效。")
        with self._guard:
            row = self._turn(session_id, turn_id)
            if after > row["next_seq"] - 1:
                raise RuntimeInputError("事件序号超出范围。")
            if after > row["sent_seq"]:
                raise RuntimeInputError("不能跳过尚未发送的事件。")
            events = [
                json.loads(r[0])
                for r in self._db.execute(
                    "SELECT payload FROM events WHERE turn_id=? AND seq>? ORDER BY seq LIMIT 256",
                    (turn_id, after),
                )
            ]
            sent = max(row["sent_seq"], events[-1]["seq"] if events else 0)
            with self._db:
                self._db.execute("UPDATE turns SET sent_seq=? WHERE id=?", (sent, turn_id))
            return {
                "events": events,
                "status": row["status"],
                "last_seq": row["next_seq"] - 1,
                "ack_seq": row["ack_seq"],
                "sent_seq": sent,
                "turn_id": turn_id,
            }

    def ack(self, session_id: str, turn_id: str, sequence: int) -> dict[str, Any]:
        with self._guard:
            row = self._turn(session_id, turn_id)
            if type(sequence) is not int or sequence < 0 or sequence > row["sent_seq"]:
                raise RuntimeInputError("只能确认已发送的事件序号。")
            with self._db:
                self._db.execute(
                    "UPDATE turns SET ack_seq=MAX(ack_seq,?) WHERE id=?", (sequence, turn_id)
                )
            return {"turn_id": turn_id, "ack_seq": max(row["ack_seq"], sequence)}

    async def close(self):
        if self._closed:
            return
        for row in self._db.execute(
            "SELECT id,session_id FROM turns WHERE status IN ('accepted','running')"
        ).fetchall():
            await self.cancel_turn(row["session_id"], row["id"])
        tasks = list(self._tasks.values())
        if tasks:
            done, pending = await asyncio.wait(tasks, timeout=5)
            for task in done:
                if not task.cancelled():
                    task.exception()
            # A non-cooperative adapter cannot write after closing. The real adapters
            # obey cancellation and have bounded HTTP deadlines.
            for task in pending:
                task.cancel()
        with self._guard:
            self._closed = True
            self._db.close()
            self._file_lock.release()


def _clip(text: str, limit: int) -> str:
    return text if len(text) <= limit else text[:limit] + "\n[较早消息已裁剪]"


def _error_message(code: str) -> str:
    known = {
        "process_interrupted": "上次程序异常退出，本轮已停止；已接受的输入与已确认内容保留。",
        "model_key_missing": "请先配置模型 API Key。",
        "search_key_missing": "请先配置搜索 API Key。",
        "model_not_configured": "请先配置模型服务与 API Key。",
        "authentication_failed": "服务鉴权失败，请检查 API Key。",
        "rate_limited": "服务请求受限，请稍后重试。",
        "timeout": "服务请求超时，请重试。",
        "network_error": "无法连接服务，请检查网络。",
        "blocked_endpoint": "服务地址不可访问或不符合网络策略。",
        "output_limit": "模型输出达到长度上限，请缩小问题范围。",
        "stream_interrupted": "模型连接中断，请重试。",
        "invalid_response": "模型返回格式不受支持，请检查兼容设置。",
        "provider_http_error": "服务返回错误，请检查配置或稍后重试。",
    }
    return known.get(code, "本轮未完成。请检查服务配置或网络后重试。")
