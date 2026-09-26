# MVP2 进入前全面评审

日期：2026-09-26（评审时源码为 v0.4.0-alpha.1 后的工作区快照）
范围：`src/ai_neko/`（5,905 行 Python）、`desktop/`（5,170 行 JS/CJS）、`tests/`（26 个文件）、`docs/`、`packaging/`、`.github/workflows/`
参照：[MVP2 规划](NEXT-GUIDE-COMPANION.md)（G1–G6、A01–A11）、[工程规则](../AGENTS.md)

评审结论：**代码安全基线明显高于同类项目**（数据根节点校验、Electron CSP 与 sender 校验、图片 TTL、提示注入边界、SSRF 防护基本到位），但存在 **2 个必须在 MVP2 前修掉的破坏性行为**、**1 个会直接击穿 MVP2 性能目标的架构约束**，以及若干技术债。最大风险集中在 `runtime/service.py` 与 `memory/service.py` 两个巨型类的擦除/清理路径，而 MVP2 的 G2/G4 恰好要复用同一路径。

---

## 第一部分 · 当前代码与架构的明显问题

### A. 潜在 Bug（按严重度）

#### A1 🔴 遗忘一条记忆 → 清空整个会话的后续所有回合

位置：`src/ai_neko/runtime/service.py:483-494` + `:962-1002`

```python
used = {fact["id"] for fact in facts}
used.update(  # ← 把本会话历史出现过的全部 fact 并入新回合
    row[0]
    for row in self._db.execute(
        "SELECT DISTINCT m.fact_id FROM turn_memory m JOIN turns t ... WHERE t.session_id=?",
        (session_id,),
    )
)
```

配合 `_complete_erasure` 的扩张规则：

```python
for identifier in list(affected):             # affected 已含第 1..N 回合
    row = SELECT session_id,created_at FROM turns WHERE id=?
    affected.update(item[0] for item in SELECT id FROM turns
                    WHERE session_id=? AND created_at>=?)   # ← 扩展到之后所有回合
```

**后果**：任何事实，从第一个引用它的回合起，其后全部回合的 `input` 都被改写成 `[已遗忘的对话]`、events/audio/元数据全删（`:997-1013`）。这不是「级联删除」，实际退化为「清空会话尾部」。

**为什么测试没抓到**：`tests/test_companion_integration.py:65` 只构造了两个**不同 session**；`tests/test_memory_backup_api.py:111` 只断言单回合 `turns[0]`。缺少「同一 session 多回合 + 遗忘其中一个早期被召回事实」的用例。

**MVP2 影响**：MVP2 §7 明确要求「记录并传播 `turn_id → guide_id/revision_id` 依赖」。如果沿用同一个并集模式，「切换/删除一份攻略」会抹掉整个会话，直接违反 §7「旧建议可以留在历史界面」和 A06/A07。

#### A2 🔴 任何一次遗忘或快照恢复，都会清空整个 checkpoint 库

位置：`src/ai_neko/runtime/service.py:1014-1027`

```python
for table in ("checkpoints", "writes"):
    if table in tables:
        database.execute(f"DELETE FROM {table}")  # ← 无 thread_id / turn_id 过滤
database.execute("PRAGMA wal_checkpoint(TRUNCATE)")
database.execute("VACUUM")  # ← 同步全库重写
```

测试 `tests/test_memory_backup_api.py:121-123` 直接断言 `COUNT(*) == 0`，说明是**刻意行为**。但这意味着：忘记一条偏好 ⇒ 所有会话的 LangGraph 执行记录全丢。MVP2 每轮会绑定 `match_id + guide_revision`，需要按 `thread_id`（现为 `{internal_id}:{turn_id}`）精确清理，否则无法区分「清理被删攻略的执行记录」和「误删无关会话」。

#### A3 🟡 `_erasure_failed` 阻塞面大、无运维可见性

`SessionRuntime` 有 6 个互相耦合的状态标志（`_closed` `_closing` `_close_task` `_memory_mutating` `_erasure_failed` `_mutation_lock`）。一旦 `_erasure_failed=True`，会话列表、回合、事件、ACK 全部返回 409（`:162` `:266` `:341` `:687`）。**修正（核验后）**：并非「只能退出重开」——错误信息本身提示「重试删除或重启」，且 `forget_memory`（`:896-900`）对 pending intent 有重试路径；但恢复/纠正等其它路径没有等价入口，且**没有任何日志或指标记录这次失败**（全文件无 logging 调用），`REVIEW.md` 要求「证据与未完成项」恰恰缺这一块。

#### A4 🟡 `events()` 游标语义是协议脆弱点（核验后降级：非现行 bug）

`runtime/service.py:638-641` 有两道 400 校验：游标不能超过 `next_seq - 1`，也不能超过 `sent_seq`。**核验后的准确表述**：取消回合时 `_settle` 只删 `seq > sent_seq` 的事件且 `next_seq` 不回退，此时用 `≤ sent_seq` 的游标轮询会**正确**返回空列表；原报告「取消后旧游标必得 400」未能在代码中证实。真实的脆弱点在于：`get_session`/`_turn_summary` 暴露的 `last_seq = next_seq - 1`（`:323`）**可以大于** `sent_seq`，若客户端混用两种游标来源会得到 400「不能跳过尚未发送的事件」。MVP2 要新增事件类型时，建议统一游标语义并在文档中钉死。

