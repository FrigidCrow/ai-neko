"""Synthetic public guide authority: provenance, bounded storage and recovery."""

import json
import os
import sqlite3
import subprocess
import sys
from concurrent.futures import ThreadPoolExecutor
from contextlib import closing

import pytest

from ai_neko.config.paths import DataRootError, initialize_data_root
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


def page(
    text="合成攻略正文：第八轮保留金币。", url="https://example.com/guide?mode=ranked", **changes
):
    return {
        "status": "read",
        "completeness": "full",
        "completeness_reasons": [],
        "text": text,
        "url": url,
        "original_url": url,
        "title": "合成攻略",
        "retrieved_at": "2026-09-27T10:00:00+00:00",
        "content_date": None,
        "headings": [],
        **changes,
    }


def test_long_body_after_twenty_thousand_is_durable_and_chunks_cover_every_character(store):
    first = "开局阶段\n" + ("先积累金币，再观察对手。\n" * 1600)
    text = first + "决胜阶段\n关键步骤：第九回合购买蓝色骑士。\n" + "不要提前花光。\n" * 400
    assert len(first) > 20_000
    headings = [
        {"level": 1, "title": "开局阶段", "start": 0, "end": 4},
        {"level": 2, "title": "决胜阶段", "start": len(first), "end": len(first) + 4},
    ]
    saved = store.ingest(
        page(text, headings=headings), game="星棋", platform="Windows", mode="排位"
    )
    body = store.get_document(saved["guide_id"])
    assert body["text"] == text
    assert body["headings"] == headings
    assert (body["game"], body["platform"], body["mode"]) == ("星棋", "Windows", "排位")
    chunks = store.chunks(saved["revision_id"])
    assert 1 < len(chunks) <= 100
    assert "".join(chunk["text"] for chunk in chunks) == text
    assert chunks[0]["start"] == 0 and chunks[-1]["end"] == len(text)
    assert all(
        left["end"] == right["start"] for left, right in zip(chunks, chunks[1:], strict=False)
    )
    assert all(text[item["start"] : item["end"]] == item["text"] for item in chunks)
    assert any("关键步骤" in item["text"] for item in chunks if item["start"] > 20_000)
    assert chunks[-1]["headings"] == headings
    assert store.rebuild_index(saved["revision_id"]) == chunks
    assert store.chunks(saved["revision_id"]) == chunks


def test_over_fifty_thousand_defensively_clips_and_marks_partial(store):
    saved = store.ingest(page("攻略\n" * 20_000))
    document = store.get_document(saved["guide_id"])
    assert len(document["text"]) == 50_000
    assert saved["completeness"] == "partial"
    assert saved["completeness_reasons"] == ["body_limit"]
    assert saved["retained_characters"] == 50_000
    assert saved["extracted_characters"] == 60_000
    assert len(store.chunks(saved["revision_id"])) <= 100


def test_reader_partial_and_unknown_version_never_become_complete_or_current(store):
    saved = store.ingest(page(completeness="partial", completeness_reasons=["images_unread"]))
    assert saved["completeness"] == "partial"
    assert saved["completeness_reasons"] == ["images_unread"]
    assert saved["game_version"] is None
    assert saved["version_basis"] is None
    assert saved["content_date"] is None


@pytest.mark.parametrize(
    "changes",
    [
        {"status": "snippet"},
        {"status": "unreadable"},
        {"status": "blocked"},
        {"text": ""},
        {"text": " \n\t"},
        {"text": None},
        {"completeness": "snippet"},
        {"completeness": None},
        {"retrieved_at": "tomorrow"},
        {"retrieved_at": None},
        {"retrieved_at": "2026-09-27T10:00:00"},
        {"content_date": "not a date"},
        {"headings": [{"level": 1, "title": "wrong", "start": 0, "end": 5}]},
    ],
)
def test_snippet_empty_unreadable_or_invalid_metadata_cannot_be_saved(store, changes):
    with pytest.raises(GuideInputError):
        store.ingest(page(**changes))
    assert store.list_documents() == []


