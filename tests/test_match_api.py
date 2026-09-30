"""G4 authenticated match transport, turn binding and delayed voice boundaries."""

import asyncio
import json
from contextlib import asynccontextmanager

import pytest
from test_memory_backup_api import api_client
from test_runtime import Store

from ai_neko.config.paths import initialize_data_root
from ai_neko.media import VoiceService
from ai_neko.runtime import (
    RuntimeAccessError,
    RuntimeConflictError,
    RuntimeInputError,
    SessionRuntime,
)

SID = "a" * 32
MID = "match-" + "b" * 32
TID = "c" * 32
REQUEST = "d" * 32
BASE = f"/api/sessions/{SID}/matches"
CONTROL = {"request_id": REQUEST, "expected_revision": 0}
START = {**CONTROL, "game": "合成星棋", "platform": "Windows", "mode": "排位"}
BINDING = {"match_id": MID, "expected_revision": 2}
ROUTES = [
    ("GET", BASE, None, "match_catalog", (SID,), 200),
    ("GET", BASE + "/" + MID, None, "match_detail", (SID, MID), 200),
    ("POST", BASE, START, "start", (SID,), 201),
    ("POST", BASE + "/" + MID + "/new", START, "new", (SID,), 201),
    ("POST", BASE + "/" + MID + "/end", CONTROL, "end", (SID,), 200),
    ("POST", BASE + "/" + MID + "/update", {**CONTROL, "goal": ""}, "update", (SID,), 200),
    (
        "POST",
        BASE + "/" + MID + "/observations",
        {**CONTROL, "turn_id": TID, "text": "我确认现在有6金币。"},
        "observe",
        (SID,),
        200,
    ),
    ("POST", BASE + "/" + MID + "/close-observation", CONTROL, "close_observation", (SID,), 200),
]


@asynccontextmanager
async def boundary(tmp_path, monkeypatch):
    runtime = SessionRuntime(initialize_data_root(tmp_path / "match-api"), Store())
    calls = []

    def recorder(name):
        async def invoke(*args, **kwargs):
            calls.append((name, args, kwargs))
            return {"boundary": name}

        return invoke

    for name in ("match_catalog", "match_detail", "match_control", "start_turn"):
        monkeypatch.setattr(runtime, name, recorder(name), raising=False)

    def validate(*args):
        calls.append(("validate_match_request", args, {}))

    monkeypatch.setattr(runtime, "validate_match_request", validate, raising=False)
    try:
        async with api_client(runtime) as client:
            yield client, runtime, calls
    finally:
        await runtime.close()


@pytest.mark.parametrize("method,path,value,target,args,status", ROUTES)
def test_match_routes_dispatch_exact_validated_contract(
    tmp_path, monkeypatch, method, path, value, target, args, status
):
    async def run():
        async with boundary(tmp_path, monkeypatch) as (client, _, calls):
            response = await client.request(method, path, **({"json": value} if value else {}))
            assert response.status_code == status, response.text
            if method == "GET":
                expected = (target, args, {})
            else:
                payload = value if target == "start" else {**value, "match_id": MID}
                expected = ("match_control", (SID, target, payload), {})
            assert calls == [expected]

    asyncio.run(run())


@pytest.mark.parametrize("method,path,value,target,args,status", ROUTES)
def test_match_authentication_precedes_body_and_runtime(
    tmp_path, monkeypatch, method, path, value, target, args, status
):
    async def run():
        async with boundary(tmp_path, monkeypatch) as (client, _, calls):
            for headers, expected in (
                ({"Authorization": ""}, 401),
                ({"Authorization": "Bearer wrong"}, 401),
                ({"Origin": "https://untrusted.example"}, 403),
            ):
                response = await client.request(method, path, headers=headers, content=b"bad JSON")
                assert response.status_code == expected
            assert not calls

    asyncio.run(run())


