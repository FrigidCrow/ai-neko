"""G2 authenticated transport boundaries; fixtures never use real providers or data."""

import asyncio
import json
from contextlib import asynccontextmanager

import pytest
from test_memory_backup_api import api_client
from test_runtime import Store

from ai_neko.config.paths import initialize_data_root
from ai_neko.memory.guides import (
    GuideAccessError,
    GuideCapacityError,
    GuideConflictError,
    GuideInputError,
)
from ai_neko.runtime import SessionRuntime

GUIDE = "guide-" + "a" * 32
REVISION = "revision-" + "b" * 32
REQUEST = "c" * 32
BACKUP = "guides-" + "d" * 32 + ".sqlite"
CONTROLS = {"request_id": REQUEST, "expected_revision": 0}
SELECTION = {
    **CONTROLS,
    "game": "合成星棋",
    "platform": "Windows",
    "mode": "排位",
    "guide_id": GUIDE,
    "revision_id": REVISION,
}
# A recording boundary verifies exactly which validated values reach Runtime.
# Runtime's durable lifecycle is exercised separately by its integration tests.
ROUTES = [
    ("GET", "/api/guides", None, "guide_catalog", (), {}, 200),
    (
        "GET",
        f"/api/guides/{GUIDE}",
        None,
        "guide_detail",
        (GUIDE,),
        {"revision_id": None},
        200,
    ),
    (
        "GET",
        f"/api/guides/{GUIDE}?revision_id={REVISION}",
        None,
        "guide_detail",
        (GUIDE,),
        {"revision_id": REVISION},
        200,
    ),
    ("PUT", "/api/guide-selection", SELECTION, "guide_selection", (SELECTION,), {}, 200),
    (
        "POST",
        "/api/guides",
        {**CONTROLS, "url": "https://example.com/guide"},
        "guide_fetch",
        ({**CONTROLS, "url": "https://example.com/guide"},),
        {},
        202,
    ),
    (
        "POST",
        f"/api/guides/{GUIDE}/refresh",
        CONTROLS,
        "guide_fetch",
        (CONTROLS,),
        {"guide_id": GUIDE},
        202,
    ),
    (
        "DELETE",
        f"/api/guides/{GUIDE}",
        {**CONTROLS, "confirm": True},
        "guide_delete",
        (GUIDE, {**CONTROLS, "confirm": True}),
        {},
        200,
    ),
    (
        "GET",
        f"/api/guide-operations/{REQUEST}",
        None,
        "guide_operation",
        (REQUEST,),
        {},
        200,
    ),
    (
        "POST",
        f"/api/guide-operations/{REQUEST}/cancel",
        {},
        "cancel_guide_operation",
        (REQUEST,),
        {},
        200,
    ),
    ("GET", "/api/guide-backups", None, "guide_backups", (), {}, 200),
    (
        "POST",
        "/api/guide-backups",
        {"request_id": REQUEST},
        "backup_guides",
        ({"request_id": REQUEST},),
        {},
        201,
    ),
    (
        "POST",
        f"/api/guide-backups/{BACKUP}/restore",
        {**CONTROLS, "confirm": True},
        "restore_guides",
        (BACKUP, {**CONTROLS, "confirm": True}),
        {},
        200,
    ),
    (
        "DELETE",
        f"/api/guide-backups/{BACKUP}",
        None,
        "delete_guide_backup",
        (BACKUP,),
        {},
        200,
    ),
]


@asynccontextmanager
async def api_boundary(tmp_path, monkeypatch):
    runtime = SessionRuntime(initialize_data_root(tmp_path / "guide-api"), Store())
    calls = []

    def record(method):
        async def invoke(*args, **kwargs):
            calls.append((method, args, kwargs))
            return {"boundary": method}

        return invoke

    for method in {row[3] for row in ROUTES}:
        monkeypatch.setattr(runtime, method, record(method), raising=False)
    try:
        async with api_client(runtime) as client:
            yield client, runtime, calls
    finally:
        await runtime.close()


