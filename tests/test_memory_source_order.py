"""Synthetic source-registration order, migration and restore boundaries."""

import asyncio
import json
import shutil
import sqlite3
from concurrent.futures import ThreadPoolExecutor
from contextlib import closing
from types import SimpleNamespace

import pytest
from test_memory_extraction import (
    claim,
    complete,
    confirm,
    correction,
    create,
)
from test_runtime import Model, Store, settled

from ai_neko.config.paths import initialize_data_root
from ai_neko.config.schema import MigrationError
from ai_neko.memory import MemoryConflictError, MemoryInputError, MemoryService
from ai_neko.memory import service as memory_module
from ai_neko.runtime import SessionRuntime


@pytest.fixture
def memory(tmp_path):
    with MemoryService(initialize_data_root(tmp_path / "synthetic")) as service:
        yield service


def orders(memory):
    return dict(
        memory._db.execute(
            "SELECT id,source_order FROM memory_sources WHERE scope=?", (memory.scope,)
        )
    )


def sequence(memory):
    return memory._db.execute(
        "SELECT source_sequence FROM memory_scopes WHERE scope=?", (memory.scope,)
    ).fetchone()[0]


def legacy_schema(db, version):
    db.execute("DROP INDEX memory_source_order")
    db.execute("ALTER TABLE memory_sources DROP COLUMN source_order")
    db.execute("ALTER TABLE memory_scopes DROP COLUMN source_sequence")
    if version == 1:
        db.execute("ALTER TABLE memory_scopes DROP COLUMN invalidation_revision")
    db.execute(f"PRAGMA user_version={version}")


@pytest.mark.parametrize("delta", [0, -1])
@pytest.mark.parametrize("operation", ["correct", "confirm"])
def test_old_source_cannot_mutate_newer_provenance_on_equal_or_backwards_clock(
    memory, monkeypatch, delta, operation
):
    clock = [2000000000.0]
    monkeypatch.setattr("ai_neko.memory.service.time", SimpleNamespace(time=lambda: clock[0]))
    source = "更正喜欢茶为喜欢咖啡" if operation == "correct" else "喜欢茶"
    job = memory.enqueue_extraction("old", source)
    clock[0] += delta
    fact = memory.remember("喜欢茶", source_id="new")
    claimed = memory.claim_extraction(job["id"])
    proposal = (
        correction(fact, "喜欢咖啡", source) if operation == "correct" else confirm(fact, source)
    )
    with pytest.raises(MemoryConflictError, match="source_precedes_target"):
        complete(memory, claimed, [proposal])
    assert memory.list_facts() == [fact]
    assert orders(memory) == {"old": 1, "new": 2}
    # The order field must not replace or distort actual wall-clock timestamps.
    assert fact["created_at"] == clock[0]
    assert job["created_at"] == 2000000000.0


@pytest.mark.parametrize("delta", [0, -1])
@pytest.mark.parametrize("operation", ["correct", "confirm"])
def test_later_source_can_mutate_older_target_on_equal_or_backwards_clock(
    memory, monkeypatch, delta, operation
):
    clock = [2000000000.0]
    monkeypatch.setattr("ai_neko.memory.service.time", SimpleNamespace(time=lambda: clock[0]))
    fact = memory.remember("喜欢茶", source_id="old")
    clock[0] += delta
    source = "更正喜欢茶为喜欢咖啡" if operation == "correct" else "喜欢茶"
    job = claim(memory, "new", source)
    proposal = (
        correction(fact, "喜欢咖啡", source) if operation == "correct" else confirm(fact, source)
    )
    assert complete(memory, job, [proposal])["status"] == "completed"


