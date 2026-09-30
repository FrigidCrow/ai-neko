"""G1 ingestion evidence is richer than the bounded model-facing source card."""

import asyncio
import json
import socket

import httpx
import pytest
from test_providers import BytesStream

from ai_neko.tools import network
from ai_neko.tools.web import MAX_RETAINED_CHARACTERS, WebTools, source


@pytest.fixture
def transport(monkeypatch):
    async def resolve(self, host, port, **kwargs):
        ip = "127.0.0.1" if host == "127.0.0.1" else "93.184.216.34"
        return [(socket.AF_INET, socket.SOCK_STREAM, 6, "", (ip, port))]

    monkeypatch.setattr(asyncio.BaseEventLoop, "getaddrinfo", resolve)

    def install(handler):
        monkeypatch.setattr(
            network,
            "client",
            lambda: httpx.AsyncClient(transport=httpx.MockTransport(handler), trust_env=False),
        )

    return install


def reply(body, *, media="text/html; charset=utf-8", status=200, headers=None):
    data = body.encode() if isinstance(body, str) else body
    return httpx.Response(
        status,
        headers={"content-type": media, **(headers or {})},
        stream=BytesStream([data]),
    )


def read(url="https://example.com/guide"):
    return asyncio.run(WebTools({}, None).execute("read_web_page", {"url": url}))


@pytest.mark.parametrize("length", [6_101, 20_101, 50_000, 50_101])
def test_ingestion_retains_body_before_old_context_limits(transport, length):
    text = "A" * 6_001 + "MID_STEP" + "B" * (length - 6_001 - 8 - 8) + "END_STEP"
    transport(lambda request: reply(text, media="text/plain; charset=utf-8"))
    item = read()["sources"][0]
    assert item["text"] == text[:MAX_RETAINED_CHARACTERS]
    assert item["extracted_characters"] == length
    assert item["retained_characters"] == min(length, MAX_RETAINED_CHARACTERS)
    assert "MID_STEP" in item["text"]
    if length <= MAX_RETAINED_CHARACTERS:
        assert item["completeness"] == "full"
        assert item["completeness_reasons"] == []
        assert item["text"].endswith("END_STEP")
    else:
        assert item["completeness"] == "partial"
        assert item["completeness_reasons"] == ["body_limit"]
        assert "END_STEP" not in item["text"]


def test_html_long_late_section_and_exact_heading_ranges_survive(transport):
    transport(
        lambda request: reply(
            "<h1>公开攻略</h1><p>" + "早期阶段。" * 4_100 + "</p>"
            "<h4>后期关键步骤</h4><p>后期应保留一个空位，等待指定棋子升级。</p>"
        )
    )
    item = read()["sources"][0]
    later = item["headings"][1]
    assert later["start"] > 20_000
    assert item["text"][later["start"] : later["end"]] == later["title"] == "后期关键步骤"
    assert "后期应保留一个空位" in item["text"]
    assert [heading["level"] for heading in item["headings"]] == [1, 4]
    assert item["completeness"] == "full"


def test_redirect_preserves_queries_original_final_and_refresh_metadata(transport):
    seen = []

    def handle(request):
        seen.append(request)
        assert "cookie" not in request.headers and "authorization" not in request.headers
        if len(seen) == 1:
            return reply(
                "",
                status=302,
                headers={"location": "https://second.example/guide?version=2&mode=duo"},
            )
        return reply(
            '<title>双人攻略</title><meta property="article:published_time" '
            'content="2026-01-02"><p>' + "公开正文与明确战术。" * 10 + "</p>",
            headers={"etag": '"revision-2"', "last-modified": "Sat, 26 Sep 2026 09:00:00 GMT"},
        )

    transport(handle)
    original = "https://example.com/guide?version=1&mode=duo"
    item = read(original)["sources"][0]
    assert item["original_url"] == original
    assert item["final_url"] == item["url"] == "https://second.example/guide?version=2&mode=duo"
    assert str(seen[0].url).endswith("?version=1&mode=duo")
    assert str(seen[1].url).endswith("?version=2&mode=duo")
    assert item["etag"] == '"revision-2"'
    assert item["last_modified"] == "Sat, 26 Sep 2026 09:00:00 GMT"
    assert item["content_date"] == "2026-01-02"
    assert item["retrieved_at"] != item["content_date"]


def test_last_modified_is_not_a_content_date_or_game_version(transport):
    transport(
        lambda request: reply(
            "公开攻略正文与战术。" * 10,
            headers={"last-modified": "Sat, 26 Sep 2026 09:00:00 GMT"},
        )
    )
    item = read()["sources"][0]
    assert item["content_date"] is None
    assert "game_version" not in item
    assert item["last_modified"] and item["retrieved_at"]


def test_oversized_optional_http_validators_are_omitted_without_clipping(transport):
    transport(
        lambda request: reply(
            "公开攻略正文与战术。" * 10,
            headers={"etag": '"' + "e" * 1_001 + '"', "last-modified": "m" * 1_001},
        )
    )
    item = read()["sources"][0]
    assert item["status"] == "read" and item["completeness"] == "full"
    assert item["etag"] is None and item["last_modified"] is None