@pytest.mark.parametrize(
    "url",
    [
        "file:///secret",
        "http://127.0.0.1/",
        "http://localhost/",
        "http://192.168.1.1/",
        "http://[::1]/",
        "http://[::ffff:127.0.0.1]/",
        "https://someone:secret@example.com/",
        "https://example.local/",
        "https://example.com\\@127.0.0.1/",
        "https://example.com/\n",
    ],
)
def test_unsafe_original_and_final_urls_rejected(store, url):
    with pytest.raises(GuideInputError):
        store.ingest(page(url=url))
    with pytest.raises(GuideInputError):
        store.ingest(page(final_url=url))
    assert store.list_documents() == []


def test_document_content_revisions_deduplicate_including_return_to_earlier_content(store):
    first = store.ingest(
        page("第一版攻略", content_date="2026-09-20", etag='"first"'),
        game_version="1.2",
        version_basis="原文明确写1.2",
    )
    second = store.ingest(page("第二版攻略", retrieved_at="2026-09-28T10:00:00+00:00"))
    again = store.ingest(
        page("第一版攻略", retrieved_at="2026-09-29T10:00:00+00:00", content_date="2026-09-29"),
        game_version="1.3",
        version_basis="不可静默改旧版证据",
    )
    assert first["guide_id"] == second["guide_id"] == again["guide_id"]
    assert first["revision_id"] != second["revision_id"]
    assert first["revision_id"] == again["revision_id"]
    assert not first["deduplicated"] and again["deduplicated"]
    assert again["retrieved_at"] == first["retrieved_at"]
    assert again["content_date"] == "2026-09-20"
    assert again["game_version"] == "1.2"
    assert again["etag"] == '"first"'
    assert again["last_checked_at"] == "2026-09-29T10:00:00+00:00"
    document = store.get_document(first["guide_id"])
    assert len(document["revisions"]) == 2
    assert document["text"] == "第一版攻略"
    assert store.get_document(first["guide_id"], second["revision_id"])["text"] == "第二版攻略"


def test_distinct_origins_and_query_parameters_retain_separate_provenance(store):
    urls = [
        "https://example.com/a?mode=one",
        "https://example.com/a?mode=two",
        "https://other.example/a",
    ]
    saved = [
        store.ingest(page("相同攻略", url=url, final_url="https://example.com/final"))
        for url in urls
    ]
    assert len({item["guide_id"] for item in saved}) == 3
    assert {item["original_url"] for item in saved} == set(urls)
    assert all(item["url"] == "https://example.com/final" for item in saved)
    assert len({item["content_hash"] for item in saved}) == 1
    same = store.ingest(page("相同攻略", url=urls[0] + "#heading"))
    assert same["guide_id"] == saved[0]["guide_id"]


def test_last_checked_does_not_regress_when_earlier_read_finishes_late(store):
    saved = store.ingest(page(retrieved_at="2026-09-27T10:00:00+00:00"))
    again = store.ingest(page(retrieved_at="2026-09-27T18:00:00+09:00"))
    assert again["revision_id"] == saved["revision_id"]
    assert again["last_checked_at"] == "2026-09-27T10:00:00+00:00"


def test_reader_header_limits_match_storage(store):
    saved = store.ingest(page(etag="a" * 1024, last_modified="b" * 1024))
    assert saved["etag"] == "a" * 1024
    assert saved["last_modified"] == "b" * 1024


def test_same_retained_fifty_thousand_gets_partial_without_mutating_revision(store):
    body = "x" * 50_000
    first = store.ingest(page(body))
    immutable = store._db.execute(
        "SELECT metadata FROM guide_revisions WHERE revision_id=?", (first["revision_id"],)
    ).fetchone()[0]
    second = store.ingest(
        page(
            body,
            completeness="partial",
            completeness_reasons=["body_limit"],
            extracted_characters=50_008,
            retrieved_at="2026-09-28T10:00:00+00:00",
        )
    )
    assert second["revision_id"] == first["revision_id"]
    assert second["deduplicated"]
    assert second["completeness"] == "partial"
    assert second["completeness_reasons"] == ["body_limit"]
    assert second["extracted_characters"] == 50_008
    assert second["revision_coverage"]["completeness"] == "full"
    assert second["last_checked_at"] == "2026-09-28T10:00:00+00:00"
    assert (
        store._db.execute(
            "SELECT metadata FROM guide_revisions WHERE revision_id=?", (first["revision_id"],)
        ).fetchone()[0]
        == immutable
    )
    document = store.get_document(first["guide_id"])
    assert len(document["revisions"]) == 1
    assert document["text"] == body
    assert document["completeness"] == "partial"
    assert store.list_documents()[0]["completeness"] == "partial"


