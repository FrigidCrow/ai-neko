"""File-backed graph state, intentionally separate from long-term memory.

Callers must supply a validated project-owned checkpoint directory and a trusted
scope. Scope is an authorization boundary, not an identity authenticator. This
synchronous M0 service is driven serially under the application's instance lock.
"""

from __future__ import annotations

import sqlite3
from contextlib import ExitStack
from dataclasses import dataclass
from pathlib import Path
from threading import RLock
from typing import Any, Callable, Literal, TypedDict
from uuid import uuid4

from filelock import FileLock, Timeout
from langgraph.checkpoint.sqlite import SqliteSaver
from langgraph.config import get_stream_writer
from langgraph.graph import END, START, StateGraph
from langgraph.types import Command, StateSnapshot, interrupt
from langsmith import tracing_context

from ai_neko.config.paths import is_redirected, safe_child


class ScopeAccessError(PermissionError):
    """The requested handle/checkpoint is not available in the supplied scope."""


class GraphStateError(RuntimeError):
    """The requested operation conflicts with the thread's current state."""


@dataclass(frozen=True)
class Scope:
    user_id: str
    character_id: str
    external_thread_id: str

    def __post_init__(self) -> None:
        for value in (self.user_id, self.character_id, self.external_thread_id):
            if not isinstance(value, str) or not value.strip() or len(value) > 256:
                raise ValueError("Scope fields must be nonempty strings up to 256 characters")
            if any(ord(char) < 32 for char in value):
                raise ValueError("Scope fields cannot contain control characters")


class DemoState(TypedDict):
    text: str
    pause: bool
    normalized: str
    response: str
    status: str


def _prepare(state: DemoState) -> dict[str, Any]:
    normalized = state["text"].strip()
    get_stream_writer()({"type": "prepared", "characters": len(normalized)})
    return {
        "normalized": normalized,
        "response": "",
        "status": "prepared" if normalized else "empty",
    }


def _route(state: DemoState) -> Literal["respond", "__end__"]:
    return "respond" if state["normalized"] else END


def _respond(state: DemoState) -> dict[str, str]:
    # An interrupted node restarts here on resume. No external side effects run
    # before interrupt, and emitted events are observations, not delivery receipts.
    suffix = ""
    if state["pause"]:
        answer = interrupt({"kind": "synthetic_confirmation", "text": state["normalized"]})
        suffix = f" | resumed: {answer}"
    response = f"synthetic: {state['normalized']}{suffix}"
    writer = get_stream_writer()
    for offset in range(0, len(response), 8):
        writer({"type": "text_delta", "text": response[offset : offset + 8]})
    writer({"type": "generation_done"})
    return {"response": response, "status": "complete"}


def _build_graph(checkpointer: SqliteSaver) -> Any:
    builder = StateGraph(DemoState)
    builder.add_node("prepare", _prepare)
    builder.add_node("respond", _respond)
    builder.add_edge(START, "prepare")
    builder.add_conditional_edges("prepare", _route)
    builder.add_edge("respond", END)
    return builder.compile(checkpointer=checkpointer)