@pytest.mark.parametrize("method,path,value,target,args,kwargs,status", ROUTES)
def test_guide_routes_dispatch_only_validated_contract(
    tmp_path, monkeypatch, method, path, value, target, args, kwargs, status
):
    async def run():
        async with api_boundary(tmp_path, monkeypatch) as (client, _, calls):
            options = {"json": value} if value is not None else {}
            response = await client.request(method, path, **options)
            assert response.status_code == status, response.text
            assert response.json() == {"boundary": target}
            assert calls == [(target, args, kwargs)]

    asyncio.run(run())


@pytest.mark.parametrize("route", ROUTES, ids=lambda row: row[0] + " " + row[1])
def test_guide_routes_authenticate_before_body_or_runtime(tmp_path, monkeypatch, route):
    async def run():
        async with api_boundary(tmp_path, monkeypatch) as (client, runtime, calls):
            for headers, expected in (
                ({"Authorization": ""}, 401),
                ({"Authorization": "Bearer synthetic-wrong-token"}, 401),
                ({"Origin": "https://untrusted.example"}, 403),
            ):
                response = await client.request(
                    route[0], route[1], headers=headers, content=b"not json"
                )
                assert response.status_code == expected, response.text
            assert calls == []
            assert runtime.memory.guides.list_documents() == []

    asyncio.run(run())


@pytest.mark.parametrize(
    "patch",
    [
        {"scope": "other-scope"},
        {"path": "/private/synthetic"},
        {"text": "forged body"},
        {"request_id": None},
        {"request_id": "a" * 31},
        {"request_id": "A" * 32},
        {"request_id": "a" * 32 + "\n"},
        {"expected_revision": True},
        {"expected_revision": -1},
        {"expected_revision": "0"},
        {"expected_revision": 0.0},
    ],
)
def test_control_mutations_reject_unknown_and_invalid_fields(tmp_path, monkeypatch, patch):
    async def run():
        async with api_boundary(tmp_path, monkeypatch) as (client, _, calls):
            for method, path, value, *_ in ROUTES:
                if isinstance(value, dict) and "expected_revision" in value:
                    response = await client.request(method, path, json={**value, **patch})
                    assert response.status_code == 400, (method, path, response.text)
            assert calls == []

    asyncio.run(run())


@pytest.mark.parametrize(
    "patch",
    [
        {"game": ""},
        {"game": "  "},
        {"platform": None},
        {"mode": "\n"},
        {"game": "猫" * 201},
        {"platform": "Windows\x00"},
        {"guide_id": None},
        {"revision_id": None},
        {"guide_id": "revision-" + "a" * 32},
        {"revision_id": GUIDE},
    ],
)
def test_selection_requires_context_and_pair_of_opaque_ids(tmp_path, monkeypatch, patch):
    async def run():
        async with api_boundary(tmp_path, monkeypatch) as (client, _, calls):
            response = await client.put("/api/guide-selection", json={**SELECTION, **patch})
            assert response.status_code == 400, response.text
            assert calls == []

    asyncio.run(run())


def test_selection_explicit_null_pair_is_valid_cancel_adoption(tmp_path, monkeypatch):
    async def run():
        async with api_boundary(tmp_path, monkeypatch) as (client, _, calls):
            value = {**SELECTION, "guide_id": None, "revision_id": None}
            response = await client.put("/api/guide-selection", json=value)
            assert response.status_code == 200
            assert calls == [("guide_selection", (value,), {})]

    asyncio.run(run())


@pytest.mark.parametrize(
    "patch",
    [
        {"url": "file:///private/guide"},
        {"url": "https://user:secret@example.com"},
        {"url": "https://example.com/" + "a" * 4096},
        {"url": 42},
        {"url": "https://example.com/guide\n"},
        {"url": "//example.com/guide"},
        {"game": None},
        {"game_version": "v" * 201},
        {"version_basis": "x" * 2001},
        {"game_version": 123},
        {"version_basis": {"path": "/private"}},
        {"platform": "\ud800"},
        {"content": "forged text"},
    ],
)
def test_import_rejects_invalid_url_and_untrusted_metadata(tmp_path, monkeypatch, patch):
    async def run():
        async with api_boundary(tmp_path, monkeypatch) as (client, _, calls):
            value = {**CONTROLS, "url": "https://example.com/guide", **patch}
            # ensure_ascii represents invalid surrogate input as JSON's escape syntax.
            response = await client.post(
                "/api/guides",
                content=json.dumps(value),
                headers={"Content-Type": "application/json"},
            )
            assert response.status_code == 400, response.text
            assert calls == []

    asyncio.run(run())


