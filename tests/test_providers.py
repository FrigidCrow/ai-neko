import asyncio
import json

import httpx
import pytest

from ai_neko.providers import ModelAdapter, ProviderError
from ai_neko.tools import network


class BytesStream(httpx.AsyncByteStream):
    def __init__(self, chunks):
        self.chunks = chunks

    async def __aiter__(self):
        for chunk in self.chunks:
            if isinstance(chunk, Exception):
                raise chunk
            yield chunk


def event(value):
    return b"data: " + (value if isinstance(value, str) else json.dumps(value)).encode() + b"\n\n"


def delta(value):
    return event({"choices": [{"delta": value}]})


@pytest.fixture
def provider_http(monkeypatch):
    clients = []

    def use(chunks=(), status=200, inspect=None):
        def handle(request):
            if inspect:
                inspect(request)
            return httpx.Response(status, stream=BytesStream(chunks))

        async def pin(url, **kwargs):
            return httpx.URL(url), {}, {}

        def client():
            result = httpx.AsyncClient(transport=httpx.MockTransport(handle), trust_env=False)
            clients.append(result)
            return result

        monkeypatch.setattr(network, "pin_url", pin)
        monkeypatch.setattr(network, "client", client)

    return use


def collect(adapter, tools=None):
    async def run():
        return [item async for item in adapter.stream([{"role": "user", "content": "test"}], tools)]

    return asyncio.run(run())


def adapter(key="synthetic-key", base="https://model.example/v1"):
    return ModelAdapter({"model": "synthetic-model", "model_base_url": base}, key)


def test_stream_text_and_fragmented_tool_arguments(provider_http):
    seen = []
    provider_http(
        [
            delta({"content": "你好"})[:8],
            delta({"content": "你好"})[8:],
            delta(
                {
                    "tool_calls": [
                        {
                            "index": 0,
                            "id": "call_1",
                            "function": {"name": "search_web", "arguments": '{"query":'},
                        }
                    ]
                }
            ),
            delta({"tool_calls": [{"index": 0, "function": {"arguments": '"攻略"}'}}]}),
            event("[DONE]"),
        ],
        inspect=lambda request: seen.append(request),
    )
    result = collect(adapter(), tools=[{"type": "function"}])
    assert result == [
        {"type": "text", "text": "你好"},
        {"type": "tool_call", "id": "call_1", "name": "search_web", "arguments": {"query": "攻略"}},
    ]
    payload = json.loads(seen[0].content)
    assert payload["stream"] and payload["parallel_tool_calls"] is False
    assert payload["max_completion_tokens"] == 4096
    assert seen[0].headers["Authorization"] == "Bearer synthetic-key"


@pytest.mark.parametrize(
    "status,code",
    [
        (401, "authentication_failed"),
        (403, "authentication_failed"),
        (429, "rate_limited"),
        (500, "provider_http_error"),
        (302, "provider_http_error"),
    ],
)
def test_status_errors_do_not_echo_provider_body(provider_http, status, code):
    provider_http([b"secret synthetic-key https://credential@example.com"], status=status)
    with pytest.raises(ProviderError) as captured:
        collect(adapter())
    assert captured.value.code == code
    assert "synthetic-key" not in str(captured.value)
    assert "example.com" not in str(captured.value)


@pytest.mark.parametrize(
    "chunks,code",
    [
        ([delta({"content": "partial"})], "stream_interrupted"),
        ([b'data: {"choices": []}\n'], "stream_interrupted"),
        ([event("garbage"), event("[DONE]")], "invalid_response"),
        ([event({"error": {"message": "secret"}})], "invalid_response"),
        (
            [
                delta(
                    {
                        "tool_calls": [
                            {
                                "index": 0,
                                "id": "x",
                                "function": {"name": "search_web", "arguments": "[]"},
                            }
                        ]
                    }
                ),
                event("[DONE]"),
            ],
            "invalid_response",
        ),
        ([httpx.ReadError("synthetic-key")], "network_error"),
        ([httpx.ReadTimeout("synthetic-key")], "timeout"),
        ([event({"choices": [{"delta": {}, "finish_reason": "length"}]})], "output_limit"),
    ],
)
def test_stream_failure_boundaries(provider_http, chunks, code):
    provider_http(chunks)
    with pytest.raises(ProviderError) as captured:
        collect(adapter())
    assert captured.value.code == code
    assert "synthetic-key" not in str(captured.value)


def test_cloud_key_required_and_local_model_without_key(provider_http):
    provider_http([event("[DONE]")])
    with pytest.raises(ProviderError, match="API Key"):
        collect(adapter(None))
    assert collect(adapter(None, "http://127.0.0.1:9000/v1")) == []


def test_configuration_and_output_limits(provider_http):
    provider_http([delta({"content": "12345"}), event("[DONE]")])
    model = adapter()
    model.MAX_TEXT = 3
    with pytest.raises(ProviderError) as captured:
        collect(model)
    assert captured.value.code == "output_limit"
    with pytest.raises(ProviderError) as captured:
        collect(ModelAdapter({}, None))
    assert captured.value.code == "model_not_configured"


def test_task_cancel_propagates(provider_http, monkeypatch):
    async def pin(*args, **kwargs):
        raise asyncio.CancelledError

    monkeypatch.setattr(network, "pin_url", pin)
    with pytest.raises(asyncio.CancelledError):
        collect(adapter())


def test_absolute_timeout_not_just_per_read_timeout(provider_http, monkeypatch):
    class SlowStream(httpx.AsyncByteStream):
        async def __aiter__(self):
            await asyncio.sleep(1)
            yield event("[DONE]")

    provider_http()
    monkeypatch.setattr(
        network,
        "client",
        lambda: httpx.AsyncClient(
            transport=httpx.MockTransport(lambda request: httpx.Response(200, stream=SlowStream()))
        ),
    )
    model = adapter()
    model.TIMEOUT = 0.01
    with pytest.raises(ProviderError) as captured:
        collect(model)
    assert captured.value.code == "timeout"


def test_stream_byte_and_line_limits(provider_http):
    provider_http([b"x" * 65_537])
    with pytest.raises(ProviderError) as captured:
        collect(adapter())
    assert captured.value.code == "invalid_response"
    provider_http([event("[DONE]")])
    model = adapter()
    model.MAX_BYTES = 3
    with pytest.raises(ProviderError) as captured:
        collect(model)
    assert captured.value.code == "output_limit"
