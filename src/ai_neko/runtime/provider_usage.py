"""Content-free local HTTP-attempt journal, independent of UI delivery/checkpoints.

A committed start records intent at the HTTP dispatch boundary, not proof that a
provider received or charged the request. There is no atomic commit spanning
SQLite and a remote service; a start without a response remains uncertain.
"""

from __future__ import annotations

import json
import re
import time

from ai_neko.providers import token_usage

SCOPES = ("chat_generation", "memory_extraction")
OUTCOMES = {"completed", "failed", "cancelled", "interrupted"}
ERROR_CODES = {
    "authentication_failed",
    "rate_limited",
    "provider_http_error",
    "invalid_response",
    "output_limit",
    "stream_interrupted",
    "timeout",
    "blocked_endpoint",
    "network_error",
    "generation_failed",
    "cancelled",
    "interrupted",
    "process_interrupted",
    "provider_tracking_failed",
    "stream_closed",
    "unexpected_error",
}


def conversation_provider_usage_v7(db):
    db.execute("""CREATE TABLE IF NOT EXISTS turn_provider_tracking (
        turn_id TEXT NOT NULL REFERENCES turns(id), scope TEXT NOT NULL,
        coverage TEXT NOT NULL, state TEXT NOT NULL,
        PRIMARY KEY(turn_id, scope)
    )""")
    db.execute("""CREATE TABLE IF NOT EXISTS provider_requests (
        call_id TEXT PRIMARY KEY, turn_id TEXT NOT NULL REFERENCES turns(id),
        scope TEXT NOT NULL, job_id TEXT, job_attempt INTEGER,
        started_at REAL NOT NULL, response_at REAL, finished_at REAL,
        http_status INTEGER, outcome TEXT, error_code TEXT,
        usage_status TEXT NOT NULL DEFAULT 'unknown', usage TEXT
    )""")
    db.execute("CREATE INDEX IF NOT EXISTS provider_requests_turn ON provider_requests(turn_id)")


