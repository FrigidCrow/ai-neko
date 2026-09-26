"""Versioned, transactional SQLite schema migrations.

``PRAGMA user_version`` is the version ledger: it lives in the database header
and is updated inside the same transaction as the schema change, so an
interrupted migration rolls back cleanly and resumes on the next open.
Version 1 is the baseline for installs that predate the framework (their
user_version is 0 while tables already exist); every step callable must be
idempotent (guard with ``PRAGMA table_info``) so a partially applied step is
safe to re-run.

Before the first pending step runs, a caller-supplied backup path receives a
SQLite online backup. The backup survives a failed migration for manual
recovery; successful migrations leave it in place (it is tiny and one-time
per database generation).
"""

from __future__ import annotations

import os
import sqlite3
import tempfile
from pathlib import Path
from typing import Callable

Step = Callable[[sqlite3.Connection], None]


class MigrationError(RuntimeError):
    """A schema step failed; the transaction rolled back and the backup remains."""


def schema_version(db: sqlite3.Connection) -> int:
    return db.execute("PRAGMA user_version").fetchone()[0]


def _write_backup(db: sqlite3.Connection, destination: Path) -> None:
    # Online backup into a fresh 0600 file, then atomically move it into place.
    fd, temporary = tempfile.mkstemp(prefix=".migration-", suffix=".sqlite", dir=destination.parent)
    os.close(fd)
    try:
        with sqlite3.connect(temporary) as target:
            db.backup(target)
        os.replace(temporary, destination)
    finally:
        Path(temporary).unlink(missing_ok=True)


def apply_migrations(
    db: sqlite3.Connection,
    *,
    steps: list[tuple[int, Step]],
    backup_path: Path | None = None,
) -> int:
    """Apply pending steps and return the resulting schema version.

    ``steps`` is ordered ``(version, callable)``; version N upgrades an N-1
    database to N. Versions must be strictly increasing and start above the
    baseline (1). A failed step rolls back its transaction only, so already
    committed steps stay applied and the next open resumes from there.
    """
    if not steps:
        raise MigrationError("migration_steps_empty")
    versions = [version for version, _ in steps]
    if versions != sorted(set(versions)) or versions[0] <= 1:
        raise MigrationError("migration_steps_invalid")
    current = schema_version(db)
    latest = versions[-1]
    if current == 0:
        # 0 means "no ledger": either a pre-framework install or a fresh
        # database whose CREATE TABLE script already produced the latest
        # shape. Idempotent steps make both safe.
        current = 1
    if current >= latest:
        return current
    if backup_path is not None:
        backup_path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
        _write_backup(db, backup_path)
    for version, step in steps:
        if version <= current:
            continue
        # The connection may run in autocommit mode (isolation_level=None),
        # so drive the transaction explicitly instead of ``with db:``.
        db.execute("BEGIN IMMEDIATE")
        try:
            step(db)
            db.execute(f"PRAGMA user_version={int(version)}")
            db.commit()
        except BaseException as exc:
            db.rollback()
            raise MigrationError(f"migration_to_{version}_failed") from exc
    return latest
