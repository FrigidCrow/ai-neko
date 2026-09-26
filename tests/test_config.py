"""Isolation contracts, using only temporary synthetic filesystem roots."""

import copy
import importlib
import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

from ai_neko.config.paths import (
    MARKER,
    SUBDIRECTORIES,
    DataRootError,
    initialize_data_root,
    resolve_data_root,
)
from ai_neko.config.settings import DEFAULT_CONFIG, Settings, initialize_config


@pytest.mark.usefixtures("sandbox_compatible")
def test_isolated_layout_reopens_without_touching_sibling(tmp_path):
    unrelated = tmp_path / "N.E.K.O"
    unrelated.mkdir()
    original = unrelated / "original.txt"
    original.write_text("synthetic original", encoding="utf-8")
    paths = initialize_data_root(tmp_path / "独立 ai-neko")
    assert {p.name for p in paths.root.iterdir()} == set(SUBDIRECTORIES) | {MARKER}
    assert initialize_data_root(paths.root) == paths
    assert original.read_text(encoding="utf-8") == "synthetic original"
    assert not (unrelated / MARKER).exists()


def test_override_only_uses_dedicated_environment(monkeypatch, tmp_path):
    monkeypatch.setenv("AI_NEKO_DATA_DIR", str(tmp_path / "chosen"))
    monkeypatch.setenv("NEKO_DATA_DIR", str(tmp_path / "N.E.K.O"))
    assert resolve_data_root() == tmp_path / "chosen"
    assert resolve_data_root(tmp_path / "explicit") == tmp_path / "explicit"


@pytest.mark.parametrize("suffix", ["N.E.K.O", "N.E.K.O/config", "neko-companion", "n.e.k.o"])
def test_original_paths_rejected(tmp_path, suffix):
    with pytest.raises(DataRootError):
        initialize_data_root(tmp_path / suffix)
    assert list(tmp_path.iterdir()) == []


@pytest.mark.parametrize("path", ["relative", "", "/", "\x00bad"])
def test_bad_paths(path):
    with pytest.raises(DataRootError):
        initialize_data_root(path)


def test_user_home_rejected():
    with pytest.raises(DataRootError):
        resolve_data_root(Path.home())


def test_existing_unowned_root_is_not_adopted(tmp_path):
    sentinel = tmp_path / "keep.txt"
    sentinel.write_text("keep", encoding="utf-8")
    with pytest.raises(DataRootError):
        initialize_data_root(tmp_path)
    assert list(tmp_path.iterdir()) == [sentinel]


def test_foreign_or_corrupt_marker(tmp_path):
    root = tmp_path / "test"
    root.mkdir()
    for value in ['{"app_id":"N.E.K.O"}', "broken", "null"]:
        (root / MARKER).write_text(value, encoding="utf-8")
        with pytest.raises(DataRootError):
            initialize_data_root(root)
        assert list(root.iterdir()) == [root / MARKER]


def test_file_data_root(tmp_path):
    path = tmp_path / "file"
    path.write_text("keep", encoding="utf-8")
    with pytest.raises(DataRootError):
        initialize_data_root(path)
    assert path.read_text(encoding="utf-8") == "keep"


def test_symlink_root_or_child_cannot_redirect_writes(tmp_path):
    other = tmp_path / "other"
    other.mkdir()
    link = tmp_path / "link"
    try:
        link.symlink_to(other, target_is_directory=True)
    except OSError:
        if sys.platform == "win32":
            pytest.skip("symlink creation requires Windows Developer Mode or privileges")
        raise
    with pytest.raises(DataRootError):
        initialize_data_root(link)
    paths = initialize_data_root(tmp_path / "own")
    paths.memory.rmdir()
    paths.memory.symlink_to(other, target_is_directory=True)
    with pytest.raises(DataRootError):
        initialize_data_root(paths.root)
    assert list(other.iterdir()) == []


def test_settings_do_not_use_generic_credentials(monkeypatch):
    monkeypatch.delenv("AI_NEKO_MODEL_API_KEY", raising=False)
    monkeypatch.setenv("OPENAI_API_KEY", "synthetic-other-app-key")
    assert Settings.load().model_api_key is None
    monkeypatch.setenv("AI_NEKO_MODEL_API_KEY", "synthetic-own-key")
    settings = Settings.load()
    assert settings.model_api_key == "synthetic-own-key"
    assert "synthetic-own-key" not in repr(settings)


