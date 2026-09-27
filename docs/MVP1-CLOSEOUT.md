# MVP1 工程收尾与 MVP2 入口

日期：2026-09-27。状态：**MVP1工程收尾完成，CI与Windows下载核验通过，可进入MVP2 G1**。本页汇总现有桌宠版本的缺陷处理与交付证据；不将准备代码或合成测试当作MVP2功能、真实服务质量或Windows11真机验收。

## 本批次范围

保留YUI猫娘桌宠、文字流式对话、停止、联网资料/来源、人格、按次桌面视觉、按键语音和本地长期记忆。修复取消、遗忘、缓存及数据库升级边界；移出未实现的攻略库界面钩子。用户已授权提交推送并运行现有CI/CD，开发目录统一为 `/Users/frigidcrow/Dev/ai-neko`。

## 缺陷收束

| 项目 | 本次处理与验收 |
| --- | --- |
| 记忆检索期间停止仍启动模型 | Runtime接受前复核取消/记忆修订/关闭/并发请求，事件屏障及实际API专项测试通过 |
| 未确认纯文字请求取消及迟到事件 | 前端持久撤销请求，取消期间禁止迟到文字展示/ACK；新增竞态回归和实际Electron场景通过 |
| 遗忘后转述仍留在历史 | 用户原话与实际注入助手文本分别记录依赖，跨重启清理派生回答；未展示的后台召回不污染无关用户问题，已遗忘占位不传播；共享来源先持久记录原话依赖，长原文及跨库中断恢复专项通过 |
| 同时间戳更正错误召回 | 缓存按实际索引文本校验；遗忘/恢复/关闭清理分词缓存，记忆专项回归通过 |
| 召回与更正/关闭冲突 | 同一事务获取事实、人格和修订；Runtime跟踪线程任务并等待实际工作结束，专项通过 |
| 旧程序打开未来数据库 | 首次写库前拒绝高版本；框架与记忆服务回归通过 |
| Windows迁移备份文件被占用 | SQLite备份连接在文件替换前显式关闭；补文件替换前关闭断言 |
| 自动迁移备份残留被遗忘原文 | 服务初始化成功后清理固定名自动备份，失败保留恢复副本；不删除用户主动快照，专项通过 |
| 本机测试被泛化跳过 | 只对已知沙箱EEXIST行为跳过；其他启动异常失败，CI仍拒绝非平台跳过 |
| 未实现的MVP2界面承诺 | 暂存此前攻略UI补丁，恢复MVP1真实能力提示；不丢弃原开发记录 |

## 旧评审条目的去向

以本页为最新收束记录；[前期评审](MVP2-READINESS-REVIEW.md)保留历史推理和当时行号，不将旧“已修复”描述替代本轮证据。

- 已有修复继续回归：精确checkpoint清理、流式事件批量落盘、记忆来源查询、凭据回滚、创建时文件权限、视觉黑屏检测、快捷键冲突提示。
- 迁移框架、攻略独立数据库目录和资料检索接口骨架已具备。攻略文档/版本/片段表、采用关系与资料检索仍未实现，归入MVP2 G1–G3。
- FTS5/向量检索、Runtime或前端大规模拆分、API契约生成、HTTP连接池复用为后续优化；当前不引入额外框架，不影响已定义的MVP1操作闭环。
- M0诊断图保留用于跨进程恢复与打包验收，产品唯一对话图仍为 `ai_neko.chat.graph`。
- 历史事件和checkpoint保留属于现有会话功能；没有自动删除用户历史的新策略。游标必须来自事件接口，不能拿会话摘要的最后生成序号冒充已投递位置。
- 现有 `first_text_ms` / `turn_total_ms` 从回合接受后开始计时，是后端生成指标；不包含完整输入/录音/网络/播放时间，不能用作端到端提速证明。
- 遗忘会清理真正消费过敏感原话或派生助手回答的后续回合，即使后面换了话题。未展示的后台召回不通过通用用户问题盲目传播。v2旧数据缺少当时的历史投递记录，升级时按前10个候选保守回填，可能多清理；新回合按实际输入分别记录。退出/恢复会等待真实本地SQLite操作结算，不承诺硬时限。

## 验证与交付

修复提交为 `748bab5164882c044b4108d41a7fa8df82a99a32`。[CI 36316166347](https://github.com/FrigidCrow/ai-neko/actions/runs/36316166347)全部必需jobs通过；本次为分支构建，发布job按设计跳过，没有创建新Release或更改默认分支。

| 验证 | 实际结果 |
| --- | --- |
| 本机Python / 格式 | 640通过、1 Windows凭据专属skip；ruff及72文件格式检查通过 |
| Linux CI | 640通过、1 Windows凭据专属skip；ci_gate PASS |
| Windows Server 2022 CI | 641通过、0skip；PASS |
| 桌面宿主单元测试 | 两平台及打包job均64/64 |
| 同一ZIP实际运行 | 冻结后端16/16、桌宠基线9/9、陪伴闭环20/20 |
| 取消实测 | 20次实际WebAudio停止；Windows p95约0.20ms，仅点击到stop()返回，非声学或完整端到端延迟 |
| 真实服务 / 用户采集 | 云模型、搜索、ASR/TTS及用户屏幕/麦克风采集均0；另行验收 |

下载[最新Windows修复包（Actions产物）](https://github.com/FrigidCrow/ai-neko/actions/runs/36316166347/artifacts/10931091498)，通常需登录GitHub，保留至2026-10-11 11:42 UTC。下载的是产物容器，解开后再完整解压其中的 `ai-neko-0.4.0-dev.748bab516488-windows-x64.zip`，双击根目录 `ai-neko.exe`。这是可运行桌面版，不是Source code。旧 `v0.4.0-alpha.1` Release保留且不包含本次修复。

[下载核对记录](evidence/companion/mvp1-closeout-verification.json)为PASS：ZIP为191,878,279字节，SHA256 `53eb5bfad5187094558dbfcd2ee814ceb4ceea8310aa638da7d0c1b32c8db65f`；CRC、包内外build-info、两套AMD64程序、执行报告及许可摘要一致。Windows文本按Git CRLF检出，逐文件重建检出字节核对通过，不能与Mac LF锁文件直接比原始摘要。Mac只验证文件，未运行Windows exe。

归档证据：[构建信息](evidence/companion/mvp1-closeout-windows/build-info.json)、[冻结后端](evidence/companion/mvp1-closeout-windows/package-smoke.json)、[桌宠基线](evidence/companion/mvp1-closeout-windows/desktop-smoke.json)、[陪伴闭环](evidence/companion/mvp1-closeout-windows/companion-smoke.json)、[Windows白裙YUI截图](evidence/companion/mvp1-closeout-windows/companion-smoke.png)、[记忆快照界面](evidence/companion/mvp1-closeout-windows/companion-smoke-snapshots.png)。已目视核对，未出现未实现的MVP2攻略库入口。

Windows11用户真机、真实云模型/搜索/ASR/TTS的游戏建议与听感仍需独立体验证据。工程收尾和CI完成不代表这些体验项已通过，也不将M0–M6历史总表全部改为PASS。

## MVP2 下一步

从 [G1交接单](HANDOFF-G1.md) 和 [MVP2计划](NEXT-GUIDE-COMPANION.md) 进入：首先完成公开文字攻略URL→正文/版本/段落保存→退出重启读取，再做采用关系和本地优先检索。本轮不开始这些新功能。
