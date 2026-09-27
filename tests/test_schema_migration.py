"""Versioned migration framework: baseline, resume, rollback, backup."""

import sqlite3

import pytest

from ai_neko.config.schema import (
    MigrationError,
    apply_migrations,
    ensure_supported_schema,
    schema_version,
)


def _add_note(db) -> None:
    columns = {row[1] for row in db.execute("PRAGMA table_info(item)")}
    if "note" not in columns:
        db.execute("ALTER TABLE item ADD COLUMN note TEXT")


def _add_rank(db) -> None:
    columns = {row[1] for row in db.execute("PRAGMA table_info(item)")}
    if "rank" not in columns:
        db.execute("ALTER TABLE item ADD COLUMN rank INTEGER NOT NULL DEFAULT 0")


def _database(*, latest_shape: bool, isolation_level=None) -> sqlite3.Connection:
    db = sqlite3.connect(":memory:", isolation_level=isolation_level)
    db.execute("CREATE TABLE item (id TEXT PRIMARY KEY)")
    if latest_shape:
        _add_note(db)
        _add_rank(db)
    return db


def test_legacy_database_upgrades_and_records_ledger(tmp_path):
    db = _database(latest_shape=False)
    result = apply_migrations(db, steps=[(2, _add_note)], backup_path=tmp_path / "b.sqlite")
    assert result == 2 == schema_version(db)
    columns = {row[1] for row in db.execute("PRAGMA table_info(item)")}
    assert columns == {"id", "note"}


def test_fresh_latest_shape_database_is_idempotent_noop(tmp_path):
    db = _database(latest_shape=True)
    backup = tmp_path / "b.sqlite"
    result = apply_migrations(db, steps=[(2, _add_note)], backup_path=backup)
    assert result == 2 == schema_version(db)
    # No schema change was pending in the ledger sense... the step still ran as a
    # guarded no-op and a one-time pre-migration backup was taken.
    assert backup.exists()


def test_already_migrated_database_never_rewrites_or_backs_up(tmp_path):
    db = _database(latest_shape=True)
    apply_migrations(db, steps=[(2, _add_note)], backup_path=tmp_path / "first.sqlite")
    backup = tmp_path / "second.sqlite"
    result = apply_migrations(db, steps=[(2, _add_note)], backup_path=backup)
    assert result == 2
    assert not backup.exists()


def test_future_schema_is_rejected_without_modifying_database_or_backup(tmp_path):
    path = tmp_path / "future.sqlite"
    backup = tmp_path / "previous-backup.sqlite"
    backup.write_bytes(b"preserve existing recovery file")
    with sqlite3.connect(path) as db:
        db.execute("CREATE TABLE future_data (secret TEXT)")
        db.execute("INSERT INTO future_data VALUES ('must survive downgrade')")
        db.execute("PRAGMA user_version=99")
    before = path.read_bytes()
    with sqlite3.connect(path) as db:
        with pytest.raises(MigrationError, match="newer_than_supported"):
            ensure_supported_schema(db, 2)
        with pytest.raises(MigrationError, match="newer_than_supported"):
            apply_migrations(db, steps=[(2, _add_note)], backup_path=backup)
        assert schema_version(db) == 99
    assert path.read_bytes() == before
    assert backup.read_bytes() == b"preserve existing recovery file"


def test_backup_connection_is_closed_before_windows_file_replace(tmp_path, monkeypatch):
    from ai_neko.config import schema

    db = _database(latest_shape=False)
    connect, replace = sqlite3.connect, schema.os.replace
    opened = []

    def track_connect(*args, **kwargs):
        target = connect(*args, **kwargs)
        opened.append(target)
        return target

    def replace_after_close(source, destination):
        assert len(opened) == 1
        with pytest.raises(sqlite3.ProgrammingError, match="closed"):
            opened[0].execute("SELECT 1")
        replace(source, destination)

    monkeypatch.setattr(schema.sqlite3, "connect", track_connect)
    monkeypatch.setattr(schema.os, "replace", replace_after_close)
    apply_migrations(db, steps=[(2, _add_note)], backup_path=tmp_path / "backup.sqlite")


def test_failed_step_rolls_back_version_and_keeps_backup(tmp_path):
    backup = tmp_path / "b.sqlite"

    def boom(db):
        db.execute("ALTER TABLE item ADD COLUMN note TEXT")
        raise RuntimeError("synthetic interruption")

    db = _database(latest_shape=False)
    with pytest.raises(MigrationError, match="migration_to_2_failed"):
        apply_migrations(db, steps=[(2, boom)], backup_path=backup)
    # The rolled-back ledger keeps 0 (no ledger); the next open re-baselines
    # at 1 and retries the step.
    assert schema_version(db) == 0
    assert backup.exists()
    # Resume on the next open with the fixed step.
    assert apply_migrations(db, steps=[(2, _add_note)]) == 2
    columns = {row[1] for row in db.execute("PRAGMA table_info(item)")}
    assert columns == {"id", "note"}


def test_committed_steps_survive_a_later_step_failure(tmp_path):
    def boom(db):
        raise RuntimeError("synthetic later failure")

    db = _database(latest_shape=False)
    with pytest.raises(MigrationError, match="migration_to_3_failed"):
        apply_migrations(db, steps=[(2, _add_note), (3, boom)], backup_path=tmp_path / "b.sqlite")
    # Step 2 committed with its ledger entry; only step 3 rolled back.
    assert schema_version(db) == 2
    assert apply_migrations(db, steps=[(2, _add_note), (3, _add_rank)]) == 3
    columns = {row[1] for row in db.execute("PRAGMA table_info(item)")}
    assert columns == {"id", "note", "rank"}


def test_autocommit_connection_also_rolls_back(tmp_path):
    # The memory service opens its database with isolation_level=None.
    db = _database(latest_shape=False, isolation_level=None)

    def boom(db):
        db.execute("ALTER TABLE item ADD COLUMN note TEXT")
        raise RuntimeError("synthetic interruption")

    with pytest.raises(MigrationError):
        apply_migrations(db, steps=[(2, boom)])
    assert schema_version(db) == 0
    columns = {row[1] for row in db.execute("PRAGMA table_info(item)")}
    assert columns == {"id"}
    assert apply_migrations(db, steps=[(2, _add_note)]) == 2


@pytest.mark.parametrize(
    "steps",
    [
        [],
        [(1, _add_note)],
        [(2, _add_note), (2, _add_note)],
        [(3, _add_note), (2, _add_rank)],
        [(3, _add_rank)],
        [(2, _add_note), (4, _add_rank)],
        [(2.0, _add_note)],
    ],
)
def test_invalid_step_lists_are_rejected(steps):
    db = _database(latest_shape=True)
    with pytest.raises(MigrationError):
        apply_migrations(db, steps=steps)


def test_conversation_and_memory_services_stamp_their_ledgers(tmp_path):
    from ai_neko.config.paths import initialize_data_root
    from ai_neko.memory import MemoryService
    from ai_neko.runtime import SessionRuntime

    class Store:
        def model(self):
            raise AssertionError("no model calls expected")

        def web_tools(self):
            raise AssertionError("no web tools expected")

    paths = initialize_data_root(tmp_path / "roots")
    with MemoryService(paths) as memory:
        assert schema_version(memory._db) == 2
    runtime = SessionRuntime(paths, Store())
    assert schema_version(runtime._db) == 3
    assert (paths.root / "guides").is_dir()

    import asyncio

    asyncio.run(runtime.close())
