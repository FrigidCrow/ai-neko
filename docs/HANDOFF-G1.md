# 交接单：下一步工作（G1 攻略入库）

日期：2026-09-27。写给接手的 session：本文件 + `AGENTS.md` + `docs/NEXT-GUIDE-COMPANION.md` 是全部必需上下文；尽量不依赖历史对话记忆。

## 1. 当前状态快照

- 分支 `workbuddy/codex-companion-five-capabilities-18174213`，本地领先远程 1 个提交：
  - `e53b529` G1 前准备批次（**尚未推送**）
  - `7ff0117` / `235342d` 已推送，CI [36258099207](https://github.com/FrigidCrow/ai-neko/actions/runs/36258099207) 全绿（Windows 577 passed / 0 skipped + 打包冒烟）
- 测试基线：本地 pytest **584 passed / 12 skipped / 0 failed**；desktop `npm test` 58/58；`ruff check` + `ruff format --check` 全绿
- 上一阶段（评审→修复→核验→提交）完整记录见 `docs/MVP2-READINESS-REVIEW.md`（附录二为修复清单）；工程记录在 `REVIEW.md` / `WORKLOG.md` 末尾

## 2. 立即事项（开工 G1 之前）

**推送 `e53b529` 触发 CI 验证准备批次**（用户此前已同意推送触发 CI 的模式，但按 `AGENTS.md` 规则推送前仍需用户确认）：

```bash
git push                                    # 分支已设 upstream
gh run list --branch workbuddy/codex-companion-five-capabilities-18174213 --limit 1
gh run watch <run-id> --exit-status
```

预期：Windows/Linux 测试全绿（本地 12 个 skip 里 11 个是本机沙箱问题，CI 上应全部通过；1 个 Windows 凭据专属只在 Windows 跑）。CI 全绿后 G1 才算站在已验证的地基上。

## 3. G1 工作单：攻略入库

依据：`docs/NEXT-GUIDE-COMPANION.md` §4（数据契约）、§8（G1 行）、§9（验收）。完成定义：**完整性、来源、版本、重复入库、重启读取、作用域及限额用例通过；正文在模型上下文裁剪之前入库**（20,000/6,000 字符裁剪前）。

### 3.1 已备好的地基（勿重复建设）

| 地基 | 位置 | 用法 |
| --- | --- | --- |
| 攻略库落位决策 | 独立 `guides.sqlite`，目录 `paths.guides`（数据根 `guides/`，已创建） | 新库从迁移框架起步 |
| 迁移框架 | `src/ai_neko/config/schema.py` | 新表定义 v2 步骤（guides 库从 baseline 1 开始；`steps=[(2, 建表)]`）。**语义**：user_version 是台账，信任它；模拟旧库测试必须连同 `PRAGMA user_version=0` 一起回退（见 `tests/test_audio_context.py:152-158` 的写法） |
| 埋点 | `src/ai_neko/config/telemetry.py`（`runtime.metrics`） | G1 的入库/检索耗时建议 `record("guide_ingest_ms", ...)` / `guide_retrieve_ms`（命名规则：小写+下划线，≤64 字符） |
| 检索接口 | `src/ai_neko/retrieval.py` | G3 才接图；G1 的段落检索实现先落库层 |

### 3.2 G1 表设计要点（从规划 §4 提炼，字段名可按既有风格调整）

- **guide_documents**：`guide_id`、scope、原 URL/最终 URL、标题、game/platform/mode、创建时间、删除修订
- **guide_revisions**：`revision_id`、正文、`content_hash`（同内容去重）、内容日期（≠抓取时间）、抓取/最后核查时间、游戏版本及依据、完整性（`full`/`partial`）、访问状态、可选 ETag/Last-Modified。**不可变内容版本**
- **guide_chunks**：`chunk_id`、`revision_id`、标题层级、原文范围、文本与检索字段（可重建派生数据）
- 限额：单篇正文 ≤50,000 字符；自动资料缓存总额 100MiB，未采用可 LRU 清理
- 入库边界：受限/图片未读/超限标 `partial` 并提示，**不能凭 HTTP 200 标成全文完成**；snippet/登录页/空页面不能伪装为已保存正文
- 同 URL 同内容不生成重复版本；URL 规范化不随意删查询参数

### 3.3 必须遵守的边界

- 攻略是「资料」，不是个人事实：不写 `memory_facts`，不走人格/记忆快照
- `ai_neko.graph` 是 M0 诊断 demo（有 banner 标记），**产品逻辑一律进 `ai_neko.chat.graph`**
- Memory Service 统一管理 guides.sqlite（app-owned 文件），但快照/恢复与记忆快照**分开**
- 规划 §5 明确：G1 只做入库；采用关系（G2）、本地优先检索（G3）不要顺手做掉
- 实现前先更新 `docs/PLAN.md`（17.1 后加 G1 任务/验收），完成后写 WORKLOG/REVIEW——见 `AGENTS.md`「开发与验收」

## 4. 本机环境与流程注意事项（不在任何仓库文档里）

1. **macOS BSD grep 不支持 `\|` 交替**——用 `grep -E` 或 Grep 工具，否则静默返回空，极易误判「代码不存在」。
2. **沙箱会拦截子进程与二次 mkdir**：同一路径第二次 `mkdir(exist_ok=True)` 抛 EEXIST。相关测试已接 `sandbox_compatible` fixture（`tests/conftest.py`），失败原因会显示为 skip。**CI 上这些用例正常跑**。
3. **pytest 长时间运行要用独立 basetemp**：`--basetemp=/tmp/<唯一名>`，否则与残留会话冲突出现批量 EEXIST 假失败。
4. **CI 有 format 门禁**：`ruff format --check src tests scripts packaging`。提交前必须本地跑 `uv run ruff format src tests scripts packaging`；ruff 新版会顺带格式化 markdown 内嵌 Python 代码块，属预期。
5. **大文件读取会被截断持久化且无法回读**：读 `runtime/service.py`（约 1200 行）这类文件必须分段 Read；**绝不能凭部分内容写 file:line 引用**（历史教训见 `docs/MVP2-READINESS-REVIEW.md` 附录一）。
6. `runtime/service.py` 现有机制速查：流式 text 事件内存缓冲（16 条/50ms 刷盘，settle 强刷）；遗忘走「turn_memory 本回合真实引用 + 内容证据扫描」（needles 只在内存，持久意图纯 ID）；checkpoint 按 `internal_id:turn_id` 精确清理。
7. 推送=外部动作，先问用户；tag 推送会走发布路径，别误触。

## 5. 文件索引

| 内容 | 文件 |
| --- | --- |
| MVP2 全部规划（G1–G6、A01–A11、性能目标） | `docs/NEXT-GUIDE-COMPANION.md` |
| 评审 + 修复记录（含延期项与理由） | `docs/MVP2-READINESS-REVIEW.md` |
| 架构与唯一对话图边界 | `docs/ARCHITECTURE.md` §2 |
| 工程规则 | `AGENTS.md` |
| 实施计划（17.1 为准备批次） | `docs/PLAN.md` |

## 6. G1 之后的顺序（仅供参考，勿提前实施）

G2 采用与管理 → G3 本地优先检索（接 `retrieval.py` 到 `chat/graph.py`，含 FTS5 切换评估——需 MVP2 标注集验证排名）→ G4 对局连续性 → G5 桌宠/语音入口 → G6 评测交付（消费 `logs/metrics.jsonl` 出 p95 报告）。
