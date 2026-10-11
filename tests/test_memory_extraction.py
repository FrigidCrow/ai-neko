"""D1/D2 regression boundaries: synthetic local facts and model outputs only."""

import asyncio
import json
from concurrent.futures import ThreadPoolExecutor
from threading import Event

import pytest
from test_memory_backup_api import api_client
from test_runtime import Model, Store, settled, text_ready

from ai_neko.chat.extraction import extract_facts
from ai_neko.config.paths import initialize_data_root
from ai_neko.memory import MemoryAccessError, MemoryConflictError, MemoryInputError, MemoryService
from ai_neko.memory.extraction import validate_operations
from ai_neko.runtime import SessionRuntime


@pytest.fixture
def memory(tmp_path):
    with MemoryService(initialize_data_root(tmp_path / "synthetic")) as service:
        yield service


def create(content, key="strategy"):
    return {
        "operation": "create",
        "content": content,
        "quote": content,
        "fact_key": key,
        "kind": "preference",
    }


def correction(fact, content, source):
    return {
        "operation": "correct",
        "target_fact_id": fact["id"],
        "target_revision": fact["revision"],
        "content": content,
        "quote": content,
        "correction_quote": source,
        "previous_quote": fact["content"],
    }


def confirm(fact, quote):
    return {
        "operation": "confirm",
        "target_fact_id": fact["id"],
        "target_revision": fact["revision"],
        "quote": quote,
    }


def claim(memory, source, text):
    return memory.claim_extraction(memory.enqueue_extraction(source, text)["id"])


def complete(memory, job, facts):
    return memory.complete_extraction(
        job["id"],
        facts,
        lease_token=job["lease_token"],
        snapshot_revision=job["snapshot_revision"],
    )


def test_correction_preserves_id_key_history_provenance_and_old_backup(memory):
    old = memory.remember("SOL在星河棋喜欢保守防守", fact_key="sol_strategy", source_id="old")
    other = memory.remember(
        "OTHER在星河棋喜欢保守防守", fact_key="other_strategy", source_id="other"
    )
    different_game = memory.remember(
        "SOL在月光棋喜欢保守防守", fact_key="sol_moon", source_id="moon"
    )
    backup = memory.backup()
    source = "把SOL在星河棋喜欢保守防守更正为SOL在星河棋喜欢主动进攻"
    job = claim(memory, "new", source)
    result = complete(memory, job, [correction(old, "SOL在星河棋喜欢主动进攻", source)])
    current = next(f for f in result["facts"] if f["id"] == old["id"])
    assert current["fact_key"] == old["fact_key"]
    assert current["kind"] == old["kind"]
    assert current["revision"] == result["revision"] > old["revision"]
    assert set(current["source_ids"]) == {"old", "new"}
    history = memory._db.execute(
        "SELECT previous_content,source_id FROM memory_corrections"
    ).fetchall()
    assert [tuple(row) for row in history] == [(old["content"], "new")]
    assert complete(memory, job, [correction(old, "ignored replay", source)])["facts"] == []
    memory.restore(backup["id"])
    facts = {f["id"]: f for f in memory.list_facts()}
    assert facts[old["id"]]["content"] == current["content"]
    assert facts[other["id"]]["content"] == other["content"]
    assert facts[different_game["id"]]["content"] == different_game["content"]
    assert memory.recall("SOL 星河棋 主动进攻")[0]["id"] == old["id"]


def test_confirm_uses_original_key_even_when_longer_than_old_model_key_limit(memory):
    old = memory.remember("喜欢茶", fact_key="k" * 200, source_id="old")
    job = claim(memory, "confirmation", "我喜欢茶")
    result = complete(memory, job, [confirm(old, "喜欢茶")])
    assert result["facts"][0]["id"] == old["id"]
    assert set(result["facts"][0]["source_ids"]) == {"old", "confirmation"}
    assert memory._db.execute("SELECT count(*) FROM memory_corrections").fetchone()[0] == 0


@pytest.mark.parametrize("key", ["new_strategy", "STRATEGY", "synthetic_user_strategy"])
def test_explicit_correction_cannot_be_downgraded_to_create_with_new_key(memory, key):
    memory.remember("喜欢保守防守", fact_key="strategy", source_id="old")
    job = claim(memory, "correction", "更正偏好：喜欢主动进攻")
    with pytest.raises(MemoryInputError, match="correction_requires_target"):
        complete(memory, job, [create("喜欢主动进攻", key)])
    assert [f["content"] for f in memory.list_facts()] == ["喜欢保守防守"]


