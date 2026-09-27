"""MVP1 memory regressions: corrections, erasure, and threaded snapshots."""

import sqlite3
from concurrent.futures import ThreadPoolExecutor
from contextlib import closing, contextmanager
from threading import Event

import pytest

import ai_neko.memory.service as memory_module
from ai_neko.config.paths import initialize_data_root
from ai_neko.config.schema import MigrationError
from ai_neko.memory import MemoryConflictError, MemoryService


@pytest.fixture
def memory(tmp_path):
    with MemoryService(initialize_data_root(tmp_path / "data")) as service:
        yield service


def test_correction_replaces_cached_tokens_even_when_clock_does_not_advance(memory, monkeypatch):
    monkeypatch.setattr(memory_module.time, "time", lambda: 1720000000.0)
    fact = memory.remember("favorite constellation orion", source_id="first")
    assert memory.recall("orion")[0]["id"] == fact["id"]
    corrected = memory.correct(fact["id"], "favorite constellation lyra", source_id="corrected")
    assert memory.recall("orion") == []
    assert memory.recall("lyra") == [corrected]


@pytest.mark.parametrize("operation", ["forget", "restore", "close"])
def test_erasure_and_close_drop_cached_private_tokens(memory, operation):
    snapshot = memory.backup()
    fact = memory.remember("private secretword", source_id="private-source")
    assert memory.recall("secretword") == [fact]
    assert memory._term_cache
    if operation == "forget":
        memory.forget(fact["id"])
    elif operation == "restore":
        memory.restore(snapshot["id"])
    else:
        memory.close()
    assert memory._term_cache == {}


def test_context_snapshot_does_not_mix_facts_with_a_later_revision(memory, monkeypatch):
    fact = memory.remember("favorite constellation orion", source_id="first")
    prior_revision = memory.revision()
    entered, release, correcting = Event(), Event(), Event()
    original = memory._recall

    def blocked_recall(*args, **kwargs):
        facts = original(*args, **kwargs)
        entered.set()
        assert release.wait(5)
        return facts

    def correct():
        correcting.set()
        return memory.correct(fact["id"], "favorite constellation lyra", source_id="corrected")

    monkeypatch.setattr(memory, "_recall", blocked_recall)
    with ThreadPoolExecutor(max_workers=2) as pool:
        reading = pool.submit(memory.recall_context, "orion")
        try:
            assert entered.wait(5)
            changing = pool.submit(correct)
            assert correcting.wait(5)
            assert not changing.done()
        finally:
            release.set()
        snapshot = reading.result(timeout=5)
        changed = changing.result(timeout=5)
    assert snapshot["facts"] == [fact]
    assert snapshot["revision"] == prior_revision
    assert snapshot["persona"] == memory.get_persona()
    assert changed["revision"] > snapshot["revision"]


def test_close_waits_for_recall_and_clears_its_cache(memory, monkeypatch):
    fact = memory.remember("private secretword", source_id="private-source")
    entered, release, closing_started = Event(), Event(), Event()
    original = memory_module.tokenize

    def blocked_tokenize(*args, **kwargs):
        entered.set()
        assert release.wait(5)
        return original(*args, **kwargs)

    def close():
        closing_started.set()
        memory.close()

    monkeypatch.setattr(memory_module, "tokenize", blocked_tokenize)
    with ThreadPoolExecutor(max_workers=2) as pool:
        reading = pool.submit(memory.recall, "secretword")
        try:
            assert entered.wait(5)
            stopping = pool.submit(close)
            assert closing_started.wait(5)
            assert not stopping.done()
        finally:
            release.set()
        assert reading.result(timeout=5) == [fact]
        stopping.result(timeout=5)
    assert memory._term_cache == {}
    with pytest.raises(MemoryConflictError, match="memory_closed"):
        memory.recall_context("secretword")