@pytest.mark.parametrize("operation", ["correct", "confirm"])
def test_later_source_remains_valid_when_older_fact_is_extracted_after_enqueue(memory, operation):
    old = claim(memory, "old", "喜欢茶")
    source = "更正喜欢茶为喜欢咖啡" if operation == "correct" else "喜欢茶"
    newer = memory.enqueue_extraction("new", source)
    fact = complete(memory, old, [create("喜欢茶")])["facts"][0]
    newer = memory.claim_extraction(newer["id"])
    assert fact["revision"] > newer["expected_revision"]
    proposal = (
        correction(fact, "喜欢咖啡", source) if operation == "correct" else confirm(fact, source)
    )
    assert complete(memory, newer, [proposal])["status"] == "completed"


def test_later_confirmation_is_a_barrier_even_without_fact_revision_change(memory):
    fact = memory.remember("喜欢茶", source_id="original")
    job = memory.enqueue_extraction("old-job", "更正喜欢茶为喜欢咖啡")
    confirmed = memory.remember("喜欢茶", source_id="latest-confirmation")
    assert confirmed["revision"] == fact["revision"]
    claimed = memory.claim_extraction(job["id"])
    with pytest.raises(MemoryConflictError, match="source_precedes_target"):
        complete(memory, claimed, [correction(confirmed, "喜欢咖啡", "更正喜欢茶为喜欢咖啡")])
    assert memory.list_facts() == [confirmed]


def test_registration_order_survives_retry_reopen_and_concurrent_connections(memory):
    paths = memory.paths
    memory.remember("喜欢茶", source_id="first")
    before = sequence(memory)
    memory.remember("喜欢茶", source_id="first")
    memory.enqueue_extraction("first", "喜欢茶")
    assert sequence(memory) == before
    with MemoryService(paths) as second, ThreadPoolExecutor(2) as pool:
        list(
            pool.map(
                lambda pair: pair[0].enqueue_extraction(pair[1], "喜欢茶"),
                [(memory, "second"), (second, "third")],
            )
        )
    with MemoryService(paths) as reopened:
        assert sorted(orders(reopened).values()) == [1, 2, 3]
        reopened.enqueue_extraction("fourth", "喜欢茶")
        assert orders(reopened)["fourth"] == 4
    with MemoryService(paths, character_id="other") as other:
        other.enqueue_extraction("first", "喜欢茶")
        assert orders(other) == {"first": 1}


@pytest.mark.parametrize("version", [1, 2])
def test_legacy_migration_keeps_unknown_order_and_new_sources_follow_it(tmp_path, version):
    paths = initialize_data_root(tmp_path / "legacy")
    with MemoryService(paths) as original:
        fact = original.remember("喜欢茶", source_id="legacy")
    with closing(sqlite3.connect(paths.memory / "long-term.sqlite")) as db, db:
        legacy_schema(db, version)
    with MemoryService(paths) as upgraded:
        assert upgraded._db.execute("PRAGMA user_version").fetchone()[0] == 3
        assert upgraded.list_facts() == [fact]
        assert orders(upgraded) == {"legacy": 0}
        source = "更正喜欢茶为喜欢咖啡"
        job = claim(upgraded, "new", source)
        assert (
            complete(upgraded, job, [correction(fact, "喜欢咖啡", source)])["status"] == "completed"
        )
    with MemoryService(paths) as reopened:
        assert orders(reopened) == {"legacy": 0, "new": 1}


@pytest.mark.parametrize("operation", ["create", "correct", "confirm"])
def test_unknown_source_only_allows_create(memory, operation):
    source = "更正喜欢茶为喜欢咖啡" if operation == "correct" else "喜欢茶"
    job = memory.enqueue_extraction("legacy", source)
    memory._db.execute("UPDATE memory_sources SET source_order=0 WHERE id='legacy'")
    if operation == "create":
        claimed = memory.claim_extraction(job["id"])
        assert complete(memory, claimed, [create(source)])["status"] == "completed"
        return
    fact = memory.remember("喜欢茶", source_id="target")
    claimed = memory.claim_extraction(job["id"])
    proposal = (
        correction(fact, "喜欢咖啡", source) if operation == "correct" else confirm(fact, source)
    )
    with pytest.raises(MemoryConflictError, match="source_order_unknown"):
        complete(memory, claimed, [proposal])
    assert memory.list_facts() == [fact]


