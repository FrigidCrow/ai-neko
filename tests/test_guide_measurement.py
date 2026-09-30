"""Honesty and failure-retention checks for the opt-in G6 backend probe.

No real provider is called. This verifies record semantics, not actual advice
quality, audible latency, cost, or Windows performance.
"""

import asyncio
import importlib.util
import json
from pathlib import Path

import pytest
from test_providers import delta, event
from test_providers import provider_http as provider_http

from ai_neko.config.paths import initialize_data_root
from ai_neko.providers import ProviderError
from ai_neko.runtime import SessionRuntime
from ai_neko.tools import network


@pytest.fixture
def probe():
    path = Path(__file__).resolve().parents[1] / "scripts" / "measure_guides.py"
    spec = importlib.util.spec_from_file_location("g6_measurement_under_test", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def config(probe):
    return probe.validate(
        {
            "model": "synthetic-probe-model",
            "model_base_url": "https://model.example/v1",
            "search_base_url": "https://search.example",
        }
    )


def evaluate(probe, tmp_path):
    return asyncio.run(
        probe.evaluate(
            probe.validate_plan(probe.synthetic_plan()),
            config(probe),
            tmp_path / "measurement.json",
            synthetic=True,
        )
    )


def unknown_measurements(turn):
    assert turn["first_useful_text_ms"] is None
    assert turn["first_useful_voice_ms"] is None
    assert turn["audio_playback"] == "NOT_RUN"
    assert turn["advice_quality"] == "manual_review_required"
    assert turn["cost"]["amount"] is None and turn["cost"]["currency"] is None


def test_synthetic_twenty_pairs_never_construct_network_adapters_or_invent_useful_timings(
    probe, tmp_path, monkeypatch
):
    def forbidden(*_args, **_kwargs):
        raise AssertionError("non-live probe attempted a network/provider operation")

    monkeypatch.setattr(network, "client", forbidden)
    monkeypatch.setattr(probe, "ModelAdapter", forbidden)
    monkeypatch.setattr(probe, "WebTools", forbidden)
    runtime_class = probe.SessionRuntime

    def runtime(*args, **kwargs):
        instance = runtime_class(*args, **kwargs)
        instance.metrics.record("guide_retrieve_ms", 99999, scope="before_phase")
        return instance

    monkeypatch.setattr(probe, "SessionRuntime", runtime)
    for name in ("AI_NEKO_MODEL_API_KEY", "AI_NEKO_SEARCH_API_KEY", "OPENAI_API_KEY"):
        monkeypatch.setenv(name, "SYNTHETIC_ENV_SECRET_MUST_NOT_APPEAR")
    result = evaluate(probe, tmp_path)
    assert len(result["pairs"]) == 20
    assert result["status"] == "SYNTHETIC_PIPELINE_PASS"
    assert result["real_model_calls"] == result["real_web_calls"] == 0
    assert result["real_model_adapter_attempts"] == result["real_web_tool_attempts"] == 0
    assert result["counter_aliases"]["real_model_calls"] == "real_model_adapter_attempts"
    assert "not HTTP" in result["counter_units"]["web_tool_attempts"]
    assert result["asr"].startswith("NOT_RUN") and result["tts"].startswith("NOT_RUN")
    for pair in result["pairs"]:
        assert pair["eligible_route"] and pair["status"] == "CAPTURED_REVIEW_PENDING"
        assert {item["name"] for item in pair["cold"]["web_calls"]} == {
            "search_web",
            "read_web_page",
        }
        assert pair["warm"]["web_calls"] == []
        assert pair["cold"]["web_tool_attempts"] == 2
        assert pair["warm"]["web_tool_attempts"] == 0
        assert pair["cold"]["turn_id"] != pair["warm"]["turn_id"]
        for phase in ("cold", "warm"):
            unknown_measurements(pair[phase])
            assert pair[phase]["first_text_ms"] is not None
            assert pair[phase]["model_adapter_attempts"] == len(pair[phase]["model_calls"])
            assert pair[phase]["retrieval_metrics_status"] == "recorded"
            metrics = pair[phase]["retrieval_metrics"]
            assert len(metrics) == 1, "cold and warm must not include one another's retrieval"
            assert metrics[0]["metric"] == "guide_retrieve_ms"
            assert metrics[0]["turn_id"] == pair[phase]["turn_id"]
            assert metrics[0]["tags"]["scope"] == "guides"
            assert metrics[0]["value"] != 99999
        assert (
            pair["retrieval_metrics"]
            == pair["cold"]["retrieval_metrics"] + pair["warm"]["retrieval_metrics"]
        )
    assert "SYNTHETIC_ENV_SECRET" not in json.dumps(result)
    assert json.loads((tmp_path / "measurement.json").read_text()) == result


def test_failed_model_pair_stays_in_the_twenty_without_exception_text_or_fake_usage(
    probe, tmp_path, monkeypatch
):
    original = probe.ProbeProviders

    class FailingProviders(original):
        async def synthetic_model(self):
            if self.item["question"] == "灯芯3是什么意思？":
                yield {"type": "text", "text": "partial synthetic text"}
                raise ProviderError("timeout", "SYNTHETIC_ERROR_SECRET_MUST_NOT_APPEAR")
            async for item in super().synthetic_model():
                yield item

    monkeypatch.setattr(probe, "ProbeProviders", FailingProviders)
    result = evaluate(probe, tmp_path)
    assert len(result["pairs"]) == 20
    assert [pair["index"] for pair in result["pairs"]] == list(range(1, 21))
    failed = result["pairs"][3]
    assert failed["status"] == "FAILED"
    assert failed["cold"]["status"] == "error"
    assert failed["cold"]["reported_total_tokens"] is None
    assert failed["cold"]["usage_status"] == "unknown"
    unknown_measurements(failed["cold"])
    assert "warm" not in failed
    assert result["status"] == "PARTIAL_REVIEW_REQUIRED"

    class ThrowingWeb:
        async def execute(self, *_args):
            raise OSError("SYNTHETIC_WEB_SECRET_MUST_NOT_APPEAR")

    monkeypatch.setenv("AI_NEKO_SEARCH_API_KEY", "SYNTHETIC_SEARCH_KEY")
    monkeypatch.setattr(probe, "WebTools", lambda *_args: ThrowingWeb())
    providers = original(config(probe), probe.synthetic_plan()["pairs"][0], synthetic=False)
    with pytest.raises(OSError):
        asyncio.run(providers.web_tools().execute("search_web", {"query": "synthetic"}))
    assert providers.web_calls[0]["status"] == "failed"
    assert providers.web_calls[0]["error_class"] == "OSError"
    assert providers.web_calls[0]["duration_ms"] >= 0
    assert "SYNTHETIC_WEB_SECRET" not in json.dumps(providers.web_calls)
    assert "SYNTHETIC_ERROR_SECRET" not in json.dumps(result)
    assert "SYNTHETIC_ERROR_SECRET" not in (tmp_path / "measurement.json").read_text()


@pytest.mark.parametrize("stage", ["setup", "close"])
def test_runtime_lifecycle_failure_is_recorded_and_does_not_abandon_remaining_planned_pairs(
    probe, tmp_path, monkeypatch, stage
):
    original = probe.SessionRuntime
    creations = 0

    def runtime(*args, **kwargs):
        nonlocal creations
        creations += 1
        if creations == 4 and stage == "setup":
            raise OSError("SYNTHETIC_LIFECYCLE_SECRET_MUST_NOT_APPEAR")
        instance = original(*args, **kwargs)
        if creations == 4 and stage == "close":
            close = instance.close

            async def failed_close():
                await close()  # Release all actual files before simulating cleanup failure.
                raise OSError("SYNTHETIC_LIFECYCLE_SECRET_MUST_NOT_APPEAR")

            instance.close = failed_close
        return instance

    monkeypatch.setattr(probe, "SessionRuntime", runtime)
    result = evaluate(probe, tmp_path)
    assert len(result["pairs"]) == creations == 20
    failed = result["pairs"][3]
    assert failed["status"] == "FAILED"
    assert failed.get("error_class", failed.get("cleanup_error_class")) == "OSError"
    assert all(pair["status"] != "RUNNING" for pair in result["pairs"])
    assert result["status"] == "PARTIAL_REVIEW_REQUIRED"
    assert "SYNTHETIC_LIFECYCLE_SECRET" not in (tmp_path / "measurement.json").read_text()


def test_failed_cold_search_followed_by_successful_direct_read_is_not_an_eligible_search_pair(
    probe, tmp_path, monkeypatch
):
    original = probe.ProbeProviders

    class FailedSearchProviders(original):
        def web_tools(self):
            adapter = super().web_tools()
            parent = self

            class Counted:
                async def execute(self, name, arguments):
                    if name == "search_web":
                        parent.web_calls.append(
                            {
                                "phase": parent.phase,
                                "name": name,
                                "status": "error",
                                "duration_ms": 1,
                            }
                        )
                        return {"status": "error", "sources": [], "error": "synthetic_search_error"}
                    return await adapter.execute(name, arguments)

            return Counted()

    monkeypatch.setattr(probe, "ProbeProviders", FailedSearchProviders)
    result = evaluate(probe, tmp_path)
    assert len(result["pairs"]) == 20
    pair = result["pairs"][0]
    assert pair["cold"]["status"] == pair["warm"]["status"] == "completed"
    assert pair["cold"]["web_calls"][0]["status"] == "error"
    assert pair["cold"]["web_calls"][1]["name"] == "read_web_page"
    assert pair["cold"]["web_calls"][1]["status"] == "ok"
    assert pair["warm"]["web_calls"] == []
    assert pair["eligible_route"] is False
    assert pair["status"] == "INELIGIBLE_ROUTE"
    assert result["status"] == "PARTIAL_REVIEW_REQUIRED"


def test_real_adapter_usage_snapshots_are_counted_once_and_invalid_final_usage_stays_unknown(
    probe, tmp_path, monkeypatch, provider_http
):
    monkeypatch.setenv("AI_NEKO_MODEL_API_KEY", "SYNTHETIC_REQUEST_KEY")
    monkeypatch.setenv("AI_NEKO_SEARCH_API_KEY", "SYNTHETIC_SEARCH_KEY")
    for invalid in (False, True):
        provider_http(
            [
                delta({"content": "synthetic answer"}),
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
                            "prompt_tokens": True if invalid else 7,
                            "completion_tokens": 2,
                            "total_tokens": 9,
                            "credential": "EXTRA_SECRET_MUST_NOT_APPEAR",
                        },
                    }
                ),
                event("[DONE]"),
            ]
        )

        async def run(invalid=invalid):
            providers = probe.ProbeProviders(
                config(probe), probe.synthetic_plan()["pairs"][0], synthetic=False
            )
            runtime = SessionRuntime(initialize_data_root(tmp_path / str(invalid)), providers)
            try:
                if invalid:
                    runtime.metrics._path = (
                        runtime.paths.logs / "missing-directory" / "metrics.jsonl"
                    )
                return await probe.measure_turn(runtime, providers, "普通合成问候")
            finally:
                await runtime.close()

        result = asyncio.run(run())
        assert result["status"] == "completed"
        assert len(result["model_calls"]) > 0
        if invalid:
            assert result["reported_total_tokens"] is None and result["usage_status"] == "unknown"
            assert all(item["usage"] is None for item in result["model_calls"])
            assert result["retrieval_metrics"] == []
            assert result["retrieval_metrics_status"] == "unknown"
        else:
            assert result["reported_total_tokens"] == 9 * len(result["model_calls"])
            assert result["usage_status"] == "reported"
            assert all(item["usage"]["total_tokens"] == 9 for item in result["model_calls"])
            assert result["retrieval_metrics_status"] == "recorded"
            assert all(item["turn_id"] == result["turn_id"] for item in result["retrieval_metrics"])
        unknown_measurements(result)
        assert "EXTRA_SECRET" not in json.dumps(result)
        assert "SYNTHETIC_REQUEST_KEY" not in json.dumps(result)
