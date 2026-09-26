# ai-neko 验收记录

2026-09-26 当前状态：M0/M1/M2/M4/M5 Partial，M3/M6 Pending。五项能力的本地实现与合成闭环已推进，详见文末及[实施及验收](docs/COMPANION-IMPLEMENTATION.md)；真实服务与Windows11真机尚待。v0.4.0-alpha.1已发布并核对下载；当前新增攻略连续陪玩计划，尚未实现。历史记录保留当时实际状态。

## P0 — 独立工程与可实施计划

| 验收项 | 状态 | 证据 |
| --- | --- | --- |
| 独立目录与 Git | PASS | `git rev-parse --show-toplevel --git-dir` 返回本工程根和独立 `.git`；分支 `codex/initial-plan`，无 remote、提交或推送；原工程 Git 状态与 tracked diff 指纹未变 |
| 用户选定的 LangGraph、Windows、本地记忆方向一致 | PASS（规划） | 独立评审确认：单一图编排、本地记忆权威、选择性复用已有组件、Windows 11 x64 实测边界一致 |
| 阶段、模块边界与验收可执行 | PASS（规划） | M0–M6 依赖、实现范围和验收齐全；内部线程身份映射、Runtime 取消结算两项评审发现已修复并复核 |
| 教程/源码映射及来源快照 | PASS（引用） | 10 个参考组、24 课、206 文件；Root 重算全部 SHA256，课程/组/引用完整，源码 commit 与未提交教程分开记录 |
| 文档链接与变更范围 | PASS（文档） | 本地链接、表格、围栏、空白与新文件 diff 检查通过；新工程仅有文档、JSON 清单和 gitignore；完整结果见 [初始化验证记录](docs/validation-initial.json) |

独立评审由 `/root/review_l15_final` 完成，未参与正文编写。两项发现与闭环：

1. checkpoint 的内部线程 ID 必须绑定用户/角色。ARCHITECTURE 明确后端映射与所有 get/history/run/resume/delete 入口校验，PLAN M0 增加相同外部 ID 不同角色的隔离用例。
2. 图取消后不能指望末节点保存部分对话。ARCHITECTURE 明确 Runtime 独立持久收尾、正常/取消共用幂等键、崩溃恢复和实际投递证据，PLAN M1/M4 加入提交前/中/后取消验收。

复核者确认上述两项闭环并返回 PASS，另检查参考组/课程/文件路径与状态。206 文件哈希由作者和 Root 检查，独立评审不冒称重算哈希或重审全部来源。P0 通过不改变下表任何产品状态。

## 产品阶段

| 阶段 | 状态 | 当前缺少的证据 |
| --- | --- | --- |
| M0 基础与复用验证 | Partial | 基础源码/CI/冻结包已验证；Windows 11 真机/ACL/junction/原版共存仍待；桌宠工具链在 M1 补验 |
| M1 / MVP1 猫娘桌宠闭环 | Partial | YUI 猫娘、宿主、伴随交互与打包已实现；真实服务、Windows 11 真机及完整 V01–V09 仍待验收 |
| M2 本地长期记忆 | Partial | 本地落盘/新会话/纠正/遗忘/进程恢复合成通过；模型正确使用及Windows重启待验 |
| M3 角色表现扩展 | Pending | 新动作/多角色/扩展渲染与隔离；基础桌宠已前移 M1 |
| M4 语音与打断 | Partial | 合成录音/ASR/TTS/WebAudio停播和迟到片段验证；真实语音、Windows设备、20次延迟待验 |
| M5 视觉/主动/查询联动 | Partial | 当轮选源/图像/关闭门控合成通过；真实游戏理解及主动模式待验 |
| M6 分发与长期运行 | Pending | 完整 Windows 成品、干净机、升级恢复、7 天观察；M0 诊断包不替代这些验收 |

## 已核实的外部边界

2026-09-25 执行 `gh repo view Project-N-E-K-O/N.E.K.O.-PC --json nameWithOwner,isPrivate,defaultBranchRef,licenseInfo,url`，退出 1，返回 `Could not resolve to a Repository`。只证明当前账号本次未解析到仓库；访问、许可、接口与桌面壳版本仍待核实。计划包含最小宿主条件方案，其他模块可继续评估。

N.E.K.O 根目录 LICENSE 为 Apache-2.0，并存在 NOTICE 和其他素材/库声明；尚未复制运行代码或素材。该根许可证不能替代逐模块来源核对。

Codex 项目列表本次尚无本目录；工具中未发现登记本地目录的入口。磁盘工程创建与侧栏登记分别记录，不声称界面已登记。

## P1 — ai-neko 改名、技能调研与图册

2026-09-25 完成文档交付。以下结果均为工程身份、图与文档的验证；不改变 M0–M6 的 Pending 状态。

| 验收项 | 状态 | 直接证据 |
| --- | --- | --- |
| 目录与项目身份更名 | PASS | 新根 `/Users/frigidcrow/Dev/ai-neko` 保留独立 `.git`，旧目录不存在；当前名称、拟定数据根、`AI_NEKO_DATA_DIR`、`src/ai_neko/` 已统一 |
| 历史与原工程隔离 | PASS | 旧名称仅留历史工作记录、初始化验证及检查器所需匹配规则；206 来源 SHA256 一致；原 repo 状态与 tracked diff 指纹未变；无自动资料迁移 |
| 技能调查 | PASS（调研） | [DIAGRAM-SKILLS](docs/DIAGRAM-SKILLS.md) 比较 6 个已读技能并标注阅读/安装范围；外部技能未安装、未执行脚本、未上传 |
| 计划与模块覆盖 | PASS（设计） | [图册及覆盖表](docs/DIAGRAMS.md) 提供 20 图、17 行模块映射；输入、处理、输出、失败边界与课程均具备，涵盖 ARCHITECTURE 的全部规划职责 |
| 源文件可编辑且实际渲染 | PASS | 20 份 `.mmd` 对应 20 份 SVG；[渲染记录](docs/diagrams/render-validation.json) 记录 CLI 版本、环境和图源/产物 SHA256；[维护方法](docs/diagrams/tools/README.md) 给出重建命令 |
| 离线浏览与链接 | PASS（Mac 浏览器） | [浏览器记录](docs/diagrams/browser-validation.json)：禁网、1440/390px、20 图加载、锚点与本地链接有效、页面无横向溢出、20 SVG 无画布外文字；[桌面截图](docs/diagrams/evidence/atlas-1440.png) / [手机截图](docs/diagrams/evidence/atlas-390.png) |
| 图与架构一致 | PASS（独立审查） | `/root/diagram_skills_research` 未参与图源编写，逐图比对 PLAN/ARCH；确认记忆权威、线程隔离、取消独立结算、生成/播放事件和条件复用；反馈已闭环 |
| 当前文档与引用复核 | PASS | [本轮验证](docs/validation.json) 重新检查名称、独立 Git 根、Markdown 链接/锚点、diff 空白、来源哈希、渲染指纹与产品阶段状态 |

