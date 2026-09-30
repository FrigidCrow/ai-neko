"""Transactional adoption, deletion and durable control-request boundaries."""

import json
import os
import sqlite3
import subprocess
import sys
from concurrent.futures import ThreadPoolExecutor
from contextlib import closing
from threading import Event
from uuid import uuid4

import pytest
from test_guides import page

from ai_neko.config.paths import initialize_data_root
from ai_neko.config.schema import MigrationError
from ai_neko.memory import guides
from ai_neko.memory.guides import (
    GuideAccessError,
    GuideCapacityError,
    GuideConflictError,
    GuideInputError,
    GuideStore,
)


@pytest.fixture
def paths(tmp_path):
    return initialize_data_root(tmp_path / "data")


@pytest.fixture
def store(paths):
    with GuideStore(paths, "scope-a") as result:
        yield result


def adopt(store, document, *, expected_revision=None, request_id=None, **dims):
    return store.set_selection(
        dims.get("game", "星棋"),
        dims.get("platform", "Windows"),
        dims.get("mode", "排位"),
        document["guide_id"] if document else None,
        document["revision_id"] if document else None,
        expected_revision=store.revision() if expected_revision is None else expected_revision,
        request_id=request_id or uuid4().hex,
    )


def delete(store, document, *, expected_revision=None, request_id=None):
    return store.delete_document(
        document["guide_id"],
        expected_revision=store.revision() if expected_revision is None else expected_revision,
        request_id=request_id or uuid4().hex,
    )


def test_adoption_commits_scope_revision_and_reopens_pinned_revision(paths):
    with GuideStore(paths, "scope-a") as store:
        assert store.control_snapshot() == {"revision": 0, "selections": []}
        original = store.ingest(page("正文一"))
        adopted = adopt(store, original)
        assert adopted["revision"] == 1 == store.published_revision
        assert adopted["selection"]["selection_revision"] == 1
        updated = store.ingest(page("正文二"), expected_revision=1)
        assert updated["revision_id"] != original["revision_id"]
        assert store.revision() == 1
    with GuideStore(paths, "scope-a") as reopened:
        assert reopened.published_revision == 1
        assert reopened.control_snapshot()["selections"] == [adopted["selection"]]
        assert reopened.get_document(original["guide_id"])["revision_id"] == updated["revision_id"]
        assert (
            reopened.get_document(original["guide_id"], adopted["selection"]["revision_id"])["text"]
            == "正文一"
        )


def test_selection_is_unique_switch_has_previous_and_clear_preserves_body(store):
    first = store.ingest(page("first"))
    second = store.ingest(page("second", url="https://example.com/b"))
    one = adopt(store, first)
    two = adopt(store, second)
    assert two["previous_selection"] == one["selection"]
    assert store.control_snapshot()["selections"] == [two["selection"]]
    cleared = adopt(store, None)
    assert cleared["selection"] is None
    assert cleared["previous_selection"] == two["selection"]
    assert store.control_snapshot() == {"revision": 3, "selections": []}
    assert {item["guide_id"] for item in store.list_documents()} == {
        first["guide_id"],
        second["guide_id"],
    }
    assert all(not item["protected"] for item in store.list_documents())


def test_explicit_adoption_fills_unknown_dimensions_and_rejects_conflicting_known(store):
    document = store.ingest(page())
    adopt(store, document)
    known = store.get_document(document["guide_id"])
    assert (known["game"], known["platform"], known["mode"]) == ("星棋", "Windows", "排位")
    for dims in ({"game": "other"}, {"platform": "other"}, {"mode": "other"}):
        with pytest.raises(GuideConflictError, match="dimensions"):
            adopt(store, document, **dims)
    assert store.revision() == 1
    assert len(store.control_snapshot()["selections"]) == 1


@pytest.mark.parametrize("dimension", ["game", "platform", "mode"])
@pytest.mark.parametrize("value", ["", " ", " leading", "trailing ", None, 4])
def test_adoption_requires_explicit_nonblank_dimension(store, dimension, value):
    document = store.ingest(page())
    with pytest.raises(GuideInputError):
        adopt(store, document, **{dimension: value})
    assert store.revision() == 0


