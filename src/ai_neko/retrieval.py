"""Retrieval seam reserved for G3: citations before any web tool round.

The conversation graph consumes citations; it does not own retrieval. This
module fixes that contract now so G1/G3 can add the guide-library retriever
without touching the graph's shape:

- ``Citation`` identifies a piece of local evidence (personal fact today,
  guide document/revision/chunk once G1 lands). ``local`` distinguishes
  already-saved material from web sources gathered this turn.
- ``Retriever`` is the protocol: recall runs off the event loop, returns
  bounded citations, and carries no tool or network authority.

The graph integration itself (local-first routing, skipping the plan/tools
rounds) is G3 work and deliberately NOT wired here.
"""

from __future__ import annotations

import asyncio
import functools
from dataclasses import dataclass, field
from typing import Any, Protocol


@dataclass(frozen=True)
class Citation:
    local: bool
    title: str
    url: str = ""
    # Guide-library identity (G1); empty for personal-memory citations.
    guide_id: str = ""
    revision_id: str = ""
    chunk_id: str = ""
    # Personal-memory identity (today's adapter).
    fact_id: str = ""
    source_ids: tuple[str, ...] = field(default=())


class Retriever(Protocol):
    async def recall(self, query: str, *, limit: int = 10) -> list[Citation]:
        """Return up to ``limit`` local citations relevant to ``query``."""
        ...


class MemoryRetriever:
    """Adapts the personal-fact MemoryService to the citation contract."""

    def __init__(self, memory: Any):
        self._memory = memory

    async def recall(self, query: str, *, limit: int = 10) -> list[Citation]:
        if not isinstance(query, str) or not query.strip():
            return []
        facts = await asyncio.to_thread(
            functools.partial(self._memory.recall, query[:2000], limit=limit)
        )
        return [
            Citation(
                local=True,
                title=fact["content"][:80],
                fact_id=fact["id"],
                source_ids=tuple(fact.get("source_ids", ())),
            )
            for fact in facts
        ]