def test_import_accepts_bounded_optional_metadata(tmp_path, monkeypatch):
    async def run():
        async with api_boundary(tmp_path, monkeypatch) as (client, _, calls):
            value = {
                **CONTROLS,
                "url": "https://example.com/guide#section",
                "game": "合成星棋",
                "platform": "Windows",
                "mode": "排位",
                "game_version": "1.2",
                "version_basis": "公开正文注明版本1.2",
            }
            response = await client.post("/api/guides", json=value)
            assert response.status_code == 202
            assert calls == [("guide_fetch", (value,), {})]

    asyncio.run(run())


@pytest.mark.parametrize("confirmation", [None, False, 1, "true"])
def test_delete_and_restore_require_literal_confirmation(tmp_path, monkeypatch, confirmation):
    async def run():
        async with api_boundary(tmp_path, monkeypatch) as (client, _, calls):
            for method, path in (
                ("DELETE", f"/api/guides/{GUIDE}"),
                ("POST", f"/api/guide-backups/{BACKUP}/restore"),
            ):
                response = await client.request(
                    method, path, json={**CONTROLS, "confirm": confirmation}
                )
                assert response.status_code == 400
            assert calls == []

    asyncio.run(run())


def test_mutations_require_complete_json_schema_and_bound_body_size(tmp_path, monkeypatch):
    async def run():
        async with api_boundary(tmp_path, monkeypatch) as (client, _, calls):
            for method, path, value, *_ in ROUTES:
                if not isinstance(value, dict):
                    continue
                # Every required field matters; cancellation alone takes an empty object.
                for missing in value:
                    incomplete = {key: item for key, item in value.items() if key != missing}
                    response = await client.request(method, path, json=incomplete)
                    assert response.status_code == 400, (method, path, missing)
                for content, headers, status in (
                    (b"not json", {}, 415),
                    (b"{", {"Content-Type": "application/json"}, 400),
                    (b"[]", {"Content-Type": "application/json"}, 400),
                    (b" " * 65537, {"Content-Type": "application/json"}, 413),
                ):
                    response = await client.request(method, path, content=content, headers=headers)
                    assert response.status_code == status, (method, path, response.text)
            assert calls == []

    asyncio.run(run())


def test_backup_and_cancel_do_not_accept_scope_paths_or_other_body_fields(tmp_path, monkeypatch):
    async def run():
        async with api_boundary(tmp_path, monkeypatch) as (client, _, calls):
            for path, value in (
                ("/api/guide-backups", {"request_id": REQUEST, "path": "/private/synthetic"}),
                ("/api/guide-backups", {"request_id": REQUEST, "scope": "other"}),
                ("/api/guide-backups", {"request_id": "invalid"}),
                (f"/api/guide-operations/{REQUEST}/cancel", {"scope": "other"}),
            ):
                response = await client.post(path, json=value)
                assert response.status_code == 400
            response = await client.request("DELETE", f"/api/guide-backups/{BACKUP}", json={})
            assert response.status_code == 400
            assert calls == []

    asyncio.run(run())


