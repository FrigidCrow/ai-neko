# 五项能力的实施与验收

日期：2026-09-26。状态：源码、合成闭环及Windows Server打包运行已通过；真实服务和Windows11真机验收未完成。`v0.4.0-alpha.1`已发布并包含这里的新增功能，可从[Release页](https://github.com/FrigidCrow/ai-neko/releases/tag/v0.4.0-alpha.1)下载。历史`v0.3.0-alpha.1`不包含这些新增功能。

## 可操作的功能

| 目标 | 工作区实现 | 仍需真实验证 |
| --- | --- | --- |
| 猫娘人格 | 桌宠设置编辑名字、称呼、性格、口吻、口癖和游戏风格；Memory Service版本化保存，下一轮统一注入 | 10个人格/游戏/未知场景的真实模型口吻与事实质量 |
| 桌面视觉 | 明确选择来源、预览、每次提问重新取一帧；时间/来源随轮绑定，不把图片存到日志或checkpoint；关闭、黑屏、来源变更均不回退其他画面 | Windows实际游戏窗口、最小化/多屏/权限以及10张标注游戏截图识别 |
| 语音输入/输出 | 麦克风设备选择、按键录音、ASR、同一对话图、按句TTS、实际播放驱动口型；停止先清播放再取消HTTP，晚到结果丢弃；按完整已听句子续接上下文 | 实际中文语音/术语/音色、游戏背景音、10轮闭环与20次停止p95；免按键/VAD未实现 |
| 网络查询 | 现有共享`search_web`与`read_web_page`继续复用，当前后端Tavily；不同已适配模型共用配置与来源 | 配置账户的真实搜索→正文→来源3次；DeepSeek真实服务往返 |
| 长期记忆 | 本地SQLite唯一权威；明确保存/查看依据/纠正/遗忘；新会话召回；可开关自动对话提取；桌宠中创建、确认恢复与删除记忆快照 | 用户真实对话提取/召回质量及Windows电脑重启验证；不以合成固定回复证明记忆理解正确 |

## 桌宠中如何使用

1. 打开小猫设置，配置对话服务。观察画面必须使用支持图片输入的模型；文字接口兼容不等于图片也兼容。
2. 人格设置修改后保存。当前对话不被清空，下一轮使用新版本。
3. 桌面视觉刷新来源列表、选择窗口或屏幕、查看预览并启用观察。每个新问题取新图；发送结果未知时重试同一问题会复用原图和请求编号。相关图片发送到你配置的模型服务。关闭或切换来源会撤销活动视觉请求；每次模型请求和流式输出都复核截图时间，超过120秒提示重新提问。已发送给服务的图片无法撤回。
4. 配置语音识别和合成的API地址、模型、音色及各自凭据；可分别使用兼容标准音频接口的服务。选择设备并试听，开启“朗读回复”。点击“说话”开始/结束录音，也可启用`Ctrl+Shift+Space`。不把DeepSeek对话Key自动当作语音Key。
5. 默认“按需联网”：模型需要资料时调用共享搜索，搜索服务凭据只配置一次。没有搜索Key仍能普通聊天，实际需要查询时才提示配置。可切换“仅聊天”禁用本轮工具；文字与语音遵循同一选择。人格、图片和资料交给同一LangGraph处理；仍由用户操作游戏。
6. 在长期记忆中明确保存事实，查看依据、修改或遗忘。自动整理默认关闭；开启后，之后完成的对话会通过已配置模型整理用户原话中的偏好/事件，保存来源，可能产生模型费用。没有开启时，明确保存与跨会话召回仍可用。
7. 在同一设置页的“记忆快照”创建备份，选择快照后明确确认恢复或删除。恢复会停止当前任务、恢复人格和事实，保留后来做过的纠正与遗忘，并清理受移除记忆影响的会话。该快照不包含服务凭据，也不是全部应用数据的备份；损坏快照会禁止恢复。

收起聊天面板仍能听语音。下次提问会带上已完整播放的连续句子；被打断句子的未听完部分不作为已听内容，文字显示确认另行登记。重启不会自动播放历史声音。

准备录音、识别和等待提交均绑定同一次录音；停止或重新开始后，旧结果不能提交或影响新录音。更换麦克风会取消本次录音，需要重新说话。关闭观察采用持久化请求撤销编号，响应丢失或记忆恢复期间到达的旧图片请求也不能重新启动；撤销记录不保存图片或问题内容。

本地开发沿用项目独立依赖与启动方式：`uv sync --locked`、`npm --prefix desktop ci`、`node desktop/vendor/fetch-core.cjs`、`npm --prefix desktop start`。不得借用参考项目虚拟环境、配置或数据。

## 与N.E.K.O的关系

固定参考源码commit：`90ccf79c95e80f899b9bf3395fa8cd9a9bfe29be`。

- **实际提取**：CJK/Latin分词、繁简转换和BM25检索；完整清单、源hash、Apache LICENSE/NOTICE及升级步骤见[记忆组件复用](MEMORY-REUSE.md)。没有新增embedding服务或运行原版模块。
- **参考后接入现有架构**：人格预设与注入、`app-screen.js`选源/截图、`app-audio-capture.js`采集代次、`tts_client/_infra.py`分句与取消、`app-audio-playback.js`停音源/清队列。没有复制原版完整聊天系统或其全局运行状态。
- 保留原文/事实/任务分层与scope先过滤，Memory Service为唯一事实和人格权威。图checkpoint只记录执行过程；图片、人格全文与召回事实不通过图state复制落盘。
- 改用显式关闭全部观察，不照搬原版停止屏幕共享后仍可保留主动视觉流、失败后整屏兜底的行为。

## 验证边界与证据

构建测试和桌面闭环的数据、图片、语音及HTTP服务均为合成资料；未读取用户桌面、真实麦克风或其他项目凭据。另单独执行了下方真实公开网页读取，不与完整真实模型问答混记。

- 快照与已听上下文收尾：本机全套Python574项通过、1项Windows凭据库专属skip，详细结果见REVIEW；Windows结果按对应commit独立记录。新增回归涵盖API确认与revision冲突、快照移除手动事实后的旧对话清理，以及清理失败时阻止读取旧事件、重启恢复。
- 宿主：58项通过。实际Electron原桌宠基线9项及新增闭环18项通过，报告和图片位于[证据目录](evidence/companion)。新增闭环核对人格、记忆CRUD/原文、快照恢复/删除/损坏与冲突、权限拒绝、所选合成窗口、fake麦克风、真实WebAudio MP3解码/播放、口型、停止和关闭观察。
- 播放`started/completed/stopped`绑定原文Unicode码点范围；收起面板已听内容可进入下一轮，但未显示文字的ACK仍为0。已验证第一句听完、第二句停止后仅第一句进入模型上下文；URL/引用被文字ACK从中截断也不会误算成已读出。重启不重新合成或播放历史语音。单次停止观测不能冒充20次p95通过。
- 遗忘验证包含来源关联闭包、派生待提取原文、会话/checkpoint清理、并发删除、过期写入拒绝，以及Memory事务已提交但会话清理前失败的断点重启恢复；只说明覆盖的故障断点，不声称所有故障均已穷举。
- 十项记忆补证使用两个实际Python进程：重启后逐条检索5个偏好和5个事件，在10个新会话中核对来源及模型请求注入；相关37项本机测试通过。[补证CI](evidence/companion/memory-recall-ci.json)在Windows576项/0skip、Linux575项/1平台skip中再次通过该回归。它证明检索/注入路径，不证明真实模型答对或Windows电脑重启。
- DeepSeek普通Chat Completions已补官方端点的`max_tokens`字段及工具往返的内部上下文传递，内部内容不显示、不读出、不写checkpoint。这基于[官方协议说明](https://api-docs.deepseek.com/quick_start/agent_integrations/oh_my_pi/)和[Thinking Mode](https://api-docs.deepseek.com/guides/thinking_mode/)，真实账户调用仍为0。
- 已用应用自己的`WebTools.read_web_page`真实读取Python、Git和LangGraph三份官方文档，核对正文片段、来源、读取时间与指纹；[读取证据](evidence/companion/public-page-reads.json)单独记录。该组件不需要搜索Key，成功读取公开页面不能证明Tavily搜索或模型带来源回答已经通过。
- CI增加实际Windows ZIP的新增闭环门禁：`node desktop/tests/companion.smoke.cjs --archive <zip> --output <json>`，使用包内应用/后端和合成服务；最终[CI](https://github.com/FrigidCrow/ai-neko/actions/runs/36229884609)源码Windows575项/0跳过、Linux574项/1平台跳过；冻结后端16/16、桌宠基线9/9和新增闭环18/18通过。[Windows实际报告](evidence/companion/windows/companion-smoke.json)使用解压后应用及合成窗口/麦克风/模型/音频服务，单次停止40ms不等于20次p95。Windows Server运行不能替代Windows11真机。

本轮没有以静态检查、合成回复、文档通过或已有旧Release作为五项功能全部完成的证据。完整目标保持进行中。

当前版本：[v0.4.0-alpha.1 Release](https://github.com/FrigidCrow/ai-neko/releases/tag/v0.4.0-alpha.1)。下载`ai-neko-0.4.0-alpha.1-windows-x64.zip`，完整解压并运行根目录`ai-neko.exe`，无需本机Python/Node。该版本包含五项能力、快照管理、已听上下文、录音生命周期与视觉撤销修复。[发布CI](https://github.com/FrigidCrow/ai-neko/actions/runs/36233199073)双平台源码、冻结后端、桌宠及新增闭环全部通过，详细结果见[发布记录](CI-RELEASES.md)。

本次[Release下载核对](evidence/companion/release-v0.4.0-alpha.1-verification.json)通过：15个附件摘要、ZIP完整性、包内外版本、源码与运行报告一致。

前次开发构建669a8f1的[历史下载核对](evidence/companion/windows/download-verification.json)保留，不能作为本次Release的下载证据。本机只核对Windows文件，不在Mac上运行exe。
