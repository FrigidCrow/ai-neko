"""Retrieval contract checks, independently authored from the frozen A03 oracle."""

import threading
import time
from datetime import UTC, datetime, timedelta
from uuid import uuid4

import pytest

from ai_neko.config.paths import initialize_data_root
from ai_neko.memory.guide_retrieval import GuideRetriever
from ai_neko.memory.guides import GuideConflictError, GuideInputError, GuideStore

NOW = datetime(2026, 9, 28, 8, tzinfo=UTC)
BODY = (
    "星堡守卫\n蓝色水晶是城堡的防御资源，红色哨塔负责侦查。\n"
    "行动顺序\n当蓝色水晶达到 8 枚，先部署银鹰，再把红色哨塔放到北门。\n"
    "记录方法\n将实际观察和猜测分开记录，未证实的条目不可当作确认事件。\n"
)


@pytest.fixture
def store(tmp_path):
    with GuideStore(initialize_data_root(tmp_path / "data"), "retrieval-synthetic") as store:
        yield store


def save(store, text=BODY, *, game="castle", platform="pc", mode="ranked", version="3.2", **extra):
    url = extra.pop("url", "https://example.org/" + uuid4().hex)
    return store.ingest(
        {
            "status": "read",
            "text": text,
            "url": url,
            "original_url": url,
            "title": "星堡独立合成攻略",
            "completeness": "full",
            "completeness_reasons": [],
            "retrieved_at": NOW.isoformat(),
            "headings": [],
            **extra,
        },
        game=game,
        platform=platform,
        mode=mode,
        game_version=version,
        version_basis=f"正文声明版本 {version}" if version else None,
    )


def select(store, document):
    store.set_selection(
        document["game"],
        document["platform"],
        document["mode"],
        document["guide_id"],
        document["revision_id"],
        expected_revision=store.revision(),
        request_id=uuid4().hex,
    )


def retrieve(store, query="蓝色水晶是什么？", **kwargs):
    return GuideRetriever(store).retrieve(query, now=NOW, **kwargs)


def test_only_fixed_adopted_revision_can_enter_retrieval(store):
    original = save(store)
    select(store, original)
    newer = save(store, BODY.replace("8 枚", "12 枚"), url=original["original_url"])
    save(store, "蓝色水晶是危险炸弹。", game="another-game")
    result = retrieve(store)
    assert result["selection"]["revision_id"] == original["revision_id"]
    assert result["update_available"] is True
    assert result["status"] == "needs_check" and result["reason"] == "update_available"
    assert result["needs_revalidation"] is False
    assert all(s["revision_id"] != newer["revision_id"] for s in result["sources"])
    assert "8 枚" in result["sources"][0]["text"]
    assert "12 枚" not in result["sources"][0]["text"]
    assert "危险炸弹" not in result["sources"][0]["text"]


def test_no_selection_does_not_search_entire_library(store):
    save(store)
    result = retrieve(store)
    assert result["status"] == "gap" and result["reason"] == "no_selection"
    assert result["sources"] == []


def test_multiple_selections_need_context_and_game_id_is_accepted(store):
    first = save(store)
    select(store, first)
    select(store, save(store, game="different", text="蓝色水晶是炸弹。"))
    assert retrieve(store)["status"] == "clarify"
    result = retrieve(store, context={"game_id": "castle"})
    assert result["selection"]["guide_id"] == first["guide_id"]
    assert all(s["guide_id"] == first["guide_id"] for s in result["sources"])


@pytest.mark.parametrize(
    "context", [{"game": "different"}, {"platform": "mobile"}, {"mode": "arena"}]
)
def test_known_condition_conflicts_return_no_supported_source(store, context):
    select(store, save(store))
    result = retrieve(store, context=context)
    assert result["status"] == "gap" and result["reason"] == "conditions_conflict"
    assert result["sources"] == []


def test_known_version_conflict_precedes_lexical_ranking(store):
    select(store, save(store))
    result = retrieve(store, context={"game_version": "4.0"})
    assert result["status"] == "gap" and result["version_status"] == "conflict"
    assert result["sources"] == [] and not result["needs_revalidation"]


@pytest.mark.parametrize(
    "query",
    [
        "蓝色水晶合成落日巨龙需要几枚？",
        "攻略附录秘密开门密码是什么？",
        "蓝色水晶能制造“红色熔炉”吗？",
        "How much adamantine does the silver eagle need?",
        "不存在的超级飞龙有多少生命？",
    ],
)
def test_unknown_subject_does_not_gain_support_from_known_words(store, query):
    select(store, save(store))
    result = retrieve(store, query, context={"game_version": "3.2"})
    assert result["status"] == "gap"
    assert result["sources"] == []


