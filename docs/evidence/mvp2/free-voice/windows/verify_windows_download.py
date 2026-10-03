"""Independently verify downloaded free-voice Windows CI artifacts.

Preparation is not verification: this script emits PASS only after all downloaded
reports, package members, source hashes, licenses and audio evidence pass.
"""

import argparse
import hashlib
import io
import json
import math
import re
import struct
import subprocess
import tomllib
import wave
import zipfile
from datetime import datetime, timezone
from pathlib import Path, PureWindowsPath

ROOT = Path.cwd()
parser = argparse.ArgumentParser()
parser.add_argument("--run", type=int, required=True)
parser.add_argument("--sha", required=True)
parser.add_argument("--linux-passed", type=int, required=True)
parser.add_argument("--windows-passed", type=int, required=True)
parser.add_argument("--desktop-passed", type=int, default=106)
args = parser.parse_args()
RUN, SHA = args.run, args.sha
BASE = ROOT / f"artifacts/mvp2/ci-{RUN}"
PKG = BASE / "ai-neko-windows-x64"
EVIDENCE = BASE / "package-evidence"


def read(path):
    return json.loads(path.read_text(encoding="utf-8"))


def digest(data):
    return hashlib.sha256(data).hexdigest()


def filehash(path):
    with path.open("rb") as stream:
        return hashlib.file_digest(stream, "sha256").hexdigest()


def verify_native_import_report(report, package_bytes, expected_packages):
    """Verify source-job imports separately from the real packaged model run."""
    assert report["schema_version"] == 1 and report["app_id"] == "ai-neko"
    assert report["stage"] == "local-voice-native-import-open-stdin"
    assert report["status"] == "PASS" and "failure" not in report
    environment = report["environment"]
    assert environment["system"] == "Windows"
    assert environment["machine"].lower() in {"amd64", "x86_64"}
    assert environment["harness_python"] == "3.11.15"
    scope = report["scope"]
    for field in ("native_imports", "worker_main_protocol", "placeholder_resources_are_not_models"):
        assert scope[field] is True, field
    assert scope["actual_model_inference"] is False and scope["user_data_accessed"] is False
    assert type(scope["real_models_downloaded"]) is int and scope["real_models_downloaded"] == 0
    input_hashes = {}
    for field, name in (
        ("worker_sha256", "worker.py"),
        ("runtime_lock_sha256", "uv.lock"),
        ("model_files_sha256", "model_files.json"),
    ):
        assert report[field] == digest(package_bytes[name]), field
        input_hashes[name] = report[field]
    installed = report["installed_packages"]
    assert len(installed) == len(expected_packages) == 3
    assert {item["name"]: item["version"] for item in installed} == expected_packages
    names = json.loads(package_bytes["model_files.json"])
    assert isinstance(names, list) and names and len(set(names)) == len(names)
    assert report["placeholder_resources"] == {"count": len(names), "bytes_per_file": 1}
    base = PureWindowsPath(report["base_python"])
    venv = PureWindowsPath(report["private_venv"])
    site = PureWindowsPath(report["private_site_packages"])
    assert base.is_absolute() and base.name.lower() == "python.exe"
    # The source job explicitly selects the uv-managed CPython build. Its
    # harness also rejects non-CPython pyvenv.cfg metadata before reporting PASS.
    assert base.parent.name == "cpython-3.11.15-windows-x86_64-none"
    assert venv.is_absolute() and venv.name == "venv" and not base.is_relative_to(venv)
    assert venv.parent.name.startswith("ai-neko-voice-import-")
    assert site == venv / "Lib/site-packages"
    assert " " in str(venv) and not str(venv).isascii()
    worker = report["worker"]
    assert worker["status"] == "PASS" and worker["exit_code"] == 0
    assert type(worker["exit_code"]) is int
    assert worker["timed_out"] is False and worker["natural_exit"] is True
    assert worker["owner_stdin_kept_open_until_exit"] is True
    assert type(worker["elapsed_seconds"]) in (int, float)
    assert type(worker["timeout_seconds"]) in (int, float)
    assert 0 < worker["elapsed_seconds"] <= worker["timeout_seconds"] <= 20
    assert type(worker["pid"]) is int and worker["pid"] > 0
    assert worker["fixed_stages"] == [
        "numpy_before",
        "numpy_after",
        "sherpa_before",
        "sherpa_after",
        "main_returned",
    ]
    assert worker["stages_match"] is True and worker["protocol_matches"] is True
    assert worker["fixed_result"] == {"ok": True, "pid": worker["pid"]}
    assert worker["fixed_result"]["ok"] is True
    assert worker["runtime_identity"] == {
        "python_version": "3.11.15",
        "pointer_bits": 64,
        "platform": "win32",
        "base_interpreter": True,
        "pid": worker["pid"],
    }
    assert worker["runtime_identity"]["base_interpreter"] is True
    return {
        "status": report["status"],
        "environment": environment,
        "scope": scope,
        "package_input_sha256": input_hashes,
        "installed_packages": installed,
        "placeholder_resources": report["placeholder_resources"],
        "base_python": str(base),
        "private_venv": str(venv),
        "worker": worker,
    }