#### A5 🟢 凭据回滚语义瑕疵（核验后撤回「部分写入」断言，降级）

`src/ai_neko/config/providers.py:125-135`。**核验结论：原报告「前一个 kind 不在 `changed` 中 → 部分持久化」是错误的**——`changed.append(kind)`（`:130`）紧跟成功的 `set()` 之后，第二个 `set()` 抛错时第一个 kind 已在 `changed` 中，会被正常回滚。残留的轻微问题：① 回滚写入的 `previous[kind]` 来自 `Credentials.get()`，可能把**环境变量回退值**固化进 vault/进程内存；② 回滚本身失败时无防护；③ 非 Windows 平台凭据只在进程内存，回滚语义本就弱。优先级降为 🟢。

#### A6 🟡 缺少 `(session_id, created_at)` 索引；events/cancelled_requests 无保留策略

`turns` 只有 `PRIMARY KEY(id)` 与 `UNIQUE(session_id, request_id)`（`:99-106`）加活跃回合部分索引（`:111-112`）。`get_session`（`:345` `ORDER BY created_at,id`）、`_history`（`:352`）、以及 `_complete_erasure` 的 `created_at>=?` 扩张查询（`:985`）都走全表扫描 + 排序。`events`、`cancelled_requests`、`turn_*` 表没有任何清理或保留策略，随时间单调增长。**核验修正**：`get_session` 并非 O(n²)——`_payloads` 走 `(turn_id, seq)` 主键，准确问题是**每个回合 4+ 次查询**（events / audio_playback / turn_metadata / heard_prefix），n 回合即 4n 次查询的 N+1 模式，且同步执行（见 B1）。

#### A7 🟢 代码卫生

- `runtime/service.py:398` 函数内 `import hashlib`（应提到模块级）。
- 图片指纹 `hashlib.sha256(json.dumps(image, sort_keys=True))`（`:400`）对含 3MB base64 的整个 dict 序列化后哈希，每次请求（含重试）重复开销。应只指纹化元数据 + 摘要。
- `tools/web.py:270` `if media != "text/plain" and parser.login_form` 依赖 `and` 短路保护未定义的 `parser`（当前安全，但极易被后续重构打破）。
- `write_private_json`（`server.py:129-138`）与 `initialize_data_root` 的 marker（`paths.py:112-115`）先创建再 `chmod(0o600)`，存在受 umask 影响的极短权限窗口；而 `providers._save` / `voice._save` 已正确使用 `mkstemp`（0600）。建议统一为后者模式。

### B. 性能瓶颈

#### B1 🔴 整个后端在 asyncio 事件循环内执行阻塞式 I/O

`SessionRuntime` 与 `MemoryService` 全部使用**同步** `sqlite3`，而调用方几乎全是 `async def`：

| 阻塞点 | 位置 | 频次 |
| --- | --- | --- |
| 每个流式 token 落库（INSERT events + UPDATE next_seq，各自提交） | `service.py:192-200` `_emit` | 一次回答上百次 |
| `with self._db:` 事务 + `recall` + `get_persona` + `revision` | `service.py:401-510` `start_turn` | 每回合 |
| MemoryService 所有方法（且 `_transaction` 用 `BEGIN IMMEDIATE`） | `memory/service.py:194-207` | 每次调用 |
| `list_sessions` / `get_session` / `events` / `ack` / `audio_ack` | `service.py:262-731` | 每次轮询 |
| SQLite online backup **全库拷贝** | `memory/service.py:875-876` | 每次快照 |
| 对每个候选快照跑 `PRAGMA integrity_check` + 逐行读表校验 | `memory/service.py:812-846` | 每次快照列表，最多 100 个文件 |
| `DELETE` 全表 + `VACUUM` | `service.py:1014-1027` | 每次遗忘/恢复 |

**后果**：单个流式回答会周期性冻结事件循环；快照操作是秒级阻塞。MVP2 §9 的 **p95 ≤150ms 本地检索** 与 **取消→停音 p95 ≤300ms** 在这套结构下不可能达标。这是 MVP2 的头号障碍。

#### B2 🔴 检索是纯 Python 全语料现场分词，无法支撑 MVP2 规模

`MemoryService.recall`（`memory/service.py:473-552`）：

```
recall → list_facts() → 每条 fact 一次 source 查询（N+1）→ bm25_rank → 对每条 fact 全文重新 tokenize
```

MVP2 目标是「临时库 200 份有界文档，采用文档 ≤100 段」，单篇上限 50,000 字符。一次查询要现场对上万段落做 CJK 2/3-gram 分词 —— 目前个人记忆只有几十条所以没有暴露，进入 G3 会立刻击穿。

同类问题：`_erase`（`:406-412`）每次把**整张 `memory_fact_sources` 拉进 Python** 做闭包传播，并在 `while changed` 里重复全表扫描。

#### B3 🔴 快照机制按「记忆库很小」设计，与 MVP2 数据量直接冲突

MVP2 §4 表格写的是攻略也放在「本工程 `memory/long-term.sqlite`」。但：

