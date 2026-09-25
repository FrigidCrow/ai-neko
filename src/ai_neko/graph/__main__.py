"""Local synthetic graph CLI; no network/model calls or credential loading."""

from __future__ import annotations

import argparse
import json
import sqlite3
import sys

from ai_neko.config.paths import initialize_data_root
from ai_neko.graph import GraphService, GraphStateError, Scope, ScopeAccessError


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--data-root", required=True, help="Absolute isolated ai-neko data root")
    parser.add_argument("--user", required=True)
    parser.add_argument("--character", required=True)
    parser.add_argument("--thread", required=True, help="External session ID within this scope")
    parser.add_argument("--handle", help="Existing opaque internal thread handle")
    parser.add_argument("--checkpoint-id", help="Owned snapshot or expected-current checkpoint")
    commands = parser.add_subparsers(dest="command", required=True)
    run = commands.add_parser("run", help="Run two deterministic synthetic nodes")
    run.add_argument("--text", required=True)
    run.add_argument("--pause", action="store_true")
    run.add_argument("--stream", action="store_true", help="Print custom events as JSON lines")
    commands.add_parser("get", help="Read a scoped checkpoint")
    history = commands.add_parser("history", help="Read newest-first scoped checkpoint history")
    history.add_argument("--limit", type=int, default=50)
    resume = commands.add_parser("resume", help="Resume a pending synthetic confirmation")
    resume.add_argument("--response", required=True)
    resume.add_argument("--stream", action="store_true")
    commands.add_parser("delete", help="Delete this scoped thread and revoke its handle")
    args = parser.parse_args(argv)

    def emit(event: dict) -> None:
        print(json.dumps({"event": event}, ensure_ascii=False), flush=True)

    try:
        scope = Scope(args.user, args.character, args.thread)
        paths = initialize_data_root(args.data_root)
        with GraphService(paths.checkpoints) as service:
            handle = args.handle or service.open_thread(scope, create=args.command == "run")
            kwargs = {"checkpoint_id": args.checkpoint_id}
            if args.command == "run":
                result = service.run(
                    scope,
                    handle,
                    args.text,
                    pause=args.pause,
                    on_event=emit if args.stream else None,
                    **kwargs,
                )
            elif args.command == "resume":
                result = service.resume(
                    scope, handle, args.response, on_event=emit if args.stream else None, **kwargs
                )
            elif args.command == "get":
                result = service.get(scope, handle, **kwargs)
            elif args.command == "history":
                result = service.history(scope, handle, limit=args.limit, **kwargs)
            else:
                service.delete(scope, handle, **kwargs)
                result = {"deleted": True, "thread_id": handle}
        print(json.dumps(result, ensure_ascii=False))
        return 0
    except (ValueError, OSError, sqlite3.Error, GraphStateError, ScopeAccessError) as exc:
        print(json.dumps({"error": type(exc).__name__, "message": str(exc)}), file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
