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
        {"search_provider": "unknown"},
        {"search_provider": None},
        {"search_provider": ["anysearch"]},
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


def test_schema_one_three_field_config_remains_tavily_and_upgrades_on_save(store):
    path = store.paths.config / "providers.json"
    original = {
        "model_base_url": "https://model.example/v1",
        "model": "synthetic-model",
        "search_base_url": "https://search.example",
    }
    path.write_text(
        json.dumps({"app_id": "ai-neko", "schema_version": 1, "providers": original}),
        encoding="utf-8",
    )
    reloaded = ProviderStore(store.paths)
    public = reloaded.public_config()
    assert public["search_provider"] == "tavily"
    assert not public["search_configured"]
    assert {key: public[key] for key in original} == original
    # A read does not rewrite old preferences; the next explicit save adds the field.
    assert json.loads(path.read_text())["providers"] == original
    reloaded.update({"model": "another-synthetic-model"})
    assert json.loads(path.read_text())["providers"]["search_provider"] == "tavily"


@pytest.mark.parametrize("extra", [{"api_key": "secret"}, {"search_backend": "anysearch"}])
def test_legacy_config_does_not_upgrade_unknown_fields(store, extra):
    config = {
        "model_base_url": "https://model.example/v1",
        "model": "synthetic-model",
        "search_base_url": "https://search.example",
        **extra,
    }
    (store.paths.config / "providers.json").write_text(
        json.dumps({"app_id": "ai-neko", "schema_version": 1, "providers": config}),
        encoding="utf-8",
    )
    with pytest.raises(ValueError, match="invalid_provider_config"):
        ProviderStore(store.paths)


def test_anysearch_selection_persists_without_search_key_and_uses_provider_default(store):
    assert store.public_config()["search_provider"] == "tavily"
    assert not store.public_config()["search_configured"]
    public = store.update({"search_provider": "anysearch"})
    assert public["search_base_url"] == "https://api.anysearch.com/v1"
    assert public["search_configured"] and not public["search_key_set"]
    reloaded = ProviderStore(store.paths)
    assert reloaded.public_config() == public
    assert reloaded.web_tools()._key is None
    changed = reloaded.update({"search_provider": "tavily"})
    assert changed["search_base_url"] == "https://api.tavily.com"
    assert not changed["search_configured"]


def test_explicit_search_base_survives_provider_selection_and_unrelated_updates(store):
    public = store.update(
        {"search_provider": "anysearch", "search_base_url": "https://search.example/v1"}
    )
    assert public["search_base_url"] == "https://search.example/v1"
    assert store.update({"model": "test"})["search_base_url"] == "https://search.example/v1"
    assert (
        store.update({"search_provider": "anysearch"})["search_base_url"]
        == public["search_base_url"]
    )


def test_anysearch_never_loads_saved_or_environment_key_into_web_tools(store, monkeypatch):
    monkeypatch.setenv("AI_NEKO_SEARCH_API_KEY", "synthetic-environment-key")
    assert store.public_config()["search_configured"]
    store.update({"search_api_key": "synthetic-saved-key", "search_provider": "anysearch"})
    assert store.public_config()["search_key_set"]
    assert store.public_config()["search_configured"]
    assert store.web_tools()._key is None
    assert "synthetic-" not in json.dumps(store.public_config())
    assert "synthetic-" not in (store.paths.config / "providers.json").read_text()
    # An explicit switch back restores the user's stored Tavily credential.
    store.update({"search_provider": "tavily"})
    assert store.web_tools()._key == "synthetic-saved-key"