@pytest.mark.parametrize("port", ["abc", "-1", "65536", "1.5"])
def test_invalid_port(monkeypatch, port):
    monkeypatch.setenv("AI_NEKO_PORT", port)
    with pytest.raises(ValueError):
        Settings.load()


def test_dedicated_port_and_explicit_override(monkeypatch):
    monkeypatch.setenv("AI_NEKO_PORT", "12345")
    assert Settings.load().port == 12345
    assert Settings.load(0).port == 0


def test_config_never_persists_key(monkeypatch, tmp_path):
    monkeypatch.setenv("AI_NEKO_MODEL_API_KEY", "synthetic-secret")
    paths = initialize_data_root(tmp_path / "own")
    initialize_config(paths)
    text = (paths.config / "app.json").read_text(encoding="utf-8")
    assert "synthetic-secret" not in text
    assert json.loads(text)["credential_service"] == "ai-neko/model/default"
    initialize_config(paths)


@pytest.mark.parametrize("value", [None, [], "bad", {"app_id": "other"}])
def test_invalid_config_has_clear_error(tmp_path, value):
    paths = initialize_data_root(tmp_path / "own")
    (paths.config / "app.json").write_text(json.dumps(value), encoding="utf-8")
    with pytest.raises(DataRootError):
        initialize_config(paths)


def test_imports_have_no_runtime_side_effects(tmp_path):
    # Fresh interpreter, synthetic HOME, and pyc disabled: imports must not start services
    # or create default data roots. This traverses all ai_neko modules, including graph.
    code = """
import importlib, pkgutil, sys
def audit(event, args):
    if event in ('socket.bind', 'socket.connect'):
        raise AssertionError('network access during import')
sys.addaudithook(audit)
import ai_neko
for module in pkgutil.walk_packages(ai_neko.__path__, ai_neko.__name__ + '.'):
    importlib.import_module(module.name)
"""
    env = {
        **os.environ,
        "AI_NEKO_DATA_DIR": str(tmp_path / "data"),
        "HOME": str(tmp_path),
        "LOCALAPPDATA": str(tmp_path),
        "PYTHONDONTWRITEBYTECODE": "1",
    }
    result = subprocess.run(
        [sys.executable, "-c", code],
        cwd=tmp_path,
        env=env,
        capture_output=True,
        text=True,
        timeout=30,
    )
    assert result.returncode == 0, result.stderr
    assert list(tmp_path.iterdir()) == []


def test_windows_default_has_no_roaming_fallback(monkeypatch, tmp_path):
    module = importlib.import_module("ai_neko.config.paths")
    # Exercise resolution logic only; this is not a Windows filesystem test.
    monkeypatch.setattr(module.sys, "platform", "win32")
    monkeypatch.delenv("LOCALAPPDATA", raising=False)
    monkeypatch.setenv("APPDATA", str(tmp_path / "roaming"))
    with pytest.raises(DataRootError):
        module.default_data_root()
    monkeypatch.setenv("LOCALAPPDATA", str(tmp_path / "local"))
    assert module.default_data_root() == tmp_path / "local" / "ai-neko"


@pytest.mark.parametrize(
    "descriptor",
    [
        [],
        None,
        {
            "app_id": "ai-neko",
            "host": "127.0.0.1",
            "port": 5555,
            "token": "synthetic-secret\ninvalid",
        },
    ],
)
def test_stop_rejects_corrupt_descriptor_without_exposing_token(tmp_path, descriptor):
    from ai_neko.__main__ import stop

    paths = initialize_data_root(tmp_path / "own")
    (paths.runtime / "connection.json").write_text(json.dumps(descriptor), encoding="utf-8")
    with pytest.raises(ValueError, match="no valid ai-neko connection descriptor") as failure:
        stop(str(paths.root))
    assert "synthetic-secret" not in str(failure.value)


@pytest.mark.parametrize("port", [True, False, 123.9, 1.0])
def test_direct_settings_rejects_noninteger_port(port):
    with pytest.raises(ValueError):
        Settings.load(port)