def test_new_unread_images_on_same_text_remain_partial_after_restart(paths):
    with GuideStore(paths, "one") as store:
        first = store.ingest(page())
        second = store.ingest(
            page(
                completeness="partial",
                completeness_reasons=["images_unread"],
                retrieved_at="2026-09-28T10:00:00+00:00",
            )
        )
        assert second["revision_id"] == first["revision_id"]
        assert second["completeness"] == "partial"
        assert second["completeness_reasons"] == ["images_unread"]
    with GuideStore(paths, "one") as store:
        document = store.get_document(first["guide_id"])
        assert document["completeness"] == "partial"
        assert document["completeness_reasons"] == ["images_unread"]
        assert document["revision_coverage"]["completeness"] == "full"


def test_rechecking_full_text_does_not_claim_formerly_unknown_material_is_captured(store):
    first = store.ingest(page(completeness="partial", completeness_reasons=["images_unread"]))
    second = store.ingest(page(retrieved_at="2026-09-28T10:00:00+00:00"))
    assert second["revision_id"] == first["revision_id"]
    assert second["completeness"] == "partial"
    assert second["completeness_reasons"] == ["images_unread"]
    assert second["last_checked_at"] == "2026-09-28T10:00:00+00:00"
    third = store.ingest(page("独立的新正文", retrieved_at="2026-09-29T10:00:00+00:00"))
    assert third["revision_id"] != first["revision_id"]
    assert third["completeness"] == "full"


def test_late_older_partial_evidence_is_kept_without_regressing_check_time(store):
    first = store.ingest(page(retrieved_at="2026-09-28T10:00:00+00:00"))
    second = store.ingest(page(completeness="partial", completeness_reasons=["images_unread"]))
    assert second["revision_id"] == first["revision_id"]
    assert second["last_checked_at"] == first["last_checked_at"]
    assert second["completeness"] == "partial"
    assert second["completeness_reasons"] == ["images_unread"]


def test_scope_cannot_read_mutate_or_rebuild_another_scopes_documents(paths):
    with GuideStore(paths, "one") as one, GuideStore(paths, "two") as two:
        saved = one.ingest(page())
        assert two.list_documents() == []
        for operation in (
            lambda: two.get_document(saved["guide_id"]),
            lambda: two.chunks(saved["revision_id"]),
            lambda: two.rebuild_index(saved["revision_id"]),
            lambda: two.set_protected(saved["guide_id"]),
        ):
            with pytest.raises(GuideAccessError):
                operation()
        other = two.ingest(page())
        assert other["guide_id"] != saved["guide_id"]
        assert one.get_document(saved["guide_id"])["text"] == page()["text"]


@pytest.mark.usefixtures("sandbox_compatible")
def test_restart_in_fresh_process_keeps_body_metadata_ids_and_chunks(paths):
    with GuideStore(paths, "one") as store:
        saved = store.ingest(page("历史正文" * 6000))
        chunks = store.chunks(saved["revision_id"])
    code = """
import json, sys
from ai_neko.config.paths import initialize_data_root
from ai_neko.memory.guides import GuideStore
with GuideStore(initialize_data_root(sys.argv[1]), 'one') as store:
    doc = store.list_documents()[0]
    print(json.dumps({'document': store.get_document(doc['guide_id']),
                      'chunks': store.chunks(doc['revision_id'])}, ensure_ascii=False))
"""
    process = subprocess.run(
        [sys.executable, "-c", code, str(paths.root)],
        check=True,
        capture_output=True,
        text=True,
        encoding="utf-8",
        env={**os.environ, "PYTHONIOENCODING": "utf-8"},
    )
    result = json.loads(process.stdout)
    assert result["document"]["text"] == "历史正文" * 6000
    assert result["document"]["guide_id"] == saved["guide_id"]
    assert result["chunks"] == chunks


