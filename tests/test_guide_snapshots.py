"""Independent guide restore validates first and never resurrects deleted sources."""

import asyncio
import hashlib
import json
import sqlite3
from contextlib import closing
from uuid import uuid4

import pytest
from test_memory_backup_api import api_client
from test_runtime import Store

from ai_neko.config.paths import DataRootError, initialize_data_root
from ai_neko.memory import guide_snapshots, guides
from ai_neko.memory.guide_snapshots import GuideSnapshots
from ai_neko.memory.guides import (
    GuideAccessError,
    GuideCapacityError,
    GuideConflictError,
    GuideInputError,
    GuideStore,
)
from ai_neko.memory.service import MemoryService
from ai_neko.runtime import SessionRuntime


def page(body="攻略正文：第八轮留金币。", url="https://example.com/guide"):
    return {
        "status": "read",
        "completeness": "full",
        "completeness_reasons": [],
        "text": body,
        "url": url,
        "title": "攻略",
        "retrieved_at": "2026-09-27T10:00:00Z",
        "headings": [],
    }


@pytest.fixture
def paths(tmp_path):
    return initialize_data_root(tmp_path / "data")


@pytest.fixture
def store(paths):
    with GuideStore(paths, "scope-a") as value:
        yield value


def select(store, document):
    return store.set_selection(
        "星棋",
        "Windows",
        "排位",
        document["guide_id"],
        document["revision_id"],
        expected_revision=store.revision(),
        request_id=uuid4().hex,
    )


def delete(store, document):
    return store.delete_document(
        document["guide_id"], expected_revision=store.revision(), request_id=uuid4().hex
    )


def mutate(paths, backup, statement, parameters=()):
    with closing(sqlite3.connect(paths.backups / backup["backup_id"])) as db:
        db.execute(statement, parameters)
        db.commit()


def test_roundtrip_fixed_adopted_revision_chunks_and_affected_union(store):
    snapshots = GuideSnapshots(store)
    first = store.ingest(page("第一版正文" * 300))
    selected = select(store, first)
    latest = store.ingest(page("第二版正文"))
    backup = snapshots.backup()
    assert backup["documents"] == 1 and backup["revisions"] == 2
    assert backup["selections"] == 1 and backup["revision"] == selected["revision"]
    replacement = store.ingest(page("其他攻略", "https://example.com/second"))
    select(store, replacement)
    old_revision = store.revision()
    preview = snapshots.inspect_restore(backup["backup_id"], expected_revision=old_revision)
    assert set(preview["guide_ids"]) == {first["guide_id"], replacement["guide_id"]}
    restored = snapshots.restore(
        backup["backup_id"], expected_revision=old_revision, request_id=uuid4().hex
    )
    assert restored["guide_ids"] == preview["guide_ids"]
    assert restored["revision"] == store.published_revision == old_revision + 1
    assert store.get_document(first["guide_id"])["revision_id"] == latest["revision_id"]
    adopted = store.control_snapshot()["selections"][0]
    assert adopted["revision_id"] == first["revision_id"]
    assert adopted["selection_revision"] == restored["revision"]
    assert "".join(row["text"] for row in store.chunks(first["revision_id"])) == "第一版正文" * 300
    with pytest.raises(GuideAccessError):
        store.get_document(replacement["guide_id"])
    store.set_selection(
        "星棋",
        "Windows",
        "排位",
        None,
        None,
        expected_revision=store.revision(),
        request_id=uuid4().hex,
    )
    assert store.get_document(first["guide_id"])["protected"] is False


