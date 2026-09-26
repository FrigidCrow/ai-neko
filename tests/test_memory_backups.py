"""Scoped snapshot management and durable restoration cleanup evidence."""

import json
import os
import sqlite3
import subprocess
import sys

import pytest

from ai_neko.config.paths import initialize_data_root
from ai_neko.memory import MemoryAccessError, MemoryConflictError, MemoryInputError, MemoryService


@pytest.fixture
def paths(tmp_path):
    return initialize_data_root(tmp_path / "data")


@pytest.fixture
def memory(paths):
    with MemoryService(paths) as service:
        yield service


def test_list_and_delete_snapshots_reveal_only_safe_metadata(memory, paths):
    memory.remember("喜欢无糖咖啡", source_id="private-source")
    first = memory.backup()
    memory.remember("常玩策略游戏", source_id="another-private-source")
    second = memory.backup()
    snapshots = memory.list_backups()
    assert [row["id"] for row in snapshots] == [second["id"], first["id"]]
    assert [row["facts_count"] for row in snapshots] == [2, 1]
    assert all(row["restorable"] for row in snapshots)
    assert set(snapshots[0]) == {"id", "created_at", "bytes", "facts_count", "restorable"}
    assert snapshots[1]["created_at"] == first["created_at"]
    assert snapshots[1]["bytes"] == (paths.backups / first["id"]).stat().st_size
    assert "private-source" not in json.dumps(snapshots)
    assert memory.delete_backup(first["id"]) == {"id": first["id"], "deleted": True}
    assert [row["id"] for row in memory.list_backups()] == [second["id"]]
    assert len(memory.list_facts()) == 2
    with pytest.raises(MemoryAccessError, match="not_found"):
        memory.delete_backup(first["id"])


def test_snapshot_list_returns_only_the_latest_hundred(memory):
    snapshots = [memory.backup() for _ in range(101)]
    expected = sorted(snapshots, key=lambda row: (row["created_at"], row["id"]), reverse=True)
    assert [row["id"] for row in memory.list_backups()] == [row["id"] for row in expected[:100]]
    # A previously listed/known snapshot remains deletable after falling outside
    # the page limit; no automatic retention policy removes the user's files.
    memory.delete_backup(expected[-1]["id"])


def test_management_uses_creator_scope_even_when_snapshot_contains_both_scopes(paths):
    with MemoryService(paths) as first, MemoryService(paths, character_id="other") as other:
        first.remember("喜欢咖啡", source_id="a")
        other.remember("喜欢茶", source_id="a")
        first_backup = first.backup()
        other_backup = other.backup()
        assert [row["id"] for row in first.list_backups()] == [first_backup["id"]]
        assert [row["id"] for row in other.list_backups()] == [other_backup["id"]]
        with pytest.raises(MemoryAccessError, match="scope"):
            first.delete_backup(other_backup["id"])
        assert (paths.backups / other_backup["id"]).exists()
        # Preserve the older explicit restore contract: the service imports
        # only its own scope from the complete database snapshot.
        first.restore(other_backup["id"])
        assert first.list_facts()[0]["content"] == "喜欢咖啡"
        assert other.list_facts()[0]["content"] == "喜欢茶"


def test_legacy_single_scope_snapshot_can_be_managed(memory, paths):
    snapshot = memory.backup()
    with sqlite3.connect(paths.backups / snapshot["id"]) as db:
        db.execute("DROP TABLE memory_backup_info")
    assert memory.list_backups()[0]["restorable"]
    memory.delete_backup(snapshot["id"])
    assert memory.list_backups() == []


def test_legacy_multiple_scope_snapshot_is_not_exposed_for_deletion(paths):
    with MemoryService(paths) as first, MemoryService(paths, character_id="other"):
        snapshot = first.backup()
        with sqlite3.connect(paths.backups / snapshot["id"]) as db:
            db.execute("DROP TABLE memory_backup_info")
        assert first.list_backups() == []
        with pytest.raises(MemoryAccessError, match="scope"):
            first.delete_backup(snapshot["id"])
        first.restore(snapshot["id"])


@pytest.mark.parametrize("operation", ["restore", "delete_backup"])
@pytest.mark.parametrize(
    "backup_id",
    ["../outside", "/tmp/file.sqlite", "memory-abc.sqlite", "memory-" + "a" * 32 + ".SQLITE", 1],
)
def test_snapshot_management_rejects_arbitrary_paths(memory, operation, backup_id):
    with pytest.raises(MemoryInputError, match="invalid_memory_backup"):
        getattr(memory, operation)(backup_id)