@pytest.mark.parametrize(
    "patch",
    [
        {"scope": "other"},
        {"session_id": SID},
        {"match_id": MID},
        {"path": "/private/other"},
        {"source_kind": "frame"},
        {"frame_id": "forged"},
        {"observed_at": 1},
        {"expires_at": 9999999999},
        {"state_revision": 0},
        {"guide_id": "guide-" + "e" * 32},
        {"selection_revision": 0},
        {"request_id": None},
        {"request_id": "A" * 32},
        {"request_id": "a" * 31},
        {"expected_revision": True},
        {"expected_revision": -1},
        {"expected_revision": 0.0},
        {"expected_revision": "0"},
    ],
)
def test_every_match_mutation_rejects_invalid_or_untrusted_controls(tmp_path, monkeypatch, patch):
    async def run():
        async with boundary(tmp_path, monkeypatch) as (client, _, calls):
            for method, path, value, *_ in ROUTES:
                if method == "POST":
                    response = await client.post(path, json={**value, **patch})
                    assert response.status_code == 400, (path, response.text)
            assert not calls

    asyncio.run(run())


@pytest.mark.parametrize(
    "patch",
    [
        {"game": ""},
        {"game": " "},
        {"game": "猫" * 201},
        {"platform": None},
        {"mode": "排位\x00"},
        {"mode": "排位\n"},
        {"game_version": 1},
        {"game_version": "x" * 201},
        {"goal": None},
        {"goal": "猫" * 2001},
        {"goal": "\ud800"},
    ],
)
def test_start_and_new_validate_game_context(tmp_path, monkeypatch, patch):
    async def run():
        async with boundary(tmp_path, monkeypatch) as (client, _, calls):
            for path in (BASE, BASE + "/" + MID + "/new"):
                response = await client.post(
                    path,
                    content=json.dumps({**START, **patch}),
                    headers={"content-type": "application/json"},
                )
                assert response.status_code == 400
            assert not calls

    asyncio.run(run())


@pytest.mark.parametrize(
    "patch", [{"turn_id": None}, {"turn_id": MID}, {"text": " "}, {"text": "猫" * 2001}]
)
def test_observation_requires_owned_turn_identifier_and_bounded_user_text(
    tmp_path, monkeypatch, patch
):
    async def run():
        async with boundary(tmp_path, monkeypatch) as (client, _, calls):
            response = await client.post(
                BASE + "/" + MID + "/observations",
                json={**CONTROL, "turn_id": TID, "text": "当前6金币", **patch},
            )
            assert response.status_code == 400 and not calls

    asyncio.run(run())


def test_match_optional_version_and_empty_goal_are_preserved(tmp_path, monkeypatch):
    async def run():
        async with boundary(tmp_path, monkeypatch) as (client, _, calls):
            payload = {**START, "game_version": None, "goal": ""}
            assert (await client.post(BASE, json=payload)).status_code == 201
            assert calls == [("match_control", (SID, "start", payload), {})]

    asyncio.run(run())


@pytest.mark.parametrize(
    "fields",
    [
        {"game_version": None},
        {"game_version": "2.4"},
        {"goal": ""},
        {"goal": "猫" * 2000, "game_version": None},
    ],
)
def test_match_update_accepts_goal_or_nullable_version(tmp_path, monkeypatch, fields):
    async def run():
        async with boundary(tmp_path, monkeypatch) as (client, _, calls):
            payload = {**CONTROL, **fields}
            response = await client.post(BASE + "/" + MID + "/update", json=payload)
            assert response.status_code == 200
            assert calls == [("match_control", (SID, "update", {**payload, "match_id": MID}), {})]

    asyncio.run(run())


@pytest.mark.parametrize(
    "fields",
    [{}, {"goal": None}, {"goal": "猫" * 2001}, {"game_version": 0}, {"game_version": "x" * 201}],
)
def test_match_update_rejects_empty_or_invalid_changes(tmp_path, monkeypatch, fields):
    async def run():
        async with boundary(tmp_path, monkeypatch) as (client, _, calls):
            response = await client.post(BASE + "/" + MID + "/update", json={**CONTROL, **fields})
            assert response.status_code == 400 and not calls

    asyncio.run(run())


