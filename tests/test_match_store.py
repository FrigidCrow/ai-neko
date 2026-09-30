"""Actual SQLite match state, scoped evidence and control-request boundaries."""

import json
import sqlite3
from uuid import uuid4

import pytest

from ai_neko.runtime.match_store import (
    MatchAccessError,
    MatchConflictError,
    MatchInputError,
    MatchStore,
    conversation_matches_v5,
)


def database(path=":memory:"):
    db = sqlite3.connect(path)
    db.row_factory = sqlite3.Row
    db.execute("PRAGMA foreign_keys=ON")
    db.executescript(
        "CREATE TABLE sessions(id TEXT PRIMARY KEY,user_id TEXT,character_id TEXT);"
        "CREATE TABLE turns(id TEXT PRIMARY KEY,session_id TEXT REFERENCES sessions(id),input TEXT);"
    )
    conversation_matches_v5(db)
    return db


def session(db, user="local", character="default"):
    identifier = uuid4().hex
    with db:
        db.execute("INSERT INTO sessions VALUES (?,?,?)", (identifier, user, character))
    return identifier


def turn(db, session_id, text="我现在有四十金币。"):
    identifier = uuid4().hex
    with db:
        db.execute("INSERT INTO turns VALUES (?,?,?)", (identifier, session_id, text))
    return identifier


@pytest.fixture
def fixture():
    db = database()
    sid = session(db)
    store = MatchStore(db, json.dumps(["local", "default"]))
    yield db, store, sid
    db.close()


def control(
    store, sid, action="start", *, now=1000, request_id=None, expected_revision=None, **value
):
    if action in {"start", "new"}:
        value = {"game": "星棋", "platform": "Windows", "mode": "排位", **value}
    return store.control(
        sid,
        action,
        {
            "request_id": request_id or uuid4().hex,
            "expected_revision": store.revision(sid)
            if expected_revision is None
            else expected_revision,
            **value,
        },
        now=now,
    )


def selection(**changes):
    return {
        "guide_id": "guide-" + uuid4().hex,
        "revision_id": "revision-" + uuid4().hex,
        "selection_revision": 1,
        "game": "星棋",
        "platform": "Windows",
        "mode": "排位",
        **changes,
    }


def vision(store, sid, mid, tid, *, now=1010, frame_id="frame-1", text="金币四十", fields=None):
    return store.add_observation(
        sid,
        mid,
        tid,
        text,
        source_kind="vision",
        frame_id=frame_id,
        fields={"gold": 40} if fields is None else fields,
        now=now,
    )


def test_start_catalog_end_and_new_have_distinct_ids_and_revisions(fixture):
    db, store, sid = fixture
    assert store.catalog(sid) == {"revision": 0, "current": None, "matches": []}
    first = control(store, sid, goal="保留经济", game_version="1.0")
    mid = first["match_id"]
    assert first["revision"] == first["committed_revision"] == 1
    assert first["match"]["status"] == "active"
    assert first["match"]["goal"] == "保留经济"
    assert first["current"]["game_version"] == "1.0"
    assert first["current"]["evidence_after"] == 1000
    assert "scope" not in first["current"]
    tid = turn(db, sid)
    store.add_observation(sid, mid, tid, "四十金币", source_kind="user", now=1001)
    second = control(store, sid, "new", match_id=mid, now=1002)
    assert second["match_id"] != mid
    assert second["revision"] == 3
    assert store.get(sid, mid)["status"] == "ended"
    assert not store.read_context(sid, mid, now=1003)["observations"]
    assert second["current"]["goal"] == ""
    assert second["current"]["game_version"] is None
    ended = control(store, sid, "end", match_id=second["match_id"], now=1004)
    assert ended["revision"] == 4 and ended["current"] is None
    assert ended["match"]["status"] == "ended"
    assert len(store.catalog(sid)["matches"]) == 2
    assert control(store, sid, now=1005)["revision"] == 5


def test_start_replay_precedes_cas_and_rebuilds_current_view(fixture):
    _, store, sid = fixture
    request_id = uuid4().hex
    first = control(store, sid, request_id=request_id, expected_revision=0, goal="original")
    same = control(store, sid, request_id=request_id, expected_revision=0, goal="original")
    assert same["replayed"] and not same["superseded"]
    assert same["match_id"] == first["match_id"]
    control(store, sid, "update", match_id=first["match_id"], goal="changed", now=1001)
    replay = control(store, sid, request_id=request_id, expected_revision=0, goal="original")
    assert replay["replayed"] and replay["superseded"]
    assert replay["revision"] == 2 and replay["committed_revision"] == 1
    assert replay["match"]["goal"] == "changed"
    control(store, sid, "new", match_id=first["match_id"], now=1002)
    replay = control(store, sid, request_id=request_id, expected_revision=0, goal="original")
    assert replay["match"]["status"] == "ended"
    assert replay["current"]["match_id"] != first["match_id"]


