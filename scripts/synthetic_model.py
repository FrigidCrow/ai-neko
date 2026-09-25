"""Local protocol fixture for source and frozen-package tests, never shipped as an adapter."""

from __future__ import annotations

import json
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer


class SyntheticModel:
    def __init__(self):
        owner = self
        self.requests: list[dict] = []

        class Handler(BaseHTTPRequestHandler):
            def log_message(self, *_args):
                pass

            def do_POST(self):
                size = int(self.headers.get("Content-Length", 0))
                if size > 1000000 or self.path != "/v1/chat/completions":
                    self.send_error(400)
                    return
                value = json.loads(self.rfile.read(size))
                owner.requests.append(value)
                messages = value.get("messages", [])
                text = next(
                    (m.get("content", "") for m in reversed(messages) if m.get("role") == "user"),
                    "",
                )
                slow = "slow" in str(text)
                pieces = ["合成回复：", "你好，", "本轮已收到。"]
                if slow:
                    pieces = ["合成慢速片段。"] * 30
                deltas = [{"content": piece} for piece in pieces]
                if value.get("tools") and not any(m.get("role") == "tool" for m in messages):
                    deltas = [
                        {
                            "tool_calls": [
                                {
                                    "index": 0,
                                    "id": "fixture-search",
                                    "type": "function",
                                    "function": {
                                        "name": "search_web",
                                        "arguments": json.dumps(
                                            {"query": "synthetic public guide"}
                                        ),
                                    },
                                },
                                {
                                    "index": 1,
                                    "id": "fixture-read",
                                    "type": "function",
                                    "function": {
                                        "name": "read_web_page",
                                        "arguments": json.dumps(
                                            {"url": "http://127.0.0.1/private"}
                                        ),
                                    },
                                },
                            ]
                        }
                    ]
                try:
                    self.send_response(200)
                    self.send_header("Content-Type", "text/event-stream")
                    self.send_header("Cache-Control", "no-cache")
                    self.send_header("Connection", "close")
                    self.end_headers()
                    for delta in deltas:
                        event = {"choices": [{"index": 0, "delta": delta}]}
                        self.wfile.write(("data: " + json.dumps(event) + "\n\n").encode())
                        self.wfile.flush()
                        time.sleep(0.08 if slow else 0.04)
                    event = {"choices": [{"index": 0, "delta": {}, "finish_reason": "stop"}]}
                    self.wfile.write(
                        ("data: " + json.dumps(event) + "\n\ndata: [DONE]\n\n").encode()
                    )
                    self.wfile.flush()
                except (BrokenPipeError, ConnectionResetError, ConnectionAbortedError):
                    pass

        self.server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        self.server.daemon_threads = True
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)

    @property
    def url(self):
        return f"http://127.0.0.1:{self.server.server_port}/v1"

    def __enter__(self):
        self.thread.start()
        return self

    def __exit__(self, *_args):
        self.server.shutdown()
        self.server.server_close()
        self.thread.join(timeout=3)
