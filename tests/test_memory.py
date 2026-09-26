"""Real SQLite persistence and erasure tests with synthetic personal data only."""

import json
import os
import sqlite3
import subprocess
import sys
import time

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


@pytest.mark.usefixtures("sandbox_compatible")
def test_ten_facts_survive_fresh_process_with_sources(paths):
    preferences = ["喜欢无糖咖啡", "喜欢猫咪", "偏爱蓝色", "常玩策略游戏", "喜欢简短回答"]
    events = ["九月学习游泳", "上周去过京都", "昨天买了键盘", "周五看了电影", "今天完成项目"]
    with MemoryService(paths) as memory:
        for index, content in enumerate(preferences + events):
            memory.remember(
                content,
                source_id=f"synthetic-turn-{index}",
                kind="preference" if index < 5 else "event",
            )
    code = """
import json,sys
from ai_neko.config.paths import initialize_data_root
from ai_neko.memory import MemoryService
with MemoryService(initialize_data_root(sys.argv[1])) as memory:
    print(json.dumps([{'fact': f, 'sources': memory.sources(f['id'])}
                      for f in memory.list_facts()],ensure_ascii=False))
"""
    result = subprocess.run(
        [sys.executable, "-c", code, str(paths.root)],
        capture_output=True,
        text=True,
        check=True,
        encoding="utf-8",
        env={**os.environ, "PYTHONIOENCODING": "utf-8"},
    )
    rows = json.loads(result.stdout)
    assert {row["fact"]["content"] for row in rows} == set(preferences + events)
    assert len(rows) == 10
    assert all(row["sources"][0]["source_text"] == row["fact"]["content"] for row in rows)
    assert all(row["sources"][0]["source_id"].startswith("synthetic-turn-") for row in rows)


def test_recall_unknowns_and_scopes(paths):
    with MemoryService(paths) as first, MemoryService(paths, character_id="other") as other:
        first.remember("喜欢无糖咖啡", source_id="a")
        other.remember("喜欢淡茶", source_id="a")
        assert first.recall("我喝什么咖啡？")[0]["content"] == "喜欢无糖咖啡"
        assert other.recall("咖啡") == []
        assert first.recall("淡茶") == []
        for unknown in ("生日", "学校", "地址", "职业", "年龄"):
            assert first.recall(unknown) == []
        with MemoryService(paths, user_id="other-user") as third:
            assert third.list_facts() == []


def test_general_personal_questions_recall_candidates_but_specific_unknowns_do_not(memory):
    preference = memory.remember("只喜欢柚子汽水，暂时不喝咖啡", source_id="preference")
    event = memory.remember("昨天去了公园", source_id="event", kind="event")
    for question in (
        "我的偏好是什么",
        "我喜欢什么？",
        "说说我的喜好",
        "我的爱好有哪些",
        "我喜欢喝什么？看看这一帧",
    ):
        assert [fact["id"] for fact in memory.recall(question)] == [preference["id"]]
    assert len(memory.recall("你还记得我吗？")) == 2
    assert [fact["id"] for fact in memory.recall("我最近做过什么？")] == [event["id"]]
    for specific in ("我的生日是什么", "我喜欢什么运动", "我的职业是什么"):
        assert memory.recall(specific) == []


def test_correction_replaces_recalled_value_retains_provenance(memory):
    fact = memory.remember("喜欢无糖咖啡", source_id="before", fact_key="drink")
    corrected = memory.correct(fact["id"], "现在喜欢茶", source_id="correction")
    assert corrected["id"] == fact["id"]
    assert corrected["revision"] > fact["revision"]
    assert corrected["source_ids"] == ["before", "correction"]
    assert memory.recall("咖啡") == []
    assert memory.recall("喜欢茶")[0]["content"] == "现在喜欢茶"
    assert {source["source_text"] for source in memory.sources(fact["id"])} == {
        "喜欢无糖咖啡",
        "现在喜欢茶",
    }


