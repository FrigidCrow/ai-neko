"""Retriever seam: citation contract and the personal-memory adapter."""

import asyncio

from ai_neko.config.paths import initialize_data_root
from ai_neko.memory import MemoryService
from ai_neko.retrieval import Citation, MemoryRetriever


def test_citation_is_frozen_with_local_defaults():
    citation = Citation(local=True, title="喜欢无糖咖啡")
    assert citation.url == "" and citation.guide_id == "" and citation.source_ids == ()
    try:
        citation.local = False
    except AttributeError:
        pass
    else:
        raise AssertionError("Citation must be frozen")


def test_memory_retriever_maps_facts_to_local_citations(tmp_path):
    paths = initialize_data_root(tmp_path / "retrieval")
    with MemoryService(paths) as memory:
        memory.remember("喜欢无糖咖啡", source_id="turn:one", kind="preference")
        memory.remember("偏爱蓝色", source_id="turn:two", kind="preference")
        retriever = MemoryRetriever(memory)
        citations = asyncio.run(retriever.recall("我喜欢喝什么"))
        assert citations and all(c.local for c in citations)
        assert all(c.fact_id for c in citations)
        assert any("咖啡" in citation.title for citation in citations)
        assert all(citation.url == "" and citation.guide_id == "" for citation in citations)


def test_memory_retriever_rejects_empty_queries(tmp_path):
    paths = initialize_data_root(tmp_path / "empty")
    with MemoryService(paths) as memory:
        retriever = MemoryRetriever(memory)
        assert asyncio.run(retriever.recall("")) == []
        assert asyncio.run(retriever.recall("   ")) == []
