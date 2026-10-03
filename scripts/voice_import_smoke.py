"""Bound the real voice worker import/main/exit protocol with stdin held open.

The caller installs only the three locked wheels into a dedicated temporary
venv. This probe creates positive-size placeholder resources, never downloads
models, and never treats successful imports as real voice inference.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import platform
import re
import subprocess
import time
import tomllib
from datetime import datetime, timezone
from email.parser import Parser
from pathlib import Path, PurePosixPath

STAGE = "local-voice-native-import-open-stdin"
ROOT = Path(__file__).resolve().parents[1]
RUNTIME = ROOT / "src/ai_neko/media/local_runtime"
TIMEOUT_SECONDS = 20
PYTHON_VERSION = "3.11.15"
PACKAGES = {"numpy", "sherpa-onnx", "sherpa-onnx-core"}
EXPECTED_STAGES = ["numpy_before", "numpy_after", "sherpa_before", "sherpa_after", "main_returned"]
OWNER = {"app_id": "ai-neko", "stage": STAGE, "schema_version": 1}
WRAPPER = r"""
import builtins,json,os,runpy,struct,sys
from pathlib import Path
worker,stages,identity=sys.argv[1:]
Path(identity).write_text(json.dumps({
    "python_version":".".join(str(part) for part in sys.version_info[:3]),
    "pointer_bits":struct.calcsize("P")*8,
    "platform":sys.platform,
    "base_interpreter":sys.prefix==sys.base_prefix,
    "pid":os.getpid(),
}),encoding="utf-8")
stage_fd=os.open(stages,os.O_WRONLY|os.O_CREAT|os.O_EXCL,0o600)
def mark(value):
    os.write(stage_fd,(value+"\n").encode("ascii"))
original_import=builtins.__import__
observed=set()
def traced_import(name,*args,**kwargs):
    label={"numpy":"numpy","sherpa_onnx":"sherpa"}.get(name)
    if label and label not in observed:
        observed.add(label)
        mark(label+"_before")
        value=original_import(name,*args,**kwargs)
        mark(label+"_after")
        return value
    return original_import(name,*args,**kwargs)