@pytest.mark.parametrize(
    "mutation",
    [
        {"target_fact_id": "unknown"},
        {"target_revision": True},
        {"target_revision": 999},
        {"quote": "not in source"},
        {"correction_quote": "not in source"},
        {"fact_key": "injected"},
        {"kind": "event"},
        {"operation": "delete"},
    ],
)
def test_invalid_target_or_provenance_never_partially_writes(memory, mutation):
    fact = memory.remember("喜欢咖啡", source_id="old")
    source = "更正喜欢咖啡为喜欢茶"
    job = claim(memory, "new", source)
    revision = memory.revision()
    with pytest.raises(MemoryInputError):
        complete(memory, job, [{**correction(fact, "喜欢茶", source), **mutation}])
    assert memory.revision() == revision
    assert memory.list_facts() == [fact]
    assert memory._db.execute("SELECT count(*) FROM memory_corrections").fetchone()[0] == 0


@pytest.mark.parametrize(
    "source",
    [
        "现在请记住Bob喜欢茶",
        "之前发生过Bob喜欢茶",
        "Bob likes snow",
        "Bob has unknown tastes",
        "我喜欢茶，但不要纠正Alice的咖啡偏好",
        "如果纠正Alice的偏好，可以说喜欢茶",
        "Do not replace Alice's preference with tea",
        "If we replace Alice's preference with tea",
    ],
)
def test_weak_time_words_negative_or_hypothetical_do_not_authorize_correction(memory, source):
    fact = memory.remember("Alice喜欢咖啡", source_id="old")
    job = claim(memory, "new", source)
    with pytest.raises(MemoryInputError, match="unverified_memory_correction"):
        complete(memory, job, [correction(fact, source, source)])
    assert memory.list_facts() == [fact]


@pytest.mark.parametrize(
    "operations", ["same_create", "existing_create", "double_correct", "correct_confirm"]
)
def test_batch_identity_collisions_are_atomic(memory, operations):
    fact = memory.remember("喜欢咖啡", source_id="old", fact_key="drink")
    source = "喜欢茶，喜欢蓝色" if "create" in operations else "更正喜欢咖啡为喜欢茶"
    job = claim(memory, "new", source)
    proposals = {
        "same_create": [create("喜欢茶", "same"), create("喜欢蓝色", "same")],
        "existing_create": [create("喜欢蓝色", "color"), create("喜欢茶", "drink")],
        "double_correct": [correction(fact, "喜欢茶", source)] * 2,
        "correct_confirm": [correction(fact, "喜欢茶", source), confirm(fact, "喜欢茶")],
    }[operations]
    with pytest.raises(MemoryInputError):
        complete(memory, job, proposals)
    assert memory.list_facts() == [fact]
    assert memory.revision() == fact["revision"]


def test_cross_scope_target_is_not_authorized(memory):
    with MemoryService(memory.paths, character_id="other") as other:
        foreign = other.remember("喜欢咖啡", source_id="foreign")
        source = "更正喜欢咖啡为喜欢茶"
        job = claim(memory, "new", source)
        assert job["existing_facts"] == []
        with pytest.raises(MemoryInputError, match="invalid_memory_target"):
            complete(memory, job, [correction(foreign, "喜欢茶", source)])
        assert other.list_facts() == [foreign]


@pytest.mark.parametrize("previous", ["喜欢", "不存在的旧值", "喜欢茶"])
def test_previous_quote_must_identify_old_value_in_both_sources(memory, previous):
    old = memory.remember("喜欢咖啡", source_id="old")
    source = "更正喜欢咖啡为喜欢茶"
    job = claim(memory, "new", source)
    with pytest.raises(MemoryInputError, match="unverified_memory_correction"):
        complete(memory, job, [{**correction(old, "喜欢茶", source), "previous_quote": previous}])
    assert memory.list_facts() == [old]


