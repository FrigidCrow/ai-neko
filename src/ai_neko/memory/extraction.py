"""Validate model proposals; stable identity and commit authority stay in MemoryService.

Evidence checks establish literal provenance and an explicit change context, not
proof of semantic entailment. Choosing the same entity/property remains a bounded
model judgment; uncertain targets must be omitted rather than merged by fuzzy keys.
"""

import re

MAX_CONTEXT_FACTS = 128
MAX_CONTEXT_CHARACTERS = 96000

_CHANGE_CONTEXT = re.compile(
    r"更正|纠正|改为|改成|替换|过时|不再|"
    r"(?:之前|以前)[\s\S]{1,1000}(?:现在|如今)|"
    r"\b(?:correct(?:ion)?|replace|instead|no longer)\b|"
    r"\b(?:previously|used to)\b[\s\S]{1,1000}\bnow\b",
    re.IGNORECASE,
)
_UNAUTHORIZED_CHANGE = re.compile(
    r"(?:不要|别|不必|不能|不应|勿)[^。！？\n]{0,40}(?:更正|纠正|改为|改成|替换)|"
    r"(?:如果|假如|假设|假設|倘若)[^。！？\n]{0,200}(?:更正|纠正|改为|改成|替换)|"
    r"\b(?:do not|don't|never|must not|should not|if|suppose|hypothetically)\b"
    r"[^.!?\n]{0,200}\b(?:correct|replace|instead)\b",
    re.IGNORECASE,
)


def _literal(value, source):
    return isinstance(value, str) and bool(value.strip()) and value in source


def validate_operations(
    facts: list[dict], source_text: str, existing_facts: list[dict]
) -> list[dict]:
    """Strict, whole-batch validation, repeated against authoritative data at commit."""
    if not isinstance(facts, list) or len(facts) > 10:
        raise ValueError("invalid_memory_extraction")
    existing = {fact["id"]: fact for fact in existing_facts}
    existing_keys = {fact["fact_key"] for fact in existing_facts}
    targets, keys, result = set(), set(), []
    correction_source = bool(_CHANGE_CONTEXT.search(source_text))
    for item in facts:
        if not isinstance(item, dict) or not _literal(item.get("quote"), source_text):
            raise ValueError("unverified_memory_fact")
        operation = item.get("operation")
        if operation == "create":
            if set(item) != {"operation", "content", "quote", "fact_key", "kind"}:
                raise ValueError("invalid_memory_operation")
            if correction_source:
                raise ValueError("memory_correction_requires_target")
            content, key = item["content"], item["fact_key"]
            if (
                not isinstance(content, str)
                or not 1 <= len(content.strip()) <= 1000
                or not isinstance(key, str)
                or not 1 <= len(key.strip()) <= 200
                or not isinstance(item["kind"], str)
                or item["kind"] not in {"preference", "event", "fact"}
            ):
                raise ValueError("unverified_memory_fact")
            key = key.strip()
            if key in existing_keys or key in keys:
                raise ValueError("memory_requires_existing_target")
            keys.add(key)
        elif isinstance(operation, str) and operation in {"correct", "confirm"}:
            if operation == "confirm" and correction_source:
                raise ValueError("memory_correction_requires_target")
            fields = {"operation", "target_fact_id", "target_revision", "quote"}
            if operation == "correct":
                fields |= {"content", "correction_quote", "previous_quote"}
            if set(item) != fields:
                raise ValueError("invalid_memory_operation")
            target_id = item["target_fact_id"]
            if not isinstance(target_id, str) or target_id not in existing or target_id in targets:
                raise ValueError("invalid_memory_target")
            target = existing[target_id]
            if (
                type(item["target_revision"]) is not int
                or item["target_revision"] != target["revision"]
            ):
                raise ValueError("stale_memory_target")
            targets.add(target_id)
            if operation == "correct":
                content, evidence = item["content"], item["correction_quote"]
                previous = item["previous_quote"]
                if (
                    not isinstance(content, str)
                    or not 1 <= len(content.strip()) <= 1000
                    or not _literal(evidence, source_text)
                    or item["quote"] not in evidence
                    or not _CHANGE_CONTEXT.search(evidence)
                    or _UNAUTHORIZED_CHANGE.search(source_text)
                    or not _literal(previous, source_text)
                    or not _literal(previous, target["content"])
                    or sum(previous in fact["content"] for fact in existing_facts) != 1
                    or (
                        previous != target["content"]
                        and sum(character.isalnum() for character in previous) < 4
                    )
                ):
                    raise ValueError("unverified_memory_correction")
        else:
            raise ValueError("invalid_memory_operation")
        result.append(dict(item))
    return result
