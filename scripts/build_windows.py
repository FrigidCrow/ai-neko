"""Build an auditable M0 portable ZIP on Windows x64, never cross-compile."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import platform
import re
import shutil
import struct
import subprocess
import sys
import sysconfig
import tempfile
import tomllib
import zipfile
from datetime import datetime, timezone
from importlib.metadata import Distribution, distribution, version
from pathlib import Path

from packaging.requirements import Requirement
from packaging.utils import canonicalize_name

ROOT = Path(__file__).resolve().parents[1]
EXPECTED_PYTHON = "3.11.15"
EXPECTED_PYINSTALLER = "6.22.3"
VERSION_PATTERN = re.compile(
    r"(?P<base>(?:0|[1-9][0-9]*)\.(?:0|[1-9][0-9]*)\.(?:0|[1-9][0-9]*))"
    r"(?:-[A-Za-z0-9]+(?:[.-][A-Za-z0-9]+)*)?"
)


def sha256(path: Path) -> str:
    with path.open("rb") as handle:
        return hashlib.file_digest(handle, "sha256").hexdigest()


def validate_version(value: str, base: str) -> str:
    matched = VERSION_PATTERN.fullmatch(value)
    if not matched or len(value) > 80:
        raise ValueError("version must be a safe X.Y.Z or X.Y.Z-prerelease value (no leading v)")
    if matched["base"] != base:
        raise ValueError(f"release version base must equal pyproject.toml version {base}")
    return value


def check_platform() -> None:
    if sys.platform != "win32" or platform.machine().lower() not in {"amd64", "x86_64"}:
        raise RuntimeError(
            "Windows x64 is required; this script does not cross-compile on Mac/Linux"
        )
    if struct.calcsize("P") != 8:
        raise RuntimeError("a 64-bit Python interpreter is required")
    if platform.python_version() != EXPECTED_PYTHON:
        raise RuntimeError(f"the build requires Python {EXPECTED_PYTHON}")
    if version("pyinstaller") != EXPECTED_PYINSTALLER:
        raise RuntimeError(f"the build requires PyInstaller {EXPECTED_PYINSTALLER}")


def dependency_closure(requirements: list[str]) -> dict[str, Distribution]:
    """Resolve installed runtime requirements, honoring Windows/Python/extra markers."""
    pending = [Requirement(raw) for raw in requirements]
    found: dict[str, Distribution] = {}
    visited = set()
    while pending:
        requirement = pending.pop()
        name = canonicalize_name(requirement.name)
        extras = tuple(sorted(requirement.extras))
        key = (name, extras)
        if key in visited:
            continue
        visited.add(key)
        dist = distribution(name)
        if requirement.specifier and not requirement.specifier.contains(dist.version):
            raise RuntimeError(f"installed {name} {dist.version} violates {requirement.specifier}")
        found[name] = dist
        for raw in dist.requires or []:
            dependency = Requirement(raw)
            if dependency.marker is None or any(
                dependency.marker.evaluate({"extra": extra}) for extra in ("", *extras)
            ):
                pending.append(dependency)
    return dict(sorted(found.items()))


def python_license_source(fallback_root: Path, records: list[dict]) -> tuple[Path, dict]:
    """Use the interpreter's notice, or an exact-version, hash-checked CPython fallback."""
    runtime_version = platform.python_version()
    if platform.python_implementation() != "CPython" or runtime_version != EXPECTED_PYTHON:
        raise RuntimeError(f"Python license requires CPython {EXPECTED_PYTHON}")
    candidates = (
        Path(sys.base_prefix) / "LICENSE.txt",
        Path(sys.base_prefix) / "LICENSE",
        Path(sysconfig.get_path("stdlib")) / "LICENSE.txt",
    )
    for source in candidates:
        if source.is_file():
            return source, {"kind": "installed-interpreter", "filename": source.name}
    for record in records:
        if record["distribution"] != "cpython" or record["version"] != runtime_version:
            continue
        source = fallback_root / record["file"]
        if sha256(source) != record["sha256"]:
            raise RuntimeError("license fallback hash mismatch: cpython")
        return source, record
    raise RuntimeError(f"missing license text for CPython {runtime_version}")