class ProviderUsageMixin:
    def _initialize_turn_usage(self, turn_id):
        # Caller is accepting this new turn in the same transaction. Never backfill
        # old turns with a zero: absence of an observation is not zero requests.
        self._db.executemany(
            "INSERT INTO turn_provider_tracking VALUES (?,?,'complete','pending')",
            [(turn_id, scope) for scope in SCOPES],
        )

    def _usage_state(self, turn_id, scope, state, *, pending_only=False):
        with self._guard:
            if self._closed:
                return
            with self._db:
                self._db.execute(
                    "UPDATE turn_provider_tracking SET state=? WHERE turn_id=? AND scope=?"
                    + (" AND state='pending'" if pending_only else ""),
                    (state, turn_id, scope),
                )

    def _observe_model_usage(self, model, turn_id, scope, *, job_id=None, job_attempt=None):
        if scope not in SCOPES:
            raise ValueError("invalid_provider_scope")
        if job_id is not None and not re.fullmatch(r"[a-f0-9]{64}", job_id):
            raise ValueError("invalid_provider_job")
        if job_attempt is not None and (type(job_attempt) is not int or job_attempt < 1):
            raise ValueError("invalid_provider_job_attempt")
        observer = getattr(model, "set_request_observer", None)
        with self._guard:
            self._check_open()
            if self._db.execute("SELECT 1 FROM turns WHERE id=?", (turn_id,)).fetchone() is None:
                if callable(observer):
                    observer(None)
                return model
            with self._db:
                self._db.execute(
                    "INSERT OR IGNORE INTO turn_provider_tracking VALUES (?,?,'legacy','running')",
                    (turn_id, scope),
                )
                self._db.execute(
                    "UPDATE turn_provider_tracking SET state='running',"
                    "coverage=CASE WHEN ? THEN coverage ELSE 'unavailable' END "
                    "WHERE turn_id=? AND scope=?",
                    (callable(observer), turn_id, scope),
                )
        if callable(observer):
            observer(
                lambda event: self._record_provider_request(
                    turn_id, scope, event, job_id=job_id, job_attempt=job_attempt
                )
            )
        return model

    def _record_provider_request(self, turn_id, scope, event, *, job_id=None, job_attempt=None):
        if not isinstance(event, dict) or event.get("type") != "provider_request":
            raise ValueError("invalid_provider_observation")
        call_id, phase = event.get("call_id"), event.get("phase")
        if not isinstance(call_id, str) or not re.fullmatch(r"[a-f0-9]{32}", call_id):
            raise ValueError("invalid_provider_call_id")
        if phase not in {"started", "response", "finished"}:
            raise ValueError("invalid_provider_phase")
        with self._guard:
            self._check_open()
            tracking = self._db.execute(
                "SELECT 1 FROM turn_provider_tracking WHERE turn_id=? AND scope=?",
                (turn_id, scope),
            ).fetchone()
            if tracking is None:
                # Erasure has removed this turn's accounting. Do not resurrect it
                # from a delayed finish callback or allow another paid dispatch.
                if phase == "started":
                    raise ValueError("provider_tracking_unavailable")
                return
            row = self._db.execute(
                "SELECT * FROM provider_requests WHERE call_id=?", (call_id,)
            ).fetchone()
            if row is not None and (row["turn_id"] != turn_id or row["scope"] != scope):
                raise ValueError("provider_call_conflict")
            now = time.time()
            with self._db:
                if phase == "started":
                    self._db.execute(
                        "INSERT OR IGNORE INTO provider_requests "
                        "(call_id,turn_id,scope,job_id,job_attempt,started_at) VALUES (?,?,?,?,?,?)",
                        (call_id, turn_id, scope, job_id, job_attempt, now),
                    )
                elif row is None:
                    raise ValueError("provider_call_not_started")
                elif row["finished_at"] is not None:
                    return  # Completion is idempotent, including after recovery.
                elif phase == "response":
                    status = event.get("http_status")
                    if type(status) is not int or not 100 <= status <= 599:
                        raise ValueError("invalid_provider_http_status")
                    self._db.execute(
                        "UPDATE provider_requests SET response_at=COALESCE(response_at,?),"
                        "http_status=COALESCE(http_status,?) WHERE call_id=?",
                        (now, status, call_id),
                    )
                else:
                    outcome = event.get("outcome")
                    if outcome not in OUTCOMES:
                        raise ValueError("invalid_provider_outcome")
                    usage = token_usage(event.get("usage"))
                    status = event.get("usage_status")
                    if usage is None or status not in {"reported", "partial"}:
                        usage, status = None, "unknown"
                    code = event.get("error_code")
                    if code is not None and code not in ERROR_CODES:
                        code = "generation_failed"
                    self._db.execute(
                        "UPDATE provider_requests SET finished_at=?,outcome=?,error_code=?,"
                        "usage_status=?,usage=? WHERE call_id=?",
                        (now, outcome, code, status, json.dumps(usage) if usage else None, call_id),
                    )

    def _recover_provider_usage(self):
        with self._db:
            self._db.execute(
                "UPDATE provider_requests SET finished_at=?,outcome='interrupted',"
                "error_code='process_interrupted' WHERE finished_at IS NULL",
                (time.time(),),
            )
            self._db.execute(
                "UPDATE turn_provider_tracking SET state='interrupted' "
                "WHERE state IN ('pending','running','queued')"
            )

    def _provider_usage_summary(self, turn_id):
        tracking = {
            row["scope"]: dict(row)
            for row in self._db.execute(
                "SELECT * FROM turn_provider_tracking WHERE turn_id=?", (turn_id,)
            )
        }
        records = []
        for row in self._db.execute(
            "SELECT * FROM provider_requests WHERE turn_id=? ORDER BY started_at,call_id",
            (turn_id,),
        ):
            item = dict(row)
            item.pop("turn_id")
            item["usage"] = json.loads(row["usage"]) if row["usage"] else None
            # Adapter currently makes exactly one POST per stream. Job attempts
            # are separate work executions and must never become HTTP retries.
            item["http_attempt_index"] = 1
            item["http_retry_count"] = 0
            records.append(item)

        def summarize(items, complete, final):
            final = final and all(item["finished_at"] is not None for item in items)
            reported = [item for item in items if item["usage_status"] == "reported"]
            totals = (
                {
                    key: sum(item["usage"][key] for item in reported)
                    for key in ("prompt_tokens", "completion_tokens", "total_tokens")
                }
                if reported
                else None
            )
            return {
                "recorded_attempt_count": len(items),
                "attempt_count": len(items) if complete else None,
                "attempt_count_complete": complete,
                "is_final": final,
                "response_count": sum(item["response_at"] is not None for item in items),
                "dispatch_uncertain_count": sum(item["response_at"] is None for item in items),
                "http_retry_count": 0 if complete else None,
                "reported_usage_count": len(reported),
                "partial_usage_count": sum(item["usage_status"] == "partial" for item in items),
                "unknown_usage_count": sum(item["usage_status"] == "unknown" for item in items),
                "reported_token_totals": totals,
                "usage_complete": complete
                and final
                and all(item["usage_status"] == "reported" for item in items),
            }

        scopes = {}
        for scope in SCOPES:
            state = tracking.get(scope, {"coverage": "legacy", "state": "unknown"})
            scopes[scope] = {
                "coverage": state["coverage"],
                "state": state["state"],
                **summarize(
                    [item for item in records if item["scope"] == scope],
                    state["coverage"] == "complete",
                    state["state"] in {"settled", "not_scheduled"}
                    or (scope == "chat_generation" and state["state"] == "interrupted"),
                ),
            }
        complete = all(scope["attempt_count_complete"] for scope in scopes.values())
        return {
            "version": 1,
            "basis": "local_http_attempts_not_provider_receipts_or_billing",
            "scopes": scopes,
            **summarize(records, complete, all(scope["is_final"] for scope in scopes.values())),
            "records": records,
        }
