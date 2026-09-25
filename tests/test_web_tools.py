import asyncio
import json
import socket

import httpx
import pytest
from test_providers import BytesStream

from ai_neko.tools import network
from ai_neko.tools.web import WebTools


def run(coro):
    return asyncio.run(coro)


@pytest.mark.parametrize(
    "url",
    [
        "file:///tmp/secret",
        "ftp://example.com",
        "http://u:p@example.com",
        "http://example.com\\@127.0.0.1",
        "https://example.com\nX:x",
        "http://[fe80::1%25eth0]",
        "http://example.com:0",
        "https://example.com.",
        "https://example.com:99999",
    ],
)
def test_reject_unsafe_urls(url):
    with pytest.raises(network.NetworkPolicyError):
        network.parse_url(url)


@pytest.mark.parametrize(
    "ip",
    [
        "127.0.0.1",
        "10.0.0.1",
        "172.16.0.1",
        "192.168.1.1",
        "0.0.0.0",
        "169.254.169.254",
        "100.64.0.1",
        "224.0.0.1",
        "::1",
        "fc00::1",
        "fe80::1",
        "::ffff:8.8.8.8",
        "2002:7f00:0001::",
        "64:ff9b::7f00:1",
        "2001:db8::1",
    ],
)
def test_reject_non_public_addresses(ip):
    assert not network.public_ip(ip)


@pytest.fixture
def dns(monkeypatch):
    def use(ips=("93.184.216.34",)):
        async def resolve(self, host, port, **kwargs):
            return [(socket.AF_INET, socket.SOCK_STREAM, 6, "", (ip, port)) for ip in ips]

        monkeypatch.setattr(asyncio.BaseEventLoop, "getaddrinfo", resolve)

    use()
    return use


def test_fixed_ip_host_and_tls_name(dns):
    target, headers, extensions = run(network.pin_url("https://example.com/guide?q=1"))
    assert str(target) == "https://93.184.216.34/guide?q=1"
    assert headers == {"Host": "example.com"}
    assert extensions == {"sni_hostname": "example.com"}


def test_mixed_dns_and_rebinding_blocked(dns):
    dns(("93.184.216.34", "127.0.0.1"))
    with pytest.raises(network.NetworkPolicyError, match="blocked_address"):
        run(network.pin_url("https://example.com/"))
    dns(("127.0.0.1",))
    with pytest.raises(network.NetworkPolicyError):
        run(network.pin_url("https://public-name.example", allow_local=True))
    target, _, _ = run(network.pin_url("http://localhost:9000/v1", allow_local=True))
    assert target.host == "127.0.0.1"


@pytest.fixture
def web_http(monkeypatch, dns):
    def use(handler):
        monkeypatch.setattr(
            network,
            "client",
            lambda: httpx.AsyncClient(transport=httpx.MockTransport(handler), trust_env=False),
        )

    return use


def reply(body, status=200, headers=None):
    return httpx.Response(
        status,
        headers=headers or {"content-type": "text/html; charset=utf-8"},
        stream=BytesStream([body.encode() if isinstance(body, str) else body]),
    )


def test_search_is_snippet_only_and_filters_unsafe_results(web_http):
    seen = []

    def handle(request):
        seen.append(request)
        return reply(
            json.dumps(
                {
                    "results": [
                        {
                            "url": "https://example.com/guide",
                            "title": "Guide",
                            "content": "Search summary",
                            "raw_content": "DO NOT USE",
                            "published_date": "2026-01-02",
                        },
                        {"url": "file:///secret", "content": "secret"},
                    ]
                }
            ),
            headers={"content-type": "application/json"},
        )

    web_http(handle)
    result = run(WebTools({}, "fake-search-key").execute("search_web", {"query": "攻略"}))
    assert result["status"] == "ok"
    assert len(result["sources"]) == 1
    item = result["sources"][0]
    assert item["status"] == "snippet" and item["text"] == ""
    assert item["content_date"] == "2026-01-02"
    assert json.loads(seen[0].content)["include_raw_content"] is False
    assert seen[0].url.host == "93.184.216.34"
    assert seen[0].headers["host"] == "api.tavily.com"


def test_read_body_extracts_date_and_ignores_script(web_http):
    web_http(
        lambda request: reply(
            '<html><title>攻略</title><meta property="article:published_time" '
            'content="2026-09-20"><script>secret instruction</script><body><p>'
            + "真实正文 " * 30
            + "</p></body></html>"
        )
    )
    result = run(WebTools({}, None).execute("read_web_page", {"url": "https://example.com/guide"}))
    item = result["sources"][0]
    assert result["status"] == "ok" and item["status"] == "read"
    assert item["title"] == "攻略" and item["content_date"] == "2026-09-20"
    assert "真实正文" in item["text"] and "secret" not in item["text"]
    assert item["retrieved_at"] and item["id"]