@pytest.mark.parametrize("change", [{"goal": "changed"}, {"expected_revision": 1}])
def test_changed_request_fingerprint_fails_without_writes(fixture, change):
    db, store, sid = fixture
    rid = uuid4().hex
    control(store, sid, request_id=rid, expected_revision=0)
    before = db.total_changes
    with pytest.raises(MatchConflictError, match="request_conflict"):
        control(store, sid, request_id=rid, **({"expected_revision": 0} | change))
    assert db.total_changes == before
    assert store.revision(sid) == 1


def test_same_request_id_cannot_change_action(fixture):
    _, store, sid = fixture
    rid = uuid4().hex
    mid = control(store, sid, request_id=rid)["match_id"]
    with pytest.raises(MatchConflictError, match="request_conflict"):
        control(store, sid, "end", match_id=mid, request_id=rid, expected_revision=0)


@pytest.mark.parametrize("expected", [-1, True, 1.0, "0", None])
def test_invalid_expected_revision(fixture, expected):
    _, store, sid = fixture
    with pytest.raises(MatchInputError):
        store.control(
            sid,
            "start",
            {
                "request_id": uuid4().hex,
                "expected_revision": expected,
                "game": "星棋",
                "platform": "Windows",
                "mode": "排位",
            },
        )
    assert store.revision(sid) == 0


def test_stale_revision_rejects_before_new_match_or_observation(fixture):
    db, store, sid = fixture
    mid = control(store, sid)["match_id"]
    tid = turn(db, sid)
    with pytest.raises(MatchConflictError, match="revision_changed"):
        control(
            store, sid, "observe", match_id=mid, turn_id=tid, text="四十金币", expected_revision=0
        )
    assert not store.read_context(sid, mid, now=1000)["observations"]
    assert store.revision(sid) == 1


@pytest.mark.parametrize(
    "field,value",
    [
        ("request_id", "A" * 32),
        ("request_id", "a" * 31),
        ("game", ""),
        ("platform", " Windows"),
        ("mode", None),
        ("goal", "a" * 2001),
        ("game_version", ""),
        ("game", "bad\x00"),
        ("goal", "bad\ud800"),
    ],
)
def test_strict_control_values(fixture, field, value):
    _, store, sid = fixture
    args = {
        "request_id": uuid4().hex,
        "expected_revision": 0,
        "game": "星棋",
        "platform": "Windows",
        "mode": "排位",
        field: value,
    }
    with pytest.raises(MatchInputError):
        store.control(sid, "start", args)
    assert store.revision(sid) == 0


@pytest.mark.parametrize(
    "field",
    ["scope", "source_kind", "frame_id", "observed_at", "expires_at", "now", "fields", "selection"],
)
def test_client_cannot_supply_evidence_authority(fixture, field):
    db, store, sid = fixture
    mid = control(store, sid)["match_id"]
    tid = turn(db, sid)
    with pytest.raises(MatchInputError, match="control_fields"):
        store.control(
            sid,
            "observe",
            {
                "request_id": uuid4().hex,
                "expected_revision": 1,
                "match_id": mid,
                "turn_id": tid,
                "text": "四十金币",
                field: {},
            },
            now=1010,
        )
    assert store.revision(sid) == 1


def test_current_match_required_and_start_does_not_replace(fixture):
    _, store, sid = fixture
    mid = control(store, sid)["match_id"]
    with pytest.raises(MatchConflictError, match="already_active"):
        control(store, sid)
    new = control(store, sid, "new", match_id=mid)
    for action in ("end", "update", "close_observation"):
        payload = {"goal": "nope"} if action == "update" else {}
        with pytest.raises(MatchConflictError, match="no_longer_current"):
            control(store, sid, action, match_id=mid, **payload)
    assert store.current(sid)["match_id"] == new["match_id"]


def test_session_and_scope_isolation_with_opaque_errors(fixture):
    db, store, sid = fixture
    second = session(db)
    foreign_sid = session(db, "other")
    foreign = MatchStore(db, json.dumps(["other", "default"]))
    mid = control(store, sid)["match_id"]
    other_mid = control(foreign, foreign_sid)["match_id"]
    for reader in (store.catalog, store.current, store.revision):
        with pytest.raises(MatchAccessError):
            reader(foreign_sid)
    for reader in (store.get, store.read_context):
        with pytest.raises(MatchAccessError):
            reader(second, mid)
        with pytest.raises(MatchAccessError):
            reader(sid, other_mid)
    assert store.catalog(second)["revision"] == 0
    assert store.catalog(second)["matches"] == []
    rid = uuid4().hex
    control(store, second, request_id=rid)
    control(foreign, foreign_sid, "update", match_id=other_mid, goal="foreign", request_id=rid)


@pytest.mark.parametrize("scope", ["scope-a", "null", "{}", "[]", '["a"]', '["a",2]', '["", "b"]'])
def test_scope_requires_runtime_identity(fixture, scope):
    db, _, _ = fixture
    with pytest.raises(MatchInputError):
        MatchStore(db, scope)