def test_latin_stopwords_do_not_count_as_subjects(store):
    select(store, save(store, "The silver eagle consumes eight blue crystals."))
    result = retrieve(store, "What does the silver eagle consume?", context={"game_version": "3.2"})
    # Inflection must not fabricate evidence. Unknown lexical forms conservatively
    # enter the gap path until an attested query form can be used.
    assert result["status"] == "gap"
    exact = retrieve(store, "What are blue crystals?", context={"game_version": "3.2"})
    assert exact["status"] == "sufficient"


def test_unknown_version_allows_original_general_description_but_not_action_rule(store):
    select(store, save(store, version=None))
    general = retrieve(store, "实际观察和猜测怎么分开记录？")
    assert general["status"] == "sufficient" and general["version_status"] == "unknown"
    action = retrieve(store, "蓝色水晶达到多少枚才能部署银鹰？")
    assert action["status"] == "needs_check" and action["reason"] == "version_unverified"
    assert not action["needs_revalidation"]
    assert action["sources"][0]["game_version"] is None


def test_source_version_is_never_inferred_as_current_version(store):
    select(store, save(store))
    result = retrieve(store, "蓝色水晶达到多少枚才能部署银鹰？")
    assert result["version_status"] == "unconfirmed" and result["status"] == "needs_check"
    matched = retrieve(store, "蓝色水晶达到多少枚才能部署银鹰？", context={"game_version": "3.2"})
    assert matched["version_status"] == "matched" and matched["status"] == "sufficient"


@pytest.mark.parametrize("age,fresh", [(0, True), (86400, True), (86400.1, False), (-1, False)])
def test_check_interval_is_not_game_version_validity(store, age, fresh):
    select(store, save(store, retrieved_at=(NOW - timedelta(seconds=age)).isoformat()))
    result = retrieve(store, context={"game_version": "3.2"})
    assert result["freshness"] == ("fresh" if fresh else "stale")
    assert result["needs_revalidation"] is not fresh
    assert result["status"] == ("sufficient" if fresh else "needs_check")


@pytest.mark.parametrize("query", ["最新版蓝色水晶是什么？", "最新版", "latest version"])
def test_explicit_latest_never_takes_zero_check_shortcut(store, query):
    select(store, save(store))
    result = retrieve(store, query, context={"game_version": "3.2"})
    assert result["status"] == "needs_check" and result["reason"] == "explicit_latest"
    assert result["needs_revalidation"]


def match(document, **changes):
    return {
        "match_id": "match-current",
        "status": "active",
        "game_version": "3.2",
        "goal": "保住星堡北门",
        "observations": [
            {
                "text": "蓝色水晶有 8 枚，红色哨塔在手中。",
                "match_id": "match-current",
                "source_kind": "explicit_user_description",
                "observed_at": (NOW - timedelta(seconds=30)).isoformat(),
                "expires_at": (NOW + timedelta(seconds=90)).isoformat(),
            }
        ],
        "last_delivered_advice": {
            "text": "先观察水晶数量。",
            "delivered": True,
            "match_id": "match-current",
            "guide_id": document["guide_id"],
            "revision_id": document["revision_id"],
            "delivered_at": (NOW - timedelta(seconds=60)).isoformat(),
            "executed_by_user": False,
        },
        **changes,
    }


def test_follow_up_uses_only_current_observed_and_delivered_evidence(store):
    doc = save(store)
    select(store, doc)
    context = match(doc)
    context["excluded_previous_match"] = {"text": "OLD_MATCH_DYNAMIC_SECRETS"}
    result = retrieve(store, "下一步呢？", context=context)
    assert result["status"] == "sufficient"
    assert "8 枚" in result["query"]
    assert "先观察水晶数量" in result["query"]
    assert "不代表用户已执行" in result["query"]
    assert "OLD_MATCH" not in result["query"]


@pytest.mark.parametrize(
    "change",
    [
        {"match_id": "old"},
        {"source_kind": "model_inference"},
        {"observed_at": (NOW - timedelta(seconds=121)).isoformat()},
        {"observed_at": (NOW + timedelta(seconds=1)).isoformat()},
        {"observed_at": None},
        {"expires_at": (NOW - timedelta(seconds=1)).isoformat()},
    ],
)
def test_follow_up_refuses_old_unverified_or_expired_observations(store, change):
    doc = save(store)
    select(store, doc)
    context = match(doc)
    context["observations"][0].update(change)
    result = retrieve(store, "然后呢？", context=context)
    assert result["status"] == "clarify" and result["sources"] == []
    assert result["reason"] == "missing_current_observation"