@pytest.mark.parametrize("version", [1, 2, 3])
def test_restore_marks_every_source_unknown_keeps_corrections_and_accepts_new_sources(
    memory, version
):
    fact = memory.remember("喜欢茶", source_id="original")
    snapshot = memory.backup()
    if version < 3:
        with closing(sqlite3.connect(memory.paths.backups / snapshot["id"])) as db, db:
            legacy_schema(db, version)
    corrected = memory.correct(fact["id"], "喜欢咖啡", source_id="corrected")
    high_water = sequence(memory)
    memory.restore(snapshot["id"])
    assert memory.list_facts() == [corrected]
    assert orders(memory) == {"original": 0, "corrected": 0}
    assert sequence(memory) == high_water
    old_job = claim(memory, "corrected", "喜欢咖啡")
    with pytest.raises(MemoryConflictError, match="source_order_unknown"):
        complete(memory, old_job, [confirm(corrected, "喜欢咖啡")])
    source = "更正喜欢咖啡为喜欢水"
    new_job = claim(memory, "new-after-restore", source)
    assert orders(memory)["new-after-restore"] > high_water
    assert (
        complete(memory, new_job, [correction(corrected, "喜欢水", source)])["status"]
        == "completed"
    )


def test_restoring_twice_never_promotes_unknown_sources_back_to_snapshot_order(memory):
    memory.remember("喜欢茶", source_id="original")
    snapshot = memory.backup()
    high_water = sequence(memory)
    memory.restore(snapshot["id"])
    memory.restore(snapshot["id"])
    assert orders(memory) == {"original": 0}
    assert sequence(memory) == high_water


def test_restore_and_forget_never_reuse_removed_high_water_order(memory):
    memory.remember("喜欢茶", source_id="original")
    snapshot = memory.backup()
    newest = memory.remember("喜欢蓝色", source_id="removed")
    high_water = sequence(memory)
    memory.forget(newest["id"])
    memory.restore(snapshot["id"])
    assert sequence(memory) == high_water
    memory.remember("喜欢猫", source_id="fresh")
    assert orders(memory)["fresh"] == high_water + 1


def test_restore_resets_only_its_scope_and_keeps_other_scope_order(memory):
    with MemoryService(memory.paths, character_id="other") as other:
        memory.remember("喜欢茶", source_id="shared-id")
        other.remember("喜欢猫", source_id="shared-id")
        snapshot = memory.backup()
        other.remember("喜欢蓝色", source_id="second")
        other_before = other.list_facts(), orders(other), sequence(other)
        memory.restore(snapshot["id"])
        assert orders(memory) == {"shared-id": 0}
        assert (other.list_facts(), orders(other), sequence(other)) == other_before


def test_collision_free_foreign_snapshot_still_cannot_establish_combined_order(memory, tmp_path):
    original = memory.remember("喜欢茶", source_id="local")
    corrected = memory.correct(original["id"], "喜欢咖啡", source_id="local-correction")
    with MemoryService(initialize_data_root(tmp_path / "foreign")) as foreign:
        foreign._db.execute("UPDATE memory_scopes SET source_sequence=6")
        imported = foreign.remember("喜欢猫", source_id="foreign")
        snapshot = foreign.backup()
        shutil.copyfile(
            foreign.paths.backups / snapshot["id"], memory.paths.backups / snapshot["id"]
        )
    memory.restore(snapshot["id"])
    assert {fact["id"] for fact in memory.list_facts()} == {corrected["id"], imported["id"]}
    assert orders(memory) == {"foreign": 0, "local": 0, "local-correction": 0}
    assert sequence(memory) == 7
    memory.enqueue_extraction("new-after-restore", "喜欢茶")
    assert orders(memory)["new-after-restore"] == 8