def test_server_selection_is_fixed_and_only_sync_changes_it(fixture):
    _, store, sid = fixture
    selected = selection()
    result = store.control(
        sid,
        "start",
        {
            "request_id": uuid4().hex,
            "expected_revision": 0,
            "game": "星棋",
            "platform": "Windows",
            "mode": "排位",
        },
        selection=selected,
        now=1000,
    )
    mid = result["match_id"]
    assert store.current(sid)["selection"] == selected
    assert store.sync_selection(sid, selected, now=1010) == 1
    assert store.get(sid, mid)["evidence_after"] == 1000
    replacement = selection(selection_revision=8)
    assert store.sync_selection(sid, replacement, now=1020) == 2
    assert store.get(sid, mid)["selection"] == replacement
    assert store.get(sid, mid)["evidence_after"] == 1020
    assert store.sync_selection(sid, None, now=1030) == 3
    assert store.current(sid)["selection"] is None
    assert store.sync_selection(sid, None, now=1040) == 3


@pytest.mark.parametrize("change", [{"game": "另一游戏"}, {"platform": "mobile"}, {"mode": "休闲"}])
def test_selection_dimensions_conflict_rolls_back_start(fixture, change):
    db, store, sid = fixture
    with pytest.raises(MatchConflictError, match="dimensions_conflict"):
        store.control(
            sid,
            "start",
            {
                "request_id": uuid4().hex,
                "expected_revision": 0,
                "game": "星棋",
                "platform": "Windows",
                "mode": "排位",
            },
            selection=selection(**change),
            now=1000,
        )
    assert store.catalog(sid) == {"revision": 0, "current": None, "matches": []}
    assert db.execute("SELECT COUNT(*) FROM match_requests").fetchone()[0] == 0


def test_observe_keeps_exact_user_quote_and_server_timestamp(fixture):
    db, store, sid = fixture
    mid = control(store, sid)["match_id"]
    tid = turn(db, sid)
    result = control(store, sid, "observe", match_id=mid, turn_id=tid, text="四十金币", now=1010)
    assert result["revision"] == 2
    observation = store.read_context(sid, mid, now=1010)["observations"][0]
    assert observation["source_kind"] == "user_description"
    assert observation["text"] == "四十金币" and observation["turn_id"] == tid
    assert observation["observed_at"] == 1010 and observation["expires_at"] == 1130
    assert observation["frame_id"] is None and not observation["unknown"]
    assert observation["source"]["kind"] == "user_description"


def test_observation_cannot_launder_other_turns_or_non_user_text(fixture):
    db, store, sid = fixture
    mid = control(store, sid)["match_id"]
    tid = turn(db, sid)
    foreign = turn(db, session(db))
    with pytest.raises(MatchAccessError):
        store.add_observation(sid, mid, foreign, "四十金币", source_kind="user", now=1010)
    with pytest.raises(MatchInputError, match="user_quote"):
        store.add_observation(sid, mid, tid, "模型建议立即升级", source_kind="user", now=1010)
    with db:
        db.execute("UPDATE turns SET input='[已遗忘的对话]' WHERE id=?", (tid,))
    with pytest.raises(MatchAccessError):
        store.add_observation(sid, mid, tid, "四十金币", source_kind="user", now=1010)
    assert store.revision(sid) == 1


@pytest.mark.parametrize(
    "age,valid", [(0, True), (119.999, True), (120, True), (120.001, False), (-0.001, False)]
)
def test_context_uses_original_observed_time_and_inclusive_120s(fixture, age, valid):
    db, store, sid = fixture
    mid = control(store, sid)["match_id"]
    tid = turn(db, sid)
    store.add_observation(sid, mid, tid, "四十金币", source_kind="user", now=1010)
    before = db.total_changes
    assert bool(store.read_context(sid, mid, now=1010 + age)["observations"]) is valid
    assert db.total_changes == before
    assert store.revision(sid) == 2


def test_new_unknown_frame_replaces_every_old_visual_field_without_renewal(fixture):
    db, store, sid = fixture
    mid = control(store, sid)["match_id"]
    tid = turn(db, sid)
    vision(store, sid, mid, tid, fields={"gold": 40, "level": 5})
    vision(store, sid, mid, tid, now=1020, frame_id="frame-2", text="看不清", fields={})
    items = store.read_context(sid, mid, now=1020)["observations"]
    assert len(items) == 1
    assert items[0]["frame_id"] == "frame-2" and items[0]["fields"] == {}
    assert items[0]["unknown"] and items[0]["observed_at"] == 1020
    assert (
        db.execute("SELECT active FROM match_observations WHERE frame_id='frame-1'").fetchone()[0]
        == 0
    )
    assert not store.read_context(sid, mid, now=1141)["observations"]


def test_partial_new_frame_never_carries_old_fields(fixture):
    db, store, sid = fixture
    mid = control(store, sid)["match_id"]
    tid = turn(db, sid)
    vision(store, sid, mid, tid, fields={"gold": 40, "level": 5})
    vision(store, sid, mid, tid, now=1020, frame_id="frame-2", fields={"level": 6, "gold": None})
    item = store.read_context(sid, mid, now=1021)["observations"][0]
    assert item["fields"] == {"level": 6, "gold": None}
    assert not item["unknown"]


