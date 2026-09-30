"""Explicit match boundaries in the existing Runtime; no additional agent loop."""

from __future__ import annotations

import asyncio
import hashlib
import json
import re
import time
from contextlib import contextmanager

from ai_neko.chat.vision import ensure_image_fresh
from ai_neko.memory.script_fold import fold_script
from ai_neko.runtime.lifecycle import _settled_mutation
from ai_neko.runtime.match_store import (
    MatchAccessError,
    MatchConflictError,
    MatchInputError,
    MatchStore,
    conversation_matches_v5,
)


def conversation_match_bindings_v5(db):
    conversation_matches_v5(db)
    db.execute(
        "CREATE TABLE IF NOT EXISTS turn_matches (turn_id TEXT PRIMARY KEY, "
        "session_id TEXT NOT NULL, match_id TEXT, payload TEXT NOT NULL)"
    )
    db.execute("CREATE INDEX IF NOT EXISTS turn_matches_match ON turn_matches(match_id)")
    db.execute(
        "CREATE TABLE IF NOT EXISTS match_settlements (session_id TEXT PRIMARY KEY, "
        "revision INTEGER NOT NULL, voice_ids TEXT NOT NULL)"
    )
    db.execute(
        "CREATE TABLE IF NOT EXISTS match_goal_evidence (match_id TEXT NOT NULL, "
        "goal_hash TEXT NOT NULL, goal TEXT NOT NULL, PRIMARY KEY(match_id,goal_hash))"
    )
    db.execute(
        "CREATE TABLE IF NOT EXISTS turn_match_goals (turn_id TEXT NOT NULL, "
        "match_id TEXT NOT NULL, goal_hash TEXT NOT NULL, PRIMARY KEY(turn_id,match_id,goal_hash))"
    )


@contextmanager
def match_errors():
    from ai_neko.runtime.service import (
        RuntimeAccessError,
        RuntimeConflictError,
        RuntimeInputError,
    )

    try:
        yield
    except MatchAccessError as exc:
        raise RuntimeAccessError("对局不存在。") from exc
    except MatchConflictError as exc:
        raise RuntimeConflictError("对局已更新，请刷新后重试。") from exc
    except MatchInputError as exc:
        raise RuntimeInputError("对局参数无效。") from exc


def user_description(text):
    """Retain literal declarative clauses; questions are not confirmed game facts.

    This is a conservative convenience for ordinary input. The authenticated
    observation action can explicitly confirm any exact quote of a user turn.
    No numeric/field interpretation or assertion of completed play is invented.
    """
    if re.search(r"如果|假如|假设|要是|\bif\b|suppos", text, re.I):
        return ""
    first = last = None
    for part in re.finditer(r"[^，,。！？!?\n]+", text):
        clause = part.group().strip()
        question = (
            re.search(
                r"如果|假如|假设|要是|是否|是不是|应该|能否|吗|什么|怎么|如何|多少|为何|下一步|\bif\b",
                clause,
                re.I,
            )
            or part.end() < len(text)
            and text[part.end()] in "？?"
        )
        declarative = re.search(
            r"^(?:我(?:们)?(?:现在|目前|刚刚|已经|还没|有|是|在|剩|确认)|"
            r"现在|目前|当前|刚刚|已经|还没|金币|血量|人口|回合|棋盘|"
            r"I (?:have|am|just|already|confirm)|we (?:have|are)|currently)|已经|尚未|还没",
            clause,
            re.I,
        )
        if clause and declarative and not question:
            if first is None:
                first = part.start()
            last = part.end()
        elif first is not None:
            break
    if first is None:
        return ""
    if last < len(text) and text[last] == "。":
        last += 1
    return text[first:last].strip()[:2000]


