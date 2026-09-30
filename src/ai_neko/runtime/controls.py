"""Durable explicit control jobs, dispatched only after their chat graph exits.

The existing guide/match methods remain the only mutation authority. Jobs are
not chat tasks and confirmations use the ordinary event/ACK/audio journal.
"""

from __future__ import annotations

import asyncio
import json
import re
import time
from uuid import uuid4

from ai_neko.memory.script_fold import fold_script


def conversation_controls_v6(db):
    db.execute(
        "CREATE TABLE IF NOT EXISTS control_jobs (id TEXT PRIMARY KEY,session_id TEXT NOT NULL,"
        "turn_id TEXT NOT NULL UNIQUE,action TEXT NOT NULL,payload TEXT NOT NULL,"
        "status TEXT NOT NULL,cancel_requested INTEGER NOT NULL DEFAULT 0,result TEXT,"
        "error TEXT,response_turn_id TEXT NOT NULL UNIQUE,created_at REAL NOT NULL,updated_at REAL NOT NULL)"
    )
    db.execute("CREATE INDEX IF NOT EXISTS control_jobs_session ON control_jobs(session_id)")


def validate_guide_target(value):
    from ai_neko.runtime.service import RuntimeInputError

    if value is None:
        return None
    if not isinstance(value, dict) or set(value) != {
        "guide_id",
        "revision_id",
        "game",
        "platform",
        "mode",
        "expected_revision",
    }:
        raise RuntimeInputError("所选攻略参数无效。")
    for key, prefix in (("guide_id", "guide-"), ("revision_id", "revision-")):
        if not isinstance(value[key], str) or not re.fullmatch(
            prefix + r"[a-f0-9]{32}", value[key]
        ):
            raise RuntimeInputError("所选攻略编号无效。")
    if type(value["expected_revision"]) is not int or value["expected_revision"] < 0:
        raise RuntimeInputError("所选攻略修订无效。")
    for key in ("game", "platform", "mode"):
        item = value[key]
        if (
            not isinstance(item, str)
            or not 1 <= len(item) <= 200
            or item != item.strip()
            or any(ord(c) < 32 or ord(c) == 127 or 0xD800 <= ord(c) <= 0xDFFF for c in item)
        ):
            raise RuntimeInputError("请明确游戏、平台和模式。")
    return dict(value)