def test_snapshot_excludes_personal_memory_credentials_requests_and_other_scope(paths):
    with MemoryService(paths) as memory, GuideStore(paths, "foreign") as foreign:
        memory.remember("不能导出的私人资料", source_id="manual-private")
        private_before = (paths.memory / "long-term.sqlite").read_bytes()
        guide = memory.guides.ingest(page())
        other = foreign.ingest(page("外部作用域正文", "https://example.com/foreign"))
        snapshots = GuideSnapshots(memory.guides)
        backup = snapshots.backup(request_id=uuid4().hex)
        data = (paths.backups / backup["backup_id"]).read_bytes()
        assert "不能导出的私人资料".encode() not in data
        assert "外部作用域正文".encode() not in data
        assert b"guide_requests" not in data and b"guide_chunks" not in data
        memory.guides.ingest(page("新加入正文", "https://example.com/new"))
        snapshots.restore(
            backup["backup_id"], expected_revision=memory.guides.revision(), request_id=uuid4().hex
        )
        assert (paths.memory / "long-term.sqlite").read_bytes() == private_before
        assert foreign.get_document(other["guide_id"])["text"] == "外部作用域正文"
        assert memory.guides.get_document(guide["guide_id"])["text"] == page()["text"]


def test_personal_memory_snapshot_does_not_touch_guide_authority(paths):
    with MemoryService(paths) as memory:
        backup = memory.backup()
        document = memory.guides.ingest(page())
        selected = select(memory.guides, document)
        memory.restore(backup["id"], expected_revision=memory.revision())
        assert memory.guides.get_document(document["guide_id"])["text"] == page()["text"]
        assert (
            memory.guides.control_snapshot()["selections"][0]["selection_revision"]
            == selected["revision"]
        )


def test_restore_preserves_later_tombstones_preventing_url_and_body_resurrection(store, paths):
    snapshots = GuideSnapshots(store)
    erased = store.ingest(page("必须被删除的正文"))
    other = store.ingest(page("保留的攻略", "https://example.com/keep"))
    select(store, erased)
    backup = snapshots.backup()
    deleted = delete(store, erased)
    restored = snapshots.restore(
        backup["backup_id"], expected_revision=store.revision(), request_id=uuid4().hex
    )
    assert restored["revision"] > deleted["revision"]
    assert [item["guide_id"] for item in store.list_documents()] == [other["guide_id"]]
    assert store.control_snapshot()["selections"] == []
    with pytest.raises(GuideConflictError):
        store.ingest(page("变更后的文本"))
    with pytest.raises(GuideConflictError):
        store.ingest(page("必须被删除的正文", "https://example.com/renamed"))
    assert snapshots.purge_deleted() == [backup["backup_id"]]
    assert not (paths.backups / backup["backup_id"]).exists()
    assert snapshots.purge_deleted() == []


def test_purge_only_affected_same_scope_snapshots(store, paths):
    snapshots = GuideSnapshots(store)
    clean = snapshots.backup()
    first = store.ingest(page())
    affected = snapshots.backup()
    with GuideStore(paths, "foreign") as foreign:
        foreign.ingest(page())
        other = GuideSnapshots(foreign).backup()
        delete(store, first)
        assert snapshots.purge_deleted() == [affected["backup_id"]]
        assert (paths.backups / other["backup_id"]).is_file()
        assert snapshots.list_backups() == [clean]


def test_same_request_restore_replays_after_new_controls_even_after_backup_deleted(store):
    snapshots = GuideSnapshots(store)
    first = store.ingest(page())
    backup = snapshots.backup()
    expected = store.revision()
    request = uuid4().hex
    restored = snapshots.restore(
        backup["backup_id"], expected_revision=expected, request_id=request
    )
    current = select(store, first)
    snapshots.delete_backup(backup["backup_id"])
    replay = snapshots.restore(backup["backup_id"], expected_revision=expected, request_id=request)
    assert replay["revision"] == restored["revision"]
    assert replay["replayed"] is True and replay["superseded"] is True
    assert store.revision() == current["revision"]
    assert store.control_snapshot()["selections"][0]["revision_id"] == first["revision_id"]
    assert snapshots.inspect_restore(
        backup["backup_id"], expected_revision=expected, request_id=request
    )["replayed"]
    with pytest.raises(GuideConflictError, match="request_conflict"):
        snapshots.restore(
            backup["backup_id"], expected_revision=store.revision(), request_id=request
        )


