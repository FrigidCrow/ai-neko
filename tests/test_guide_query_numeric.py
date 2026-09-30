"""Regressions for exact mixed Chinese/numeric subjects found by cold/warm probing."""

from datetime import UTC, datetime
from uuid import uuid4

import pytest

from ai_neko.config.paths import initialize_data_root
from ai_neko.memory.guide_retrieval import GuideRetriever
from ai_neko.memory.guides import GuideStore


@pytest.mark.parametrize(
    "query_term,body_term,supported",
    [
        ("灯芯10", "灯芯10", True),
        ("灯芯11", "灯芯11", True),
        ("灯芯19", "灯芯19", True),
        ("灯芯10", "灯芯100", False),
        ("灯芯10", "灯芯110", False),
        ("灯芯99", "灯芯19", False),
        ("灯芯01", "灯芯1", False),
        ("灯芯10", "灯芯10a", False),
    ],
)
def test_single_mixed_subject_requires_exact_body_evidence(
    tmp_path, query_term, body_term, supported
):
    now = datetime.now(UTC)
    with GuideStore(initialize_data_root(tmp_path / "data"), "numeric-synthetic") as store:
        text = f"{body_term}是潮灯守卫，负责照亮码头。"
        doc = store.ingest(
            {
                "status": "read",
                "text": text,
                "url": "https://example.test/numeric",
                "title": "独立合成数字名称",
                "completeness": "full",
                "retrieved_at": now.isoformat(),
            },
            game="synthetic",
            platform="pc",
            mode="notes",
        )
        store.set_selection(
            "synthetic",
            "pc",
            "notes",
            doc["guide_id"],
            doc["revision_id"],
            expected_revision=store.revision(),
            request_id=uuid4().hex,
        )
        result = GuideRetriever(store).retrieve(f"{query_term}是什么意思？", now=now)
        assert result["status"] == ("sufficient" if supported else "gap")
        if supported:
            assert result["sources"][0]["text"] == text
            assert result["sources"][0]["revision_id"] == doc["revision_id"]
        else:
            assert result["sources"] == []


def test_numeric_sentence_keeps_ordinary_question_coverage(tmp_path):
    from test_guide_retrieval import NOW, save, select

    with GuideStore(initialize_data_root(tmp_path / "data"), "numeric-sentence") as store:
        doc = save(store)
        select(store, doc)
        result = GuideRetriever(store).retrieve(
            "蓝色水晶达到8枚能部署银鹰吗？", context={"game_version": "3.2"}, now=NOW
        )
        assert result["status"] == "sufficient"
        assert any("蓝色水晶达到 8 枚" in item["text"] for item in result["sources"])