def copy_licenses(destination: Path, dependencies: dict[str, Distribution]) -> dict:
    """Copy only license/notice files, including version-bound missing-wheel fallbacks."""
    destination.mkdir()
    fallback_root = ROOT / "packaging" / "licenses"
    fallback = json.loads((fallback_root / "provenance.json").read_text(encoding="utf-8"))
    entries = []
    # The bootloader is redistributed; PyInstaller's COPYING includes its exception.
    notices = {**dependencies, "pyinstaller": distribution("pyinstaller")}
    for name, dist in sorted(notices.items()):
        target = destination / f"{name}-{dist.version}"
        target.mkdir()
        files = []
        for source in dist.files or []:
            if not source.name.upper().startswith(("LICENSE", "COPYING", "NOTICE")):
                continue
            path = Path(dist.locate_file(source))
            if not path.is_file():
                continue
            # A numeric prefix avoids collisions between multiple nested notices.
            output = target / f"{len(files):02d}-{source.name}"
            shutil.copyfile(path, output)
            files.append(
                {
                    "path": output.relative_to(destination).as_posix(),
                    "sha256": sha256(output),
                    "origin": str(source),
                }
            )
        if not files:
            for record in fallback:
                if record["distribution"] != name or record["version"] != dist.version:
                    continue
                source = fallback_root / record["file"]
                if sha256(source) != record["sha256"]:
                    raise RuntimeError(f"license fallback hash mismatch: {name}")
                output = target / record["file"]
                shutil.copyfile(source, output)
                files.append(
                    {
                        "path": output.relative_to(destination).as_posix(),
                        "sha256": sha256(output),
                        "origin": record,
                    }
                )
        if not files:
            raise RuntimeError(f"missing redistributable license text: {name}=={dist.version}")
        entries.append(
            {
                "name": name,
                "version": dist.version,
                "license": dist.metadata.get("License-Expression") or dist.metadata.get("License"),
                "files": files,
            }
        )
    python_license, python_origin = python_license_source(fallback_root, fallback)
    shutil.copyfile(python_license, destination / "PYTHON-LICENSE.txt")
    manifest = {
        "schema_version": 1,
        "scope": "runtime dependency closure and PyInstaller bootloader; no N.E.K.O code/assets",
        "dependencies": entries,
        "python": {
            "version": platform.python_version(),
            "file": "PYTHON-LICENSE.txt",
            "sha256": sha256(destination / "PYTHON-LICENSE.txt"),
            "origin": python_origin,
        },
    }
    (destination / "manifest.json").write_text(
        json.dumps(manifest, indent=2) + "\n", encoding="utf-8"
    )
    return manifest


def input_hashes() -> dict[str, str]:
    files = [ROOT / "pyproject.toml", ROOT / "uv.lock", ROOT / ".python-version"]
    for name in ("src", "scripts", "packaging", ".github/workflows"):
        files += [
            path
            for path in (ROOT / name).rglob("*")
            if path.is_file() and "__pycache__" not in path.parts
        ]
    return {
        path.relative_to(ROOT).as_posix(): sha256(path)
        for path in sorted(set(files))
        if path.is_file()
    }


def git(*args: str) -> str:
    return subprocess.check_output(["git", *args], cwd=ROOT, text=True).strip()


