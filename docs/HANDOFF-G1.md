# 交接单：下一步工作（G1 攻略入库）

日期：2026-09-27。写给接手的 session：本文件 + `AGENTS.md` + `docs/NEXT-GUIDE-COMPANION.md` 是全部必需上下文；尽量不依赖历史对话记忆。

## 1. 当前状态快照

- 唯一后续开发目录：`/Users/frigidcrow/Dev/ai-neko`，分支 `codex/companion-five-capabilities`。WorkBuddy提交已合入，保留原工作区供回溯，不再从旧副本继续开发。
- 用户已授权MVP1问题收尾及CI/CD，然后进入MVP2准备。最新收尾结论、源码/CI及下载核对见 [MVP1收尾报告](MVP1-CLOSEOUT.md)，不要用历史CI或旧测试数量替代它。
- G1前准备批次已具备；G1–G6功能和验收仍Pending。历史评审见 [MVP2就绪评审](MVP2-READINESS-REVIEW.md)，实际命令和证据见WORKLOG/REVIEW文末。
- 原MVP2桌面WIP已另存于 `artifacts/mvp2-deferred/desktop-guides-wip.patch`，合并前保护stash为 `3e77be31a370800c2759dac10f6ce6b6ddcbbbda`。它是未完成的界面草稿，不能直接套用到收尾后的取消流程。

## 2. 立即事项（开工 G1 之前）

先确认收尾报告中当前提交的CI与Windows下载核对通过，再登记G1任务并实施公开文字攻略的入库纵切。不要再次推送旧的WorkBuddy分支来验证已经合入的准备批次。真实服务和Windows11体验项按报告的实际状态保留，不冒称已验收。

## 3. G1 工作单：攻略入库

依据：`docs/NEXT-GUIDE-COMPANION.md` §4（数据契约）、§8（G1 行）、§9（验收）。完成定义：**完整性、来源、版本、重复入库、重启读取、作用域及限额用例通过；正文在模型上下文裁剪之前入库**（20,000/6,000 字符裁剪前）。

### 3.1 已备好的地基（勿重复建设）

| 地基 | 位置 | 用法 |
| --- | --- | --- |
| 攻略库落位决策 | 独立 `guides.sqlite`，目录 `paths.guides`（数据根 `guides/`，已创建） | 新库从迁移框架起步 |
| 迁移框架 | `src/ai_neko/config/schema.py` | guides新库从baseline 1、建表v2开始；迁移步骤连续，拒绝未来库。conversation已为v3、个人记忆为v2。服务初始化成功后删除固定名自动迁移备份，失败保留；不能制造不可管理的长期正文副本 |
| 埋点 | `src/ai_neko/config/telemetry.py`（`runtime.metrics`） | G1 的入库/检索耗时建议 `record("guide_ingest_ms", ...)` / `guide_retrieve_ms`（命名规则：小写+下划线，≤64 字符） |
| 检索接口 | `src/ai_neko/retrieval.py` | 当前仅骨架；G1需补正文、游戏/版本筛选及字符预算契约，G3才接对话图 |

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

1. 文件与文本检索优先用 `rg` / `rg --files`；不要因一次检索为空就断言代码不存在。
2. 旧WorkBuddy沙箱曾拦截子进程和二次mkdir；当前本机这些用例可以运行。`sandbox_compatible`只跳过已知EEXIST故障，其他错误会失败；CI拒绝非预期skip。
3. **pytest 长时间运行要用独立 basetemp**：`--basetemp=/tmp/<唯一名>`，否则与残留会话冲突出现批量 EEXIST 假失败。
4. **CI 有 format 门禁**：`ruff format --check src tests scripts packaging`。提交前必须本地跑 `uv run ruff format src tests scripts packaging`；ruff 新版会顺带格式化 markdown 内嵌 Python 代码块，属预期。
5. **大文件读取会被截断持久化且无法回读**：读 `runtime/service.py`（约 1200 行）这类文件必须分段 Read；**绝不能凭部分内容写 file:line 引用**（历史教训见 `docs/MVP2-READINESS-REVIEW.md` 附录一）。
6. Runtime的取消/关闭/记忆变更必须结算真正的线程操作，不能把取消await当作SQLite线程已经停止。遗忘结合本轮事实与实际传入的历史依赖，内容证据不持久化；checkpoint按 `internal_id:turn_id` 清理。后续回合即使换话题也可能携旧历史，需要保守清理，不能承诺同会话后续总会保留。
7. 推送和发布遵循当前用户授权；本次收尾已授权CI/CD。tag会触发发布路径，不能把未授权的新版本发布视为普通本地操作。

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