def test_adoption_cannot_pair_foreign_revision_or_half_clear(store):
    one = store.ingest(page("one"))
    two = store.ingest(page("two", url="https://example.com/two"))
    with pytest.raises(GuideAccessError):
        adopt(store, one | {"revision_id": two["revision_id"]})
    with pytest.raises(GuideInputError):
        adopt(store, one | {"revision_id": None})
    with pytest.raises(GuideInputError):
        adopt(store, one | {"guide_id": None})
    assert store.revision() == 0


def test_replay_precedes_cas_but_changed_payload_or_expected_revision_is_conflict(store):
    document = store.ingest(page())
    request_id = uuid4().hex
    first = adopt(store, document, expected_revision=0, request_id=request_id)
    replay = adopt(store, document, expected_revision=0, request_id=request_id)
    assert replay["replayed"] and not replay["superseded"]
    assert replay["selection"] == first["selection"]
    assert store.revision() == 1
    for kwargs in ({"expected_revision": 1}, {"mode": "casual", "expected_revision": 0}):
        with pytest.raises(GuideConflictError, match="request_conflict"):
            adopt(store, document, request_id=request_id, **kwargs)
    adopt(store, None)
    replay = adopt(store, document, expected_revision=0, request_id=request_id)
    assert replay["replayed"] and replay["superseded"] and replay["current_revision"] == 2
    assert store.control_snapshot()["selections"] == []


@pytest.mark.parametrize("request_id", ["", "a" * 31, "a" * 33, "A" * 32, "x" * 32, None, 5])
def test_request_id_format_rejected_before_mutation(store, request_id):
    document = store.ingest(page())
    with pytest.raises(GuideInputError, match="request_id"):
        store.delete_document(document["guide_id"], expected_revision=0, request_id=request_id)
    assert store.get_document(document["guide_id"])["text"]
    assert store.revision() == 0


@pytest.mark.parametrize("expected", [None, True, -1, "0", 2**63])
def test_control_cas_is_strict_and_preflight_has_no_side_effect(store, expected):
    document = store.ingest(page())
    with pytest.raises(GuideInputError, match="expected_revision"):
        store.preflight("delete", {"guide_id": document["guide_id"]}, uuid4().hex, expected)
    assert store.revision() == 0
    assert store.export_tombstones() == []


def test_delete_preflight_proves_target_and_cas_without_reserving(store):
    document = store.ingest(page())
    request_id = uuid4().hex
    assert store.preflight("delete", {"guide_id": document["guide_id"]}, request_id, 0) is None
    assert store.get_document(document["guide_id"])["text"]
    assert store._db.execute("SELECT COUNT(*) FROM guide_requests").fetchone()[0] == 0
    with pytest.raises(GuideAccessError):
        store.preflight("delete", {"guide_id": "guide-" + "a" * 32}, request_id, 0)
    adopt(store, document)
    with pytest.raises(GuideConflictError, match="revision_conflict"):
        delete(store, document, expected_revision=0)
    assert store.export_tombstones() == []


def test_delete_removes_revisions_chunks_checks_selection_and_has_safe_replay(store):
    first = store.ingest(page("SECRET body version one", title="SECRET title"))
    second = store.ingest(page("SECRET body version two"))
    adopt(store, first)
    request_id = uuid4().hex
    result = delete(store, first, expected_revision=1, request_id=request_id)
    assert result["revision"] == 2
    assert set(result["revision_ids"]) == {first["revision_id"], second["revision_id"]}
    assert (
        result["selection_changes"][0]["previous_selection"]["revision_id"] == first["revision_id"]
    )
    assert store.control_snapshot() == {"revision": 2, "selections": []}
    for table in ("guide_documents", "guide_revisions", "guide_chunks", "guide_revision_checks"):
        assert store._db.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0] == 0
    replay = delete(store, first, expected_revision=1, request_id=request_id)
    assert replay["replayed"] and replay["revision"] == 2
    assert store.revision() == 2
    ledger = " ".join(row[0] for row in store._db.execute("SELECT result FROM guide_requests"))
    assert "SECRET" not in ledger and "https://" not in ledger and "星棋" not in ledger
    tombstones = store.export_tombstones()
    assert tombstones[0]["guide_id"] == first["guide_id"]
    assert len(tombstones[0]["content_hashes"]) == 2
    assert "SECRET" not in json.dumps(tombstones)