- `backup()` 用 `self._db.backup(target)` **整库在线拷贝**，不加过滤；
- `_BACKUP_MAX_BYTES = 256MB`，而 §4 拟定攻略缓存总额 **100MiB**；
- 每次快照 = 复制全部正文，**且阻塞事件循环**（B1）；
- `_read_backup` 恢复时逐行读全表进内存做 JSON 校验；
- 快照无法「只排除正文」。

结果：用户每做一次记忆快照就要复制上百 MB 并卡住 UI，而 §7 又要求「v0.4 旧快照恢复时不清空新资料」——需求本身合理，但当前实现扛不住。

#### B4 🟠 每回合新建 checkpoint 连接，且 checkpoint 只增不减

`service.py:537-539` 每回合 `AsyncSqliteSaver.from_conn_string(...)`，`thread_id = f"{internal_id}:{turn_id}"` 每回合一个命名空间，从不回收（除 A2 的全局清空）。MVP2 会话更长、工具轮次更多，增长更快。

#### B5 🟠 HTTP 客户端零复用

`network.client()`（`tools/network.py:100-108`）每次请求新建 `httpx.AsyncClient`，`max_connections=1, max_keepalive_connections=0`。`read_web_page` 的重定向循环（最多 5 跳）与多源读取意味着反复 TLS 握手。安全理由（隔离 cookie）成立，但可用「共享 client + 显式禁用 cookie jar」替代。

#### B6 🟠 Electron 主进程逐像素扫描

`desktop/lib/vision.cjs` `capture()`：`for (offset... pixels.length; offset += 4)` 遍历 1366×768 位图（约 100 万次迭代），在**主进程同步执行**。频次 = 每次截图。建议改采样抽样或移到 utility process。

### C. 安全隐患

> 说明：CSP、`trustedSender`、`localAsset` 路径逃逸防护、off-the-wall 图片 120 秒 TTL、提示注入边界（untrusted_data 标记 + 引用过滤 `CitationFilter`）、SSRF/私网阻断（`pin_url` + `public_ip`）整体做得相当扎实，以下问题多为「规模扩大后才暴露」的类别。

| 级别 | 问题 |
| --- | --- |
| 🟠 | **无版本化数据库迁移框架**。现在只在构造函数里 `ALTER TABLE ADD COLUMN` 兜底（`runtime/service.py:137-143`、`memory/service.py:179-184`）。MVP2 要新增 guide / revision / chunk / selection / match 整套表 + `schema_version`，G6 的 A10「升级恢复」必须有可回滚、可中断续跑的迁移。这是 MVP2 **最早**要落地的基建。 |
| 🟠 | **`restore` 依赖列顺序**。`memory/service.py:960-967` 用 `tuple(row.values())` 回灌，契约是「快照的表结构必须与当前库 `PRAGMA table_info` 完全一致」。已有校验（`:776-781`）缓解，但新增表/列必须同时更新 `_BACKUP_TABLES` 与该校验，容易漏。 |
| 🟡 | `_erasure_failed` 状态无运维可见性（见 A3）。 |
| 🟡 | `memory_erasure` / `cancelled_requests` 无 TTL 清理，可被无限写入（单用户本地，风险低）。 |
| 🟡 | 全局快捷键 `CommandOrControl+Shift+Space` 被占用时静默失败，无提示。 |
| 🟡 | `render-process-gone` 直接 `app.quit()`（`main.cjs:230`），丢失未结算回合状态。 |
| 🟢 | `list_backups` 对「可读但内容非法」的快照返回 `restorable: false` 但仍允许删除，对「完全损坏」的快照既不列出也不删除（刻意设计）。MVP2 快照数量上后，用户会积压无法清理的文件。 |

### D. 可维护性与技术债

| 级别 | 问题 |
| --- | --- |
| 🔴 | **两套平行的 LangGraph**。`ai_neko.graph.GraphService` 是 M0 合成 demo（自有 `scopes.sqlite` / `graph.sqlite` / `.graph.lock`、同步 API、`DemoState`），仅被 `graph/__main__.py`、`diagnostics.py`、`tests/test_graph.py` 使用；真正的对话编排在 `ai_neko.chat.graph`。这违反 AGENTS.md「LangGraph 是唯一对话决策中心」，MVP2 要在 `chat/graph.py` 加检索节点时新人极易改错文件。建议MVP2 前删除或明确改名（如 `ai_neko._m0_demo`）+ 在 ARCHITECTURE 划线。 |
| 🟠 | **文档重量远超代码**：`PLAN.md` 50KB + `REVIEW.md` 45KB + `WORKLOG.md` 70KB + `reference-manifest.json` 161KB + `mvp1-assets-manifest.json` 31KB + `m0-reuse-manifest.json` 28KB。三者职责边界重叠，MVP2 每次改动同步三处，是真实人力债。 |
| 🟠 | **`runtime/service.py`(1080) 与 `memory/service.py`(1052) 两个巨型类**，同时承担 schema 迁移、事务控制、业务规则、错误语义、清理协调、任务生命周期。MVP2 G2/G4 都要修改它们，建议先拆出「后台任务协调器」与「擦除/恢复协调器」。 |
| 🟠 | **无统一 API 契约**。路由白名单是 `desktop/lib/security.cjs` 的手写正则，实现是 `app/api.py` 的手写路由，消费方是 `desktop/renderer/app.js`，测试是 `desktop/tests/*.cjs`。MVP2 §8 要新增 `guides` / `guide-selection` / `matches` 三组 API，**四处手工同步，最容易漏配导致「接口写了但桌宠用不了」**。 |
| 🟠 | **前端 `renderer/app.js` 是 981 行单 IIFE、52 个内部函数、无 ES module**（index.html 142 行）。G5 要加攻略库面板、来源卡片「采用」按钮、对局入口，会继续膨胀。 |
| 🟡 | 版本号三处手写同步：`pyproject.toml` 0.4.0、`src/ai_neko/__init__.py` 0.4.0、`desktop/package.json` 0.4.0。 |
| 🟡 | `packaging/ai-neko.spec` 手工罗列 hidden imports / licenses；双语言双包管理器（uv + npm）无统一入口。 |
| 🟡 | `ruff` 报 21 处错误（全部在 `docs/diagrams/tools/validate-docs.py`），`select` 规则集偏窄（仅 E4/E7/E9/F/I），未启用 B/SIM/ASYNC/PL。**建议 MVP2 前启用 ASYNC 规则集**（正好能自动捕获 B1 类问题）。 |
| 🟡 | 11 个「真进程」测试在本地受限环境全红（见 D 下方说明）。 |