def test_newer_memory_schema_is_rejected_before_mutation_and_connection_is_closed(
    tmp_path, monkeypatch
):
    paths = initialize_data_root(tmp_path / "data")
    path = paths.memory / "long-term.sqlite"
    with closing(sqlite3.connect(path)) as db, db:
        db.execute("CREATE TABLE future_only (payload TEXT)")
        db.execute("INSERT INTO future_only VALUES ('keep this exact database')")
        db.execute("PRAGMA user_version=999")
    before = path.read_bytes()
    connections = []
    original = memory_module.sqlite3.connect

    def track(*args, **kwargs):
        connection = original(*args, **kwargs)
        connections.append(connection)
        return connection

    monkeypatch.setattr(memory_module.sqlite3, "connect", track)
    with pytest.raises(MigrationError):
        MemoryService(paths)
    assert path.read_bytes() == before
    assert list(paths.backups.iterdir()) == []
    assert len(connections) == 1
    with pytest.raises(sqlite3.ProgrammingError, match="closed"):
        connections[0].execute("SELECT 1")


def test_cached_tokens_follow_corrections_from_a_second_connection(memory, monkeypatch):
    monkeypatch.setattr(memory_module.time, "time", lambda: 1720000000.0)
    fact = memory.remember("favorite constellation orion", source_id="first")
    assert memory.recall("orion") == [fact]
    with MemoryService(memory.paths) as second:
        corrected = second.correct(fact["id"], "favorite constellation lyra", source_id="corrected")
    assert memory.recall("orion") == []
    assert memory.recall("lyra") == [corrected]


@pytest.mark.parametrize("legacy", [True, False])
def test_successful_memory_open_removes_only_automatic_migration_backup(tmp_path, legacy):
    paths = initialize_data_root(tmp_path / "data")
    automatic = paths.backups / "long-term-pre-migration-v2.sqlite"
    with MemoryService(paths) as memory:
        fact = memory.remember("synthetic private constellation orion", source_id="private-source")
        snapshot = memory.backup()
    user_snapshot = paths.backups / snapshot["id"]
    snapshot_bytes = user_snapshot.read_bytes()
    database = paths.memory / "long-term.sqlite"
    if legacy:
        with closing(sqlite3.connect(database)) as db:
            db.execute("PRAGMA user_version=0")
    else:
        # The previous release left this hidden copy even after a successful v2
        # upgrade. Opening the already-current database must also retire it.
        automatic.write_bytes(database.read_bytes())
    with MemoryService(paths) as memory:
        assert memory.list_facts() == [fact]
        assert not automatic.exists()
        assert user_snapshot.read_bytes() == snapshot_bytes
        memory.forget(fact["id"])
        assert memory.list_facts() == []
        assert not automatic.exists()
        # User-requested snapshots remain visible and independently manageable.
        assert [row["id"] for row in memory.list_backups()] == [snapshot["id"]]
        assert user_snapshot.read_bytes() == snapshot_bytes


@pytest.mark.parametrize("failure", ["migration", "scope_initialization"])
def test_failed_memory_upgrade_preserves_automatic_recovery_copy(tmp_path, monkeypatch, failure):
    paths = initialize_data_root(tmp_path / "data")
    with MemoryService(paths) as memory:
        fact = memory.remember("synthetic recovery evidence", source_id="recovery-source")
    with closing(sqlite3.connect(paths.memory / "long-term.sqlite")) as db:
        db.execute("PRAGMA user_version=0")
    if failure == "migration":

        def fail(db):
            db.execute("UPDATE memory_facts SET content='must roll back'")
            raise RuntimeError("synthetic migration interruption")

        monkeypatch.setattr(memory_module, "_MIGRATIONS", [(2, fail)])
        expected_error = MigrationError
    else:

        @contextmanager
        def fail(self):
            raise MemoryConflictError("synthetic scope initialization failure")
            yield

        monkeypatch.setattr(MemoryService, "_transaction", fail)
        expected_error = MemoryConflictError
    with pytest.raises(expected_error):
        MemoryService(paths)
    recovery = paths.backups / "long-term-pre-migration-v2.sqlite"
    assert recovery.exists()
    with closing(sqlite3.connect(recovery)) as db:
        assert db.execute("PRAGMA user_version").fetchone()[0] == 0
        assert (
            db.execute("SELECT content FROM memory_facts WHERE id=?", (fact["id"],)).fetchone()[0]
            == fact["content"]
        )
    with closing(sqlite3.connect(paths.memory / "long-term.sqlite")) as db:
        assert (
            db.execute("SELECT content FROM memory_facts WHERE id=?", (fact["id"],)).fetchone()[0]
            == fact["content"]
        )