def test_backup_replays_after_deletion_without_recreating_body(store, paths):
    snapshots = GuideSnapshots(store)
    first = store.ingest(page())
    request = uuid4().hex
    backup = snapshots.backup(request_id=request)
    delete(store, first)
    snapshots.purge_deleted()
    replay = snapshots.backup(request_id=request)
    assert replay["backup_id"] == backup["backup_id"] and replay["replayed"]
    assert not (paths.backups / replay["backup_id"]).exists()


def test_backup_reuses_completed_file_after_crash_before_receipt(store, paths):
    snapshots = GuideSnapshots(store)
    store.ingest(page())
    request = uuid4().hex
    backup = snapshots.backup(request_id=request)
    store._db.execute(
        "DELETE FROM guide_requests WHERE scope=? AND request_id=?", (store.scope, request)
    )
    before = (paths.backups / backup["backup_id"]).read_bytes()
    store.ingest(page("变化后的正文"))
    assert snapshots.backup(request_id=request) == backup
    assert (paths.backups / backup["backup_id"]).read_bytes() == before


def test_restore_receipt_survives_restart(store, paths):
    snapshots = GuideSnapshots(store)
    store.ingest(page())
    backup = snapshots.backup()
    expected, request = store.revision(), uuid4().hex
    result = snapshots.restore(backup["backup_id"], expected_revision=expected, request_id=request)
    with GuideStore(paths, store.scope) as reopened:
        replay = GuideSnapshots(reopened).restore(
            backup["backup_id"], expected_revision=expected, request_id=request
        )
        assert replay["replayed"] and replay["revision"] == result["revision"]
        assert reopened.revision() == result["revision"]


def test_stale_preflight_and_restore_never_mutate(store):
    snapshots = GuideSnapshots(store)
    document = store.ingest(page())
    backup = snapshots.backup()
    before = store.revision()
    select(store, document)
    for operation in (
        lambda: snapshots.inspect_restore(
            backup["backup_id"], expected_revision=before, request_id=uuid4().hex
        ),
        lambda: snapshots.restore(
            backup["backup_id"], expected_revision=before, request_id=uuid4().hex
        ),
    ):
        with pytest.raises(GuideConflictError, match="revision_conflict"):
            operation()
    assert store.revision() == before + 1


@pytest.mark.parametrize(
    "statement,parameters",
    [
        ("PRAGMA user_version=999", ()),
        ("UPDATE snapshot_meta SET format_version=999", ()),
        ("UPDATE snapshot_meta SET complete=0", ()),
        ("CREATE VIEW poison AS SELECT load_extension('evil')", ()),
        ("UPDATE guide_revisions SET body='tampered'", ()),
        ("UPDATE guide_revisions SET metadata='[]'", ()),
        ("UPDATE guide_revisions SET metadata=?", ('{"headings": 123}',)),
        ("UPDATE guide_revisions SET body=x'0001'", ()),
        ("UPDATE guide_documents SET created_at='tomorrow'", ()),
        ("UPDATE guide_documents SET scope='foreign'", ()),
        ("UPDATE guide_documents SET canonical_url='http://127.0.0.1/'", ()),
        ("UPDATE guide_documents SET current_revision_id=?", ("revision-" + "0" * 32,)),
        ("DELETE FROM guide_revision_checks", ()),
        ("INSERT INTO guide_documents SELECT * FROM guide_documents", ()),
        ("UPDATE guide_selections SET selection_revision=999", ()),
        ("UPDATE guide_selections SET revision_id=?", ("revision-" + "0" * 32,)),
        ("UPDATE guide_selections SET game=''", ()),
        ("UPDATE guide_selections SET game='wrong-game'", ()),
        ("UPDATE snapshot_meta SET revision=9223372036854775807", ()),
        ("DELETE FROM guide_url_hashes", ()),
        ("UPDATE guide_url_hashes SET value='invalid-hash'", ()),
    ],
)
def test_malformed_snapshot_rejected_before_any_live_change(store, paths, statement, parameters):
    snapshots = GuideSnapshots(store)
    first = store.ingest(page())
    select(store, first)
    backup = snapshots.backup()
    mutate(paths, backup, statement, parameters)
    database = paths.guides / "guides.sqlite"
    before = database.read_bytes()
    with pytest.raises(GuideInputError):
        snapshots.restore(
            backup["backup_id"], expected_revision=store.revision(), request_id=uuid4().hex
        )
    assert database.read_bytes() == before