def test_tombstones_reject_original_redirect_alias_changed_url_same_body_and_old_versions(store):
    original = store.ingest(page("one", final_url="https://example.com/final"))
    store.ingest(page("two", final_url="https://example.com/final2"))
    delete(store, original)
    candidates = [
        page("new body"),
        page("new body", url="https://example.com/final"),
        page("new body", url="https://example.com/new", final_url="https://example.com/final2"),
        page("one", url="https://example.com/new"),
        page("two", url="https://example.com/new"),
    ]
    for candidate in candidates:
        with pytest.raises(GuideConflictError, match="guide_deleted"):
            store.ingest(candidate)
    assert store.list_documents() == []


def test_late_fetch_unknown_new_url_is_rejected_by_captured_revision(store):
    original = store.ingest(page())
    captured = store.revision()
    delete(store, original)
    with pytest.raises(GuideConflictError, match="revision_conflict"):
        store.ingest(
            page("late unknown text", url="https://example.com/late"), expected_revision=captured
        )
    assert store.list_documents() == []
    assert store.ingest(
        page("new independent text", url="https://example.com/new"), expected_revision=1
    )["saved"]


def test_same_body_refresh_remembers_redirect_alias_for_deletion(store):
    original = store.ingest(page("one", final_url="https://example.com/first-final"))
    refreshed = store.ingest(page("one", final_url="https://example.com/latest-final"))
    assert refreshed["revision_id"] == original["revision_id"]
    assert refreshed["final_url"] == "https://example.com/first-final"
    delete(store, original)
    with pytest.raises(GuideConflictError, match="guide_deleted"):
        store.ingest(page("changed body", url="https://example.com/latest-final"))
    assert store._db.execute("SELECT COUNT(*) FROM guide_url_hashes").fetchone()[0] == 0


def test_scope_isolation_covers_selection_revision_request_ids_and_tombstones(paths):
    with GuideStore(paths, "scope-a") as a, GuideStore(paths, "scope-b") as b:
        first = a.ingest(page())
        second = b.ingest(page())
        request_id = uuid4().hex
        adopt(a, first, request_id=request_id)
        assert b.revision() == 0
        with pytest.raises(GuideAccessError):
            adopt(b, first)
        adopt(b, second, request_id=request_id)
        delete(a, first)
        assert b.ingest(page())["saved"]
        assert b.control_snapshot()["selections"][0]["guide_id"] == second["guide_id"]
        assert b.export_tombstones() == [] and b.revision() == 1


def test_selected_document_resists_lru_even_manual_unprotect_then_release(store, monkeypatch):
    first = store.ingest(page("first" * 100))
    second = store.ingest(page("second" * 100, url="https://example.com/second"))
    adopt(store, first)
    store.set_protected(first["guide_id"], False)
    assert store.get_document(first["guide_id"])["protected"]
    monkeypatch.setattr(guides, "MAX_STORAGE_BYTES", store.logical_bytes() + 200)
    new = store.ingest(page("third" * 100, url="https://example.com/third"))
    assert new["evicted_guide_ids"] == [second["guide_id"]]
    assert store.get_document(first["guide_id"])["text"] == "first" * 100
    adopt(store, None)
    store.get_document(new["guide_id"])
    new = store.ingest(page("fourth" * 100, url="https://example.com/fourth"))
    assert first["guide_id"] in new["evicted_guide_ids"]


def test_capacity_failure_does_not_advance_or_change_adoption(store, monkeypatch):
    original = store.ingest(page("body" * 100))
    adopted = adopt(store, original)
    monkeypatch.setattr(guides, "MAX_STORAGE_BYTES", store.logical_bytes())
    with pytest.raises(GuideCapacityError):
        store.ingest(page("new body", url="https://example.com/new"), expected_revision=1)
    assert store.control_snapshot()["selections"] == [adopted["selection"]]
    assert store.revision() == store.published_revision == 1


def test_ingest_request_ledger_does_not_rewrite_on_retry_or_resurrect_after_delete(store):
    request_id = uuid4().hex
    payload = {"url": "https://example.com/guide?mode=ranked"}
    saved = store.ingest(
        page("SECRET source body"),
        expected_revision=0,
        request_id=request_id,
        operation="import",
        operation_payload=payload,
    )
    replay = store.ingest(
        page("SECRET changed retry body"),
        expected_revision=0,
        request_id=request_id,
        operation="import",
        operation_payload=payload,
    )
    assert replay["replayed"] and replay["revision_id"] == saved["revision_id"]
    assert store.get_document(saved["guide_id"])["text"] == "SECRET source body"
    delete(store, saved)
    replay = store.ingest(
        page("SECRET source body"),
        expected_revision=0,
        request_id=request_id,
        operation="import",
        operation_payload=payload,
    )
    assert replay["replayed"] and replay["superseded"] and replay["deleted"]
    assert not replay["saved"] and "text" not in replay and "original_url" not in replay
    assert store.list_documents() == []
    raw = " ".join(str(tuple(row)) for row in store._db.execute("SELECT * FROM guide_requests"))
    assert "SECRET" not in raw and "https://" not in raw


