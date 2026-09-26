"""Local memory preferences; credentials remain in the provider vault."""

import json
import os
import tempfile
from pathlib import Path

from ai_neko.config.paths import safe_child


class MemoryPreferences:
    def __init__(self, paths):
        self.path = safe_child(paths.config, "memory.json")
        self.value = {"auto_extract": False}
        if self.path.exists():
            value = json.loads(self.path.read_text(encoding="utf-8"))
            self._validate(value)
            self.value = value

    @staticmethod
    def _validate(value):
        if (
            not isinstance(value, dict)
            or set(value) != {"auto_extract"}
            or type(value["auto_extract"]) is not bool
        ):
            raise ValueError("invalid_memory_preferences")

    def update(self, value):
        self._validate(value)
        fd, temporary = tempfile.mkstemp(prefix=".memory-", dir=self.path.parent)
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as handle:
                json.dump(value, handle)
                handle.flush()
                os.fsync(handle.fileno())
            Path(temporary).chmod(0o600)
            os.replace(temporary, self.path)
        finally:
            Path(temporary).unlink(missing_ok=True)
        self.value = dict(value)
        return dict(self.value)