#### D-附：本地测试现状（已核验，非产品缺陷）

本机 `uv run pytest -q`：**564 passed / 11 failed / 1 skipped**。失败的 11 个全部集中在派生子进程/二进制的用例（`test_config`、`test_desktop_backend`、`test_graph`、`test_m1_api`、`test_memory`(×2)、`test_memory_backups`、`test_memory_recall_acceptance`、`test_runtime`、`test_server_process`(×2)）。逐个跟踪确认失败原因为宿主沙箱向子进程注入 `sitecustomize.py` 触发 `PermissionError: EEXIST ... pytest-of-root`，**不是源码缺陷**。但这些恰恰是承担「进程崩溃后持久性」「安装包实际下载核对」等关键保证的用例，其环境脆弱性会掩盖真实回归，建议加环境探测 skip 而非静默失败。

---

## 第二部分 · MVP1 优化优先级

### 🔴 P0：进入 MVP2 前必须优化

| # | 优化项 | 理由 | 影响范围 | 预估工作量 |
| --- | --- | --- | --- | --- |
| P0-1 | **把 SQLite / 文件系统 / CPU 密集操作全部移出事件循环**（`asyncio.to_thread` 或专用 single-thread executor + 串行队列） | 直接决定 MVP2 §9 所有 p95 目标能否达成；不先做，G3/G6 的性能证据不可信 | `runtime/service.py` 全部公开方法、`memory/service.py` 全部公开方法、`list_backups` / `backup` / `_complete_erasure` | 大（但机械） |
| P0-2 | **修复遗忘级联**：`turn_memory` 只记录「本回合真实引用」，删除会话历史并集；`affected` 扩张只覆盖含证据的回合 | 现行为等同清空会话（A1）；MVP2 要用同一路径传播攻略依赖（§7），不改会放大成数据丢失 | `runtime/service.py:483-494`、`:962-1002` + 新增多回合会话回归用例 | 中 |
| P0-3 | **checkpoint 清理按 `thread_id` 精确化**，去掉全局 `DELETE` + `VACUUM` | MVP2 每轮绑定 `match_id + guide_revision`，需要区分清理边界（§7） | `runtime/service.py:1014-1027` | 中 |
| P0-4 | **建立版本化迁移框架**（`schema_version` + 事务迁移 + 迁移前备份 + 失败回滚） | MVP2 G1 就要新增整套攻略表；G6 A10 必测 v0.4→新版升级与中断续跑 | 新增模块；收编现有两处 `ALTER TABLE` 兜底 | 中 |
| P0-5 | **替换检索实现：SQLite FTS5（`trigram` tokenizer，无新增依赖，天然支持中文）+ 自建倒排索引层** | 现 `bm25_rank` 全语料现场分词无法支撑 200 文档 × 100 段 | `memory/recall.py`、`memory/service.py` | 中～大 |
| P0-6 | **流式事件刷盘改为「内存累积 + 定量/定时 flush + 终端强刷」** | 每 token 一次事务提交是 B1 的最大单项来源 | `runtime/service.py:183-200` + `_settle` 保证不丢 | 小～中 |
| P0-7 | **删除或隔离 `ai_neko.graph` M0 demo 包** | 两套 LangGraph 违反架构约定，MVP2 加节点时易改错文件 | `src/ai_neko/graph/`、`tests/test_graph.py`、`diagnostics.py`、`__main__.py` | 小 |
| P0-8 | **收敛 PLAN / REVIEW / WORKLOG 三份文档职责** | MVP2 每次改动同步三处是持续人力支出 | `docs/PLAN.md`、`REVIEW.md`、`WORKLOG.md` | 小 |

### 🟡 P1：可延后到 MVP2 内部处理