def test_delete_and_selection_failure_roll_back_every_table_and_published_revision(
    store, monkeypatch
):
    original = store.ingest(page())
    record = store._record_request

    def fail(*args, **kwargs):
        record(*args, **kwargs)
        raise RuntimeError("synthetic commit failure")

    with monkeypatch.context() as patch:
        patch.setattr(store, "_record_request", fail)
        with pytest.raises(RuntimeError):
            adopt(store, original)
        assert store.control_snapshot() == {"revision": 0, "selections": []}
        assert store.published_revision == 0
        assert store.get_document(original["guide_id"])["game"] == ""
    adopt(store, original)
    before = store.control_snapshot()
    with monkeypatch.context() as patch:
        patch.setattr(store, "_record_request", fail)
        with pytest.raises(RuntimeError):
            delete(store, original)
    assert store.control_snapshot() == before
    assert store.published_revision == 1
    assert store.export_tombstones() == []
    assert store.get_document(original["guide_id"])["text"]
    assert store.chunks(original["revision_id"])


def test_published_revision_does_not_block_or_expose_inflight_write(store):
    started, release = Event(), Event()

    def mutate():
        with store._transaction():
            store._advance_revision()
            started.set()
            assert release.wait(3)

    with ThreadPoolExecutor() as pool:
        future = pool.submit(mutate)
        assert started.wait(3)
        try:
            assert store.published_revision == 0
        finally:
            release.set()
        future.result(timeout=3)
    assert store.published_revision == 1


@pytest.mark.parametrize(
    "value",
    [
        {"text": "private body"},
        {"url": "https://example.com/body"},
        {"selection": {"game": "arbitrary user label"}},
        {"guide_ids": ["private body"]},
    ],
)
def test_request_ledger_rejects_accidental_plaintext_receipt(store, value):
    with pytest.raises(GuideInputError), store._transaction():
        store._record_request("selection", {}, uuid4().hex, {"revision": 0, **value}, 0)
    assert store._db.execute("SELECT COUNT(*) FROM guide_requests").fetchone()[0] == 0


def test_revision_exhaustion_rolls_back_instead_of_turning_integer_into_real(store):
    document = store.ingest(page())
    with store._transaction():
        store._db.execute("INSERT INTO guide_control VALUES (?,?)", (store.scope, 2**63 - 2))
    with pytest.raises(GuideConflictError, match="revision_exhausted"):
        adopt(store, document, expected_revision=2**63 - 2)
    assert type(store.revision()) is int
    assert store.revision() == store.published_revision == 2**63 - 2
    assert store.control_snapshot()["selections"] == []


def test_independent_connections_cas_and_replayed_receipts_survive_reopen(paths):
    with GuideStore(paths, "scope-a") as a, GuideStore(paths, "scope-a") as b:
        first = a.ingest(page())
        request_id = uuid4().hex
        result = adopt(a, first, expected_revision=0, request_id=request_id)
        with pytest.raises(GuideConflictError):
            adopt(b, first, expected_revision=0)
        assert b.revision() == 1
    with GuideStore(paths, "scope-a") as reopened:
        replay = adopt(reopened, first, expected_revision=0, request_id=request_id)
        assert replay["replayed"] and replay["selection"] == result["selection"]