def test_same_old_value_for_different_people_requires_unique_literal_anchor(memory):
    alice = memory.remember("Alice喜欢咖啡", source_id="alice")
    bob = memory.remember("Bob喜欢咖啡", source_id="bob")
    source = "把Bob喜欢咖啡更正为Bob喜欢茶"
    job = claim(memory, "new", source)
    wrong_target = {
        **correction(alice, "Alice喜欢茶", source),
        "quote": "Bob喜欢茶",
        "previous_quote": "喜欢咖啡",
    }
    with pytest.raises(MemoryInputError, match="unverified_memory_correction"):
        complete(memory, job, [wrong_target])
    right_target = correction(bob, "Bob喜欢茶", source)
    result = complete(memory, job, [right_target])
    assert result["facts"][0]["id"] == bob["id"]
    assert next(f for f in memory.list_facts() if f["id"] == alice["id"]) == alice


def test_complete_short_old_fact_is_sufficient_when_unique(memory):
    old = memory.remember("喜欢猫", source_id="old")
    source = "把喜欢猫更正为喜欢狗"
    job = claim(memory, "new", source)
    assert (
        complete(memory, job, [correction(old, "喜欢狗", source)])["facts"][0]["content"]
        == "喜欢狗"
    )


def test_explicit_correction_cannot_confirm_the_rejected_old_value(memory):
    old = memory.remember("喜欢咖啡", source_id="old")
    source = "把喜欢咖啡更正为喜欢茶"
    job = claim(memory, "new", source)
    with pytest.raises(MemoryInputError, match="correction_requires_target"):
        complete(memory, job, [confirm(old, "喜欢咖啡")])
    assert memory.list_facts() == [old]


def test_management_snapshot_never_pairs_old_content_with_new_revision(memory, monkeypatch):
    old = memory.remember("喜欢咖啡", source_id="old")
    source = "把喜欢咖啡更正为喜欢茶"
    job = claim(memory, "new", source)
    read, release, writer_started = Event(), Event(), Event()
    original = memory._list_facts

    def hold_read():
        facts = original()
        read.set()
        if not release.wait(5):
            raise AssertionError("snapshot test gate timed out")
        return facts

    def write(other):
        writer_started.set()
        return complete(other, job, [correction(old, "喜欢茶", source)])

    monkeypatch.setattr(memory, "_list_facts", hold_read)
    with MemoryService(memory.paths) as other, ThreadPoolExecutor(2) as pool:
        reading = pool.submit(memory.management_snapshot)
        try:
            assert read.wait(5)
            writing = pool.submit(write, other)
            assert writer_started.wait(5)
            assert not writing.done()
        finally:
            release.set()
        snapshot = reading.result(5)
        written = writing.result(5)
    assert snapshot["memories"] == [old]
    assert snapshot["revision"] == old["revision"]
    assert written["revision"] > snapshot["revision"]
    assert memory.list_facts()[0]["content"] == "喜欢茶"


def test_snapshot_cas_rejects_racing_create_then_retry_reads_current_identity(memory):
    one = claim(memory, "one", "喜欢茶")
    two = claim(memory, "two", "喜欢茶")
    complete(memory, one, [create("喜欢茶", "drink")])
    with pytest.raises(MemoryConflictError, match="stale_memory_revision"):
        complete(memory, two, [create("喜欢茶", "other_key")])
    memory.fail_extraction(two["id"], lease_token=two["lease_token"])
    retry = memory.claim_extraction(two["id"])
    assert retry["expected_revision"] == two["expected_revision"]
    assert retry["existing_facts"][0]["fact_key"] == "drink"
    complete(memory, retry, [confirm(retry["existing_facts"][0], "喜欢茶")])
    assert len(memory.list_facts()) == 1


@pytest.mark.parametrize("winner", [0, 1])
def test_two_corrections_only_first_commit_wins_and_other_cannot_retry(memory, winner):
    old = memory.remember("喜欢咖啡", source_id="old")
    sources = ["更正喜欢咖啡为喜欢茶", "更正喜欢咖啡为喜欢水"]
    jobs = [claim(memory, str(i), source) for i, source in enumerate(sources)]
    proposals = [correction(old, source.split("为")[1], source) for source in sources]
    result = complete(memory, jobs[winner], [proposals[winner]])
    assert result["status"] == "completed"
    assert complete(memory, jobs[1 - winner], [proposals[1 - winner]])["status"] == "cancelled"
    assert memory.claim_extraction(jobs[1 - winner]["id"]) is None
    assert memory.list_facts()[0]["content"] == proposals[winner]["content"]
    assert memory.extraction_overview()["cancelled"] == 1


