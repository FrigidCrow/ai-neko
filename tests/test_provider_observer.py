import asyncio
import json
import re

import httpx
import pytest
from test_providers import adapter, collect, delta, event
from test_providers import provider_http as provider_http

from ai_neko.providers import ModelAdapter, ProviderError
from ai_neko.tools import network

USAGE = {"prompt_tokens": 7, "completion_tokens": 2, "total_tokens": 9}


def observed(model=None):
    model = model or adapter()
    records = []
    model.set_request_observer(records.append)
    return model, records


def assert_attempt(records, *, response=True, outcome="completed", code=None):
    assert [item["phase"] for item in records] == (
        ["started", "response", "finished"] if response else ["started", "finished"]
    )
    assert {item["type"] for item in records} == {"provider_request"}
    assert len({item["call_id"] for item in records}) == 1
    assert re.fullmatch("[a-f0-9]{32}", records[0]["call_id"])
    assert set(records[0]) == {"type", "phase", "call_id"}
    assert records[-1]["outcome"] == outcome
    assert records[-1]["error_code"] == code
    assert set(records[-1]) == {
        "type",
        "phase",
        "call_id",
        "outcome",
        "error_code",
        "usage",
        "usage_status",
    }


def test_observer_preserves_payload_and_stream_and_keeps_last_reported_usage(provider_http):
    requests = []
    provider_http(
        [
            delta({"content": "PRIVATE_REPLY"}),
            event({"choices": [], "usage": {**USAGE, "completion_tokens": 1, "total_tokens": 8}}),
            event({"choices": [], "usage": {**USAGE, "secret": "PRIVATE_METADATA"}}),
            event("[DONE]"),
        ],
        inspect=lambda request: requests.append(json.loads(request.content)),
    )
    model, records = observed(adapter(key="PRIVATE_API_KEY"))
    assert collect(model) == [
        {"type": "text", "text": "PRIVATE_REPLY"},
        {"type": "usage", "usage": USAGE},
    ]
    assert_attempt(records)
    assert records[1]["http_status"] == 200
    assert set(records[1]) == {"type", "phase", "call_id", "http_status"}
    assert records[-1]["usage_status"] == "reported"
    assert records[-1]["usage"] == USAGE
    assert len(requests) == 1
    assert "stream_options" not in requests[0]
    serialized = json.dumps(records)
    for forbidden in ("PRIVATE_", "model.example", "Authorization", "messages", "synthetic-model"):
        assert forbidden not in serialized


@pytest.mark.parametrize("last", [None, {}, {**USAGE, "total_tokens": 99}])
def test_missing_or_invalid_final_usage_is_unknown(provider_http, last):
    chunks = [delta({"content": "ok"})]
    if last is not None:
        chunks += [event({"usage": USAGE}), event({"usage": last})]
    provider_http([*chunks, event("[DONE]")])
    model, records = observed()
    assert collect(model) == [{"type": "text", "text": "ok"}]
    assert_attempt(records)
    assert records[-1]["usage_status"] == "unknown"
    assert records[-1]["usage"] is None


@pytest.mark.parametrize(
    "status,code",
    [(401, "authentication_failed"), (429, "rate_limited"), (500, "provider_http_error")],
)
def test_http_error_is_one_attempt_without_retry(provider_http, status, code):
    requests = []
    provider_http(status=status, inspect=lambda request: requests.append(request))
    model, records = observed()
    with pytest.raises(ProviderError) as failure:
        collect(model)
    assert failure.value.code == code
    assert_attempt(records, outcome="failed", code=code)
    assert records[1]["http_status"] == status
    assert records[-1]["usage_status"] == "unknown"
    assert len(requests) == 1


@pytest.mark.parametrize(
    "tail,code",
    [
        ([], "stream_interrupted"),
        ([b"data: {BROKEN_PRIVATE_RESPONSE}\n\n"], "invalid_response"),
        ([httpx.ReadError("PRIVATE_ERROR")], "network_error"),
        ([httpx.ReadTimeout("PRIVATE_ERROR")], "timeout"),
        ([event({"choices": [{"finish_reason": "length"}]})], "output_limit"),
    ],
)
def test_incomplete_stream_keeps_partial_reported_numbers(provider_http, tail, code):
    provider_http([event({"usage": USAGE}), *tail])
    model, records = observed()
    with pytest.raises(ProviderError) as failure:
        collect(model)
    assert failure.value.code == code
    assert_attempt(records, outcome="failed", code=code)
    assert records[-1]["usage_status"] == "partial"
    assert records[-1]["usage"] == USAGE
    assert "PRIVATE_" not in json.dumps(records)