def test_concurrent_ingestion_is_serialized_and_close_rejects_late_writes(paths):
    store = GuideStore(paths, "one")
    with ThreadPoolExecutor(max_workers=8) as executor:
        results = list(executor.map(lambda _: store.ingest(page()), range(32)))
    assert len({item["revision_id"] for item in results}) == 1
    assert sum(not item["deduplicated"] for item in results) == 1
    store.close()
    store.close()
    with pytest.raises(GuideConflictError, match="guides_closed"):
        store.ingest(page())
    with pytest.raises(GuideConflictError, match="guides_closed"):
        store.list_documents()


def test_quota_includes_body_and_chunks_and_lru_evicts_only_oldest(store, monkeypatch):
    first = store.ingest(page("a" * 4000, url="https://example.com/1"))
    assert store.logical_bytes() > 8000  # Full body and derived chunks are counted.
    second = store.ingest(page("b" * 4000, url="https://example.com/2"))
    quota = store.logical_bytes()
    monkeypatch.setattr(guides, "MAX_STORAGE_BYTES", quota)
    store.get_document(first["guide_id"])  # Reading makes first newer than second.
    third = store.ingest(page("c" * 4000, url="https://example.com/3"))
    assert third["evicted_guide_ids"] == [second["guide_id"]]
    assert store.logical_bytes() <= quota
    assert {row["guide_id"] for row in store.list_documents()} == {
        first["guide_id"],
        third["guide_id"],
    }
    with pytest.raises(GuideAccessError):
        store.chunks(second["revision_id"])


def test_oversized_single_document_never_evicts_existing_document(store, monkeypatch):
    first = store.ingest(page("small", url="https://example.com/1"))
    before = store.logical_bytes()
    monkeypatch.setattr(guides, "MAX_STORAGE_BYTES", before + 1000)
    with pytest.raises(GuideCapacityError, match="guide_too_large"):
        store.ingest(page("x" * 10_000, url="https://example.com/2"))
    assert store.logical_bytes() == before
    assert store.list_documents()[0]["guide_id"] == first["guide_id"]


def test_protected_guides_survive_quota_failure_and_restart(paths, monkeypatch):
    with GuideStore(paths, "one") as store:
        first = store.ingest(page("a" * 4000, url="https://example.com/1"))
        store.set_protected(first["guide_id"])
        quota = store.logical_bytes() + 100
    monkeypatch.setattr(guides, "MAX_STORAGE_BYTES", quota)
    with GuideStore(paths, "one") as store:
        with pytest.raises(GuideCapacityError, match="guide_capacity_exhausted"):
            store.ingest(page("b" * 4000, url="https://example.com/2"))
        assert store.get_document(first["guide_id"])["protected"]
        store.set_protected(first["guide_id"], False)
        saved = store.ingest(page("b" * 4000, url="https://example.com/2"))
        assert saved["evicted_guide_ids"] == [first["guide_id"]]


def test_quota_does_not_evict_other_scope_data(paths, monkeypatch):
    with GuideStore(paths, "one") as one, GuideStore(paths, "two") as two:
        first = one.ingest(page("a" * 4000))
        monkeypatch.setattr(guides, "MAX_STORAGE_BYTES", one.logical_bytes() + 100)
        with pytest.raises(GuideCapacityError):
            two.ingest(page("b" * 4000))
        assert one.get_document(first["guide_id"])["text"] == "a" * 4000
        assert two.list_documents() == []


def test_quota_failed_revision_does_not_change_current_body_or_evict_other_docs(store, monkeypatch):
    first = store.ingest(page("a" * 1000, url="https://example.com/1"))
    other = store.ingest(page("b" * 1000, url="https://example.com/2"))
    original = store.logical_bytes()
    monkeypatch.setattr(guides, "MAX_STORAGE_BYTES", original + 100)
    with pytest.raises(GuideCapacityError):
        store.ingest(page("c" * 20_000, url="https://example.com/1"))
    assert store.logical_bytes() == original
    assert store.get_document(first["guide_id"])["revision_id"] == first["revision_id"]
    assert store.get_document(other["guide_id"])["text"] == "b" * 1000


