# Memory 检索组件复用记录

2026-09-26，用户要求人格、视觉、语音、搜索与长期记忆实现继续参照 N.E.K.O。此记录区分实际迁入的源码与只参考设计的部分。

## 来源与许可

- 上游：Project N.E.K.O.；本机只读源码 `/Users/frigidcrow/Dev/neko-companion`。
- 固定来源 commit：`90ccf79c95e80f899b9bf3395fa8cd9a9bfe29be`。
- 许可证：Apache License 2.0。实际迁入文件保留 `Copyright 2025-2026 Project N.E.K.O. Team` 及 Apache 许可头。
- 完整许可及上游 NOTICE 原样保留在 [NEKO-LICENSE.txt](../src/ai_neko/memory/licenses/NEKO-LICENSE.txt) 与 [NEKO-NOTICE.txt](../src/ai_neko/memory/licenses/NEKO-NOTICE.txt)。NOTICE 其中提到的其他原版组件不因此被本次导入。
- 本次未导入上游配置、凭据、运行数据、服务初始化、模型客户端、桌面宿主或 `.venv`；没有引用原版包名 `memory`、`config` 或 `utils`。

## 实际迁入清单

| 原路径与范围 | 本项目位置 | 修改 |
| --- | --- | --- |
| `memory/hybrid_recall.py:126–210`，`_tokenize` | [recall.py](../src/ai_neko/memory/recall.py)，`tokenize` | 保留 CJK 2/3-gram、Latin 整词、重复词频及繁简折叠；拆除配置、日志与 persona 模块导入；加入大小写折叠和显式停用词参数。 |
| `memory/hybrid_recall.py:216–294`，`_bm25_rank` | 同文件，`bm25_rank` | 保留 Okapi BM25、IDF、词频、长度归一化及默认 `k1=1.5,b=0.75`；查询词排序使跨进程浮点累加顺序稳定；只接受调用方已经隔离好的候选。 |
| `memory/persona/_shared.py:33–35`，分隔正则 | 同文件，`_SPLIT_RE` | 原规则迁入；无 persona 运行依赖。 |
| `memory/stop_names.py:135–177`，`strip_stop_names` 及所需常量 | 同文件，`strip_stop_names` | 保留短于两字符名字不删除、Latin 单词边界、CJK 分隔替换；函数内部按名称长度排序，不读取人物配置。 |
| `memory/script_fold.py` | [script_fold.py](../src/ai_neko/memory/script_fold.py) | 字符映射与折叠逻辑整体迁入，仅格式化和移除本项目不识别的自定义 noqa 注释。纯 Python 运行，不需要 OpenCC。 |
| `LICENSE` / `NOTICE` | `src/ai_neko/memory/licenses/` | 原样复制。 |

上游各完整文件的 SHA-256 记录如下；分段提取不能用整个源文件 hash 验证本地片段相同，应结合固定 commit 与修改说明审阅：

```text
memory/hybrid_recall.py 540d4e80390968ee0205244643072df5dac6052405fc23add48d6d3a032a76d5
memory/persona/_shared.py f9b822c18b79ef946449894a825eaac16c0bde5ff03c90ce638a843c62b5ae2a
memory/stop_names.py fb8f285f44c13b87c9aaa2401689e43e932cb1c79c120f26612e7ad53fe4b303
memory/script_fold.py 6f63f68bf34eead23edb6ecaf1582fcf5b16d300eeadb418fecf648f2a63a7a8
LICENSE 0d99b64baf1323c3a7f70a28075b14d573df6d773aa2d33d827053cc775062d8
NOTICE 96199da23327c5086c4f562e4c1a05685e18643ab32ce896f13934b15b4a8aca
```

## 接入边界与合理差异

[MemoryService](../src/ai_neko/memory/service.py) 先从本工程 SQLite 按 `user_id + character_id` 读取当前有效事实，再传给 BM25；另一作用域的内容不会参与候选、词频或 IDF 计算。默认内容哈希不参与检索词项。人物名称从本工程角色档案获取，仅用于停用名处理。

“我的偏好是什么”“我喜欢喝什么？看看这一帧”等个人泛问先召回当前作用域的对应类型候选；针对具体主题的请求走 BM25，零分不返回。召回结果始终是候选事实，不能凭泛问候选为未提供的生日、职业等信息编造答案。原始事实与来源不做繁简改写，折叠只用于查询排序。

此处只移植词法检索，不声称已经具有原版完整混合检索能力。N.E.K.O 的 `hybrid_recall.py:1085–1244` 还组合了反思、归档、embedding 与 RRF；其 embedding 路径依赖服务和模型运行环境。本轮保留单一 Memory Service 与现有依赖，不引入第二个记忆服务器、原版生产配置或额外模型下载。BM25 仍不能覆盖所有同义表达。

以下功能只参考边界后独立实现，未复制为上游源码：

- `timeindex.py:871–901` 原文与事件来源分离 → 本工程 `memory_sources` 保存来源原文，Runtime 管理会话执行日志。
- `outbox.py:17–41` 持久 pending/done 与幂等回放 → 本工程 SQLite 任务、稳定来源 ID、租约、重试及结果事务提交。
- `scopes.py:76–107` 独立作用域 → 本工程服务器控制的 user/character 作用域。
- `facts.py:794–919,994–1057,3370–3407` 原文相关副本删除、持久删除界限、迟到结果防复活 → 本工程来源级联遗忘、tombstones、invalidation revision 和恢复删除策略。
- `persona/corrections.py:416–444,797–819` 锁外处理与作用域内纠正 → 本工程用户明确纠正、短 SQLite 事务与版本检查；不让提取任务覆盖人工纠正。

## 验证与升级

本次本机确定性测试命令：

```sh
uv run --locked ruff check src/ai_neko/memory tests/test_memory*.py tests/test_persona*.py
uv run --locked pytest tests/test_memory*.py tests/test_persona*.py -q
```

49 项通过，覆盖：原有持久/纠正/遗忘/任务恢复；BM25 长度归一化和词频排序；繁简查询不改写原文；CJK/Latin 分词；人物名字边界；作用域隔离；空结果；新进程导入不加载原版服务或重型依赖；版权文件存在。这些是合成资料的本机测试，不是 Windows 真机或真实模型回答质量验证。

升级步骤：先只读获取新版对应源码与 commit，比较上述四个实现文件和 LICENSE/NOTICE；保留本工程作用域、停用词、无运行副作用与持久权威边界；迁入必要变化，更新来源 hash 和改动说明；重跑上述测试及 Runtime 记忆集成测试。不要整文件替换回原版 `hybrid_recall.py`，否则会重新引入配置、日志、embedding 及生产模块耦合。发布包应包含这些源码对应的 Apache LICENSE/NOTICE；桌面现有 N.E.K.O 许可目录也需在打包时继续保留。
