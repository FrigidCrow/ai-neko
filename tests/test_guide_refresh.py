import asyncio

import pytest
from test_web_tools import dns as dns
from test_web_tools import reply
from test_web_tools import web_http as web_http

from ai_neko.tools.web import WebTools

URL = "https://example.com/guide"
DATE = "Sat, 26 Sep 2026 09:00:00 GMT"


def test_conditional_304_has_check_evidence_but_no_body_or_content_date(web_http):
    requests = []

    def handle(request):
        requests.append(request)
        assert request.headers["If-None-Match"] == 'W/"known-body"'
        assert request.headers["If-Modified-Since"] == DATE
        return reply("", 304, {"etag": '"new-validator"'})

    web_http(handle)
    outcome = asyncio.run(
        WebTools({}, None).read(
            URL, validators={"url": URL, "etag": 'W/"known-body"', "last_modified": DATE}
        )
    )
    assert outcome["status"] == "not_modified" and outcome["sources"] == []
    assert outcome["final_url"] == URL and outcome["original_url"] == URL
    assert outcome["checked_at"] and outcome["etag"] == '"new-validator"'
    assert outcome["last_modified"] == DATE
    assert "content_date" not in outcome and "text" not in outcome and "game_version" not in outcome
    assert len(requests) == 1


@pytest.mark.parametrize(
    "validators", [None, {}, {"url": URL}, {"url": URL + "/other", "etag": '"v1"'}]
)
def test_unsolicited_304_is_not_accepted_as_read_or_verified(web_http, validators):
    web_http(lambda request: reply("", 304))
    outcome = asyncio.run(WebTools({}, None).read(URL, validators=validators))
    assert outcome["status"] == "error" and outcome["error"] == "unexpected_not_modified"
    assert outcome["sources"][0]["text"] == ""


@pytest.mark.parametrize(
    "validators",
    [
        {"url": URL, "etag": '"tag"\r\nAuthorization: hidden'},
        {"url": URL, "etag": '"tag"\n'},
        {"url": URL, "etag": "*"},
        {"url": URL, "etag": '"a", "b"'},
        {"url": URL, "etag": "no-quotes"},
        {"url": URL, "etag": '"' + "a" * 1000 + '"'},
        {"url": URL, "etag": '"中文"'},
        {"url": URL, "last_modified": "yesterday"},
        {"url": URL, "last_modified": DATE + "\r\nCookie: secret"},
        {"url": URL, "last_modified": "Sat, 26 Sep 2026 09:00:00"},
        {"url": URL, "Authorization": "secret"},
        {"etag": '"v1"'},
        {"url": "file:///private", "etag": '"v1"'},
        {"url": URL, "etag": 1},
        ["etag", '"v1"'],
    ],
)
def test_invalid_conditional_input_never_reaches_network(web_http, validators):
    def forbidden(request):
        pytest.fail("invalid headers must be rejected before network")

    web_http(forbidden)
    outcome = asyncio.run(WebTools({}, None).read(URL, validators=validators))
    assert outcome["status"] == "error"
    assert outcome["error"] in {"invalid_validators", "invalid_url"}


@pytest.mark.parametrize(
    "target", ["https://other.example/guide", URL + "?changed=1", URL + "/new"]
)
def test_redirect_never_forwards_validator_to_different_resource(web_http, target):
    requests = []

    def handle(request):
        requests.append(request)
        if len(requests) == 1:
            assert request.headers["If-None-Match"] == '"v1"'
            return reply("", 302, {"location": target, "set-cookie": "private=1"})
        assert "If-None-Match" not in request.headers
        assert "If-Modified-Since" not in request.headers
        assert "authorization" not in request.headers and "cookie" not in request.headers
        return reply("changed public source body " * 10, headers={"content-type": "text/plain"})

    web_http(handle)
    outcome = asyncio.run(
        WebTools({}, "must-not-be-forwarded").read(URL, validators={"url": URL, "etag": '"v1"'})
    )
    assert outcome["status"] == "ok" and outcome["sources"][0]["final_url"] == target
    assert len(requests) == 2