class ControlRuntimeMixin:
    def _initialize_control_runtime(self):
        self._control_tasks = {}
        self._control_boot_time = time.time()

    def _control_for_turn(self, turn_id):
        return self._db.execute(
            "SELECT * FROM control_jobs WHERE turn_id=? OR response_turn_id=?", (turn_id, turn_id)
        ).fetchone()

    def _guide_target_for_turn(self, turn_id):
        row = self._db.execute(
            "SELECT payload FROM turn_metadata WHERE turn_id=?", (turn_id,)
        ).fetchone()
        return json.loads(row[0]).get("guide_target") if row else None

    def _validate_control_retry(self, turn_id, target):
        from ai_neko.runtime.service import RuntimeConflictError

        if target != self._guide_target_for_turn(turn_id):
            raise RuntimeConflictError("重试所选攻略与原请求不一致。")

    def _resolve_control_intent(self, session_id, turn_id, action):
        self._assert_guide_turn(session_id, turn_id)
        binding = self._binding(turn_id)
        if binding.get("review_match_id"):
            return {"clarification": "历史复盘不能更改当前对局，请回到当前对局后明确操作。"}
        current = self.matches.current(session_id)
        if action == "new_match":
            if current is None:
                return {"clarification": "请先选择游戏、平台和模式并开始陪玩，再开始新一局。"}
            value = {
                "match_id": current["match_id"],
                "expected_revision": binding["expected_revision"],
                **{key: current[key] for key in ("game", "platform", "mode", "game_version")},
                "goal": "",
            }
        else:
            target = self._guide_target_for_turn(turn_id)
            if target is None:
                return {"clarification": "请先选中一份攻略，明确要采用或切换哪一份。"}
            if current and any(
                fold_script(current[key]).casefold() != fold_script(target[key]).casefold()
                for key in ("game", "platform", "mode")
            ):
                return {"clarification": "所选攻略的游戏、平台或模式与当前局不一致，请先确认条件。"}
            value = target
        return {
            "action": action,
            "value": value,
            "match": {key: binding[key] for key in ("match_id", "expected_revision")},
            "guide_revision": self._guide_turn_revisions[turn_id],
        }

    def _queue_control(self, session_id, turn_id, intent):
        identifier, response = uuid4().hex, uuid4().hex
        payload = {**intent, "value": {**intent["value"], "request_id": identifier}}
        now = time.time()
        with self._db:
            self._db.execute(
                "INSERT INTO control_jobs VALUES (?,?,?,?,?,'pending',0,NULL,NULL,?,?,?)",
                (
                    identifier,
                    session_id,
                    turn_id,
                    intent["action"],
                    json.dumps(payload),
                    response,
                    now,
                    now,
                ),
            )
            self._insert_event(
                turn_id,
                {"type": "status", "status": "control_pending", "control_job_id": identifier},
            )
        return identifier

    def _launch_control(self, identifier):
        if self._closing or self._closed or identifier in self._control_tasks:
            return
        row = self._db.execute(
            "SELECT status,cancel_requested FROM control_jobs WHERE id=?", (identifier,)
        ).fetchone()
        if row is None or row[0] != "pending" or row[1]:
            return
        task = asyncio.create_task(self._run_control(identifier))
        self._control_tasks[identifier] = task
        task.add_done_callback(lambda _task: self._control_tasks.pop(identifier, None))

    def _validate_control_commit(self, identifier):
        from ai_neko.runtime.service import RuntimeConflictError

        row = self._db.execute("SELECT * FROM control_jobs WHERE id=?", (identifier,)).fetchone()
        if row["cancel_requested"] or self._closing:
            raise asyncio.CancelledError
        payload = json.loads(row["payload"])
        origin = self._turn(row["session_id"], row["turn_id"])
        if origin["input"] == "[已遗忘的对话]":
            raise asyncio.CancelledError
        if self._db.execute(
            "SELECT 1 FROM cancelled_requests WHERE session_id=? AND request_id=?",
            (row["session_id"], origin["request_id"]),
        ).fetchone():
            raise asyncio.CancelledError
        self._match_snapshot(row["session_id"], payload["match"])
        if payload["guide_revision"] != self.memory.guides.published_revision:
            raise RuntimeConflictError("攻略状态已更新，请重新操作。")

    def _control_receipt(self, row):
        payload = json.loads(row["payload"])
        try:
            if row["action"] == "new_match":
                return self.matches.preflight(row["session_id"], "new", payload["value"])
            value = payload["value"]
            return self.memory.guides.preflight(
                "selection",
                {k: v for k, v in value.items() if k not in {"request_id", "expected_revision"}},
                value["request_id"],
                value["expected_revision"],
            )
        except (ValueError, LookupError):
            return None

    async def _run_control(self, identifier):
        row = self._db.execute("SELECT * FROM control_jobs WHERE id=?", (identifier,)).fetchone()
        if row is None or row["cancel_requested"]:
            return
        with self._db:
            self._db.execute(
                "UPDATE control_jobs SET status='running',updated_at=? WHERE id=?",
                (time.time(), identifier),
            )
        payload = json.loads(row["payload"])
        try:

            def guard():
                self._validate_control_commit(identifier)

            if row["action"] == "new_match":
                result = await self.match_control(
                    row["session_id"], "new", payload["value"], _before_commit=guard
                )
            else:
                result = await self.guide_selection(payload["value"], _before_commit=guard)
            self._finish_control(identifier, result)
        except asyncio.CancelledError:
            current = self._db.execute(
                "SELECT * FROM control_jobs WHERE id=?", (identifier,)
            ).fetchone()
            receipt = self._control_receipt(current)
            with self._db:
                self._db.execute(
                    "UPDATE control_jobs SET status='cancelled',cancel_requested=1,result=?,updated_at=? WHERE id=?",
                    (json.dumps(receipt) if receipt else None, time.time(), identifier),
                )
        except Exception:
            receipt = self._control_receipt(row)
            pending = (
                self._db.execute("SELECT 1 FROM memory_erasure").fetchone()
                or self._db.execute(
                    "SELECT 1 FROM match_settlements WHERE session_id=?", (row["session_id"],)
                ).fetchone()
            )
            # A worker can commit and lose its return value. The authority's
            # durable receipt is sufficient once its cancellation cleanup ended.
            self._finish_control(
                identifier, receipt, error="control_failed" if pending or receipt is None else None
            )

    def _finish_control(self, identifier, result, *, recovered=False, error=None):
        row = self._db.execute("SELECT * FROM control_jobs WHERE id=?", (identifier,)).fetchone()
        if (
            row["cancel_requested"]
            or self._turn(row["session_id"], row["turn_id"])["input"] == "[已遗忘的对话]"
        ):
            with self._db:
                self._db.execute(
                    "UPDATE control_jobs SET status='cancelled',cancel_requested=1,result=? WHERE id=?",
                    (json.dumps(result), identifier),
                )
            return
        current = self.matches.current(row["session_id"])
        binding = {
            "match_id": current["match_id"] if current else None,
            "expected_revision": self.matches.revision(row["session_id"]),
            "state_revision": current["state_revision"]
            if current
            else self.matches.revision(row["session_id"]),
            "selection": current.get("selection") if current else None,
            "input_origin": (self._binding(row["turn_id"]) or {}).get("input_origin", "text"),
            "control_response": True,
        }
        text = (
            "已经开始新一局；本局目标和动态状态已清空，攻略选择保留。"
            if row["action"] == "new_match"
            else "已经切换到所选攻略。"
            if result and result.get("previous_selection")
            else "已经采用所选攻略。"
        )
        superseded = self._control_superseded(row, result)
        if superseded:
            text = "这次操作已经提交；当前状态已有后续更新，请查看当前攻略和对局。"
        if error:
            text = (
                "操作已提交，但收尾尚未完成，请稍后刷新状态。"
                if result
                else "操作未能完成，请刷新当前状态后重试。"
            )
        now = time.time()
        with self._db:
            self._db.execute(
                "INSERT OR IGNORE INTO turns(id,session_id,input,guide,status,created_at,settled_at) VALUES (?,?,'',0,'completed',?,?)",
                (row["response_turn_id"], row["session_id"], now, now),
            )
            self._db.execute(
                "INSERT OR IGNORE INTO turn_matches VALUES (?,?,?,?)",
                (
                    row["response_turn_id"],
                    row["session_id"],
                    binding["match_id"],
                    json.dumps(binding),
                ),
            )
            self._db.execute(
                "INSERT OR IGNORE INTO turn_user_history VALUES (?,?)",
                (row["response_turn_id"], row["turn_id"]),
            )
            self._db.execute(
                "INSERT OR IGNORE INTO turn_metadata VALUES (?,?)",
                (
                    row["response_turn_id"],
                    json.dumps(
                        {
                            "guide_control": {"revision": self.memory.guides.published_revision},
                            "kind": "control_response",
                        }
                    ),
                ),
            )
            if row["action"] == "select_guide" and result:
                value = json.loads(row["payload"])["value"]
                self._db.execute(
                    "INSERT OR IGNORE INTO turn_guides VALUES (?,?,?,0)",
                    (row["response_turn_id"], value["guide_id"], value["revision_id"]),
                )
            if not self._db.execute(
                "SELECT 1 FROM events WHERE turn_id=?", (row["response_turn_id"],)
            ).fetchone():
                self._insert_event(row["response_turn_id"], {"type": "text", "text": text})
                self._insert_event(row["response_turn_id"], {"type": "done", "status": "completed"})
            self._db.execute(
                "UPDATE control_jobs SET status=?,result=?,error=?,updated_at=? WHERE id=?",
                (
                    "error" if error else "completed",
                    json.dumps(result) if result else None,
                    error or ("recovered_no_autoplay" if recovered else None),
                    now,
                    identifier,
                ),
            )

    def _control_superseded(self, row, result):
        if not result:
            return False
        current = (
            self.matches.revision(row["session_id"])
            if row["action"] == "new_match"
            else self.memory.guides.published_revision
        )
        return result.get("committed_revision", result.get("revision")) != current

    def _recover_control_jobs(self):
        for row in self._db.execute(
            "SELECT * FROM control_jobs WHERE status IN ('pending','running') OR (status='cancelled' AND result IS NULL)"
        ).fetchall():
            receipt = self._control_receipt(row)
            if receipt:
                self._finish_control(row["id"], receipt, recovered=True)
            elif not row["cancel_requested"]:
                with self._db:
                    self._db.execute(
                        "UPDATE control_jobs SET status='interrupted',error='application_restarted' WHERE id=?",
                        (row["id"],),
                    )

    def control_job(self, session_id, identifier):
        from ai_neko.runtime.service import RuntimeAccessError, _identifier

        self._check_open()
        self._session(session_id)
        _identifier(identifier)
        row = self._db.execute(
            "SELECT * FROM control_jobs WHERE id=? AND session_id=?", (identifier, session_id)
        ).fetchone()
        if row is None:
            raise RuntimeAccessError("操作不存在。")
        result = json.loads(row["result"]) if row["result"] else None
        response = self._db.execute(
            "SELECT * FROM turns WHERE id=?", (row["response_turn_id"],)
        ).fetchone()
        return {
            "id": identifier,
            "control_job_id": identifier,
            "action": row["action"],
            "status": row["status"],
            "response_turn_id": row["response_turn_id"] if response else None,
            "response_turn": self._turn_summary(response) if response else None,
            "result": result,
            "committed": result is not None,
            "error": row["error"],
            "replayed": row["created_at"] < self._control_boot_time,
            "superseded": self._control_superseded(row, result),
            "confirmation_cancelled": bool(row["cancel_requested"]),
        }

    async def cancel_control_job(self, session_id, identifier):
        self.control_job(session_id, identifier)
        with self._db:
            self._db.execute(
                "UPDATE control_jobs SET cancel_requested=1,status='cancelled',updated_at=? WHERE id=?",
                (time.time(), identifier),
            )
            self._db.execute(
                "UPDATE audio_playback SET state='interrupted' WHERE state='started' AND turn_id=(SELECT response_turn_id FROM control_jobs WHERE id=?)",
                (identifier,),
            )
        task = self._control_tasks.get(identifier)
        if task and task is not asyncio.current_task() and not task.done():
            task.cancel()
            await asyncio.gather(task, return_exceptions=True)
        return self.control_job(session_id, identifier)

    def validate_voice_turn(self, session_id, turn_id):
        from ai_neko.runtime.service import RuntimeConflictError

        turn = self._turn(session_id, turn_id)
        if (
            turn["status"] not in {"running", "completed"}
            or turn["input"] == "[已遗忘的对话]"
            or not any(
                event.get("type") == "text"
                for event in self._payloads(turn_id, through=turn["sent_seq"])
            )
        ):
            raise RuntimeConflictError("该回复已失效或尚未投递。")
        row = self._db.execute(
            "SELECT payload FROM turn_metadata WHERE turn_id=?", (turn_id,)
        ).fetchone()
        metadata = json.loads(row[0]) if row else {}
        revision = metadata.get("guide_control", {}).get("revision")
        if (
            revision is not None and revision != self.memory.guides.published_revision
        ) or self._db.execute(
            "SELECT 1 FROM turn_guides WHERE turn_id=? AND invalidated=1", (turn_id,)
        ).fetchone():
            raise RuntimeConflictError("回复所用攻略已更新。")
        try:
            self._assert_match_turn(session_id, turn_id)
        except asyncio.CancelledError:
            raise RuntimeConflictError("所属对局已更新。") from None
        job = self._control_for_turn(turn_id)
        if job and (job["cancel_requested"] or job["status"] != "completed"):
            raise RuntimeConflictError("操作提示已停止。")

    async def _stop_control_jobs(self):
        with self._db:
            self._db.execute(
                "UPDATE control_jobs SET cancel_requested=1,status='cancelled' WHERE status IN ('pending','running')"
            )
        tasks = list(self._control_tasks.values())
        for task in tasks:
            task.cancel()
        if tasks:
            await asyncio.gather(*tasks, return_exceptions=True)
