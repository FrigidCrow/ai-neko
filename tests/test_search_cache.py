import asyncio
import json

import pytest
from test_web_tools import dns as dns
from test_web_tools import reply
from test_web_tools import web_http as web_http

from ai_neko.config.paths import initialize_data_root
from ai_neko.config.providers import ProviderStore
from ai_neko.tools.search_cache import SearchCache, SearchCacheError
from ai_neko.tools.web import WebTools, source


def result(label="snippet"):
    return {
        "status": "ok",
        "sources": [source("https://example.com/guide", status="snippet", snippet=label)],
        "error": None,
    }


async def lookup(cache, fetch, *, scope="scope-a", query="guide", locale="zh-CN", force=False):
    return await cache.get(
        scope=scope,
        generation=cache.generation,
        query=query,
        locale=locale,
        fetch=fetch,
        force_refresh=force,
    )


def test_ttl_is_exactly_120_seconds_and_copies_are_isolated():
    async def scenario():
        now = [10.0]
        cache = SearchCache(clock=lambda: now[0])
        calls = []

        async def fetch():
            calls.append(1)
            return result(str(len(calls)))

        first = await lookup(cache, fetch)
        first["sources"][0]["snippet"] = "caller edit"
        now[0] += 119.999
        second = await lookup(cache, fetch)
        assert second["cache"]["status"] == "hit"
        assert second["sources"][0]["snippet"] == "1"
        assert second["sources"][0]["retrieved_at"] == first["sources"][0]["retrieved_at"]
        second["sources"].clear()
        now[0] = 130.0
        third = await lookup(cache, fetch)
        assert third["cache"]["status"] == "miss"
        assert third["sources"][0]["snippet"] == "2"
        assert len(calls) == 2
        await cache.close()

    asyncio.run(scenario())


def test_64_entries_evict_least_recently_used():
    async def scenario():
        cache = SearchCache()
        calls = []

        async def fetch():
            calls.append(1)
            return result()

        for index in range(64):
            await lookup(cache, fetch, query=str(index))
        assert (await lookup(cache, fetch, query="0"))["cache"]["status"] == "hit"
        await lookup(cache, fetch, query="64")
        assert len(cache._entries) == 64
        assert (await lookup(cache, fetch, query="0"))["cache"]["status"] == "hit"
        assert (await lookup(cache, fetch, query="1"))["cache"]["status"] == "miss"
        assert len(calls) == 66
        await cache.close()

    asyncio.run(scenario())


def test_scope_locale_query_generation_and_explicit_latest_are_separate():
    async def scenario():
        cache = SearchCache()
        calls = []

        async def fetch():
            calls.append(1)
            return result(str(len(calls)))

        for options in ({}, {"scope": "scope-b"}, {"locale": "en-US"}, {"query": "new"}):
            assert (await lookup(cache, fetch, **options))["cache"]["status"] == "miss"
        assert (await lookup(cache, fetch))["cache"]["status"] == "hit"
        assert (await lookup(cache, fetch, force=True))["cache"]["status"] == "miss"
        assert (await lookup(cache, fetch))["sources"][0]["snippet"] == "5"
        cache.advance_generation()
        assert (await lookup(cache, fetch))["cache"]["status"] == "miss"
        assert len(calls) == 6
        await cache.close()

    asyncio.run(scenario())


@pytest.mark.parametrize("behavior", ["error", "exception", "body"])
def test_errors_exceptions_and_page_bodies_are_never_cached(behavior):
    async def scenario():
        cache = SearchCache()
        calls = []

        async def fetch():
            calls.append(1)
            if behavior == "exception":
                raise ValueError("synthetic failure")
            if behavior == "body":
                return {"status": "ok", "sources": [{"status": "read", "text": "private body"}]}
            return {"status": "error", "sources": [], "error": "network_error"}

        for _ in range(2):
            if behavior == "exception":
                with pytest.raises(ValueError, match="synthetic failure"):
                    await lookup(cache, fetch)
            else:
                await lookup(cache, fetch)
        assert len(calls) == 2 and not cache._entries
        assert not cache._flights and not cache._tasks
        await cache.close()

    asyncio.run(scenario())