run = read(BASE / "run.json")
assert run["headSha"] == SHA and run["conclusion"] == "success"
required = [j for j in run["jobs"] if not j["name"].startswith("Publish tagged")]
assert len(required) == 3 and all(j["conclusion"] == "success" for j in required)
assert all(
    j["conclusion"] == "skipped" for j in run["jobs"] if j["name"].startswith("Publish tagged")
)
artifacts = read(BASE / "artifacts.json")["artifacts"]
artifact = next(a for a in artifacts if a["name"] == "ai-neko-windows-x64")
assert not artifact["expired"] and artifact["workflow_run"]["head_sha"] == SHA
archives = list(PKG.glob("*.zip"))
assert len(archives) == 1
archive = archives[0]
archive_hash = filehash(archive)
checksums = dict(
    (line.split()[1].lstrip("*"), line.split()[0])
    for line in (PKG / "SHA256SUMS.txt").read_text().splitlines()
    if line.strip()
)
assert checksums[archive.name] == archive_hash
assert checksums["build-info.json"] == filehash(PKG / "build-info.json")
build = read(PKG / "build-info.json")
assert build["source"]["commit"] == SHA and build["source"]["dirty"] is False
assert build["version"] == f"0.4.0-dev.{SHA[:12]}"
assert str(build["build"]["github_run_id"]) == str(RUN)
windows_crlf_inputs = []


def matches_checkout(relative, expected):
    try:
        raw = subprocess.check_output(
            ["git", "show", f"{SHA}:{relative}"], cwd=ROOT, stderr=subprocess.DEVNULL
        )
    except subprocess.CalledProcessError:
        assert relative == "desktop/vendor/live2dcubismcore.min.js", relative
        raw = (ROOT / relative).read_bytes()
        pinned = json.loads(
            subprocess.check_output(["git", "show", f"{SHA}:desktop/vendor/sources.json"], cwd=ROOT)
        )["core"]
        assert len(raw) == pinned["bytes"] and digest(raw) == pinned["sha256"]
    if digest(raw) == expected:
        return
    assert b"\x00" not in raw, relative
    raw.decode("utf-8")
    # GitHub Windows checkout uses autocrlf for ordinary text; compare exact
    # reconstructed checkout bytes, without accepting other content changes.
    assert digest(raw.replace(b"\r\n", b"\n").replace(b"\n", b"\r\n")) == expected, relative
    windows_crlf_inputs.append(relative)


for relative, value in build["source"]["input_sha256"].items():
    matches_checkout(relative, value)