@pytest.mark.parametrize(
    "method,path",
    [
        ("GET", "/api/guides/not-a-guide"),
        ("GET", f"/api/guides/{GUIDE}?revision_id={GUIDE}"),
        ("GET", f"/api/guides/{GUIDE}?revision_id={REVISION}&revision_id={REVISION}"),
        ("GET", f"/api/guides/{GUIDE}?revision_id="),
        ("GET", f"/api/guides/{GUIDE}?scope=other"),
        ("GET", "/api/guides?scope=other"),
        ("GET", "/api/guide-backups?path=/private/synthetic"),
        ("GET", "/api/guide-operations/../../config"),
        ("GET", "/api/guide-operations/invalid"),
        ("POST", f"/api/guide-operations/{REQUEST}/cancel?scope=other"),
        ("POST", f"/api/guides/{GUIDE}/refresh?url=https://example.com/other"),
        ("POST", f"/api/guide-backups/memory-{'d' * 32}.sqlite/restore"),
        ("DELETE", "/api/guide-backups/%2Fprivate%2Fsynthetic.sqlite"),
        ("DELETE", "/api/guide-backups/guides-synthetic.sqlite"),
    ],
)
def test_routes_reject_foreign_identifiers_and_extra_query_inputs(
    tmp_path, monkeypatch, method, path
):
    async def run():
        async with api_boundary(tmp_path, monkeypatch) as (client, _, calls):
            response = await client.request(method, path, json={})
            assert response.status_code in {400, 404}
            assert calls == []

    asyncio.run(run())


@pytest.mark.parametrize(
    "exception,status",
    [
        (GuideInputError, 400),
        (GuideAccessError, 404),
        (GuideConflictError, 409),
        (GuideCapacityError, 507),
    ],
)
def test_guide_errors_are_generic_and_never_leak_exception_details(
    tmp_path, monkeypatch, exception, status
):
    async def run():
        async with api_boundary(tmp_path, monkeypatch) as (client, runtime, _):

            async def fail():
                raise exception("SYNTHETIC_SECRET /private/synthetic guides.sqlite scope=other")

            monkeypatch.setattr(runtime, "guide_catalog", fail)
            response = await client.get("/api/guides")
            assert response.status_code == status
            assert set(response.json()) == {"detail"}
            assert "SYNTHETIC_SECRET" not in response.text
            assert "sqlite" not in response.text
            assert "private" not in response.text
            assert "scope" not in response.text

    asyncio.run(run())


class GuideProviders(Store):
    """Use the real Runtime with only its network provider replaced by a fixture."""

    def __init__(self, web):
        super().__init__()
        self.web = web

    def web_tools(self):
        return self.web


@asynccontextmanager
async def live_guide_api(tmp_path, web):
    runtime = SessionRuntime(initialize_data_root(tmp_path / "live-guide-api"), GuideProviders(web))
    try:
        async with api_client(runtime) as client:
            yield client, runtime
    finally:
        await runtime.close()


async def settled_guide_operation(client, request_id):
    for _ in range(200):
        response = await client.get(f"/api/guide-operations/{request_id}")
        assert response.status_code == 200, response.text
        operation = response.json()
        if operation["status"] != "running":
            return operation
        await asyncio.sleep(0.005)
    raise AssertionError(f"guide operation {request_id} did not settle")


async def live_request(client, method, path, *, value=None, status=200):
    response = await client.request(method, path, **({"json": value} if value is not None else {}))
    assert response.status_code == status, response.text
    return response.json()


async def fresh_guide_controls(client, **fields):
    from uuid import uuid4

    listing = await live_request(client, "GET", "/api/guides")
    return {"request_id": uuid4().hex, "expected_revision": listing["revision"], **fields}


async def import_live_guide(client, url, **metadata):
    value = await fresh_guide_controls(client, url=url, **metadata)
    accepted = await live_request(client, "POST", "/api/guides", value=value, status=202)
    assert accepted["request_id"] == value["request_id"]
    assert accepted["status"] in {"running", "completed"}
    operation = await settled_guide_operation(client, value["request_id"])
    assert operation["status"] == "completed", operation
    assert operation["error"] is None
    assert operation["result"]["saved"] is True
    return value, operation


