# ai-neko

独立的个人 AI 伴侣项目：主体是 Windows 11 x64 桌面上的可见猫娘桌宠，文字聊天、流式回复和查攻略围绕她展开。LangGraph 负责对话编排，会话保存在本机，允许调用自己配置的云模型。

**当前发布目标：五项能力桌宠预览 `v0.4.0-alpha.1`。** 保留参考 N.E.K.O. 的白裙 **YUI Lolita 猫娘**和本项目 Electron 宿主，提供透明角色、待机、拖动/缩放/置顶、伴随聊天、托盘找回和退出。未配置模型时也能显示猫娘，收起聊天后她仍留在桌面。

本版加入可编辑人格、选定窗口的当轮视觉、按键语音输入与按句朗读、跨兼容模型共享的按需搜索，以及本地长期记忆的保存、召回、依据、纠正、遗忘和快照管理。已完整听完的句子可进入后续上下文。N.E.K.O. 分词/BM25组件的来源和许可已保留，使用与验收见[五项能力说明](docs/COMPANION-IMPLEMENTATION.md)。

前往 [v0.4.0-alpha.1 下载页](https://github.com/FrigidCrow/ai-neko/releases/tag/v0.4.0-alpha.1)，下载 **`ai-neko-0.4.0-alpha.1-windows-x64.zip`，完整解压并双击根目录 `ai-neko.exe`**。版本tag的两平台测试、Windows打包和实际桌面检查全部通过后才创建Release，结果以下载页和[发布记录](docs/CI-RELEASES.md)为准。包内包含Electron、Python、猫娘和运行依赖，无需另装Python、Node或uv。首次接受Live2D条款后加载角色；在猫娘设置中配置模型、搜索和ASR/TTS，看图需使用支持图片输入的模型。

**真实云模型/搜索/语音质量、Windows11游戏真机及p95性能仍待验收。** CI使用合成窗口、麦克风和服务；应用的公开网页读取已另行实测。M0/M1等阶段的完整验收不因发布而转为完成，最新证据见[REVIEW](REVIEW.md)。免按键语音、角色扩展和主动陪伴仍属后续范围，当前不做键鼠代操作。

[v0.3.0-alpha.1](https://github.com/FrigidCrow/ai-neko/releases/tag/v0.3.0-alpha.1)是历史基础桌宠版；[v0.2.0-alpha.1](https://github.com/FrigidCrow/ai-neko/releases/tag/v0.2.0-alpha.1)是网页预览，`v0.1.0-alpha.1`为基础诊断包。旧版本保留。

使用说明：[M1 试用与验收](docs/M1-QUICKSTART.md)、[Windows 下载与 CI/CD](docs/CI-RELEASES.md)。

## 从这里进入

1. [计划与模块图册](docs/DIAGRAMS.md)：20 张图，先看整体，再看每个模块；可直接打开 [离线图册](docs/diagrams/index.html)。
2. [图解技能调研](docs/DIAGRAM-SKILLS.md)：调查了什么、各图种适合解释什么。
3. [实施计划](docs/PLAN.md)：做什么、先做什么、怎样验收。
4. [架构与接口](docs/ARCHITECTURE.md)：LangGraph、记忆服务、桌面与语音各自负责什么。
5. [教程与源码映射](docs/REFERENCES.md)：每个模块参考哪一课、哪个源文件。
6. [验收记录](REVIEW.md) 与 [工作记录](WORKLOG.md)：实际完成状态及命令。

工程位置：`/Users/frigidcrow/Dev/ai-neko`。N.E.K.O 参考工程是 `/Users/frigidcrow/Dev/neko-companion`，二者独立维护。

## MVP1 与后续路线

当前闭环为：启动见猫娘 → 点击输入 → 身旁流式回复 → 停止/继续聊天/查攻略 → 重开续接会话。实现范围见 [MVP1 规格](docs/MVP1-DESKTOP-PET.md)，本版已扩展人格、视觉、按键语音与长期记忆。接下来继续 Windows 11 真机和真实服务验收；免按键对话、角色表现扩展与主动陪伴仍属后续范围。

本地会话记录与 LangGraph checkpoint 已有，二者都不能代替 M2 的长期事实/人格记忆。本版以独立 Memory Service 实现跨会话用户偏好的召回、修改和删除，保留来源及持久提取任务；真实记忆质量仍需验收。

窗口与托盘采用本项目最小 Electron 宿主；猫娘资源从只读 N.E.K.O. 参考中提取，来源、逐文件哈希及独立 SDK 许可见 [资源记录](docs/MVP1-ASSETS.md)。不会读取或迁移原工程的配置、运行数据、凭据或服务。

本地仓库创建不等于 Codex 侧栏已经登记。可将本目录作为新项目打开；后续开发从这个目录继续。
