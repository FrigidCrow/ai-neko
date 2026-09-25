"""Disposable M0 checks, independent of user configuration and credentials."""

from __future__ import annotations

import json
import tempfile
from pathlib import Path

from ai_neko import APP_ID
from ai_neko.config.paths import initialize_data_root
from ai_neko.graph import GraphService, Scope, ScopeAccessError


def check_foundation() -> dict:
    checks = []
    # An explicit new root overrides AI_NEKO_DATA_DIR and never adopts user data.
    with tempfile.TemporaryDirectory(prefix="ai-neko-self-check-") as temporary:
        paths = initialize_data_root(Path(temporary) / "synthetic-data")
        checks.append("isolated_temporary_data")
        scope = Scope("diagnostic-user", "diagnostic-character", "diagnostic-session")
        with GraphService(paths.checkpoints) as service:
            handle = service.open_thread(scope)
            paused = service.run(scope, handle, "foundation check", pause=True)
            if paused["next"] != ["respond"] or not paused["interrupts"]:
                raise RuntimeError("synthetic graph did not pause")
            checks.append("graph_pause")
        # Close all SQLite handles, then reconstruct the graph from saved state.
        # CI additionally checks this across separate frozen executable processes.
        with GraphService(paths.checkpoints) as service:
            if service.open_thread(scope, create=False) != handle:
                raise RuntimeError("saved thread identity did not survive reopen")
            restored = service.get(scope, handle)
            if restored["checkpoint_id"] != paused["checkpoint_id"]:
                raise RuntimeError("saved checkpoint did not survive reopen")
            resumed = service.resume(scope, handle, "yes", checkpoint_id=paused["checkpoint_id"])
            expected = "synthetic: foundation check | resumed: yes"
            if resumed["values"]["response"] != expected or resumed["next"]:
                raise RuntimeError("synthetic graph did not resume correctly")
            checks.append("graph_reopen_resume")
            other = Scope("other-user", "diagnostic-character", "diagnostic-session")
            try:
                service.get(other, handle)
            except ScopeAccessError:
                checks.append("scope_isolation")
            else:
                raise RuntimeError("foreign scope accessed the saved thread")
    return {
        "app_id": APP_ID,
        "stage": "M0",
        "status": "passed",
        "checks": checks,
        "real_model_calls": 0,
        "external_network_calls": 0,
        "user_data_accessed": False,
    }


def main() -> int:
    try:
        result = check_foundation()
    except Exception as exc:
        print(
            json.dumps(
                {
                    "app_id": APP_ID,
                    "stage": "M0",
                    "status": "failed",
                    "error": type(exc).__name__,
                    "message": str(exc),
                }
            )
        )
        return 2
    print(json.dumps(result))
    return 0
