import json
import sys
import uuid

import pytest

from ai_neko.config import credentials
from ai_neko.config.paths import initialize_data_root
from ai_neko.config.providers import ProviderStore


class FakeVault:
    values = {}

    def get(self, target):
        return self.values.get(target)

    def set(self, target, value):
        if value is None:
            self.values.pop(target, None)
        else:
            self.values[target] = value


@pytest.fixture
def store(tmp_path, monkeypatch):
    monkeypatch.delenv("AI_NEKO_MODEL_API_KEY", raising=False)
    monkeypatch.delenv("AI_NEKO_SEARCH_API_KEY", raising=False)
    monkeypatch.setattr(credentials, "WindowsVault", FakeVault)
    return ProviderStore(initialize_data_root(tmp_path / "ai-neko-test"))


def test_provider_config_no_plaintext_credentials_and_reload(store):
    result = store.update(
        {
            "model": "test-model",
            "model_api_key": "synthetic-model-key",
            "search_api_key": "synthetic-search-key",
        }
    )
    serialized = json.dumps(result)
    assert "synthetic-" not in serialized
    assert result["model_key_set"] and result["search_key_set"]
    saved = (store.paths.config / "providers.json").read_text()
    assert "synthetic-" not in saved and "api_key" not in saved
    assert not list(store.paths.config.glob("*.tmp"))
    assert ProviderStore(store.paths).public_config()["model"] == "test-model"
    assert store.model()._key == "synthetic-model-key"
    assert store.web_tools()._key == "synthetic-search-key"


def test_blank_key_preserves_clear_removes(store):
    store.update({"model_api_key": "synthetic-model-key"})
    assert store.update({"model_api_key": ""})["model_key_set"]
    assert not store.update({"clear_model_api_key": True})["model_key_set"]


def test_data_roots_and_standard_environment_are_isolated(store, tmp_path, monkeypatch):
    monkeypatch.setenv("OPENAI_API_KEY", "other-app-key")
    assert not store.public_config()["model_key_set"]
    store.update({"model_api_key": "ai-neko-key"})
    other = ProviderStore(initialize_data_root(tmp_path / "other-root"))
    assert not other.public_config()["model_key_set"]
    assert credentials.namespace(store.paths.root) != credentials.namespace(other.paths.root)
    assert credentials.namespace(store.paths.root).startswith("ai-neko/providers/")


def test_only_application_environment_key_and_explicit_clear(store, monkeypatch):
    monkeypatch.setenv("AI_NEKO_SEARCH_API_KEY", "app-env-key")
    assert store.public_config()["search_key_set"]
    store.update({"clear_search_api_key": True})
    assert not store.public_config()["search_key_set"]


@pytest.mark.parametrize(
    "change",
    [
        {"model_base_url": "https://user:secret@example.com/v1"},
        {"search_base_url": "http://example.com"},
        {"search_base_url": "https://127.0.0.1"},
        {"search_base_url": "https://localhost"},
        {"model_base_url": "http://example.com"},
        {"model_base_url": "https://10.0.0.1/v1"},
        {"model_base_url": "https://example.com?key=secret"},
        {"model_base_url": "https://example.com#fragment"},
        {"model": "model\n"},
        {"model_api_key": "secret\n"},
        {"model_api_key": "x", "clear_model_api_key": True},
        {"clear_model_api_key": "true"},
        {"unknown": "x"},
    ],
)
def test_invalid_configuration_rejected_without_changes(store, change):
    before = store.public_config()
    with pytest.raises(ValueError):
        store.update(change)
    assert store.public_config() == before
    assert not (store.paths.config / "providers.json").exists()


def test_local_models_allowed(store):
    for base in ("http://127.0.0.1:11434/v1", "http://localhost:11434/v1", "http://[::1]:11434/v1"):
        assert store.update({"model_base_url": base})["model_base_url"] == base


def test_credential_write_failure_does_not_save_config(store, monkeypatch):
    def fail(*args):
        raise credentials.CredentialError("credential_write_failed")

    monkeypatch.setattr(store._credentials, "set", fail)
    with pytest.raises(ValueError, match="credential_write_failed"):
        store.update({"model": "new-model", "model_api_key": "secret"})
    assert store.public_config()["model"] == ""
    assert not (store.paths.config / "providers.json").exists()


def test_reject_secret_in_file_and_wrong_owner(store):
    path = store.paths.config / "providers.json"
    path.write_text(
        json.dumps({"app_id": "ai-neko", "schema_version": 1, "providers": {"api_key": "secret"}})
    )
    with pytest.raises(ValueError, match="invalid_provider_config"):
        ProviderStore(store.paths)


@pytest.mark.skipif(sys.platform != "win32", reason="Windows Credential Manager requires Windows")
def test_windows_vault_real_roundtrip_and_delete():
    vault = credentials.WindowsVault()
    target = "ai-neko/test-synthetic/" + uuid.uuid4().hex
    try:
        vault.set(target, "synthetic-test-only")
        assert vault.get(target) == "synthetic-test-only"
        vault.set(target, None)
        assert vault.get(target) is None
    finally:
        vault.set(target, None)


def test_atomic_file_failure_rolls_back_changed_credentials(store, monkeypatch):
    store.update({"model_api_key": "previous-key"})

    def fail(*args):
        raise ValueError("provider_config_write_failed")

    monkeypatch.setattr(store, "_save", fail)
    with pytest.raises(ValueError):
        store.update({"model": "new-model", "model_api_key": "new-key"})
    assert store.model()._key == "previous-key"
    assert store.public_config()["model"] == ""