def test_match_fields_queries_identifiers_and_size_are_strict(tmp_path, monkeypatch):
    async def run():
        async with boundary(tmp_path, monkeypatch) as (client, _, calls):
            for method, path, value, *_ in ROUTES:
                options = {"json": value} if value else {}
                for suffix in ("?scope=other", "?x=1&x=2", "?expected_revision=1"):
                    assert (
                        await client.request(method, path + suffix, **options)
                    ).status_code == 400
                assert (
                    await client.request(method, path.replace(SID, "invalid"), **options)
                ).status_code == 400
                if MID in path:
                    assert (
                        await client.request(
                            method, path.replace(MID, "match-" + "B" * 32), **options
                        )
                    ).status_code == 400
                if method == "POST":
                    for missing in value:
                        reduced = {key: item for key, item in value.items() if key != missing}
                        assert (await client.post(path, json=reduced)).status_code == 400
                    assert (await client.post(path, json=[])).status_code == 400
                    assert (await client.post(path, content=b"not JSON")).status_code == 415
                    assert (
                        await client.post(
                            path, content=b"bad", headers={"content-type": "application/json"}
                        )
                    ).status_code == 400
                    assert (await client.post(path, json={"text": "x" * 65536})).status_code == 413
            assert not calls

    asyncio.run(run())


@pytest.mark.parametrize(
    "exception,status",
    [(RuntimeInputError, 400), (RuntimeAccessError, 404), (RuntimeConflictError, 409)],
)
def test_match_runtime_errors_never_expose_private_exception_data(
    tmp_path, monkeypatch, exception, status
):
    async def run():
        async with boundary(tmp_path, monkeypatch) as (client, runtime, _):

            async def fail(*args):
                raise exception("PRIVATE_SECRET /private/source.sqlite scope=other")

            monkeypatch.setattr(runtime, "match_control", fail)
            response = await client.post(BASE, json=START)
            assert response.status_code == status
            assert "PRIVATE_SECRET" not in response.text and "sqlite" not in response.text

    asyncio.run(run())


@pytest.mark.parametrize(
    "extra",
    [
        {},
        {"match": BINDING},
        {"input_origin": "voice"},
        {"review_match_id": MID},
        {
            "match": {"match_id": None, "expected_revision": 0},
            "input_origin": "text",
            "review_match_id": None,
        },
    ],
)
def test_turn_dispatch_preserves_only_explicit_new_keywords(tmp_path, monkeypatch, extra):
    async def run():
        async with boundary(tmp_path, monkeypatch) as (client, _, calls):
            response = await client.post(
                f"/api/sessions/{SID}/turns", json={"text": "下一步呢", **extra}
            )
            assert response.status_code == 202
            assert calls == [
                (
                    "start_turn",
                    (SID, "下一步呢"),
                    {"guide": False, "request_id": None, "image": None, **extra},
                )
            ]

    asyncio.run(run())


@pytest.mark.parametrize(
    "extra",
    [
        {"match": None},
        {"match": {}},
        {"match": {**BINDING, "scope": "other"}},
        {"match": {**BINDING, "expected_revision": True}},
        {"match": {**BINDING, "match_id": "invalid"}},
        {"match": {**BINDING, "expected_revision": -1}},
        {"input_origin": "image"},
        {"input_origin": {}},
        {"input_origin": None},
        {"review_match_id": "invalid"},
        {"scope": "other"},
    ],
)
def test_turn_rejects_untrusted_binding_shape(tmp_path, monkeypatch, extra):
    async def run():
        async with boundary(tmp_path, monkeypatch) as (client, _, calls):
            response = await client.post(
                f"/api/sessions/{SID}/turns", json={"text": "下一步呢", **extra}
            )
            assert response.status_code == 400 and not calls

    asyncio.run(run())


VOICE_INPUTS = {
    "transcribe": {"audio_base64": "synthetic", "mime_type": "audio/wav"},
    "synthesize": {"text": "合成朗读"},
}