tracked = (
    subprocess.check_output(["git", "ls-tree", "-r", "--name-only", SHA], cwd=ROOT)
    .decode()
    .splitlines()
)
expected_inputs = {
    path
    for path in tracked
    if path
    in {
        "pyproject.toml",
        "uv.lock",
        ".python-version",
        ".gitattributes",
        "docs/FREE-VOICE-SOURCES.md",
    }
    or path.startswith(("src/", "scripts/", "packaging/", "desktop/", ".github/workflows/"))
}
expected_inputs = {
    path
    for path in expected_inputs
    if not {"__pycache__", "node_modules", "test-results"}.intersection(Path(path).parts)
}
expected_inputs.add("desktop/vendor/live2dcubismcore.min.js")
assert set(build["source"]["input_sha256"]) == expected_inputs
assert len(expected_inputs) >= 210
assert build["lockfile"]["sha256"] == build["source"]["input_sha256"]["uv.lock"]
assert (
    build["desktop"]["package_lock_sha256"]
    == build["source"]["input_sha256"]["desktop/package-lock.json"]
)
with zipfile.ZipFile(archive) as bundle:
    assert bundle.testzip() is None
    roots = {name.split("/")[0] for name in bundle.namelist()}
    assert len(roots) == 1
    prefix = roots.pop() + "/"
    assert json.loads(bundle.read(prefix + "build-info.json")) == build
    assert (
        digest(bundle.read(prefix + "FREE-VOICE-SOURCES.md"))
        == build["source"]["input_sha256"]["docs/FREE-VOICE-SOURCES.md"]
    )
    assert json.loads(bundle.read(prefix + "resources/app/package.json"))["version"] == "0.4.0"
    executables = {}
    for name, entry, hash_key in [
        ("desktop", "ai-neko.exe", "executable_sha256"),
        ("backend", "resources/backend/ai-neko.exe", "backend_sha256"),
    ]:
        data = bundle.read(prefix + entry)
        assert digest(data) == build["desktop"][hash_key]
        pe = struct.unpack_from("<I", data, 0x3C)[0]
        assert data[:2] == b"MZ" and data[pe : pe + 4] == b"PE\0\0"
        assert struct.unpack_from("<H", data, pe + 4)[0] == 0x8664
        executables[name] = {"sha256": digest(data), "machine": "AMD64"}
    for name, expected in build["memory_reuse"]["notices"].items():
        paths = [p for p in bundle.namelist() if p.endswith("/" + name)]
        assert paths and all(digest(bundle.read(p)) == expected for p in paths)
    # Verify each copied Python/runtime notice, not only the presence of a folder.
    license_root = prefix + "third-party-licenses/"
    licenses = json.loads(bundle.read(license_root + "manifest.json"))
    expected_dependencies = {**build["dependencies"], "pyinstaller": build["build"]["pyinstaller"]}
    assert {d["name"]: d["version"] for d in licenses["dependencies"]} == expected_dependencies
    license_files = {}
    for dependency in licenses["dependencies"]:
        assert dependency["files"], dependency["name"]
        for record in dependency["files"]:
            value = digest(bundle.read(license_root + record["path"]))
            assert value == record["sha256"], record["path"]
            license_files[record["path"]] = value
    python_notice = licenses["python"]
    assert python_notice["version"] == build["build"]["python"]
    assert digest(bundle.read(license_root + python_notice["file"])) == python_notice["sha256"]
    runtime_notices = licenses["windows_runtime"]
    for record in runtime_notices["files"]:
        value = digest(bundle.read(license_root + "windows-runtime/" + record["file"]))
        assert value == record["sha256"], record["file"]
        license_files["windows-runtime/" + record["file"]] = value
    assert runtime_notices == json.loads(
        bundle.read(license_root + "windows-runtime/provenance.json")
    )
    for notice in ("LICENSE", "LICENSES.chromium.html"):
        assert len(bundle.read(prefix + notice)) > 100
    assets = json.loads(bundle.read(prefix + "mvp1-assets-manifest.json"))
    assert (
        digest(bundle.read(prefix + "mvp1-assets-manifest.json"))
        == build["desktop"]["asset_manifest_sha256"]
    )
    for record in assets["files"]:
        assert record["path"].startswith("desktop/")
        entry = prefix + "resources/app/" + record["path"].removeprefix("desktop/")
        value = bundle.read(entry)
        assert len(value) == record["bytes"] and digest(value) == record["sha256"], entry
    assert (
        digest(bundle.read(prefix + "resources/app/vendor/live2dcubismcore.min.js"))
        == build["desktop"]["core_sha256"]
    )
    # The native libraries/models are installed later, but their exact bootstrap
    # source, lock, required-file manifest and complete license texts must ship.
    voice_root = "src/ai_neko/media/local_runtime/"
    voice_sources = sorted(path for path in expected_inputs if path.startswith(voice_root))
    expected_voice_files = {
        "worker.py",
        "pyproject.toml",
        "uv.lock",
        "model_files.json",
        "LICENSES.md",
        "licenses/UV-LICENSE-APACHE.txt",
        "licenses/UV-LICENSE-MIT.txt",
        "licenses/NUMPY-LICENSE.txt",
        "licenses/SENSEVOICE-MODEL-LICENSE.txt",
        "licenses/ESPEAK-GPL-3.0.txt",
        "licenses/KOKORO-APACHE-2.0.txt",
        "licenses/SHERPA-APACHE-2.0.txt",
    }
    assert {path.removeprefix(voice_root) for path in voice_sources} >= expected_voice_files
    voice_package_members = {}
    voice_package_bytes = {}
    for relative in voice_sources:
        suffix = "ai_neko/media/local_runtime/" + relative.removeprefix(voice_root)
        entries = [entry for entry in bundle.namelist() if entry.endswith("/" + suffix)]
        assert len(entries) == 1, (relative, entries)
        raw = bundle.read(entries[0])
        assert digest(raw) == build["source"]["input_sha256"][relative], relative
        if "/licenses/" in relative:
            assert len(raw) > 500, relative
        voice_package_members[relative] = {
            "entry": entries[0],
            "sha256": digest(raw),
            "bytes": len(raw),
        }
        voice_package_bytes[relative.removeprefix(voice_root)] = raw
    assert (
        "sherpa-onnx" not in build["dependencies"]
        and "sherpa-onnx-core" not in build["dependencies"]
    )
    assert not any(name.endswith((".onnx", "voices.bin")) for name in bundle.namelist())
