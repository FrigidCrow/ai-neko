"""Application-owned paths; no fallback, migration, or import-time filesystem access."""

from __future__ import annotations

import json
import os
import stat
import sys
from dataclasses import dataclass
from pathlib import Path

from ai_neko import APP_ID

MARKER = ".ai-neko.json"
SUBDIRECTORIES = ("config", "memory", "checkpoints", "logs", "backups", "runtime", "assets")


class DataRootError(ValueError):
    """The supplied location cannot safely serve as an ai-neko data root."""


@dataclass(frozen=True)
class DataPaths:
    root: Path
    config: Path
    memory: Path
    checkpoints: Path
    logs: Path
    backups: Path
    runtime: Path
    assets: Path


def default_data_root() -> Path:
    if sys.platform == "win32":
        local = os.environ.get("LOCALAPPDATA")
        if not local or not Path(local).is_absolute():
            raise DataRootError("LOCALAPPDATA must be an absolute directory on Windows")
        return Path(local) / APP_ID
    if sys.platform == "darwin":
        return Path.home() / "Library" / "Application Support" / APP_ID
    return Path.home() / ".local" / "share" / APP_ID


def resolve_data_root(data_dir: str | Path | None = None) -> Path:
    raw = data_dir if data_dir is not None else os.environ.get("AI_NEKO_DATA_DIR")
    if raw is not None and not str(raw).strip():
        raise DataRootError("data root cannot be empty")
    requested = Path(raw).expanduser() if raw is not None else default_data_root()
    if not requested.is_absolute():
        raise DataRootError("data root must be absolute")
    try:
        if is_redirected(requested):
            raise DataRootError("data root cannot be a symlink or reparse point")
        root = requested.resolve()
        forbidden = {"n.e.k.o", ".neko", "neko-companion"}
        if any(part.casefold() in forbidden for part in root.parts + requested.parts):
            raise DataRootError(
                "reference project and original application directories are forbidden"
            )
        if root == Path(root.anchor) or root == Path.home().resolve():
            raise DataRootError("data root must be a dedicated application directory")
        if root.exists() and not root.is_dir():
            raise DataRootError("data root is not a directory")
    except (OSError, RuntimeError, ValueError) as exc:
        if isinstance(exc, DataRootError):
            raise
        raise DataRootError("data root cannot be resolved") from exc
    return root


def is_redirected(path: Path) -> bool:
    """Detect symlinks and Windows junction/reparse points on Python 3.11."""
    try:
        info = path.lstat()
    except FileNotFoundError:
        return False
    return stat.S_ISLNK(info.st_mode) or bool(getattr(info, "st_file_attributes", 0) & 0x400)


def safe_child(parent: Path, name: str) -> Path:
    """Refuse known redirects, shared hardlinks, and special files before writes.

    This guards accidental adoption of pre-existing foreign state, not a hostile
    same-user process replacing paths concurrently after validation.
    """
    if Path(name).name != name or name in {".", ".."}:
        raise DataRootError("managed child must be a single path component")
    child = parent / name
    if is_redirected(child):
        raise DataRootError(f"managed path cannot be a symlink or reparse point: {name}")
    try:
        info = child.stat()
    except FileNotFoundError:
        return child
    if stat.S_ISREG(info.st_mode):
        if info.st_nlink > 1:
            raise DataRootError(f"managed file cannot have multiple hardlinks: {name}")
    elif not stat.S_ISDIR(info.st_mode):
        raise DataRootError(f"managed path must be a regular file or directory: {name}")
    return child


def initialize_data_root(data_dir: str | Path | None = None) -> DataPaths:
    root = resolve_data_root(data_dir)
    try:
        root.mkdir(mode=0o700, parents=True, exist_ok=True)
        marker = safe_child(root, MARKER)
        if not marker.exists():
            if any(root.iterdir()):
                raise DataRootError("nonempty data root has no ai-neko ownership marker")
            with marker.open("x", encoding="utf-8") as handle:
                json.dump({"app_id": APP_ID, "schema_version": 1}, handle)
                handle.write("\n")
            marker.chmod(0o600)
        owner = json.loads(marker.read_text(encoding="utf-8"))
        if (
            not isinstance(owner, dict)
            or owner != {"app_id": APP_ID, "schema_version": 1}
            or type(owner.get("schema_version")) is not int
        ):
            raise DataRootError("data root belongs to another application or schema version")
        children = {name: safe_child(root, name) for name in SUBDIRECTORIES}
        for child in children.values():
            if child.exists() and not child.is_dir():
                raise DataRootError(f"managed directory is not a directory: {child.name}")
        for child in children.values():
            child.mkdir(mode=0o700, exist_ok=True)
        return DataPaths(root=root, **children)
    except (OSError, ValueError) as exc:
        if isinstance(exc, DataRootError):
            raise
        raise DataRootError("cannot initialize application data root") from exc
