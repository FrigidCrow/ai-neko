"""Bounded, adopted-revision retrieval over the Memory Service guide authority.

The lexical gate is an abstention heuristic, not an answer verifier. It rejects
uncovered subjects even when generic terms overlap; the answering model must
still distinguish what the cited text establishes from what remains unknown.
No model or network call, independent durable index, or inferred game state.
"""

from __future__ import annotations

import math
import re
from datetime import UTC, datetime

from .guides import GuideConflictError, GuideInputError, GuideStore
from .recall import bm25_rank, tokenize
from .script_fold import fold_script

MAX_CHUNKS = 6
MAX_CONTEXT_CHARACTERS = 8_000
CHECK_INTERVAL_SECONDS = 86_400
OBSERVATION_SECONDS = 120
# Grammatical/question scaffolding, deliberately independent of any game or
# evaluation corpus. Substantive unknown nouns remain visible to the gate.
_SCAFFOLD = re.compile(
    r"这份|那份|当前采用的|已保存的|攻略里|攻略中|攻略|文档中|文档|笔记中|笔记|"
    r"最新版本|最新版|最新|当前版本|现在的版本|新版本|更新后的|"
    r"里说的|说的|建议|应该|应当|请问|请从|请给|请|现在是|我玩的是|我玩|我打|"
    r"到底|究竟|怎样|怎么|如何|什么|哪个|哪些|哪种|哪边|哪位|多少|几名|几枚|"
    r"才会|它会|他们会|它们会|我会|你会|我们会|"
    r"几颗|几次|几个|完整|顺序|需要|才能|是不是|是否|是什么意思|意思是|"
    r"是什么意思|有什么|会不会|可以|能不能|不能|能否|直接|重新|之后|以后|"
    r"然后|还是|应该由|由哪|要用|该用|该怎么|的时候|找出|查找|查询|"
    r"(?:\b(?:a|an|the|what|which|how|where|when|why|is|are|was|were|do|does|"
    r"should|would|could|can|in|on|of|to|for|with|this|that|it|guide|document)\b)",
    re.IGNORECASE,
)
_FOLLOW_UP = re.compile(
    r"^(?:(?:那|再|接着|然后|现在|好|好的)[，,\s]*)?"
    r"(?:下一步|接下来|然后|接着)(?:呢|怎么办|怎么做|做什么|该做什么|该怎么办|该怎么做)?[？?。！!\s]*$"
    r"|^(?:what(?:'s| is)? next|and then|next step)[?!.\s]*$",
    re.IGNORECASE,
)
_LATEST = re.compile(
    r"最新|现行|当前版本|现在的版本|新版本|更新后的|(?:\blatest(?:\s+version)?\b|\bup.to.date\b|\bcurrent version\b)",
    re.IGNORECASE,
)
_SENSITIVE = re.compile(
    r"多少|几[枚颗名次个点]|门槛|阈值|消耗|升[级到至]|购买|刷新|开局|操作|"
    r"下一步|接下来|先.{0,12}再|应该先|先.{0,12}还是|怎么[做按]|该怎么|"
    r"(?:\bcost\b|\bupgrade\b|\bbuy\b|\bdamage\b|\bhow many\b|\bnext step\b)",
    re.IGNORECASE,
)
_CJK = re.compile(r"[\u4e00-\u9fff]+")
_LATIN = re.compile(r"[a-z][a-z0-9_-]+")
_OBSERVATION_KINDS = frozenset(
    {"explicit_user_description", "user_description", "user_correction", "vision", "screenshot"}
)


def requests_latest(query: str) -> bool:
    """Whether this turn explicitly requests rechecking rather than cached search."""
    return isinstance(query, str) and bool(_LATEST.search(query))


def _normalized(value: object) -> str:
    return fold_script(value.strip()).casefold() if isinstance(value, str) else ""


def _timestamp(value: object) -> float | None:
    try:
        if isinstance(value, datetime):
            parsed = value
        elif isinstance(value, str):
            parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
        elif type(value) in (int, float) and math.isfinite(value):
            return float(value)
        else:
            return None
        if parsed.tzinfo is None:
            return None
        return parsed.timestamp()
    except (ValueError, OverflowError, OSError):
        return None


