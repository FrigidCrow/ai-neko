"""Upgrade actual v0.4-produced files; never manufacture an old PRAGMA version.

The frozen source archive and database bytes make the tests independent of Git
history, network, user configuration, and the reference project.
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import os
import shutil
import sqlite3
import subprocess
import sys
import zipfile
from contextlib import closing
from pathlib import Path
from uuid import uuid4

import pytest
from langgraph.checkpoint.sqlite.aio import AsyncSqliteSaver

from ai_neko.config.paths import initialize_data_root
from ai_neko.config.schema import MigrationError
from ai_neko.memory.guides import GuideAccessError, GuideConflictError
from ai_neko.runtime import SessionRuntime
from ai_neko.tools.web import source

ROOT = Path(__file__).resolve().parents[1]
FIXTURE = ROOT / "tests" / "fixtures" / "v04"
COMMIT = "f359826bd60139a6a9efcef8d959f56c385228ba"
MANIFEST = json.loads((FIXTURE / "manifest.json").read_text(encoding="utf-8"))
EXPECTED = json.loads((FIXTURE / "expected.json").read_text(encoding="utf-8"))
MARKER = "V04_SYNTHETIC_ORCHID"


def digest(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def read_database(path):
    # immutable prevents even empty -wal/-shm files beside frozen fixture inputs.
    return closing(sqlite3.connect(path.resolve().as_uri() + "?mode=ro&immutable=1", uri=True))


def copy_fixture(destination):
    for relative, evidence in MANIFEST["files"].items():
        original = FIXTURE / relative
        assert digest(original) == evidence["sha256"], relative
        target = destination / Path(relative).relative_to("data")
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(original, target)
    return initialize_data_root(destination)


def child_environment(work):
    home = work / "isolated-home"
    home.mkdir(exist_ok=True)
    return {
        **{
            key: os.environ[key]
            for key in ("PATH", "SYSTEMROOT", "TEMP", "TMP")
            if key in os.environ
        },
        "HOME": str(home),
        "USERPROFILE": str(home),
        "LOCALAPPDATA": str(home / "AppData" / "Local"),
        "PYTHONUTF8": "1",
    }


def legacy_verify(paths, work):
    archive = FIXTURE / "legacy-src.zip"
    assert digest(archive) == MANIFEST["source_archive_sha256"]
    old = work / "old-source"
    with zipfile.ZipFile(archive) as zipped:
        assert set(zipped.namelist()) == set(MANIFEST["source_files"])
        for name, expected_hash in MANIFEST["source_files"].items():
            assert name.startswith("src/") and ".." not in Path(name).parts
            payload = zipped.read(name)
            assert hashlib.sha256(payload).hexdigest() == expected_hash
            target = old / name
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(payload)
    # Preserve the frozen driver bytes. Windows needs its local wakeup socket
    # before the driver's strict network guard, but old application imports and
    # every application operation still occur only after that guard is installed.
    bootstrap = """
import argparse, asyncio, runpy, sys
from pathlib import Path
driver = runpy.run_path(sys.argv[1])
args = argparse.Namespace(mode="verify", source=Path(sys.argv[2]),
                          data=Path(sys.argv[3]), expected=Path(sys.argv[4]))
assert not any(name == "ai_neko" or name.startswith("ai_neko.") for name in sys.modules)
with asyncio.Runner() as runner:
    runner.get_loop()
    sys.addaudithook(driver["deny_network"])
    for event in ("socket.connect", "socket.getaddrinfo", "socket.gethostbyname"):
        try:
            sys.audit(event)
        except AssertionError:
            pass
        else:
            raise AssertionError("legacy network audit guard was not installed")
    runner.run(driver["run"](args))