def test_real_http_import_adopt_refresh_keeps_version_unselect_delete_and_replay(tmp_path):
    from test_chat import ScriptWeb, result
    from test_guides import page

    url = "https://example.com/http-guide"
    first_body = "合成旧版攻略：第七轮攒到五十金币再升级。"
    refreshed_body = "合成更新攻略：第六轮先购买护盾再升级。"
    web = ScriptWeb([result(page(first_body, url=url)), result(page(refreshed_body, url=url))])

    async def run():
        async with live_guide_api(tmp_path, web) as (client, runtime):
            dimensions = {"game": "合成星棋", "platform": "Windows", "mode": "排位"}
            value, operation = await import_live_guide(
                client, url, **dimensions, game_version="1.2", version_basis="正文注明版本1.2"
            )
            guide = operation["result"]["guide_id"]
            original_version = operation["result"]["revision_id"]
            repeated = await live_request(client, "POST", "/api/guides", value=value, status=202)
            assert repeated == operation
            assert len(web.calls) == 1
            detail = await live_request(client, "GET", f"/api/guides/{guide}")
            assert detail["guide"]["text"] == first_body
            adoption = await fresh_guide_controls(
                client, **dimensions, guide_id=guide, revision_id=original_version
            )
            adopted = await live_request(client, "PUT", "/api/guide-selection", value=adoption)
            assert adopted["selection"]["revision_id"] == original_version
            assert adopted["replayed"] is False
            repeated_adoption = await live_request(
                client, "PUT", "/api/guide-selection", value=adoption
            )
            assert repeated_adoption["replayed"] is True
            assert repeated_adoption["revision"] == adopted["revision"]
            assert repeated_adoption["selection"] == adopted["selection"]

            refresh = await fresh_guide_controls(client)
            accepted = await live_request(
                client, "POST", f"/api/guides/{guide}/refresh", value=refresh, status=202
            )
            assert accepted["request_id"] == refresh["request_id"]
            refreshed = await settled_guide_operation(client, refresh["request_id"])
            assert refreshed["status"] == "completed", refreshed
            assert refreshed["result"]["guide_id"] == guide
            assert refreshed["result"]["revision_id"] != original_version
            listing = await live_request(client, "GET", "/api/guides")
            assert listing["selections"] == [adopted["selection"]]
            # Public-page ingestion creates a content revision without changing
            # the control generation or silently adopting that new revision.
            assert listing["revision"] == adopted["revision"]
            new_detail = await live_request(client, "GET", f"/api/guides/{guide}")
            old_detail = await live_request(
                client, "GET", f"/api/guides/{guide}?revision_id={original_version}"
            )
            assert new_detail["guide"]["text"] == refreshed_body
            assert new_detail["guide"]["game_version"] is None
            assert old_detail["guide"]["text"] == first_body
            assert old_detail["guide"]["game_version"] == "1.2"
            assert len(web.calls) == 2

            from uuid import uuid4

            stale = await client.put(
                "/api/guide-selection", json={**adoption, "request_id": uuid4().hex}
            )
            assert stale.status_code == 409
            cleared = await live_request(
                client,
                "PUT",
                "/api/guide-selection",
                value=await fresh_guide_controls(
                    client, **dimensions, guide_id=None, revision_id=None
                ),
            )
            assert cleared["selection"] is None
            listing = await live_request(client, "GET", "/api/guides")
            assert listing["selections"] == [] and len(listing["guides"]) == 1
            delete_value = await fresh_guide_controls(client, confirm=True)
            deleted = await live_request(
                client, "DELETE", f"/api/guides/{guide}", value=delete_value
            )
            assert deleted["guide_ids"] == [guide]
            assert set(deleted["revision_ids"]) == {
                original_version,
                refreshed["result"]["revision_id"],
            }
            repeated_delete = await live_request(
                client, "DELETE", f"/api/guides/{guide}", value=delete_value
            )
            assert repeated_delete["replayed"] is True
            assert repeated_delete["revision"] == deleted["revision"]
            listing = await live_request(client, "GET", "/api/guides")
            assert listing["guides"] == listing["selections"] == []
            assert (await client.get(f"/api/guides/{guide}")).status_code == 404
            removed_operation = await settled_guide_operation(client, value["request_id"])
            assert removed_operation["result"] is None
            assert removed_operation["status"] == "cancelled"
            assert removed_operation["error"] == "guide_removed"
            changed_retry = await client.post(
                "/api/guides", json={**value, "url": "https://example.com/different"}
            )
            assert changed_retry.status_code == 409
            assert len(web.calls) == 2
            for response in (
                operation,
                adopted,
                listing,
                new_detail,
                old_detail,
                removed_operation,
            ):
                assert str(runtime.paths.root) not in json.dumps(response)
                assert "scope" not in response

    asyncio.run(run())