人工可读性复核是截图抽查，自动渲染与画布边界检查覆盖全部 20 图；不宣称已人工逐像素检查所有连线。ER 是概念模型，数据布局在 M2 落地；路线图不包含虚构工期。图册工具已在 Mac 运行，Windows 产品与图册重建命令尚未实机验证。

## M0 — 独立运行基础与复用接口核查

日期：2026-09-25。**阶段总状态 Partial；Mac 源码/合成进程验收 PASS。110 passed、0 failed、0 errors、0 skipped，真实模型测试 0。** 本轮只运行临时合成资料，没有在 Windows 启动。主要证据为 [macos-smoke.json](docs/evidence/m0/macos-smoke.json)，包括各测试名、结果、环境、源码清单摘要和实际依赖版本。运行方式见 [M0 开发说明](docs/M0-QUICKSTART.md)。

| 验收项 | 状态 | 直接证据与边界 |
| --- | --- | --- |
| 独立 Python 环境和依赖锁 | PASS（Mac） | Python 3.11.15；LangGraph 1.2.12、SQLite checkpointer 3.1.1；本项目 `.venv` / `uv.lock`，`uv lock --check`、`uv pip check` 通过 |
| 数据根、身份、配置隔离 | PASS（Mac + 合成平台逻辑） | `test_config.py` 55 项：专属环境覆盖、无原版回退、所有权、非法根、符号/硬链接拒绝、配置类型/凭据命名空间、Key 不落盘与 import 无运行副作用；Windows junction/ACL 尚未真机验证 |
| 本机服务和退出清理 | PASS（Mac 真实子进程） | `test_server_process.py` 18 项：中文空格路径、OS 分配端口、占用端口、同根第二实例、两根共存和独立退出、强制结束后新 token；不按旧 PID 杀进程 |
| 连接边界 | PASS（Mac） | HTTP token、Host，WS Origin、首帧 token、5 秒超时和 query token 拒绝；正常/活跃 WS 退出；令牌未进入输出与日志；stop 拒绝重定向转发 token |
| 两节点图、条件边与真实流事件 | PASS（确定性） | `test_graph.py` 37 项：真实 LangGraph + SqliteSaver，空输入分支/流式事件/暂停恢复；没有模型、工具或媒体 |
| 跨解释器 checkpoint 与线程归属 | PASS（Mac） | 四个独立进程暂停→读取→恢复→读取；15 个 scope×操作组合验证 get/history/run/resume/delete 全入口；外部 handle/checkpoint、旧版本写入、撤销句柄拒绝 |
| 四类组件来源审计 | PASS（静态核查） | [审计](docs/M0-REUSE-AUDIT.md) 与 [manifest](docs/m0-reuse-manifest.json)：33 份选定源码/许可文件与来源 HEAD 一致，第三方迁入记录 `imports=[]`；没有导入原模块运行 |
| 桌面与凭据方向 | PASS（决策） | 独立桌面壳仍不可解析，M3 采用本项目最小 Electron 宿主；M1 采用专属命名空间的 Windows Credential Manager；尚未实现窗口或系统凭据存取 |
| Node/前端/渲染兼容 | Partial | 候选 Node 24.21.0 / Electron 44.4.5 等的官方元数据、engines/peer 已核对；产品前端 lock、安装/构建与渲染 SDK/素材许可待后续实现验证 |
| Windows 11 x64 最小启动 | Pending | [Windows 执行入口](docs/WINDOWS-M0.md)、PowerShell 脚本与取证生成器已写；尚无 Windows 执行证据 |
| 真实模型 / 长期记忆 / 桌面 / 语音 | Pending（后续阶段） | 真实模型测试 0；checkpoint 不是长期事实记忆；建好 memory/backups 目录不等于相应功能已实现 |

独立审查由未编写生产代码的 `/root/m0_reuse` 执行。发现并复现两个 P2：硬链接可使 FileLock 截断外部文件；配置接受布尔版本、非字符串模型字段和外部凭据 namespace。已修为写入前拒绝硬链接/重定向与严格配置校验，并新增回归用例；独立重新执行原始复现后确认外部文件不变、非法配置拒绝、正常配置接受。未发现剩余 M0 源码阻断，此结论不代替 Windows 验证。

Root 另补充 stop 的重定向测试：本机端口被其他服务接替时，shutdown 不跟随其 302 重定向，避免把 token 转发到新目标。源文件摘要在最终 smoke 前后保持一致；当时无首个 Git commit，因此报告 `git_commit: null` 并提供文件清单摘要，没有虚构发布来源 commit。该次验收未构建或发布 Windows 产物，后续 CI/版本交付见本文件末节。

不在本轮结论中：原版 N.E.K.O 与 ai-neko 真正同时运行、真实 Windows junction/ACL、原媒体模块提取后 import 探针、前端组件集成。取消与崩溃中回合结算留 M1，完整事实删除留 M2；显式图暂停恢复不能证明这些行为。后端 Scope 需要可信调用方构造，目前只有本机合成 CLI，没有接受任意 user ID 的网络图接口。

## GitHub 首次上传 — PASS