"""
    result = subprocess.run(
        [
            sys.executable,
            "-I",
            "-X",
            "utf8",
            "-c",
            bootstrap,
            str(FIXTURE / "legacy_driver.py"),
            str(old / "src"),
            str(paths.root),
            str(FIXTURE / "expected.json"),
        ],
        cwd=work,
        env=child_environment(work),
        capture_output=True,
        text=True,
        encoding="utf-8",
        timeout=30,
    )
    assert result.returncode == 0, result.stderr
    assert json.loads(result.stdout) == {
        "old_source_imported": True,
        "mode": "verify",
        "network_calls": 0,
    }


class Providers:
    def __init__(self):
        self.messages = []

    def model(self):
        return self

    def web_tools(self):
        raise AssertionError("upgrade acceptance never uses network")

    async def stream(self, messages, tools=None):
        self.messages.append(messages)
        yield {"type": "text", "text": "候选版合成回复。"}


def assert_preserved(runtime):
    assert runtime.memory.get_persona() == EXPECTED["persona"]
    assert runtime.memory.list_facts() == EXPECTED["facts"]
    for old in EXPECTED["sessions"]:
        current = runtime.get_session(old["id"])
        assert current["id"] == old["id"] and current["title"] == old["title"]
        assert len(current["turns"]) == len(old["turns"])
        for before, after in zip(old["turns"], current["turns"], strict=True):
            for field in (
                "id",
                "input",
                "status",
                "created_at",
                "settled_at",
                "confirmed_text",
                "delivered_text",
                "heard_text",
                "audio_playback",
                "ack_seq",
                "sent_seq",
            ):
                assert after[field] == before[field], field


async def candidate_probe(root):
    paths = initialize_data_root(root)
    providers = Providers()
    runtime = SessionRuntime(paths, providers)
    try:
        assert_preserved(runtime)
        assert runtime._db.execute("PRAGMA user_version").fetchone()[0] == 6
        assert runtime.memory._db.execute("PRAGMA user_version").fetchone()[0] == 2
        assert providers.messages == []  # No generation or audio replay on upgrade.
        async with AsyncSqliteSaver.from_conn_string(
            str(paths.checkpoints / "chat-graph.sqlite")
        ) as saver:
            for old in EXPECTED["sessions"]:
                internal = runtime._db.execute(
                    "SELECT internal_id FROM sessions WHERE id=?", (old["id"],)
                ).fetchone()[0]
                for turn in old["turns"]:
                    checkpoint = await saver.aget_tuple(
                        {"configurable": {"thread_id": internal + ":" + turn["id"]}}
                    )
                    assert (
                        checkpoint.checkpoint["channel_values"]["output"] == turn["confirmed_text"]
                    )
        # A new accepted request must actually receive the old persona and fact.
        sid = runtime.create_session()["id"]
        turn = await runtime.start_turn(sid, "青柚茶和 V04_SYNTHETIC_ORCHID 是什么偏好？")
        await asyncio.wait_for(asyncio.shield(runtime._tasks[turn["id"]]), 10)
        delivered = runtime.events(sid, turn["id"])
        assert delivered["status"] == "completed"
        runtime.ack(sid, turn["id"], delivered["last_seq"])
        actual = json.dumps(providers.messages, ensure_ascii=False)
        assert EXPECTED["persona"]["name"] in actual
        assert EXPECTED["erased_fact"]["content"] in actual
        assert len(providers.messages) == 1
        assert not list(paths.backups.glob("*-pre-migration-*.sqlite"))
    finally:
        await runtime.close()


def test_frozen_v04_provenance_ddl_hashes_and_actual_legacy_reader(tmp_path):
    assert MANIFEST["source_commit"] == COMMIT
    assert MANIFEST["source_tag"] == "v0.4.0-alpha.1"
    assert MANIFEST["synthetic_only"] is True
    assert MANIFEST["real_model_calls"] == MANIFEST["network_calls"] == 0
    assert digest(FIXTURE / "legacy_driver.py") == MANIFEST["driver_sha256"]
    assert digest(FIXTURE / "expected.json") == MANIFEST["expected_sha256"]
    for relative, evidence in MANIFEST["files"].items():
        path = FIXTURE / relative
        assert path.stat().st_size == evidence["bytes"]
        assert digest(path) == evidence["sha256"]
        if "database" not in evidence:
            continue
        with read_database(path) as database:
            assert database.execute("PRAGMA user_version").fetchone()[0] == 0
            assert database.execute("PRAGMA integrity_check").fetchone()[0] == "ok"
            ddl = database.execute(
                "SELECT type,name,tbl_name,sql FROM sqlite_master "
                "WHERE sql IS NOT NULL ORDER BY type,name"
            ).fetchall()
            assert json.loads(json.dumps(ddl)) == evidence["database"]["ddl"]
            assert (
                hashlib.sha256(json.dumps(ddl, ensure_ascii=False).encode()).hexdigest()
                == evidence["database"]["ddl_sha256"]
            )
    paths = copy_fixture(tmp_path / "legacy readable")
    legacy_verify(paths, tmp_path)


def test_current_new_process_upgrades_real_v04_and_uses_preserved_evidence(tmp_path):
    paths = copy_fixture(tmp_path / "真实旧库 upgrade")
    script = """
