"""Run only the archived v0.4 application against an explicit synthetic root."""

from __future__ import annotations

import argparse
import asyncio
import json
import socket
import sys
from pathlib import Path
from uuid import uuid4


def deny_network(event, _arguments):
    if event in {"socket.connect", "socket.getaddrinfo", "socket.gethostbyname"}:
        raise AssertionError("legacy fixture must never contact a network")


class Model:
    def __init__(self):
        self.calls = 0

    async def stream(self, messages, tools=None):
        assert not tools
        self.calls += 1
        latest = messages[-1]["content"]
        answer = (
            "独立合成天气记录：晴朗。"
            if "独立天气" in latest
            else "已确认合成偏好：青柚茶，校验代号 V04_SYNTHETIC_ORCHID。"
        )
        yield {"type": "text", "text": answer}


class Providers:
    def __init__(self):
        self.adapter = Model()

    def model(self):
        return self.adapter

    def web_tools(self):
        raise AssertionError("legacy fixture must never use web tools")


async def run(args):
    # -I prevents the repository, PYTHONPATH, user site and inherited configuration
    # from selecting the candidate implementation instead of the archived release.
    old_source = args.source.resolve()
    sys.path.insert(0, str(old_source))
    import ai_neko.memory.service as memory_module
    import ai_neko.runtime.service as runtime_module
    from ai_neko.config.paths import initialize_data_root
    from ai_neko.runtime import SessionRuntime

    assert Path(runtime_module.__file__).resolve().is_relative_to(old_source)
    assert Path(memory_module.__file__).resolve().is_relative_to(old_source)
    providers = Providers()
    runtime = SessionRuntime(initialize_data_root(args.data.resolve()), providers)
    try:
        if args.mode == "create":
            persona = runtime.memory.update_persona(
                {"name": "旧版合成猫", "user_name": "合成测试员", "traits": ["耐心", "坦诚"]}
            )
            sid = runtime.create_session()["id"]

            async def deliver(session, question):
                turn = await runtime.start_turn(session, question, request_id=uuid4().hex)
                task = runtime._tasks[turn["id"]]
                await asyncio.wait_for(asyncio.shield(task), 10)
                batch = runtime.events(session, turn["id"])
                assert batch["status"] == "completed"
                runtime.ack(session, turn["id"], batch["last_seq"])
                return turn["id"]

            question = "这是合成数据：我喜欢青柚茶，校验代号 V04_SYNTHETIC_ORCHID。"
            first = await deliver(sid, question)
            fact = runtime.memory.remember(
                "喜欢青柚茶，校验代号 V04_SYNTHETIC_ORCHID",
                source_id="turn:" + first,
                source_text=question,
            )
            preserved = runtime.memory.remember(
                "合成测试偏好：回复简短", source_id="manual:v04-preserved"
            )
            second = await deliver(sid, "请回顾青柚茶和 V04_SYNTHETIC_ORCHID 的合成偏好。")
            independent = runtime.create_session()["id"]
            other = await deliver(independent, "请记一条独立天气记录。")
            spoken = runtime.get_session(sid)["turns"][0]["delivered_text"]
            segment = uuid4().hex
            for state in ("started", "completed"):
                runtime.audio_ack(sid, first, segment, state, text_start=0, text_end=len(spoken))
            snapshot = await runtime.backup_memory()
            expected = {
                "persona": persona,
                "facts": runtime.memory.list_facts(),
                "erased_fact": fact,
                "preserved_fact": preserved,
                "session_id": sid,
                "turn_ids": [first, second],
                "independent_session_id": independent,
                "independent_turn_id": other,
                "sessions": [runtime.get_session(sid), runtime.get_session(independent)],
                "snapshot": snapshot,
                "model_calls": providers.adapter.calls,
                "network_calls": 0,
            }
            args.expected.write_text(
                json.dumps(expected, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
            )
        else:
            expected = json.loads(args.expected.read_text(encoding="utf-8"))
            assert runtime.memory.get_persona() == expected["persona"]
            assert runtime.memory.list_facts() == expected["facts"]
            for session in expected["sessions"]:
                actual = runtime.get_session(session["id"])
                assert [(t["input"], t["confirmed_text"]) for t in actual["turns"]] == [
                    (t["input"], t["confirmed_text"]) for t in session["turns"]
                ]
            assert providers.adapter.calls == 0
        print(json.dumps({"old_source_imported": True, "mode": args.mode, "network_calls": 0}))
    finally:
        await runtime.close()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("mode", choices=("create", "verify"))
    parser.add_argument("--source", type=Path, required=True)
    parser.add_argument("--data", type=Path, required=True)
    parser.add_argument("--expected", type=Path, required=True)
    args = parser.parse_args()
    # Loading socket before the audit hook lets asyncio create its local wakeup
    # socket pair, while any actual connect or name resolution remains forbidden.
    assert socket.socket
    sys.addaudithook(deny_network)
    asyncio.run(run(args))


if __name__ == "__main__":
    main()
