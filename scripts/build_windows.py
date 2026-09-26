"""Build an auditable M1 portable ZIP on Windows x64, never cross-compile."""

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


def copy_windows_runtime_notices(destination: Path, python_root: Path) -> dict:
    """Retain native-library notices from the exact uv/PBS Windows distribution."""
    sources = ROOT / "packaging" / "licenses"
    provenance = json.loads((sources / "windows-runtime.json").read_text(encoding="utf-8"))
    if provenance["python_version"] != platform.python_version():
        raise RuntimeError("Windows runtime notices do not match the Python version")
    # Same Python patch version can be rebuilt against different native libraries.
    # Validate the actual binary distribution, not only its version string.
    for relative, expected in provenance["installed_file_sha256"].items():
        source = python_root / relative
        if not source.is_file() or sha256(source) != expected:
            raise RuntimeError(f"Windows runtime distribution changed: {relative}; refresh notices")
    destination.mkdir()
    for record in provenance["files"]:
        source = sources / record["file"]
        if sha256(source) != record["sha256"]:
            raise RuntimeError(f"Windows runtime notice hash mismatch: {record['file']}")
        shutil.copyfile(source, destination / record["file"])
    (destination / "provenance.json").write_text(
        json.dumps(provenance, indent=2) + "\n", encoding="utf-8"
    )
    return provenance


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
            # Some Windows wheels write backslashes in RECORD. PackagePath is
            # PurePosixPath even on Windows, so its .name is then the whole path.
            source_name = str(source).replace("\\", "/").rsplit("/", 1)[-1]
            if not source_name.upper().startswith(("LICENSE", "COPYING", "NOTICE")):
                continue
            path = Path(dist.locate_file(source))
            if not path.is_file():
                continue
            # A numeric prefix avoids collisions between multiple nested notices.
            output = target / f"{len(files):02d}-{source_name}"
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
        "scope": "Python runtime dependency closure and PyInstaller bootloader; desktop notices are separate",
        "dependencies": entries,
        "python": {
            "version": platform.python_version(),
            "file": "PYTHON-LICENSE.txt",
            "sha256": sha256(destination / "PYTHON-LICENSE.txt"),
            "origin": python_origin,
        },
    }
    if sys.platform == "win32":
        manifest["windows_runtime"] = copy_windows_runtime_notices(
            destination / "windows-runtime", Path(sys.base_prefix)
        )
    (destination / "manifest.json").write_text(
        json.dumps(manifest, indent=2) + "\n", encoding="utf-8"
    )
    return manifest