def test_cross_scope_snapshots_rejected_without_live_mutation(store, paths):
    snapshots = GuideSnapshots(store)
    first = store.ingest(page())
    backup = snapshots.backup()
    with GuideStore(paths, "other") as other:
        foreign = GuideSnapshots(other)
        assert foreign.list_backups() == []
        for operation in (
            lambda: foreign.inspect_restore(backup["backup_id"]),
            lambda: foreign.restore(
                backup["backup_id"], expected_revision=other.revision(), request_id=uuid4().hex
            ),
            lambda: foreign.delete_backup(backup["backup_id"]),
        ):
            with pytest.raises(GuideAccessError, match="wrong_scope"):
                operation()
        assert other.list_documents() == []
    assert store.get_document(first["guide_id"])["text"] == page()["text"]


@pytest.mark.parametrize("bound", ["bytes", "rows", "metadata"])
def test_snapshot_bounds_reject_without_live_mutation(store, paths, monkeypatch, bound):
    snapshots = GuideSnapshots(store)
    store.ingest(page())
    backup = snapshots.backup()
    if bound == "bytes":
        monkeypatch.setattr(guide_snapshots, "MAX_SNAPSHOT_BYTES", 10)
    elif bound == "rows":
        monkeypatch.setattr(guide_snapshots, "MAX_SNAPSHOT_ROWS", 1)
    else:
        mutate(
            paths,
            backup,
            "UPDATE guide_revisions SET metadata=?",
            ("x" * (guide_snapshots.MAX_METADATA_BYTES + 1),),
        )
    before = (paths.guides / "guides.sqlite").read_bytes()
    with pytest.raises(GuideInputError):
        snapshots.restore(
            backup["backup_id"], expected_revision=store.revision(), request_id=uuid4().hex
        )
    assert (paths.guides / "guides.sqlite").read_bytes() == before


def test_restore_over_global_quota_rolls_back_everything(store, paths, monkeypatch):
    snapshots = GuideSnapshots(store)
    first = store.ingest(page("long" * 1000))
    select(store, first)
    backup = snapshots.backup()
    current = store.ingest(page("other", "https://example.com/new"))
    select(store, current)
    before = (paths.guides / "guides.sqlite").read_bytes()
    monkeypatch.setattr(guides, "MAX_STORAGE_BYTES", 100)
    with pytest.raises(GuideCapacityError):
        snapshots.restore(
            backup["backup_id"], expected_revision=store.revision(), request_id=uuid4().hex
        )
    assert (paths.guides / "guides.sqlite").read_bytes() == before
    assert store.control_snapshot()["selections"][0]["guide_id"] == current["guide_id"]


def test_interrupted_restore_rolls_back_and_retry_executes_once(store, monkeypatch):
    snapshots = GuideSnapshots(store)
    store.ingest(page())
    backup = snapshots.backup()
    expected, request = store.revision(), uuid4().hex
    original = store._chunks_write

    def interrupted(*args):
        raise RuntimeError("synthetic crash during derived index")

    monkeypatch.setattr(store, "_chunks_write", interrupted)
    with pytest.raises(RuntimeError, match="synthetic crash"):
        snapshots.restore(backup["backup_id"], expected_revision=expected, request_id=request)
    assert store.revision() == expected
    assert store.preflight("restore", {"backup_id": backup["backup_id"]}, request, expected) is None
    monkeypatch.setattr(store, "_chunks_write", original)
    result = snapshots.restore(backup["backup_id"], expected_revision=expected, request_id=request)
    assert (
        snapshots.restore(backup["backup_id"], expected_revision=expected, request_id=request)[
            "revision"
        ]
        == result["revision"]
    )