@pytest.mark.usefixtures("sandbox_compatible")
def test_new_process_recovers_adoption_and_enforces_committed_deletion(paths):
    with GuideStore(paths, "scope-a") as store:
        removed = store.ingest(page("removed", url="https://example.com/removed"))
        active = store.ingest(page("active", url="https://example.com/active"))
        adopted = adopt(store, active)
        delete(store, removed)
    code = """
import json, sys
from ai_neko.config.paths import initialize_data_root
from ai_neko.memory.guides import GuideStore, GuideConflictError
with GuideStore(initialize_data_root(sys.argv[1]), 'scope-a') as store:
    result = store.control_snapshot()
    try:
        store.ingest(json.loads(sys.argv[2]), expected_revision=result['revision'])
    except GuideConflictError as exc:
        result['error'] = str(exc)
    result['tombstones'] = store.export_tombstones()
    print(json.dumps(result, ensure_ascii=False))
"""
    process = subprocess.run(
        [sys.executable, "-c", code, str(paths.root), json.dumps(page("removed"))],
        check=True,
        capture_output=True,
        text=True,
        encoding="utf-8",
        timeout=15,
        env={**os.environ, "PYTHONIOENCODING": "utf-8"},
    )
    recovered = json.loads(process.stdout)
    assert recovered["revision"] == 2
    assert recovered["selections"] == [adopted["selection"]]
    assert recovered["error"] == "guide_deleted"
    assert recovered["tombstones"][0]["guide_id"] == removed["guide_id"]


def test_v2_upgrade_preserves_documents_and_failed_v3_is_reopenable(paths, monkeypatch):
    path = paths.guides / "guides.sqlite"
    with closing(sqlite3.connect(path)) as db:
        guides._guides_v2(db)
        db.execute("PRAGMA user_version=2")
        db.execute("CREATE TABLE legacy_marker(value TEXT)")
        db.execute("INSERT INTO legacy_marker VALUES ('preserve')")
        db.commit()
    original = guides._MIGRATIONS

    def failure(db):
        guides._guides_v3(db)
        raise RuntimeError("synthetic migration interruption")

    with monkeypatch.context() as patch:
        patch.setattr(guides, "_MIGRATIONS", [(2, guides._guides_v2), (3, failure)])
        with pytest.raises(MigrationError, match="migration_to_3_failed"):
            GuideStore(paths, "scope-a")
    with closing(sqlite3.connect(path)) as db:
        assert db.execute("PRAGMA user_version").fetchone()[0] == 2
        assert db.execute("SELECT value FROM legacy_marker").fetchone()[0] == "preserve"
        assert not db.execute(
            "SELECT name FROM sqlite_master WHERE name='guide_control'"
        ).fetchall()
    assert original == guides._MIGRATIONS
    with GuideStore(paths, "scope-a") as upgraded:
        assert upgraded.revision() == upgraded.published_revision == 0
        assert upgraded._db.execute("PRAGMA user_version").fetchone()[0] == 3
        assert upgraded.ingest(page())["saved"]
    assert not list(paths.backups.glob("guides-pre-migration-*.sqlite"))


def test_backup_registry_migrates_only_scoped_live_ledger_ownership(paths):
    backup_id = "guides-" + "a" * 32 + ".sqlite"
    with GuideStore(paths, "scope-a") as store:
        with store._transaction():
            store._record_request(
                "backup",
                {},
                uuid4().hex,
                {"backup_id": backup_id, "created_at": 12.0, "revision": 0},
                None,
            )
        store._db.execute("DROP TABLE guide_backup_registry")
        store._db.execute("PRAGMA user_version=2")
    with GuideStore(paths, "scope-b") as reopened:
        row = reopened._db.execute("SELECT * FROM guide_backup_registry").fetchone()
        assert tuple(row) == (backup_id, "scope-a", 12.0)


def test_backup_registry_migration_rejects_conflicting_scopes_without_partial_state(paths):
    backup_id = "guides-" + "b" * 32 + ".sqlite"
    with GuideStore(paths, "scope-a") as store:
        for scope in ("scope-a", "scope-b"):
            store._db.execute(
                "INSERT INTO guide_requests VALUES (?,?,?,?,?)",
                (
                    scope,
                    uuid4().hex,
                    "backup",
                    "a" * 64,
                    json.dumps({"backup_id": backup_id, "created_at": 1, "revision": 0}),
                ),
            )
        store._db.execute("DROP TABLE guide_backup_registry")
        store._db.execute("PRAGMA user_version=2")
    with pytest.raises(MigrationError, match="migration_to_3_failed"):
        GuideStore(paths, "scope-a")
    with closing(sqlite3.connect(paths.guides / "guides.sqlite")) as db:
        assert db.execute("PRAGMA user_version").fetchone()[0] == 2
        assert not db.execute(
            "SELECT name FROM sqlite_master WHERE name='guide_backup_registry'"
        ).fetchone()
