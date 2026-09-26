# 五项能力的实施与验收

日期：2026-09-26。状态：源码、合成闭环及Windows Server打包运行已通过；真实服务和Windows11真机验收未完成。GitHub现有`v0.3.0-alpha.1`不包含这里的新增功能；本轮没有发布新版。

## 可操作的功能

| 目标 | 工作区实现 | 仍需真实验证 |
| --- | --- | --- |
| 猫娘人格 | 桌宠设置编辑名字、称呼、性格、口吻、口癖和游戏风格；Memory Service版本化保存，下一轮统一注入 | 10个人格/游戏/未知场景的真实模型口吻与事实质量 |
| 桌面视觉 | 明确选择来源、预览、每次提问重新取一帧；时间/来源随轮绑定，不把图片存到日志或checkpoint；关闭、黑屏、来源变更均不回退其他画面 | Windows实际游戏窗口、最小化/多屏/权限以及10张标注游戏截图识别 |
| 语音输入/输出 | 麦克风设备选择、按键录音、ASR、同一对话图、按句TTS、实际播放驱动口型；停止先清播放再取消HTTP，晚到结果丢弃 | 实际中文语音/术语/音色、游戏背景音、10轮闭环与20次停止p95；免按键/VAD未实现 |
| 网络查询 | 现有共享`search_web`与`read_web_page`继续复用，当前后端Tavily；不同已适配模型共用配置与来源 | 配置账户的真实搜索→正文→来源3次；DeepSeek真实服务往返 |
| 长期记忆 | 本地SQLite唯一权威；明确保存/查看依据/纠正/遗忘；新会话召回；可开关自动对话提取；来源、持久任务、删除界限与恢复策略 | 用户真实对话提取/召回质量及Windows电脑重启验证；不以合成固定回复证明记忆理解正确 |

## 桌宠中如何使用

1. 打开小猫设置，配置对话服务。观察画面必须使用支持图片输入的模型；文字接口兼容不等于图片也兼容。
2. 人格设置修改后保存。当前对话不被清空，下一轮使用新版本。
3. 桌面视觉刷新来源列表、选择窗口或屏幕、查看预览并启用观察。每次提问取新图；相关图片发送到你配置的模型服务。关闭后不采集、不继续上传待发送图。
4. 配置语音识别和合成的API地址、模型、音色及各自凭据；可分别使用兼容标准音频接口的服务。选择设备并试听，开启“朗读回复”。点击“说话”开始/结束录音，也可启用`Ctrl+Shift+Space`。不把DeepSeek对话Key自动当作语音Key。
5. 默认“按需联网”：模型需要资料时调用共享搜索，搜索服务凭据只配置一次。没有搜索Key仍能普通聊天，实际需要查询时才提示配置。可切换“仅聊天”禁用本轮工具；文字与语音遵循同一选择。人格、图片和资料交给同一LangGraph处理；仍由用户操作游戏。
6. 在长期记忆中明确保存事实，查看依据、修改或遗忘。自动整理默认关闭；开启后，之后完成的对话会通过已配置模型整理用户原话中的偏好/事件，保存来源，可能产生模型费用。没有开启时，明确保存与跨会话召回仍可用。

本地开发沿用项目独立依赖与启动方式：`uv sync --locked`、`npm --prefix desktop ci`、`node desktop/vendor/fetch-core.cjs`、`npm --prefix desktop start`。不得借用参考项目虚拟环境、配置或数据。

## 与N.E.K.O的关系

固定参考源码commit：`90ccf79c95e80f899b9bf3395fa8cd9a9bfe29be`。

- **实际提取**：CJK/Latin分词、繁简转换和BM25检索；完整清单、源hash、Apache LICENSE/NOTICE及升级步骤见[记忆组件复用](MEMORY-REUSE.md)。没有新增embedding服务或运行原版模块。
- **参考后接入现有架构**：人格预设与注入、`app-screen.js`选源/截图、`app-audio-capture.js`采集代次、`tts_client/_infra.py`分句与取消、`app-audio-playback.js`停音源/清队列。没有复制原版完整聊天系统或其全局运行状态。
- 保留原文/事实/任务分层与scope先过滤，Memory Service为唯一事实和人格权威。图checkpoint只记录执行过程；图片、人格全文与召回事实不通过图state复制落盘。
- 改用显式关闭全部观察，不照搬原版停止屏幕共享后仍可保留主动视觉流、失败后整屏兜底的行为。

## 验证边界与证据

所有本轮数据、图片、语音和HTTP服务均为合成资料；未读取用户桌面、真实麦克风或其他项目凭据。

- Python全套：467通过、1项非Windows凭据库跳过；人格/记忆检索相关49项。来源为本轮工作区运行，Windows执行结果单独记录。
- 宿主：28项通过。实际Electron原桌宠基线9项及新增闭环13项通过，报告和图片位于[证据目录](evidence/companion)。新增闭环核对人格、记忆CRUD/原文、权限拒绝、所选合成窗口、fake麦克风、真实WebAudio MP3解码/播放、口型、停止和关闭观察。
- 播放`started/completed/stopped`单独登记；收起面板仍可播放，但未显示文字的ACK为0。重启不重新合成或播放历史语音。单次停止观测不能冒充20次p95通过。
- 遗忘验证包含来源关联闭包、派生待提取原文、会话/checkpoint清理、并发删除、过期写入拒绝，以及Memory事务已提交但会话清理前失败的断点重启恢复；只说明覆盖的故障断点，不声称所有故障均已穷举。
- DeepSeek普通Chat Completions已补官方端点的`max_tokens`字段及工具往返的内部上下文传递，内部内容不显示、不读出、不写checkpoint。这基于[官方协议说明](https://api-docs.deepseek.com/quick_start/agent_integrations/oh_my_pi/)和[Thinking Mode](https://api-docs.deepseek.com/guides/thinking_mode/)，真实账户调用仍为0。
- CI增加实际Windows ZIP的新增闭环门禁：`node desktop/tests/companion.smoke.cjs --archive <zip> --output <json>`，使用包内应用/后端和合成服务；最终[CI](https://github.com/FrigidCrow/ai-neko/actions/runs/36225891452)源码Windows469项/0跳过、Linux468项/1平台跳过；冻结后端16/16、桌宠基线9/9和新增闭环13/13通过。[Windows实际报告](evidence/companion/windows/companion-smoke.json)使用解压后应用及合成窗口/麦克风/模型/音频服务，单次停止70ms不等于20次p95。Windows Server运行不能替代Windows11真机。

本轮没有以静态检查、合成回复、文档通过或已有旧Release作为五项功能全部完成的证据。完整目标保持进行中。

开发包：登录GitHub后下载 [ai-neko-windows-x64](https://github.com/FrigidCrow/ai-neko/actions/runs/36225891452/artifacts/10901235961)，展开构建产物，再完整解压内层`ai-neko-0.3.0-dev.7bd14343178a-windows-x64.zip`并运行根目录`ai-neko.exe`。无需本机Python/Node；这是开发构建，未创建新版Release，产物保留14天。

[下载核对记录](evidence/companion/windows/download-verification.json)已确认191,838,005字节ZIP与CI执行报告同一摘要、源码和程序；本机仅核对下载文件，没有将Mac运行当Windows证据。