@pytest.mark.parametrize("kind", VOICE_INPUTS)
@pytest.mark.parametrize("bound", [False, True])
def test_voice_binding_is_checked_before_and_after_provider_and_not_forwarded(
    tmp_path, monkeypatch, kind, bound
):
    async def run():
        async with boundary(tmp_path, monkeypatch) as (client, _, calls):

            async def voice(self, **kwargs):
                calls.append((kind, (), kwargs))
                return {"synthetic": True}

            monkeypatch.setattr(VoiceService, kind, voice)
            additional = {"session_id": SID, "match": BINDING} if bound else {}
            response = await client.post(
                "/api/voice/" + kind,
                json={**VOICE_INPUTS[kind], "request_id": REQUEST, **additional},
            )
            assert response.status_code == 200 and response.json() == {"synthetic": True}
            check = ("validate_match_request", (SID, BINDING), {})
            expected = [(kind, (), VOICE_INPUTS[kind])]
            assert calls == ([check, *expected, check] if bound else expected)

    asyncio.run(run())


@pytest.mark.parametrize(
    "extra",
    [
        {"session_id": SID},
        {"match": BINDING},
        {"session_id": "invalid", "match": BINDING},
        {"session_id": SID, "match": None},
        {"session_id": SID, "match": {**BINDING, "expected_revision": True}},
        {"session_id": SID, "match": {**BINDING, "scope": "other"}},
    ],
)
def test_voice_requires_complete_valid_match_binding_pair(tmp_path, monkeypatch, extra):
    async def run():
        async with boundary(tmp_path, monkeypatch) as (client, _, calls):
            for kind, values in VOICE_INPUTS.items():
                response = await client.post("/api/voice/" + kind, json={**values, **extra})
                assert response.status_code == 400
            assert not calls

    asyncio.run(run())


@pytest.mark.parametrize("kind", VOICE_INPUTS)
def test_old_voice_result_is_rejected_after_match_revision_changes(tmp_path, monkeypatch, kind):
    async def run():
        async with boundary(tmp_path, monkeypatch) as (client, runtime, calls):
            entered, release = asyncio.Event(), asyncio.Event()
            current = [2]

            def validate(sid, binding):
                if binding["expected_revision"] != current[0]:
                    raise RuntimeConflictError("synthetic old match")

            async def voice(self, **kwargs):
                entered.set()
                await release.wait()
                return {"text": "LATE_OLD_MATCH_RESPONSE"}

            monkeypatch.setattr(runtime, "validate_match_request", validate)
            monkeypatch.setattr(VoiceService, kind, voice)
            pending = asyncio.create_task(
                client.post(
                    "/api/voice/" + kind,
                    json={
                        **VOICE_INPUTS[kind],
                        "session_id": SID,
                        "match": BINDING,
                        "request_id": REQUEST,
                    },
                )
            )
            await asyncio.wait_for(entered.wait(), 2)
            current[0] += 1
            release.set()
            response = await pending
            assert response.status_code == 409
            assert "LATE_OLD_MATCH_RESPONSE" not in response.text
            assert not runtime.voice_tasks and not calls

    asyncio.run(run())