def test_real_http_cancel_before_start_tombstones_request_without_fetch(tmp_path):
    from uuid import uuid4

    from test_chat import ScriptWeb

    web = ScriptWeb([])

    async def run():
        async with live_guide_api(tmp_path, web) as (client, _):
            value = await fresh_guide_controls(client, url="https://example.com/cancelled")
            path = f"/api/guide-operations/{value['request_id']}"
            cancelled = await live_request(client, "POST", path + "/cancel", value={})
            assert cancelled["request_id"] == value["request_id"]
            assert cancelled["status"] == "cancelled"
            assert cancelled["result"] is None
            assert await live_request(client, "POST", path + "/cancel", value={}) == cancelled
            assert (
                await live_request(client, "POST", "/api/guides", value=value, status=202)
                == cancelled
            )
            assert await live_request(client, "GET", path) == cancelled
            assert (await client.get(f"/api/guide-operations/{uuid4().hex}")).status_code == 404
            assert web.calls == []
            assert (await live_request(client, "GET", "/api/guides"))["guides"] == []

    asyncio.run(run())


def test_real_http_inflight_retry_and_cancellation_share_one_job(tmp_path):
    from test_chat import result
    from test_guides import page

    async def run():
        started, stopped, release = (asyncio.Event() for _ in range(3))

        class SlowWeb:
            calls = 0

            async def execute(self, name, arguments):
                assert name == "read_web_page"
                self.calls += 1
                started.set()
                try:
                    await release.wait()
                    return result(page(url=arguments["url"]))
                finally:
                    stopped.set()

        web = SlowWeb()
        async with live_guide_api(tmp_path, web) as (client, _):
            value = await fresh_guide_controls(client, url="https://example.com/inflight")
            accepted = await live_request(client, "POST", "/api/guides", value=value, status=202)
            await asyncio.wait_for(started.wait(), 2)
            assert accepted["status"] == "running"
            assert (
                await live_request(client, "POST", "/api/guides", value=value, status=202)
                == accepted
            )
            path = f"/api/guide-operations/{value['request_id']}"
            assert (await live_request(client, "GET", path))["status"] == "running"
            cancelled = await live_request(client, "POST", path + "/cancel", value={})
            assert cancelled["status"] == "cancelled"
            await asyncio.wait_for(stopped.wait(), 2)
            release.set()
            assert await settled_guide_operation(client, value["request_id"]) == cancelled
            assert web.calls == 1
            assert (await live_request(client, "GET", "/api/guides"))["guides"] == []

    asyncio.run(run())


def test_real_http_unreadable_import_persists_safe_terminal_error(tmp_path):
    from test_chat import ScriptWeb, result, source

    web = ScriptWeb([result(source("blocked"), status="error", error="SYNTHETIC_SECRET")])

    async def run():
        async with live_guide_api(tmp_path, web) as (client, _):
            value = await fresh_guide_controls(client, url="https://example.com/unreadable")
            await live_request(client, "POST", "/api/guides", value=value, status=202)
            operation = await settled_guide_operation(client, value["request_id"])
            assert operation["status"] == "error"
            assert operation["result"] is None
            assert operation["error"] == "guide_fetch_failed"
            assert "SYNTHETIC_SECRET" not in json.dumps(operation)
            assert (
                await live_request(client, "POST", "/api/guides", value=value, status=202)
                == operation
            )
            assert len(web.calls) == 1
            assert (await live_request(client, "GET", "/api/guides"))["guides"] == []

    asyncio.run(run())


