"""Independent atomic provider preferences and redacted public configuration."""

from __future__ import annotations

import hashlib
import ipaddress
import json
import os
import tempfile
import threading
from pathlib import Path

from ai_neko.config.credentials import Credentials
from ai_neko.config.paths import DataPaths, safe_child
from ai_neko.providers import ModelAdapter
from ai_neko.tools import network
from ai_neko.tools.search_cache import SearchCache
from ai_neko.tools.web import WebTools

DEFAULTS = {
    "model_base_url": "https://api.openai.com/v1",
    "model": "",
    "search_provider": "tavily",
    "search_base_url": "https://api.tavily.com",
}

SEARCH_BASE_URLS = {
    "tavily": "https://api.tavily.com",
    "anysearch": "https://api.anysearch.com/v1",
}


def endpoint(value, *, model=False):
    url = network.parse_url(value)
    if url.query or url.fragment or "#" in value:
        raise ValueError("invalid_endpoint")
    local = model and network.explicit_loopback(url.host)
    if url.scheme != "https" and not local:
        raise ValueError("https_required")
    try:
        ipaddress.ip_address(url.host)
    except ValueError:
        if (
            url.host.lower() in {"localhost", "localhost.localdomain"}
            or url.host.lower().endswith((".localhost", ".local", ".internal"))
        ) and not local:
            raise ValueError("blocked_endpoint") from None
    else:
        if not local and not network.public_ip(url.host):
            raise ValueError("blocked_endpoint")
    return str(url).rstrip("/")


def validate(config):
    # Schema 1 and older explicit CLI callers used exactly these three fields.
    # Only that known shape is upgraded; unknown fields stay invalid.
    if isinstance(config, dict) and set(config) == set(DEFAULTS) - {"search_provider"}:
        config = {**config, "search_provider": "tavily"}
    if not isinstance(config, dict) or set(config) != set(DEFAULTS):
        raise ValueError("invalid_provider_config")
    provider = config["search_provider"]
    if not isinstance(provider, str) or provider not in SEARCH_BASE_URLS:
        raise ValueError("invalid_search_provider")
    result = {
        "model_base_url": endpoint(config["model_base_url"], model=True),
        "search_provider": provider,
        "search_base_url": endpoint(config["search_base_url"]),
    }
    model = config["model"]
    if not isinstance(model, str) or len(model) > 200 or any(ord(c) < 32 for c in model):
        raise ValueError("invalid_model")
    result["model"] = model.strip()
    return result


class ProviderStore:
    def __init__(self, paths: DataPaths):
        self.paths = paths
        self._lock = threading.RLock()
        self._credentials = Credentials(paths.root)
        self._config = dict(DEFAULTS)
        self.search_cache = SearchCache()
        self._search_identity = None
        path = safe_child(paths.config, "providers.json")
        if path.exists():
            try:
                if path.stat().st_size > 16_384:
                    raise ValueError
                saved = json.loads(path.read_text(encoding="utf-8"))
                if (
                    not isinstance(saved, dict)
                    or saved.get("app_id") != "ai-neko"
                    or (
                        type(saved.get("schema_version")) is not int or saved["schema_version"] != 1
                    )
                    or set(saved) != {"app_id", "schema_version", "providers"}
                ):
                    raise ValueError
                self._config = validate(saved["providers"])
            except (OSError, ValueError, TypeError):
                raise ValueError("invalid_provider_config") from None

    def public_config(self) -> dict:
        with self._lock:
            search_key_set = bool(self._credentials.get("search"))
            return {
                **self._config,
                "model_key_set": bool(self._credentials.get("model")),
                "search_key_set": search_key_set,
                "search_configured": self._config["search_provider"] == "anysearch"
                or search_key_set,
                "credential_storage": self._credentials.storage,
            }

    def update(self, changes: dict) -> dict:
        with self._lock:
            allowed = set(DEFAULTS) | {
                "model_api_key",
                "search_api_key",
                "clear_model_api_key",
                "clear_search_api_key",
            }
            if not isinstance(changes, dict) or set(changes) - allowed:
                raise ValueError("invalid_provider_fields")
            fields = {key: value for key, value in changes.items() if key in DEFAULTS}
            provider = fields.get("search_provider")
            if (
                isinstance(provider, str)
                and provider in SEARCH_BASE_URLS
                and provider != self._config["search_provider"]
                and "search_base_url" not in fields
            ):
                fields["search_base_url"] = SEARCH_BASE_URLS[provider]
            updated = validate(
                {
                    **self._config,
                    **fields,
                }
            )
            secrets = {}
            for kind in ("model", "search"):
                key = changes.get(kind + "_api_key", "")
                clear = changes.get("clear_" + kind + "_api_key", False)
                if (
                    not isinstance(key, str)
                    or len(key) > 2000
                    or any(ord(char) < 33 or ord(char) > 126 for char in key)
                    or type(clear) is not bool
                    or (clear and key)
                ):
                    raise ValueError("invalid_credential")
                if clear or key:
                    secrets[kind] = None if clear else key
            # Failure is explicit; never silently fall back to plaintext credential storage.
            # Roll back to the previous *stored* state: when nothing was stored,
            # delete instead of persisting an environment fallback into the vault.
            previous = {
                kind: (self._credentials.get(kind), self._credentials.has(kind)) for kind in secrets
            }
            changed = []
            try:
                for kind, secret in secrets.items():
                    self._credentials.set(kind, secret)
                    changed.append(kind)
                self._save(updated)
            except (OSError, ValueError):
                for kind in reversed(changed):
                    value, was_stored = previous[kind]
                    self._credentials.set(kind, value if was_stored else None)
                raise
            self._config = updated
            if any(key in changes for key in DEFAULTS) or "search" in secrets:
                self.search_cache.advance_generation()
                self._search_identity = None
            return self.public_config()

    def _save(self, config):
        destination = safe_child(self.paths.config, "providers.json")
        fd, temporary = tempfile.mkstemp(prefix=".providers-", suffix=".tmp", dir=self.paths.config)
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as handle:
                json.dump(
                    {"app_id": "ai-neko", "schema_version": 1, "providers": config},
                    handle,
                    ensure_ascii=False,
                    indent=2,
                )
                handle.write("\n")
                handle.flush()
                os.fsync(handle.fileno())
            Path(temporary).chmod(0o600)
            os.replace(temporary, destination)
        except OSError:
            raise ValueError("provider_config_write_failed") from None
        finally:
            Path(temporary).unlink(missing_ok=True)

    def model(self) -> ModelAdapter:
        with self._lock:
            return ModelAdapter(self._config, self._credentials.get("model"))

    def web_tools(self, *, scope: str | None = None, locale: str = "zh-CN") -> WebTools:
        with self._lock:
            provider = self._config["search_provider"]
            key = self._credentials.get("search") if provider == "tavily" else None
            # Credentials may also come from this application's environment. Only a
            # one-way identity is kept alongside the cache, never the credential.
            identity = hashlib.sha256(
                json.dumps([provider, self._config["search_base_url"], key]).encode()
            ).digest()
            if self._search_identity is not None and identity != self._search_identity:
                self.search_cache.advance_generation()
            self._search_identity = identity
            return WebTools(
                self._config,
                key,
                search_cache=self.search_cache,
                scope=scope if scope is not None else str(self.paths.root),
                generation=self.search_cache.generation,
                locale=locale,
            )

    def invalidate_search_cache(self, scope: str | None = None) -> None:
        self.search_cache.invalidate(scope)

    async def close_search_cache(self) -> None:
        await self.search_cache.close()
