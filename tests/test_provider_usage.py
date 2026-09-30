import json

import pytest
from test_providers import adapter, collect, delta, event
from test_providers import provider_http as provider_http

from ai_neko.providers import ModelAdapter, ProviderError


def test_usage_with_empty_choices_keeps_latest_total_without_summing(provider_http):
    seen = []
    provider_http(
        [
            delta({"content": "Answer"}),
            event(
                {
                    "choices": [],
                    "usage": {"prompt_tokens": 7, "completion_tokens": 1, "total_tokens": 8},
                }
            ),
            event(
                {
                    "choices": [],
                    "usage": {
                        "prompt_tokens": 7,
                        "completion_tokens": 2,
                        "total_tokens": 9,
                        "prompt_cache_hit_tokens": 3,
                        "prompt_tokens_details": {"cached_tokens": 3, "secret": "never retained"},
                        "credential": "never retained",
                    },
                }
            ),
            event("[DONE]"),
        ],
        inspect=lambda request: seen.append(json.loads(request.content)),
    )
    result = collect(
        ModelAdapter(
            {"model": "synthetic", "model_base_url": "https://model.example/v1"},
            "synthetic-key",
            request_usage=True,
        )
    )
    assert result == [
        {"type": "text", "text": "Answer"},
        {
            "type": "usage",
            "usage": {
                "prompt_tokens": 7,
                "completion_tokens": 2,
                "total_tokens": 9,
                "prompt_cache_hit_tokens": 3,
                "prompt_tokens_details": {"cached_tokens": 3},
            },
        },
    ]
    assert seen[0]["stream_options"] == {"include_usage": True}


@pytest.mark.parametrize(
    "usage",
    [
        None,
        {},
        {"prompt_tokens": True, "completion_tokens": 2, "total_tokens": 3},
        {"prompt_tokens": 1, "completion_tokens": 2, "total_tokens": 9},
    ],
)
def test_missing_or_malformed_usage_is_unknown_and_does_not_break_answer(provider_http, usage):
    provider_http(
        [delta({"content": "Answer"}), event({"choices": [], "usage": usage}), event("[DONE]")]
    )
    assert collect(adapter()) == [{"type": "text", "text": "Answer"}]


def test_interrupted_stream_does_not_claim_complete_token_usage(provider_http):
    provider_http(
        [
            event(
                {
                    "choices": [],
                    "usage": {"prompt_tokens": 1, "completion_tokens": 2, "total_tokens": 3},
                }
            )
        ]
    )
    with pytest.raises(ProviderError, match="模型连接中断"):
        collect(adapter())


def test_default_requests_do_not_require_optional_usage_feature(provider_http):
    seen = []
    provider_http(
        [delta({"content": "ok"}), event("[DONE]")],
        inspect=lambda request: seen.append(json.loads(request.content)),
    )
    collect(adapter())
    assert "stream_options" not in seen[0]