def test_real_match_api_lifecycle_replay_binding_and_user_confirmation(tmp_path):
    from uuid import uuid4

    from test_runtime import settled

    async def run():
        runtime = SessionRuntime(initialize_data_root(tmp_path / "real-match-api"), Store())
        try:
            async with api_client(runtime) as client:
                sid = (await client.post("/api/sessions")).json()["id"]
                base = f"/api/sessions/{sid}/matches"
                assert (await client.get(base)).json() == {
                    "revision": 0,
                    "current": None,
                    "matches": [],
                }
                start = {
                    **START,
                    "request_id": uuid4().hex,
                    "goal": "争取进入前四",
                    "game_version": "2.4",
                }
                response = await client.post(base, json=start)
                assert response.status_code == 201, response.text
                first = response.json()
                mid = first["match_id"]
                assert first["revision"] == 1 and first["current"]["status"] == "active"
                assert (await client.post(base, json=start)).json()["replayed"]
                assert (
                    await client.post(base, json={**start, "goal": "changed"})
                ).status_code == 409
                assert (
                    await client.post(f"/api/sessions/{sid}/turns", json={"text": "缺少对局绑定"})
                ).status_code == 400
                literal = "确认观察：当前金币6"
                response = await client.post(
                    f"/api/sessions/{sid}/turns",
                    json={
                        "text": literal,
                        "match": {"match_id": mid, "expected_revision": 1},
                        "input_origin": "text",
                    },
                )
                assert response.status_code == 202, response.text
                tid = response.json()["id"]
                await settled(runtime, sid, tid)
                catalog = (await client.get(base)).json()
                controls = {"request_id": uuid4().hex, "expected_revision": catalog["revision"]}
                observed = await client.post(
                    base + f"/{mid}/observations",
                    json={**controls, "turn_id": tid, "text": "当前金币6"},
                )
                assert observed.status_code == 200, observed.text
                current = observed.json()
                detail = (await client.get(base + "/" + mid)).json()
                assert any(
                    item["text"] == "当前金币6" and item["turn_id"] == tid
                    for item in detail["observations"]
                )
                assert (
                    await client.post(
                        base + f"/{mid}/observations",
                        json={**controls, "turn_id": tid, "text": "当前金币6"},
                    )
                ).json()["replayed"]
                invented = await client.post(
                    base + f"/{mid}/observations",
                    json={
                        "request_id": uuid4().hex,
                        "expected_revision": current["revision"],
                        "turn_id": tid,
                        "text": "我已执行升级",
                    },
                )
                assert invented.status_code == 400
                update = await client.post(
                    base + f"/{mid}/update",
                    json={
                        "request_id": uuid4().hex,
                        "expected_revision": current["revision"],
                        "game_version": None,
                    },
                )
                assert update.status_code == 200 and update.json()["match"]["game_version"] is None
                current = update.json()
                closed = await client.post(
                    base + f"/{mid}/close-observation",
                    json={"request_id": uuid4().hex, "expected_revision": current["revision"]},
                )
                assert closed.status_code == 200
                replacement = {
                    **START,
                    "request_id": uuid4().hex,
                    "expected_revision": closed.json()["revision"],
                }
                next_match = await client.post(base + f"/{mid}/new", json=replacement)
                assert next_match.status_code == 201, next_match.text
                current = next_match.json()
                assert current["match_id"] != mid
                assert (await client.get(base + "/" + mid)).json()["status"] == "ended"
                assert (await client.get(base + "/" + current["match_id"])).json()[
                    "observations"
                ] == []
                end = await client.post(
                    base + f"/{current['match_id']}/end",
                    json={"request_id": uuid4().hex, "expected_revision": current["revision"]},
                )
                assert end.status_code == 200 and end.json()["current"] is None
                replay = (await client.post(base + f"/{mid}/new", json=replacement)).json()
                assert replay["replayed"] and replay["superseded"] and replay["current"] is None
        finally:
            await runtime.close()

    asyncio.run(run())