def test_two_waiters_share_execution_and_cancel_one_does_not_cancel_the_other():
    async def scenario():
        cache = SearchCache()
        started, finish = asyncio.Event(), asyncio.Event()
        calls = []

        async def fetch():
            calls.append(1)
            started.set()
            await finish.wait()
            return result()

        first = asyncio.create_task(lookup(cache, fetch))
        await started.wait()
        second = asyncio.create_task(lookup(cache, fetch))
        await asyncio.sleep(0)
        first.cancel()
        with pytest.raises(asyncio.CancelledError):
            await first
        assert len(cache._tasks) == 1 and not second.done()
        finish.set()
        answer = await second
        assert answer["cache"]["status"] == "coalesced"
        assert len(calls) == 1 and not cache._tasks and not cache._flights
        await cache.close()

    asyncio.run(scenario())


@pytest.mark.parametrize("swallow", [False, True])
def test_last_waiter_cancellation_drains_request_and_never_stores_late_result(swallow):
    async def scenario():
        cache = SearchCache()
        started, released = asyncio.Event(), asyncio.Event()

        async def fetch():
            started.set()
            try:
                await asyncio.Event().wait()
            except asyncio.CancelledError:
                if swallow:
                    return result("late result")
                raise
            finally:
                released.set()

        caller = asyncio.create_task(lookup(cache, fetch))
        await started.wait()
        caller.cancel()
        with pytest.raises(asyncio.CancelledError):
            await caller
        assert released.is_set()
        assert not cache._tasks and not cache._flights and not cache._entries
        await cache.close()

    asyncio.run(scenario())


@pytest.mark.parametrize("kind", ["scope", "all", "config", "close"])
def test_invalidation_and_shutdown_revoke_in_flight_results(kind):
    async def scenario():
        cache = SearchCache()
        started = asyncio.Event()

        async def fetch():
            started.set()
            try:
                await asyncio.Event().wait()
            except asyncio.CancelledError:
                return result("late deleted source")

        caller = asyncio.create_task(lookup(cache, fetch))
        await started.wait()
        if kind == "scope":
            await asyncio.to_thread(cache.invalidate, "scope-a")
        elif kind == "all":
            cache.invalidate()
        elif kind == "config":
            cache.advance_generation()
        else:
            await cache.close()
        with pytest.raises(SearchCacheError, match="invalidated"):
            await caller
        assert not cache._tasks and not cache._flights and not cache._entries
        await cache.close()

    asyncio.run(scenario())


def test_scope_delete_retains_other_scope_and_does_not_revive_old_cache():
    async def scenario():
        cache = SearchCache()

        async def fetch():
            return result()

        await lookup(cache, fetch)
        await lookup(cache, fetch, scope="scope-b")
        cache.invalidate("scope-a")
        assert (await lookup(cache, fetch))["cache"]["status"] == "miss"
        assert (await lookup(cache, fetch, scope="scope-b"))["cache"]["status"] == "hit"
        await cache.close()

    asyncio.run(scenario())


def test_concurrent_explicit_refresh_cannot_be_overwritten_by_older_normal_request():
    async def scenario():
        cache = SearchCache()
        started, finish = asyncio.Event(), asyncio.Event()

        async def old():
            started.set()
            await finish.wait()
            return result("old")

        async def fresh():
            return result("new")

        caller = asyncio.create_task(lookup(cache, old))
        await started.wait()
        assert (await lookup(cache, fresh, force=True))["sources"][0]["snippet"] == "new"
        finish.set()
        await caller
        assert (await lookup(cache, old))["sources"][0]["snippet"] == "new"
        await cache.close()

    asyncio.run(scenario())


def test_oversubscribed_requests_are_bounded_and_close_releases_every_waiter():
    async def scenario():
        cache = SearchCache()

        async def fetch():
            await asyncio.Event().wait()

        callers = [asyncio.create_task(lookup(cache, fetch, query=str(i))) for i in range(64)]
        await asyncio.sleep(0)
        with pytest.raises(SearchCacheError, match="busy"):
            await lookup(cache, fetch, query="overflow")
        await cache.close()
        outcomes = await asyncio.gather(*callers, return_exceptions=True)
        assert all(isinstance(value, asyncio.CancelledError) for value in outcomes)
        assert not cache._tasks and not cache._flights
        with pytest.raises(SearchCacheError, match="closed"):
            await lookup(cache, fetch)

    asyncio.run(scenario())