source_tests = []
guide_reports = {}
for platform, passed, skipped in [
    ("Windows", args.windows_passed, 0),
    ("Linux", args.linux_passed, 1),
]:
    path = BASE / ("evidence-" + platform) / "m0" / (platform + "-smoke.json")
    d = read(path)
    assert d["ci_gate"] == "PASS" and d["source"]["git_commit"] == SHA
    assert d["source"]["git_dirty"] is False and d["source_unchanged_during_run"]
    assert {k: d["tests"][k] for k in ("passed", "skipped", "failed", "errors")} == dict(
        passed=passed, skipped=skipped, failed=0, errors=0
    )
    assert d["tests"]["real_model_tests"] == 0
    source_tests.append(
        {
            "name": path.name,
            "sha256": filehash(path),
            "passed": passed,
            "skipped": skipped,
            "ci_gate": d["ci_gate"],
            "status": d["status"],
            "environment": d["environment"],
        }
    )
    for suffix in ("evaluation", "benchmark"):
        guide_path = path.parents[1] / "mvp2" / f"{platform}-guide-{suffix}.json"
        guide = read(guide_path)
        assert guide["summary"]["status"] == "PASS"
        assert guide["summary"]["source_files_unchanged"] is True
        for relative, expected in guide["source_hashes"].items():
            matches_checkout(relative, expected)
        if suffix == "evaluation":
            assert len(guide["questions"]) == 30
            for key, expected in {
                "answerable_questions": 24,
                "top3_evidence_hits": 24,
                "absent_questions": 6,
                "absent_rejections": 6,
                "answerable_zero_network": 24,
                "answerable_skipped_planning": 24,
                "wrong_game_hits": 0,
                "known_conflicting_version_hits": 0,
                "prohibited_hits": 0,
                "source_identity_errors": 0,
                "ingestion_errors": 0,
                "withheld_or_previous_match_leaks": 0,
            }.items():
                assert guide["summary"][key] == expected, (platform, key)
            assert guide["summary"]["plain_chat_checks_passed"] is True
        else:
            assert guide["dataset"]["documents"] == 200
            assert guide["summary"]["sample_count"] == 100
            assert guide["summary"]["network_attempts"] == 0
            assert guide["summary"]["p95_ms"] <= guide["summary"]["p95_target_ms"] == 150
            assert not guide["summary"]["retrieval_verification_failures"]
        guide_reports[guide_path.name] = {
            "sha256": filehash(guide_path),
            "summary": guide["summary"],
            "environment": guide["environment"],
        }
reports = {}
for name, count in [
    ("package-smoke.json", 16),
    ("desktop-smoke.json", 9),
    ("companion-smoke.json", 21),
    ("g6-acceptance.json", 8),
    ("free-voice-smoke.json", 11),
]:
    path = PKG / name
    assert path.read_bytes() == (EVIDENCE / name).read_bytes()
    d = read(path)
    assert (
        d["status"] == "PASS" and d["source_commit"] == SHA and d["archive_sha256"] == archive_hash
    )
    cases = d.get("cases", d.get("tests", {}).get("cases"))
    assert len(cases) == count and all(c.get("status", c.get("outcome")) == "PASS" for c in cases)
    reports[name] = {"sha256": filehash(path), "passed": count}