@pytest.mark.parametrize(
    "change", [{"status": "ended"}, {"status": "needs_update"}, {"match_id": None}]
)
def test_ended_or_restarted_match_cannot_expand_follow_up(store, change):
    doc = save(store)
    select(store, doc)
    assert retrieve(store, "那接下来做什么？", context=match(doc, **change))["status"] == "clarify"


@pytest.mark.parametrize(
    "change",
    [
        {"delivered": False},
        {"match_id": "old"},
        {"guide_id": "old"},
        {"revision_id": "old"},
        {"delivered_at": (NOW + timedelta(seconds=1)).isoformat()},
    ],
)
def test_wrong_or_undelivered_advice_never_enters_follow_up_query(store, change):
    doc = save(store)
    select(store, doc)
    context = match(doc)
    context["last_delivered_advice"].update(change, text="DO_NOT_PROPAGATE")
    result = retrieve(store, "下一步呢？", context=context)
    assert "DO_NOT_PROPAGATE" not in result["query"]


def test_long_body_retrieval_and_locator_survive_rebuild_and_reopen(store):
    text = (
        "园区步行日志，地图美术和音乐赏析。\n" * 1500
    ) + "决胜密码：将银鹰放在北门，等待蓝色光圈。\n"
    doc = save(store, text)
    select(store, doc)
    before = retrieve(store, "决胜密码是什么？", context={"game_version": "3.2"})
    assert before["status"] == "sufficient"
    source = before["sources"][0]
    assert source["start"] > 20_000
    assert text[source["start"] : source["end"]] == source["text"]
    store.rebuild_index(doc["revision_id"])
    assert retrieve(store, "决胜密码是什么？", context={"game_version": "3.2"}) == before
    with GuideStore(store.paths, store.scope) as reopened:
        assert retrieve(reopened, "决胜密码是什么？", context={"game_version": "3.2"}) == before


def test_budget_and_limit_are_hard_caps_and_clipping_is_disclosed(store):
    text = "蓝色水晶规则\n" + "蓝色水晶放在北门。等待银鹰。\n" * 3000
    doc = save(store, text)
    select(store, doc)
    result = retrieve(store, limit=100, budget=80_000, context={"game_version": "3.2"})
    assert len(result["sources"]) <= 6
    assert sum(len(s["text"]) for s in result["sources"]) <= 8_000
    clipped = retrieve(store, budget=13, context={"game_version": "3.2"})
    assert len(clipped["sources"]) == 1 and len(clipped["sources"][0]["text"]) == 13
    assert clipped["partial_context"] and clipped["sources"][0]["context_truncated"]
    assert clipped["status"] == "gap" and clipped["reason"] == "context_budget"
    source = clipped["sources"][0]
    assert text[source["start"] : source["end"]] == source["text"]


def test_revision_guard_and_deleted_or_unselected_content(store):
    doc = save(store)
    select(store, doc)
    revision = store.revision()
    retrieve(store, expected_revision=revision)
    store.delete_document(doc["guide_id"], expected_revision=revision, request_id=uuid4().hex)
    with pytest.raises(GuideConflictError, match="guide_revision_changed"):
        retrieve(store, expected_revision=revision)
    assert retrieve(store)["sources"] == []


@pytest.mark.parametrize(
    "kwargs",
    [
        {"limit": True},
        {"limit": 0},
        {"budget": -1},
        {"budget": 0.5},
        {"context": []},
        {"expected_revision": True},
        {"expected_revision": -1},
    ],
)
def test_invalid_input_rejected_before_read(store, kwargs):
    with pytest.raises(GuideInputError):
        retrieve(store, **kwargs)


@pytest.mark.parametrize("now", [float("inf"), "not-a-date", datetime(2026, 9, 28), True])
def test_invalid_or_timezone_unknown_clock_rejected(store, now):
    with pytest.raises(GuideInputError):
        GuideRetriever(store).retrieve("蓝色水晶", now=now)


def test_selection_snapshot_and_body_are_read_under_same_guard(store, monkeypatch):
    doc = save(store)
    select(store, doc)
    entered, done = threading.Event(), threading.Event()
    original = store.get_document

    def reading(*args):
        entered.set()
        time.sleep(0.03)
        assert not done.is_set()
        return original(*args)

    monkeypatch.setattr(store, "get_document", reading)

    def delete():
        assert entered.wait(2)
        store.delete_document(doc["guide_id"], expected_revision=1, request_id=uuid4().hex)
        done.set()

    thread = threading.Thread(target=delete)
    thread.start()
    result = retrieve(store)
    thread.join(2)
    assert done.is_set() and result["revision"] == 1
    assert store.revision() == 2
    # Runtime must validate returned revision immediately before model injection.
    assert result["sources"][0]["guide_id"] == doc["guide_id"]