def test_remember_retry_idempotent_and_explicit_correction_required(memory):
    first = memory.remember("喜欢咖啡", source_id="original", fact_key="drink")
    revision = memory.revision()
    second = memory.remember("喜欢咖啡", source_id="original", fact_key="drink")
    assert second == first
    assert memory.revision() == revision
    with pytest.raises(MemoryConflictError, match="correction"):
        memory.remember("喜欢茶", source_id="new-source", fact_key="drink")
    assert memory.revision() == revision
    assert len(memory.list_facts()) == 1
    with pytest.raises(MemoryConflictError, match="source"):
        memory.remember("替换旧原文", source_id="original", fact_key="other")


def test_cross_instance_revision_prevents_stale_writes(paths):
    with MemoryService(paths) as first, MemoryService(paths) as second:
        original = first.revision()
        first.remember("喜欢咖啡", source_id="one", expected_revision=original)
        with pytest.raises(MemoryConflictError, match="stale"):
            second.remember("喜欢茶", source_id="two", expected_revision=original)
        assert len(second.list_facts()) == 1
        assert second.revision() == first.revision()


def test_forget_erases_source_correction_index_and_related_facts(memory, paths):
    body = "我喜欢无糖咖啡，也喜欢猫咪。"
    coffee = memory.remember("喜欢无糖咖啡", source_id="shared", source_text=body, fact_key="drink")
    cat = memory.remember("喜欢猫咪", source_id="shared", source_text=body, fact_key="pet")
    memory.correct(coffee["id"], "现在喜欢茶", source_id="corrected")
    result = memory.forget(coffee["id"])
    assert set(result["fact_ids"]) == {coffee["id"], cat["id"]}
    assert result["source_ids"] == ["corrected", "shared"]
    assert memory.list_facts() == []
    with sqlite3.connect(paths.memory / "long-term.sqlite") as connection:
        for table in (
            "memory_sources",
            "memory_facts",
            "memory_fact_sources",
            "memory_corrections",
        ):
            assert connection.execute(f"SELECT count(*) FROM {table}").fetchone()[0] == 0
    assert "咖啡".encode() not in (paths.memory / "long-term.sqlite").read_bytes()
    with pytest.raises(MemoryConflictError, match="erased"):
        memory.remember("喜欢无糖咖啡", source_id="shared", source_text=body)
    # An intentional new source may save the preference again.
    assert memory.remember("喜欢无糖咖啡", source_id="user-saved-again")


def test_forget_only_one_scope(paths):
    with MemoryService(paths) as first, MemoryService(paths, character_id="other") as other:
        fact = first.remember("喜欢咖啡", source_id="one")
        theirs = other.remember("喜欢咖啡", source_id="one")
        with pytest.raises(MemoryAccessError):
            first.forget(theirs["id"])
        first.forget(fact["id"])
        assert len(other.list_facts()) == 1


def test_erasure_evidence_survives_service_restart_for_journal_reconciliation(paths):
    with MemoryService(paths) as first:
        fact = first.remember("生日六月一日", source_id="birthday")
        deleted = first.forget(fact["id"])
    with MemoryService(paths) as reopened, MemoryService(paths, character_id="other") as other:
        assert reopened.erasure_state() == deleted
        assert other.erasure_state() == {"revision": 0, "source_ids": [], "fact_ids": []}


def test_job_idempotency_atomic_commit_and_duplicate_completion(memory):
    revision = memory.revision()
    job = memory.enqueue_extraction("turn1", "我喜欢蓝色，昨天去了公园", turn_id="turn1")
    retry = memory.enqueue_extraction("turn1", "我喜欢蓝色，昨天去了公园", turn_id="turn1")
    assert job["id"] == retry["id"]
    assert len(memory.pending_jobs()) == 1
    claimed = memory.claim_extraction(job["id"])
    assert claimed["source_text"] == "我喜欢蓝色，昨天去了公园"
    assert memory.claim_extraction(job["id"]) is None
    result = memory.complete_extraction(
        job["id"],
        [
            {"content": "喜欢蓝色", "fact_key": "color"},
            {"content": "昨天去了公园", "fact_key": "park", "kind": "event"},
        ],
        lease_token=claimed["lease_token"],
    )
    assert result["status"] == "completed"
    assert len(result["facts"]) == 2 and result["revision"] == revision + 1
    retry = memory.complete_extraction(
        job["id"], [{"content": "不能重复写"}], lease_token=claimed["lease_token"]
    )
    assert retry["status"] == "completed" and retry["facts"] == []
    assert len(memory.list_facts()) == 2
    assert memory.pending_jobs() == []


