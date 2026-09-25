"""Local development entry points; no external service starts on import."""

from __future__ import annotations

import argparse
import json
import os
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
    serve_parser.add_argument("--desktop-parent", action="store_true", help=argparse.SUPPRESS)
    serve_parser.add_argument("--desktop-parent-pid", type=int, help=argparse.SUPPRESS)
    start_parser = commands.add_parser("start", help="start ai-neko and open the local chat page")
    start_parser.add_argument("--data-dir")
    start_parser.add_argument("--port", type=int, default=None)
    stop_parser = commands.add_parser("stop", help="request graceful shutdown of this data root")
    stop_parser.add_argument("--data-dir")
    paths_parser = commands.add_parser("paths", help="show chosen paths without creating them")
    paths_parser.add_argument("--data-dir")
    paths_parser.add_argument("--initialize", action="store_true", help=argparse.SUPPRESS)
    commands.add_parser("graph", help="run synthetic graph/checkpoint diagnostics (graph --help)")
    commands.add_parser(
        "self-check", help="check the M0 foundation using disposable synthetic data"
    )
    if not argv:
        parser.print_help()
        print(
            "\nThis executable is the backend CLI. Launch the root ai-neko.exe for the "
            "desktop catgirl, or use 'npm --prefix desktop start' from the source checkout. "
            "Voice is not implemented in this preview."
        )
        return 0
    args = parser.parse_args(argv)
    if args.command == "self-check":
        from ai_neko.diagnostics import main as diagnostic_main

        return diagnostic_main()
    from ai_neko.app.server import InstanceInUseError, PortInUseError, WindowsParentProcess, serve
    from ai_neko.config.settings import Settings

    try:
        if args.command == "paths":
            root = resolve_data_root(args.data_dir)
            result = {"app_id": APP_ID, "data_root": str(root)}
            if args.initialize:
                paths = initialize_data_root(root)
                desktop = safe_child(paths.root, "desktop")
                desktop.mkdir(mode=0o700, exist_ok=True)
                result["desktop_root"] = str(desktop)
            print(json.dumps(result))
        elif args.command == "stop":
            stop(args.data_dir)
        else:
            desktop_parent = bool(getattr(args, "desktop_parent", False))
            parent_pid = getattr(args, "desktop_parent_pid", None)
            if parent_pid is not None and (
                not desktop_parent or not 0 < parent_pid <= 0xFFFFFFFF or parent_pid == os.getpid()
            ):
                raise ValueError(
                    "desktop parent process ID requires private pipe mode and a valid ID"
                )
            if desktop_parent and os.name == "nt" and parent_pid is None:
                raise ValueError("Windows desktop parent requires its process ID")
            if desktop_parent and (
                sys.stdin is None or sys.stdout is None or sys.stdin.isatty() or sys.stdout.isatty()
            ):
                raise ValueError("desktop parent requires private stdin and stdout pipes")
            # Open once before creating application data or listening. The held
            # process object remains stable even if Windows later reuses its PID.
            parent_process = (
                WindowsParentProcess(parent_pid) if desktop_parent and os.name == "nt" else None
            )
            try:
                settings = Settings.load(args.port)
                serve(
                    initialize_data_root(args.data_dir),
                    settings,
                    open_browser=args.command == "start",
                    parent_input=sys.stdin if desktop_parent else None,
                    parent_process=parent_process,
                )
            finally:
                if parent_process is not None:
                    parent_process.close()
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