class GraphService:
    """M0 synchronous graph access with ownership checked before every operation.

    ``checkpoint_dir`` must already exist after application data-root validation.
    No directory or database is created until entering this context manager.
    Handles are internal UUIDs bound durably to all three scope fields. This API
    never accepts a caller-supplied LangGraph config or checkpoint namespace.
    """

    def __init__(self, checkpoint_dir: Path):
        self.checkpoint_dir = Path(checkpoint_dir)
        self._stack: ExitStack | None = None
        self._lock = RLock()

    def __enter__(self) -> GraphService:
        if self._stack is not None:
            raise GraphStateError("GraphService is already open")
        if (
            not self.checkpoint_dir.is_absolute()
            or not self.checkpoint_dir.is_dir()
            or is_redirected(self.checkpoint_dir)
        ):
            raise ValueError("A pre-existing absolute checkpoint directory is required")
        # SQLite may open auxiliary files itself; reject existing redirects before
        # either connection is opened, including its WAL/journal sidecars.
        for name in (".graph.lock", "scopes.sqlite", "graph.sqlite"):
            suffixes = ("",) if name == ".graph.lock" else ("", "-wal", "-shm", "-journal")
            for suffix in suffixes:
                managed = safe_child(self.checkpoint_dir, name + suffix)
                if managed.exists() and not managed.is_file():
                    raise ValueError("Managed graph files must be regular files")
        stack = ExitStack()
        try:
            try:
                stack.enter_context(FileLock(self.checkpoint_dir / ".graph.lock", timeout=0))
            except Timeout as exc:
                raise GraphStateError("Checkpoint directory is already in use") from exc
            self._scopes = sqlite3.connect(
                self.checkpoint_dir / "scopes.sqlite", check_same_thread=False
            )
            stack.callback(self._scopes.close)
            self._scopes.execute(
                """CREATE TABLE IF NOT EXISTS thread_scopes (
                    internal_thread_id TEXT PRIMARY KEY,
                    user_id TEXT NOT NULL,
                    character_id TEXT NOT NULL,
                    external_thread_id TEXT NOT NULL,
                    UNIQUE (user_id, character_id, external_thread_id)
                )"""
            )
            self._scopes.commit()
            self._saver = stack.enter_context(
                SqliteSaver.from_conn_string(str(self.checkpoint_dir / "graph.sqlite"))
            )
            self._graph = _build_graph(self._saver)
        except BaseException:
            stack.close()
            raise
        self._stack = stack
        return self

    def __exit__(self, *exc: Any) -> None:
        with self._lock:
            if self._stack is not None:
                self._stack.close()
                self._stack = None

    def _check_open(self) -> None:
        if self._stack is None:
            raise GraphStateError("Use GraphService inside a with block")

    @staticmethod
    def _config(thread_id: str, checkpoint_id: str | None = None) -> dict[str, Any]:
        configurable = {"thread_id": thread_id, "checkpoint_ns": ""}
        if checkpoint_id is not None:
            configurable["checkpoint_id"] = checkpoint_id
        return {"configurable": configurable, "callbacks": []}

    def open_thread(self, scope: Scope, *, create: bool = True) -> str:
        """Return/create this trusted scope's opaque handle without running a node."""
        with self._lock:
            self._check_open()
            if not isinstance(scope, Scope):
                raise TypeError("scope must be a Scope")
            values = (scope.user_id, scope.character_id, scope.external_thread_id)
            with self._scopes:
                if create:
                    self._scopes.execute(
                        "INSERT OR IGNORE INTO thread_scopes VALUES (?, ?, ?, ?)",
                        (str(uuid4()), *values),
                    )
                row = self._scopes.execute(
                    """SELECT internal_thread_id FROM thread_scopes
                       WHERE user_id = ? AND character_id = ? AND external_thread_id = ?""",
                    values,
                ).fetchone()
            if row is None:
                raise ScopeAccessError("Thread or checkpoint is unavailable in this scope")
            return row[0]

    def _authorize(self, scope: Scope, thread_id: str, checkpoint_id: str | None) -> None:
        self._check_open()
        if not isinstance(scope, Scope) or not isinstance(thread_id, str):
            raise ScopeAccessError("Thread or checkpoint is unavailable in this scope")
        row = self._scopes.execute(
            """SELECT 1 FROM thread_scopes WHERE internal_thread_id = ?
               AND user_id = ? AND character_id = ? AND external_thread_id = ?""",
            (thread_id, scope.user_id, scope.character_id, scope.external_thread_id),
        ).fetchone()
        if row is None:
            raise ScopeAccessError("Thread or checkpoint is unavailable in this scope")
        if checkpoint_id is not None:
            if not isinstance(checkpoint_id, str) or not checkpoint_id:
                raise ScopeAccessError("Thread or checkpoint is unavailable in this scope")
            if self._saver.get_tuple(self._config(thread_id, checkpoint_id)) is None:
                raise ScopeAccessError("Thread or checkpoint is unavailable in this scope")

    @staticmethod
    def _snapshot(snapshot: StateSnapshot, thread_id: str) -> dict[str, Any]:
        interrupts = [
            {"id": item.id, "value": item.value}
            for task in snapshot.tasks
            for item in task.interrupts
        ]
        return {
            "thread_id": thread_id,
            "checkpoint_id": snapshot.config.get("configurable", {}).get("checkpoint_id"),
            "values": snapshot.values,
            "next": list(snapshot.next),
            "interrupts": interrupts,
        }

    def get(self, scope: Scope, thread_id: str, *, checkpoint_id: str | None = None) -> dict:
        with self._lock, tracing_context(enabled=False):
            self._authorize(scope, thread_id, checkpoint_id)
            return self._snapshot(
                self._graph.get_state(self._config(thread_id, checkpoint_id)), thread_id
            )

    def history(
        self, scope: Scope, thread_id: str, *, checkpoint_id: str | None = None, limit: int = 50
    ) -> list[dict]:
        """Newest first; checkpoint_id, when provided, is an exclusive before cursor."""
        with self._lock, tracing_context(enabled=False):
            self._authorize(scope, thread_id, checkpoint_id)
            if type(limit) is not int or not 1 <= limit <= 1000:
                raise ValueError("history limit must be an integer between 1 and 1000")
            before = self._config(thread_id, checkpoint_id) if checkpoint_id else None
            return [
                self._snapshot(snapshot, thread_id)
                for snapshot in self._graph.get_state_history(
                    self._config(thread_id), before=before, limit=limit
                )
            ]

    def _current(self, thread_id: str, checkpoint_id: str | None) -> dict:
        current = self._snapshot(self._graph.get_state(self._config(thread_id)), thread_id)
        if checkpoint_id is not None and current["checkpoint_id"] != checkpoint_id:
            raise GraphStateError("Checkpoint is no longer current")
        return current

    def _execute(
        self, thread_id: str, payload: dict | Command, on_event: Callable[[dict], None] | None
    ) -> dict:
        events = []
        # Tracing is disabled around every public graph call even if a developer's
        # shell has global LangSmith settings. M0 sends no graph content to a cloud.
        for event in self._graph.stream(payload, self._config(thread_id), stream_mode="custom"):
            events.append(event)
            if on_event is not None:
                on_event(event)
        result = self._current(thread_id, None)
        result["events"] = events
        return result

    def run(
        self,
        scope: Scope,
        thread_id: str,
        text: str,
        *,
        pause: bool = False,
        checkpoint_id: str | None = None,
        on_event: Callable[[dict], None] | None = None,
    ) -> dict:
        with self._lock, tracing_context(enabled=False):
            self._authorize(scope, thread_id, checkpoint_id)
            if not isinstance(text, str) or len(text) > 4096 or type(pause) is not bool:
                raise ValueError("text must be at most 4096 characters and pause must be boolean")
            if self._current(thread_id, checkpoint_id)["next"]:
                raise GraphStateError("Thread has pending work; use resume or delete it")
            return self._execute(
                thread_id,
                {"text": text, "pause": pause, "normalized": "", "response": "", "status": "new"},
                on_event,
            )

    def resume(
        self,
        scope: Scope,
        thread_id: str,
        response: str,
        *,
        checkpoint_id: str | None = None,
        on_event: Callable[[dict], None] | None = None,
    ) -> dict:
        with self._lock, tracing_context(enabled=False):
            self._authorize(scope, thread_id, checkpoint_id)
            if not isinstance(response, str) or len(response) > 4096:
                raise ValueError("response must be a string of at most 4096 characters")
            if not self._current(thread_id, checkpoint_id)["interrupts"]:
                raise GraphStateError("Thread is not waiting for a resume value")
            return self._execute(thread_id, Command(resume=response), on_event)

    def delete(self, scope: Scope, thread_id: str, *, checkpoint_id: str | None = None) -> None:
        """Delete owned checkpoints first, then revoke the handle; safe to retry after a crash."""
        with self._lock, tracing_context(enabled=False):
            self._authorize(scope, thread_id, checkpoint_id)
            self._current(thread_id, checkpoint_id)
            self._saver.delete_thread(thread_id)
            with self._scopes:
                self._scopes.execute(
                    "DELETE FROM thread_scopes WHERE internal_thread_id = ?", (thread_id,)
                )
