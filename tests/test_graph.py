"""Synthetic persistence and authorization checks; no real model tests."""

import json
import os
import sqlite3
import subprocess
import sys
from pathlib import Path
from uuid import UUID

import pytest

from ai_neko.graph import GraphService, GraphStateError, Scope, ScopeAccessError


@pytest.fixture
def service(tmp_path):
    checkpoints = tmp_path / "checkpoints"
    checkpoints.mkdir()
    with GraphService(checkpoints) as graph:
        yield graph


OWNER = Scope("synthetic-user", "cat-a", "same-external-session")
OTHER_SCOPES = [
    Scope("other-user", "cat-a", "same-external-session"),
    Scope("synthetic-user", "cat-b", "same-external-session"),
    Scope("synthetic-user", "cat-a", "other-session"),
]


def test_condition_and_custom_stream(service):
    handle = service.open_thread(OWNER)
    assert UUID(handle).version == 4
    assert service.open_thread(OWNER) == handle
    observed = []
    result = service.run(OWNER, handle, "  hello  ", on_event=observed.append)
    assert result["values"]["response"] == "synthetic: hello"
    assert result["next"] == []
    assert observed == result["events"]
    assert observed[0] == {"type": "prepared", "characters": 5}
    assert "".join(event.get("text", "") for event in observed) == "synthetic: hello"
    assert observed[-1]["type"] == "generation_done"
    blank = service.run(OWNER, handle, " \n\t ")
    assert blank["values"]["status"] == "empty"
    assert blank["values"]["response"] == ""
    assert blank["events"] == [{"type": "prepared", "characters": 0}]


def test_interrupt_resume_and_checkpoint_guards(service):
    handle = service.open_thread(OWNER)
    paused = service.run(OWNER, handle, "synthetic pause", pause=True)
    assert paused["next"] == ["respond"]
    assert paused["interrupts"][0]["value"]["text"] == "synthetic pause"
    assert paused["values"]["response"] == ""
    assert all(event["type"] != "generation_done" for event in paused["events"])
    with pytest.raises(GraphStateError, match="pending"):
        service.run(OWNER, handle, "replacement")
    resumed = service.resume(OWNER, handle, "yes", checkpoint_id=paused["checkpoint_id"])
    assert resumed["values"]["response"] == "synthetic: synthetic pause | resumed: yes"
    assert resumed["interrupts"] == []
    with pytest.raises(GraphStateError, match="not waiting"):
        service.resume(OWNER, handle, "again")
    with pytest.raises(GraphStateError, match="no longer current"):
        service.run(OWNER, handle, "stale run", checkpoint_id=paused["checkpoint_id"])
    with pytest.raises(GraphStateError, match="no longer current"):
        service.delete(OWNER, handle, checkpoint_id=paused["checkpoint_id"])
    assert (
        service.get(OWNER, handle, checkpoint_id=paused["checkpoint_id"])["values"]["response"]
        == ""
    )
    history = service.history(OWNER, handle)
    assert history[0]["checkpoint_id"] == resumed["checkpoint_id"]
    assert (
        len(service.history(OWNER, handle, checkpoint_id=resumed["checkpoint_id"]))
        == len(history) - 1
    )


def _call(service, method, scope, handle, **kwargs):
    if method == "run":
        return service.run(scope, handle, "intruder input", **kwargs)
    if method == "resume":
        return service.resume(scope, handle, "intruder response", **kwargs)
    return getattr(service, method)(scope, handle, **kwargs)