package = read(PKG / "package-smoke.json")
desktop = read(PKG / "desktop-smoke.json")
companion = read(PKG / "companion-smoke.json")
assert package["executable_sha256"] == executables["backend"]["sha256"]
assert package["desktop_executable_sha256"] == executables["desktop"]["sha256"]
for d, harness in [
    (desktop, "scripts/desktop_smoke.cjs"),
    (companion, "desktop/tests/companion.smoke.cjs"),
]:
    assert d["build"] == build and d["source_dirty"] is False
    assert d["executable_sha256"] == executables["desktop"]["sha256"]
    matches_checkout(harness, d["harness_sha256"])
assert not companion["renderer_errors"]
persona_save_wait = companion["persona_save_wait"]
for field in (
    "explicit_promise_gate",
    "actual_form_and_ipc",
    "stale_success_predicate_passed_while_put_blocked",
    "current_save_predicate_rejected_while_put_blocked",
    "persisted_new_name_verified",
):
    assert persona_save_wait[field] is True, field
assert persona_save_wait["previous_version"] == 2
assert persona_save_wait["saved_version"] == 3
assert (
    companion["real_model_calls"]
    == companion["real_audio_service_calls"]
    == companion["actual_user_microphone_captures"]
    == companion["actual_desktop_captures"]
    == 0
)
benchmark = companion["audio_stop_benchmark"]
assert benchmark["samples"] == 20 and benchmark["actual_web_audio"] is True
assert benchmark["hardware_acoustic_measurement"] is False
assert benchmark["p95_ms"] <= benchmark["target_ms"]
assert all(companion["plain_request_revocation"].values())
screenshots = {}
for item in desktop["screenshots"]:
    path = EVIDENCE / item["file"]
    assert filehash(path) == item["sha256"]
    screenshots[path.name] = filehash(path)
for field in (
    "screenshot",
    "snapshot_screenshot",
    "search_settings_screenshot",
    "voice_settings_screenshot",
):
    path = EVIDENCE / Path(companion[field].replace("\\", "/")).name
    assert filehash(path) == companion[field + "_sha256"]
    screenshots[path.name] = filehash(path)
desktop_unit_logs = {}
for platform in ("Windows", "Linux"):
    path = BASE / (platform.lower() + "-job.log")
    log = path.read_text(encoding="utf-8")
    assert re.search(rf"(?:#|ℹ) tests {args.desktop_passed}\b", log) and re.search(
        rf"(?:#|ℹ) pass {args.desktop_passed}\b", log
    ), platform
    assert re.search(r"(?:#|ℹ) fail 0\b", log), platform
    desktop_unit_logs[platform] = {
        "sha256": filehash(path),
        "passed": args.desktop_passed,
        "failed": 0,
    }
g6 = read(PKG / "g6-acceptance.json")
assert g6["build"] == build and g6["source_dirty"] is False
assert g6["executable_sha256"] == executables["desktop"]["sha256"]
assert g6["execution"] == "windows_extracted_executable" and g6["packaged_windows"] == "PASS"
assert g6["source_files_unchanged"] and not g6["renderer_and_provider_errors"]
assert (
    g6["real_provider_calls"]
    == g6["actual_user_microphone_captures"]
    == g6["actual_desktop_captures"]
    == 0
)
assert g6["user_mutation_api_shortcuts"] == 0
assert len(g6["launches"]) == 2 and len({item["pid"] for item in g6["launches"]}) == 2
assert all(item["packaged"] for item in g6["launches"])
assert len(g6["questions_observed"]) >= 20
assert all(
    item["status"] == "completed" and item["search_requests"] == item["page_requests"] == 0
    for item in g6["questions_observed"]
)
for relative, expected in g6["source_hashes"].items():
    matches_checkout(relative, expected)
for item in g6["screenshots"]:
    path = EVIDENCE / item["file"]
    assert filehash(path) == item["sha256"]
    screenshots[path.name] = filehash(path)