@pytest.mark.parametrize(
    "fields,unknown",
    [({}, True), ({"gold": None}, True), ({"gold": 0}, False), ({"alive": False}, False)],
)
def test_visual_unknown_preserves_zero_and_false(fixture, fields, unknown):
    db, store, sid = fixture
    mid = control(store, sid)["match_id"]
    vision(store, sid, mid, turn(db, sid), text="", fields=fields)
    item = store.read_context(sid, mid, now=1010)["observations"][0]
    assert item["unknown"] is unknown
    assert item["text"]


def test_new_user_description_replaces_user_fields_but_keeps_recent_vision(fixture):
    db, store, sid = fixture
    mid = control(store, sid)["match_id"]
    first = turn(db, sid)
    second = turn(db, sid, "我已经升级了。")
    store.add_observation(
        sid, mid, first, "四十金币", source_kind="user", fields={"gold": 40}, now=1001
    )
    vision(store, sid, mid, first, now=1002)
    store.add_observation(
        sid, mid, second, "我已经升级了。", source_kind="user_confirmation", now=1003
    )
    items = store.read_context(sid, mid, now=1004)["observations"]
    assert [item["source_kind"] for item in items] == ["vision", "user_correction"]
    assert items[1]["fields"] == {} and items[1]["text"] == "我已经升级了。"


def test_older_observation_cannot_replace_newer_same_kind(fixture):
    db, store, sid = fixture
    mid = control(store, sid)["match_id"]
    tid = turn(db, sid)
    vision(store, sid, mid, tid, now=1020)
    with pytest.raises(MatchConflictError, match="superseded"):
        store.add_observation(
            sid,
            mid,
            tid,
            "迟到画面",
            source_kind="vision",
            frame_id="older",
            observed_at=1010,
            now=1021,
        )
    assert store.revision(sid) == 2
    assert store.read_context(sid, mid, now=1021)["observations"][0]["frame_id"] == "frame-1"


def test_close_observation_clears_visual_only_and_allows_new_valid_frame(fixture):
    db, store, sid = fixture
    mid = control(store, sid)["match_id"]
    tid = turn(db, sid)
    store.add_observation(sid, mid, tid, "四十金币", source_kind="user", now=1001)
    vision(store, sid, mid, tid, now=1002)
    closed = control(store, sid, "close_observation", match_id=mid, now=1003)
    assert closed["revision"] == 4
    assert [
        item["source_kind"] for item in store.read_context(sid, mid, now=1004)["observations"]
    ] == ["user_description"]
    vision(store, sid, mid, tid, now=1005, frame_id="new")
    assert len(store.read_context(sid, mid, now=1006)["observations"]) == 2


def test_restart_is_scoped_invalidates_evidence_and_records_advice_boundary(fixture):
    db, store, sid = fixture
    mid = control(store, sid)["match_id"]
    tid = turn(db, sid)
    vision(store, sid, mid, tid)
    other_sid = session(db, "other")
    other = MatchStore(db, json.dumps(["other", "default"]))
    other_mid = control(other, other_sid)["match_id"]
    assert store.restart(now=1020) == 1
    assert store.current(sid)["status"] == "needs_update"
    assert store.current(sid)["evidence_after"] == 1020
    assert store.current(sid)["state_revision"] == 3
    assert not store.read_context(sid, mid, now=1021)["observations"]
    assert other.current(other_sid)["status"] == "active"
    assert other.get(other_sid, other_mid)["state_revision"] == 1
    store.add_observation(sid, mid, tid, "四十金币", source_kind="user", now=1030)
    assert store.current(sid)["status"] == "active"
    assert store.current(sid)["evidence_after"] == 1020
    assert len(store.read_context(sid, mid, now=1031)["observations"]) == 1


def test_ended_match_cannot_be_reactivated_by_late_evidence(fixture):
    db, store, sid = fixture
    mid = control(store, sid)["match_id"]
    tid = turn(db, sid)
    control(store, sid, "end", match_id=mid)
    with pytest.raises(MatchConflictError, match="no_longer_current"):
        vision(store, sid, mid, tid)
    assert store.restart(now=1020) == 0