def test_provider_shares_cache_across_tools_and_invalidates_environment_credentials(
    tmp_path, monkeypatch
):
    async def scenario():
        monkeypatch.setenv("AI_NEKO_SEARCH_API_KEY", "synthetic-key-a")
        store = ProviderStore(initialize_data_root(tmp_path / "cache-owned"))
        calls = []

        async def fetch(self, query):
            calls.append((self._key, query))
            return result()

        monkeypatch.setattr(WebTools, "_search", fetch)
        old = store.web_tools(scope="scope-a")
        first = await old.execute("search_web", {"query": "guide"})
        second = await store.web_tools(scope="scope-a").execute("search_web", {"query": "guide"})
        assert first["cache"]["status"] == "miss" and second["cache"]["status"] == "hit"
        await store.web_tools(scope="scope-b").execute("search_web", {"query": "guide"})
        monkeypatch.setenv("AI_NEKO_SEARCH_API_KEY", "synthetic-key-b")
        fresh = store.web_tools(scope="scope-a")
        assert (await fresh.execute("search_web", {"query": "guide"}))["cache"]["status"] == "miss"
        assert (await old.execute("search_web", {"query": "guide"}))[
            "error"
        ] == "search_configuration_changed"
        assert len(calls) == 3
        assert "synthetic-key" not in repr(store.search_cache.__dict__)
        assert "synthetic-key" not in json.dumps(store.public_config())
        await store.close_search_cache()

    asyncio.run(scenario())


def test_provider_config_update_invalidates_cache_even_from_worker_thread(tmp_path, monkeypatch):
    async def scenario():
        monkeypatch.setenv("AI_NEKO_SEARCH_API_KEY", "synthetic-key")
        store = ProviderStore(initialize_data_root(tmp_path / "cache-owned"))

        async def fetch(self, query):
            return result()

        monkeypatch.setattr(WebTools, "_search", fetch)
        old = store.web_tools(scope="scope-a")
        await old.execute("search_web", {"query": "guide"})
        await asyncio.to_thread(store.update, {"search_base_url": "https://search.example.com"})
        assert not store.search_cache._entries
        assert (await old.execute("search_web", {"query": "guide"}))[
            "error"
        ] == "search_configuration_changed"
        fresh = store.web_tools(scope="scope-a")
        assert (await fresh.execute("search_web", {"query": "guide"}))["cache"]["status"] == "miss"
        store.invalidate_search_cache("scope-a")
        assert not store.search_cache._entries
        await store.close_search_cache()

    asyncio.run(scenario())


def test_provider_key_rotation_and_failed_update_cache_boundaries(tmp_path, monkeypatch):
    async def scenario():
        monkeypatch.delenv("AI_NEKO_SEARCH_API_KEY", raising=False)
        store = ProviderStore(initialize_data_root(tmp_path / "cache-owned"))
        # Avoid touching a real OS credential store in this synthetic test.
        store._credentials.vault = None
        store.update({"search_api_key": "synthetic-a"})

        async def fetch(self, query):
            return result()

        monkeypatch.setattr(WebTools, "_search", fetch)
        before = store.web_tools(scope="scope-a")
        await before.execute("search_web", {"query": "guide"})
        store.update({"search_api_key": "synthetic-b"})
        assert not store.search_cache._entries
        assert (await before.execute("search_web", {"query": "guide"}))[
            "error"
        ] == "search_configuration_changed"
        current = store.web_tools(scope="scope-a")
        await current.execute("search_web", {"query": "guide"})

        def fail(config):
            raise ValueError("provider_config_write_failed")

        monkeypatch.setattr(store, "_save", fail)
        with pytest.raises(ValueError, match="write_failed"):
            store.update({"search_api_key": "synthetic-c"})
        assert store.web_tools(scope="scope-a")._key == "synthetic-b"
        assert (await current.execute("search_web", {"query": "guide"}))["cache"]["status"] == "hit"
        await store.close_search_cache()

    asyncio.run(scenario())


def test_real_search_http_is_coalesced_and_short_waiter_timeout_is_independent(web_http):
    async def scenario():
        loop = asyncio.get_running_loop()
        loop._clock_resolution = max(loop._clock_resolution, 0.015625)
        cache = SearchCache()
        entered, release = asyncio.Event(), asyncio.Event()
        requests = []

        async def handle(request):
            requests.append(request)
            entered.set()
            await release.wait()
            return reply(
                json.dumps(
                    {"results": [{"url": "https://example.com/guide", "content": "snippet"}]}
                ),
                headers={"content-type": "application/json"},
            )

        web_http(handle)
        first = WebTools({}, "synthetic-key", search_cache=cache, scope="scope-a")
        first.TIMEOUT = 0.01
        second = WebTools({}, "synthetic-key", search_cache=cache, scope="scope-a")
        short = asyncio.create_task(first.execute("search_web", {"query": "guide"}))
        long = asyncio.create_task(second.execute("search_web", {"query": "guide"}))
        # Both waiters must attach before a coarse Windows timer can expire.
        await asyncio.sleep(0)
        assert len(cache._flights) == 1
        assert next(iter(cache._flights.values())).waiters == 2
        await entered.wait()
        assert (await short)["error"] == "timeout"
        assert not long.done()
        release.set()
        outcome = await long
        assert outcome["cache"]["status"] == "coalesced" and len(requests) == 1
        assert outcome["sources"][0]["status"] == "snippet"
        third = WebTools({}, "synthetic-key", search_cache=cache, scope="scope-a")
        assert (await third.execute("search_web", {"query": "guide"}))["cache"]["status"] == "hit"
        assert len(requests) == 1
        await cache.close()

    asyncio.run(scenario())