def test_lists_tables_images_and_heading_offsets_are_explicit(transport):
    html = (
        "<title>阵容指南</title><h1>基础 <em>策略</em></h1>"
        "<p>按照下面的购买和升级顺序操作，最后一列是每个棋子的费用。</p>"
        '<ol start="3"><li>保留金币<ul><li>先买前排</li><li>再买后排</li></ul></li>'
        '<li value="6">升到四级</li></ol>'
        "<h2>费用表</h2><table><tr><th>棋子</th><th>费用</th></tr>"
        "<tr><td>守卫</td><td>2 金币</td></tr></table>"
        '<img src="board.png" alt="这里展示站位，不等于已读取图片">'
        "<script>不要保存的脚本指令</script>"
        '<div hidden>隐藏段落</div><div style="display: none">隐藏内容</div>'
    )
    transport(lambda request: reply(html))
    item = read()["sources"][0]
    assert "3. 保留金币\n  - 先买前排\n  - 再买后排\n6. 升到四级" in item["text"]
    assert "| 棋子 | 费用 |\n| 守卫 | 2 金币 |" in item["text"]
    assert "隐藏" not in item["text"] and "脚本指令" not in item["text"]
    assert "站位" not in item["text"]
    assert item["completeness"] == "partial"
    assert item["completeness_reasons"] == ["images_unread"]
    for heading in item["headings"]:
        assert item["text"][heading["start"] : heading["end"]] == heading["title"]
    assert [heading["title"] for heading in item["headings"]] == ["基础 策略", "费用表"]
    assert read()["sources"][0]["headings"] == item["headings"]


def test_a_heading_crossing_retention_limit_cannot_claim_a_complete_span(transport):
    transport(lambda request: reply("<p>" + "x" * 49_990 + "</p><h2>" + "长标题" * 10 + "</h2>"))
    item = read()["sources"][0]
    assert len(item["text"]) == 50_000
    assert item["headings"] == []
    assert item["completeness_reasons"] == ["body_limit"]


def test_repeated_and_multiline_headings_have_distinct_exact_locations(transport):
    transport(
        lambda request: reply(
            "<h2>相同标题</h2><p>第一段正文给出了开局策略和金币规则。</p>"
            "<h2>相同标题</h2><p>第二段正文给出了收官规则和阵容变更。</p>"
            "<h6>多行<br>标题</h6><p>第三段对应附录。</p>"
        )
    )
    item = read()["sources"][0]
    first, second, third = item["headings"]
    assert first["title"] == second["title"]
    assert first["end"] < second["start"] < third["start"]
    assert third["title"] == "多行\n标题"
    for heading in item["headings"]:
        assert item["text"][heading["start"] : heading["end"]] == heading["title"]


def test_nested_lists_do_not_amplify_response_size_without_a_bound(transport):
    body = "<ul><li>" * 100 + "公开攻略的可读取步骤。" * 10 + "</li></ul>" * 100
    transport(lambda request: reply(body))
    item = read()["sources"][0]
    assert item["extracted_characters"] < 5_000
    assert item["completeness"] == "partial"
    assert item["completeness_reasons"] == ["structure_depth_limit"]


@pytest.mark.parametrize(
    "body,reason",
    [
        (b"public text " * 10 + b"\xff", "decode_replacement"),
        (
            "<p>攻略文字与策略。</p>" * 10 + '<iframe src="/diagram"></iframe>',
            "embedded_content_unread",
        ),
    ],
)
def test_known_missing_information_never_claims_full_body(transport, body, reason):
    transport(lambda request: reply(body))
    item = read()["sources"][0]
    assert item["status"] == "read"
    assert item["completeness"] == "partial"
    assert reason in item["completeness_reasons"]


@pytest.mark.parametrize(
    "body,status,error",
    [
        ("forbidden body " * 20, 403, "page_unavailable"),
        ('<form><input type="password"></form>' + "login prompt " * 20, 200, "login_required"),
        ("<p>" + "preview " * 1_000 + "</p><p>Subscribe to read</p>", 200, "content_unavailable"),
        ('<img alt="' + "unread image description " * 20 + '">', 200, "page_body_unavailable"),
        ("<script>drawFullGuide()</script><div id='app'></div>", 200, "page_body_unavailable"),
    ],
)
def test_unavailable_pages_have_no_ingestible_body(transport, body, status, error):
    transport(lambda request: reply(body, status=status))
    result = read()
    assert result["status"] == "error" and result["error"] == error
    item = result["sources"][0]
    assert item["status"] == item["completeness"] == "unreadable"
    assert item["text"] == "" and item["headings"] == []
    assert item["extracted_characters"] == item["retained_characters"] == 0


def test_redirect_private_address_still_blocked_before_second_request(transport):
    seen = []

    def handle(request):
        seen.append(request)
        return reply("", status=302, headers={"location": "http://127.0.0.1/private"})

    transport(handle)
    result = read()
    assert result["error"] == "blocked_address"
    assert len(seen) == 1
    assert result["sources"][0]["text"] == ""
    assert result["sources"][0]["original_url"] == "https://example.com/guide"


def test_search_stays_bounded_snippet_only(transport):
    payload = {
        "results": [
            {
                "url": "https://example.com/guide?v=2",
                "title": "Guide",
                "content": "s" * 3_000,
                "raw_content": "must never be read as body" * 1_000,
            }
        ],
    }
    transport(lambda request: reply(json.dumps(payload), media="application/json"))
    result = asyncio.run(WebTools({}, "test").execute("search_web", {"query": "guide"}))
    item = result["sources"][0]
    assert item["status"] == item["completeness"] == "snippet"
    assert item["text"] == "" and len(item["snippet"]) == 2_000
    assert item["extracted_characters"] == item["retained_characters"] == 0


def test_source_helper_preserves_old_call_shape_without_promoting_snippets():
    item = source(
        "https://example.com/", title="legacy", status="read", text="body", date="2026-01-02"
    )
    assert item["text"] == "body" and item["content_date"] == "2026-01-02"
    assert item["original_url"] == item["final_url"] == item["url"]
    assert source("https://example.com/", status="snippet", text="not a body")["text"] == ""
