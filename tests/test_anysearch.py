import asyncio
import json
import socket

import httpx
import pytest
from test_web_tools import dns as dns
from test_web_tools import reply
from test_web_tools import web_http as web_http

from ai_neko.config.paths import initialize_data_root
from ai_neko.config.providers import ProviderStore
from ai_neko.tools.web import WebTools


def result(*items):
    return {"code": 0, "data": {"results": list(items)}}


def test_anonymous_request_ignores_key_and_returns_bounded_snippets_only(web_http):
    seen = []

    def handle(request):
        seen.append(request)
        return reply(
            json.dumps(
                result(
                    {
                        "url": "https://example.com/guide",
                        "title": "Guide " * 100,
                        "snippet": "Search summary " * 300,
                        "content": "DO NOT REPLACE SNIPPET",
                        "raw_content": "DO NOT TREAT AS PAGE BODY",
                        "published_date": "2026-09-30",
                    },
                    {"url": "https://example.com/other", "content": "Fallback summary"},
                )
            )
        )

    web_http(handle)
    tool = WebTools({"search_provider": "anysearch"}, "synthetic-old-tavily-key", locale="en-US")
    outcome = asyncio.run(tool.execute("search_web", {"query": "  guide  "}))
    assert outcome["status"] == "ok" and tool._key is None
    assert len(seen) == 1
    request = seen[0]
    assert request.method == "POST" and request.url.path == "/v1/search"
    assert request.url.host == "93.184.216.34"
    assert request.headers["host"] == "api.anysearch.com"
    assert request.extensions["sni_hostname"] == "api.anysearch.com"
    assert "authorization" not in request.headers and "cookie" not in request.headers
    assert "synthetic-old-tavily-key" not in request.content.decode()
    assert json.loads(request.content) == {
        "query": "guide",
        "max_results": 5,
        "language": "en-US",
        "format": "json",
    }
    first, second = outcome["sources"]
    assert first["status"] == first["completeness"] == "snippet"
    assert first["text"] == "" and first["retained_characters"] == 0
    assert len(first["title"]) == 300 and len(first["snippet"]) == 2000
    assert first["content_date"] == "2026-09-30"
    assert second["snippet"] == "Fallback summary"


class UnreadErrorBody(httpx.AsyncByteStream):
    def __init__(self):
        self.reads = 0
        self.closed = False

    async def __aiter__(self):
        self.reads += 1
        yield b'{"message":"synthetic-created-password","api_key":"synthetic-created-key"}'

    async def aclose(self):
        self.closed = True


@pytest.mark.parametrize(
    "status,error",
    [
        (301, "provider_http_error"),
        (401, "authentication_failed"),
        (402, "search_quota_exhausted"),
        (403, "authentication_failed"),
        (429, "rate_limited"),
        (500, "provider_http_error"),
    ],
)
def test_error_bodies_never_read_or_returned_and_requests_never_retried(web_http, status, error):
    body = UnreadErrorBody()
    seen = []

    def handle(request):
        seen.append(request)
        return httpx.Response(
            status, stream=body, headers={"location": "https://elsewhere.example"}
        )

    web_http(handle)
    outcome = asyncio.run(
        WebTools({"search_provider": "anysearch"}, None).execute("search_web", {"query": "guide"})
    )
    assert outcome == {"status": "error", "sources": [], "error": error}
    assert len(seen) == 1 and body.reads == 0 and body.closed
    assert "synthetic-" not in json.dumps(outcome)


@pytest.mark.parametrize(
    "payload",
    [
        None,
        [],
        {},
        {"code": False, "data": {"results": []}},
        {"code": 0.0, "data": {"results": []}},
        {"code": "0", "data": {"results": []}},
        {"code": 1, "data": {"results": []}, "message": "synthetic-secret"},
        {"code": 200, "data": {"results": []}},
        {"code": 0, "data": None},
        {"code": 0, "data": {"results": {}}},
        {"results": []},
    ],
)
def test_only_integer_zero_and_nested_results_are_success(web_http, payload):
    web_http(lambda request: reply(json.dumps(payload)))
    outcome = asyncio.run(
        WebTools({"search_provider": "anysearch"}, None).execute("search_web", {"query": "guide"})
    )
    assert outcome == {"status": "error", "sources": [], "error": "invalid_response"}


