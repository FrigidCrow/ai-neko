"""Ten synthetic facts: process restart, per-question recall, and request injection.

The adapter only records its input. Its fixed output proves neither real model
understanding nor an operating-system reboot; those acceptance gates remain separate.
"""

import json
import os
import subprocess
import sys

import pytest

CASES = [
    ("喜欢无糖咖啡", "preference", "我的咖啡口味偏好是什么？"),
    ("喜欢猫咪", "preference", "我喜欢猫咪吗？"),
    ("偏爱蓝色", "preference", "我对蓝色有什么偏好？"),
    ("常玩策略游戏", "preference", "我常玩的游戏是什么类型？"),
    ("喜欢简短回答", "preference", "我喜欢简短还是详细的回答？"),
    ("九月学习游泳", "event", "我九月学习了什么？"),
    ("上周去过京都", "event", "我上周去过哪里？"),
    ("昨天买了键盘", "event", "我昨天买了什么？"),
    ("周五看了电影", "event", "周五我看了什么？"),
    ("今天完成项目", "event", "我今天完成了什么？"),
]


SEED_PROCESS = """
import asyncio,json,os,sys
from ai_neko.config.paths import initialize_data_root
from ai_neko.runtime import SessionRuntime

async def run():
    cases = json.load(sys.stdin)
    runtime = SessionRuntime(initialize_data_root(sys.argv[1]), object())
    try:
        session_id = runtime.create_session()['id']
        internal_id = runtime._session(session_id)['internal_id']
        saved = []
        for index, (content, kind, question) in enumerate(cases):
            source_id = f'synthetic-seed-source-{index}'
            fact = runtime.memory.remember(content, kind=kind, source_id=source_id)
            saved.append({'fact': fact, 'source_id': source_id, 'question': question})
        assert len(runtime.memory.list_facts()) == 10
        result = {'pid': os.getpid(), 'session_id': session_id,
                  'internal_id': internal_id, 'saved': saved}
    finally:
        await runtime.close()
    print(json.dumps(result, ensure_ascii=False))

asyncio.run(run())
"""


RECALL_PROCESS = """
import asyncio,json,os,sqlite3,sys
from contextlib import closing
from ai_neko.config.paths import initialize_data_root
from ai_neko.runtime import SessionRuntime

class ObservingModel:
    def __init__(self):
        self.requests = []

    async def stream(self, messages, tools=None):
        assert tools is None
        self.requests.append(messages)
        yield {'type': 'text', 'text': '合成观察器仅核对请求，不验证回答正确性。'}

class Providers:
    def __init__(self):
        self.observer = ObservingModel()

    def model(self):
        return self.observer

    def web_tools(self):
        raise AssertionError('Memory recall acceptance must not call a network tool')

async def run():
    seed = json.load(sys.stdin)
    providers = Providers()
    runtime = SessionRuntime(initialize_data_root(sys.argv[1]), providers)
    try:
        assert runtime.memory_preferences.value['auto_extract'] is False
        expected_ids = {item['fact']['id'] for item in seed['saved']}
        assert {fact['id'] for fact in runtime.memory.list_facts()} == expected_ids
        observed = []
        for item in seed['saved']:
            expected = item['fact']
            recalled = runtime.memory.recall(item['question'])
            assert recalled and recalled[0] == expected, item['question']
            sources = runtime.memory.sources(recalled[0]['id'])
            assert len(sources) == 1
            assert sources[0]['source_id'] == item['source_id']
            assert sources[0]['source_text'] == expected['content']

            # Every question uses a new session and graph namespace, so the
            # answer request cannot borrow a prior question's chat history.
            sid = runtime.create_session()['id']
            internal_id = runtime._session(sid)['internal_id']
            assert sid != seed['session_id'] and internal_id != seed['internal_id']
            request_index = len(providers.observer.requests)
            turn = await runtime.start_turn(sid, item['question'], guide=False)
            tid = turn['id']
            await asyncio.wait_for(runtime._tasks[tid], timeout=10)
            assert runtime.get_session(sid)['turns'][0]['status'] == 'completed'
            assert len(providers.observer.requests) == request_index + 1
            messages = providers.observer.requests[request_index]
            assert [message['content'] for message in messages if message['role'] == 'user'] == [
                item['question']
            ]
            assert not any(message['role'] == 'assistant' for message in messages)
            system = '\\n'.join(message['content'] for message in messages
                               if message['role'] == 'system')
            injected_fact = {'id': expected['id'], 'content': expected['content'],
                             'sources': [item['source_id']]}
            assert json.dumps(injected_fact, ensure_ascii=False) in system, item['question']
            dependencies = {row[0] for row in runtime._db.execute(
                'SELECT fact_id FROM turn_memory WHERE turn_id=?', (tid,))}
            assert expected['id'] in dependencies
            observed.append({'question': item['question'], 'fact_id': expected['id'],
                             'source_id': item['source_id'], 'session_id': sid,
                             'thread_id': internal_id + ':' + tid,
                             'request_fact_injected': True})

        assert len(providers.observer.requests) == 10
        assert len({row['session_id'] for row in observed}) == 10
        assert len({row['thread_id'] for row in observed}) == 10
        with closing(sqlite3.connect(runtime.paths.checkpoints / 'chat-graph.sqlite')) as db:
            threads = {row[0] for row in db.execute('SELECT DISTINCT thread_id FROM checkpoints')}
        assert threads == {row['thread_id'] for row in observed}
        assert {fact['id'] for fact in runtime.memory.list_facts()} == expected_ids
        result = {'pid': os.getpid(), 'observed': observed, 'model_requests': 10,
                  'real_model_calls': 0, 'model_correctness_verified': False,
                  'operating_system_reboot_verified': False}
    finally:
        await runtime.close()
    print(json.dumps(result, ensure_ascii=False))

asyncio.run(run())
"""


def run_process(code, data_root, payload):
    process = subprocess.run(
        [sys.executable, "-c", code, str(data_root)],
        input=json.dumps(payload, ensure_ascii=False),
        capture_output=True,
        text=True,
        encoding="utf-8",
        env={**os.environ, "PYTHONIOENCODING": "utf-8"},
        timeout=45,
    )
    assert process.returncode == 0, process.stderr
    return json.loads(process.stdout)


@pytest.mark.usefixtures("sandbox_compatible")
def test_ten_facts_recall_with_sources_and_reach_new_thread_requests_after_process_restart(
    tmp_path,
):
    data_root = tmp_path / "ten-fact-acceptance"
    seed = run_process(SEED_PROCESS, data_root, CASES)
    # The writer process has fully exited before the reader process starts.
    evidence = run_process(RECALL_PROCESS, data_root, seed)
    assert len(evidence["observed"]) == 10
    assert {row["fact_id"] for row in evidence["observed"]} == {
        item["fact"]["id"] for item in seed["saved"]
    }
    assert {row["source_id"] for row in evidence["observed"]} == {
        item["source_id"] for item in seed["saved"]
    }
    assert all(row["request_fact_injected"] for row in evidence["observed"])
    assert evidence["model_requests"] == 10 and evidence["real_model_calls"] == 0
    assert evidence["model_correctness_verified"] is False
    assert evidence["operating_system_reboot_verified"] is False