@pytest.mark.parametrize("method", ["get", "history", "run", "resume", "delete"])
@pytest.mark.parametrize("other", OTHER_SCOPES)
def test_all_operations_reject_foreign_handle_and_checkpoint(service, method, other):
    owner_handle = service.open_thread(OWNER)
    owner_state = service.run(OWNER, owner_handle, "owner synthetic data", pause=True)
    other_handle = service.open_thread(other)
    assert other_handle != owner_handle
    other_state = service.run(other, other_handle, "other synthetic data", pause=True)
    # A known opaque handle and the true owner's checkpoint cannot bypass scope.
    with pytest.raises(ScopeAccessError):
        _call(service, method, other, owner_handle, checkpoint_id=owner_state["checkpoint_id"])
    # Pairing an owned handle with another scope's checkpoint must also reject.
    with pytest.raises(ScopeAccessError):
        _call(service, method, other, other_handle, checkpoint_id=owner_state["checkpoint_id"])
    # Supplying a checkpoint as a handle never becomes a raw LangGraph lookup.
    with pytest.raises(ScopeAccessError):
        _call(service, method, other, owner_state["checkpoint_id"])
    assert service.get(OWNER, owner_handle) == {
        key: value for key, value in owner_state.items() if key != "events"
    }
    assert service.get(other, other_handle)["checkpoint_id"] == other_state["checkpoint_id"]


def test_delete_revokes_handle_and_removes_all_checkpoint_rows(service):
    owner_handle = service.open_thread(OWNER)
    service.run(OWNER, owner_handle, "delete synthetic data", pause=True)
    other = OTHER_SCOPES[1]
    other_handle = service.open_thread(other)
    service.run(other, other_handle, "keep synthetic data")
    service.delete(OWNER, owner_handle)
    with pytest.raises(ScopeAccessError):
        service.get(OWNER, owner_handle)
    assert service.open_thread(OWNER) != owner_handle
    assert (
        service.get(other, other_handle)["values"]["response"] == "synthetic: keep synthetic data"
    )
    with sqlite3.connect(service.checkpoint_dir / "graph.sqlite") as connection:
        for table in ("checkpoints", "writes"):
            assert (
                connection.execute(
                    f"SELECT COUNT(*) FROM {table} WHERE thread_id = ?", (owner_handle,)
                ).fetchone()[0]
                == 0
            )


def _cli(root: Path, command: list[str], *, handle=None, user=OWNER.user_id):
    args = [
        sys.executable,
        "-m",
        "ai_neko.graph",
        "--data-root",
        str(root),
        "--user",
        user,
        "--character",
        OWNER.character_id,
        "--thread",
        OWNER.external_thread_id,
    ]
    if handle:
        args.extend(["--handle", handle])
    result = subprocess.run(
        [*args, *command], capture_output=True, text=True, timeout=30, check=False
    )
    assert result.returncode == 0, result.stderr
    return json.loads(result.stdout)


def test_fresh_process_recovers_paused_graph_and_scope_mapping(tmp_path):
    # Each subprocess starts a fresh Python interpreter, not just a new object.
    root = tmp_path / "isolated-data"
    paused = _cli(root, ["run", "--text", "restart evidence", "--pause"])
    recovered = _cli(root, ["get"])
    assert recovered["thread_id"] == paused["thread_id"]
    assert recovered["checkpoint_id"] == paused["checkpoint_id"]
    assert recovered["interrupts"] == paused["interrupts"]
    resumed = _cli(root, ["resume", "--response", "after process exit"])
    assert (
        resumed["values"]["response"] == "synthetic: restart evidence | resumed: after process exit"
    )
    assert resumed["next"] == []
    latest = _cli(root, ["get"])
    assert latest["checkpoint_id"] == resumed["checkpoint_id"]


def test_second_graph_process_rejected_and_lock_released(tmp_path):
    checkpoints = tmp_path / "checkpoints"
    checkpoints.mkdir()
    with GraphService(checkpoints):
        child = subprocess.run(
            [
                sys.executable,
                "-c",
                (
                    "from pathlib import Path; from ai_neko.graph import GraphService; "
                    f"GraphService(Path({str(checkpoints)!r})).__enter__()"
                ),
            ],
            capture_output=True,
            text=True,
            timeout=30,
            check=False,
        )
        assert child.returncode != 0
        assert "Checkpoint directory is already in use" in child.stderr
    with GraphService(checkpoints) as opened:
        assert opened.open_thread(OWNER)