def test_result_urls_are_revalidated_and_duplicates_filtered(web_http, monkeypatch):
    async def resolve(self, host, port, **kwargs):
        ip = "127.0.0.1" if host in {"127.0.0.1", "private.example"} else "93.184.216.34"
        return [(socket.AF_INET, socket.SOCK_STREAM, 6, "", (ip, port))]

    monkeypatch.setattr(asyncio.BaseEventLoop, "getaddrinfo", resolve)
    web_http(
        lambda request: reply(
            json.dumps(
                result(
                    {"url": "https://example.com/guide", "snippet": "Kept"},
                    {"url": "https://example.com/guide", "snippet": "Duplicate"},
                    {"url": "http://127.0.0.1/private", "snippet": "Private IP"},
                    {"url": "https://private.example/secret", "snippet": "Private DNS"},
                    {"url": "file:///etc/hosts", "snippet": "Local file"},
                )
            )
        )
    )
    outcome = asyncio.run(
        WebTools({"search_provider": "anysearch"}, None).execute("search_web", {"query": "guide"})
    )
    assert outcome["status"] == "ok"
    assert [item["url"] for item in outcome["sources"]] == ["https://example.com/guide"]


def test_result_limit_and_invalid_entries_remain_bounded(web_http):
    web_http(
        lambda request: reply(
            json.dumps(
                result(
                    None,
                    {"url": "https://example.com/invalid-title", "title": 123},
                    {"url": "https://example.com/invalid-snippet", "snippet": []},
                    {"url": "https://example.com/four", "snippet": "four"},
                    {"url": "https://example.com/five", "snippet": "five"},
                    {"url": "https://example.com/six", "snippet": "never included"},
                )
            )
        )
    )
    outcome = asyncio.run(
        WebTools({"search_provider": "anysearch"}, None).execute("search_web", {"query": "guide"})
    )
    assert [item["snippet"] for item in outcome["sources"]] == ["four", "five"]


def test_private_search_endpoint_never_receives_request(web_http, dns):
    seen = []
    web_http(lambda request: seen.append(request))
    dns(("127.0.0.1",))
    outcome = asyncio.run(
        WebTools({"search_provider": "anysearch"}, None).execute("search_web", {"query": "guide"})
    )
    assert outcome["error"] == "blocked_address" and not seen


def test_oversized_body_is_rejected_before_consumption(web_http):
    body = UnreadErrorBody()
    web_http(
        lambda request: httpx.Response(200, headers={"content-length": "2000001"}, stream=body)
    )
    outcome = asyncio.run(
        WebTools({"search_provider": "anysearch"}, None).execute("search_web", {"query": "guide"})
    )
    assert outcome["error"] == "response_too_large"
    assert body.reads == 0 and body.closed


def test_provider_switch_invalidates_old_cache_even_with_same_endpoint(
    tmp_path, monkeypatch, web_http
):
    async def scenario():
        monkeypatch.setenv("AI_NEKO_SEARCH_API_KEY", "synthetic-tavily-key")
        store = ProviderStore(initialize_data_root(tmp_path / "provider-switch"))
        store.update({"search_base_url": "https://search.example/v1"})
        seen = []

        def handle(request):
            seen.append(request)
            label = "tavily" if "authorization" in request.headers else "anysearch"
            entry = {"url": "https://example.com/guide", "content": label}
            return reply(json.dumps({"results": [entry]} if label == "tavily" else result(entry)))

        web_http(handle)
        old = store.web_tools()
        first = await old.execute("search_web", {"query": "guide"})
        assert first["sources"][0]["snippet"] == "tavily"
        assert (await store.web_tools().execute("search_web", {"query": "guide"}))["cache"][
            "status"
        ] == "hit"
        store.update(
            {"search_provider": "anysearch", "search_base_url": "https://search.example/v1"}
        )
        fresh = store.web_tools()
        answer = await fresh.execute("search_web", {"query": "guide"})
        assert answer["cache"]["status"] == "miss"
        assert answer["sources"][0]["snippet"] == "anysearch"
        assert (await old.execute("search_web", {"query": "guide"}))[
            "error"
        ] == "search_configuration_changed"
        monkeypatch.setenv("AI_NEKO_SEARCH_API_KEY", "synthetic-rotated-unused-key")
        assert (await store.web_tools().execute("search_web", {"query": "guide"}))["cache"][
            "status"
        ] == "hit"
        assert len(seen) == 2
        assert "authorization" not in seen[1].headers
        await store.close_search_cache()

    asyncio.run(scenario())


def test_cancellation_stops_anonymous_request_without_retry(web_http):
    async def scenario():
        started, released = asyncio.Event(), asyncio.Event()
        seen = []

        async def handle(request):
            seen.append(request)
            started.set()
            try:
                await asyncio.Event().wait()
            finally:
                released.set()

        web_http(handle)
        tool = WebTools({"search_provider": "anysearch"}, None)
        caller = asyncio.create_task(tool.execute("search_web", {"query": "guide"}))
        await started.wait()
        caller.cancel()
        with pytest.raises(asyncio.CancelledError):
            await caller
        assert released.is_set() and len(seen) == 1

    asyncio.run(scenario())