free = read(PKG / "free-voice-smoke.json")
assert free["stage"] == "windows-packaged-free-voice"
assert free["schema_version"] == 1 and free["app_id"] == "ai-neko"
assert free["executable_sha256"] == executables["backend"]["sha256"]
assert free["desktop_executable_sha256"] == executables["desktop"]["sha256"]
assert free["tests"]["kind"] == "real_local_voice_with_fixed_synthetic_text"
expected_voice_cases = {
    "windows_x64_execution_environment",
    "archive_layout_and_safe_extraction",
    "authenticated_packaged_backend",
    "voice_routes_reject_missing_auth",
    "explicit_install_reaches_ready",
    "installed_model_and_runtime_provenance",
    "local_selection_preserves_cloud_configuration",
    "real_local_chinese_tts_without_chat_model",
    "real_local_asr_recognizes_synthesized_chinese",
    "restart_preserves_ready_resources",
    "graceful_stop_and_unchanged_archive",
}
assert {case["name"] for case in free["tests"]["cases"]} == expected_voice_cases
assert "failure" not in free and "api_failure" not in free
scope = free["scope"]
assert scope["real_voice_inference"] == "PASS"
for field in ("actual_user_microphone_captures", "paid_api_calls", "conversational_model_calls"):
    assert scope[field] == 0, field
assert scope["network_during_installation"] == "public_pinned_runtime_and_models"
assert scope["speaker_playback"] == scope["windows_11_device_acceptance"] == "NOT_TESTED"
environment = free["environment"]
assert environment["system"] == "Windows"
assert environment["machine"].lower() in {"amd64", "x86_64"}
assert environment["harness_pointer_bits"] == 64
assert environment["application_runtime"] == "extracted_executable_only"
assert free["execution"]["source_application_imports"] is False
install = free["installation"]
assert install["explicit_confirmation"] is True
assert install["timeout_seconds"] == 900
assert 0 < install["elapsed_seconds"] <= install["timeout_seconds"] + 10
assert install["last_status"]["state"] == "ready" and install["last_status"]["supported"] is True
assert install["transitions"][-1]["state"] == "ready"
configuration = free["configuration"]
assert configuration == {
    "voice_provider": "local",
    "cloud_voice_keys_set": False,
    "prior_cloud_voice_fields_preserved": True,
    "chat_model_configured": False,
}

resources = free["installed_resources"]
voice_lock = tomllib.loads(voice_package_bytes["uv.lock"].decode("utf-8"))
expected_packages = {
    item["name"]: item["version"]
    for item in voice_lock["package"]
    if item["name"] in {"sherpa-onnx", "sherpa-onnx-core", "numpy"}
}
assert expected_packages == {
    "sherpa-onnx": "1.13.8",
    "sherpa-onnx-core": "1.13.8",
    "numpy": "2.4.4",
}
imports_artifact = next(a for a in artifacts if a["name"] == "evidence-Windows")
assert not imports_artifact["expired"] and imports_artifact["workflow_run"]["head_sha"] == SHA
imports_path = BASE / "evidence-Windows/mvp2/Windows-voice-import-smoke.json"
native_import_verification = {
    "file": str(imports_path.relative_to(BASE)),
    "sha256": filehash(imports_path),
    **verify_native_import_report(read(imports_path), voice_package_bytes, expected_packages),
}
for field in ("runtime_packages", "installed_packages"):
    records = resources[field]
    assert len(records) == len(expected_packages)
    assert {item["name"]: item["version"] for item in records} == expected_packages, field
expected_archives = {
    "uv-x86_64-pc-windows-msvc.zip": {
        "bytes": 23530194,
        "sha256": "c84629a56e0706b69a47ea35862208af827cb6fbfa1d0ca763c52c67594637e8",
    },
    "sherpa-onnx-sense-voice-zh-en-ja-ko-yue-int8-2024-07-17.tar.bz2": {
        "bytes": 163002883,
        "sha256": "7d1efa2138a65b0b488df37f8b89e3d91a60676e416f515b952358d83dfd347e",
    },
    "kokoro-int8-multi-lang-v1_1.tar.bz2": {
        "bytes": 147031220,
        "sha256": "a1e94694776049035c4f2c6529f003aaece993c76aae9a78995831c3c4dcafc6",
    },
}
assert len(resources["download_archives"]) == 3
assert {
    item["name"]: {"bytes": item["bytes"], "sha256": item["sha256"]}
    for item in resources["download_archives"]
} == expected_archives
assert resources["manifest"] == {
    "schema": 1,
    "python": "3.11.15",
    "lock": digest(voice_package_bytes["uv.lock"]),
    "asr": expected_archives["sherpa-onnx-sense-voice-zh-en-ja-ko-yue-int8-2024-07-17.tar.bz2"][
        "sha256"
    ],
    "tts": expected_archives["kokoro-int8-multi-lang-v1_1.tar.bz2"]["sha256"],
}
assert resources["worker_sha256"] == digest(voice_package_bytes["worker.py"])
assert resources["model_files_sha256"] == digest(voice_package_bytes["model_files.json"])
assert len(resources["models"]) == 2
assert {
    item["name"]: (item["model_bytes"], item["model_sha256"]) for item in resources["models"]
} == {
    "sherpa-onnx-sense-voice-zh-en-ja-ko-yue-int8-2024-07-17": (
        239233841,
        "c71f0ce00bec95b07744e116345e33d8cbbe08cef896382cf907bf4b51a2cd51",
    ),
    "kokoro-int8-multi-lang-v1_1": (
        114299010,
        "bda15858163726a492d02a9a727bc263551b86ac77f90812c4b30ff41d380e26",
    ),
}