def test_modal_question_grammar_does_not_turn_attested_subject_into_gap(store):
    select(store, save(store, "拉杆只能长按使用。小圆盾用于防御，不会增加金币。"))
    for query in ("拉杆该怎么按才会生效？", "小圆盾有什么作用，它会提高金币吗？"):
        result = retrieve(store, query, context={"game_version": "3.2"})
        assert result["status"] == "sufficient"


@pytest.mark.parametrize(
    "context",
    [
        {"game": 123},
        {"game_id": []},
        {"game_version": 3.2},
        {"platform": False},
        {"observations": None},
        {"observations": {}},
        {"game": "display name", "game_id": "identifier"},
    ],
)
def test_malformed_conditions_cannot_silently_fall_back_to_sole_selection(store, context):
    select(store, save(store))
    with pytest.raises(GuideInputError):
        retrieve(store, context=context)


def test_malformed_expiry_is_not_fresh_evidence(store):
    doc = save(store)
    select(store, doc)
    context = match(doc)
    context["observations"][0]["expires_at"] = "unknown"
    result = retrieve(store, "然后该怎么办？", context=context)
    assert result["status"] == "clarify" and result["sources"] == []


def test_checked_latest_still_requires_known_version_for_sensitive_advice(store):
    select(store, save(store, version=None))
    result = retrieve(store, "最新版本蓝色水晶达到多少枚才能部署银鹰？", checked_this_turn=True)
    assert result["status"] == "needs_check" and result["reason"] == "version_unverified"
    assert not result["needs_revalidation"]
    assert result["version_status"] == "unknown"
    assert result["query"].startswith("最新版本")


def test_checked_latest_can_use_covered_matching_rule(store):
    select(store, save(store))
    result = retrieve(
        store,
        "最新版本蓝色水晶达到多少枚才能部署银鹰？",
        context={"game_version": "3.2"},
        checked_this_turn=True,
    )
    assert result["status"] == "sufficient" and not result["needs_revalidation"]
    assert result["query"].startswith("最新版本")


@pytest.mark.parametrize(
    "query,kwargs,reason",
    [
        ("最新版蓝色水晶是什么？", {"budget": 10}, "context_budget"),
        ("最新版蓝色水晶是什么？", {"context": {"game_version": "8.0"}}, "version_conflict"),
        ("最新版蓝色水晶合成落日巨龙需要几枚？", {}, "uncovered_subject"),
    ],
)
def test_successful_check_receipt_never_bypasses_other_evidence_gates(store, query, kwargs, reason):
    select(store, save(store))
    result = retrieve(store, query, checked_this_turn=True, **kwargs)
    assert result["status"] == "gap" and result["reason"] == reason


def test_check_receipt_does_not_reset_freshness_or_suppress_new_revision_notice(store):
    old = save(store, retrieved_at=(NOW - timedelta(days=2)).isoformat())
    select(store, old)
    result = retrieve(store, "最新版蓝色水晶是什么？", checked_this_turn=True)
    assert result["status"] == "needs_check" and result["reason"] == "stale"
    save(store, BODY + "新内容", url=old["original_url"])
    result = retrieve(store, "最新版蓝色水晶是什么？", checked_this_turn=True)
    assert result["status"] == "needs_check" and result["reason"] == "update_available"


@pytest.mark.parametrize("value", [1, None, "yes", {}, []])
def test_check_receipt_requires_actual_bool_not_truthiness(store, value):
    with pytest.raises(GuideInputError, match="invalid_guide_check_receipt"):
        retrieve(store, checked_this_turn=value)


def test_untrusted_context_cannot_supply_check_receipt(store):
    select(store, save(store))
    result = retrieve(store, "最新版蓝色水晶是什么？", context={"checked_this_turn": True})
    assert result["status"] == "needs_check" and result["needs_revalidation"]


def test_latest_detector_is_independent_of_adoption_and_untrusted_context():
    from ai_neko.memory.guide_retrieval import requests_latest

    for question in ("最新攻略", "请核查当前版本", "the latest version", "up-to-date rules"):
        assert requests_latest(question)
    for question in ("解释原文", "version 3.2", "previous version", ""):
        assert not requests_latest(question)


def test_checked_english_latest_question_keeps_original_query_and_subject_gate(store):
    select(store, save(store, "The silver eagle consumes eight blue crystals."))
    result = retrieve(store, "What are the latest blue crystals?", checked_this_turn=True)
    assert result["status"] == "sufficient"
    assert result["query"] == "What are the latest blue crystals?"