def test_concurrent_connections_have_single_correction_commit(memory):
    old = memory.remember("喜欢咖啡", source_id="old")
    source = "更正喜欢咖啡为喜欢茶"
    jobs = [claim(memory, f"turn-{i}", source) for i in range(2)]
    with MemoryService(memory.paths) as second, ThreadPoolExecutor(2) as pool:
        results = list(
            pool.map(
                lambda args: complete(args[0], args[1], [correction(old, "喜欢茶", source)]),
                [(memory, jobs[0]), (second, jobs[1])],
            )
        )
    assert sorted(r["status"] for r in results) == ["cancelled", "completed"]
    assert memory._db.execute("SELECT count(*) FROM memory_corrections").fetchone()[0] == 1


@pytest.mark.parametrize("erase", ["fact", "source"])
def test_forget_and_restore_never_revive_corrected_or_late_content(memory, erase):
    old = memory.remember("喜欢咖啡", source_id="old")
    backup = memory.backup()
    source = "更正喜欢咖啡为喜欢茶"
    done = claim(memory, "new", source)
    complete(memory, done, [correction(old, "喜欢茶", source)])
    late = claim(memory, "late", "更正喜欢咖啡为喜欢水")
    if erase == "fact":
        memory.forget(old["id"])
    else:
        memory.forget_source("old")
    memory.restore(backup["id"])
    assert memory.list_facts() == []
    # This old backup predates the job; restore removed it. A late completion
    # must be refused as missing rather than recreate either job or source.
    with pytest.raises(MemoryAccessError, match="job_not_found"):
        complete(memory, late, [correction(old, "喜欢水", "更正喜欢咖啡为喜欢水")])
    assert memory.list_facts() == []


def test_out_of_order_old_source_cannot_correct_a_newer_target(memory):
    old_job = memory.enqueue_extraction("earlier", "更正喜欢茶为喜欢咖啡")
    newer = memory.remember("喜欢茶", source_id="later")
    claimed = memory.claim_extraction(old_job["id"])
    with pytest.raises(MemoryConflictError, match="source_precedes_target"):
        complete(memory, claimed, [correction(newer, "喜欢咖啡", "更正喜欢茶为喜欢咖啡")])
    assert memory.list_facts() == [newer]


@pytest.mark.parametrize("count,size", [(129, 10), (100, 1000)])
def test_complete_snapshot_overflow_never_silently_drops_candidates(memory, count, size):
    for index in range(count):
        memory.remember("x" * size, source_id=f"s{index}", fact_key=f"k{index}")
    job = claim(memory, "new", "喜欢茶")
    assert job["context_limit_exceeded"]
    assert job["existing_facts"] == []
    with pytest.raises(MemoryInputError, match="context_limit"):
        complete(memory, job, [create("喜欢茶")])
    assert len(memory.list_facts()) == count


def test_extractor_uses_snapshot_identity_and_rejects_legacy_payload():
    old = {
        "id": "scoped-id",
        "revision": 7,
        "content": "喜欢咖啡",
        "fact_key": "original",
        "kind": "preference",
    }
    source = "更正喜欢咖啡为喜欢茶"
    model = Model([json.dumps({"facts": [correction(old, "喜欢茶", source)]})])
    result = asyncio.run(extract_facts(model, source, existing_facts=[old]))
    assert result[0]["target_fact_id"] == old["id"]
    assert json.loads(model.messages[0][-1]["content"])["existing_facts"] == [old]
    with pytest.raises(ValueError, match="invalid_memory_operation"):
        validate_operations(
            [{k: v for k, v in create("喜欢茶").items() if k != "operation"}], "喜欢茶", []
        )