def test_erase_removes_inactive_and_current_copies_but_replay_cannot_restore_them(fixture):
    db, store, sid = fixture
    mid = control(store, sid)["match_id"]
    tid = turn(db, sid)
    rid = uuid4().hex
    control(
        store,
        sid,
        "observe",
        match_id=mid,
        turn_id=tid,
        text="四十金币",
        now=1001,
        request_id=rid,
        expected_revision=1,
    )
    vision(store, sid, mid, tid, now=1002)
    vision(store, sid, mid, tid, now=1003, frame_id="second")
    assert store.erase_observations([tid], now=1004) == 3
    assert store.revision(sid) == 5
    replay = control(
        store,
        sid,
        "observe",
        match_id=mid,
        turn_id=tid,
        text="四十金币",
        now=1005,
        request_id=rid,
        expected_revision=1,
    )
    assert replay["replayed"] and replay["superseded"]
    assert not store.read_context(sid, mid, now=1006)["observations"]
    assert db.execute("SELECT COUNT(*) FROM match_observations").fetchone()[0] == 0
    assert "四十金币" not in " ".join(
        str(tuple(row)) for row in db.execute("SELECT * FROM match_requests")
    )
    assert store.erase_observations([tid], now=1007) == 0
    assert store.revision(sid) == 5


def test_erase_cannot_affect_foreign_scope(fixture):
    db, store, sid = fixture
    mid = control(store, sid)["match_id"]
    local_tid = turn(db, sid)
    vision(store, sid, mid, local_tid)
    foreign_sid = session(db, "other")
    foreign = MatchStore(db, json.dumps(["other", "default"]))
    other_mid = control(foreign, foreign_sid)["match_id"]
    foreign_tid = turn(db, foreign_sid)
    vision(foreign, foreign_sid, other_mid, foreign_tid)
    assert store.erase_observations([local_tid, foreign_tid], now=1020) == 1
    assert foreign.revision(foreign_sid) == 2
    assert len(foreign.read_context(foreign_sid, other_mid, now=1021)["observations"]) == 1


def test_request_ledger_never_contains_arbitrary_content(fixture):
    db, store, sid = fixture
    marker = "PRIVATE-CONTENT-unique"
    mid = control(store, sid, goal=marker)["match_id"]
    tid = turn(db, sid, marker)
    control(store, sid, "observe", match_id=mid, turn_id=tid, text=marker)
    rows = [tuple(row) for row in db.execute("SELECT * FROM match_requests")]
    assert marker not in repr(rows)
    assert len(rows) == 2
    assert {row[3] for row in rows} == {"start", "observe"}


@pytest.mark.parametrize(
    "changes",
    [
        {"source_kind": "assistant"},
        {"source_kind": "guide"},
        {"observed_at": True},
        {"observed_at": float("nan")},
        {"observed_at": float("inf")},
        {"observed_at": 1201},
        {"observed_at": 1079},
        {"frame_id": None},
        {"fields": {"gold": float("nan")}},
        {"fields": []},
        {"fields": {"a": object()}},
    ],
)
def test_invalid_evidence_fails_atomically(fixture, changes):
    db, store, sid = fixture
    mid = control(store, sid)["match_id"]
    tid = turn(db, sid)
    args = {"source_kind": "vision", "frame_id": "frame", "now": 1200, **changes}
    with pytest.raises(MatchInputError):
        store.add_observation(sid, mid, tid, "画面", **args)
    assert store.revision(sid) == 1
    assert db.execute("SELECT COUNT(*) FROM match_observations").fetchone()[0] == 0


def test_outer_transaction_is_not_committed_and_rollback_undoes_everything(fixture):
    db, store, sid = fixture
    mid = control(store, sid)["match_id"]
    tid = uuid4().hex
    db.execute("BEGIN")
    db.execute("INSERT INTO turns VALUES (?,?,?)", (tid, sid, "金币四十"))
    assert (
        store.add_observation(sid, mid, tid, "金币四十", source_kind="user", now=1010, commit=False)
        == 2
    )
    assert db.in_transaction
    db.rollback()
    assert store.revision(sid) == 1
    assert not store.read_context(sid, mid, now=1011)["observations"]
    assert db.execute("SELECT * FROM turns WHERE id=?", (tid,)).fetchone() is None
    with pytest.raises(MatchConflictError, match="outer_transaction_required"):
        store.restart(now=1020, commit=False)


def test_default_mutation_also_preserves_existing_outer_transaction(fixture):
    db, store, sid = fixture
    db.execute("BEGIN")
    control(store, sid)
    assert db.in_transaction
    db.rollback()
    assert store.current(sid) is None and store.revision(sid) == 0


def test_failed_mutation_rolls_back_inner_work_without_rolling_back_caller(fixture):
    db, store, sid = fixture
    mid = control(store, sid)["match_id"]
    db.execute(
        "CREATE TRIGGER fail_receipt BEFORE INSERT ON match_requests BEGIN SELECT RAISE(ABORT,'synthetic failure'); END"
    )
    with pytest.raises(sqlite3.IntegrityError, match="synthetic failure"):
        control(store, sid, "new", match_id=mid)
    assert store.current(sid)["match_id"] == mid
    assert store.get(sid, mid)["status"] == "active"
    assert store.revision(sid) == 1
    assert len(store.catalog(sid)["matches"]) == 1
    db.execute("BEGIN")
    tid = uuid4().hex
    db.execute("INSERT INTO turns VALUES (?,?,?)", (tid, sid, "保留调用方写入"))
    with pytest.raises(sqlite3.IntegrityError):
        control(store, sid, "end", match_id=mid)
    assert db.in_transaction
    db.commit()
    assert store.current(sid)["status"] == "active"
    assert (
        db.execute("SELECT input FROM turns WHERE id=?", (tid,)).fetchone()[0] == "保留调用方写入"
    )