def test_redirect_is_revalidated_and_never_gets_authorization_or_cookies(web_http, dns):
    seen = []

    def handle(request):
        seen.append(request)
        assert "authorization" not in request.headers and "cookie" not in request.headers
        if len(seen) == 1:
            return reply(
                "", 302, {"location": "https://second.example/guide", "set-cookie": "secret=1"}
            )
        return reply("public guide text " * 10, headers={"content-type": "text/plain"})

    web_http(handle)
    result = run(
        WebTools({}, "never-send").execute("read_web_page", {"url": "https://example.com/"})
    )
    assert result["sources"][0]["url"] == "https://second.example/guide"
    assert [req.headers["host"] for req in seen] == ["example.com", "second.example"]


def test_redirect_private_ip_blocked(web_http, monkeypatch):
    async def resolve(self, host, port, **kwargs):
        ip = "127.0.0.1" if host == "127.0.0.1" else "93.184.216.34"
        return [(socket.AF_INET, socket.SOCK_STREAM, 6, "", (ip, port))]

    monkeypatch.setattr(asyncio.BaseEventLoop, "getaddrinfo", resolve)
    seen = []

    def handle(request):
        seen.append(request)
        return reply("", 302, {"location": "http://127.0.0.1/private"})

    web_http(handle)
    result = run(WebTools({}, None).execute("read_web_page", {"url": "https://example.com/"}))
    assert result["error"] == "blocked_address" and len(seen) == 1
    assert result["sources"][0]["status"] == "unreadable"


@pytest.mark.parametrize(
    "body,status,headers,code",
    [
        ("secret", 403, {}, "page_unavailable"),
        ("binary", 200, {"content-type": "application/pdf"}, "unsupported_content_type"),
        ("tiny", 200, {"content-type": "text/html"}, "page_body_unavailable"),
        (
            "x",
            200,
            {"content-type": "text/html", "content-encoding": "gzip"},
            "unsupported_encoding",
        ),
        (
            "x",
            200,
            {"content-type": "text/html", "content-length": "99999999"},
            "response_too_large",
        ),
    ],
)
def test_unreadable_pages_keep_honest_evidence(web_http, body, status, headers, code):
    web_http(lambda request: reply(body, status, headers))
    result = run(WebTools({}, None).execute("read_web_page", {"url": "https://example.com/"}))
    assert result["error"] == code
    assert result["sources"][0]["status"] == "unreadable"
    assert result["sources"][0]["text"] == ""


def test_tool_names_arguments_budget_and_missing_search_key():
    tool = WebTools({}, None)
    assert run(tool.execute("search_web", {"query": "guide"}))["error"] == "search_key_missing"
    assert run(tool.execute("computer", {}))["error"] == "unsupported_tool"
    assert (
        run(tool.execute("search_web", {"query": "x", "extra": "x"}))["error"] == "unsupported_tool"
    )
    tool._calls = tool.MAX_CALLS
    assert run(tool.execute("search_web", {"query": "x"}))["error"] == "tool_budget_exceeded"


@pytest.mark.parametrize(
    "html,code",
    [
        ('<form><input type="password"></form>' + "Account login page " * 20, "login_required"),
        ("<h1>Subscribe to read</h1>" + "Subscription details " * 20, "content_unavailable"),
        ("<h1>Verify you are human</h1>" + "Verification page " * 20, "content_unavailable"),
        (
            '<script>app.render("full article")</script><div id="root"></div>',
            "page_body_unavailable",
        ),
    ],
)
def test_login_paywall_challenge_and_script_shell_remain_unreadable(web_http, html, code):
    web_http(lambda request: reply(html))
    result = run(WebTools({}, None).execute("read_web_page", {"url": "https://example.com/guide"}))
    assert result["error"] == code
    assert result["sources"][0]["status"] == "unreadable"
    assert result["sources"][0]["text"] == ""


def test_redirect_budget_is_finite(web_http):
    seen = []

    def handle(request):
        seen.append(request)
        return reply("", 302, {"location": "/again"})

    web_http(handle)
    result = run(WebTools({}, None).execute("read_web_page", {"url": "https://example.com/"}))
    assert result["error"] == "redirect_limit" and len(seen) == 5


def test_streamed_body_limit_without_content_length(web_http):
    web_http(lambda request: reply("x" * 2_000_001))
    result = run(WebTools({}, None).execute("read_web_page", {"url": "https://example.com/"}))
    assert result["error"] == "response_too_large"