@pytest.mark.parametrize(
    "backup_id",
    [
        "../guides-" + "a" * 32 + ".sqlite",
        "/tmp/guide.sqlite",
        "memory-" + "a" * 32 + ".sqlite",
        "guides-bad.sqlite",
    ],
)
def test_arbitrary_paths_rejected(store, backup_id):
    snapshots = GuideSnapshots(store)
    with pytest.raises(GuideInputError, match="invalid_guide_backup_id"):
        snapshots.inspect_restore(backup_id)


def test_redirected_snapshot_refused_without_touching_foreign_file(store, paths, tmp_path):
    outside = tmp_path / "outside.sqlite"
    outside.write_bytes(b"not ours")
    name = "guides-" + "a" * 32 + ".sqlite"
    try:
        (paths.backups / name).symlink_to(outside)
    except OSError:
        pytest.skip("symlinks unavailable")
    with pytest.raises(DataRootError):
        GuideSnapshots(store).delete_backup(name)
    assert outside.read_bytes() == b"not ours"


def test_corrupted_scope_identifiable_backup_is_removed_during_erasure(store, paths):
    snapshots = GuideSnapshots(store)
    first = store.ingest(page())
    backup = snapshots.backup()
    mutate(paths, backup, "UPDATE guide_revisions SET metadata='broken'")
    delete(store, first)
    assert snapshots.purge_deleted() == [backup["backup_id"]]


def test_unknown_corrupt_backup_blocks_cleanup_instead_of_claiming_success(store, paths):
    first = store.ingest(page())
    delete(store, first)
    (paths.backups / ("guides-" + "f" * 32 + ".sqlite")).write_bytes(b"unknown corrupt file")
    with pytest.raises(GuideInputError, match="unregistered_guide_backup_requires_manual_recovery"):
        GuideSnapshots(store).purge_deleted()


@pytest.mark.parametrize("with_request", [False, True])
def test_backup_ownership_commits_before_any_file_write(store, paths, monkeypatch, with_request):
    snapshots = GuideSnapshots(store)
    original = guide_snapshots.os.open
    observed = []

    def checking_open(path, flags, mode):
        with closing(sqlite3.connect(paths.guides / "guides.sqlite")) as reader:
            owner = reader.execute(
                "SELECT scope FROM guide_backup_registry WHERE backup_id=?", (path.name,)
            ).fetchone()
            assert owner is not None and owner[0] == store.scope
        observed.append(path.name)
        return original(path, flags, mode)

    monkeypatch.setattr(guide_snapshots.os, "open", checking_open)
    backup = snapshots.backup(request_id=uuid4().hex if with_request else None)
    assert observed == [backup["backup_id"]]
    assert b"guide_backup_registry" not in (paths.backups / backup["backup_id"]).read_bytes()


@pytest.mark.parametrize("damage", ["header", "oversized", "journal"])
def test_registered_corrupt_backup_can_be_listed_and_deleted_without_reading_content(
    store, paths, damage, monkeypatch
):
    snapshots = GuideSnapshots(store)
    backup = snapshots.backup()
    path = paths.backups / backup["backup_id"]
    if damage == "header":
        path.write_bytes(b"broken sqlite header")
    elif damage == "oversized":
        monkeypatch.setattr(guide_snapshots, "MAX_SNAPSHOT_BYTES", 1)
    else:
        (paths.backups / (backup["backup_id"] + "-journal")).write_bytes(b"interrupted write")
    listed = snapshots.list_backups()
    assert listed[0]["backup_id"] == backup["backup_id"]
    assert listed[0]["status"] == "corrupt" and listed[0]["restorable"] is False
    assert snapshots.delete_backup(backup["backup_id"])
    assert not path.exists()
    assert not list(paths.backups.glob(backup["backup_id"] + "-*"))


