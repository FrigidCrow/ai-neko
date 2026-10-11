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
                override = owner.response(value)
                if override is not None:
                    pieces = [override]
                deltas = [{"content": piece} for piece in pieces]
                if (
                    override is None
                    and value.get("tools")
                    and not any(m.get("role") == "tool" for m in messages)
                ):
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

    def response(self, request: dict) -> str | None:
        return None

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


class SyntheticMemoryModel(SyntheticModel):
    """Deterministic responses to the extraction protocol, not an AI evaluator."""

    old_content = "合成用户PACKAGE在虚构游戏星河棋中长期喜欢保守防守策略"
    new_content = "合成用户PACKAGE在虚构游戏星河棋中长期喜欢主动进攻策略"
    first_statement = old_content + "。这是稳定偏好。"
    correction_statement = "把" + old_content + "更正为" + new_content + "。"
    recall_statement = "合成用户PACKAGE在虚构游戏星河棋中的长期策略偏好是什么？"
    fact_key = "package_xingheqi_strategy_preference"

    def __init__(self):
        self.chats: list[dict] = []
        self.extractions: list[dict] = []
        super().__init__()

    def response(self, request: dict) -> str:
        messages = request["messages"]
        if "从用户原话中整理" not in messages[0]["content"]:
            self.chats.append(request)
            return "合成协议回复；不作为真实模型理解或记忆保存的证据。"
        source = json.loads(messages[-1]["content"])
        self.extractions.append(source)
        if source["user_statement"] == self.first_statement:
            facts = [
                {
                    "operation": "create",
                    "content": self.old_content,
                    "quote": self.old_content,
                    "fact_key": self.fact_key,
                    "kind": "preference",
                }
            ]
        elif source["user_statement"] == self.correction_statement:
            # The application must supply the persisted identity; the fixture
            # cannot invent it or fall back to an independently generated key.
            targets = [
                fact for fact in source["existing_facts"] if fact["content"] == self.old_content
            ]
            facts = (
                [
                    {
                        "operation": "correct",
                        "target_fact_id": targets[0]["id"],
                        "target_revision": targets[0]["revision"],
                        "previous_quote": self.old_content,
                        "content": self.new_content,
                        "quote": self.new_content,
                        "correction_quote": self.correction_statement,
                    }
                ]
                if len(targets) == 1
                else []
            )
        else:
            facts = []
        return json.dumps({"facts": facts}, ensure_ascii=False)