def test_file_reopen_preserves_control_idempotency_and_never_auto_restarts(tmp_path):
    path = tmp_path / "conversation.sqlite"
    db = database(path)
    sid = session(db)
    store = MatchStore(db, '["local", "default"]')
    rid = uuid4().hex
    mid = control(store, sid, request_id=rid, expected_revision=0)["match_id"]
    vision(store, sid, mid, turn(db, sid))
    db.close()
    db = sqlite3.connect(path)
    try:
        store = MatchStore(db, '["local","default"]')
        assert store.current(sid)["match_id"] == mid
        assert store.revision(sid) == 2
        assert control(store, sid, request_id=rid, expected_revision=0)["replayed"]
        store.restart(now=1020)
        assert store.current(sid)["status"] == "needs_update"
        assert not store.read_context(sid, mid, now=1021)["observations"]
    finally:
        db.close()


def test_migration_is_idempotent_and_does_not_commit_caller_transaction(fixture):
    db, store, sid = fixture
    control(store, sid)
    db.execute("BEGIN")
    conversation_matches_v5(db)
    assert db.in_transaction
    db.rollback()
    assert store.revision(sid) == 1
    assert db.execute("PRAGMA foreign_key_check").fetchall() == []


def test_migration_schema_can_be_rolled_back_without_partial_tables():
    db = sqlite3.connect(":memory:")
    try:
        db.execute("CREATE TABLE sessions(id TEXT PRIMARY KEY)")
        db.execute("CREATE TABLE turns(id TEXT PRIMARY KEY)")
        db.execute("BEGIN")
        conversation_matches_v5(db)
        db.rollback()
        tables = {row[0] for row in db.execute("SELECT name FROM sqlite_master WHERE type='table'")}
        assert tables == {"sessions", "turns"}
    finally:
        db.close()


def test_goal_limit_and_nullable_version_update_preserve_observation_deadline(fixture):
    db, store, sid = fixture
    mid = control(store, sid, goal="目" * 2000, game_version="1.0")["match_id"]
    tid = turn(db, sid)
    vision(store, sid, mid, tid, now=1010)
    changed = control(store, sid, "update", match_id=mid, goal="", game_version=None, now=1020)
    assert changed["match"]["goal"] == "" and changed["match"]["game_version"] is None
    assert changed["revision"] == 3
    assert store.read_context(sid, mid, now=1129)["observations"][0]["expires_at"] == 1130
    assert not store.read_context(sid, mid, now=1131)["observations"]
    with pytest.raises(MatchInputError, match="empty_match_update"):
        control(store, sid, "update", match_id=mid)
    assert store.revision(sid) == 3


def test_late_frame_from_before_restart_cannot_restore_previous_context(fixture):
    db, store, sid = fixture
    mid = control(store, sid)["match_id"]
    tid = turn(db, sid)
    store.restart(now=1020)
    with pytest.raises(MatchInputError, match="predates_current_context"):
        store.add_observation(
            sid,
            mid,
            tid,
            "重启前画面",
            source_kind="vision",
            frame_id="old",
            observed_at=1019,
            now=1021,
        )
    assert store.current(sid)["status"] == "needs_update"
    assert store.revision(sid) == 2
    vision(store, sid, mid, tid, now=1022, frame_id="new")
    assert store.current(sid)["status"] == "active"
    assert store.current(sid)["evidence_after"] == 1020


def test_selection_change_preserves_fresh_observations_but_old_frame_cannot_commit(fixture):
    db, store, sid = fixture
    mid = control(store, sid)["match_id"]
    tid = turn(db, sid)
    vision(store, sid, mid, tid)
    store.sync_selection(sid, selection(), now=1020)
    assert len(store.read_context(sid, mid, now=1021)["observations"]) == 1
    with pytest.raises(MatchInputError, match="predates_current_context"):
        store.add_observation(
            sid,
            mid,
            tid,
            "旧任务的帧",
            source_kind="vision",
            frame_id="old",
            observed_at=1019,
            now=1021,
        )
    assert store.revision(sid) == 3


def test_inactive_observations_do_not_resurface_after_current_one_is_erased(fixture):
    db, store, sid = fixture
    mid = control(store, sid)["match_id"]
    first = turn(db, sid, "金币四十")
    second = turn(db, sid, "金币三十")
    store.add_observation(sid, mid, first, "金币四十", source_kind="user", now=1010)
    store.add_observation(sid, mid, second, "金币三十", source_kind="user", now=1020)
    assert store.erase_observations([second], now=1030) == 1
    assert not store.read_context(sid, mid, now=1031)["observations"]
    assert (
        db.execute("SELECT active FROM match_observations WHERE turn_id=?", (first,)).fetchone()[0]
        == 0
    )