| # | 优化项 | 理由 | 影响范围 |
| --- | --- | --- | --- |
| P1-1 | `list_facts` 的 N+1 源查询 → 一次 JOIN | 与 P0-5 同批做成本最低 | `memory/service.py:463-471` |
| P1-2 | `network.client()` 复用连接池（保留禁用 cookie/env proxy/trust_env） | 网页读取多跳时收益明显 | `tools/network.py:100-108` |
| P1-3 | 补 `(session_id, created_at)` 索引 + 定义 events / cancelled_requests 保留策略 | 会话变长后才痛 | schema 迁移（并入 P0-4） |
| P1-4 | 快照机制支持「剔除正文」或增量备份 | 依赖 P0-4/B3 的决策（正文是否独立库） | `memory/service.py:858-894` |
| P1-5 | `list_backups` 去掉 `PRAGMA integrity_check`，改为恢复时才校验 | 100 个快照时是分钟级操作 | `memory/service.py:812-846` |
| P1-6 | `renderer/app.js` 拆为 ES modules | G5 前拆完收益最大 | `desktop/renderer/` |
| P1-7 | 建立 API 契约 SSOT（Schema 文件 → 生成 Python 路由 + 桌面端正则白名单 + 类型） | §8 新增三组 API 时的防漏网 | `src/ai_neko/app/api.py`、`desktop/lib/security.cjs` |
| P1-8 | `vision.cjs` 黑屏检测改采样或移出主进程 | 单次约 100 万次迭代 | `desktop/lib/vision.cjs` |
| P1-9 | 版本号三处统一 + spec 透传 | 发布门禁防错配 | `pyproject.toml`、`__init__.py`、`desktop/package.json` |
| P1-10 | ruff 启用 `ASYNC` / `B` / `SIM` 规则集，修 `docs/diagrams/tools/validate-docs.py` 的 21 处 | 自动化捕获 B1 类问题 | `pyproject.toml` |
| P1-11 | 子进程测试加环境探测 skip，失败信息带原始 stderr | 防止环境失败掩盖真实回归 | `tests/*` |
| P1-12 | `0600` 创建统一用 `mkstemp`；凭据回滚避免把环境变量回退值固化进 vault（A5 残留项） | 低概率但涉及凭据 | `config/providers.py`、`config/paths.py`、`app/server.py` |
| P1-13 | `_erasure_failed` 加结构化日志 + 用户可见的重试入口 | 可观测性缺口 | `runtime/service.py` |

---

## 第三部分 · MVP2 预备：需要提前预留接口或重构的地方

> 原则：**MVP2 不要往现有「并集传播 + 全局清理 + 同步 I/O」的骨架上加表**。下面 8 项如果在 G1 之前不定稿，G2–G5 大概率返工。

### 1）⚠️ 最重要：不要把攻略正文放进 `memory/long-term.sqlite`

MVP2 §4 写的是「沿用本工程 `memory/long-term.sqlite`」，但这与现有快照/恢复设计直接冲突（B3）：

- `backup()` 整库拷贝 → 每次快照复制 100MiB 且冻结 UI；
- `_read_backup` 逐行全表读进内存校验 → 恢复变慢；
- `_BACKUP_TABLES` 是硬编码白名单 → 新表要手工登记，漏一个就静默不参与快照。

**建议**：新增**独立的 `guides.sqlite`**（同一 DataPaths 下另一个 app-owned 文件），或至少给 `long-term.sqlite` 加「表分组」概念并让 `backup()/restore()/_BACKUP_TABLES` 支持按组排除。这样：

- 记忆快照保持小巧快速，恢复语义不变；
- 攻略有自己的增长上限与 LRU 清理而不污染记忆；
- §7「旧快照没有攻略表 → 保留当前攻略库」的语义天然成立，不需要特判。

### 2）检索抽象层与 LangGraph 节点预留

MVP2 §5 要求「有已采用攻略时，确定性本地检索**先于**模型规划」。当前图的拓扑是：

```
START ─(guide)─> plan ─(有 calls)─> tools ─(rounds<3)─> plan ──> answer
```

`answer` 节点没有「外部注入的本地资料」入口，`plan` 总是先联网。**必须在 MVP2 前预留：**

- 一个 `retrieve` 节点位，位于 `START → plan` 之前，产出 `{references, budget_used, selection_revision}`；
- `answer` 节点支持两条证据来源（本地引用 / 工具来源）的分开标注；
- `plan` 在 `guide` 模式下，若 `retrieve` 已命中则跳过工具轮（目标：A02「每轮搜索/取页均 0 次」）。

建议把 retrieval 抽成 `Retriever` 协议（`recall(query, selection, budget) -> list[Citation]`），先用 BM25/FTS5 实现，后续换向量不需要改图。

### 3）稳定 ID 与每回合引用重生成

现状：`_bounded_source` 里的 `[S#]` 是本回合位置编号（`S{len(sources)+1}`），而 `_history`（`runtime/service.py:370`）用硬编码正则把历史引用替换成 `[历史来源：需重新检索]`。

MVP2 §5 要求「每轮用稳定 `文档/版本/段落ID` 重新生成 `[S#]` 映射」。需要：

- `Citation` 携带 `{guide_id, revision_id, chunk_id, local: true}`；
- `_history` 的正则替换 → 改为基于持久 ID 的显式重映射函数；
- `CitationFilter`（`chat/graph.py:51`）的 allowed 集合由 manager 生成，而不是单次状态。

### 4）会话/回合元数据的扩展点