@pytest.mark.parametrize("latest", [False, True])
def test_runtime_without_adopted_guide_honors_explicit_latest_bypass(
    tmp_path, monkeypatch, web_http, latest
):
    from test_chat import ScriptModel, call, text
    from test_guide_local_runtime import ask

    from ai_neko.runtime import SessionRuntime
    from ai_neko.runtime.guide_query import web_adapter

    async def scenario():
        requests = []
        web_http(
            lambda request: (
                requests.append(request)
                or reply('{"results": []}', headers={"content-type": "application/json"})
            )
        )
        monkeypatch.setenv("AI_NEKO_SEARCH_API_KEY", "synthetic-search")
        paths = initialize_data_root(tmp_path / "runtime-cache")
        providers = ProviderStore(paths)
        model = ScriptModel(
            [[call("search_web", query="synthetic version")], [], [text("没有依据。")]]
        )
        monkeypatch.setattr(providers, "model", lambda: model)
        runtime = SessionRuntime(paths, providers)
        try:
            await web_adapter(runtime).execute("search_web", {"query": "synthetic version"})
            await ask(runtime, "请查最新版攻略" if latest else "请搜索攻略", guide=True)
            assert len(requests) == (2 if latest else 1)
        finally:
            await runtime.close()
        assert providers.search_cache._closed and not providers.search_cache._tasks

    asyncio.run(scenario())


def test_runtime_factory_partitions_real_provider_cache_by_memory_character(
    tmp_path, monkeypatch, web_http
):
    from types import SimpleNamespace

    from ai_neko.memory import MemoryService
    from ai_neko.runtime.guide_query import web_adapter

    async def scenario():
        requests = []
        web_http(
            lambda request: (
                requests.append(request)
                or reply('{"results": []}', headers={"content-type": "application/json"})
            )
        )
        monkeypatch.setenv("AI_NEKO_SEARCH_API_KEY", "synthetic-search")
        paths = initialize_data_root(tmp_path / "scope-cache")
        providers = ProviderStore(paths)
        memories = [MemoryService(paths, character_id=name) for name in ("cat", "fox")]
        try:
            for memory in [*memories, *memories]:
                runtime = SimpleNamespace(providers=providers, memory=memory)
                adapter = web_adapter(runtime)
                assert adapter._scope == memory.scope
                await adapter.execute("search_web", {"query": "synthetic version"})
            assert len(requests) == 2
            assert {key[0] for key in providers.search_cache._entries} == {
                memory.scope for memory in memories
            }
        finally:
            await providers.close_search_cache()
            for memory in memories:
                memory.close()

    asyncio.run(scenario())


@pytest.mark.parametrize("action", ["selection", "delete", "restore"])
def test_runtime_guide_control_invalidates_real_provider_scope(
    tmp_path, monkeypatch, web_http, action
):
    from uuid import uuid4

    from test_guide_runtime import selection

    from ai_neko.runtime import SessionRuntime
    from ai_neko.runtime.guide_query import web_adapter

    async def scenario():
        requests = []
        web_http(
            lambda request: (
                requests.append(request)
                or reply('{"results": []}', headers={"content-type": "application/json"})
            )
        )
        monkeypatch.setenv("AI_NEKO_SEARCH_API_KEY", "synthetic-search")
        paths = initialize_data_root(tmp_path / "control-cache")
        providers = ProviderStore(paths)
        runtime = SessionRuntime(paths, providers)
        try:
            document = runtime.memory.guides.ingest(
                source("https://example.com/guide", status="read", text="攻略正文。" * 30)
            )
            if action == "restore":
                backup = await runtime.backup_guides({"request_id": uuid4().hex})
            await web_adapter(runtime).execute("search_web", {"query": "synthetic version"})
            assert len(providers.search_cache._entries) == 1
            control = {"request_id": uuid4().hex, "expected_revision": 0, "confirm": True}
            if action == "selection":
                await runtime.guide_selection(selection(runtime, document))
            elif action == "delete":
                await runtime.guide_delete(document["guide_id"], control)
            else:
                await runtime.restore_guides(backup["backup_id"], control)
            assert not providers.search_cache._entries
            await web_adapter(runtime).execute("search_web", {"query": "synthetic version"})
            assert len(requests) == 2
        finally:
            await runtime.close()
        assert providers.search_cache._closed and not providers.search_cache._tasks

    asyncio.run(scenario())