def test_real_http_guide_snapshot_restore_retains_personal_memory_and_pinned_selection(tmp_path):
    from uuid import uuid4

    from test_chat import ScriptWeb, result
    from test_guides import page

    url_a, url_b = "https://example.com/backup-a", "https://example.com/backup-b"
    web = ScriptWeb(
        [
            result(page("合成备份攻略A：先升级护盾。", url=url_a)),
            result(page("合成后来攻略B：先购买增益。", url=url_b)),
        ]
    )

    async def run():
        async with live_guide_api(tmp_path, web) as (client, runtime):
            memory = await live_request(
                client, "POST", "/api/memories", value={"content": "喜欢合成柠檬汽水"}, status=201
            )
            dimensions = {"game": "合成星棋", "platform": "Windows", "mode": "排位"}
            _, first = await import_live_guide(client, url_a, **dimensions)
            first_ids = {key: first["result"][key] for key in ("guide_id", "revision_id")}
            await live_request(
                client,
                "PUT",
                "/api/guide-selection",
                value=await fresh_guide_controls(client, **dimensions, **first_ids),
            )
            backup_value = {"request_id": uuid4().hex}
            backup = await live_request(
                client, "POST", "/api/guide-backups", value=backup_value, status=201
            )
            assert backup["documents"] == backup["revisions"] == backup["selections"] == 1
            repeated = await live_request(
                client, "POST", "/api/guide-backups", value=backup_value, status=201
            )
            assert repeated["backup_id"] == backup["backup_id"]
            assert repeated["revision"] == backup["revision"]
            assert repeated["replayed"] is True
            backups = await live_request(client, "GET", "/api/guide-backups")
            assert [item["backup_id"] for item in backups["backups"]] == [backup["backup_id"]]
            assert str(runtime.paths.root) not in json.dumps(backups)
            _, second = await import_live_guide(client, url_b, **dimensions)
            second_ids = {key: second["result"][key] for key in ("guide_id", "revision_id")}
            await live_request(
                client,
                "PUT",
                "/api/guide-selection",
                value=await fresh_guide_controls(client, **dimensions, **second_ids),
            )
            restore_value = await fresh_guide_controls(client, confirm=True)
            backup_path = "/api/guide-backups/" + backup["backup_id"]
            restored = await live_request(
                client, "POST", backup_path + "/restore", value=restore_value
            )
            assert restored["backup_id"] == backup["backup_id"]
            assert restored["revision"] > restore_value["expected_revision"]
            listing = await live_request(client, "GET", "/api/guides")
            assert [item["guide_id"] for item in listing["guides"]] == [first_ids["guide_id"]]
            assert len(listing["selections"]) == 1
            assert listing["selections"][0]["guide_id"] == first_ids["guide_id"]
            assert listing["selections"][0]["revision_id"] == first_ids["revision_id"]
            assert (await client.get("/api/guides/" + second_ids["guide_id"])).status_code == 404
            assert [
                item["id"]
                for item in (await live_request(client, "GET", "/api/memories"))["memories"]
            ] == [memory["id"]]
            replay = await live_request(
                client, "POST", backup_path + "/restore", value=restore_value
            )
            assert replay["replayed"] is True
            assert replay["revision"] == restored["revision"]
            assert await live_request(client, "DELETE", backup_path) == {"deleted": True}
            assert (await live_request(client, "GET", "/api/guide-backups"))["backups"] == []
            assert (await client.delete(backup_path)).status_code == 404
            deleted_backup_replay = await live_request(
                client, "POST", "/api/guide-backups", value=backup_value, status=201
            )
            assert deleted_backup_replay["replayed"] is True
            assert (await live_request(client, "GET", "/api/guide-backups"))["backups"] == []

    asyncio.run(run())


def test_real_http_provider_configuration_failure_never_strands_running_operation(tmp_path):
    from ai_neko.providers import ProviderError

    async def run():
        async with live_guide_api(tmp_path, None) as (client, runtime):

            def unavailable():
                raise ProviderError("synthetic_missing_config", "合成服务尚未配置。")

            runtime.providers.web_tools = unavailable
            value = await fresh_guide_controls(client, url="https://example.com/config-failure")
            response = await client.post("/api/guides", json=value)
            assert response.status_code in {400, 202}
            operation = await client.get(f"/api/guide-operations/{value['request_id']}")
            assert operation.status_code in {200, 404}
            if operation.status_code == 200:
                assert operation.json()["status"] != "running", operation.json()
            assert (await live_request(client, "GET", "/api/guides"))["guides"] == []

    asyncio.run(run())