def test_purge_registered_bad_header_and_orphan_journal_preserves_other_scope(store, paths):
    snapshots = GuideSnapshots(store)
    document = store.ingest(page())
    corrupt = snapshots.backup()
    orphaned = snapshots.backup()
    (paths.backups / corrupt["backup_id"]).write_bytes(b"damaged header, unknown in-file scope")
    (paths.backups / orphaned["backup_id"]).unlink()
    orphan_journal = paths.backups / (orphaned["backup_id"] + "-journal")
    orphan_journal.write_bytes(b"orphaned guide body bytes")
    with GuideStore(paths, "foreign") as other:
        foreign = GuideSnapshots(other).backup()
        foreign_path = paths.backups / foreign["backup_id"]
        foreign_path.write_bytes(b"foreign corrupt bytes must remain untouched")
        delete(store, document)
        assert set(snapshots.purge_deleted()) == {corrupt["backup_id"], orphaned["backup_id"]}
        assert foreign_path.read_bytes() == b"foreign corrupt bytes must remain untouched"
        assert not orphan_journal.exists()
        assert snapshots.purge_deleted() == []


def test_failed_backup_leaves_durable_owner_for_crash_recovery(store, paths, monkeypatch):
    snapshots = GuideSnapshots(store)
    request = uuid4().hex
    original = snapshots._write_backup
    written = []

    def interrupted(backup_id):
        written.append(backup_id)
        (paths.backups / backup_id).write_bytes(b"partial sqlite header")
        (paths.backups / (backup_id + "-journal")).write_bytes(b"partial journal")
        raise RuntimeError("synthetic process death before backup receipt")

    monkeypatch.setattr(snapshots, "_write_backup", interrupted)
    with pytest.raises(RuntimeError, match="synthetic process death"):
        snapshots.backup(request_id=request)
    assert snapshots._owner(written[0])["scope"] == store.scope
    assert store.preflight("backup", {}, request, None) is None
    monkeypatch.setattr(snapshots, "_write_backup", original)
    completed = snapshots.backup(request_id=request)
    assert completed["backup_id"] == written[0]
    assert snapshots._read(completed["backup_id"])["snapshot_meta"]["complete"] == 1
    assert not list(paths.backups.glob(completed["backup_id"] + "-*"))


def test_registry_remains_local_during_restore_and_can_claim_legacy_valid_snapshot(store):
    snapshots = GuideSnapshots(store)
    backup = snapshots.backup()
    store._db.execute("DELETE FROM guide_backup_registry WHERE backup_id=?", (backup["backup_id"],))
    assert snapshots._owner(backup["backup_id"]) is None
    snapshots.restore(
        backup["backup_id"], expected_revision=store.revision(), request_id=uuid4().hex
    )
    assert snapshots._owner(backup["backup_id"]) is None
    assert snapshots.delete_backup(backup["backup_id"])
    assert snapshots._owner(backup["backup_id"])["scope"] == store.scope


def test_registered_backup_redirect_never_deletes_foreign_target(store, paths, tmp_path):
    snapshots = GuideSnapshots(store)
    backup = snapshots.backup()
    path = paths.backups / backup["backup_id"]
    path.unlink()
    outside = tmp_path / "foreign.sqlite"
    outside.write_bytes(b"external content")
    try:
        path.symlink_to(outside)
    except OSError:
        pytest.skip("symlinks unavailable")
    with pytest.raises(DataRootError):
        snapshots.delete_backup(backup["backup_id"])
    assert outside.read_bytes() == b"external content"


def test_corrupt_owned_snapshot_does_not_trap_runtime_api_or_restart(paths):
    async def run():
        runtime = SessionRuntime(paths, Store())
        try:
            document = runtime.memory.guides.ingest(page())
            async with api_client(runtime) as client:
                response = await client.post("/api/guide-backups", json={"request_id": uuid4().hex})
                assert response.status_code == 201
                backup = response.json()
                path = paths.backups / backup["backup_id"]
                path.write_bytes(b"corrupt sqlite header")
                response = await client.request(
                    "DELETE",
                    "/api/guides/" + document["guide_id"],
                    json={
                        "request_id": uuid4().hex,
                        "expected_revision": runtime.memory.guides.revision(),
                        "confirm": True,
                    },
                )
                assert response.status_code == 200, response.text
                assert not path.exists()
                assert not runtime._db.execute("SELECT 1 FROM memory_erasure").fetchone()
                assert not runtime._erasure_failed
                assert (await client.get("/api/guide-backups")).status_code == 200
                assert (await client.get("/api/memories")).status_code == 200
        finally:
            await runtime.close()
        restarted = SessionRuntime(paths, Store())
        try:
            assert restarted.memory.guides.list_documents() == []
            assert not restarted._db.execute("SELECT 1 FROM memory_erasure").fetchone()
        finally:
            await restarted.close()

    asyncio.run(run())