2026-09-25，按用户明确授权上传至 [FrigidCrow/ai-neko](https://github.com/FrigidCrow/ai-neko)，保留 `codex/initial-plan` 分支。源码基线提交 `7d77223f7375445988c0767bf6eb5273d7336066` 已推送，GitHub 分支 SHA 与本地核对一致。103 文件经过独立纳入清单检查；无真实凭据、运行数据或虚拟环境。源码与 110 passed 的合成测试清单摘要一致，上传只调整 Git 交付相关文档与验证器。M0 仍 Partial，Windows 项仍 Pending；这不是产品发布。

## 查攻略优先与范围调整 — PASS（规划）

2026-09-25，按用户需求将真实搜索/公开网页正文读取/带来源攻略提前至 M1；当前不做键鼠自动操作、控制游戏/其他软件或浏览器账号操作。正常桌宠拖拽、语音、主动提供图片仍保留；M5 复用已有查询能力处理图片问题。PLAN、ARCHITECTURE、README、AGENTS、参考组与图册已一致更新。

独立复核 `/root/search_scope_plan_review` 指出的两项已修复：首个文字体验从 M1 引出，M2 单列记忆；R01 的外部副作用要求明确留作未来参考。工具图无结果/不可读路径输出缺口说明，不强行给出攻略。reference-manifest 的 R07/R08 仅更新规划边界及阶段，206 个原始来源文件指纹未更改。

20 图重新渲染，1440/390px 离线浏览全部加载、无坏锚点/页面溢出，见 [图册检查](docs/diagrams/browser-validation.json)；Root 查看 [查询流程截图](docs/diagrams/evidence/08-tools.png)。独立复核最终 PASS。产品源码、测试、运行依赖没有改动，本轮不重复执行产品测试；AGENTS 等文档已更新，旧 smoke 保持其运行时的源码/文档摘要，不伪改成新一轮结果。联网查询仍未实现，真实搜索与模型验收均属于 M1。

## GitHub CI/CD 与 Windows 基础验证包 — PASS（本节交付）

源码基线 `c745193e4a24f44489d99564aac0e08b4a3fd0dc` 的[普通构建 36123110092](https://github.com/FrigidCrow/ai-neko/actions/runs/36123110092)已成功：Windows Server 2022 x64 与 Linux 分别通过 162 项合成测试，0 失败/错误/跳过；解压后的冻结 exe 通过 13 项真实进程检查。构建来源工作区干净，模型测试 0。

包检查实际覆盖中文/空格路径、独立合成 profile、HTTP/WS 鉴权、端口占用、同根第二实例、优雅退出、跨进程图恢复和用户/角色隔离。子进程只运行包内 exe，PATH 清至系统目录；未借用开发 Python 或源码。此结论是 CI Windows Server；Windows 11 x64 真机、无开发工具机器、ACL/junction/原版共存仍须单独取证。没有真实聊天、攻略、长期记忆、桌宠、托盘或语音。

已增加独立诊断入口、来源/许可证/摘要清单；许可文本按实际 wheel/CPython 发行校验，不把构建测试工具当运行依赖打包。M0 仍 Partial，M1–M6 仍 Pending；M1 的下载包/本地文字页面/攻略功能是下一阶段目标。具体使用见 [下载与 CI 说明](docs/CI-RELEASES.md)。版本 tag 发布结果在下方单列。

预发布 [v0.1.0-alpha.1](https://github.com/FrigidCrow/ai-neko/releases/tag/v0.1.0-alpha.1) 已由[tag workflow 36123430471](https://github.com/FrigidCrow/ai-neko/actions/runs/36123430471)自动发布；该次 Windows/Linux 各 162 项源码测试与 13 项冻结包检查再次通过。6 个发布资产已实际下载，逐个核对 GitHub digest；ZIP/清单/构建信息和测试记录中的 exe SHA256 一致。ZIP 为 21,419,567 字节，SHA256 `d3aad33aa6e692eda305cb3eac552a7c7f24c889704f8eb60c248722d9cf351d`。

直接证据：[下载核对报告](docs/evidence/m0/release-v0.1.0-alpha.1-verification.json)、[构建来源](docs/evidence/m0/release-v0.1.0-alpha.1-build.json)、[包检查](docs/evidence/m0/release-v0.1.0-alpha.1-package.json)。源码测试原始 JSON 同时作为 Release 资产保留。Mac 上只读取下载产物、核对摘要和 PE AMD64 格式，没有执行 Windows exe；Windows 执行结论来自上述远端 runner。当前可双击基础自检，无需开发工具；聊天/查询仍属于 M1，不能把基础包称为完整伴侣。


## M0 收尾与 M1 文字/攻略实现（2026-09-25）

用户已授权本轮 M0 收尾、M1 和 CI/CD。M0 可自动化工程项继续通过，Windows 11 真机仍 Pending；M1 源码已实现，发布与最终分项证据在本节续记。M2–M6 未启动。

| 项目 | 当前证据与边界 |
| --- | --- |
| 模型/图/持久会话/API | OpenAI 兼容 SSE、唯一 LangGraph、3 轮/9 次只读工具上限、流式文字、查询来源、输入/发送/确认/幂等结算、取消与真进程崩溃恢复；38 项图/runtime 确定性测试通过 |
| 网络/凭据 | DNS 固定 IP/TLS SNI、公开地址与重定向校验、无浏览器 Cookie/环境代理、限制大小/时间；Windows Credential Manager 单独实测，Mac/Linux 使用进程内凭据 |
| Mac 源码预检 | [全套报告](docs/evidence/m1/macos-final-smoke.json)：286 passed、0 failed/error、1 Windows-only skipped，源码运行期间未变，CI gate PASS；报告总状态保留 PARTIAL，未执行的 Windows vault 不计通过 |
| 真实公开正文 | [一次 HTTPS 正文读取](docs/evidence/m1/public-page-read.json)：Python 官方页实际读取成功；没有真实搜索或模型调用，不是三次端到端攻略验收 |
| 真实模型与攻略质量 | [明确缺凭据](docs/evidence/m1/live-provider-acceptance.json)：专属模型/搜索 Key 未提供，外部调用 0。10 轮真实对话及 3 次真实搜索→正文→答案逐项人工核对 Pending |
| Windows 11 | 用户真机、中文账户/ACL/junction、原版真实共存与实际交互 Pending；不能以 GitHub Windows Server 替代 |

独立审查由 Provider 与 Runtime 工作者互查及 Root 集成复核完成；已闭环未发送草稿回放、迟到 checkpoint、旧来源编号误用、明确错误提示、配置凭据失败回滚。打包审查核对新增模块/静态资源路径/许可证闭包及 0.2.0 版本一致；私网冻结探针严格断言 blocked_address，源码子进程同时清除模型和搜索 Key。所有真实服务质量与 Windows 11 缺口保持明确。


网页最终验证：[14 项浏览器检查](docs/evidence/m1/ui-browser-check.json) PASS，使用 Mac Chrome 与真实本地服务/合成模型；实际检查一次性 bootstrap、流式/ACK/取消/恢复、未领取完成回合、响应丢失后的幂等重试、鉴权错误在 done/reload 后保留、退出、移动端和不外传 Key。来源卡片/XSS 为明确 renderer fixture，不冒充真实检索结果。[桌面](docs/evidence/m1/ui-desktop-1440.png)、[窄屏](docs/evidence/m1/ui-mobile-390.png)、[设置](docs/evidence/m1/ui-settings-1440.png)、[聊天](docs/evidence/m1/ui-chat-1440.png)截图已查看。所有临时服务已退出；未做 Windows 浏览器验收。


最终 [tag workflow 36127322450](https://github.com/FrigidCrow/ai-neko/actions/runs/36127322450) 全部必要 jobs SUCCESS，发布 [v0.2.0-alpha.1](https://github.com/FrigidCrow/ai-neko/releases/tag/v0.2.0-alpha.1)。源 commit `987d8b0a99d29332bd1972147803dd8af9057e96`，Windows 287 passed/0 skipped，Linux 286 passed/1 专属 skip；Windows Credential Manager 实际读写删除通过。解压后的 exe 16/16 检查 PASS，新增网页/API、流式 ACK/取消、完整回复、会话正常重开和两项攻略工具失败路径；包测试仍不声称真实搜索成功、真实云模型质量或包内崩溃恢复已验收。

六个发布资产已实际下载、逐项验证 GitHub digest/size、SHA256SUMS、源 commit/clean 状态、ZIP 内外 build-info、exe SHA 与 PE AMD64、M1 启动器和网页文件字节对应。ZIP 为 21,541,629 字节，SHA256 `549aec1047932520d5f68f728aea81c2fb7a05750d852f4abd980acdca14d0d5`。发布于 2026-09-25T11:07:04Z。

直接证据：[发布核对](docs/evidence/m1/release-v0.2.0-alpha.1-verification.json)、[构建来源](docs/evidence/m1/release-v0.2.0-alpha.1-build.json)、[冻结包检查](docs/evidence/m1/release-v0.2.0-alpha.1-package.json)。Windows/Linux 原始完整源码报告作为 Release 资产保留；入库的 Windows JSON 仅将 CRLF 正规化为 LF，下载原始文件的 hash 保留在核对报告。Mac 未执行 Windows exe。

结论：本轮代码、UI、CI/CD 与 M1 可下载预览交付通过；M0/M1 总阶段保留 **Partial**，缺口是 Windows 11 真机，以及用户配置实际模型/搜索服务后的 10 轮/3 次逐项验收。没有 Key 时不能代做真实服务验收；M2–M6 不在本轮实现范围。


## MVP1 以可见猫娘桌宠为主体（2026-09-25，规划调整）

用户要求先规划以桌面可见猫娘为准的 MVP1。已新增 [MVP1 规格](docs/MVP1-DESKTOP-PET.md)，同步 PLAN/README/ARCHITECTURE 与历史试用说明边界；图册对应路线同步。M1 必须包含基础桌宠、伴随输入/真实流式回复、停止与本地会话、查攻略来源、基本托盘和桌宠 Windows 下载包。M2 记忆不阻塞桌宠，M3 改为角色表现扩展。

当前素材、桌面宿主和渲染未实现；默认 Live2D 仅为建议，具体猫娘/SDK/Core 分发条件尚未确认。已有 v0.2.0-alpha.1 保留网页工程预览定位，不覆盖其 tag 或二进制。本轮无产品代码、依赖或构建流水线修改，无真实模型/搜索调用、无新的 Windows 执行或发布。

独立只读评审 `/root/mvp1_pet_plan_review` 核对现有后端能力与缺口，明确普通聊天窗口不能替代猫娘桌宠、角色在聊天中可见、素材许可不能套用仓库根许可。相应要求纳入规格；文档与图册验证结果另记下方。M0/M1 总状态保持 Partial，M2–M6 Pending。

本轮规划校验 PASS：20 图重新渲染，离线 1440/390 两视口无坏锚点、外部网络请求和横向溢出，SVG 无画布外文字；`validate-docs.py` 校验 206 参考文件、306 本地链接、阶段状态及渲染指纹通过，参考工程状态/差异未变。仅表示文档与图解一致，不增加任何桌宠运行通过记录。

最终独立复核发现 ARCHITECTURE 查询接口仍标待实现、M0 审计旧 M3 安排未注明替代；已修正当前实现描述并在历史审计入口补新阶段映射。Root 查看路线图截图，桌宠 MVP1 前移及历史预览边界可读。

## 2026-09-26 — MVP1 猫娘桌宠实现

已接入用户确认的白裙 YUI Lolita 猫娘、Electron 44.4.5 宿主与伴随聊天/历史/设置；默认入口为桌面猫娘。模型与查询沿用唯一 LangGraph 决策中心。主进程保有后端 token，renderer sandbox 与 contextIsolation，有限 IPC；专属数据根、单实例和 stdin EOF 后端生命周期均落地。

资源来自只读参考 commit `90ccf79c95e80f899b9bf3395fa8cd9a9bfe29be`；61 个 YUI 文件及渲染/许可文件合计 79 项逐文件校验。YUI 许可依据、推断边界和 SDK 独立条款见 [资源说明](docs/MVP1-ASSETS.md)；用户首次接受随包条款后才加载 Core。参考仓库 206 文件、tracked diff/status 未变。早期 Mao 渲染探针外观不符合猫娘目标，已移除且不进入发布。

| 验证层 | 实际结果与边界 |
| --- | --- |
| Mac Python 源码 | 291 passed / 1 Windows vault skipped；Ruff 格式与静态检查通过；真实模型/搜索调用 0 |
| Electron 主进程 | 最终15项 node:test 通过；打包相关 Python 44 项通过；79 个资源字节/大小摘要通过 |
| 实际 Electron 窗口 | [本地报告](docs/evidence/mvp1/macos-desktop.json) 9/9 PASS：许可后实渲染与透明像素、有限权限、配置时角色可见、递增回复/停止、普通回复与缺搜索 Key 提示、折叠保留角色/偏好、第二实例、退出重开历史、实际杀宿主后后端退出且不自动重放 |
| Windows 原生包 | [36174872767](https://github.com/FrigidCrow/ai-neko/actions/runs/36174872767) Windows 302/0skip、Linux 301/1skip，15宿主、16后端包检查与9实际桌宠检查通过；[下载开发包核对](docs/evidence/mvp1/dev-023bee3-verification.json) PASS，正式版本发布结果在下文续记 |
| 完整 MVP1 用户验收 | Pending：Windows 11 无开发工具真机、多 DPI/多屏/透明点击与托盘、真实模型 10 轮和攻略 3 次、20 片段显示延迟 p95、10 次取消边界与旧版数据升级。M0/M1 总体仍 Partial；不据 Mac 或 Server CI 写完整 MVP1 通过 |

修复的实际问题包括渲染 bundle 选择、角色可见区域适配、配置按钮早于后端 ready 时状态恢复，以及重启加载历史时错误接受新输入。最后一项现在以完整会话恢复为输入启用条件，E2E 同样等待可见 UI 就绪。宿主异常退出验证真正杀死本次创建的 GUI 进程，确认属于它的后端退出和历史中断不重放，未结束其他应用。

Windows 原生实测还发现并修复两项 Mac 未暴露的问题：独立 profile 必须在 Electron ready 前设置；默认 libuv job 会在宿主强杀时跳过后端收尾。现在同步初始化独立路径，Windows 后端以 detached 创建但保留管道和引用，并持有本次宿主 HANDLE 监听结束；不轮询/终止任意 PID，也不依赖原工程。前几轮失败如实保留在 WORKLOG，最终包按原严格条件 9/9 通过，没有删除强退检查或将残留描述当成通过。

### 桌宠预览发布与最终下载核对

[v0.3.0-alpha.1](https://github.com/FrigidCrow/ai-neko/releases/tag/v0.3.0-alpha.1) 已于 `2026-09-25T18:51:55Z` 发布；[tag workflow 36175618386](https://github.com/FrigidCrow/ai-neko/actions/runs/36175618386) 测试、原生 Windows 构建、冻结后端16项、实际 Electron 桌宠9项与发布全部 SUCCESS。源 commit `023bee38f29d385bdfc690e2b5c4ea00d4ee5ecb`，Windows 302 passed/0 skipped，Linux 301 passed/1明确平台skip，15项宿主测试通过。

7项 Release 资产实际下载并核对 GitHub digest/size、SHA256SUMS、tag、同一 clean 源码、包内外 build-info、GUI/后端 PE AMD64 与报告身份、96个桌面源码文件、79项导入资源和61文件 YUI引用闭包、57项后端依赖通知及无用户运行资料。5张 Windows 截图的摘要全部匹配，Root 目视确认正确猫娘和重开历史。ZIP 为191,723,563字节，SHA256 `adadafa71d67f705b2fdf16131889974f17e77f98dc770374931957ef83d26b1`。Mac 只核对下载产物，Windows 程序执行证据来自原生 Windows Server 2022 CI。

直接证据：[发布核对](docs/evidence/mvp1/release-v0.3.0-alpha.1-verification.json)、[构建来源](docs/evidence/mvp1/release-v0.3.0-alpha.1-build.json)、[冻结后端](docs/evidence/mvp1/release-v0.3.0-alpha.1-package.json)、[实际桌宠报告](docs/evidence/mvp1/windows/desktop-smoke.json)、[猫娘与恢复聊天截图](docs/evidence/mvp1/windows/desktop-restored.png)。Windows/Linux完整源码报告保留为Release资产；入库JSON只正规化行尾，原始下载摘要在核对报告中。

桌面预览与 CI/CD 交付 PASS。真实模型/搜索调用0；Windows 11 真机与上表完整 MVP1 用户验收仍 Pending，M0/M1 保持 Partial。下载后无需 Python/Node，首次接受随包运行条款后出现猫娘；云聊天需配置本项目模型，攻略需另配置 Tavily Key。

## 2026-09-26 — 下一交付的视觉语音陪玩方案

用户提出人格、桌面视觉、语音输入/输出，并明确“玩《王者万象棋》时语音问下一步，由猫娘根据当前画面语音建议”的使用场景。已写 [实施方案](docs/NEXT-GAME-COMPANION.md) 并同步PLAN/ARCHITECTURE：首个增量包含角色档案、当轮截图、ASR→同一LangGraph→按句TTS、真实播放取消与游戏质量验收；不等待完整长期记忆、多角色或主动搭话。用户模型服务及游戏画面来源仍待确定。

本轮为规划与只读源码核对。参考人格预设/注入、选源/图片输入、麦克风采集、SentenceBuffer与播放清队列；未导入或运行原版，不读取其配置/数据/凭据。人格建议与按键说话是待落实设计，未冒称用户已选择音色或交互形式。M0/M1 Partial、M2–M6 Pending，新增视觉/语音真实调用及Windows运行均为0；文档通过不计为功能通过。

用户随后明确网络查询拟用DeepSeek。已更新方案6.1及PLAN：官方deepseek-flash有视觉能力；原生搜索文档针对Anthropic/Claude Code路径，Responses明确忽略web_search；ASR/TTS公开入口本轮未确认。独立只读核对本工程当前仍只有普通Chat Completions、Tavily函数工具和文字输入，没有供应商原生来源/思考上下文适配。因此“供应商支持”不写成“本工程已支持”。DeepSeek真实请求0，渠道/具体模型与语音服务待定，未改产品代码或发布。

用户进一步要求通用联网搜索，已修正前一方案的原生搜索优先方向：现有LangGraph共享工具与Tavily后端已实现模型/搜索配置分离；下一交付沿用此边界，搜索服务接入一次、跨兼容模型复用。原生搜索只是可选后端，模型协议兼容与搜索适配分别验收。不宣称所有模型已可用，未替换搜索服务或新增真实API验证。本次仅澄清规划。

## 2026-09-26 — 五项能力本地实现

按持续目标实施人格、桌面视觉、ASR/TTS、公共查询与长期记忆，保留YUI桌宠入口。用户再次强调参照N.E.K.O后，实际提取了分词、繁简转换、BM25纯函数，来源和许可见[复用记录](docs/MEMORY-REUSE.md)。人格、选源与语音生命周期参考原版机制接到本工程，未读取其凭据/运行数据或启动原版。

Python全套457 passed/1 Windows凭据存储专属skip；最后增加每轮人格/记忆版本与图像元数据后，Runtime/协议/综合/边界62项再次通过。宿主28项、[实际Electron新增闭环](docs/evidence/companion/macos-companion.json)9项和[原桌宠基线](docs/evidence/companion/macos-baseline.json)9项通过。[实际截图](docs/evidence/companion/macos-companion.png)仍为用户确认的白裙猫娘。全程合成资料、无用户画面/麦克风/真实云调用。

独立审查实际发现并修复：长消息被召回长度挡住、并发close重复关库、并发遗忘互斥、派生待提取原文遗留、Memory提交后日志清理前失败的恢复、吞取消图节点晚到入队、慢音频上传在取消/关闭后创建新供应商任务。相应回归已覆盖，不把“测试未报错”当作所有错误路径证明。

新增实际音频回执独立于显示ACK；生成done不表示听完，隐藏聊天播放不虚增显示ACK。语音停止只有单次本机观测，20次p95、真实声音/游戏模型质量、Windows新包运行和Windows11真机仍Pending。完整目标仍进行中。CI已增加Windows包的新闭环门禁及提取组件许可打包，尚未以未执行的Windows步骤记PASS。


首轮新功能Windows CI [36224678803](https://github.com/FrigidCrow/ai-neko/actions/runs/36224678803)失败，Linux通过。已定位并修正新增许可的行尾转换、截图测试对Windows活跃文件锁的读取假设，并取消记忆数据库链接测试的无条件平台跳过；最终效果等待Windows重跑。默认语音按需联网及旧历史/重试不覆盖当前模式的缺口已纳入修复，真实搜索/音频服务调用仍为0。失败报告无原始traceback，不据本地复现实验断言Windows失败的唯一原因。

修复后Mac源码全套467 passed/1专属skip；桌宠新增闭环13/13、旧基线9/9、宿主28/28。默认按需查询、仅聊天模式、真实工具错误提示与历史重试行为已合成验证。Windows结果以下一轮CI为准。


第二轮[36225439869](https://github.com/FrigidCrow/ai-neko/actions/runs/36225439869)证实Windows源码468项全部通过、无跳过；Linux467项/1平台skip。首轮失败修正已在Windows实际通过。ZIP构建成功，但冻结后端聊天检查8/16失败，未进入桌面/新功能包测试；当前仍不能写Windows包验收通过。

冻结后端旧探针失败已由真实源码服务准确复现，修正后同探针通过。现在要求4次模型请求与最终两工具失败完整传递；新增31项定向回归通过，等待第三轮Windows冻结包验收。


### Windows开发包验收通过

最终源码`7bd14343178a5066182ac75007cf9137fafe6074`的[CI 36225891452](https://github.com/FrigidCrow/ai-neko/actions/runs/36225891452)通过。Windows469/0skip、Linux468/1平台skip；[冻结后端](docs/evidence/companion/windows/package-smoke.json)16/16、[实际桌宠](docs/evidence/companion/windows/desktop-smoke.json)9/9、[新增陪伴闭环](docs/evidence/companion/windows/companion-smoke.json)13/13。Root实际查看[Windows截图](docs/evidence/companion/windows/companion-smoke.png)。使用解压包中的Electron/后端，不依赖用户Python/Node；视觉和语音来自合成窗口/fake麦克风/合成服务，未读取用户桌面或麦克风。单次停播70ms，不是p95。

已上传[Windows开发构建](https://github.com/FrigidCrow/ai-neko/actions/runs/36225891452/artifacts/10901235961)，未创建新tag或Release，旧版保持不变。真实云模型/搜索/ASR/TTS调用0，Windows11真机、真实游戏决策质量及性能仍Pending；完整目标继续进行中。

最终开发包已实际下载，191,838,005字节，SHA256 `2f995145581ec22a59f1ba7de01a38832169214dea67c41c2c9871f9d33dd4e9`。核对SHA256SUMS、两平台同一clean源码、包内外build-info、GUI/后端AMD64 PE与执行报告摘要、记忆组件通知、桌面新模块及截图摘要通过；Mac未运行Windows exe。下载核对见 [Windows下载核对](docs/evidence/companion/windows/download-verification.json)。


## 2026-09-26 — 记忆快照与语音上下文收尾

桌宠现有记忆快照管理入口及确认恢复/删除；恢复保留纠正与遗忘。已听句子按原文范围登记，隐藏面板时能续接已听完前缀，仍不增加文字显示ACK。相同事实ID异常内容快照拒绝恢复，不能把旧对话中的事实引用改指其他内容。

独立审查复现并修正两项恢复缺陷：仅保存来源ID会漏清手动事实曾被召回的旧回答；恢复清理失败时事件接口仍可读旧内容。现在移除事实ID同事务持久化，失败期间会话/事件/列表/取消/ACK均拒绝返回旧内容，重启完成清理。另修复显示ACK停在URL或引用内部时，未读出的后缀误入已听历史。

Mac全套544 passed / 1 Windows凭据专属skip；快照API25项、已听上下文15项、记忆底层61项定向通过。宿主31/31、实际Electron基线9/9、新增闭环16/16，证据见[Mac报告](docs/evidence/companion/macos-companion.json)及[快照界面](docs/evidence/companion/macos-snapshots.png)。报告真实记录未提交工作区及旧HEAD，不把它当新不可变源码。Windows包尚待本轮CI；真实模型/搜索/语音及用户采集均0，Windows11游戏场景仍Pending。


本轮首个Windows CI [36227635761](https://github.com/FrigidCrow/ai-neko/actions/runs/36227635761)未通过：源码ba77e55，Linux544/1平台skip；Windows540 passed、5 failed、0skip，打包未启动。下载源码证据后确认五项失败集中于test_memory_backups的旧格式、损坏快照和非法数据快照删除；报告未包含原始异常堆栈。代码检查发现SQLite测试上下文只提交、未关闭连接，现改为显式closing，在删除/恢复/VACUUM前释放测试连接；迁移/清理测试同步释放连接，新进程探针加30秒上限。产品代码不变，需以Windows复跑确认修正，不仅凭Mac通过推定。


第二轮[36227919089](https://github.com/FrigidCrow/ai-neko/actions/runs/36227919089)Windows544通过、1失败；上轮5项快照删除用例均通过。唯一失败为旧播放回执测试的列表排序断言。Root将测试时间戳固定相等、片段ID固定为逆序，在本机准确复现旧断言返回started/completed顺序；按稳定segment_id逐一检查完成/开始以及重启后完成/中断状态后通过，未改生产排序或添加sleep。同步更正包内说明为拖动角色、开启观察后核对预览。Windows最终效果仍以下轮CI为准。


### 快照与已听上下文 Windows 交付通过

最终产品源码 `5871f2d94e2f176a863262ae12c86edf2e05066c` 的 [CI 36228220001](https://github.com/FrigidCrow/ai-neko/actions/runs/36228220001) 必要 jobs 全部 SUCCESS，非 tag 发布正常跳过。Windows 545 passed/0 skip、Linux 544 passed/1 平台 skip；宿主 31 项，冻结后端 16/16、实际桌宠基线 9/9、新增陪伴闭环 16/16。前两轮快照文件句柄与同时间戳排序断言修正已在 Windows 实际通过。

Root 用 gh run download 分别取得两平台源码、package-evidence 和 ai-neko-windows-x64；比对重复报告后，运行 `python3 artifacts/ci/verify-companion-download.py artifacts/ci/36228220001/verified 5871f2d94e2f176a863262ae12c86edf2e05066c` 为 PASS。核对 SHA256SUMS、clean 源 commit、ZIP 内外 build-info、GUI/后端 AMD64 PE、实际执行摘要、N.E.K.O 记忆通知、桌面模块与两张截图摘要；实际查看 Windows 快照界面。ZIP `ai-neko-0.3.0-dev.5871f2d94e2f-windows-x64.zip` 为 191,849,807 字节，SHA256 `af1f20aecd0673421d35c914b3e240f3aeca7a36b9cadf068af610676a355015`。

[可下载开发包](https://github.com/FrigidCrow/ai-neko/actions/runs/36228220001/artifacts/10902330067)：展开 Actions 产物后完整解压内层 ZIP，运行 ai-neko.exe。证据更新至 docs/evidence/companion/windows；仓库 JSON 仅统一 LF 行尾，原始下载文件保留在 artifacts。该版含快照管理和隐藏面板已听前缀续聊，旧 v0.3.0-alpha.1 保持不变。合成调用 16 模型/3 ASR/9 TTS，单次停音 34ms，不是 p95；真实云服务、用户采集及 Windows 11 真机验收未进行，完整目标仍在进行中。

## 2026-09-26 — 录音与视觉生命周期审查

修复了录音准备时停止仍打开麦克风、快速重复启动遗留流、旧ASR等待回合结束后提交并取消新录音、切设备后仍用旧麦克风等实际源码竞态。视觉重试保留同一帧与请求编号，避免响应丢失后重新截图导致幂等冲突；撤销编号持久化，覆盖POST仍在上传、已接受但响应丢失、活动图请求、取消失败后的重试以及记忆恢复期间取消。

模型每次附图及流式事件均检查120秒时效，过期中止并提示重新提问。已发送给配置服务的图片和此前已经显示的有效回答无法撤回。图片仍不写入checkpoint/数据库；撤销表只存本工程会话、请求编号和时间。

新增回归执行真实前端代码；独立审查复现恢复期间拒绝取消的缺陷，修复后迟到上传409、模型调用0。实际Electron联调另发现勾选意图与语音识别提示回归，已纳入修复，最终结果待下方补记。真实音色/术语识别/游戏判断、真实云服务、Windows11与取消p95仍未验收，未创建新版Release。

本轮最终Mac源码574项通过、1平台skip且源码扫描未变化；宿主58/58、实际Electron基线9/9、陪伴闭环18/18。最终闭环包含真实IPC请求取消、迟到图片409以及模型响应中关闭观察后无晚到输出，见[Mac报告](docs/evidence/companion/macos-companion.json)。前述勾选和识别状态回归均在实际桌面通过；合成17模型/3ASR/9TTS，用户采集和真实服务0。当前Windows旧开发包仍为5871f2d，新的Windows验收结果随后单列。

### 录音与视觉生命周期 Windows 交付通过

源码`669a8f181e45f838239bf7900bb20765588bbbd8`的[CI](https://github.com/FrigidCrow/ai-neko/actions/runs/36229884609)全部必要jobs通过：Windows575项/0skip、Linux574项/1平台skip、宿主58项、冻结后端16/16、实际桌宠9/9、陪伴闭环18/18。包内程序验证了实际IPC撤销、迟到图片请求409及关闭观察后的活动模型取消，已查看[Windows实际截图](docs/evidence/companion/windows/companion-smoke.png)。全部采集和服务仍为合成资料，Windows Server不等于Windows11游戏真机。

已实际下载并核对[新开发包](https://github.com/FrigidCrow/ai-neko/actions/runs/36229884609/artifacts/10902556977)：ZIP `ai-neko-0.3.0-dev.669a8f181e45-windows-x64.zip`，191,856,745字节，SHA256 `d803d6e68dcbfbc90a92e48c2501f1c98bc302dec01d8dd97d26fbe8fafbf849`。源码、build-info、PE执行摘要、许可、截图和报告一致，[核对记录](docs/evidence/companion/windows/download-verification.json)为PASS；Mac只核对文件，未运行exe。旧Release不变。真实云模型/搜索/ASR/TTS与游戏判断、Windows11真机及20次取消p95仍Pending，完整目标不记完成。

## 2026-09-26 — 完成审计的补证

真实网页读取组件已验证：应用自身读取三份公开官方文档，正文片段及来源元数据通过，见[独立报告](docs/evidence/companion/public-page-reads.json)。这只证明公开页面读取，真实搜索和模型组织带来源答案仍未执行。

十条记忆的原新进程测试只读取列表，未证明逐条问答前的召回与注入。本轮新增两个真实子进程、十个新会话的逐项检索和实际模型请求验证；相关37项本机测试通过，剥除注入的负控失败。生产代码未出现新故障，该补证不代表真实模型使用正确，也不代替Windows11重启验收。Windows新增回归等待本轮CI。

五项目标的真实验收仍缺：人格10场景人工质量、10张实际游戏截图、10轮真实麦克风到扬声器和20次取消/时延、3次真实搜索→正文→回答、记忆模型正确使用/未知信息回答及Windows11重启。当前没有可用项目服务配置或Windows11会话，不能以新增合成测试把这些项标记完成；免按键、多角色和长期运行扩展不作为本轮补证的新任务。

补证提交2a41fc0的[CI36230897663](https://github.com/FrigidCrow/ai-neko/actions/runs/36230897663)已全部通过：Windows576/0skip、Linux575/1skip，十项跨进程新会话召回/注入回归两平台均通过；冻结后端16、桌宠9、陪伴闭环18继续通过。[精选证据](docs/evidence/companion/memory-recall-ci.json)明确区分进程重启和电脑重启，实际模型调用仍0。产品相关目录与669a8f1无差异，本轮没有新产品修改，也未重新下载新ZIP；此前已实际下载核对的开发包仍可使用。真实服务和Windows11所需条件未改变，完整目标保持进行中。

## 2026-09-26 — v0.4.0-alpha.1 发布准备

用户已要求发布新的Release，版本统一为0.4.0，计划通过v0.4.0-alpha.1标签触发既有CI/CD。打包/配置定向检查67 passed、1平台skip；依赖版本未变。发布说明包含人格、视觉、按键语音、共享查询和本地长期记忆，仍为预览版。tag对应构建、Release创建及实际下载核对尚未执行，不能用前次开发包结果替代本次发布证据。

### v0.4.0-alpha.1 已发布并核对下载

[Release](https://github.com/FrigidCrow/ai-neko/releases/tag/v0.4.0-alpha.1)已发布为prerelease，源码`f359826bd60139a6a9efcef8d959f56c385228ba`；[tag流水线](https://github.com/FrigidCrow/ai-neko/actions/runs/36233199073)测试、Windows打包与发布全部SUCCESS。Windows576/0skip、Linux575/1平台skip、宿主58、冻结后端16/16、实际桌宠9/9、陪伴闭环18/18，额外桌面启动诊断也通过。

[Release实际下载核对](docs/evidence/companion/release-v0.4.0-alpha.1-verification.json)PASS：15个附件的GitHub摘要匹配，191,844,465字节ZIP的SHA256为`0e133684466e13d481f7de7988f5c23cef240291740cf362640c7b280f7ac3f6`；源码、版本、内外build-info、AMD64程序执行摘要、许可与截图一致，ZIP CRC通过。实际查看本次Windows白裙YUI截图；未在Mac运行Windows程序。Release中语音使用说明明确勾选朗读回复，未改tag和二进制。

发布任务完成；真实云服务、Windows11游戏窗口/麦克风/音色/建议质量以及20次取消p95仍Pending，不将本次合成17模型/3ASR/9TTS调用和单次110ms停音当作真实质量验收。五项功能的完整验收状态不因此变更。


## 2026-09-26 — 下一阶段攻略连续陪玩规划

用户要求根据最佳实践编写下阶段plan。新增[指定攻略、本地攻略库与连续陪玩](docs/NEXT-GUIDE-COMPANION.md)，拆成G1入库、G2采用管理、G3本地检索、G4对局连续性、G5桌宠/语音入口、G6验收交付。PLAN第17节及README/原陪玩方案/架构入口已同步，当前基线修正为v0.4已发布。

规划审查依据当前执行路径：网页在20,000字符和6,000字符两处裁剪；旧来源随日志保存但不作新轮检索；最近10轮聊天可能跨对局残留；来源编号仅属于当前轮。计划要求在模型裁剪前有界入库、持久文档/版本/片段ID、明确采用修订、新局过滤旧动态历史，以及删除/旧快照/在途请求防复活。参考N.E.K.O的搜索内存缓存与请求合并，保留LangGraph唯一对话决策和Memory Service唯一长期权威。

G1–G6、A01–A11及新性能目标均Pending。本轮只修改计划、说明和验证记录，没有产品代码、依赖、数据迁移或新发布；不能将文档校验写作功能验收。独立审查与文档校验结果在下方按实际补记。

独立只读评审发现两项契约缺口并已修订：语音切换/新局后成功提示由新修订的控制响应承载，避免撤销自身回合；保存并传播turn到guide/revision依赖，切换/删除后旧建议排除当前决策上下文，A06检查下一轮真实模型请求。评审未发现TTL、采用关系与旧快照语义的其他执行冲突；这属于规划审查，不是实现测试。

最终文档校验PASS：25份Markdown、386个本地链接、206个参考文件，参考仓库状态/差异指纹未变；git diff --check通过。新增计划共214行，所有新实现/运行验收仍Pending，本轮未运行产品测试。

## 2026-09-26 — MVP2 前评审问题修复与回归

依据 docs/MVP2-READINESS-REVIEW.md 完成逐项处理（清单见该文附录二）。两个红色项已修：遗忘级联不再清空会话尾部（turn_memory 只记本回合真实引用 + 内容证据扫描），checkpoint 改为按 thread 精确清理并去掉整库 VACUUM；新增同一 session 三回合回归用例证明无关回合与其 checkpoint 存活。性能项完成 recall/快照/备份/恢复/遗忘/提取的 to_thread 迁移与流式事件缓冲刷盘（16 条或 50ms 刷盘，settle 强刷）。

结果：`uv run pytest tests/` 564 passed / 12 skipped；`npm --prefix desktop test` 58/58；`uv run ruff check .`（含新增 B/SIM）0 错误。12 个 skipped 中 11 个为环境敏感子进程用例——已查明统一根因为宿主沙箱对同一路径第二次 mkdir(exist_ok=True) 抛 EEXIST，新增 tests/conftest.py 的 sandbox_compatible 探测使其显式 skip 而非误报失败；另 1 个为 Windows 凭据专属。真实云模型/搜索/ASR/TTS 调用仍为 0，Windows11 真机与 p95 验收保持 Pending，本轮不改动这些验收状态。

延期项与理由已写入 docs/MVP2-READINESS-REVIEW.md 附录二：迁移框架（随 G1）、FTS5（待 MVP2 标注集验证）、攻略独立库决策（G1 前定稿）、demo 图删除（承载 Windows 打包冒烟证据，已显式标记）、轻量读路径异步化（随迁移框架系统化）等。