sentence = "你好，我是小猫。今天我们一起学习，让生活更有趣。"
synthesis, recognition = free["synthesis"], free["recognition"]
assert synthesis["text"] == sentence and synthesis["characters"] == len(sentence)
for operation in (synthesis, recognition):
    assert operation["timeout_seconds"] == 120
    assert 0 < operation["elapsed_seconds"] <= operation["timeout_seconds"] + 5


def audio_info(raw):
    assert 44 <= len(raw) <= 8 * 1024 * 1024
    with wave.open(io.BytesIO(raw), "rb") as stream:
        assert stream.getnchannels() == 1 and stream.getsampwidth() == 2
        assert stream.getcomptype() == "NONE"
        rate, frames = stream.getframerate(), stream.getnframes()
        assert 8000 <= rate <= 48000 and 0 < frames <= rate * 60
        pcm = stream.readframes(frames)
        assert len(pcm) == frames * 2
    samples = struct.unpack(f"<{frames}h", pcm)
    return {
        "mime_type": "audio/wav",
        "channels": 1,
        "sample_rate": rate,
        "bits_per_sample": 16,
        "frames": frames,
        "duration_seconds": round(frames / rate, 4),
        "bytes": len(raw),
        "sha256": digest(raw),
        "peak": max(abs(sample) for sample in samples),
        "rms": round(math.sqrt(sum(sample * sample for sample in samples) / frames), 4),
    }, samples


audio_name = synthesis["audio"]["file"]
assert Path(audio_name).name == audio_name and audio_name == "free-voice-smoke-speech.wav"
voice_wav = EVIDENCE / audio_name
raw_wav = voice_wav.read_bytes()
wav_metadata, samples = audio_info(raw_wav)
assert {key: synthesis["audio"][key] for key in wav_metadata} == wav_metadata
assert wav_metadata["sample_rate"] == 24000
assert wav_metadata["peak"] > 0 and wav_metadata["rms"] > 0
# Independently reconstruct the exact bounded 24 kHz -> 16 kHz box-filtered
# upload, then check the report's ASR input hash and all audio metadata.
ratio = wav_metadata["sample_rate"] / 16000
count = max(1, math.floor(len(samples) / ratio))
pcm16 = bytearray(count * 2)
for index in range(count):
    position, end = index * ratio, min(len(samples), (index + 1) * ratio)
    value = sum(
        samples[source] * (min(end, source + 1) - max(position, source))
        for source in range(math.floor(position), math.ceil(end))
    ) / (end - position)
    struct.pack_into("<h", pcm16, index * 2, max(-32768, min(32767, round(value))))
converted = io.BytesIO()
with wave.open(converted, "wb") as stream:
    stream.setparams((1, 2, 16000, count, "NONE", "not compressed"))
    stream.writeframes(pcm16)
asr_metadata, _ = audio_info(converted.getvalue())
assert recognition["input_audio"] == asr_metadata
assert recognition["characters"] == len(recognition["text"])
left = "".join(char.lower() for char in sentence if char.isalnum())
right = "".join(char.lower() for char in recognition["text"] if char.isalnum())
assert left and right
distance = list(range(len(right) + 1))
for row, source in enumerate(left, 1):
    current = [row]
    for column, target in enumerate(right, 1):
        current.append(
            min(current[-1] + 1, distance[column] + 1, distance[column - 1] + (source != target))
        )
    distance = current
