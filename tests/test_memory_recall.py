"""Functional checks for the extracted N.E.K.O. lexical retrieval boundary."""

import importlib.util
import json
import subprocess
import sys
from pathlib import Path

import pytest

from ai_neko.config.paths import initialize_data_root
from ai_neko.memory import MemoryService
from ai_neko.memory.recall import bm25_rank, strip_stop_names, tokenize
from ai_neko.memory.script_fold import fold_script


def test_bm25_prefers_relevant_concise_fact_over_long_unrelated_context():
    pool = [
        {"id": "long", "text": "猫咪 " + "天气很好 " * 80},
        {"id": "specific", "text": "猫咪喜欢鱼肉"},
        {"id": "unrelated", "text": "周五乘坐火车"},
    ]
    ranked = bm25_rank("猫咪", pool)
    assert [doc["id"] for doc, _ in ranked] == ["specific", "long"]
    assert ranked[0][1] > ranked[1][1] > 0


def test_bm25_uses_term_frequency_and_latin_case_fold():
    pool = [{"id": "twice", "text": "coffee coffee tea"}, {"id": "once", "text": "coffee tea tea"}]
    assert [doc["id"] for doc, _ in bm25_rank("COFFEE", pool)] == ["twice", "once"]
    assert tokenize("coffee COFFEE").count("coffee") == 2
    assert tokenize("咖啡咖啡").count("咖啡") == 2


def test_traditional_simplified_queries_match_without_rewriting_facts(tmp_path):
    with MemoryService(initialize_data_root(tmp_path / "data")) as memory:
        fact = memory.remember("喜欢研究机器学习", source_id="synthetic")
        assert memory.recall("機器學習")[0]["id"] == fact["id"]
        assert memory.list_facts()[0]["content"] == "喜欢研究机器学习"
        assert memory.recall("我的喜好是什麼？")[0]["id"] == fact["id"]


def test_tokenizer_supports_japanese_korean_and_whole_latin_words():
    assert "ねこ" in tokenize("ねこ好き")
    assert "고양" in tokenize("고양이가좋아요")
    assert bm25_rank("cat", [{"text": "concatenate"}]) == []
    assert bm25_rank("impossible", [{"text": "coffee"}]) == []


def test_stop_names_preserve_word_boundaries_and_do_not_strip_single_characters():
    assert strip_stop_names("Algorithm Al", ["Al"]) == "Algorithm  "
    assert strip_stop_names("今天天气好", ["天"]) == "今天天气好"
    assert tokenize("小猫喜欢蓝色", ["小猫"])[0] == "喜欢"
    assert tokenize("我喜欢猫咪", stop_terms=frozenset({"我喜", "喜欢", "我喜欢"}))


def test_bm25_cannot_observe_another_scopes_facts(tmp_path):
    paths = initialize_data_root(tmp_path / "data")
    with MemoryService(paths) as first, MemoryService(paths, character_id="other") as second:
        local = first.remember("咖啡加牛奶", source_id="one")
        assert first.recall("咖啡")[0]["id"] == local["id"]
        for index in range(20):
            second.remember(f"别的角色咖啡偏好编号{index}", source_id=f"other-{index}")
        assert [fact["id"] for fact in first.recall("咖啡")] == [local["id"]]
        assert first.recall("别的角色") == []


@pytest.mark.parametrize(
    "query,pool", [("", [{"text": "cat"}]), ("cat", []), ("cat", [{"text": ""}])]
)
def test_empty_retrieval(query, pool):
    assert bm25_rank(query, pool) == []


def test_recall_import_has_no_reference_runtime_or_heavy_dependencies():
    code = """
import json,sys
from ai_neko.memory.recall import bm25_rank
assert bm25_rank('coffee',[{'text':'coffee'}])
print(json.dumps(sorted(set(sys.modules) & {'memory','config','numpy','torch','jieba',
    'sentence_transformers','opencc'})))
"""
    result = subprocess.run(
        [sys.executable, "-c", code], capture_output=True, text=True, check=True
    )
    assert json.loads(result.stdout) == []


def test_copied_fold_is_length_preserving_and_idempotent():
    text = "記憶機器學習貓咪軟體 coffee ねこ 고양이"
    folded = fold_script(text)
    assert len(folded) == len(text)
    assert fold_script(folded) == folded
    assert "记忆机器学习猫咪" in folded


def test_attribution_is_shipped_next_to_imported_sources():
    module = importlib.util.find_spec("ai_neko.memory.recall")
    folder = Path(module.origin).parent
    assert "Copyright 2025-2026 Project N.E.K.O. Team" in (folder / "recall.py").read_text(
        encoding="utf-8"
    )
    assert "Apache License" in (folder / "licenses/NEKO-LICENSE.txt").read_text(encoding="utf-8")
    assert "Hongzhi Wen" in (folder / "licenses/NEKO-NOTICE.txt").read_text(encoding="utf-8")