@pytest.mark.usefixtures("sandbox_compatible")
def test_job_recovery_after_real_process_kill(paths):
    code = """
import json,sys,time
from ai_neko.config.paths import initialize_data_root
from ai_neko.memory import MemoryService
memory=MemoryService(initialize_data_root(sys.argv[1]))
job=memory.enqueue_extraction('crash-turn','喜欢雨天')
claimed=memory.claim_extraction(job['id'],lease_seconds=1)
print(json.dumps(claimed),flush=True)
time.sleep(60)
"""
    process = subprocess.Popen(
        [sys.executable, "-u", "-c", code, str(paths.root)],
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    )
    try:
        claimed = json.loads(process.stdout.readline())
        assert process.poll() is None
        process.kill()
        process.wait(timeout=5)
    finally:
        if process.poll() is None:
            process.kill()
            process.wait(timeout=5)
    with MemoryService(paths) as memory:
        while time.time() < claimed["lease_until"]:
            time.sleep(0.05)
        jobs = memory.pending_jobs()
        assert len(jobs) == 1 and jobs[0]["id"] == claimed["id"]
        resumed = memory.claim_extraction(jobs[0]["id"])
        assert resumed["lease_token"] != claimed["lease_token"]
        with pytest.raises(MemoryConflictError, match="lease"):
            memory.complete_extraction(
                jobs[0]["id"], [{"content": "迟到写入"}], lease_token=claimed["lease_token"]
            )
        memory.complete_extraction(
            jobs[0]["id"], [{"content": "喜欢雨天"}], lease_token=resumed["lease_token"]
        )
        assert len(memory.list_facts()) == 1


@pytest.mark.parametrize("claim_first", [True, False])
def test_late_job_never_revives_forgotten_source(memory, claim_first):
    job = memory.enqueue_extraction("old-turn", "喜欢咖啡")
    claimed = memory.claim_extraction(job["id"]) if claim_first else None
    memory.forget_source("old-turn")
    assert memory.claim_extraction(job["id"]) is None
    if claimed:
        result = memory.complete_extraction(
            job["id"], [{"content": "喜欢咖啡"}], lease_token=claimed["lease_token"]
        )
        assert result["status"] == "cancelled"
    with pytest.raises(MemoryConflictError, match="erased"):
        memory.enqueue_extraction("old-turn", "喜欢咖啡")
    assert memory.list_facts() == [] and memory.pending_jobs() == []


def test_stale_job_cannot_override_newer_manual_correction(memory):
    fact = memory.remember("喜欢咖啡", fact_key="drink", source_id="original")
    job = memory.enqueue_extraction("old-turn", "我喜欢咖啡")
    claimed = memory.claim_extraction(job["id"])
    memory.correct(fact["id"], "现在喜欢茶", source_id="correction")
    result = memory.complete_extraction(
        job["id"],
        [{"content": "喜欢咖啡", "fact_key": "drink"}],
        lease_token=claimed["lease_token"],
    )
    assert result["status"] == "cancelled"
    assert memory.list_facts()[0]["content"] == "现在喜欢茶"


def test_job_failure_retries_without_duplicate_facts(memory):
    job = memory.enqueue_extraction("turn", "我喜欢蓝色")
    claimed = memory.claim_extraction(job["id"])
    memory.fail_extraction(job["id"], lease_token=claimed["lease_token"])
    resumed = memory.claim_extraction(job["id"])
    assert resumed["attempts"] == 2
    memory.complete_extraction(
        job["id"], [{"content": "喜欢蓝色"}], lease_token=resumed["lease_token"]
    )
    assert len(memory.list_facts()) == 1