def test_future_memory_rejection_preserves_existing_migration_backup(tmp_path):
    paths = initialize_data_root(tmp_path / "data")
    database = paths.memory / "long-term.sqlite"
    with closing(sqlite3.connect(database)) as db:
        db.execute("PRAGMA user_version=999")
    recovery = paths.backups / "long-term-pre-migration-v2.sqlite"
    recovery.write_bytes(b"existing recovery backup must not be touched")
    before = database.read_bytes(), recovery.read_bytes()
    with pytest.raises(MigrationError, match="newer_than_supported"):
        MemoryService(paths)
    assert (database.read_bytes(), recovery.read_bytes()) == before


def test_published_revision_getter_does_not_wait_for_recall_lock(memory, monkeypatch):
    fact = memory.remember("private secretword", source_id="private-source")
    entered, release = Event(), Event()
    original = memory_module.tokenize

    def blocked_tokenize(*args, **kwargs):
        entered.set()
        assert release.wait(5)
        return original(*args, **kwargs)

    monkeypatch.setattr(memory_module, "tokenize", blocked_tokenize)
    with ThreadPoolExecutor(max_workers=2) as pool:
        reading = pool.submit(memory.recall_context, "secretword")
        try:
            assert entered.wait(5)
            # The retrieval worker still owns both the guard and SQLite write
            # transaction. This read must finish while that worker is blocked.
            revision = pool.submit(lambda: memory.published_revision).result(timeout=1)
            assert revision == fact["revision"]
        finally:
            release.set()
        assert reading.result(timeout=5)["revision"] == revision


def test_published_revision_changes_only_after_successful_commit(memory):
    initial = memory.published_revision
    with pytest.raises(RuntimeError, match="rollback this change"), memory._transaction():
        uncommitted = memory._bump()
        assert uncommitted > initial
        assert memory.published_revision == initial
        raise RuntimeError("rollback this change")
    assert memory.published_revision == initial == memory.revision()
    with memory._transaction():
        committed = memory._bump()
        assert memory.published_revision == initial
    assert memory.published_revision == committed == memory.revision()
    fact = memory.remember("favorite constellation orion", source_id="original")
    assert memory.published_revision == fact["revision"]
    corrected = memory.correct(fact["id"], "favorite constellation lyra", source_id="correction")
    assert memory.published_revision == corrected["revision"]
    erased = memory.forget(fact["id"])
    assert memory.published_revision == erased["revision"]


def test_published_revision_initializes_from_existing_database_and_tracks_restore(memory):
    snapshot = memory.backup()
    fact = memory.remember("favorite constellation orion", source_id="original")
    with MemoryService(memory.paths) as second:
        assert second.published_revision == fact["revision"]
    restored = memory.restore(snapshot["id"])
    assert memory.published_revision == restored["revision"] == memory.revision()


def test_erasure_reports_only_real_source_rows_before_deleting_them(memory):
    # This raw source has not yielded any facts yet, so fact-source links alone
    # cannot identify it as user input when Runtime propagates erasure.
    source_id = "turn:pending-real-source"
    memory.enqueue_extraction(source_id, "synthetic private raw source")
    result = memory.forget_source(source_id, with_evidence=True)
    assert result["existing_source_ids"] == [source_id]
    assert result["source_ids"] == [source_id]
    assert result["source_bodies"] == ["synthetic private raw source"]
    assert memory.forget_source(source_id, with_evidence=True)["existing_source_ids"] == []


@pytest.mark.parametrize("with_evidence", [True, False])
def test_erasure_does_not_classify_synthetic_markers_as_raw_sources(memory, with_evidence):
    marker = "turn:derived-assistant-without-source-row"
    result = memory.forget_source(marker, with_evidence=with_evidence)
    assert result["source_ids"] == [marker]
    if with_evidence:
        assert result["existing_source_ids"] == []
        assert result["source_bodies"] == []
    else:
        assert set(result) == {"revision", "fact_ids", "source_ids"}