@pytest.mark.parametrize("link_kind", ["symlink", "hardlink"])
def test_redirected_snapshot_is_not_read_or_deleted(memory, paths, tmp_path, link_kind):
    external = tmp_path / "outside.sqlite"
    external.write_bytes(b"private external file")
    name = "memory-" + "a" * 32 + ".sqlite"
    link = paths.backups / name
    try:
        if link_kind == "symlink":
            link.symlink_to(external)
        else:
            link.hardlink_to(external)
    except OSError as exc:
        if getattr(exc, "winerror", None) == 1314:
            pytest.skip("Host needs Windows Developer Mode or symlink privileges")
        raise
    assert memory.list_backups() == []
    for operation in (memory.restore, memory.delete_backup):
        with pytest.raises(MemoryInputError, match="invalid_memory_backup"):
            operation(name)
    assert external.read_bytes() == b"private external file"
    assert link.exists()


def test_owned_damaged_snapshot_remains_deletable_but_cannot_restore(memory, paths):
    remembered = memory.remember("喜欢无糖咖啡", source_id="source")
    snapshot = memory.backup()
    with sqlite3.connect(paths.backups / snapshot["id"]) as db:
        db.execute("DROP TABLE memory_sources")
    assert memory.list_backups() == [
        {
            "id": snapshot["id"],
            "created_at": snapshot["created_at"],
            "bytes": (paths.backups / snapshot["id"]).stat().st_size,
            "restorable": False,
            "error": "invalid_memory_backup",
        }
    ]
    with pytest.raises(MemoryInputError, match="invalid_memory_backup"):
        memory.restore(snapshot["id"])
    assert memory.list_facts() == [remembered]
    memory.delete_backup(snapshot["id"])


def test_completely_corrupt_or_unowned_files_do_not_hide_healthy_snapshots(memory, paths):
    snapshot = memory.backup()
    corrupt_name = "memory-" + "b" * 32 + ".sqlite"
    (paths.backups / corrupt_name).write_bytes(b"not a database")
    (paths.backups / "notes.txt").write_text("unrelated application data")
    assert [row["id"] for row in memory.list_backups()] == [snapshot["id"]]
    with pytest.raises(MemoryInputError, match="invalid_memory_backup"):
        memory.delete_backup(corrupt_name)
    assert (paths.backups / corrupt_name).exists()


@pytest.mark.parametrize(
    "damage",
    [
        "UPDATE memory_scopes SET persona='not JSON'",
        "UPDATE memory_scopes SET revision='invalid revision'",
        "DELETE FROM memory_sources",
    ],
)
def test_structurally_valid_snapshot_with_invalid_data_cannot_replace_live_memory(
    memory, paths, damage
):
    fact = memory.remember("喜欢无糖咖啡", source_id="source")
    snapshot = memory.backup()
    with sqlite3.connect(paths.backups / snapshot["id"]) as db:
        db.execute(damage)
    assert memory.list_backups()[0]["restorable"] is False
    with pytest.raises(MemoryInputError, match="invalid_memory_backup"):
        memory.restore(snapshot["id"])
    assert memory.list_facts() == [fact]
    memory.delete_backup(snapshot["id"])


def test_oversize_snapshots_are_bounded_and_new_creation_leaves_no_partial_file(
    memory, paths, monkeypatch
):
    snapshot = memory.backup()
    monkeypatch.setattr("ai_neko.memory.service._BACKUP_MAX_BYTES", 1)
    assert memory.list_backups() == []
    with pytest.raises(MemoryInputError, match="memory_backup_too_large"):
        memory.restore(snapshot["id"])
    before = set(paths.backups.iterdir())
    with pytest.raises(MemoryInputError, match="memory_backup_too_large"):
        memory.backup()
    assert set(paths.backups.iterdir()) == before


@pytest.mark.parametrize("expected", [True, -1, 1.5, "1", 2**63])
def test_restore_rejects_invalid_expected_revision_without_mutation(memory, expected):
    snapshot = memory.backup()
    fact = memory.remember("喜欢咖啡", source_id="source")
    with pytest.raises(MemoryInputError, match="invalid_revision"):
        memory.restore(snapshot["id"], expected_revision=expected)
    assert memory.list_facts() == [fact]