def test_failed_v3_migration_rolls_back_both_columns_then_reopens_cleanly(tmp_path, monkeypatch):
    paths = initialize_data_root(tmp_path / "migration-recovery")
    with MemoryService(paths) as original:
        fact = original.remember("喜欢茶", source_id="legacy")
    database = paths.memory / "long-term.sqlite"
    with closing(sqlite3.connect(database)) as db, db:
        legacy_schema(db, 2)
    steps = memory_module._MIGRATIONS

    def interrupt(db):
        memory_module._long_term_v3(db)
        raise RuntimeError("synthetic interruption after both columns")

    with monkeypatch.context() as scoped:
        scoped.setattr(memory_module, "_MIGRATIONS", [steps[0], (3, interrupt)])
        with pytest.raises(MigrationError, match="migration_to_3_failed"):
            MemoryService(paths)
    with closing(sqlite3.connect(database)) as db:
        assert db.execute("PRAGMA user_version").fetchone()[0] == 2
        assert "source_order" not in {
            row[1] for row in db.execute("PRAGMA table_info(memory_sources)")
        }
        assert "source_sequence" not in {
            row[1] for row in db.execute("PRAGMA table_info(memory_scopes)")
        }
    recovery = paths.backups / "long-term-pre-migration-v2.sqlite"
    assert recovery.exists()
    with MemoryService(paths) as recovered:
        assert recovered.list_facts() == [fact]
        assert orders(recovered) == {"legacy": 0}
        assert not recovery.exists()


@pytest.mark.parametrize(
    "damage",
    [
        "UPDATE memory_sources SET source_order=-1",
        "UPDATE memory_sources SET source_order=1.5",
        "UPDATE memory_sources SET source_order='invalid'",
        "UPDATE memory_scopes SET source_sequence=0",
        "UPDATE memory_scopes SET source_sequence=-1",
        "UPDATE memory_scopes SET source_sequence=1.5",
        "UPDATE memory_sources SET turn_id=42",
        "UPDATE memory_sources SET body='different source identity'",
        "UPDATE memory_sources SET turn_id='different-turn'",
        "ALTER TABLE memory_scopes DROP COLUMN source_sequence",
        "ALTER TABLE memory_sources DROP COLUMN source_order",
    ],
)
def test_invalid_source_snapshot_rolls_back_every_live_table(memory, damage):
    memory.remember("喜欢茶", source_id="original")
    snapshot = memory.backup()
    with closing(sqlite3.connect(memory.paths.backups / snapshot["id"])) as db, db:
        db.execute("DROP INDEX memory_source_order")
        db.execute(damage)
    before = list(memory._db.iterdump()), memory.published_revision
    with pytest.raises(MemoryInputError, match="invalid_memory_backup"):
        memory.restore(snapshot["id"])
    assert (list(memory._db.iterdump()), memory.published_revision) == before


@pytest.mark.parametrize("conflict", ["same-id", "different-id", "internal-duplicate"])
def test_snapshot_known_order_conflicts_are_rejected_atomically(memory, conflict):
    memory.remember("喜欢茶", source_id="original")
    memory.remember("喜欢猫", source_id="second")
    snapshot = memory.backup()
    with closing(sqlite3.connect(memory.paths.backups / snapshot["id"])) as db, db:
        db.execute("DROP INDEX memory_source_order")
        if conflict == "same-id":
            db.execute("UPDATE memory_scopes SET source_sequence=3")
            db.execute("UPDATE memory_sources SET source_order=3 WHERE id='original'")
        elif conflict == "different-id":
            db.execute("UPDATE memory_sources SET source_order=3-source_order")
        else:
            db.execute("UPDATE memory_sources SET source_order=1")
    before = list(memory._db.iterdump()), memory.published_revision
    with pytest.raises(MemoryInputError, match="invalid_memory_backup"):
        memory.restore(snapshot["id"])
    assert (list(memory._db.iterdump()), memory.published_revision) == before


