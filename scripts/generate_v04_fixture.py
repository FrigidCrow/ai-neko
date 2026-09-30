"""Freeze synthetic databases produced by the exact local v0.4 release source.

Only fixture regeneration needs the Git tag. Tests consume the frozen files and
source archive without Git, network, credentials, or a reference checkout.
"""

from __future__ import annotations

import argparse
import hashlib
import importlib.metadata
import json
import os
import platform
import shutil
import sqlite3
import subprocess
import sys
import tempfile
import zipfile
from contextlib import closing
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
COMMIT = "f359826bd60139a6a9efcef8d959f56c385228ba"
TAG = "v0.4.0-alpha.1"
FIXTURE = ROOT / "tests" / "fixtures" / "v04"


def sha256(data):
    return hashlib.sha256(data).hexdigest()


def git(*arguments):
    return subprocess.check_output(["git", *arguments], cwd=ROOT)


def child_environment():
    return {
        **{
            key: os.environ[key]
            for key in ("PATH", "SYSTEMROOT", "TEMP", "TMP")
            if key in os.environ
        },
        "PYTHONUTF8": "1",
    }


def database_evidence(path):
    uri = path.resolve().as_uri() + "?mode=ro&immutable=1"
    with closing(sqlite3.connect(uri, uri=True)) as db:
        ddl = db.execute(
            "SELECT type,name,tbl_name,sql FROM sqlite_master "
            "WHERE sql IS NOT NULL ORDER BY type,name"
        ).fetchall()
        counts = {
            name: db.execute('SELECT count(*) FROM "' + name.replace('"', '""') + '"').fetchone()[0]
            for (name,) in db.execute(
                "SELECT name FROM sqlite_master WHERE type='table' ORDER BY name"
            )
        }
        return {
            "user_version": db.execute("PRAGMA user_version").fetchone()[0],
            "integrity_check": db.execute("PRAGMA integrity_check").fetchone()[0],
            "ddl": ddl,
            "ddl_sha256": sha256(json.dumps(ddl, ensure_ascii=False).encode()),
            "rows": counts,
        }


def generate(destination):
    if git("rev-parse", TAG + "^{commit}").decode().strip() != COMMIT:
        raise RuntimeError("local release tag does not identify the frozen source")
    if destination.exists():
        raise ValueError("destination must be new; frozen evidence is never overwritten")
    source_names = git("ls-tree", "-r", "--name-only", COMMIT, "src").decode().splitlines()
    source_hashes = {}
    driver = FIXTURE / "legacy_driver.py"
    with tempfile.TemporaryDirectory(prefix="ai-neko-v04-generator-") as folder:
        staging = Path(folder)
        old = staging / "release"
        archive = staging / "legacy-src.zip"
        with zipfile.ZipFile(archive, "w", compression=zipfile.ZIP_DEFLATED) as zipped:
            for name in source_names:
                data = git("show", COMMIT + ":" + name)
                source_hashes[name] = sha256(data)
                target = old / name
                target.parent.mkdir(parents=True, exist_ok=True)
                target.write_bytes(data)
                info = zipfile.ZipInfo(name, date_time=(2026, 9, 25, 0, 0, 0))
                info.compress_type = zipfile.ZIP_DEFLATED
                zipped.writestr(info, data)
        data_root = staging / "data"
        expected = staging / "expected.json"
        command = [
            sys.executable,
            "-I",
            str(driver),
            "create",
            "--source",
            str(old / "src"),
            "--data",
            str(data_root),
            "--expected",
            str(expected),
        ]
        generated = subprocess.run(
            command,
            cwd=staging,
            env=child_environment(),
            capture_output=True,
            text=True,
            check=True,
            timeout=60,
        )
        # No logs, discovery descriptor, lock, credentials or WAL/journal files
        # are fixture inputs. The old program closes SQLite before these copies.
        retained = [
            path
            for path in data_root.rglob("*")
            if path.is_file() and (path.suffix == ".sqlite" or path.name == ".ai-neko.json")
        ]
        destination.mkdir(parents=True)
        shutil.copy2(archive, destination / archive.name)
        shutil.copy2(expected, destination / expected.name)
        files = {}
        for path in retained:
            relative = Path("data") / path.relative_to(data_root)
            target = destination / relative
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(path, target)
            files[relative.as_posix()] = {
                "sha256": sha256(path.read_bytes()),
                "bytes": path.stat().st_size,
                **({"database": database_evidence(path)} if path.suffix == ".sqlite" else {}),
            }
        manifest = {
            "format": "ai-neko-v04-upgrade-fixture-v1",
            "synthetic_only": True,
            "source_tag": TAG,
            "source_commit": COMMIT,
            "source_files": source_hashes,
            "source_archive_sha256": sha256(archive.read_bytes()),
            "driver_sha256": sha256(driver.read_bytes()),
            "expected_sha256": sha256(expected.read_bytes()),
            "generation": json.loads(generated.stdout),
            "files": files,
            "environment": {
                "python": platform.python_version(),
                "system": platform.platform(),
                "sqlite": sqlite3.sqlite_version,
                "dependencies": {
                    name: importlib.metadata.version(name)
                    for name in ("langgraph", "langgraph-checkpoint-sqlite", "aiosqlite", "httpx")
                },
            },
            "release_lock_sha256": sha256(git("show", COMMIT + ":uv.lock")),
            "network_calls": 0,
            "real_model_calls": 0,
        }
        (destination / "manifest.json").write_text(
            json.dumps(manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
        )
    return manifest


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    result = generate(args.output.resolve())
    print(
        json.dumps(
            {"source_commit": COMMIT, "files": len(result["files"]), "output": str(args.output)}
        )
    )


if __name__ == "__main__":
    main()