def _covered_query(query: str, text: str, metadata: dict) -> bool:
    """Require substantive overlap and abstain on an unattested query subject.

    Mark characters occurring in an attested CJK bigram; a remaining run of four
    characters generally denotes an unsupported subject rather than an omitted
    particle. Exact quoted names and Latin words must be attested in full. This
    avoids treating 'known resource + invented item + quantity' as supported.
    It intentionally favors an explicit evidence gap over speculative recall.
    """
    body = _normalized(text)
    query = _normalized(query)
    for term in re.findall(r'[“"「『](.+?)[”"」』]', query):
        if len(term.strip()) >= 2 and term.strip() not in body:
            return False
    for key in ("game", "platform", "mode", "game_version"):
        value = _normalized(metadata.get(key))
        if value:
            query = query.replace(value, " ")
    query = _SCAFFOLD.sub(" ", _LATEST.sub(" ", query))
    terms = re.findall(r"[\u4e00-\u9fffa-z0-9_-]+", query)
    mixed_identifier = len(terms) == 1 and re.fullmatch(
        r"[\u4e00-\u9fff]+[0-9]+[a-z0-9_-]*", terms[0]
    )
    # The shared tokenizer treats a short mixed name such as 灯芯10 as one
    # token, while a Chinese sentence yields only bigrams/trigrams. Require
    # exact evidence for this single subject, including its numeric boundary;
    # overlapping Chinese grams must not turn 灯芯100 into evidence for 灯芯10.
    if mixed_identifier:
        if not re.search(r"(?<![a-z0-9_-])" + re.escape(terms[0]) + r"(?![a-z0-9_-])", body):
            return False
    elif not set(tokenize(query)).intersection(tokenize(body)):
        return False
    body_words = set(_LATIN.findall(body))
    if any(word not in body_words for word in _LATIN.findall(query)):
        return False
    for span in _CJK.findall(query):
        covered = [False] * len(span)
        for index in range(len(span) - 1):
            if span[index : index + 2] in body:
                covered[index : index + 2] = (True, True)
        residual = "".join(
            " " if known else char for char, known in zip(span, covered, strict=True)
        )
        if any(len(run) >= 4 for run in residual.split()):
            return False
    return True


def _follow_up_query(query: str, context: dict, selection: dict, now: float) -> str | None:
    match_id = context.get("match_id")
    if not isinstance(match_id, str) or not match_id or context.get("status") != "active":
        return None
    observations = []
    for item in context.get("observations", [])[:32]:
        if not isinstance(item, dict):
            continue
        observed = _timestamp(item.get("observed_at"))
        expires = _timestamp(item.get("expires_at"))
        if (
            item.get("match_id") == match_id
            and item.get("source_kind") in _OBSERVATION_KINDS
            and observed is not None
            and 0 <= now - observed <= OBSERVATION_SECONDS
            and ("expires_at" not in item or expires is not None and now <= expires)
            and isinstance(item.get("text"), str)
        ):
            observations.append(item["text"][:1_000])
    if not observations:
        return None
    parts = [query, "本局有效观察：" + "\n".join(observations)]
    goal = context.get("goal")
    if isinstance(goal, str) and goal:
        parts.append("本局目标：" + goal[:1_000])
    advice = context.get("last_delivered_advice")
    if (
        isinstance(advice, dict)
        and advice.get("delivered") is True
        and advice.get("match_id") == match_id
        and advice.get("guide_id", selection["guide_id"]) == selection["guide_id"]
        and advice.get("revision_id", selection["revision_id"]) == selection["revision_id"]
        and isinstance(advice.get("text"), str)
    ):
        delivered = _timestamp(advice.get("delivered_at"))
        if delivered is not None and delivered <= now:
            parts.append("本局已投递建议（不代表用户已执行）：" + advice["text"][:1_000])
    return "\n".join(parts)[:MAX_CONTEXT_CHARACTERS]