namespace=runpy.run_path(worker)
builtins.__import__=traced_import
namespace["main"]()
mark("main_returned")
os.close(stage_fd)
"""


def sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def require(value: bool, code: str) -> None:
    if not value:
        raise ValueError(code)


def inspect_venv(venv: Path) -> tuple[Path, Path, list[dict[str, str]]]:
    require(venv.is_absolute() and venv.name == "venv", "dedicated_venv_required")
    require(venv.parent.name.startswith("ai-neko-voice-import-"), "dedicated_profile_required")
    require(not venv.is_symlink(), "redirected_venv")
    venv = venv.resolve(strict=True)
    cfg = dict(
        (name.strip(), value.strip())
        for line in (venv / "pyvenv.cfg").read_text(encoding="utf-8").splitlines()
        if "=" in line
        for name, value in [line.split("=", 1)]
    )
    require(cfg.get("version_info") == PYTHON_VERSION, "unexpected_python_version")
    require(cfg.get("implementation") == "CPython", "unexpected_python_implementation")
    require(cfg.get("include-system-site-packages") == "false", "system_packages_forbidden")
    home = Path(cfg["home"]).resolve(strict=True)
    base = home / ("python.exe" if os.name == "nt" else "python3.11")
    base = base.resolve(strict=True)
    require(base.is_file() and not base.is_relative_to(venv), "base_python_required")
    suffix = "Lib/site-packages" if os.name == "nt" else "lib/python3.11/site-packages"
    site = venv / suffix
    require(site.is_dir() and site.resolve().is_relative_to(venv), "private_packages_required")
    lock = tomllib.loads((RUNTIME / "uv.lock").read_text(encoding="utf-8"))
    expected = {
        item["name"]: item["version"] for item in lock["package"] if item["name"] in PACKAGES
    }
    require(set(expected) == PACKAGES, "locked_packages_missing")
    installed = {}
    for metadata in site.glob("*.dist-info/METADATA"):
        require(
            not metadata.is_symlink() and metadata.stat().st_size <= 1024 * 1024, "unsafe_metadata"
        )
        parsed = Parser().parsestr(metadata.read_text(encoding="utf-8"))
        name = re.sub(r"[-_.]+", "-", (parsed.get("Name") or "").lower())
        require(name in PACKAGES and name not in installed, "unexpected_installed_package")
        installed[name] = parsed.get("Version")
    require(installed == expected, "installed_versions_differ_from_lock")
    return base, site, [{"name": name, "version": installed[name]} for name in sorted(installed)]


def prepare_profile(venv: Path, names: list[str]) -> Path:
    profile = venv.parent
    require(not (profile / "models").exists(), "placeholder_models_already_exist")
    require(not (profile / ".voice-import-owner.json").exists(), "profile_already_used")
    require(
        {item.name for item in profile.iterdir()} <= {"venv", "cache"}, "unrelated_profile_files"
    )
    require(isinstance(names, list) and bool(names), "invalid_resource_list")
    for name in names:
        require(isinstance(name, str) and bool(name), "invalid_resource_name")
        parts = PurePosixPath(name).parts
        require(
            not PurePosixPath(name).is_absolute()
            and not any(p in {".", ".."} for p in parts)
            and "\\" not in name
            and ":" not in name,
            "unsafe_resource_name",
        )
    (profile / ".voice-import-owner.json").write_text(json.dumps(OWNER), encoding="utf-8")
    models = profile / "models"
    for name in names:
        target = models / name
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(b"x")
    for name in ("home", "temp"):
        (profile / name).mkdir()
    return models


def child_environment(profile: Path) -> dict[str, str]:
    keep = {"SYSTEMROOT", "WINDIR", "COMSPEC"}
    env = {key: value for key, value in os.environ.items() if key.upper() in keep}
    system = next(
        (value for key, value in env.items() if key.upper() == "SYSTEMROOT"), r"C:\Windows"
    )
    env.update(
        {
            "PATH": str(Path(system) / "System32") if os.name == "nt" else "/usr/bin:/bin",
            "HOME": str(profile / "home"),
            "USERPROFILE": str(profile / "home"),
            "LOCALAPPDATA": str(profile / "home"),
            "APPDATA": str(profile / "home"),
            "TEMP": str(profile / "temp"),
            "TMP": str(profile / "temp"),
            "TMPDIR": str(profile / "temp"),
            "PYTHONUTF8": "1",
        }
    )
    return env


def run_worker(
    base: Path, site: Path, models: Path, worker: Path, *, timeout: float = TIMEOUT_SECONDS
) -> dict:
    require(0 < timeout <= TIMEOUT_SECONDS, "invalid_timeout")
    profile = models.parent
    stages_path, identity_path = profile / "fixed-stages.txt", profile / "runtime-info.json"
    require(not stages_path.exists() and not identity_path.exists(), "probe_already_ran")
    started = time.monotonic()
    child = subprocess.Popen(
        [str(base), "-I", "-B", "-c", WRAPPER, str(worker), str(stages_path), str(identity_path)],
        cwd=profile,
        env=child_environment(profile),
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.DEVNULL,
    )
    timed_out = False
    try:
        request = {
            "operation": "check",
            "parent": os.getpid(),
            "models": str(models),
            "site_packages": str(site),
        }
        child.stdin.write(json.dumps(request).encode("utf-8") + b"\n")
        child.stdin.flush()
        # Never communicate(input) or close stdin before process exit: the
        # open owner pipe is the exact native-import regression under test.
        try:
            child.wait(timeout=max(0.001, timeout - (time.monotonic() - started)))
        except subprocess.TimeoutExpired:
            timed_out = True
            child.kill()
            child.wait(timeout=5)
        elapsed = time.monotonic() - started
        protocol_bytes = child.stdout.read(4097)
        try:
            protocol = json.loads(protocol_bytes) if len(protocol_bytes) <= 4096 else None
        except (ValueError, UnicodeError):
            protocol = None
        expected = {"ok": True, "pid": child.pid}
        protocol_matches = protocol == expected
        stages = (
            stages_path.read_text(encoding="ascii").splitlines() if stages_path.exists() else []
        )
        valid_stages = all(stage in EXPECTED_STAGES for stage in stages)
        identity = (
            json.loads(identity_path.read_text(encoding="utf-8")) if identity_path.exists() else {}
        )
        expected_keys = {"python_version", "pointer_bits", "platform", "base_interpreter", "pid"}
        identity_valid = (
            set(identity) == expected_keys
            and identity.get("pid") == child.pid
            and identity.get("python_version") == PYTHON_VERSION
            and identity.get("base_interpreter") is True
            and identity.get("pointer_bits") == 64
        )
        passed = (
            not timed_out
            and elapsed <= timeout
            and child.returncode == 0
            and protocol_matches
            and valid_stages
            and stages == EXPECTED_STAGES
            and identity_valid
        )
        return {
            "status": "PASS" if passed else "FAILED",
            "timeout_seconds": timeout,
            "timed_out": timed_out,
            "elapsed_seconds": round(elapsed, 4),
            "pid": child.pid,
            "exit_code": child.returncode,
            "owner_stdin_kept_open_until_exit": True,
            "natural_exit": not timed_out and child.returncode == 0,
            "fixed_stages": stages if valid_stages else [],
            "stages_match": valid_stages and stages == EXPECTED_STAGES,
            "protocol_matches": protocol_matches,
            "fixed_result": expected if protocol_matches else None,
            "runtime_identity": identity if identity_valid else None,
        }
    finally:
        if child.poll() is None:
            child.kill()
            child.wait(timeout=5)
        child.stdin.close()
        child.stdout.close()


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--venv", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args(argv)
    output = args.output.expanduser().absolute()
    if (
        output.suffix != ".json"
        or output.is_symlink()
        or output.exists()
        or any(
            ROOT / folder in output.resolve().parents
            for folder in ("src", "tests", "scripts", ".git", ".venv")
        )
    ):
        parser.error("Use a new dedicated evidence JSON path outside application sources")
    started = time.monotonic()
    evidence = {
        "schema_version": 1,
        "app_id": "ai-neko",
        "stage": STAGE,
        "status": "FAILED",
        "started_at_utc": datetime.now(timezone.utc).isoformat(),
        "environment": {
            "system": platform.system(),
            "machine": platform.machine(),
            "harness_python": platform.python_version(),
        },
        "scope": {
            "native_imports": True,
            "worker_main_protocol": True,
            "actual_model_inference": False,
            "real_models_downloaded": 0,
            "user_data_accessed": False,
            "placeholder_resources_are_not_models": True,
        },
        "worker_sha256": sha256(RUNTIME / "worker.py"),
        "runtime_lock_sha256": sha256(RUNTIME / "uv.lock"),
        "model_files_sha256": sha256(RUNTIME / "model_files.json"),
    }
    stage = "inspect_private_locked_venv"
    try:
        requested_venv = args.venv.expanduser().absolute()
        require(not requested_venv.is_symlink(), "redirected_venv")
        venv = requested_venv.resolve(strict=True)
        base, site, installed = inspect_venv(venv)
        evidence.update(
            {
                "base_python": str(base),
                "private_venv": str(venv),
                "private_site_packages": str(site),
                "installed_packages": installed,
            }
        )
        stage = "create_positive_size_placeholder_resources"
        names = json.loads((RUNTIME / "model_files.json").read_text(encoding="utf-8"))
        models = prepare_profile(venv, names)
        evidence["placeholder_resources"] = {"count": len(names), "bytes_per_file": 1}
        stage = "worker_open_stdin_import_main_and_natural_exit"
        result = run_worker(base, site, models, RUNTIME / "worker.py")
        evidence["worker"] = result
        evidence["status"] = result["status"]
    except Exception as error:
        evidence["failure"] = {"stage": stage, "error_class": type(error).__name__}
    evidence["duration_seconds"] = round(time.monotonic() - started, 4)
    evidence["diagnostic_policy"] = (
        "Only fixed import markers and validated result/identity; native stderr is discarded."
    )
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(evidence, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(f"Voice native import/open-stdin protocol: {evidence['status']}; evidence: {output}")
    return 0 if evidence["status"] == "PASS" else 1


if __name__ == "__main__":
    raise SystemExit(main())