def test_restore_rejects_stale_revision_then_accepts_current(memory):
    snapshot = memory.backup()
    revision = memory.revision()
    fact = memory.remember("喜欢咖啡", source_id="source")
    with pytest.raises(MemoryConflictError, match="stale_memory_revision"):
        memory.restore(snapshot["id"], expected_revision=revision)
    assert memory.list_facts() == [fact]
    memory.restore(snapshot["id"], expected_revision=memory.revision())
    assert memory.list_facts() == []


def test_restore_commits_removed_source_tombstones_for_fresh_process_recovery(memory, paths):
    original = memory.remember("喜欢咖啡", source_id="original")
    snapshot = memory.backup()
    removed_fact = memory.remember("刚新增的私人事实", source_id="removed-source")
    memory.enqueue_extraction("removed-pending-turn", "只存在于恢复前的待提取资料")
    result = memory.restore(snapshot["id"], expected_revision=memory.revision())
    assert set(result["source_ids"]) == {"removed-source", "removed-pending-turn"}
    assert result["fact_ids"] == [removed_fact["id"]]
    assert memory.list_facts() == [original]
    # The caller may die immediately after restore commits, before receiving its
    # return value or redacting the separate conversation journal.
    code = """
import json,sys
from ai_neko.config.paths import initialize_data_root
from ai_neko.memory import MemoryService
with MemoryService(initialize_data_root(sys.argv[1])) as memory:
    print(json.dumps(memory.erasure_state()))
"""
    process = subprocess.run(
        [sys.executable, "-c", code, str(paths.root)],
        check=True,
        capture_output=True,
        text=True,
        encoding="utf-8",
        env={**os.environ, "PYTHONIOENCODING": "utf-8"},
    )
    recovered = json.loads(process.stdout)
    assert set(recovered["source_ids"]) == set(result["source_ids"])
    assert recovered["fact_ids"] == [removed_fact["id"]]
    assert recovered["revision"] == result["revision"]
    with pytest.raises(MemoryConflictError, match="erased_memory_source"):
        memory.remember("刚新增的私人事实", source_id="removed-source")


def test_restore_keeps_removed_fact_tombstone_even_when_shared_source_survives(memory):
    evidence = "喜欢咖啡，也喜欢蓝色"
    original = memory.remember("喜欢咖啡", source_id="manual:shared", source_text=evidence)
    snapshot = memory.backup()
    removed = memory.remember("喜欢蓝色", source_id="manual:shared", source_text=evidence)
    result = memory.restore(snapshot["id"])
    assert result["source_ids"] == []
    assert result["fact_ids"] == [removed["id"]]
    assert memory.erasure_state()["fact_ids"] == [removed["id"]]
    assert memory.list_facts() == [original]
    assert memory.sources(original["id"])[0]["source_id"] == "manual:shared"


@pytest.mark.parametrize(
    "field,value", [("content", "喜欢茶"), ("fact_key", "changed-key"), ("kind", "event")]
)
def test_restore_rejects_changed_meaning_under_existing_fact_id_atomically(
    memory, paths, field, value
):
    fact = memory.remember("喜欢咖啡", source_id="manual:source")
    snapshot = memory.backup()
    with sqlite3.connect(paths.backups / snapshot["id"]) as db:
        db.execute(f"UPDATE memory_facts SET {field}=?", (value,))
    before = memory.erasure_state()
    persona = memory.get_persona()
    with pytest.raises(MemoryInputError, match="invalid_memory_backup"):
        memory.restore(snapshot["id"])
    assert memory.list_facts() == [fact]
    assert memory.erasure_state() == before
    assert memory.get_persona() == persona


def test_restore_retains_corrected_current_fact_without_erasing_its_identity(memory):
    fact = memory.remember("喜欢咖啡", source_id="manual:original")
    snapshot = memory.backup()
    corrected = memory.correct(fact["id"], "现在喜欢茶", source_id="manual:correction")
    result = memory.restore(snapshot["id"])
    assert memory.list_facts() == [corrected]
    assert fact["id"] not in result["fact_ids"]
    assert fact["id"] not in memory.erasure_state()["fact_ids"]


def test_closed_service_rejects_all_snapshot_operations(memory):
    snapshot = memory.backup()
    memory.close()
    operations = [
        memory.list_backups,
        memory.backup,
        lambda: memory.delete_backup(snapshot["id"]),
        lambda: memory.restore(snapshot["id"]),
    ]
    for operation in operations:
        with pytest.raises(MemoryConflictError, match="memory_closed"):
            operation()