def test_observation_insert_failure_does_not_deactivate_previous_or_bump_revision(fixture):
    db, store, sid = fixture
    mid = control(store, sid)["match_id"]
    tid = turn(db, sid)
    vision(store, sid, mid, tid)
    db.execute(
        "CREATE TRIGGER fail_observation BEFORE INSERT ON match_observations BEGIN SELECT RAISE(ABORT,'synthetic observation failure'); END"
    )
    with pytest.raises(sqlite3.IntegrityError, match="synthetic observation failure"):
        vision(store, sid, mid, tid, now=1020, frame_id="second")
    assert store.revision(sid) == 2
    assert store.read_context(sid, mid, now=1021)["observations"][0]["frame_id"] == "frame-1"


def test_observe_retry_after_turn_erasure_returns_receipt_without_resurrecting(fixture):
    db, store, sid = fixture
    mid = control(store, sid)["match_id"]
    tid = turn(db, sid)
    rid = uuid4().hex
    control(
        store,
        sid,
        "observe",
        match_id=mid,
        turn_id=tid,
        text="四十金币",
        request_id=rid,
        expected_revision=1,
        now=1010,
    )
    store.erase_observations([tid], now=1020)
    with db:
        db.execute("UPDATE turns SET input='[已遗忘的对话]' WHERE id=?", (tid,))
    replay = control(
        store,
        sid,
        "observe",
        match_id=mid,
        turn_id=tid,
        text="四十金币",
        request_id=rid,
        expected_revision=1,
        now=1030,
    )
    assert replay["replayed"] and replay["superseded"]
    assert not store.read_context(sid, mid, now=1030)["observations"]


def test_store_has_no_assistant_advice_or_media_persistence_tables(fixture):
    db, _, _ = fixture
    names = {row[0] for row in db.execute("SELECT name FROM sqlite_master WHERE type='table'")}
    assert names == {
        "sessions",
        "turns",
        "matches",
        "match_control",
        "match_requests",
        "match_observations",
    }
    for name in names - {"sessions", "turns"}:
        columns = {row[1] for row in db.execute(f"PRAGMA table_info({name})")}
        assert not columns.intersection({"audio", "audio_base64", "data_url", "image", "advice"})


def test_preflight_is_read_only_and_commit_rechecks_cas(fixture):
    db, store, sid = fixture
    value = {
        "request_id": uuid4().hex,
        "expected_revision": 0,
        "game": "星棋",
        "platform": "Windows",
        "mode": "排位",
    }
    before = db.total_changes
    assert store.preflight(sid, "start", value) is None
    assert db.total_changes == before and not db.in_transaction
    control(store, sid)
    with pytest.raises(MatchConflictError, match="revision_changed"):
        store.control(sid, "start", value, now=1001)
    assert len(store.catalog(sid)["matches"]) == 1


def test_preflight_replays_after_forgetting_and_matches_control_result(fixture):
    db, store, sid = fixture
    mid = control(store, sid)["match_id"]
    tid = turn(db, sid)
    value = {
        "request_id": uuid4().hex,
        "expected_revision": 1,
        "match_id": mid,
        "turn_id": tid,
        "text": "四十金币",
    }
    store.control(sid, "observe", value, now=1010)
    store.erase_observations([tid], now=1020)
    with db:
        db.execute("UPDATE turns SET input='[已遗忘的对话]' WHERE id=?", (tid,))
    before = db.total_changes
    preview = store.preflight(sid, "observe", value)
    assert preview["replayed"] and preview["superseded"]
    assert db.total_changes == before and not db.in_transaction
    assert preview == store.control(sid, "observe", value, now=1030)
    assert db.total_changes == before
    with pytest.raises(MatchConflictError, match="request_conflict"):
        store.preflight(sid, "observe", value | {"text": "三十金币"})


def test_revision_exhaustion_cannot_convert_integer_control_to_float(fixture):
    db, store, sid = fixture
    mid = control(store, sid)["match_id"]
    with db:
        db.execute("UPDATE match_control SET revision=? WHERE session_id=?", (2**63 - 2, sid))
    with pytest.raises(MatchConflictError, match="revision_exhausted"):
        control(store, sid, "end", match_id=mid)
    assert store.current(sid)["status"] == "active"
    assert type(store.revision(sid)) is int


def test_forget_goals_updates_live_revision_and_replay_cannot_restore_goal(fixture):
    db, store, sid = fixture
    rid = uuid4().hex
    mid = control(store, sid, goal="需要遗忘的目标", request_id=rid, expected_revision=0)[
        "match_id"
    ]
    assert store.forget_goals([mid, mid], now=1010) == 1
    current = store.current(sid)
    assert current["goal"] == ""
    assert current["state_revision"] == store.revision(sid) == 2
    assert current["evidence_after"] == 1010
    assert current["status"] == "active"
    before = db.total_changes
    assert store.forget_goals([mid], now=1020) == 0
    assert db.total_changes == before and store.revision(sid) == 2
    replay = control(store, sid, goal="需要遗忘的目标", request_id=rid, expected_revision=0)
    assert replay["replayed"] and replay["superseded"] and replay["match"]["goal"] == ""
    assert "需要遗忘的目标" not in repr(
        [tuple(row) for row in db.execute("SELECT * FROM match_requests")]
    )