def build_info(release_version: str, base: str, dependencies: dict[str, Distribution]) -> dict:
    return {
        "schema_version": 1,
        "app_id": "ai-neko",
        "stage": "M0",
        "version": release_version,
        "release_version": release_version,
        "base_version": base,
        "app_version": base,
        "source": {
            "commit": git("rev-parse", "HEAD"),
            "dirty": bool(git("status", "--porcelain")),
            "input_sha256": input_hashes(),
        },
        "lockfile": {"file": "uv.lock", "sha256": sha256(ROOT / "uv.lock")},
        "build": {
            "created_utc": datetime.now(timezone.utc).isoformat(),
            "platform": platform.platform(),
            "architecture": platform.machine(),
            "python": platform.python_version(),
            "pyinstaller": version("pyinstaller"),
            "pyinstaller_hooks_contrib": version("pyinstaller-hooks-contrib"),
            "runner_os": os.environ.get("RUNNER_OS"),
            "runner_image": os.environ.get("ImageOS"),
            "runner_image_version": os.environ.get("ImageVersion"),
            "github_run_id": os.environ.get("GITHUB_RUN_ID"),
            "github_run_attempt": os.environ.get("GITHUB_RUN_ATTEMPT"),
        },
        "dependencies": {name: dist.version for name, dist in dependencies.items()},
        "verification": {
            "packaged_smoke": "pending; see separate CI smoke report",
            "windows_11": "pending; Windows Server CI is not Windows 11 acceptance",
            "real_model_calls": 0,
        },
    }


def make_zip(folder: Path, archive: Path) -> None:
    with zipfile.ZipFile(archive, "w", compression=zipfile.ZIP_DEFLATED, compresslevel=6) as output:
        for path in sorted(folder.rglob("*")):
            if path.is_symlink():
                raise RuntimeError("portable packages cannot contain symbolic links")
            if path.is_file():
                output.write(path, path.relative_to(folder.parent))


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--version", required=True, help="0.1.0-alpha.1 or 0.1.0-dev.COMMIT")
    parser.add_argument("--output", type=Path, default=Path("artifacts/package"))
    args = parser.parse_args(argv)
    try:
        project = tomllib.loads((ROOT / "pyproject.toml").read_text(encoding="utf-8"))["project"]
        release_version = validate_version(args.version, project["version"])
        check_platform()
        output = args.output.resolve()
        if output.exists() and any(output.iterdir()):
            raise ValueError("output directory must be empty; select a new folder")
        dependencies = dependency_closure(project["dependencies"])
        info = build_info(release_version, project["version"], dependencies)
        name = f"ai-neko-{release_version}-windows-x64"
        with tempfile.TemporaryDirectory(prefix="ai-neko-build-") as temporary:
            work = Path(temporary)
            subprocess.run(
                [
                    sys.executable,
                    "-m",
                    "PyInstaller",
                    "--noconfirm",
                    "--clean",
                    "--distpath",
                    str(work / "dist"),
                    "--workpath",
                    str(work / "build"),
                    str(ROOT / "packaging" / "ai-neko.spec"),
                ],
                cwd=ROOT,
                check=True,
            )
            package = work / name
            shutil.move(str(work / "dist" / "ai-neko"), package)
            if not (package / "ai-neko.exe").is_file():
                raise RuntimeError("PyInstaller did not produce ai-neko.exe")
            for filename in (
                "README-WINDOWS.txt",
                "Check foundation.cmd",
                "Start service.cmd",
                "Stop service.cmd",
            ):
                source = ROOT / "packaging" / filename
                # Windows launchers should work even if Git checked them out with LF.
                (package / filename).write_text(
                    source.read_text(encoding="utf-8"), encoding="utf-8", newline="\r\n"
                )
            copy_licenses(package / "third-party-licenses", dependencies)
            rendered = json.dumps(info, indent=2) + "\n"
            (package / "build-info.json").write_text(rendered, encoding="utf-8")
            output.mkdir(parents=True, exist_ok=True)
            archive = output / f"{name}.zip"
            make_zip(package, archive)
            (output / "build-info.json").write_text(rendered, encoding="utf-8")
            checksums = "".join(
                f"{sha256(path)}  {path.name}\n" for path in (archive, output / "build-info.json")
            )
            (output / "SHA256SUMS.txt").write_text(checksums, encoding="utf-8", newline="\n")
        print(
            json.dumps(
                {
                    "status": "built",
                    "stage": "M0",
                    "archive": str(archive),
                    "sha256": sha256(archive),
                    "windows_11_acceptance": "pending",
                }
            )
        )
        return 0
    except (ValueError, RuntimeError, OSError, subprocess.CalledProcessError) as exc:
        print(f"build_windows: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
