# ai-neko

独立的个人 AI 伴侣项目：主体是 Windows 11 x64 桌面上的可见猫娘桌宠，文字聊天、流式回复和查攻略围绕她展开。LangGraph 负责对话编排，会话保存在本机，允许调用自己配置的云模型。

**最新 MVP2 Windows 开发包为 `0.4.0-dev.b443768df012`，已通过 Windows CI、真实免费语音测试和实际下载核对。** 保留参考 N.E.K.O. 的白裙 **YUI Lolita 猫娘**和本项目 Electron 宿主，提供透明角色、待机、拖动/缩放/置顶、伴随聊天、托盘找回和退出。未配置模型时也能显示猫娘，收起聊天后她仍留在桌面。

本版加入攻略库、本地资料复用与当前对局管理：保存并采用攻略后，可在后续提问和新聊天中引用同一份原文；切换攻略、新开一局和明确历史复盘各有边界。已有可编辑人格、当轮视觉、按键语音、按句朗读、共享搜索及长期记忆；已完整听完的句子可进入后续上下文。N.E.K.O. 分词/BM25组件的来源和许可已保留，使用与验收见[五项能力说明](docs/COMPANION-IMPLEMENTATION.md)。

下载[最新 MVP2 Windows 开发包（Actions 产物）](https://github.com/FrigidCrow/ai-neko/actions/runs/37135192000/artifacts/11278787928)，解开产物容器，再完整解压其中的 **`ai-neko-0.4.0-dev.b443768df012-windows-x64.zip`，双击根目录 `ai-neko.exe`**。通常需登录 GitHub，产物保留至 **2026-10-17 16:14:17 UTC**。包内包含 Electron、Python、猫娘和运行依赖，无需另装 Python、Node 或 uv。首次接受 Live2D 条款后加载角色；聊天模型单独配置，看图需使用支持图片输入的模型。免费语音组件在首次启用时下载。

免费搜索入口：**设置 → 共享联网搜索 → AnySearch（免费免 Key）**，核对基础地址 `https://api.anysearch.com/v1` 后保存。切换时已有自定义地址不会自动覆盖；聊天模型仍需单独配置，语音可选下方免费方案。额度和实测边界见[免费服务说明](docs/FREE-SERVICES-AND-WINDOWS-VM.md)。

本包已包含**免费本地中文语音：SenseVoice 识别 + Kokoro 朗读**。在 **设置 → 语音输入与输出 → 免费本地语音（无需 Key）** 中点击 **“下载并启用免费语音”**；首次下载约 310 MB 模型及额外运行组件，建议预留 2 GiB。准备完成后可点“试听已保存的音色”，无需聊天模型或语音 Key。语音在本机运行，不产生语音 API 费用；聊天模型仍需单独配置，详见[免费语音使用说明](docs/FREE-VOICE.md)。

[本次 CI](https://github.com/FrigidCrow/ai-neko/actions/runs/37135192000)通过 Windows 1,673 项、Linux 1,672 项及 1 项平台跳过，两平台桌面单测各 106 项；实际解压包通过后端 16 项、桌宠 9 项、陪伴 21 项、G6 联合 8 项及真实免费语音 11 项。固定短句的真实语音合成约 17.297 秒、识别约 2.219 秒；初版每次请求都加载模型，暂不承诺即时响应。包摘要及完整证据见[Windows 验证报告](docs/MVP2-WINDOWS-VERIFICATION.md)和[下载与校验](docs/CI-RELEASES.md)。

**Windows 11 x64 真实游戏、聊天模型建议质量、用户麦克风/音色、端到端冷暖性能及硬件停止延迟仍待验收，完整 MVP2 尚未完成。** Windows Server 2022 CI 的四套原有包测试使用合成窗口、麦克风和服务，新增免费语音测试则用真实本地模型处理固定文本与合成音频；公开 AnySearch 搜索与网页正文读取已单独实测，不代表完整带来源模型问答质量。本地资料复用仍使用所配置的模型。最新边界见[REVIEW](REVIEW.md)；免按键语音、角色扩展和主动陪伴仍属后续范围，当前不做键鼠代操作。

此前 `50227627e8d4` 攻略与免费搜索开发包的[完整记录](docs/MVP2-WINDOWS-VERIFICATION.md#历史50227627e8d4-攻略与免费搜索开发包)继续保留，该包不含免费本地语音。历史 [MVP1 收尾包](docs/MVP1-CLOSEOUT.md)和 [v0.4.0-alpha.1 Release](https://github.com/FrigidCrow/ai-neko/releases/tag/v0.4.0-alpha.1)继续保留，均不包含本次 MVP2 攻略、对局与免费搜索能力。本轮交付分支开发包，没有创建新 Release；历史发布与校验见[CI/CD 记录](docs/CI-RELEASES.md)。

[v0.3.0-alpha.1](https://github.com/FrigidCrow/ai-neko/releases/tag/v0.3.0-alpha.1)是历史基础桌宠版；[v0.2.0-alpha.1](https://github.com/FrigidCrow/ai-neko/releases/tag/v0.2.0-alpha.1)是网页预览，`v0.1.0-alpha.1`为基础诊断包。旧版本保留。

使用说明：[免费搜索、模型/语音与VM条件](docs/FREE-SERVICES-AND-WINDOWS-VM.md)、[五项能力与配置](docs/COMPANION-IMPLEMENTATION.md)、[Windows 下载与 CI/CD](docs/CI-RELEASES.md)；[v0.3 历史试用与验收](docs/M1-QUICKSTART.md)。

## 从这里进入

1. [计划与模块图册](docs/DIAGRAMS.md)：20 张图，先看整体，再看每个模块；可直接打开 [离线图册](docs/diagrams/index.html)。
2. [图解技能调研](docs/DIAGRAM-SKILLS.md)：调查了什么、各图种适合解释什么。
3. [实施计划](docs/PLAN.md)：做什么、先做什么、怎样验收。
4. [架构与接口](docs/ARCHITECTURE.md)：LangGraph、记忆服务、桌面与语音各自负责什么。
5. [教程与源码映射](docs/REFERENCES.md)：每个模块参考哪一课、哪个源文件。
6. [验收记录](REVIEW.md) 与 [工作记录](WORKLOG.md)：实际完成状态及命令。

工程位置：`/Users/frigidcrow/Dev/ai-neko`。N.E.K.O 参考工程是 `/Users/frigidcrow/Dev/neko-companion`，二者独立维护。

## 当前进展与后续验收

[MVP1工程收尾](docs/MVP1-CLOSEOUT.md)已完成：取消、遗忘、升级和桌面生命周期问题已修复，对应提交的Windows构建与下载产物已验证。真实云服务和Windows11体验仍按实际证据单独验收。

MVP2 的攻略与对局能力、本机联合验收和测量工具已进入上述 Windows 开发包。[G6 报告](docs/MVP2-G6-REPORT.md)记录阶段证据，[Windows 验证报告](docs/MVP2-WINDOWS-VERIFICATION.md)记录本次交付。聊天中的“攻略库”和“管理本局”可采用/切换资料、管理快照与当前对局；文字或语音明确控制后播放独立确认，新局隔离旧状态并停止旧声音，重启后局势待更新。本次实际 Windows 程序验证了采用后退出、新进程新聊天复用、配置搜索和禁用搜索各连续五问，以及旧局/新局/明确复盘隔离。[真实验收记录工具](docs/MVP2-LIVE-ACCEPTANCE.md)继续保留有效语音、费用和真机证据缺口；范围见[连续陪玩计划](docs/NEXT-GUIDE-COMPANION.md)，接点见[交接单](docs/HANDOFF-G1.md)。

当前闭环为：启动见猫娘 → 点击输入 → 身旁流式回复 → 停止/继续聊天/查攻略 → 重开续接会话。实现范围见 [MVP1 规格](docs/MVP1-DESKTOP-PET.md)，本版已扩展人格、视觉、按键语音与长期记忆。接下来继续 Windows 11 真机和真实服务验收；免按键对话、角色表现扩展与主动陪伴仍属后续范围。

本地会话记录与 LangGraph checkpoint 已有，二者都不能代替 M2 的长期事实/人格记忆。本版以独立 Memory Service 实现跨会话用户偏好的召回、修改和删除，保留来源及持久提取任务；真实记忆质量仍需验收。

窗口与托盘采用本项目最小 Electron 宿主；猫娘资源从只读 N.E.K.O. 参考中提取，来源、逐文件哈希及独立 SDK 许可见 [资源记录](docs/MVP1-ASSETS.md)。不会读取或迁移原工程的配置、运行数据、凭据或服务。

本地仓库创建不等于 Codex 侧栏已经登记。可将本目录作为新项目打开；后续开发从这个目录继续。