@pytest.mark.parametrize(
    "change",
    [
        {"schema_version": True},
        {"credential_service": "NEKO/shared"},
        {"model": []},
        {
            "model": {
                "protocol": "openai-chat-completions",
                "base_url": {"api_key": "synthetic"},
                "model": None,
            }
        },
        {"model": {"protocol": "openai-chat-completions", "base_url": None, "model": 7}},
    ],
)
def test_configuration_rejects_malformed_types_and_foreign_namespace(tmp_path, change):
    paths = initialize_data_root(tmp_path / "own")
    value = copy.deepcopy(DEFAULT_CONFIG)
    value.update(change)
    (paths.config / "app.json").write_text(json.dumps(value), encoding="utf-8")
    with pytest.raises(DataRootError):
        initialize_config(paths)


@pytest.mark.parametrize(
    "endpoint",
    [
        "ftp://example.invalid",
        "https://user:synthetic@example.invalid/v1",
        "https://example.invalid/v1?api_key=synthetic",
        "https://example.invalid/#fragment",
        "https://example.invalid:notaport",
        "https://",
        "https://example.invalid:0",
    ],
)
def test_config_rejects_invalid_or_credential_bearing_urls(tmp_path, endpoint):
    paths = initialize_data_root(tmp_path / "own")
    value = copy.deepcopy(DEFAULT_CONFIG)
    value["model"]["base_url"] = endpoint
    (paths.config / "app.json").write_text(json.dumps(value), encoding="utf-8")
    with pytest.raises(DataRootError):
        initialize_config(paths)


def test_valid_cloud_config_is_read_without_calling_endpoint(tmp_path):
    paths = initialize_data_root(tmp_path / "own")
    value = copy.deepcopy(DEFAULT_CONFIG)
    value["model"].update({"base_url": "https://example.invalid/v1", "model": "synthetic-model"})
    (paths.config / "app.json").write_text(json.dumps(value), encoding="utf-8")
    initialize_config(paths)


@pytest.mark.parametrize(
    "relative", [MARKER, "runtime/instance.lock", "logs/service.jsonl", "config/app.json"]
)
def test_existing_hardlinks_cannot_modify_other_data(tmp_path, relative):
    from ai_neko.app.server import serve

    paths = initialize_data_root(tmp_path / "own")
    original = tmp_path / "foreign-file"
    original.write_text("must remain unchanged", encoding="utf-8")
    destination = paths.root / relative
    destination.unlink(missing_ok=True)
    os.link(original, destination)
    with pytest.raises(DataRootError, match="hardlinks"):
        if relative == MARKER:
            initialize_data_root(paths.root)
        else:
            serve(paths, Settings())
    assert original.read_text(encoding="utf-8") == "must remain unchanged"


def test_marker_rejects_boolean_schema_version(tmp_path):
    paths = initialize_data_root(tmp_path / "own")
    (paths.root / MARKER).write_text('{"app_id":"ai-neko","schema_version":true}', encoding="utf-8")
    with pytest.raises(DataRootError):
        initialize_data_root(paths.root)


def test_reparse_attribute_detection_is_explicit(monkeypatch, tmp_path):
    from types import SimpleNamespace

    from ai_neko.config.paths import is_redirected

    path = tmp_path / "junction"
    monkeypatch.setattr(
        Path, "lstat", lambda _: SimpleNamespace(st_mode=0o40700, st_file_attributes=0x400)
    )
    assert is_redirected(path)


def test_stop_never_forwards_token_along_redirect(tmp_path):
    from http.server import BaseHTTPRequestHandler, HTTPServer
    from threading import Thread

    from ai_neko.__main__ import stop

    paths = initialize_data_root(tmp_path / "own")
    received = []

    class Redirector(BaseHTTPRequestHandler):
        def do_POST(self):
            received.append(self.path)
            self.send_response(302)
            self.send_header("Location", "/unexpected-token-forwarding")
            self.end_headers()

        def do_GET(self):
            received.append(self.path)
            self.send_response(202)
            self.end_headers()

        def log_message(self, *_args):
            pass

    with HTTPServer(("127.0.0.1", 0), Redirector) as server:
        thread = Thread(target=server.serve_forever, daemon=True)
        thread.start()
        descriptor = {
            "app_id": "ai-neko",
            "host": "127.0.0.1",
            "port": server.server_port,
            "token": "x" * 43,
        }
        (paths.runtime / "connection.json").write_text(json.dumps(descriptor), encoding="utf-8")
        try:
            with pytest.raises(ValueError, match="unavailable or descriptor is stale"):
                stop(str(paths.root))
            assert received == ["/shutdown"]
        finally:
            server.shutdown()
            thread.join(timeout=3)
