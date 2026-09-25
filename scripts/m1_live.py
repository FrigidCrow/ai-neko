"""Opt-in, billable real-provider acceptance using synthetic inputs and a disposable data root.

Keys are accepted only from AI_NEKO_MODEL_API_KEY / AI_NEKO_SEARCH_API_KEY.
No project/user config, ordinary OPENAI key, browser profile or prior chats are read.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import platform
import tempfile
from datetime import datetime, timezone
from pathlib import Path

from ai_neko.config.paths import initialize_data_root
from ai_neko.config.providers import validate
from ai_neko.providers import ModelAdapter
from ai_neko.runtime import SessionRuntime
from ai_neko.tools.web import WebTools

QUESTIONS = [
    "Python 3.11，Windows 11 PowerShell，如何创建并激活虚拟环境？请查官方正文并给步骤与来源。",
    "Git 2.x 命令行，已有本地仓库，如何创建并切换一个新分支？请查官方正文给步骤与来源。",
    "星露谷物语 PC 1.6，首次建造筒仓需要哪些材料和前置条件？请核对正文并给来源，版本不明不要猜。",
]


class LiveProviders:
    def __init__(self, config):
        self.config = config
        self.model_calls = 0
        self.search_calls = 0
        self.page_calls = 0

    def model(self):
        parent = self
        adapter = ModelAdapter(self.config, os.environ["AI_NEKO_MODEL_API_KEY"])

        class Counted:
            async def stream(self, messages, tools=None):
                parent.model_calls += 1
                async for event in adapter.stream(messages, tools):
                    yield event

        return Counted()

    def web_tools(self):
        parent = self
        adapter = WebTools(self.config, os.environ["AI_NEKO_SEARCH_API_KEY"])

        class Counted:
            async def execute(self, name, arguments):
                if name == "search_web":
                    parent.search_calls += 1
                elif name == "read_web_page":
                    parent.page_calls += 1
                return await adapter.execute(name, arguments)

        return Counted()


async def turn(runtime, sid, text, guide):
    started = await runtime.start_turn(sid, text, guide=guide)
    tid, cursor, events = started["turn_id"], 0, []
    async with asyncio.timeout(540):
        while True:
            batch = runtime.events(sid, tid, cursor)
            events.extend(batch["events"])
            if batch["events"]:
                cursor = batch["events"][-1]["seq"]
                runtime.ack(sid, tid, cursor)
            if batch["status"] not in {"accepted", "running"} and cursor >= batch["last_seq"]:
                break
            await asyncio.sleep(0.05)
    return {
        "input": text,
        "status": batch["status"],
        "output": "".join(e["text"] for e in events if e["type"] == "text"),
        "sources": [e["source"] for e in events if e["type"] == "source"],
        "errors": [e["code"] for e in events if e["type"] == "error"],
    }


async def evaluate(config, report):
    providers = LiveProviders(config)
    try:
        with tempfile.TemporaryDirectory(prefix="ai-neko-live-synthetic-") as folder:
            runtime = SessionRuntime(initialize_data_root(Path(folder) / "data"), providers)
            try:
                sid = runtime.create_session()["session_id"]
                for index in range(10):
                    question = (
                        "这是合成测试。我喜欢无糖茶，测试代号是星河。请简短确认。"
                        if index == 0
                        else f"第 {index + 1} 轮：我的测试代号和饮品偏好是什么？只根据本会话回答。"
                    )
                    result = await turn(runtime, sid, question, False)
                    report["conversation"].append(result)
                    if result["status"] != "completed":
                        break
                for question in QUESTIONS:
                    sid = runtime.create_session()["session_id"]
                    report["guides"].append(await turn(runtime, sid, question, True))
            finally:
                await runtime.close()
    finally:
        report["real_model_calls"] = providers.model_calls
        report["real_search_calls"] = providers.search_calls
        report["real_page_calls"] = providers.page_calls
    report["status"] = "manual_review_required"
    report["manual_review"] = (
        "Pending: independently check all 10 replies and 3 guides against read text."
    )


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--model", required=True)
    parser.add_argument("--model-base-url", default="https://api.openai.com/v1")
    parser.add_argument("--search-base-url", default="https://api.tavily.com")
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    config = validate(
        {
            "model": args.model,
            "model_base_url": args.model_base_url,
            "search_base_url": args.search_base_url,
        }
    )
    output = args.output.resolve()
    artifacts = Path(__file__).resolve().parents[1] / "artifacts"
    if not output.is_relative_to(artifacts) or output.suffix != ".json" or output.exists():
        parser.error(
            "Use a new evidence JSON file under artifacts/; existing evidence is never overwritten."
        )
    report = {
        "stage": "M1-real-providers",
        "status": "Blocked",
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "platform": platform.platform(),
        "config": config,
        "real_model_calls": 0,
        "real_search_calls": 0,
        "real_page_calls": 0,
        "conversation": [],
        "guides": [],
    }
    missing = [
        name
        for name in ("AI_NEKO_MODEL_API_KEY", "AI_NEKO_SEARCH_API_KEY")
        if not os.environ.get(name)
    ]
    if missing:
        report["missing_environment_variables"] = missing
    else:
        try:
            asyncio.run(evaluate(config, report))
        except Exception as exc:
            report["status"] = "Failed"
            report["error_class"] = type(exc).__name__
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({"status": report["status"], "real_model_calls": report["real_model_calls"]}))
    return 0 if report["status"] == "manual_review_required" else 2


if __name__ == "__main__":
    raise SystemExit(main())
