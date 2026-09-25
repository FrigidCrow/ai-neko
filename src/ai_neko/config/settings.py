"""M0 settings. Cloud adapters and OS credential access are later-stage work."""

from __future__ import annotations

import json
import os
import re
from dataclasses import dataclass, field
from urllib.parse import urlsplit

from ai_neko.config.paths import DataPaths, DataRootError, safe_child

DEFAULT_CONFIG = {
    "schema_version": 1,
    "app_id": "ai-neko",
    "model": {"protocol": "openai-chat-completions", "base_url": None, "model": None},
    "credential_service": "ai-neko/model/default",
}


@dataclass(frozen=True)
class Settings:
    port: int = 0
    model_api_key: str | None = field(default=None, repr=False)

    @classmethod
    def load(cls, port: int | None = None) -> Settings:
        raw_port = port if port is not None else os.environ.get("AI_NEKO_PORT", "0")
        if not (
            type(raw_port) is int or isinstance(raw_port, str) and re.fullmatch(r"[0-9]+", raw_port)
        ):
            raise ValueError("port must be an integer from 0 to 65535")
        try:
            selected = int(raw_port)
        except (TypeError, ValueError) as exc:
            raise ValueError("AI_NEKO_PORT must be an integer from 0 to 65535") from exc
        if not 0 <= selected <= 65535:
            raise ValueError("port must be from 0 to 65535")
        return cls(port=selected, model_api_key=os.environ.get("AI_NEKO_MODEL_API_KEY"))


def initialize_config(paths: DataPaths) -> None:
    config = safe_child(paths.config, "app.json")
    try:
        if not config.exists():
            with config.open("x", encoding="utf-8") as handle:
                json.dump(DEFAULT_CONFIG, handle, ensure_ascii=False, indent=2)
                handle.write("\n")
            config.chmod(0o600)
        existing = json.loads(config.read_text(encoding="utf-8"))
        if not isinstance(existing, dict) or existing.get("app_id") != "ai-neko":
            raise DataRootError("invalid app configuration identity")
        if type(existing.get("schema_version")) is not int or existing["schema_version"] != 1:
            raise DataRootError("unsupported app configuration version")
        if existing.get("credential_service") != "ai-neko/model/default":
            raise DataRootError("credential service must use the ai-neko default namespace")
        if not isinstance(existing.get("model"), dict):
            raise DataRootError("model configuration must be an object")
        if existing.get("model", {}).get("protocol") != "openai-chat-completions":
            raise DataRootError("unsupported model protocol")
        # Credentials never belong in this file, including unused provider settings.
        if set(existing) != set(DEFAULT_CONFIG) or set(existing["model"]) != {
            "protocol",
            "base_url",
            "model",
        }:
            raise DataRootError(
                "unknown configuration keys; credentials must use the dedicated environment variable"
            )
        model = existing["model"]
        for name in ("model", "base_url"):
            value = model[name]
            if value is not None and (
                not isinstance(value, str) or not value.strip() or any(ord(c) < 32 for c in value)
            ):
                raise DataRootError("model name and URL must be nonempty strings or null")
        if model["base_url"] is not None:
            endpoint = urlsplit(model["base_url"])
            if (
                endpoint.scheme not in {"http", "https"}
                or not endpoint.hostname
                or endpoint.username is not None
                or endpoint.password is not None
                or endpoint.query
                or endpoint.fragment
            ):
                raise DataRootError(
                    "model base URL must be HTTP(S) without credentials, query or fragment"
                )
            # Force validation of malformed numeric ports before M1 uses the URL.
            if endpoint.port is not None and not 1 <= endpoint.port <= 65535:
                raise DataRootError("model base URL port is invalid")
    except (OSError, ValueError, AttributeError, TypeError) as exc:
        if isinstance(exc, DataRootError):
            raise
        raise DataRootError("cannot read app configuration") from exc