def input_hashes() -> dict[str, str]:
    files = [
        ROOT / name for name in ("pyproject.toml", "uv.lock", ".python-version", ".gitattributes")
    ]
    for name in ("src", "scripts", "packaging", "desktop", ".github/workflows"):
        files += [
            path
            for path in (ROOT / name).rglob("*")
            if path.is_file()
            and not {"__pycache__", "node_modules", "test-results"}.intersection(path.parts)
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
        "stage": "MVP1-desktop",
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
            "runner_image": os.environ.get("ImageOS"),  # noqa: SIM112 (GitHub runner casing)
            "runner_image_version": os.environ.get("ImageVersion"),  # noqa: SIM112 (GitHub runner casing)
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


def verify_desktop_assets() -> None:
    """Reject changed, redirected or missing imported bytes before distribution."""
    manifest = json.loads((ROOT / "docs" / "mvp1-assets-manifest.json").read_text(encoding="utf-8"))
    if not manifest.get("files"):
        raise RuntimeError("Desktop asset provenance is empty")
    for record in manifest["files"]:
        relative = Path(record["path"])
        if relative.is_absolute() or ".." in relative.parts:
            raise RuntimeError("Desktop asset path escapes the source tree")
        file = ROOT / relative
        if (
            file.is_symlink()
            or not file.is_file()
            or not file.resolve().is_relative_to(ROOT.resolve())
            or file.stat().st_size != record["bytes"]
            or sha256(file) != record["sha256"]
        ):
            raise RuntimeError(f"Desktop asset integrity mismatch: {relative.as_posix()}")


def copy_memory_notices(destination: Path) -> dict:
    """Preserve notices for the independently extracted upstream memory helpers."""
    destination.mkdir(parents=True, exist_ok=True)
    records = {}
    expected = {
        "NEKO-LICENSE.txt": "0d99b64baf1323c3a7f70a28075b14d573df6d773aa2d33d827053cc775062d8",
        "NEKO-NOTICE.txt": "96199da23327c5086c4f562e4c1a05685e18643ab32ce896f13934b15b4a8aca",
    }
    for name, digest in expected.items():
        source = ROOT / "src" / "ai_neko" / "memory" / "licenses" / name
        if sha256(source) != digest:
            raise RuntimeError("Memory component license provenance mismatch")
        shutil.copyfile(source, destination / name)
        records[name] = digest
    shutil.copyfile(ROOT / "docs" / "MEMORY-REUSE.md", destination / "MEMORY-REUSE.md")
    return {"source_commit": "90ccf79c95e80f899b9bf3395fa8cd9a9bfe29be", "notices": records}


def assemble_desktop(package: Path, backend: Path) -> dict:
    """Copy Electron's exact installed distribution, never depend on user Node/Python."""
    desktop = ROOT / "desktop"
    verify_desktop_assets()
    metadata = json.loads((desktop / "package.json").read_text(encoding="utf-8"))
    dist = desktop / "node_modules" / "electron" / "dist"
    expected = metadata["devDependencies"]["electron"]
    lock = json.loads((desktop / "package-lock.json").read_text(encoding="utf-8"))
    if lock["packages"]["node_modules/electron"]["version"] != expected:
        raise RuntimeError("Electron package and dependency lock differ")
    if (dist / "version").read_text(encoding="utf-8").strip() != expected:
        raise RuntimeError("Electron distribution differs from locked desktop version")
    if not (dist / "electron.exe").is_file():
        raise RuntimeError("Windows Electron x64 distribution is required")
    for notice in ("LICENSE", "LICENSES.chromium.html"):
        if not (dist / notice).is_file():
            raise RuntimeError(f"Missing Electron distribution notice: {notice}")
    core = desktop / "vendor" / "live2dcubismcore.min.js"
    if not core.is_file():
        raise RuntimeError("Run the pinned Core asset fetch before building")
    shutil.copytree(dist, package)
    (package / "electron.exe").rename(package / "ai-neko.exe")
    default_app = package / "resources" / "default_app.asar"
    default_app.unlink(missing_ok=True)
    app = package / "resources" / "app"
    app.mkdir()
    for name in ("main.cjs", "preload.cjs", "package.json"):
        shutil.copyfile(desktop / name, app / name)
    for name in ("lib", "renderer", "consent", "vendor", "assets"):
        shutil.copytree(desktop / name, app / name)
    shutil.move(str(backend), package / "resources" / "backend")
    for name in ("MVP1-ASSETS.md", "mvp1-assets-manifest.json"):
        shutil.copyfile(ROOT / "docs" / name, package / name)
    return {
        "electron": expected,
        "package_lock_sha256": sha256(desktop / "package-lock.json"),
        "asset_manifest_sha256": sha256(ROOT / "docs" / "mvp1-assets-manifest.json"),
        "entry": "ai-neko.exe",
        "executable_sha256": sha256(package / "ai-neko.exe"),
        "backend_entry": "resources/backend/ai-neko.exe",
        "backend_sha256": sha256(package / "resources" / "backend" / "ai-neko.exe"),
        "core_sha256": sha256(core),
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
    parser.add_argument("--version", required=True, help="0.4.0-alpha.1 or 0.4.0-dev.COMMIT")
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
            backend = work / "dist" / "ai-neko"
            if not (backend / "ai-neko.exe").is_file():
                raise RuntimeError("PyInstaller did not produce ai-neko.exe")
            info["desktop"] = assemble_desktop(package, backend)
            for filename in (
                "README-WINDOWS.txt",
                "Check foundation.cmd",
                "Start service.cmd",
                "Start ai-neko.cmd",
                "Stop service.cmd",
            ):
                source = ROOT / "packaging" / filename
                # Windows launchers should work even if Git checked them out with LF.
                (package / filename).write_text(
                    source.read_text(encoding="utf-8"), encoding="utf-8", newline="\r\n"
                )
            copy_licenses(package / "third-party-licenses", dependencies)
            info["memory_reuse"] = copy_memory_notices(
                package / "third-party-licenses" / "neko-memory"
            )
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
                    "stage": "MVP1-desktop",
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
