"""Local development entry points; no external service starts on import."""

from __future__ import annotations

import argparse
import json
import re
import sys
from urllib.error import URLError
from urllib.request import HTTPRedirectHandler, ProxyHandler, Request, build_opener

from ai_neko import APP_ID
from ai_neko.config.paths import DataRootError, initialize_data_root, resolve_data_root, safe_child


class _NoRedirect(HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        # A stale port may now belong to another process. Never forward the local
        # bearer token to a redirect destination, even another loopback endpoint.
        return None


def stop(data_dir: str | None) -> None:
    root = resolve_data_root(data_dir)
    runtime = safe_child(root, "runtime")
    path = safe_child(runtime, "connection.json")
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
        if not isinstance(value, dict):
            raise ValueError
        port = value["port"]
        if value.get("app_id") != APP_ID or value.get("host") != "127.0.0.1":
            raise ValueError
        if not isinstance(port, int) or isinstance(port, bool) or not 1 <= port <= 65535:
            raise ValueError
        token = value["token"]
        if not isinstance(token, str) or not re.fullmatch(r"[A-Za-z0-9_-]{43}", token):
            raise ValueError
    except (OSError, ValueError, KeyError, TypeError) as exc:
        raise ValueError("no valid ai-neko connection descriptor") from exc
    request = Request(
        f"http://127.0.0.1:{port}/shutdown",
        method="POST",
        data=b"",
        headers={"Authorization": f"Bearer {token}"},
    )
    try:
        with build_opener(ProxyHandler({}), _NoRedirect()).open(request, timeout=5) as response:
            if response.status != 202:
                raise ValueError("local instance refused shutdown")
    except (URLError, OSError) as exc:
        raise ValueError("local instance is unavailable or descriptor is stale") from exc
    print(json.dumps({"app_id": APP_ID, "status": "stopping"}))


def main(argv: list[str] | None = None) -> int:
    argv = list(sys.argv[1:] if argv is None else argv)
    if argv and argv[0] == "graph":
        from ai_neko.graph.__main__ import main as graph_main

        return graph_main(argv[1:])
    parser = argparse.ArgumentParser(description="ai-neko local chat and guide preview")
    commands = parser.add_subparsers(dest="command", required=True)
    serve_parser = commands.add_parser("serve", help="start the authenticated loopback probe")
    serve_parser.add_argument("--data-dir")
    serve_parser.add_argument("--port", type=int, default=None)
    start_parser = commands.add_parser("start", help="start ai-neko and open the local chat page")
    start_parser.add_argument("--data-dir")
    start_parser.add_argument("--port", type=int, default=None)
    stop_parser = commands.add_parser("stop", help="request graceful shutdown of this data root")
    stop_parser.add_argument("--data-dir")
    paths_parser = commands.add_parser("paths", help="show chosen paths without creating them")
    paths_parser.add_argument("--data-dir")
    commands.add_parser("graph", help="run synthetic graph/checkpoint diagnostics (graph --help)")
    commands.add_parser(
        "self-check", help="check the M0 foundation using disposable synthetic data"
    )
    if not argv:
        parser.print_help()
        print(
            "\nUse 'start' to open chat and configure your model/search services. "
            "Voice and desktop avatar are not included in this preview."
        )
        return 0
    args = parser.parse_args(argv)
    if args.command == "self-check":
        from ai_neko.diagnostics import main as diagnostic_main

        return diagnostic_main()
    from ai_neko.app.server import InstanceInUseError, PortInUseError, serve
    from ai_neko.config.settings import Settings

    try:
        if args.command == "paths":
            print(
                json.dumps({"app_id": APP_ID, "data_root": str(resolve_data_root(args.data_dir))})
            )
        elif args.command == "stop":
            stop(args.data_dir)
        else:
            settings = Settings.load(args.port)
            serve(
                initialize_data_root(args.data_dir), settings, open_browser=args.command == "start"
            )
    except InstanceInUseError as exc:
        print(f"ai-neko: {exc}", file=sys.stderr)
        return 3
    except PortInUseError as exc:
        print(f"ai-neko: {exc}", file=sys.stderr)
        return 4
    except (DataRootError, ValueError, OSError, RuntimeError) as exc:
        print(f"ai-neko: {exc}", file=sys.stderr)
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
