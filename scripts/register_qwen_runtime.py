"""Explicitly register already installed, approved Qwen files; no inference/setup."""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from ai_neko.config.paths import initialize_data_root
from ai_neko.media.qwen_runtime.binding import (
    APPROVED_PROJECT,
    ENVIRONMENT_RELATIVE,
    MODEL_RELATIVE,
    PROMPT_RELATIVE,
    BindingError,
    register_binding,
)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--data-dir", type=Path)
    parser.add_argument("--environment", type=Path, default=APPROVED_PROJECT / ENVIRONMENT_RELATIVE)
    parser.add_argument("--model", type=Path, default=APPROVED_PROJECT / MODEL_RELATIVE)
    parser.add_argument("--prompt", type=Path, default=APPROVED_PROJECT / PROMPT_RELATIVE)
    parser.add_argument("--idle-unload-seconds", type=int, default=300)
    args = parser.parse_args(argv)
    try:
        paths = initialize_data_root(args.data_dir)
        manifest = register_binding(
            paths,
            environment=args.environment,
            model=args.model,
            prompt=args.prompt,
            idle_unload_seconds=args.idle_unload_seconds,
        )
    except (BindingError, ValueError, OSError) as exc:
        parser.exit(1, f"Qwen registration failed: {exc}\n")
    print(
        json.dumps(
            {
                "state": "registered",
                "binding": str(paths.assets / "qwen" / "binding.json"),
                "backend": manifest["backend"],
                "model_revision": manifest["model_revision"],
                "idle_unload_seconds": manifest["idle_unload_seconds"],
                "inference_run": False,
            },
            ensure_ascii=True,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