def test_forget_goals_on_ended_match_invalidates_current_advice_without_reviving(fixture):
    _, store, sid = fixture
    old = control(store, sid, goal="旧局目标")["match_id"]
    new = control(store, sid, "new", match_id=old, goal="新局保留目标", now=1010)["match_id"]
    assert store.forget_goals([old], now=1020) == 1
    historical = store.get(sid, old)
    assert historical["status"] == "ended" and historical["goal"] == ""
    assert historical["state_revision"] == 3
    current = store.current(sid)
    assert current["match_id"] == new and current["goal"] == "新局保留目标"
    assert current["state_revision"] == store.revision(sid) == 3
    assert current["evidence_after"] == 1020


def test_forget_goals_when_no_current_keeps_match_ended(fixture):
    _, store, sid = fixture
    mid = control(store, sid, goal="目标")["match_id"]
    control(store, sid, "end", match_id=mid, now=1010)
    assert store.forget_goals([mid], now=1020) == 1
    assert store.current(sid) is None
    assert store.get(sid, mid)["status"] == "ended"
    assert store.get(sid, mid)["goal"] == "" and store.revision(sid) == 3


def test_forget_goals_bumps_each_session_once_and_ignores_foreign_or_absent_ids(fixture):
    db, store, sid = fixture
    old = control(store, sid, goal="old")["match_id"]
    new = control(store, sid, "new", match_id=old, goal="new", now=1010)["match_id"]
    other_sid = session(db)
    other = control(store, other_sid, goal="other")["match_id"]
    foreign_sid = session(db, "other")
    foreign = MatchStore(db, '["other", "default"]')
    foreign_mid = control(foreign, foreign_sid, goal="foreign")["match_id"]
    assert store.forget_goals([old, new, other, foreign_mid, "match-" + uuid4().hex], now=1020) == 3
    assert store.revision(sid) == 3 and store.revision(other_sid) == 2
    assert foreign.get(foreign_sid, foreign_mid)["goal"] == "foreign"
    assert foreign.revision(foreign_sid) == 1


def test_forget_goals_is_atomic_with_caller_transaction(fixture):
    db, store, sid = fixture
    mid = control(store, sid, goal="still here")["match_id"]
    db.execute("BEGIN")
    assert store.forget_goals([mid], now=1010, commit=False) == 1
    assert db.in_transaction and store.current(sid)["goal"] == ""
    db.rollback()
    assert store.current(sid)["goal"] == "still here" and store.revision(sid) == 1
    with pytest.raises(MatchConflictError, match="outer_transaction_required"):
        store.forget_goals([mid], now=1010, commit=False)


def test_forget_goals_failure_rolls_back_goal_and_current_boundary_together(fixture):
    db, store, sid = fixture
    old = control(store, sid, goal="old goal")["match_id"]
    current = control(store, sid, "new", match_id=old, goal="current goal", now=1010)["match_id"]
    db.execute(
        "CREATE TRIGGER fail_current_revision BEFORE UPDATE ON matches "
        "WHEN OLD.status='active' BEGIN SELECT RAISE(ABORT,'synthetic failure'); END"
    )
    with pytest.raises(sqlite3.IntegrityError, match="synthetic failure"):
        store.forget_goals([old], now=1020)
    assert store.get(sid, old)["goal"] == "old goal"
    assert store.get(sid, old)["status"] == "ended"
    assert store.get(sid, current)["evidence_after"] == 1010
    assert store.revision(sid) == 2


@pytest.mark.parametrize("ids", ["match-" + "a" * 32, [None], ["wrong"], ["match-" + "A" * 32]])
def test_forget_goals_rejects_malformed_input_without_mutation(fixture, ids):
    _, store, sid = fixture
    control(store, sid, goal="goal")
    with pytest.raises(MatchInputError):
        store.forget_goals(ids, now=1010)
    assert store.current(sid)["goal"] == "goal" and store.revision(sid) == 1


def test_selection_normalization_matches_retriever_casefold_and_script_fold(fixture):
    _, store, sid = fixture
    adopted = selection(game="星際棋局", platform="WINDOWS", mode="標準")
    value = {
        "request_id": uuid4().hex,
        "expected_revision": 0,
        "game": "星际棋局",
        "platform": "Windows",
        "mode": "标准",
    }
    result = store.control(sid, "start", value, selection=adopted, now=1000)
    assert result["current"]["selection"] == adopted
    assert result["current"]["game"] == "星际棋局"
    replacement = selection(game="星际棋局", platform="windows", mode="标准", selection_revision=2)
    assert store.sync_selection(sid, replacement, now=1010) == 2
    assert store.current(sid)["selection"] == replacement