def test_restart_resumes_pending_erasure_with_registered_corrupt_snapshot(paths, monkeypatch):
    async def run():
        runtime = SessionRuntime(paths, Store())
        try:
            document = runtime.memory.guides.ingest(page())
            backup = runtime.guide_snapshots.backup()
            (paths.backups / backup["backup_id"]).write_bytes(b"corrupt sqlite header")

            def interrupted():
                raise OSError("synthetic cleanup interruption")

            monkeypatch.setattr(runtime.guide_snapshots, "purge_deleted", interrupted)
            with pytest.raises(OSError, match="synthetic cleanup interruption"):
                await runtime.guide_delete(
                    document["guide_id"],
                    {
                        "request_id": uuid4().hex,
                        "expected_revision": runtime.memory.guides.revision(),
                        "confirm": True,
                    },
                )
            assert runtime._db.execute("SELECT 1 FROM memory_erasure").fetchone()
        finally:
            await runtime.close()
        restarted = SessionRuntime(paths, Store())
        try:
            assert not (paths.backups / backup["backup_id"]).exists()
            assert not restarted._db.execute("SELECT 1 FROM memory_erasure").fetchone()
            assert restarted.memory.guides.list_documents() == []
        finally:
            await restarted.close()

    asyncio.run(run())


def test_restored_tombstones_bump_revision_and_block_other_snapshots(store, paths):
    snapshots = GuideSnapshots(store)
    first = store.ingest(page())
    old = snapshots.backup()
    delete(store, first)
    deleted = snapshots.backup()
    # Simulate a user rolling the authority back externally while retaining the
    # newer explicit snapshot: imported deletion barriers must still win.
    store._db.execute("DELETE FROM guide_tombstone_hashes WHERE scope=?", (store.scope,))
    store._db.execute("DELETE FROM guide_tombstones WHERE scope=?", (store.scope,))
    store._db.execute("UPDATE guide_control SET revision=0 WHERE scope=?", (store.scope,))
    store.ingest(page())
    result = snapshots.restore(deleted["backup_id"], expected_revision=0, request_id=uuid4().hex)
    assert result["revision"] > deleted["revision"]
    assert store.list_documents() == []
    assert snapshots.purge_deleted() == [old["backup_id"]]


def test_same_deleted_hash_can_belong_to_multiple_tombstones(store):
    snapshots = GuideSnapshots(store)
    first = store.ingest(page("identical", "https://example.com/a"))
    second = store.ingest(page("identical", "https://example.com/b"))
    delete(store, first)
    delete(store, second)
    backup = snapshots.backup()
    assert snapshots.inspect_restore(backup["backup_id"])["guide_ids"] == []
    snapshot = snapshots._read(backup["backup_id"])
    hashes = [row for row in snapshot["guide_tombstone_hashes"] if row["kind"] == "content"]
    assert len(hashes) == 2 and hashes[0]["value"] == hashlib.sha256(b"identical").hexdigest()


def test_snapshot_refuses_unexpected_metadata_fields_instead_of_importing_them(store, paths):
    snapshots = GuideSnapshots(store)
    store.ingest(page())
    backup = snapshots.backup()
    data = snapshots._read(backup["backup_id"])
    metadata = json.loads(data["guide_revisions"][0]["metadata"])
    metadata["private_credential"] = "must never import"
    mutate(paths, backup, "UPDATE guide_revisions SET metadata=?", (json.dumps(metadata),))
    with pytest.raises(GuideInputError):
        snapshots.inspect_restore(backup["backup_id"])