`turn_metadata` 的 JSON 列目前记录 `{persona_version, memory_revision, image}` —— 这是一个**很好的扩展点**，MVP2 只需追加 `{selection_revision, guide_id, revision_id, match_id, state_revision}` 即可获得「每一轮用哪份资料回答」的可核查证据（A01/A06/A09 都需要）。**务必在 G1 定稿字段名**，避免 G4 回头改 SQL。

### 5）修订 barriers 统一建模

现在已经有三套修订：`persona_version` / `memory_revision` / `invalidation_revision`。MVP2 再加 `selection_revision` / `guide_revision` / `state_revision`（§7）。建议抽象成一个 `Revision` 小类型 + 统一的「发生时检查 / 提交时校验」约定，而不是每处手写 `_integer` + 抛不同异常。否则「在发给模型、接收流和写库前复核绑定」这条要求无法系统性验证。

### 6）跨「五个边界」的统一取消协调器

MVP2 §7 要求「持久化失效修订 → 停止播放 / 撤销未提交图片与请求 → 清理旧任务」，覆盖**抓取、检索、模型生成、TTS 排队、播放**五个边界。

现状是三套并行机制：`turn task`（`_tasks`）、`voice task`（`voice_tasks` + `voice_cancelled` 词典 + 180s 过期）、`memory worker`（`_memory_task` + lease）。这是 A06（切换/删除竞态）最难测对的地方。**建议在 MVP2 前抽出一个统一的 `TaskCoordinator`**：统一 request_id / lease / 取消令牌 / 「撤销后 180 秒拒绝重试」语义。

另外：`turn_id` 与 `request_id` 两套标识符目前并行，MVP2 的语音控制要用「独立控制响应 ID」（§8），正好一起并入。

### 7）前端与 IPC 的 MVP2 入口预留

- 来源卡片「采用这份攻略」按钮 → 需要新 IPC + 新的 `ai-neko:request` 路由白名单正则。**这是 P1-7 契约 SSOT 的最大受益点**：现在改一处漏一处会表现为「静默 404」。
- `preload.cjs` 的 `Object.freeze` API 面需要新增 guide / match 相关方法。
- 建议在 G5 前把 `renderer/app.js` 拆为 `chat.js` / `guide.js` / `match.js` / `settings.js`（P1-6），否则 G5 会再增加 400+ 行到同一个闭包。

### 8）埋点与性能度量基建

MVP2 §9 要求 p95（本地检索 ≤150ms、取消→停音 ≤300ms）、20 组冷暖对照、token/费用记录。当前**完全没有应用内度量**：所有延迟数据都是人工从 `docs/evidence/*.json` 里整理的一次性观测，`REVIEW.md` 也反复强调「单次 70ms 不是 p95」。

建议在 MVP2 前落地一个极小的结构化指标 sink（本地 JSONL + 分位数计算），至少覆盖：本地检索耗时、首字/首音延迟、工具调用次数、取消到音频停止延迟、模型 token 计数。**这是 G6 能否产出可信报告的前提**，且必须在 G3/G4 之前就埋好。

---

## 执行建议：G1 之前的最小准备批次

按依赖顺序，建议MVP2 实际开工前先做完这一批（其余并入各关卡）：

1. **P0-4 迁移框架** （先把地基垫好，后面所有加表都靠它）
2. **P0-7 删除 M0 demo 图** + **P0-8 文档收口** （低成本，立刻降低认知负担）
3. **第三-1 项决策：攻略是否独立 SQLite** （决定 G1/G6 的全部形态，必须先定）
4. **P0-1 异步化** （建议与 G1 并行，先覆盖 `MemoryService`，再覆盖 `SessionRuntime`）
5. **P0-2 / P0-3 修复遗忘与 checkpoint 清理语义** （G2 直接依赖）
6. **P0-5 换 FTS5** + **第三-2 项 `Retriever` 接口**（G3 直接依赖）

> 若时间紧张，P0-1 可以只先做 `MemoryService` 部分——MVP2 的检索热点在那里；`SessionRuntime` 的事件刷盘（P0-6）单独一笔性价比很高，可以先做。

---

## 附录 · 事实核验记录（2026-09-26 第二轮）

应要求对本报告全部 `file:line` 引用与行为断言做了逐条回源核验。**必须坦白的核验背景**：本报告初稿对 `runtime/service.py` 的引用是在该文件只读入前 56 行的情况下写出的（大文件读取被截断），因此该文件的引用属于重点复核对象。第二轮已**完整通读全部 1080 行**并逐条比对，结论如下。

### ✅ 核验属实（含精确行号）

