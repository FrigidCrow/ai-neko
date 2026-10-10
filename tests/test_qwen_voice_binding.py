"""Synthetic local binding tests: no real models, native imports, or registration."""

from __future__ import annotations

import builtins
import hashlib
import importlib.util
import json
import os
from pathlib import Path

import pytest

from ai_neko.config.paths import initialize_data_root
from ai_neko.media.qwen_runtime import binding


@pytest.fixture
def installed(tmp_path, monkeypatch):
    project = tmp_path / "project"
    python_home = tmp_path / "python-home"
    environment = project / binding.ENVIRONMENT_RELATIVE
    model = project / binding.MODEL_RELATIVE
    prompt = project / binding.PROMPT_RELATIVE
    packages = environment / "Lib" / "site-packages"
    packages.mkdir(parents=True)
    python_home.mkdir()
    (python_home / "python.exe").write_bytes(b"synthetic executable; never execute")
    (environment / "pyvenv.cfg").write_text(
        f"home = {python_home}\nimplementation = CPython\n"
        f"version_info = {binding.PYTHON_VERSION}\ninclude-system-site-packages = false\n",
        encoding="utf-8",
    )
    for name, version in binding.VERSIONS.items():
        metadata = packages / f"{name.replace('-', '_')}-{version}.dist-info" / "METADATA"
        metadata.parent.mkdir()
        metadata.write_text(f"Name: {name}\nVersion: {version}\n", encoding="utf-8")
    model.mkdir(parents=True)
    data = {
        "config.json": json.dumps({"tts_model_type": "base", "tts_model_size": "0b6"}).encode(),
        "model.safetensors": b"synthetic-main-weights",
        "speech_tokenizer/model.safetensors": b"synthetic-tokenizer-weights",
    }
    model_files = {}
    for name, contents in data.items():
        path = model / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(contents)
        model_files[name] = (len(contents), hashlib.sha256(contents).hexdigest())
    prompt_data = b"synthetic known prompt; not a pickle"
    prompt.write_bytes(prompt_data)
    monkeypatch.setattr(binding, "APPROVED_PROJECT", project)
    monkeypatch.setattr(binding, "APPROVED_PYTHON_HOME", python_home)
    monkeypatch.setattr(binding, "APPROVED_PYTHON_TARGET", python_home)
    monkeypatch.setattr(binding, "MODEL_FILES", model_files)
    monkeypatch.setattr(binding, "PROMPT_SHA256", hashlib.sha256(prompt_data).hexdigest())
    monkeypatch.setattr(binding, "PROMPT_BYTES", len(prompt_data))
    paths = initialize_data_root(tmp_path / "app-data")
    return paths, {"environment": environment, "model": model, "prompt": prompt}


def register(installed, **kwargs):
    paths, sources = installed
    return binding.register_binding(paths, **sources, **kwargs)


def mutate_manifest(paths, edit):
    path = paths.assets / "qwen" / "binding.json"
    value = json.loads(path.read_text(encoding="utf-8"))
    edit(value)
    path.write_text(json.dumps(value), encoding="utf-8")


def test_registration_binds_base_interpreter_and_is_native_import_free(installed, monkeypatch):
    original_import = builtins.__import__

    def guard(name, *args, **kwargs):
        assert name.split(".")[0] not in {"torch", "qwen_tts", "transformers"}
        return original_import(name, *args, **kwargs)

    monkeypatch.setattr(builtins, "__import__", guard)
    result = register(installed)
    paths, sources = installed
    assert result["python"] == str(binding.APPROVED_PYTHON_HOME / "python.exe")
    assert result["site_packages"] == str(sources["environment"] / "Lib" / "site-packages")
    assert result["prompt_sha256"] == binding.PROMPT_SHA256
    assert result["idle_unload_seconds"] == 300
    assert result["versions"] == binding.VERSIONS
    assert result == binding.load_binding(paths)
    assert not list((paths.assets / "qwen").glob("*.tmp"))


@pytest.mark.parametrize("fault", [None, "wrong_target", "target_redirect", "parent_redirect"])
def test_only_exact_approved_uv_alias_resolves_to_pinned_real_interpreter(
    installed, monkeypatch, tmp_path, fault
):
    alias = binding.APPROVED_PYTHON_HOME
    target = tmp_path / "pinned-version-python"
    target.mkdir()
    (target / "python.exe").write_bytes(b"synthetic pinned base executable")
    monkeypatch.setattr(binding, "APPROVED_PYTHON_TARGET", target)
    real_resolve, real_redirect = Path.resolve, binding.is_redirected

    def resolve(path, *args, **kwargs):
        if path == alias:
            return tmp_path / "unapproved-target" if fault == "wrong_target" else target
        return real_resolve(path, *args, **kwargs)

    def redirected(path):
        return (
            path == alias
            or (fault == "target_redirect" and path == target)
            or (fault == "parent_redirect" and path == alias.parent)
            or real_redirect(path)
        )

    monkeypatch.setattr(Path, "resolve", resolve)
    monkeypatch.setattr(binding, "is_redirected", redirected)
    if fault:
        with pytest.raises(binding.BindingError):
            register(installed)
    else:
        result = register(installed)
        assert result["python"] == str(target / "python.exe")
        assert result["python"] != str(alias / "python.exe")
        assert binding.load_binding(installed[0]) == result


