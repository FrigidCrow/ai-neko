# Copyright 2025-2026 Project N.E.K.O. Team
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#     http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.

"""N.E.K.O. tokenizer and BM25, isolated from its production runtime.

Modified for ai-neko: local pure dependencies, optional explicit stop terms,
Unicode case folding, deterministic query-term iteration. Callers MUST scope
the corpus before ranking. No model, filesystem, embedding or configuration IO.
Provenance and upgrade instructions: docs/MEMORY-REUSE.md.
"""

from __future__ import annotations

import math
import re

from .script_fold import fold_script

# From memory/persona/_shared.py:33-35 at the pinned upstream revision.
_SPLIT_RE = re.compile(
    r"[，。、！？；：\u201c\u201d\u2018\u2019（）()\[\]{}<>《》【】\s,.!?;:\-\u2014\u2026\xb7\u3000]+"
)
_LATIN_ALIAS_RE = re.compile(r"^[A-Za-z0-9_\-]+$")


def strip_stop_names(text: str, stop_names: list[str] | None) -> str:
    """Adapted from memory/stop_names.py:135-177; no config-manager access."""
    if not text or not stop_names:
        return text
    out = text
    for name in sorted(stop_names, key=len, reverse=True):
        if len(name) < 2:
            continue
        if _LATIN_ALIAS_RE.fullmatch(name):
            pattern = r"(?<![A-Za-z0-9_])" + re.escape(name) + r"(?![A-Za-z0-9_])"
            out = re.sub(pattern, " ", out, flags=re.IGNORECASE)
        else:
            out = out.replace(name, " ")
    return out


def tokenize(
    text: str,
    stop_names: list[str] | None = None,
    *,
    stop_terms: frozenset[str] = frozenset(),
) -> list[str]:
    """CJK 2/3-grams and Latin words, retaining multiplicity for BM25 TF.

    The original pure loop is from hybrid_recall.py:126-210. Traditional and
    Simplified queries share tokens; stored text is never rewritten.
    """
    raw_text = fold_script(str(text or "")).casefold()
    if stop_names:
        raw_text = strip_stop_names(
            raw_text, [fold_script(str(name)).casefold() for name in stop_names]
        )
    out: list[str] = []
    for seg in _SPLIT_RE.split(raw_text):
        seg = seg.strip()
        if not seg:
            continue
        cjk_count = sum(
            1 for ch in seg if "一" <= ch <= "鿿" or "぀" <= ch <= "ヿ" or "가" <= ch <= "힯"
        )
        if cjk_count > len(seg) // 2:
            for n in (2, 3):
                for i in range(len(seg) - n + 1):
                    out.append(seg[i : i + n])
        elif len(seg) >= 2:
            out.append(seg)
    return [term for term in out if term not in stop_terms]


def bm25_rank(
    query: str,
    pool: list[dict],
    *,
    stop_names: list[str] | None = None,
    stop_terms: frozenset[str] = frozenset(),
    k1: float = 1.5,
    b: float = 0.75,
) -> list[tuple[dict, float]]:
    """Adapted from hybrid_recall.py:216-294. Rows provide a ``text`` field.

    Return positive-score rows in descending order; retain the input order on
    ties. Empty and unrelated queries have no candidates. Scope filtering must
    occur before this function, so IDF never observes another user's corpus.
    """
    if not query or not pool:
        return []
    query_terms = tokenize(query, stop_names, stop_terms=stop_terms)
    if not query_terms:
        return []
    doc_terms_list = [
        tokenize(doc.get("text", "") or "", stop_names, stop_terms=stop_terms) for doc in pool
    ]
    n_docs = len(pool)
    total_len = sum(len(terms) for terms in doc_terms_list)
    if total_len == 0:
        return []
    avgdl = total_len / n_docs
    # Sorting changes only last-bit float accumulation versus upstream's set;
    # this makes output deterministic across PYTHONHASHSEED/process restarts.
    query_unique = sorted(set(query_terms))
    query_set = set(query_unique)
    df: dict[str, int] = dict.fromkeys(query_unique, 0)
    doc_tf_list: list[dict[str, int]] = []
    for terms in doc_terms_list:
        tf_map: dict[str, int] = {}
        for term in terms:
            if term in query_set:
                tf_map[term] = tf_map.get(term, 0) + 1
        doc_tf_list.append(tf_map)
        for term in tf_map:
            df[term] += 1
    scored: list[tuple[dict, float]] = []
    for doc, doc_terms, doc_tf in zip(pool, doc_terms_list, doc_tf_list):
        if not doc_terms:
            continue
        dl = len(doc_terms)
        norm = 1.0 - b + b * dl / avgdl
        score = 0.0
        for query_term in query_unique:
            n = df.get(query_term, 0)
            if n <= 0:
                continue
            idf = math.log((n_docs - n + 0.5) / (n + 0.5) + 1.0)
            if idf <= 0:
                continue
            tf = doc_tf.get(query_term, 0)
            if tf == 0:
                continue
            score += idf * (tf * (k1 + 1)) / (tf + k1 * norm)
        if score > 0:
            scored.append((doc, score))
    scored.sort(key=lambda pair: pair[1], reverse=True)
    return scored