| 断言 | 证据 |
| --- | --- |
| A1 遗忘级联：`turn_memory` 会话历史并集 + `created_at>=` 扩张 | `runtime/service.py:483-494`（含 `:484` 原文注释「Propagate dependencies through historical answers」）、`:962-1002`、`:997-1013`，行号与代码引用逐字一致 |
| A2 checkpoint 全局清空 + VACUUM | `runtime/service.py:1014-1027`；`tests/test_memory_backup_api.py:121-123` 断言 `COUNT(*)==0` |
| A3 `_erasure_failed` 四处 409 | `:162` `:265-266` `:341-342` `:687-688` |
| A6 缺 `(session_id, created_at)` 索引、无保留策略 | `turns` schema `:99-106`、部分索引 `:111-112`、三处全表排序 `:345` `:352` `:985` |
| A7 `import hashlib` 在函数内、图片指纹哈希整 dict | `:398`、`:400` |
| B1 各阻塞点 | `_emit :192-200`、`start_turn :401-510`、`memory/service.py:194-206`（`BEGIN IMMEDIATE`）、`backup() :875-876`、`list_backups :812-846`（`integrity_check :765`） |
| B2 检索现场分词 + N+1；`_erase` 全表闭包 | `memory/service.py:473-552`、`:406-412` |
| B3 `_BACKUP_MAX_BYTES=256MB` vs 规划 100MiB | `memory/service.py:53`；`NEXT-GUIDE-COMPANION.md:81` |
| B4 每回合新建 `AsyncSqliteSaver`、thread 命名空间 | `runtime/service.py:537-539`、`:563` |
| C 迁移兜底、`restore` 列序依赖 | `runtime/service.py:137-143`、`memory/service.py:179-184`、`:960-967`、`_BACKUP_TABLES :54-62` |
| D 文档体积、`app.js` 规模、GraphService 仅测试/CLI 使用、ruff 21 处全在 `validate-docs.py` | `ls -la` 实测（PLAN 50,101B / REVIEW 45,180B / WORKLOG 70,237B / reference-manifest 161,465B）；`wc -l` 981 行、`grep -c function` 52；全局 grep 引用点；`ruff --output-format concise` 实测 21/21 |
| 本地测试 564/11/1 与失败根因 | 本机 `uv run pytest -q` 实测；11 个失败均为子进程型用例，逐个跟踪为宿主沙箱 `sitecustomize.py` 注入导致 `PermissionError: EEXIST ... pytest-of-root` |
| 第三-2 图拓扑、`_history` 正则、`_bounded_source` 位置编号、`turn_metadata` 字段 | `chat/graph.py:327-335`、`runtime/service.py:370`、`chat/graph.py:232`、`runtime/service.py:470-479` |

### ✏️ 核验后修正（报告已就地改）

1. **A5 撤回「部分持久化」断言并降级 🟡→🟢**。`changed.append(kind)`（`providers.py:130`）紧跟成功的 `set()` 之后，第二个 `set()` 失败时第一个 kind 会被正常回滚；初稿的窗口分析不成立。残留项（环境变量回退值固化、回滚无自保护）保留。
2. **A3 修正「只能退出重开」**。`forget_memory:896-900` 存在 pending intent 重试路径，错误信息本身也提示「重试删除或重启」；阻塞面大与无日志的结论不变。
3. **A4 降级为「协议脆弱点」**。初稿「取消后旧游标必得 400」未能在代码中证实；真实的点是 `last_seq=next_seq-1`（`:323`）可能大于 `sent_seq`，混用游标来源会 400。
4. **A6 删除「O(n²)」**。`_payloads` 走 `(turn_id, seq)` 主键；准确问题是每回合 4+ 次查询的 N+1 模式。
5. **A1 措辞**：「从第 1 回合起」→「从第一个引用该事实的回合起」，避免过度概括。
6. **A7** 删除初稿残留的乱码片段「`preserve rights`: 」；`paths.py` / `server.py` 行号补全为区间。

### 核验结论

两个红色核心断言（A1 遗忘级联、A2 checkpoint 全局清空）与头号性能断言（B1 同步 I/O）**经完整源码回读确认属实**，可作为 MVP2 前修复的依据；A4/A5 两处初稿断言不准确已修正，A3/A6 措辞已收紧。若仍不放心，建议抽查顺序：A1（`service.py:483-494` + `:962-1013`）→ A2（`:1014-1027`）→ B1（`:192-200`）三处即可覆盖全部红色结论。

---

## 附录二 · 处理结果（2026-09-26 第三轮，已实施）

测试基线（2026-09-27 终验）：`uv run pytest tests/` **565 passed / 12 skipped / 0 failed**（11 个环境敏感用例经探测显式 skip + 1 个 Windows 专属；此前两轮长跑中 `test_server_process.py` 个别网络用例偶发失败，终验环境压力恢复后全绿，确认为沙箱负载抖动而非回归），`npm --prefix desktop test` **58/58**，`uv run ruff check .`（E4/E7/E9/F/I/B/SIM）**0 错误**。新增回归用例 `test_forgetting_one_fact_preserves_unrelated_turns_in_same_session`（同一 session 三回合，遗忘一条事实后无关回合与其 checkpoint 存活）。

### 已修复