def test_load_only_reads_small_metadata_never_weight_or_prompt_bytes(installed, monkeypatch):
    result = register(installed)
    paths, sources = installed
    original_open = Path.open

    def guard(path, *args, **kwargs):
        assert path != sources["prompt"]
        assert path.suffix not in {".safetensors", ".exe"}
        return original_open(path, *args, **kwargs)

    monkeypatch.setattr(Path, "open", guard)
    monkeypatch.setattr(binding, "_sha256", lambda *_: pytest.fail("status must not hash files"))
    assert binding.load_binding(paths) == result


def test_missing_binding_is_read_only(installed):
    paths, _ = installed
    with pytest.raises(binding.BindingError):
        binding.load_binding(paths)
    assert not (paths.assets / "qwen").exists()


@pytest.mark.parametrize("name", ["model.safetensors", "speech_tokenizer/model.safetensors"])
def test_registration_rejects_same_size_wrong_weight_digest(installed, name):
    paths, sources = installed
    weight = sources["model"] / name
    weight.write_bytes(b"x" * weight.stat().st_size)
    with pytest.raises(binding.BindingError, match="digest"):
        register(installed)
    assert not (paths.assets / "qwen").exists()


def test_registration_requires_accepted_prompt_digest(installed):
    paths, sources = installed
    sources["prompt"].write_bytes(b"x" * binding.PROMPT_BYTES)
    with pytest.raises(binding.BindingError, match="digest.*prompt"):
        register(installed)
    assert not (paths.assets / "qwen").exists()


@pytest.mark.parametrize("field", ["environment", "model", "prompt"])
def test_registration_rejects_unapproved_existing_locations(installed, tmp_path, field):
    paths, sources = installed
    foreign = tmp_path / "foreign"
    if field == "prompt":
        foreign.write_bytes(b"do not read")
    else:
        foreign.mkdir()
    with pytest.raises(binding.BindingError, match="approved"):
        binding.register_binding(paths, **{**sources, field: foreign})
    assert not (paths.assets / "qwen").exists()


@pytest.mark.parametrize("location", ["relative", "//server/share/python.exe", "\\\\server\\share"])
def test_nonlocal_paths_are_rejected_before_access(installed, location):
    paths, sources = installed
    with pytest.raises(binding.BindingError, match="absolute local"):
        binding.register_binding(paths, **{**sources, "environment": Path(location)})


def test_redirected_source_ancestor_is_rejected(installed, monkeypatch):
    paths, sources = installed
    redirected = sources["model"].parent
    real_check = binding.is_redirected
    monkeypatch.setattr(
        binding, "is_redirected", lambda path: path == redirected or real_check(path)
    )
    with pytest.raises(binding.BindingError, match="reparse"):
        register(installed)
    assert not (paths.assets / "qwen").exists()


def test_redirected_managed_ancestor_is_rejected(installed, monkeypatch):
    register(installed)
    paths, _ = installed
    real_check = binding.is_redirected
    monkeypatch.setattr(
        binding, "is_redirected", lambda path: path == paths.root or real_check(path)
    )
    with pytest.raises(binding.BindingError, match="reparse"):
        binding.load_binding(paths)


def test_unmarked_foreign_qwen_directory_is_not_adopted(installed):
    paths, _ = installed
    root = paths.assets / "qwen"
    root.mkdir()
    foreign = root / "foreign.txt"
    foreign.write_text("keep", encoding="utf-8")
    with pytest.raises(binding.BindingError, match="foreign"):
        register(installed)
    assert foreign.read_text(encoding="utf-8") == "keep"
    assert not (root / ".owner.json").exists()


@pytest.mark.parametrize("marker", [".owner.json", "binding.json"])
def test_hardlinked_managed_json_is_rejected(installed, tmp_path, marker):
    register(installed)
    paths, _ = installed
    target = paths.assets / "qwen" / marker
    os.link(target, tmp_path / "other-link.json")
    with pytest.raises(binding.BindingError):
        binding.load_binding(paths)