def test_source_sequence_exhaustion_is_atomic_and_idempotent_retry_still_works(memory):
    fact = memory.remember("喜欢茶", source_id="existing")
    memory._db.execute("UPDATE memory_scopes SET source_sequence=?", (2**63 - 1,))
    before = list(memory._db.iterdump())
    with pytest.raises(MemoryInputError, match="source_sequence"):
        memory.remember("喜欢猫", source_id="overflow")
    assert list(memory._db.iterdump()) == before
    assert memory.remember("喜欢茶", source_id="existing") == fact


def test_restore_failure_after_replacing_tables_rolls_back_order_and_high_water(
    memory, monkeypatch
):
    original = memory.remember("喜欢茶", source_id="original")
    snapshot = memory.backup()
    memory.correct(original["id"], "喜欢咖啡", source_id="corrected")
    memory.enqueue_extraction("pending", "喜欢猫")
    before = list(memory._db.iterdump()), memory.published_revision
    original_erase = memory._erase

    def interrupt(*args, **kwargs):
        original_erase(*args, **kwargs)
        assert set(orders(memory).values()) == {0}
        raise RuntimeError("synthetic interruption after snapshot tables replaced")

    monkeypatch.setattr(memory, "_erase", interrupt)
    with pytest.raises(RuntimeError, match="synthetic interruption"):
        memory.restore(snapshot["id"])
    assert (list(memory._db.iterdump()), memory.published_revision) == before


def test_unknown_source_create_cannot_bypass_existing_fact_key(memory):
    fact = memory.remember("喜欢茶", source_id="original", fact_key="drink")
    job = memory.enqueue_extraction("legacy", "喜欢咖啡")
    memory._db.execute("UPDATE memory_sources SET source_order=0 WHERE id='legacy'")
    claimed = memory.claim_extraction(job["id"])
    with pytest.raises(MemoryInputError):
        complete(memory, claimed, [create("喜欢咖啡", "drink")])
    assert memory.list_facts() == [fact]


def test_runtime_unknown_source_failure_settles_after_bounded_retries(tmp_path, monkeypatch):
    async def run():
        runtime = SessionRuntime(initialize_data_root(tmp_path / "runtime"), Store(Model()))
        try:
            sid = runtime.create_session()["id"]
            tid = (await runtime.start_turn(sid, "synthetic chat"))["id"]
            await settled(runtime, sid, tid)
            fact = runtime.memory.remember("喜欢茶", source_id="target")
            job = runtime.memory.enqueue_extraction("legacy", "喜欢茶", turn_id=tid)
            runtime.memory._db.execute("UPDATE memory_sources SET source_order=0 WHERE id='legacy'")
            runtime.providers.adapter = Model([json.dumps({"facts": [confirm(fact, "喜欢茶")]})])
            runtime.memory_preferences.update({"auto_extract": True})
            failures = []
            original_complete = runtime.memory.complete_extraction

            def observe_failure(*args, **kwargs):
                try:
                    return original_complete(*args, **kwargs)
                except MemoryConflictError as exc:
                    failures.append(str(exc))
                    raise

            monkeypatch.setattr(runtime.memory, "complete_extraction", observe_failure)
            for attempt in range(3):
                await runtime._process_memories()
                if attempt < 2:
                    scope = runtime.get_session(sid)["turns"][0]["provider_usage"]["scopes"][
                        "memory_extraction"
                    ]
                    assert scope["state"] == "retry_pending" and not scope["is_final"]
            assert failures == ["memory_source_order_unknown"] * 3
            status = runtime.memory.extraction_status(job["id"])
            assert status["status"] == "failed" and status["attempts"] == 3
            assert runtime.memory.list_facts() == [fact]
            assert runtime.memory.extraction_overview()["failed"] == 1
            usage = runtime.get_session(sid)["turns"][0]["provider_usage"]["scopes"][
                "memory_extraction"
            ]
            assert usage["state"] == "settled" and usage["is_final"]
            await runtime._process_memories()
            assert runtime.memory.extraction_status(job["id"])["attempts"] == 3
        finally:
            await runtime.close()

    asyncio.run(run())