| 条目 | 处理 |
| --- | --- |
| A1 遗忘级联 | `turn_memory` 只记录本回合真实注入的事实（删会话历史并集）；`_complete_erasure` 删除 `created_at>=` 扩张，改为「内容证据扫描」（从 memory 层取回被删内容，单次遍历 events 找出仍引用被删文本的回合）；证据只存在于内存，不落盘（tombstone/意图仍为纯 ID） |
| A2 checkpoint 全局清空 | 按 `internal_id:turn_id` thread 精确 `DELETE`；去掉整库 `VACUUM`（保留 `secure_delete=ON` + `wal_checkpoint(TRUNCATE)`） |
| A3 可观测性 | 新增 `logs/runtime.jsonl` 操作日志：`memory_erasure_failed` / `memory_erasure_completed`（forget/restore/retry 三类） |
| A5 凭据回滚 | `Credentials.has()` 区分「实际存储」与「环境变量回退」；回滚恢复未存储语义（providers 与 voice 两处） |
| A6 索引/保留 | 新增 `turns(session_id, created_at)` 索引；`cancelled_requests` 启动时清理 30 天前记录 |
| A7 代码卫生 | `hashlib` 提至模块级；图片指纹改为元数据+内容摘要（不再每次序列化 4MB）；`web.py` parser 显式 `None` 判断；`write_private_json` 与数据根 marker 改为创建即 0600（`os.open`） |
| B1 同步 I/O | recall、快照列表、备份、删除快照、恢复、遗忘、记忆提取任务全部 `asyncio.to_thread`；流式 text 事件改为内存缓冲（16 条或 50ms 或非 text 事件触发刷盘，settle/close 强刷），取消路径语义不变（settle 先刷盘再按 sent_seq 截断） |
| B2 检索 | `list_facts` N+1 → 单 JOIN；`bm25_rank` 支持预分词 `_terms` 输入 + `MemoryService` 按 `(updated_at, stop_names)` 缓存分词 |
| B6 视觉 | 黑屏检测改稀疏采样（大图每 16 像素取 1） |
| C2 restore 列序 | 回灌按**当前** schema 显式列序取值，旧快照缺列用默认值；不再依赖 `row.values()` 顺序 |
| 快照列表 | `list_backups` 保留逐行内容校验（`restorable` 语义不变），去掉全 B-tree `integrity_check`（恢复路径仍执行） |
| 快捷键 | 全局快捷键注册失败时向 UI 发状态提示（不再静默） |
| D1 demo 图 | `ai_neko/graph/__init__.py` 加醒目 banner；`ARCHITECTURE.md` §2 划线「唯一对话图是 `ai_neko.chat.graph`」 |
| D7 ruff | `select` 扩为 E4/E7/E9/F/I/B/SIM；修复全部 47 处（含 `validate-docs.py` 21 处；SIM118 在 `sqlite3.Row` 与 SIM112 在 GitHub runner 环境变量上为误报，已 noqa 说明） |
| D8 子进程测试 | 新增 `tests/conftest.py::sandbox_compatible` 探测 fixture；查明 11 个失败的统一根因为**宿主沙箱对同一路径第二次 `mkdir(exist_ok=True)` 抛 EEXIST**（父进程建目录→子进程再初始化即触发），环境不符时明确 skip 而非误报失败 |

### 保留行为（有意不改）

- **A4 `events()` 游标 400**：受 `test_ack_cannot_claim_untransmitted_content_or_move_backward` 保护的投递记账契约。已补注释钉死语义：游标只能来自 `events()` 响应；`get_session` 的 `last_seq`（=next_seq-1）不可用作游标。

### 延期（附理由）

| 条目 | 理由 |
| --- | --- |
| B1 剩余轻量读路径（list_sessions/get_session/events/ack/audio_ack） | 单次亚毫秒级；与 MVP2 迁移框架（P0-4）一并系统化，避免现在为每个方法手写线程边界引入新竞态 |
| P0-4 迁移框架 | 评审自身排期在 G1；当前两处 ALTER 兜底够用，现在建框架无新表可迁 |
| P0-5 FTS5 替换 BM25 | 排名行为变化需要 MVP2 标注集（A03）验证后才能判定是否达标；已完成前置（N+1 与分词缓存），切换点保留在 `bm25_rank` 单处 |
| B3 攻略独立 guides.sqlite | 是 MVP2 G1 的设计决策而非当前代码缺陷；报告第三-1 已记录，G1 前定稿 |
| B4 checkpoint 清理/保留策略 | `test_memory_recall_acceptance` 把 per-turn checkpoint 线程作为证据断言；清理策略需与保留策略一并设计（随 P0-4） |
| B5 HTTP client 复用 | 每次请求全新 cookie jar 是刻意的安全姿态（`network.client()` 注释）；复用需先解决 jar 隔离，收益有限 |
| D1 demo 图删除 | `graph` CLI 与 GraphService 承载 Windows 打包冒烟的**跨进程 checkpoint/scope 证据**（`package_smoke.check_graph_recovery/check_scope_separation`），属发布闸门；在 Mac 上盲改冒烟脚本的风险高于收益，已显式标记防误改 |
| D2/D3/D4/D5/D6 文档收口、巨型类拆分、API SSOT、app.js 拆分、版本号统一 | 分别并入 G1（迁移框架）、G2（新增 API 时做契约 SSOT）、G5（前端拆分）；版本号与发布流程一并处理 |
| events/turn_* 表保留策略 | 历史展示直接依赖 events 全量存在，清理策略是产品决策，不能擅自删历史 |

### 修复过程发现并处理的连带问题

- ruff 扩展抓到 SIM118/SIM112 两处**规则误报**，若盲改会引入真 bug（`key in sqlite3.Row` 查的是值不是列名；`ImageOS` 是 GitHub runner 原始大小写），已 noqa 并注明。
- `test_memory_backups.py::test_restore_recovers_when_memory_commits_before_history_cleanup` 的 monkeypatch 桩签名随 `_complete_erasure` 增加证据参数而更新。
- 两个新增 `import pytest`（test_m1_api / test_memory_recall_acceptance，装饰器需要）。
