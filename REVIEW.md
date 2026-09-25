# ai-neko 验收记录

2026-09-25 工程初始化与计划评审已完成，M0 已实施并进入分项验收；总体 Partial，M1–M6 仍 Pending。不存在已验收的 Windows App、真实模型调用、长期记忆召回或语音结果。历史 P0/P1 记录描述各轮完成时的状态，当前结果见末尾 M0。

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
| M0 基础与复用验证 | Partial | Mac 源码与进程证据见下；Windows 真机、前端组合实际安装/构建未验证 |
| M1 文字与工具循环 | Pending | 本工程实现、真实模型、流式与取消测试 |
| M2 本地长期记忆 | Pending | 落盘、新会话召回、纠正/遗忘、进程与电脑重启 |
| M3 桌面与角色 | Pending | 桌面接口、窗口/托盘/角色与原版共存 |
| M4 语音与打断 | Pending | Windows 真机采集/播放、打断与迟到片段 |
| M5 视觉/主动/实际工具 | Pending | 图像归属、活动门控和工具结果 |
| M6 分发与长期运行 | Pending | Windows 产物、干净机、升级恢复、7 天观察 |

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

Root 另补充 stop 的重定向测试：本机端口被其他服务接替时，shutdown 不跟随其 302 重定向，避免把 token 转发到新目标。源文件摘要在最终 smoke 前后保持一致；无首个 Git commit，因此报告 `git_commit: null` 并提供文件清单摘要，没有虚构发布来源 commit。当前未构建或发布 Windows 产物。

不在本轮结论中：原版 N.E.K.O 与 ai-neko 真正同时运行、真实 Windows junction/ACL、原媒体模块提取后 import 探针、前端组件集成。取消与崩溃中回合结算留 M1，完整事实删除留 M2；显式图暂停恢复不能证明这些行为。后端 Scope 需要可信调用方构造，目前只有本机合成 CLI，没有接受任意 user ID 的网络图接口。