@pytest.mark.parametrize(
    "bad",
    [b"[]", b"{", b"x" * (binding.MAX_BINDING_BYTES + 1)],
    ids=["array", "invalid-json", "oversized"],
)
def test_malformed_or_oversize_manifest_is_rejected(installed, bad):
    register(installed)
    paths, _ = installed
    (paths.assets / "qwen" / "binding.json").write_bytes(bad)
    with pytest.raises(binding.BindingError):
        binding.load_binding(paths)


def test_duplicate_json_keys_are_rejected(installed):
    register(installed)
    paths, _ = installed
    path = paths.assets / "qwen" / "binding.json"
    text = path.read_text(encoding="utf-8")
    path.write_text(text.replace('"schema_version": 1', '"schema_version": 1, "schema_version": 1'))
    with pytest.raises(binding.BindingError, match="duplicate"):
        binding.load_binding(paths)


@pytest.mark.parametrize(
    "edit",
    [
        lambda value: value.update(extra="not allowed"),
        lambda value: value.update(python="C:/unapproved/python.exe"),
        lambda value: value.update(site_packages="C:/unapproved/site-packages"),
        lambda value: value.update(schema_version=True),
        lambda value: value.update(model_revision="foreign"),
        lambda value: value.update(prompt_sha256="0" * 64),
        lambda value: value.update(versions={"torch": "foreign"}),
        lambda value: value["files"].pop("prompt"),
        lambda value: value["files"]["prompt"].update(sha256="0" * 64),
        lambda value: value["files"]["python"].update(size=True),
    ],
)
def test_manifest_pins_and_inventory_cannot_be_changed(installed, edit):
    register(installed)
    paths, _ = installed
    mutate_manifest(paths, edit)
    with pytest.raises(binding.BindingError):
        binding.load_binding(paths)


@pytest.mark.parametrize("source", ["prompt", "python", "model/model.safetensors", "package/torch"])
def test_changed_sources_require_registration(installed, source):
    result = register(installed)
    path = Path(result["files"][source]["path"])
    path.write_bytes(path.read_bytes() + b"changed")
    with pytest.raises(binding.BindingError, match="changed"):
        binding.load_binding(installed[0])


def test_registration_checks_metadata_versions_without_import(installed):
    _, sources = installed
    metadata = sources["environment"] / "Lib/site-packages/torch-2.10.0+cu128.dist-info/METADATA"
    metadata.write_text("Name: torch\nVersion: 0.0.0\n", encoding="utf-8")
    with pytest.raises(binding.BindingError, match="pinned installed distribution"):
        register(installed)


def test_registration_requires_base_model_even_with_matching_test_digest(installed, monkeypatch):
    _, sources = installed
    config = sources["model"] / "config.json"
    data = b'{"tts_model_type":"voice_design","tts_model_size":"1b7"}'
    config.write_bytes(data)
    monkeypatch.setitem(
        binding.MODEL_FILES, "config.json", (len(data), hashlib.sha256(data).hexdigest())
    )
    with pytest.raises(binding.BindingError, match="Base 0.6B"):
        register(installed)


@pytest.mark.parametrize("idle", [0, 601, True, 1.5, "60"])
def test_invalid_idle_timeout_is_rejected_before_creating_root(installed, idle):
    with pytest.raises(binding.BindingError, match="idle_unload_seconds"):
        register(installed, idle_unload_seconds=idle)
    assert not (installed[0].assets / "qwen").exists()


def test_registration_can_update_idle_timeout(installed):
    register(installed)
    result = register(installed, idle_unload_seconds=120)
    assert result["idle_unload_seconds"] == 120
    assert binding.load_binding(installed[0])["idle_unload_seconds"] == 120


def test_foreign_pyvenv_home_is_rejected(installed, tmp_path):
    _, sources = installed
    config = sources["environment"] / "pyvenv.cfg"
    config.write_text(f"home = {tmp_path}\n", encoding="utf-8")
    with pytest.raises(binding.BindingError, match="approved isolated Python"):
        register(installed)


def test_cli_registers_synthetic_runtime_and_reports_no_inference(installed, capsys):
    file = Path(__file__).parents[1] / "scripts/register_qwen_runtime.py"
    spec = importlib.util.spec_from_file_location("register_qwen_runtime_test", file)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    paths, sources = installed
    assert (
        module.main(
            [
                "--data-dir",
                str(paths.root),
                "--environment",
                str(sources["environment"]),
                "--model",
                str(sources["model"]),
                "--prompt",
                str(sources["prompt"]),
                "--idle-unload-seconds",
                "30",
            ]
        )
        == 0
    )
    output = json.loads(capsys.readouterr().out)
    assert output["state"] == "registered"
    assert output["inference_run"] is False
    assert binding.load_binding(paths)["idle_unload_seconds"] == 30