def test_runtime_same_process_intent_recovery_invalidates_cache_even_before_first_invalidation(
    tmp_path, monkeypatch, web_http
):
    from uuid import uuid4

    from ai_neko.runtime import SessionRuntime
    from ai_neko.runtime.guide_query import web_adapter

    async def scenario():
        web_http(
            lambda request: reply('{"results": []}', headers={"content-type": "application/json"})
        )
        monkeypatch.setenv("AI_NEKO_SEARCH_API_KEY", "synthetic-search")
        paths = initialize_data_root(tmp_path / "recovery-cache")
        providers = ProviderStore(paths)
        runtime = SessionRuntime(paths, providers)
        try:
            document = runtime.memory.guides.ingest(
                source("https://example.com/guide", status="read", text="攻略正文。" * 30)
            )
            await web_adapter(runtime).execute("search_web", {"query": "synthetic version"})
            assert providers.search_cache._entries
            save_intent = runtime._write_guide_intent

            def fail_after_persist(intent):
                save_intent(intent)
                raise OSError("synthetic failure after durable intent, before invalidation")

            monkeypatch.setattr(runtime, "_write_guide_intent", fail_after_persist)
            control = {"request_id": uuid4().hex, "expected_revision": 0, "confirm": True}
            with pytest.raises(OSError):
                await runtime.guide_delete(document["guide_id"], control)
            monkeypatch.setattr(runtime, "_write_guide_intent", save_intent)
            retry = await runtime.guide_delete(document["guide_id"], control)
            assert retry["replayed"] and not runtime.memory.guides.list_documents()
            assert not providers.search_cache._entries
        finally:
            await runtime.close()

    asyncio.run(scenario())


@pytest.mark.parametrize("action", ["delete", "close"])
def test_real_runtime_releases_in_flight_search_and_drops_late_deleted_snippet(
    tmp_path, monkeypatch, web_http, action
):
    from uuid import uuid4

    from test_chat import ScriptModel, call

    from ai_neko.runtime import SessionRuntime

    async def scenario():
        entered, released = asyncio.Event(), asyncio.Event()

        async def handle(request):
            entered.set()
            try:
                await asyncio.Event().wait()
            except asyncio.CancelledError:
                # A late transport result must not escape the revoked scope.
                return reply(
                    '{"results":[{"url":"https://example.com/guide","content":"DELETED_SNIPPET"}]}',
                    headers={"content-type": "application/json"},
                )
            finally:
                released.set()

        web_http(handle)
        monkeypatch.setenv("AI_NEKO_SEARCH_API_KEY", "synthetic-search")
        paths = initialize_data_root(tmp_path / "in-flight-cache")
        providers = ProviderStore(paths)
        model = ScriptModel([[call("search_web", query="synthetic version")]])
        monkeypatch.setattr(providers, "model", lambda: model)
        runtime = SessionRuntime(paths, providers)
        try:
            document = runtime.memory.guides.ingest(
                source("https://example.com/guide", status="read", text="攻略正文。" * 30)
            )
            sid = runtime.create_session()["id"]
            tid = (await runtime.start_turn(sid, "请搜索攻略", guide=True))["id"]
            await asyncio.wait_for(entered.wait(), 2)
            if action == "delete":
                await runtime.guide_delete(
                    document["guide_id"],
                    {"request_id": uuid4().hex, "expected_revision": 0, "confirm": True},
                )
                events = runtime.events(sid, tid)
                assert events["status"] == "cancelled"
                assert "DELETED_SNIPPET" not in json.dumps(events)
                assert not runtime.memory.guides.list_documents()
            else:
                await runtime.close()
            assert released.is_set()
            assert not providers.search_cache._entries
            assert not providers.search_cache._tasks
            assert not providers.search_cache._flights
        finally:
            await runtime.close()
        assert providers.search_cache._closed

    asyncio.run(scenario())
