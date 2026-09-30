"""Read-only local-first query orchestration, owned by the existing Runtime/graph."""

from __future__ import annotations

import sqlite3
import time

from ai_neko.config.providers import ProviderStore
from ai_neko.memory.guide_retrieval import requests_latest
from ai_neko.memory.guides import GuideInputError
from ai_neko.tools.web import WebTools


def web_adapter(runtime):
    if isinstance(runtime.providers, ProviderStore):
        return runtime.providers.web_tools(scope=runtime.memory.guides.scope)
    return runtime.providers.web_tools()


class LazyWebTools:
    """A local hit requires no search configuration or network adapter creation."""

    def __init__(self, runtime):
        self.runtime = runtime
        self.adapter = None
        self.force_refresh = False

    async def execute(self, name, arguments):
        if self.adapter is None:
            self.adapter = web_adapter(self.runtime)
        if isinstance(self.adapter, WebTools):
            return await self.adapter.execute(name, arguments, force_refresh=self.force_refresh)
        return await self.adapter.execute(name, arguments)


def validators(document):
    result = {"url": document["final_url"]}
    result.update({key: document[key] for key in ("etag", "last_modified") if document.get(key)})
    return result if len(result) > 1 else None


async def read_selected(adapter, document):
    if isinstance(adapter, WebTools):
        return await adapter.read(document["original_url"], validators=validators(document))
    return await adapter.execute("read_web_page", {"url": document["original_url"]})


async def local_query(runtime, session_id, turn_id, query, *, network, web, context=None, now=None):
    expected = runtime._guide_turn_revisions[turn_id]
    started = time.monotonic()
    current = context() if callable(context) else context
    if current and current.get("history_only"):
        runtime._assert_guide_turn(session_id, turn_id)
        return {"status": "historical", "reason": "explicit_review", "sources": []}

    async def retrieve(*, checked_this_turn=False):
        retrieval_started = time.monotonic()
        result = await runtime._memory_call(
            runtime.guide_retriever.retrieve,
            query,
            context=context() if callable(context) else context,
            expected_revision=expected,
            now=now,
            checked_this_turn=checked_this_turn,
        )
        runtime.metrics.record(
            "guide_retrieve_ms", (time.monotonic() - retrieval_started) * 1000, scope="guides"
        )
        runtime._assert_guide_turn(session_id, turn_id)
        return result

    result = await retrieve()
    web.force_refresh = requests_latest(query)
    if network and result.get("needs_revalidation") and result.get("selection"):
        selected = result["selection"]
        document = await runtime._memory_call(
            runtime.memory.guides.get_document,
            selected["guide_id"],
            selected["revision_id"],
        )
        runtime._assert_guide_turn(session_id, turn_id)
        try:
            adapter = web_adapter(runtime)
            check_started = time.monotonic()
            try:
                checked = await read_selected(adapter, document)
            finally:
                runtime.metrics.record(
                    "guide_revalidate_ms", (time.monotonic() - check_started) * 1000, scope="guides"
                )
            runtime._assert_guide_turn(session_id, turn_id)
            async with runtime._mutation_lock:
                runtime._assert_guide_turn(session_id, turn_id)
                if checked.get("status") == "not_modified":
                    if checked.get("final_url") != document["final_url"] or not validators(
                        document
                    ):
                        raise GuideInputError("invalid_conditional_response")
                    saved = await runtime._memory_call(
                        runtime.memory.guides.mark_checked,
                        document["guide_id"],
                        document["revision_id"],
                        checked_at=checked["checked_at"],
                        expected_revision=expected,
                    )
                    revalidation = {
                        "status": "not_modified",
                        "checked_at": saved["last_checked_at"],
                    }
                elif checked.get("status") == "ok" and checked.get("sources"):
                    source = checked["sources"][0]
                    saved = await runtime._memory_call(
                        runtime.memory.guides.ingest,
                        source,
                        expected_revision=expected,
                        **{key: document[key] for key in ("game", "platform", "mode")},
                    )
                    revalidation = {
                        "status": "unchanged"
                        if saved["revision_id"] == document["revision_id"]
                        else "update_available",
                        "guide_id": saved["guide_id"],
                        "revision_id": saved["revision_id"],
                    }
                else:
                    raise GuideInputError("guide_refresh_failed")
            # Only this turn's actual unchanged check fulfills a latest request;
            # all coverage, version and budget gates still run on the same query.
            result = await retrieve(
                checked_this_turn=revalidation["status"] in {"not_modified", "unchanged"}
            )
            result["revalidation"] = revalidation
        except (ValueError, OSError, sqlite3.Error):
            runtime._assert_guide_turn(session_id, turn_id)
            result["revalidation"] = {"status": "failed"}
            result["status"] = "needs_check"
    runtime._assert_guide_turn(session_id, turn_id)
    # Commit actual source dependencies before graph events or model input. This
    # path never reinserts local chunks as new documents or personal facts.
    from ai_neko.runtime.guides import guide_source_hashes

    with runtime._db:
        for source in result.get("sources", []):
            runtime._db.execute(
                "INSERT OR IGNORE INTO turn_guides VALUES (?,?,?,0)",
                (turn_id, source["guide_id"], source["revision_id"]),
            )
            runtime._db.executemany(
                "INSERT OR IGNORE INTO turn_guide_sources VALUES (?,?,?)",
                [(turn_id, kind, value) for kind, value in guide_source_hashes(source)],
            )
    runtime.metrics.record("guide_query_ms", (time.monotonic() - started) * 1000, scope="guides")
    return result
