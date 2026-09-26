"""Real local server/model processes; all provider responses and user data are synthetic."""

from __future__ import annotations

import importlib.util
import json
import time
from pathlib import Path

import pytest
import test_server_process

server_factory = test_server_process.server_factory

FIXTURE = Path(__file__).resolve().parents[1] / "scripts/synthetic_model.py"
SPEC = importlib.util.spec_from_file_location("synthetic_model", FIXTURE)
MODEL = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(MODEL)


def api(server, method, path, data=None):
    response = server.request(
        method,
        path,
        headers={**server.auth, "Origin": server.connection["http_url"]},
        **({"json": data} if data is not None else {}),
    )
    assert 200 <= response.status_code < 300, (response.status_code, response.text)
    return response.json()


def test_ui_assets_and_api_security(server_factory):
    server = server_factory()
    for path in ("/", "/static/app.js", "/static/style.css"):
        response = server.request("GET", path)
        assert response.status_code == 200
        assert response.headers["cache-control"] == "no-store"
        assert "frame-ancestors 'none'" in response.headers["content-security-policy"]
        assert server.connection["token"] not in response.text
    for method, path in (
        ("GET", "/api/config"),
        ("PUT", "/api/config"),
        ("GET", "/api/sessions"),
        ("POST", "/api/sessions"),
    ):
        assert server.request(method, path).status_code == 401
        response = server.request(
            method, path, headers={**server.auth, "Origin": "https://evil.invalid"}
        )
        assert response.status_code == 403
    assert server.request("GET", "/static/credentials.py").status_code == 404
    assert server.request("POST", "/api/bootstrap", json={"code": "bad"}).status_code == 403
    assert (
        server.request("PUT", "/api/config", headers=server.auth, content=b"not JSON").status_code
        == 415
    )
    response = server.request(
        "PUT", "/api/config", headers=server.auth, json={"model": "x" * 70000}
    )
    assert response.status_code == 413
    session = api(server, "POST", "/api/sessions")
    sid = session.get("session_id", session.get("id"))
    assert (
        server.request(
            "GET", f"/api/sessions/{sid}/turns/invalid/events?after=bad", headers=server.auth
        ).status_code
        == 400
    )
    assert server.request("GET", "/api/sessions/invalid", headers=server.auth).status_code == 404


@pytest.mark.usefixtures("sandbox_compatible")
def test_real_process_stream_cancel_and_restart(server_factory):
    server = server_factory()
    with MODEL.SyntheticModel() as model:
        configured = api(
            server, "PUT", "/api/config", {"model_base_url": model.url, "model": "synthetic-model"}
        )
        assert "model_api_key" not in configured
        session = api(server, "POST", "/api/sessions")
        sid = session.get("session_id", session.get("id"))
        base = f"/api/sessions/{sid}"
        turn = api(server, "POST", base + "/turns", {"text": "slow synthetic", "guide": False})
        path = base + "/turns/" + turn["turn_id"]
        deadline = time.monotonic() + 10
        visible = ""
        sequence = 0
        while time.monotonic() < deadline:
            batch = api(server, "GET", path + f"/events?after={sequence}")
            for event in batch["events"]:
                sequence = event["seq"]
                if event["type"] == "text":
                    visible += event["text"]
            if visible:
                assert batch["status"] in {"accepted", "running", "active"}
                api(server, "POST", path + "/ack", {"sequence": sequence})
                break
            time.sleep(0.03)
        assert visible
        api(server, "POST", path + "/cancel")
        settled = api(server, "GET", base)["turns"][0]
        assert settled["status"] == "cancelled"
        assert settled["confirmed_text"] == visible
        for _ in range(2):
            api(server, "POST", path + "/cancel")
        assert len(api(server, "GET", base)["turns"]) == 1
        original_root = server.root
        server.close()
        restarted = server_factory(original_root)
        history = api(restarted, "GET", base)
        assert history["turns"][0]["confirmed_text"] == visible
        assert len(model.requests) == 1
        second = api(restarted, "POST", base + "/turns", {"text": "continue", "guide": False})
        second_path = base + "/turns/" + second["turn_id"]
        deadline = time.monotonic() + 10
        while time.monotonic() < deadline:
            batch = api(restarted, "GET", second_path + "/events?after=0")
            if batch["status"] not in {"accepted", "running", "active"}:
                break
            time.sleep(0.04)
        assert batch["status"] == "completed"
        assert any(e["type"] == "text" and "合成回复" in e["text"] for e in batch["events"])
        assert len(model.requests) == 2
        assert visible in json.dumps(model.requests[-1], ensure_ascii=False)


def test_bootstrap_is_single_use_and_same_origin(tmp_path):
    from fastapi.testclient import TestClient

    from ai_neko.app.server import Connection, create_app
    from ai_neko.config.paths import initialize_data_root
    from ai_neko.config.providers import ProviderStore
    from ai_neko.runtime import SessionRuntime

    paths = initialize_data_root(tmp_path / "bootstrap")
    store = ProviderStore(paths)
    runtime = SessionRuntime(paths, store)
    connection = Connection(12345, "synthetic-local-token", "synthetic-instance")
    app = create_app(
        connection, lambda: None, runtime=runtime, providers=store, bootstrap_code="synthetic-code"
    )
    with TestClient(app, base_url=connection.url) as client:
        assert client.post("/api/bootstrap", json={"code": "synthetic-code"}).status_code == 403
        headers = {"Origin": connection.url}
        assert (
            client.post("/api/bootstrap", headers=headers, json={"code": "wrong"}).status_code
            == 401
        )
        result = client.post("/api/bootstrap", headers=headers, json={"code": "synthetic-code"})
        assert result.json() == {"token": "synthetic-local-token"}
        assert (
            client.post(
                "/api/bootstrap", headers=headers, json={"code": "synthetic-code"}
            ).status_code
            == 401
        )
        import asyncio

        asyncio.run(runtime.close())