import asyncio,sys
sys.path[:0]=[sys.argv[1],sys.argv[2]]
from test_v04_upgrade import candidate_probe
asyncio.run(candidate_probe(sys.argv[3]))
print('candidate-upgrade-preserved-and-injected')
"""
    result = subprocess.run(
        [
            sys.executable,
            "-I",
            "-X",
            "utf8",
            "-c",
            script,
            str(ROOT / "src"),
            str(ROOT / "tests"),
            str(paths.root),
        ],
        cwd=tmp_path,
        env=child_environment(tmp_path),
        capture_output=True,
        text=True,
        encoding="utf-8",
        timeout=30,
    )
    assert result.returncode == 0, result.stderr
    assert result.stdout.strip() == "candidate-upgrade-preserved-and-injected"


def guide_page():
    return source(
        "https://example.com/v04-upgrade-guide",
        status="read",
        title="新版合成攻略",
        text="潮灯是放在码头中央的照明建筑。G6_SYNTHETIC_GUIDE_BODY",
    )


async def adopted_guide(runtime):
    document = runtime.memory.guides.ingest(
        guide_page(), game="synthetic-v04", platform="pc", mode="notes", game_version="1.0"
    )
    selected = await runtime.guide_selection(
        {
            "request_id": uuid4().hex,
            "expected_revision": runtime.memory.guides.revision(),
            "game": "synthetic-v04",
            "platform": "pc",
            "mode": "notes",
            "guide_id": document["guide_id"],
            "revision_id": document["revision_id"],
        }
    )
    return document, selected


def test_old_and_new_memory_snapshots_preserve_new_guide_and_never_resurrect_deletion(tmp_path):
    async def run():
        paths = copy_fixture(tmp_path / "snapshots")
        runtime = SessionRuntime(paths, Providers())
        try:
            document, _ = await adopted_guide(runtime)
            selection = runtime.memory.guides.control_snapshot()
            new_snapshot = await runtime.backup_memory()
            for backup_id in (EXPECTED["snapshot"]["id"], new_snapshot["id"]):
                transient = runtime.memory.remember(
                    "合成快照之外的新事实 G6_TRANSIENT", source_id="manual:" + uuid4().hex
                )
                assert transient["id"] in {item["id"] for item in runtime.memory.list_facts()}
                await runtime.restore_memory(backup_id, runtime.memory.revision())
                assert runtime.memory.guides.control_snapshot() == selection
                assert (
                    runtime.memory.guides.get_document(document["guide_id"])["text"]
                    == guide_page()["text"]
                )
                assert runtime.memory.list_facts() == EXPECTED["facts"]
            await runtime.guide_delete(
                document["guide_id"],
                {
                    "request_id": uuid4().hex,
                    "expected_revision": runtime.memory.guides.revision(),
                    "confirm": True,
                },
            )
            deleted_revision = runtime.memory.guides.revision()
            for backup_id in (EXPECTED["snapshot"]["id"], new_snapshot["id"]):
                await runtime.restore_memory(backup_id, runtime.memory.revision())
                assert runtime.memory.guides.revision() == deleted_revision
                assert runtime.memory.guides.control_snapshot()["selections"] == []
                with pytest.raises(GuideAccessError):
                    runtime.memory.guides.get_document(document["guide_id"])
                with pytest.raises(GuideConflictError):
                    runtime.memory.guides.ingest(guide_page())
        finally:
            await runtime.close()
        reopened = SessionRuntime(paths, Providers())
        try:
            assert not reopened.memory.guides.list_documents()
            with pytest.raises(GuideConflictError):
                reopened.memory.guides.ingest(guide_page())
        finally:
            await reopened.close()

    asyncio.run(run())


@pytest.mark.parametrize("failure", ["conversation", "memory"])
def test_actual_v04_migration_failure_retains_old_program_recovery_and_retries(
    tmp_path, monkeypatch, failure
):
    import ai_neko.memory.service as memory_module
    import ai_neko.runtime.service as runtime_module

    paths = copy_fixture(tmp_path / "migration interrupted")
    target = runtime_module if failure == "conversation" else memory_module
    version = 5 if failure == "conversation" else 2

    def fail_transaction(database):
        database.execute("CREATE TABLE synthetic_should_rollback(value TEXT)")
        raise OSError("synthetic migration interruption")

    steps = [
        (number, fail_transaction if number == version else step)
        for number, step in target._MIGRATIONS
    ]
    with monkeypatch.context() as patch:
        patch.setattr(target, "_MIGRATIONS", steps)
        with pytest.raises(MigrationError, match=f"migration_to_{version}_failed"):
            SessionRuntime(paths, Providers())
    recovery = copy_fixture(tmp_path / "old program recovery")
    backup = paths.backups / (
        "conversation-pre-migration-v6.sqlite"
        if failure == "conversation"
        else "long-term-pre-migration-v2.sqlite"
    )
    assert backup.is_file()
    database_name = "conversation.sqlite" if failure == "conversation" else "long-term.sqlite"
    with read_database(backup) as database:
        assert database.execute("PRAGMA user_version").fetchone()[0] == 0
        assert not database.execute(
            "SELECT 1 FROM sqlite_master WHERE name='synthetic_should_rollback'"
        ).fetchone()
    shutil.copyfile(backup, recovery.memory / database_name)
    legacy_verify(recovery, tmp_path)
    with read_database(paths.memory / database_name) as database:
        assert not database.execute(
            "SELECT 1 FROM sqlite_master WHERE name='synthetic_should_rollback'"
        ).fetchone()
    asyncio.run(candidate_probe(paths.root))


def test_upgraded_legacy_erasure_retries_cleanup_and_old_snapshot_cannot_revive(
    tmp_path, monkeypatch
):
    async def run():
        paths = copy_fixture(tmp_path / "erasure interrupted")
        runtime = SessionRuntime(paths, Providers())
        fact = EXPECTED["erased_fact"]
        original = runtime.memory.forget_source
        failed = False

        def committed_then_lost(source_id, *args, **kwargs):
            nonlocal failed
            result = original(source_id, *args, **kwargs)
            if source_id in fact["source_ids"] and not failed:
                failed = True
                raise OSError("synthetic post-commit cleanup interruption")
            return result

        try:
            with monkeypatch.context() as patch:
                patch.setattr(runtime.memory, "forget_source", committed_then_lost)
                with pytest.raises(OSError, match="post-commit cleanup"):
                    await runtime.forget_memory(fact["id"])
            assert failed
            assert fact["id"] not in {item["id"] for item in runtime.memory.list_facts()}
            intent = runtime._db.execute("SELECT payload FROM memory_erasure").fetchone()[0]
            assert fact["id"] in intent and MARKER not in intent
        finally:
            await runtime.close()
        runtime = SessionRuntime(paths, Providers())
        try:
            assert not runtime._db.execute("SELECT 1 FROM memory_erasure").fetchone()
            await runtime.restore_memory(EXPECTED["snapshot"]["id"], runtime.memory.revision())
            assert [item["id"] for item in runtime.memory.list_facts()] == [
                EXPECTED["preserved_fact"]["id"]
            ]
            history = runtime.get_session(EXPECTED["session_id"])
            assert history["turns"][0]["input"] == "[已遗忘的对话]"
            assert all(not turn["confirmed_text"] for turn in history["turns"])
            assert MARKER not in json.dumps(history, ensure_ascii=False)
            independent = runtime.get_session(EXPECTED["independent_session_id"])
            assert (
                independent["turns"][0]["confirmed_text"]
                == EXPECTED["sessions"][1]["turns"][0]["confirmed_text"]
            )
        finally:
            await runtime.close()
        with read_database(paths.checkpoints / "chat-graph.sqlite") as database:
            threads = {row[0] for row in database.execute("SELECT thread_id FROM checkpoints")}
            assert not any(thread.endswith(tuple(EXPECTED["turn_ids"])) for thread in threads)
            assert any(thread.endswith(EXPECTED["independent_turn_id"]) for thread in threads)
        for path in (
            paths.memory / "conversation.sqlite",
            paths.memory / "long-term.sqlite",
            paths.checkpoints / "chat-graph.sqlite",
        ):
            assert MARKER.encode() not in path.read_bytes()

    asyncio.run(run())