def test_constructor_and_import_have_no_application_writes(tmp_path):
    root = tmp_path / "must-not-exist"
    GraphService(root)
    assert not root.exists()
    script = (
        "import sys; sys.dont_write_bytecode = True; "
        "from pathlib import Path; import ai_neko.graph; "
        f"assert not Path({str(root)!r}).exists()"
    )
    result = subprocess.run(
        [sys.executable, "-B", "-c", script],
        cwd=tmp_path,
        env={**os.environ, "AI_NEKO_DATA_DIR": str(root)},
        capture_output=True,
        text=True,
        timeout=30,
        check=False,
    )
    assert result.returncode == 0, result.stderr
    assert list(tmp_path.iterdir()) == []


def test_graph_disables_environment_enabled_cloud_tracing(service, monkeypatch):
    import socket

    def forbidden(*args, **kwargs):
        raise AssertionError("M0 graph must not open network connections")

    monkeypatch.setenv("LANGSMITH_TRACING", "true")
    monkeypatch.setenv("LANGCHAIN_TRACING_V2", "true")
    monkeypatch.setattr(socket.socket, "connect", forbidden)
    handle = service.open_thread(OWNER)
    assert service.run(OWNER, handle, "offline synthetic input")["values"]["status"] == "complete"


@pytest.mark.parametrize(
    "name", ["graph.sqlite", "scopes.sqlite", "graph.sqlite-wal", ".graph.lock"]
)
def test_graph_rejects_redirected_database_and_lock_paths(tmp_path, name):
    checkpoints = tmp_path / "checkpoints"
    checkpoints.mkdir()
    target = tmp_path / "unrelated-file"
    target.write_text("unchanged", encoding="utf-8")
    try:
        (checkpoints / name).symlink_to(target)
    except OSError:
        pytest.skip("Host cannot create symlinks; execute this check with Windows Developer Mode")
    with pytest.raises(ValueError, match="symlink|reparse"):
        with GraphService(checkpoints):
            pass
    assert target.read_text(encoding="utf-8") == "unchanged"


@pytest.mark.parametrize(
    "name",
    [".graph.lock"]
    + [
        database + suffix
        for database in ("graph.sqlite", "scopes.sqlite")
        for suffix in ("", "-wal", "-shm", "-journal")
    ],
)
def test_graph_rejects_hardlinked_files_before_any_write(tmp_path, name):
    checkpoints = tmp_path / "checkpoints"
    checkpoints.mkdir()
    target = tmp_path / "unrelated-file"
    original = b"M0 unrelated hardlink target must not be truncated or modified"
    target.write_bytes(original)
    try:
        (checkpoints / name).hardlink_to(target)
    except OSError:
        pytest.skip("Host filesystem cannot create hardlinks; record this check as skipped")
    with pytest.raises(ValueError, match="hardlink|hard.link"):
        with GraphService(checkpoints):
            pass
    assert target.read_bytes() == original
    # Preflight must reject before creating the lock or either SQLite database.
    assert [path.name for path in checkpoints.iterdir()] == [name]


def test_graph_rejects_redirected_checkpoint_directory(tmp_path):
    target = tmp_path / "unrelated-directory"
    target.mkdir()
    checkpoints = tmp_path / "checkpoints"
    try:
        checkpoints.symlink_to(target, target_is_directory=True)
    except OSError:
        pytest.skip("Host cannot create directory symlinks; record this check as skipped")
    with pytest.raises(ValueError, match="checkpoint directory"):
        with GraphService(checkpoints):
            pass
    assert list(target.iterdir()) == []


def test_missing_or_closed_service_and_invalid_scope_are_rejected(tmp_path):
    with pytest.raises(ValueError):
        Scope("", "cat", "session")
    with pytest.raises(ValueError):
        Scope("user", "cat\x00", "session")
    graph = GraphService(tmp_path)
    with pytest.raises(GraphStateError, match="with block"):
        graph.open_thread(OWNER)
    with graph:
        with pytest.raises(ScopeAccessError):
            graph.open_thread(OWNER, create=False)
    with pytest.raises(GraphStateError, match="with block"):
        graph.open_thread(OWNER)