def test_real_match_api_scope_ownership_and_restart_receipts(tmp_path):
    from uuid import uuid4

    async def run():
        paths = initialize_data_root(tmp_path / "real-match-restart")
        runtime = SessionRuntime(paths, Store())
        try:
            async with api_client(runtime) as client:
                sid, other = [(await client.post("/api/sessions")).json()["id"] for _ in range(2)]
                base = f"/api/sessions/{sid}/matches"
                payload = {**START, "request_id": uuid4().hex}
                first = (await client.post(base, json=payload)).json()
                mid = first["match_id"]
                assert (await client.get(f"/api/sessions/{other}/matches/{mid}")).status_code == 404
                response = await client.post(
                    f"/api/sessions/{other}/matches/{mid}/end",
                    json={"request_id": uuid4().hex, "expected_revision": 0},
                )
                assert response.status_code == 404
        finally:
            await runtime.close()
        reopened = SessionRuntime(paths, Store())
        try:
            async with api_client(reopened) as client:
                catalog = (await client.get(base)).json()
                assert catalog["revision"] > first["revision"]
                assert catalog["current"]["status"] == "needs_update"
                detail = (await client.get(base + "/" + mid)).json()
                assert detail["observations"] == []
                replay = (await client.post(base, json=payload)).json()
                assert replay["replayed"] and replay["superseded"] and replay["match_id"] == mid
                response = await client.post(
                    f"/api/sessions/{sid}/turns",
                    json={
                        "text": "旧请求",
                        "match": {"match_id": mid, "expected_revision": first["revision"]},
                    },
                )
                assert response.status_code == 409
        finally:
            await reopened.close()

    asyncio.run(run())


@pytest.mark.parametrize("action", ["start", "new", "update"])
def test_real_match_api_and_store_share_2000_character_goal_limit(tmp_path, action):
    from uuid import uuid4

    async def run():
        runtime = SessionRuntime(initialize_data_root(tmp_path / "real-goal-limit"), Store())
        try:
            async with api_client(runtime) as client:
                sid = (await client.post("/api/sessions")).json()["id"]
                base = f"/api/sessions/{sid}/matches"
                goal = "猫" * 2000
                if action == "start":
                    path, value, status = (
                        base,
                        {**START, "request_id": uuid4().hex, "goal": goal},
                        201,
                    )
                else:
                    created = (
                        await client.post(base, json={**START, "request_id": uuid4().hex})
                    ).json()
                    path = base + f"/{created['match_id']}/{action}"
                    value = {
                        "request_id": uuid4().hex,
                        "expected_revision": created["revision"],
                        "goal": goal,
                    }
                    if action == "new":
                        value |= {key: START[key] for key in ("game", "platform", "mode")}
                    status = 201 if action == "new" else 200
                response = await client.post(path, json=value)
                assert response.status_code == status, response.text
                assert response.json()["match"]["goal"] == goal
        finally:
            await runtime.close()

    asyncio.run(run())


@pytest.mark.parametrize("kind", VOICE_INPUTS)
def test_real_match_change_rejects_voice_provider_that_returns_after_cancellation(
    tmp_path, monkeypatch, kind
):
    from uuid import uuid4

    async def run():
        runtime = SessionRuntime(initialize_data_root(tmp_path / "real-match-voice"), Store())
        entered, released = asyncio.Event(), asyncio.Event()

        async def voice(self, **kwargs):
            entered.set()
            try:
                await asyncio.Event().wait()
            except asyncio.CancelledError:
                return {"text": "OLD_MATCH_VOICE_RESULT"}
            finally:
                released.set()

        monkeypatch.setattr(VoiceService, kind, voice)
        try:
            async with api_client(runtime) as client:
                sid = (await client.post("/api/sessions")).json()["id"]
                base = f"/api/sessions/{sid}/matches"
                first = (await client.post(base, json={**START, "request_id": uuid4().hex})).json()
                mid = first["match_id"]
                pending = asyncio.create_task(
                    client.post(
                        "/api/voice/" + kind,
                        json={
                            **VOICE_INPUTS[kind],
                            "request_id": uuid4().hex,
                            "session_id": sid,
                            "match": {"match_id": mid, "expected_revision": first["revision"]},
                        },
                    )
                )
                await asyncio.wait_for(entered.wait(), 2)
                changed = await client.post(
                    base + f"/{mid}/new",
                    json={
                        **START,
                        "request_id": uuid4().hex,
                        "expected_revision": first["revision"],
                    },
                )
                assert changed.status_code == 201, changed.text
                response = await pending
                assert response.status_code == 409 and "OLD_MATCH_VOICE_RESULT" not in response.text
                assert released.is_set() and not runtime.voice_tasks
        finally:
            await runtime.close()

    asyncio.run(run())