def _validate_match_binding_shape(supplied, *, allow_none=False):
    """Validate request syntax without checking any current match state."""
    from ai_neko.runtime.service import RuntimeInputError

    if supplied is None and allow_none:
        return
    if (
        not isinstance(supplied, dict)
        or set(supplied) != {"match_id", "expected_revision"}
        or type(supplied.get("expected_revision")) is not int
        or supplied["expected_revision"] < 0
        or supplied.get("match_id") is not None
        and (
            not isinstance(supplied["match_id"], str)
            or not re.fullmatch(r"match-[a-f0-9]{32}", supplied["match_id"])
        )
    ):
        raise RuntimeInputError("请附上当前对局编号与修订。")


class MatchRuntimeMixin:
    def _initialize_match_runtime(self):
        self.matches = MatchStore(self._db, self.memory.scope)
        self._match_mutating_sessions = set()
        self.matches.restart()

    def _binding(self, turn_id):
        row = self._db.execute(
            "SELECT payload FROM turn_matches WHERE turn_id=?", (turn_id,)
        ).fetchone()
        return json.loads(row[0]) if row else None

    def _match_snapshot(self, session_id, supplied):
        from ai_neko.runtime.service import RuntimeConflictError

        self._session(session_id)
        if (
            session_id in self._match_mutating_sessions
            or self._db.execute(
                "SELECT 1 FROM match_settlements WHERE session_id=?", (session_id,)
            ).fetchone()
        ):
            raise RuntimeConflictError("对局正在更新。")
        with match_errors():
            revision = self.matches.revision(session_id)
            current = self.matches.current(session_id)
        if supplied is None and revision == 0:
            supplied = {"match_id": None, "expected_revision": 0}
        _validate_match_binding_shape(supplied)
        if supplied["expected_revision"] != revision or supplied["match_id"] != (
            current["match_id"] if current else None
        ):
            raise RuntimeConflictError("对局已更新，旧请求不能用于当前局。")
        return {
            **supplied,
            "state_revision": current["state_revision"] if current else revision,
            "selection": current.get("selection") if current else None,
        }

    def validate_match_request(self, session_id, match):
        with self._guard:
            self._check_memory_available()
            return self._match_snapshot(session_id, match)

    def _validate_match_retry(self, turn_id, supplied, origin, review_match_id):
        from ai_neko.runtime.service import RuntimeConflictError

        # Old accepted receipts remain readable after later match changes, but
        # Python's bool/float equality must not bypass the original input schema.
        _validate_match_binding_shape(supplied, allow_none=True)
        binding = self._binding(turn_id)
        original = binding.get("request_binding") if binding else None
        if (
            supplied != original
            or origin != (binding.get("input_origin", "text") if binding else "text")
            or review_match_id != (binding.get("review_match_id") if binding else None)
        ):
            raise RuntimeConflictError("重试对局归属与已接受请求不一致。")

    def _accept_match_turn(self, session_id, turn_id, snapshot, supplied, origin, review, text):
        # Called inside the turn acceptance transaction after its turn row exists.
        binding = {
            **snapshot,
            "request_binding": supplied,
            "input_origin": origin,
            "review_match_id": review,
            "accepted_at": time.time(),
        }
        match_id = snapshot["match_id"]
        description = user_description(text) if match_id and not review else ""
        if description:
            with match_errors():
                revision = self.matches.add_observation(
                    session_id,
                    match_id,
                    turn_id,
                    description,
                    source_kind="user_description",
                    commit=False,
                )
            binding["expected_revision"] = binding["state_revision"] = revision
        self._db.execute(
            "INSERT INTO turn_matches VALUES (?,?,?,?)",
            (turn_id, session_id, match_id, json.dumps(binding)),
        )
        return binding

    def _assert_match_turn(self, session_id, turn_id):
        binding = self._binding(turn_id)
        if not binding:
            return
        if session_id in self._match_mutating_sessions:
            raise asyncio.CancelledError
        with match_errors():
            if binding["expected_revision"] != self.matches.revision(session_id):
                raise asyncio.CancelledError
            current = self.matches.current(session_id)
        if binding["match_id"] != (current["match_id"] if current else None):
            raise asyncio.CancelledError

    async def match_catalog(self, session_id):
        with self._guard, match_errors():
            self._check_memory_available()
            self._session(session_id)
            return self.matches.catalog(session_id)

    async def match_detail(self, session_id, match_id):
        with self._guard, match_errors():
            self._check_memory_available()
            self._session(session_id)
            return self.matches.read_context(session_id, match_id)

    def _matching_selection(self, fields, snapshot):
        def normalized(value):
            return fold_script(value.strip()).casefold() if isinstance(value, str) else ""

        candidates = [
            item
            for item in snapshot["selections"]
            if all(
                normalized(item[key]) == normalized(fields[key])
                for key in ("game", "platform", "mode")
            )
        ]
        return candidates[0] if len(candidates) == 1 else None

    @_settled_mutation
    async def match_control(self, session_id, action, value, *, _before_commit=None):
        from ai_neko.runtime.service import RuntimeInputError

        async with self._mutation_lock:
            await self._recover_pending_guide_control()
            self._check_memory_available()
            self._session(session_id)
            await self._finish_match_settlement(session_id)
            with match_errors():
                replay = self.matches.preflight(session_id, action, value)
            if replay:
                return replay
            if _before_commit is not None:
                _before_commit()
            selection = None
            if action in {"start", "new"}:
                snapshot = await self._memory_call(self.memory.guides.control_snapshot)
                try:
                    selection = self._matching_selection(value, snapshot)
                except (KeyError, TypeError):
                    raise RuntimeInputError("缺少游戏条件。") from None
            if action == "observe":
                turn = self._turn(session_id, value.get("turn_id"))
                binding = self._binding(turn["id"])
                if (
                    not binding
                    or binding["match_id"] != value.get("match_id")
                    or not isinstance(value.get("text"), str)
                    or not value["text"].strip()
                    or value["text"] not in turn["input"]
                ):
                    raise RuntimeInputError("观察必须引用本局用户原话。")
            with self._guard, self._db, match_errors():
                # Start an outer transaction so a committed match change always
                # has its durable cancellation receipt before either is visible.
                self._db.execute("UPDATE sessions SET title=title WHERE id=?", (session_id,))
                result = self.matches.control(
                    session_id, action, value, selection=selection, commit=False
                )
                if result.get("replayed"):
                    return result
                self._db.execute(
                    "INSERT OR REPLACE INTO match_settlements VALUES (?,?,?)",
                    (session_id, result["revision"], json.dumps(list(self.voice_tasks))),
                )
            await self._finish_match_settlement(session_id)
            return result

    async def _finish_match_settlement(self, session_id):
        pending = self._db.execute(
            "SELECT revision,voice_ids FROM match_settlements WHERE session_id=?", (session_id,)
        ).fetchone()
        if not pending:
            return
        self._match_mutating_sessions.add(session_id)
        try:
            await self._stop_match_work(session_id, pending[0], json.loads(pending[1]))
            with self._db:
                self._db.execute("DELETE FROM match_settlements WHERE session_id=?", (session_id,))
        finally:
            self._match_mutating_sessions.discard(session_id)

    async def _stop_match_work(self, session_id, revision, voice_ids):
        tasks = []
        for row in self._db.execute(
            "SELECT id FROM turns WHERE session_id=? AND status IN ('accepted','running')",
            (session_id,),
        ).fetchall():
            if (self._binding(row[0]) or {}).get("expected_revision", 0) >= revision:
                continue
            task = self._tasks.get(row[0])
            if task:
                tasks.append(task)
            await self.cancel_turn(session_id, row[0], _internal=True)
        # A previous attempt may have persisted terminal status before failing
        # to drain the task. Include those live owners during durable recovery.
        for turn_id, task in list(self._tasks.items()):
            row = self._db.execute("SELECT session_id FROM turns WHERE id=?", (turn_id,)).fetchone()
            if (
                row
                and row[0] == session_id
                and (self._binding(turn_id) or {}).get("expected_revision", 0) < revision
                and not task.done()
            ):
                if not task.cancelling():
                    task.cancel()
                if task not in tasks:
                    tasks.append(task)
        # Voice jobs have no per-session registry before G5; settle them
        # conservatively, while turn generation in other sessions stays intact.
        voices = [self.voice_tasks[key] for key in voice_ids if key in self.voice_tasks]
        for task in voices:
            task.cancel()
        if tasks or voices:
            await asyncio.gather(*tasks, *voices, return_exceptions=True)
        with self._db:
            self._db.execute(
                "UPDATE audio_playback SET state='interrupted' WHERE state='started' "
                "AND turn_id IN (SELECT id FROM turns WHERE session_id=?)",
                (session_id,),
            )

    async def _record_match_frame(self, session_id, turn_id, image, fields):
        self._assert_guide_turn(session_id, turn_id)
        ensure_image_fresh(image)
        binding = self._binding(turn_id)
        if not binding or not binding["match_id"] or binding.get("review_match_id"):
            raise asyncio.CancelledError
        # The graph has strictly validated only visible fields of this image.
        values = {item["name"]: item["value"] for item in fields}
        text = "；".join(
            f"{name}：{value if value is not None else '未知'}" for name, value in values.items()
        )
        with self._guard, self._db, match_errors():
            self._assert_guide_turn(session_id, turn_id)
            # Open a transaction before the store's non-committing mutation.
            self._db.execute("UPDATE turn_matches SET payload=payload WHERE turn_id=?", (turn_id,))
            revision = self.matches.add_observation(
                session_id,
                binding["match_id"],
                turn_id,
                text,
                source_kind="vision",
                observed_at=image["captured_at"],
                frame_id=image["frame_id"],
                fields=values,
                commit=False,
            )
            binding["expected_revision"] = binding["state_revision"] = revision
            self._db.execute(
                "UPDATE turn_matches SET payload=? WHERE turn_id=?", (json.dumps(binding), turn_id)
            )

    def _match_context_for_turn(self, session_id, turn_id):
        self._assert_match_turn(session_id, turn_id)
        binding = self._binding(turn_id)
        if not binding:
            return None
        review = binding.get("review_match_id")
        if review:
            with match_errors():
                historical = self.matches.get(session_id, review)
            self._record_goal_dependency(turn_id, historical)
            return {
                **{
                    key: historical[key]
                    for key in ("match_id", "game", "platform", "mode", "game_version", "goal")
                },
                "status": "historical",
                "history_only": True,
                "observations": [],
                "notice": "用户明确复盘的历史对局；这些内容不是当前局势，不证明已执行建议。",
            }
        if not binding["match_id"]:
            return None
        with match_errors():
            context = self.matches.read_context(session_id, binding["match_id"])
        self._record_goal_dependency(turn_id, context)
        with self._db:
            for observation in context.get("observations", []):
                if observation["turn_id"] != turn_id:
                    self._db.execute(
                        "INSERT OR IGNORE INTO turn_user_history VALUES (?,?)",
                        (turn_id, observation["turn_id"]),
                    )
        context["last_delivered_advice"] = self._last_match_advice(session_id, turn_id, context)
        context["notice"] = "观察是有来源和时效的资料；用户提问不是事实，模型建议不代表用户执行。"
        return context

    def _guide_context_for_turn(self, session_id, turn_id):
        context = self._match_context_for_turn(session_id, turn_id)
        if context is None:
            return None
        if context.get("history_only"):
            return context
        return {
            **context,
            "observations": [o for o in context.get("observations", []) if not o.get("unknown")],
        }

    def _last_match_advice(self, session_id, turn_id, context):
        from ai_neko.runtime.service import _speech_text

        if context["status"] != "active":
            return None
        after = max(context.get("evidence_after", 0), time.time() - 120)
        rows = self._db.execute(
            "SELECT t.* FROM turns t JOIN turn_matches m ON t.id=m.turn_id "
            "WHERE t.session_id=? AND m.match_id=? AND t.id<>? AND t.created_at>=? "
            "AND t.status NOT IN ('accepted','running') ORDER BY t.created_at DESC,t.id DESC LIMIT 10",
            (session_id, context["match_id"], turn_id, after),
        ).fetchall()
        selected = context.get("selection")
        for row in rows:
            binding = self._binding(row["id"])
            if (
                binding.get("review_match_id")
                or binding.get("control_response")
                or binding.get("selection") != selected
            ):
                continue
            if self._db.execute(
                "SELECT 1 FROM turn_guides WHERE turn_id=? AND invalidated=1", (row["id"],)
            ).fetchone():
                continue
            summary = self._turn_summary(row)
            if binding.get("input_origin") == "voice":
                text = _speech_text(summary["delivered_text"][: summary["heard_characters"]])
                delivery = "heard"
            else:
                text, delivery = summary["confirmed_text"], "shown"
            if not text.strip():
                continue
            # Dependencies include this actual advice consumer, not all old turns.
            with self._db:
                self._db.execute(
                    "INSERT OR IGNORE INTO turn_history VALUES (?,?)", (turn_id, row["id"])
                )
                self._db.execute(
                    "INSERT OR IGNORE INTO turn_guides SELECT ?,guide_id,revision_id,0 FROM turn_guides WHERE turn_id=? AND invalidated=0",
                    (turn_id, row["id"]),
                )
            return {
                "match_id": context["match_id"],
                "turn_id": row["id"],
                "text": re.sub(r"\[[Ss]\d+\]", "[历史来源：需重新检索]", text[:2400]),
                "delivered": True,
                "delivery": delivery,
                "executed": False,
                "delivered_at": binding.get("delivered_at", row["settled_at"] or row["created_at"]),
                **({key: selected[key] for key in ("guide_id", "revision_id")} if selected else {}),
            }
        return None

    def _match_history_mode(self, session_id, turn_id, row):
        """Past dynamic messages only enter an explicit historical review."""
        binding = self._binding(turn_id)
        prior = self._binding(row["id"])
        if prior and prior.get("control_response"):
            return False
        if binding and binding.get("review_match_id"):
            return bool(prior and prior.get("match_id") == binding["review_match_id"])
        if binding and binding.get("match_id"):
            # Current dynamic evidence and delivered advice have explicit times
            # and origins in match_context. Raw ten-turn replay would bypass TTL.
            return False
        return not (prior and (prior.get("match_id") or prior.get("review_match_id")))

    def _record_match_delivery(self, turn_id):
        binding = self._binding(turn_id)
        if binding:
            binding["delivered_at"] = time.time()
            self._db.execute(
                "UPDATE turn_matches SET payload=? WHERE turn_id=?",
                (json.dumps(binding), turn_id),
            )

    def _record_goal_dependency(self, turn_id, context):
        goal = context.get("goal")
        if goal:
            digest = hashlib.sha256(goal.encode()).hexdigest()
            with self._db:
                self._db.execute(
                    "INSERT OR IGNORE INTO match_goal_evidence VALUES (?,?,?)",
                    (context["match_id"], digest, goal),
                )
                self._db.execute(
                    "INSERT OR IGNORE INTO turn_match_goals VALUES (?,?,?)",
                    (turn_id, context["match_id"], digest),
                )

    def _sync_match_selections(self, database):
        store = MatchStore(database, self.memory.scope)
        snapshot = self.memory.guides.control_snapshot()
        for session in database.execute(
            "SELECT id FROM sessions WHERE user_id=? AND character_id=?",
            json.loads(self.memory.scope),
        ).fetchall():
            current = store.current(session[0])
            if current:
                store.sync_selection(session[0], self._matching_selection(current, snapshot))