def test_sequential_pending_sources_survive_other_successful_extractions(memory):
    original = memory.revision()
    first = memory.enqueue_extraction("first", "喜欢蓝色", expected_revision=original)
    second = memory.enqueue_extraction("second", "喜欢猫咪", expected_revision=original)
    for job, content in ((first, "喜欢蓝色"), (second, "喜欢猫咪")):
        claimed = memory.claim_extraction(job["id"])
        assert claimed is not None
        result = memory.complete_extraction(
            job["id"], [{"content": content}], lease_token=claimed["lease_token"]
        )
        assert result["status"] == "completed"
    # A turn that was generated before another source was added can still enqueue.
    third = memory.enqueue_extraction("third", "喜欢游泳", expected_revision=original)
    assert memory.claim_extraction(third["id"]) is not None
    assert {fact["content"] for fact in memory.list_facts()} == {"喜欢蓝色", "喜欢猫咪"}


def test_invalid_extraction_rolls_back_entire_result(memory):
    memory.remember("喜欢咖啡", fact_key="drink", source_id="original")
    job = memory.enqueue_extraction("turn", "喜欢茶，喜欢蓝色")
    claimed = memory.claim_extraction(job["id"])
    revision = memory.revision()
    with pytest.raises(MemoryConflictError):
        memory.complete_extraction(
            job["id"],
            [{"content": "喜欢蓝色"}, {"content": "喜欢茶", "fact_key": "drink"}],
            lease_token=claimed["lease_token"],
        )
    assert memory.revision() == revision
    assert len(memory.list_facts()) == 1
    memory.fail_extraction(job["id"], lease_token=claimed["lease_token"], retry=False)


def test_restore_old_backup_keeps_erasure_and_correction(memory, paths):
    first = memory.remember("喜欢咖啡", source_id="coffee", fact_key="drink")
    deleted = memory.remember("生日六月一日", source_id="birthday", kind="fact")
    memory.enqueue_extraction("pending-turn", "我喜欢猫咪")
    backup = memory.backup()
    memory.correct(first["id"], "现在喜欢茶", source_id="new-drink")
    memory.forget(deleted["id"])
    previous_revision = memory.revision()
    result = memory.restore(backup["id"])
    assert result["revision"] > previous_revision
    assert "birthday" in result["source_ids"]
    facts = memory.list_facts()
    assert len(facts) == 1 and facts[0]["content"] == "现在喜欢茶"
    assert facts[0]["source_ids"] == ["coffee", "new-drink"]
    assert memory.pending_jobs() == []
    with pytest.raises(MemoryConflictError, match="erased"):
        memory.remember("生日六月一日", source_id="birthday")
    assert (paths.backups / backup["id"]).exists()


def test_restore_is_scoped_and_does_not_change_other_persona(paths):
    with MemoryService(paths) as first, MemoryService(paths, character_id="other") as other:
        original = first.remember("喜欢咖啡", source_id="coffee")
        other.remember("喜欢茶", source_id="tea")
        backup = first.backup()
        first.remember("喜欢蓝色", source_id="color")
        other.remember("喜欢红色", source_id="color")
        other.update_persona({"name": "另一个角色"})
        first.restore(backup["id"])
        assert [fact["id"] for fact in first.list_facts()] == [original["id"]]
        assert len(other.list_facts()) == 2
        assert other.get_persona()["name"] == "另一个角色"


@pytest.mark.parametrize("backup_id", ["../outside", "/tmp/file.sqlite", "memory-abc.sqlite", 1])
def test_backup_does_not_accept_arbitrary_path(memory, backup_id):
    with pytest.raises(MemoryInputError):
        memory.restore(backup_id)


def test_symlink_database_is_rejected(paths, tmp_path):
    foreign = tmp_path / "foreign.sqlite"
    foreign.write_text("foreign")
    try:
        (paths.memory / "long-term.sqlite").symlink_to(foreign)
    except OSError as exc:
        if getattr(exc, "winerror", None) == 1314:
            pytest.skip("Host needs Windows Developer Mode or symlink privileges")
        raise
    with pytest.raises(ValueError, match="symlink|reparse"):
        MemoryService(paths)
    assert foreign.read_text() == "foreign"


def test_closed_service_rejects_writes(memory):
    memory.close()
    memory.close()
    with pytest.raises(MemoryConflictError, match="closed"):
        memory.remember("喜欢咖啡", source_id="turn")