class GuideRetriever:
    def __init__(self, store: GuideStore):
        self.store = store

    def retrieve(
        self,
        query: str,
        *,
        context: dict | None = None,
        expected_revision: int | None = None,
        now: datetime | str | float | None = None,
        limit: int = MAX_CHUNKS,
        budget: int = MAX_CONTEXT_CHARACTERS,
        checked_this_turn: bool = False,
    ) -> dict:
        if type(checked_this_turn) is not bool:
            raise GuideInputError("invalid_guide_check_receipt")
        if not isinstance(query, str) or len(query) > 16_000:
            raise GuideInputError("invalid_guide_query")
        if context is not None and not isinstance(context, dict):
            raise GuideInputError("invalid_guide_context")
        if context is not None:
            for key in (
                "game",
                "game_id",
                "platform",
                "mode",
                "game_version",
                "match_id",
                "status",
            ):
                value = context.get(key)
                if value is not None and (not isinstance(value, str) or len(value) > 200):
                    raise GuideInputError("invalid_guide_context")
            if "observations" in context and not isinstance(context["observations"], list):
                raise GuideInputError("invalid_guide_context")
            if (
                context.get("game")
                and context.get("game_id")
                and (_normalized(context["game"]) != _normalized(context["game_id"]))
            ):
                raise GuideInputError("ambiguous_guide_game_context")
        if type(limit) is not int or limit < 1 or type(budget) is not int or budget < 1:
            raise GuideInputError("invalid_guide_retrieval_budget")
        if expected_revision is not None and (
            type(expected_revision) is not int or expected_revision < 0
        ):
            raise GuideInputError("invalid_guide_expected_revision")
        timestamp = datetime.now(UTC).timestamp() if now is None else _timestamp(now)
        if timestamp is None:
            raise GuideInputError("invalid_guide_retrieval_time")
        limit, budget = min(limit, MAX_CHUNKS), min(budget, MAX_CONTEXT_CHARACTERS)
        context = context or {}
        # The guard prevents selection/delete/restore racing the immutable body
        # read. LRU access bookkeeping is the store's only permitted side effect.
        with self.store._guard:
            snapshot = self.store.control_snapshot()
            revision = snapshot["revision"]
            if expected_revision is not None and expected_revision != revision:
                raise GuideConflictError("guide_revision_changed")
            result = {
                "status": "gap",
                "reason": "no_selection",
                "sources": [],
                "selection": None,
                "version_status": "unknown",
                "revision": revision,
                "query": query,
                "partial_context": False,
                "needs_revalidation": False,
                "update_available": False,
            }
            selections = snapshot["selections"]
            conditions = {
                "game": context.get("game", context.get("game_id")),
                "platform": context.get("platform"),
                "mode": context.get("mode"),
            }
            selections = [
                item
                for item in selections
                if all(
                    not _normalized(value) or _normalized(item[key]) == _normalized(value)
                    for key, value in conditions.items()
                )
            ]
            if not selections:
                if snapshot["selections"]:
                    result["reason"] = "conditions_conflict"
                return result
            if len(selections) != 1:
                return result | {"status": "clarify", "reason": "ambiguous_selection"}
            selection = selections[0]
            result["selection"] = selection
            document = self.store.get_document(selection["guide_id"], selection["revision_id"])
            chunks = self.store.chunks(selection["revision_id"])
        result["update_available"] = document["current_revision_id"] != selection["revision_id"]
        # This trusted Runtime receipt only proves that this turn checked the
        # unchanged source. It does not establish game version/applicability or
        # answer coverage, and must never be read from untrusted context fields.
        explicit_latest = requests_latest(query) and not checked_this_turn
        if explicit_latest:
            result.update(status="needs_check", reason="explicit_latest", needs_revalidation=True)
        current_version = _normalized(context.get("game_version"))
        source_version = _normalized(document.get("game_version"))
        result["version_status"] = (
            "matched"
            if current_version and source_version == current_version
            else "unconfirmed"
            if source_version
            else "unknown"
        )
        if current_version and source_version and current_version != source_version:
            return result | {
                "status": "gap",
                "reason": "version_conflict",
                "version_status": "conflict",
                "needs_revalidation": False,
            }
        short_follow_up = bool(_FOLLOW_UP.fullmatch(query.strip()))
        expanded = (
            _follow_up_query(query, context, selection, timestamp) if short_follow_up else query
        )
        if expanded is None:
            return result | {"status": "clarify", "reason": "missing_current_observation"}
        result["query"] = expanded
        ranked = bm25_rank(expanded, chunks)
        if not ranked:
            return result if explicit_latest else result | {"reason": "no_relevant_passage"}
        # Follow-up expansion includes labels and observed values which need not
        # literally appear in the guide. Its applicability gate is fresh scoped
        # observations; ordinary questions also require subject coverage.
        if not short_follow_up and not _covered_query(query, document["text"], document):
            return result if explicit_latest else result | {"reason": "uncovered_subject"}
        sources = []
        remaining = budget
        for chunk, score in ranked[:limit]:
            if not remaining:
                break
            text = chunk["text"][:remaining]
            source = {
                key: document.get(key)
                for key in (
                    "url",
                    "original_url",
                    "final_url",
                    "title",
                    "last_checked_at",
                    "retrieved_at",
                    "content_date",
                    "game",
                    "platform",
                    "mode",
                    "game_version",
                    "version_basis",
                    "completeness",
                    "completeness_reasons",
                    "retained_characters",
                    "extracted_characters",
                    "content_hash",
                )
            }
            source.update(
                status="read",
                local=True,
                guide_id=selection["guide_id"],
                revision_id=selection["revision_id"],
                selection_revision=selection["selection_revision"],
                chunk_id=chunk["chunk_id"],
                chunk_start=chunk["start"],
                chunk_end=chunk["end"],
                start=chunk["start"],
                end=chunk["start"] + len(text),
                text=text,
                headings=chunk["headings"],
                context_truncated=len(text) < len(chunk["text"]),
                score=score,
                version_status=result["version_status"],
            )
            sources.append(source)
            remaining -= len(text)
        result.update(
            sources=sources,
            status="sufficient",
            reason="local_evidence",
            partial_context=len(sources) < len(ranked)
            or any(source["context_truncated"] for source in sources),
        )
        if any(source["context_truncated"] for source in sources):
            # A clipped snippet may lose the qualifying clause/answer. Keep the
            # locator for display, but never label a cut answer as sufficient.
            result.update(status="gap", reason="context_budget")
        checked_at = _timestamp(document.get("last_checked_at"))
        result["freshness"] = (
            "fresh"
            if checked_at is not None and 0 <= timestamp - checked_at <= CHECK_INTERVAL_SECONDS
            else "stale"
        )
        if explicit_latest:
            result.update(status="needs_check", reason="explicit_latest", needs_revalidation=True)
        elif result["update_available"]:
            result.update(status="needs_check", reason="update_available")
        elif result["freshness"] == "stale":
            result.update(status="needs_check", reason="stale", needs_revalidation=True)
        elif result["version_status"] != "matched" and (
            short_follow_up or _SENSITIVE.search(query)
        ):
            result.update(status="needs_check", reason="version_unverified")
        return result