def test_aclose_records_partial_attempt_and_captures_original_observer(provider_http):
    provider_http([event({"usage": USAGE}), delta({"content": "first"}), event("[DONE]")])

    async def run():
        model, records = observed()
        other = []
        stream = model.stream([{"role": "user", "content": "PRIVATE_PROMPT"}])
        assert await anext(stream) == {"type": "text", "text": "first"}
        model.set_request_observer(other.append)
        await stream.aclose()
        assert_attempt(records, outcome="interrupted", code="stream_closed")
        assert records[-1]["usage_status"] == "partial"
        assert records[-1]["usage"] == USAGE
        assert other == []

    asyncio.run(run())


def test_cancellation_after_dispatch_records_partial_and_closes_response(monkeypatch):
    async def run():
        reached = asyncio.Event()
        closed = []

        class BlockingStream(httpx.AsyncByteStream):
            async def __aiter__(self):
                yield event({"usage": USAGE})
                reached.set()
                await asyncio.Event().wait()

            async def aclose(self):
                closed.append(True)

        async def pin(url, **kwargs):
            return httpx.URL(url), {}, {}

        monkeypatch.setattr(network, "pin_url", pin)
        monkeypatch.setattr(
            network,
            "client",
            lambda: httpx.AsyncClient(
                transport=httpx.MockTransport(
                    lambda request: httpx.Response(200, stream=BlockingStream())
                ),
                trust_env=False,
            ),
        )
        model, records = observed()

        async def consume():
            return [item async for item in model.stream([])]

        task = asyncio.create_task(consume())
        await asyncio.wait_for(reached.wait(), 2)
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task
        assert_attempt(records, outcome="cancelled", code="cancelled")
        assert records[-1]["usage_status"] == "partial"
        assert records[-1]["usage"] == USAGE
        assert closed == [True]

    asyncio.run(run())


@pytest.mark.parametrize("phase", ["started", "response", "finished"])
def test_observer_write_failure_is_safe_and_never_retries(provider_http, phase):
    requests, records = [], []
    provider_http([delta({"content": "ok"}), event("[DONE]")], inspect=requests.append)
    model = adapter()

    def observe(record):
        records.append(record)
        if record["phase"] == phase:
            raise OSError("PRIVATE_STORAGE_PATH_AND_SECRET")

    model.set_request_observer(observe)
    with pytest.raises(ProviderError) as failure:
        collect(model)
    assert failure.value.code == "provider_tracking_failed"
    assert "PRIVATE_" not in str(failure.value)
    assert len(requests) == (0 if phase == "started" else 1)
    if phase == "started":
        assert [item["phase"] for item in records] == ["started"]
    elif phase == "response":
        assert_attempt(records, outcome="failed", code="provider_tracking_failed")
    else:
        assert_attempt(records)
    assert "PRIVATE_" not in json.dumps(records)


@pytest.mark.parametrize(
    "kind", ["missing_model", "missing_key", "blocked_endpoint", "client_failure"]
)
def test_preflight_failures_have_no_http_attempt(provider_http, monkeypatch, kind):
    requests = []
    provider_http(inspect=requests.append)
    model = (
        ModelAdapter({}, None)
        if kind == "missing_model"
        else adapter(key=None if kind == "missing_key" else "synthetic")
    )
    model, records = observed(model)
    if kind == "blocked_endpoint":

        async def blocked(*args, **kwargs):
            raise network.NetworkPolicyError("PRIVATE_ENDPOINT")

        monkeypatch.setattr(network, "pin_url", blocked)
    if kind == "client_failure":

        def unavailable():
            raise RuntimeError("PRIVATE_CLIENT_ERROR")

        monkeypatch.setattr(network, "client", unavailable)
    with pytest.raises((ProviderError, RuntimeError)):
        collect(model)
    assert requests == []
    assert records == []


def test_two_streams_have_separate_ids_and_disabling_observer_is_compatible(provider_http):
    requests = []
    provider_http([delta({"content": "ok"}), event("[DONE]")], inspect=requests.append)
    model, records = observed()
    for _ in range(2):
        assert collect(model) == [{"type": "text", "text": "ok"}]
    assert len({item["call_id"] for item in records}) == 2
    assert len(records) == 6
    model.set_request_observer(None)
    assert collect(model) == [{"type": "text", "text": "ok"}]
    assert len(records) == 6
    assert len(requests) == 3