def test_saved_final_url_validator_is_sent_only_after_original_redirect(web_http):
    final_url = "https://cdn.example/guide"
    requests = []

    def handle(request):
        requests.append(request)
        if len(requests) == 1:
            assert "If-None-Match" not in request.headers
            return reply("", 302, {"location": final_url})
        assert request.headers["If-None-Match"] == '"v1"'
        return reply("", 304)

    web_http(handle)
    outcome = asyncio.run(
        WebTools({}, None).read(URL, validators={"url": final_url, "etag": '"v1"'})
    )
    assert outcome["status"] == "not_modified"
    assert outcome["original_url"] == URL and outcome["final_url"] == final_url
    assert len(requests) == 2


def test_conditional_redirect_rechecks_private_target_before_headers_are_sent(
    web_http, monkeypatch
):
    from ai_neko.tools import network

    actual_pin = network.pin_url
    requests = []

    async def pin(value, **kwargs):
        if "127.0.0.1" in value:
            raise network.NetworkPolicyError("blocked_address")
        return await actual_pin(value, **kwargs)

    monkeypatch.setattr(network, "pin_url", pin)
    web_http(
        lambda request: (
            requests.append(request) or reply("", 302, {"location": "http://127.0.0.1/private"})
        )
    )
    outcome = asyncio.run(WebTools({}, None).read(URL, validators={"url": URL, "etag": '"v1"'}))
    assert outcome["error"] == "blocked_address" and len(requests) == 1


def test_conditional_200_returns_changed_content_separately_from_last_modified(web_http):
    web_http(
        lambda request: reply(
            "new public body " * 10,
            headers={"content-type": "text/plain", "etag": '"v2"', "last-modified": DATE},
        )
    )
    outcome = asyncio.run(WebTools({}, None).read(URL, validators={"url": URL, "etag": '"v1"'}))
    item = outcome["sources"][0]
    assert outcome["status"] == "ok" and item["status"] == "read"
    assert item["text"].startswith("new public body") and item["etag"] == '"v2"'
    assert item["content_date"] is None and item["last_modified"] == DATE


@pytest.mark.parametrize("status", [403, 404, 429, 500])
def test_failed_refresh_does_not_return_old_body_as_new_read(web_http, status):
    web_http(lambda request: reply("failure response", status))
    outcome = asyncio.run(WebTools({}, None).read(URL, validators={"url": URL, "etag": '"v1"'}))
    assert outcome["status"] == "error"
    assert outcome["sources"][0]["status"] == "unreadable"
    assert outcome["sources"][0]["text"] == ""


def test_model_arguments_cannot_supply_conditional_headers(web_http):
    def forbidden(request):
        pytest.fail("model validator fields are not tools")

    web_http(forbidden)
    outcome = asyncio.run(
        WebTools({}, None).execute(
            "read_web_page", {"url": URL, "validators": {"url": URL, "etag": '"v1"'}}
        )
    )
    assert outcome["error"] == "unsupported_tool"


def test_direct_conditional_read_has_a_total_deadline_and_releases_http_request(web_http):
    async def scenario():
        released = asyncio.Event()

        async def handle(request):
            try:
                await asyncio.Event().wait()
            finally:
                released.set()

        web_http(handle)
        adapter = WebTools({}, None)
        adapter.TIMEOUT = 0.01
        outcome = await adapter.read(URL, validators={"url": URL, "etag": '"v1"'})
        assert outcome["error"] == "timeout" and released.is_set()
        assert outcome["sources"][0]["status"] == "unreadable"

    asyncio.run(scenario())


def test_completed_dns_does_not_swallow_deadline_with_windows_clock_resolution(web_http):
    async def scenario():
        loop = asyncio.get_running_loop()
        previous = loop._clock_resolution
        # Windows 3.11 may run the 10ms deadline in the same loop iteration as
        # an already-completed DNS task. A finite body keeps a regression bounded.
        loop._clock_resolution = 0.015625

        async def handle(request):
            await asyncio.sleep(0.1)
            return reply("late public guide body " * 10)

        web_http(handle)
        adapter = WebTools({}, None)
        adapter.TIMEOUT = 0.01
        try:
            outcome = await adapter.read(URL, validators={"url": URL, "etag": '"v1"'})
            assert outcome["status"] == "error" and outcome["error"] == "timeout"
            assert outcome["sources"][0]["text"] == ""
        finally:
            loop._clock_resolution = previous

    asyncio.run(scenario())
