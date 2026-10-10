"""Explicit local registration of the already approved Qwen experiment assets.

Registration hashes existing files; it never installs, downloads, starts Python,
imports native inference libraries, or deserializes the trusted pickle prompt.
Status validates bounded JSON and file stats only. This detects accidental
replacement, not a malicious process with the same user's write permissions.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import stat
import uuid
from importlib.metadata import PathDistribution
from pathlib import Path

from ai_neko import APP_ID
from ai_neko.config.paths import DataPaths, DataRootError, is_redirected, safe_child

OWNER = {"app_id": APP_ID, "component": "qwen-local-voice", "schema_version": 1}
MAX_BINDING_BYTES = 32 * 1024
MODEL_REVISION = "5d83992436eae1d760afd27aff78a71d676296fc"
PROMPT_SHA256 = "c39fe66d150c565e45adb36213bee8c417bbda054fde9d1ca87b871589cf8e5e"
PROMPT_BYTES = 12029
PYTHON_VERSION = "3.11.15"
VERSIONS = {"qwen-tts": "0.1.1", "torch": "2.10.0+cu128"}
# This first adapter reuses these expressly approved installed experiment files.
# A frozen application can read the same registration; it does not bundle them.
APPROVED_PROJECT = Path("D:/dev/ai-neko")
APPROVED_PYTHON_HOME = Path(
    "D:/dev/.ai-neko-tools/20261003-164747/python/cpython-3.11-windows-x86_64-none"
)
APPROVED_PYTHON_TARGET = Path(
    "D:/dev/.ai-neko-tools/20261003-164747/python/cpython-3.11.15-windows-x86_64-none"
)
ENVIRONMENT_RELATIVE = "reports/qwen3-voicedesign-20261004/.venv"
MODEL_RELATIVE = "reports/qwen3-base-06b-20261005/model"
PROMPT_RELATIVE = "reports/qwen3-base-06b-20261005/voice-clone-prompt.pt"
MODEL_FILES = {
    "config.json": (4494, "2e714c787c8edb98b05432685cddb634add2de4d4e645f653d68251ef72ba011"),
    "generation_config.json": (
        245,
        "f1b90b4513f3b34c62851049e2492d7b4c5940daf1276f89c82b8ef04127f3aa",
    ),
    "merges.txt": (1671839, "599bab54075088774b1733fde865d5bd747cbcc7a547c5bc12610e874e26f5e3"),
    "model.safetensors": (
        1829344272,
        "180b3b10eb1c9f1b4db7806d5475bae3071c0243c299d49926bab1da3b6946f6",
    ),
    "preprocessor_config.json": (
        127,
        "efdde1022ea9d76928bf7a9cd53139138f5ba2e466e837f08f6105ab1af1c119",
    ),
    "speech_tokenizer/config.json": (
        2336,
        "ee65bb901c876664ab8707c487157aa1a6ee57c65969b28fb5ec9dc211e68167",
    ),
    "speech_tokenizer/configuration.json": (
        76,
        "6bc26d64eb5024b4d1dab5a52371958b429256d6c9d59787f1f5294a54e0cebd",
    ),
    "speech_tokenizer/model.safetensors": (
        682293092,
        "836b7b357f5ea43e889936a3709af68dfe3751881acefe4ecf0dbd30ba571258",
    ),
    "speech_tokenizer/preprocessor_config.json": (
        234,
        "fcb3805e597e786d4067706e602f6688524640f8d3396790e2e09b5942fcbdfb",
    ),
    "tokenizer_config.json": (
        7344,
        "dc3c31c3bdaedd5016382bb3cbe07323026775ad51f5a4fb564505992ae4a670",
    ),
    "vocab.json": (2776833, "ca10d7e9fb3ed18575dd1e277a2579c16d108e32f27439684afa0e10b1440910"),
}


class BindingError(ValueError):
    """The approved local runtime is missing or its registration is invalid."""


def _local_path(path: Path, *, directory: bool | None = None) -> Path:
    value = str(path)
    if (
        not path.is_absolute()
        or value.startswith(("\\\\", "//"))
        or ".." in path.parts
        or any(part.casefold() in {"n.e.k.o", ".neko", "neko-companion"} for part in path.parts)
    ):
        raise BindingError("Qwen paths must be approved absolute local paths")
    for ancestor in (*reversed(path.parents), path):
        if is_redirected(ancestor):
            raise BindingError("Qwen paths cannot contain symlinks or reparse points")
    if directory is not None:
        info = path.stat()
        expected = stat.S_ISDIR(info.st_mode) if directory else stat.S_ISREG(info.st_mode)
        if not expected:
            raise BindingError("Qwen path has an unexpected file type")
    return path


def _object(pairs):
    result = {}
    for name, value in pairs:
        if name in result:
            raise BindingError("duplicate JSON fields are forbidden")
        result[name] = value
    return result


def _read_json(path: Path, limit: int = MAX_BINDING_BYTES) -> dict:
    _local_path(path, directory=False)
    if path.stat().st_size > limit:
        raise BindingError("Qwen JSON file exceeds its size limit")
    with path.open("rb") as handle:
        data = handle.read(limit + 1)
    if len(data) > limit:
        raise BindingError("Qwen JSON file exceeds its size limit")
    value = json.loads(data.decode("utf-8"), object_pairs_hook=_object)
    if type(value) is not dict:
        raise BindingError("Qwen JSON must be an object")
    return value


def owned_root(paths: DataPaths, create: bool = False) -> Path:
    """Return only an application-owned Qwen root; never adopt foreign files."""
    try:
        _local_path(paths.root, directory=True)
        if paths.assets != paths.root / "assets":
            raise BindingError("Qwen assets must belong to the application data root")
        marker = safe_child(paths.root, ".ai-neko.json")
        owner = _read_json(marker, 1024)
        if (
            owner != {"app_id": APP_ID, "schema_version": 1}
            or type(owner.get("schema_version")) is not int
        ):
            raise BindingError("foreign application data root")
        _local_path(paths.assets, directory=True)
        root = safe_child(paths.assets, "qwen")
        if create:
            root.mkdir(mode=0o700, exist_ok=True)
        _local_path(root, directory=True)
        marker = safe_child(root, ".owner.json")
        if create and not marker.exists():
            if any(root.iterdir()):
                raise BindingError("foreign Qwen directory has no ownership marker")
            fd = os.open(marker, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
            with os.fdopen(fd, "w", encoding="utf-8") as handle:
                json.dump(OWNER, handle)
                handle.write("\n")
        owner = _read_json(marker, 1024)
        if owner != OWNER or type(owner.get("schema_version")) is not int:
            raise BindingError("foreign Qwen ownership marker")
        return root
    except (OSError, ValueError, RecursionError) as exc:
        if isinstance(exc, BindingError):
            raise
        raise BindingError("Qwen owned directory is unavailable or invalid") from exc


def _environment_facts(environment: Path) -> tuple[Path, Path]:
    _local_path(environment, directory=True)
    config = _local_path(environment / "pyvenv.cfg", directory=False)
    if config.stat().st_size > 4096:
        raise BindingError("invalid Qwen virtual environment configuration")
    with config.open("r", encoding="utf-8") as handle:
        text = handle.read(4097)
    fields = {}
    for line in text.splitlines():
        if not line.strip():
            continue
        name, separator, value = line.partition("=")
        name, value = name.strip(), value.strip()
        if not separator or name in fields:
            raise BindingError("invalid Qwen virtual environment configuration")
        fields[name] = value
    if (
        Path(fields.get("home", "")) != APPROVED_PYTHON_HOME
        or fields.get("version_info") != PYTHON_VERSION
        or fields.get("implementation") != "CPython"
        or fields.get("include-system-site-packages", "").casefold() != "false"
    ):
        raise BindingError("Qwen requires the approved isolated Python environment")
    # uv uses this one known version alias junction in the approved installation.
    # Resolve only that alias to its exact pinned-version directory; never allow
    # arbitrary redirects in any registered source or in the executable's path.
    _local_path(APPROVED_PYTHON_HOME.parent, directory=True)
    target = _local_path(APPROVED_PYTHON_TARGET, directory=True)
    if APPROVED_PYTHON_HOME.resolve(strict=True) != target:
        raise BindingError("approved Qwen Python alias has an unexpected target")
    python = _local_path(target / "python.exe", directory=False)
    packages = _local_path(environment / "Lib" / "site-packages", directory=True)
    return python, packages


def _approved_facts(environment: Path, model: Path, prompt: Path) -> dict:
    expected = {
        "environment": APPROVED_PROJECT / ENVIRONMENT_RELATIVE,
        "model": APPROVED_PROJECT / MODEL_RELATIVE,
        "prompt": APPROVED_PROJECT / PROMPT_RELATIVE,
    }
    for name, value in {"environment": environment, "model": model, "prompt": prompt}.items():
        _local_path(value, directory=name != "prompt")
        if value != expected[name]:
            raise BindingError(f"{name} must use the expressly approved local installation")
    python, packages = _environment_facts(environment)
    return {
        "schema_version": 1,
        "app_id": APP_ID,
        "backend": "qwen3-base-0.6b",
        "environment": str(environment),
        "python": str(python),
        "site_packages": str(packages),
        "model": str(model),
        "prompt": str(prompt),
        "model_revision": MODEL_REVISION,
        "prompt_sha256": PROMPT_SHA256,
        "python_version": PYTHON_VERSION,
        "versions": dict(VERSIONS),
    }


def _source_files(facts: dict) -> dict[str, tuple[Path, int | None, str | None]]:
    packages = Path(facts["site_packages"])
    files = {
        "python": (Path(facts["python"]), None, None),
        "pyvenv": (Path(facts["environment"]) / "pyvenv.cfg", None, None),
        "prompt": (Path(facts["prompt"]), PROMPT_BYTES, PROMPT_SHA256),
    }
    for name, (size, digest) in MODEL_FILES.items():
        files[f"model/{name}"] = (Path(facts["model"]) / name, size, digest)
    for name, version in VERSIONS.items():
        metadata = packages / f"{name.replace('-', '_')}-{version}.dist-info" / "METADATA"
        files[f"package/{name}"] = (metadata, None, None)
    return files


def _fingerprint(path: Path) -> dict:
    _local_path(path, directory=False)
    info = path.stat()
    if info.st_size <= 0:
        raise BindingError("Qwen source files must not be empty")
    return {
        "path": str(path),
        "size": info.st_size,
        "mtime_ns": info.st_mtime_ns,
        "ctime_ns": info.st_ctime_ns,
        "device": info.st_dev,
        "inode": info.st_ino,
    }


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def _idle_seconds(value: object) -> int:
    if type(value) is not int or not 1 <= value <= 600:
        raise BindingError("idle_unload_seconds must be an integer from 1 through 600")
    return value


def load_binding(paths: DataPaths) -> dict:
    """Read a strict manifest and check pinned facts/stats without hashing weights."""
    try:
        root = owned_root(paths)
        manifest = _read_json(safe_child(root, "binding.json"))
        for name in ("environment", "model", "prompt"):
            if type(manifest.get(name)) is not str:
                raise BindingError("Qwen binding is missing an approved path")
        facts = _approved_facts(
            *(Path(manifest[name]) for name in ("environment", "model", "prompt"))
        )
        if set(manifest) != {*facts, "files", "idle_unload_seconds"}:
            raise BindingError("Qwen binding contains missing or unknown fields")
        if type(manifest["schema_version"]) is not int or any(
            manifest[name] != value for name, value in facts.items()
        ):
            raise BindingError("Qwen binding facts differ from the approved runtime")
        _idle_seconds(manifest["idle_unload_seconds"])
        files = _source_files(facts)
        records = manifest["files"]
        if type(records) is not dict or set(records) != set(files):
            raise BindingError("Qwen binding source-file inventory is invalid")
        for name, (path, size, digest) in files.items():
            record = records[name]
            current = _fingerprint(path)
            if (
                type(record) is not dict
                or set(record) != {*current, "sha256"}
                or any(type(record[key]) is not type(value) for key, value in current.items())
                or any(record[key] != value for key, value in current.items())
                or type(record["sha256"]) is not str
                or not re.fullmatch(r"[0-9a-f]{64}", record["sha256"])
                or (size is not None and current["size"] != size)
                or (digest is not None and record["sha256"] != digest)
            ):
                raise BindingError(f"Qwen source changed; register it again: {name}")
        return manifest
    except (OSError, ValueError, TypeError, RecursionError) as exc:
        if isinstance(exc, BindingError):
            raise
        raise BindingError("Qwen binding is unavailable or invalid") from exc


def register_binding(
    paths: DataPaths,
    *,
    environment: Path,
    model: Path,
    prompt: Path,
    idle_unload_seconds: int = 300,
) -> dict:
    """Register approved existing assets, intended solely for the explicit local CLI."""
    try:
        idle = _idle_seconds(idle_unload_seconds)
        facts = _approved_facts(environment, model, prompt)
        records = {}
        for name, (path, size, expected_digest) in _source_files(facts).items():
            before = _fingerprint(path)
            if size is not None and before["size"] != size:
                raise BindingError(f"Qwen source size does not match the approved file: {name}")
            if name.startswith("package/") and before["size"] > 128 * 1024:
                raise BindingError("Qwen distribution metadata exceeds its size limit")
            digest = _sha256(path)
            if _fingerprint(path) != before:
                raise BindingError("Qwen source changed during registration")
            if expected_digest is not None and digest != expected_digest:
                raise BindingError(f"Qwen source digest does not match the approved file: {name}")
            if name.startswith("package/"):
                package = name.removeprefix("package/")
                metadata = PathDistribution(path.parent).metadata
                if (
                    metadata.get("Name", "").lower().replace("_", "-") != package
                    or metadata.get("Version") != VERSIONS[package]
                ):
                    raise BindingError(
                        f"Qwen requires the pinned installed distribution: {package}"
                    )
            records[name] = {**before, "sha256": digest}
        config = _read_json(model / "config.json")
        if config.get("tts_model_type") != "base" or config.get("tts_model_size") != "0b6":
            raise BindingError("only the approved Qwen Base 0.6B model is supported")
        manifest = {**facts, "files": records, "idle_unload_seconds": idle}
        serialized = (json.dumps(manifest, ensure_ascii=True, indent=2) + "\n").encode("utf-8")
        if len(serialized) > MAX_BINDING_BYTES:
            raise BindingError("Qwen binding exceeds its size limit")
        root = owned_root(paths, create=True)
        target = safe_child(root, "binding.json")
        temporary = safe_child(root, f".binding-{uuid.uuid4().hex}.tmp")
        try:
            fd = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
            with os.fdopen(fd, "wb") as handle:
                handle.write(serialized)
                handle.flush()
                os.fsync(handle.fileno())
            safe_child(root, "binding.json")
            os.replace(temporary, target)
        finally:
            if temporary.exists():
                temporary.unlink()
        return load_binding(paths)
    except (OSError, ValueError, TypeError, RecursionError) as exc:
        if isinstance(exc, BindingError):
            raise
        if isinstance(exc, DataRootError):
            raise BindingError("Qwen managed paths are invalid") from exc
        raise BindingError("Qwen local registration failed") from exc