cer = distance[-1] / len(left)
match = recognition["match"]
assert match == {
    "expected_normalized_characters": len(left),
    "recognized_normalized_characters": len(right),
    "edit_distance": distance[-1],
    "character_error_rate": round(cer, 4),
    "maximum_character_error_rate": 0.35,
    "passed": True,
    "scope": "one_fixed_synthesized_chinese_sentence_not_microphone_quality",
}
assert cer <= 0.35
restart = free["restart"]
assert restart["first_pid"] != restart["second_pid"]
assert restart["first_pid"] > 0 and restart["second_pid"] > 0
assert restart["ready_without_redownload"] is True
voice_wav_verified = {"file": audio_name, **wav_metadata}
free_voice_verification = {
    "status": "PASS",
    "cases": 11,
    "scope": scope,
    "environment": environment,
    "installation": install,
    "installed_resources": resources,
    "configuration": configuration,
    "synthesis": synthesis,
    "recognition": recognition,
    "restart": restart,
    "wav_metadata_recomputed": True,
    "asr_input_resampling_hash_recomputed": True,
    "character_error_rate_recomputed": True,
}
result = {
    "status": "PASS",
    "verified_utc": datetime.now(timezone.utc).isoformat(),
    "source_commit": SHA,
    "source_clean": True,
    "ci_run": RUN,
    "ci_url": run["url"],
    "workflow_conclusion": run["conclusion"],
    "jobs": [{"name": j["name"], "conclusion": j["conclusion"]} for j in run["jobs"]],
    "download_kind": "GitHub Actions artifact; tagged release publication was skipped in this run",
    "verifier": {"path": str(Path(__file__).relative_to(ROOT)), "sha256": filehash(Path(__file__))},
    "artifact": {
        "id": artifact["id"],
        "url": f"https://github.com/FrigidCrow/ai-neko/actions/runs/{RUN}/artifacts/{artifact['id']}",
        "expires_at": artifact["expires_at"],
        "requires_github_login": True,
    },
    "version": build["version"],
    "archive": archive.name,
    "archive_bytes": archive.stat().st_size,
    "archive_sha256": archive_hash,
    "zip_crc": "PASS",
    "inner_outer_build_info_match": True,
    "source_inputs_verified_with_windows_checkout_line_endings": True,
    "source_input_count": len(build["source"]["input_sha256"]),
    "windows_crlf_inputs": sorted(set(windows_crlf_inputs)),
    "lockfiles": {
        "uv.lock": build["lockfile"]["sha256"],
        "desktop/package-lock.json": build["desktop"]["package_lock_sha256"],
    },
    "build_environment": build["build"],
    "source_tests": source_tests,
    "desktop_unit_passed": args.desktop_passed,
    "packaged_reports": reports,
    "guide_reports": guide_reports,
    "desktop_unit_logs": desktop_unit_logs,
    "license_files_verified": license_files,
    "desktop_assets_verified": len(assets["files"]),
    "g6_packaged": {
        "status": g6["status"],
        "case_count": len(g6["cases"]),
        "processes": len(g6["launches"]),
        "questions_observed": len(g6["questions_observed"]),
        "synthetic_calls": g6["synthetic_calls"],
    },
    "executables": executables,
    "memory_reuse": build["memory_reuse"],
    "screenshots": screenshots,
    "plain_request_revocation": companion["plain_request_revocation"],
    "persona_save_wait": persona_save_wait,
    "audio_stop_benchmark": benchmark,
    "synthetic_calls": companion["synthetic_calls"],
    "real_cloud_service_calls": 0,
    "actual_user_microphone_captures": 0,
    "actual_desktop_captures": 0,
    "windows_11": "NOT_VERIFIED",
    "local_windows_executable_run": False,
    "mvp2_g1_g6": "LOCAL_AND_WINDOWS_CI_VERIFIED; REAL_CHAT_SERVICES_AND_WINDOWS_11_NOT_VERIFIED",
    "free_voice": free_voice_verification,
    "native_voice_imports": native_import_verification,
    "local_voice_runtime_package_members": voice_package_members,
    "fixed_synthetic_voice_wav": voice_wav_verified,
}
output = BASE / "windows-download-verification.json"
output.write_text(json.dumps(result, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
print(
    json.dumps(
        {
            k: result[k]
            for k in (
                "status",
                "source_commit",
                "version",
                "archive_sha256",
                "artifact",
                "packaged_reports",
                "native_voice_imports",
                "audio_stop_benchmark",
            )
        },
        ensure_ascii=False,
        indent=2,
    )
)