def test_migration_is_transactional_and_temporary_backup_cleanup(paths, monkeypatch):
    db_path = paths.guides / "guides.sqlite"
    with closing(sqlite3.connect(db_path)) as db:
        db.execute("CREATE TABLE legacy_marker (text TEXT)")
        db.execute("INSERT INTO legacy_marker VALUES ('preserve me')")
        db.execute("PRAGMA user_version=1")
        db.commit()
    original_steps = guides._MIGRATIONS

    def failure(db):
        guides._guides_v2(db)
        raise RuntimeError("synthetic interruption")

    monkeypatch.setattr(guides, "_MIGRATIONS", [(2, failure)])
    with pytest.raises(MigrationError, match="migration_to_2_failed"):
        GuideStore(paths, "one")
    backup = paths.backups / "guides-pre-migration-v2.sqlite"
    assert backup.is_file()
    with closing(sqlite3.connect(db_path)) as db:
        assert db.execute("PRAGMA user_version").fetchone()[0] == 1
        assert db.execute("SELECT text FROM legacy_marker").fetchone()[0] == "preserve me"
        assert not db.execute(
            "SELECT name FROM sqlite_master WHERE name='guide_documents'"
        ).fetchall()
    monkeypatch.setattr(guides, "_MIGRATIONS", original_steps)
    with GuideStore(paths, "one") as store:
        store.ingest(page())
    assert not backup.exists()
    assert not list(paths.backups.glob(".migration-*"))
    with closing(sqlite3.connect(db_path)) as db:
        assert db.execute("PRAGMA user_version").fetchone()[0] == 3
        assert db.execute("SELECT text FROM legacy_marker").fetchone()[0] == "preserve me"


def test_future_database_is_rejected_without_mutation(paths):
    db_path = paths.guides / "guides.sqlite"
    with closing(sqlite3.connect(db_path)) as db:
        db.execute("PRAGMA user_version=999")
        db.execute("CREATE TABLE future_content (body TEXT)")
        db.execute("INSERT INTO future_content VALUES ('not ours to mutate')")
        db.commit()
    before = db_path.read_bytes()
    with pytest.raises(MigrationError, match="newer_than_supported"):
        GuideStore(paths, "one")
    assert db_path.read_bytes() == before
    assert not list(paths.backups.iterdir())


def test_unversioned_old_check_table_adds_coverage_transactionally(paths):
    # An interrupted/pre-ledger schema can contain the old checks shape. Its
    # missing derived coverage must be seeded from the immutable revision.
    with GuideStore(paths, "one") as store:
        saved = store.ingest(page(completeness="partial", completeness_reasons=["images_unread"]))
    with closing(sqlite3.connect(paths.guides / "guides.sqlite")) as db:
        db.execute("ALTER TABLE guide_revision_checks RENAME TO old_checks")
        db.execute(
            "CREATE TABLE guide_revision_checks (scope TEXT NOT NULL,revision_id TEXT NOT NULL,"
            "last_checked_at TEXT NOT NULL,PRIMARY KEY(scope,revision_id),"
            "FOREIGN KEY(scope,revision_id) REFERENCES guide_revisions(scope,revision_id)"
            " ON DELETE CASCADE)"
        )
        db.execute(
            "INSERT INTO guide_revision_checks SELECT scope,revision_id,last_checked_at FROM old_checks"
        )
        db.execute("DROP TABLE old_checks")
        db.execute("PRAGMA user_version=0")
        db.commit()
    with GuideStore(paths, "one") as store:
        document = store.get_document(saved["guide_id"])
        assert document["completeness"] == "partial"
        assert document["completeness_reasons"] == ["images_unread"]
        assert document["revision_id"] == saved["revision_id"]
        assert store.logical_bytes() > 0
    assert not (paths.backups / "guides-pre-migration-v2.sqlite").exists()


@pytest.mark.parametrize("name", ["guides.sqlite", "guides.sqlite-wal", "guides.sqlite-journal"])
def test_redirected_database_files_are_rejected(paths, tmp_path, name):
    outside = tmp_path / "outside.sqlite"
    outside.write_bytes(b"foreign data")
    redirected = paths.guides / name
    try:
        redirected.symlink_to(outside)
    except OSError:
        pytest.skip("symlink creation not supported")
    with pytest.raises(DataRootError):
        GuideStore(paths, "one")
    assert outside.read_bytes() == b"foreign data"