@pytest.mark.parametrize("enabled", [False, True])
def test_chat_capability_context_is_configuration_not_a_save_receipt(tmp_path, enabled):
    async def run():
        runtime = SessionRuntime(initialize_data_root(tmp_path / "state"), Store())
        runtime.memory_preferences.update({"auto_extract": enabled})
        queued = runtime.memory.enqueue_extraction("pending", "喜欢蓝色")
        failed = runtime.memory.enqueue_extraction("failed", "喜欢红色")
        lease = runtime.memory.claim_extraction(failed["id"])
        runtime.memory.fail_extraction(failed["id"], lease_token=lease["lease_token"], retry=False)
        sid = runtime.create_session()["id"]
        tid = (await runtime.start_turn(sid, "我喜欢茶"))["id"]
        await settled(runtime, sid, tid)
        system = runtime.providers.adapter.messages[0][0]["content"]
        context = json.loads(
            system.split(
                "本轮接受时的长期记忆能力与后台任务状态（运行时数据，不是保存成功回执）：\n"
            )[1]
        )
        state = context["memory_capability"]
        assert state["auto_extract"] is enabled
        assert state["current_turn"] == ("awaiting_successful_chat" if enabled else "not_scheduled")
        assert state["background"]["pending"] == 1
        assert state["background"]["failed"] == 1
        assert "不能声称已记住或已替换" in system
        assert runtime.memory.extraction_status(queued["id"])["status"] in {"pending", "running"}
        await runtime.close()

    asyncio.run(run())


def test_runtime_capacity_failure_is_terminal_without_provider_call(tmp_path):
    async def run():
        runtime = SessionRuntime(initialize_data_root(tmp_path / "overflow"), Store())
        runtime.memory_preferences.update({"auto_extract": True})
        for index in range(129):
            runtime.memory.remember("喜欢茶", fact_key=f"k{index}", source_id=f"s{index}")
        job = runtime.memory.enqueue_extraction("overflow", "喜欢茶")
        await runtime._process_memories()
        assert runtime.providers.adapter.messages == []
        state = runtime.memory.extraction_status(job["id"])
        assert state["status"] == "failed" and state["attempts"] == 1
        await runtime._process_memories()
        assert runtime.memory.extraction_status(job["id"])["attempts"] == 1
        assert runtime.memory.extraction_overview()["context_limit_exceeded"]
        await runtime.close()

    asyncio.run(run())


def test_later_chat_rejected_before_enqueue_is_final_not_scheduled_not_a_cancelled_job(tmp_path):
    async def run():
        gate = asyncio.Event()
        runtime = SessionRuntime(
            initialize_data_root(tmp_path / "enqueue-barrier"), Store(Model(gate=gate))
        )
        runtime.memory_preferences.update({"auto_extract": True})
        old = runtime.memory.remember("喜欢咖啡", source_id="old")
        source = "把喜欢咖啡更正为喜欢茶"
        prior = claim(runtime.memory, "correction", source)
        sid = runtime.create_session()["id"]
        tid = (await runtime.start_turn(sid, "我喜欢蓝色"))["id"]
        task = runtime._tasks[tid]
        try:
            await text_ready(runtime, sid, tid)
            complete(runtime.memory, prior, [correction(old, "喜欢茶", source)])
            gate.set()
            await task
            turn = runtime.get_session(sid)["turns"][0]
            assert turn["status"] == "completed"
            usage = turn["provider_usage"]["scopes"]["memory_extraction"]
            assert usage["state"] == "not_scheduled" and usage["is_final"]
            assert runtime.memory._db.execute("SELECT count(*) FROM memory_jobs").fetchone()[0] == 1
            overview = runtime.memory.extraction_overview()
            assert overview["latest"]["status"] == "completed"
            assert overview["cancelled"] == 0  # No job was created for this turn.
            assert [fact["content"] for fact in runtime.memory.list_facts()] == ["喜欢茶"]
        finally:
            gate.set()
            await runtime.close()

    asyncio.run(run())


def test_memory_api_reports_atomic_snapshot_and_content_free_task_overview(tmp_path):
    async def run():
        runtime = SessionRuntime(initialize_data_root(tmp_path / "memory-api"), Store())
        fact = runtime.memory.remember("SYNTHETIC_PRIVATE_FACT", source_id="manual")
        runtime.memory.enqueue_extraction("pending", "SYNTHETIC_PRIVATE_PENDING_SOURCE")
        try:
            async with api_client(runtime) as client:
                response = await client.get("/api/memories")
                assert response.status_code == 200
                value = response.json()
                assert value["memories"] == [fact]
                assert value["revision"] == fact["revision"]
                assert value["extraction_overview"] == {
                    "pending": 1,
                    "running": 0,
                    "failed": 0,
                    "cancelled": 0,
                    "context_limit_exceeded": False,
                    "latest": {"status": "pending", "attempts": 0},
                }
                assert "PRIVATE" not in json.dumps(value["extraction_overview"])
        finally:
            await runtime.close()

    asyncio.run(run())
