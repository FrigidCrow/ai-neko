# ai-neko

独立的个人 AI 伴侣项目：主体是 Windows 11 x64 桌面上的可见猫娘桌宠，文字聊天、流式回复和查攻略围绕她展开。LangGraph 负责对话编排，会话保存在本机，允许调用自己配置的云模型。

**当前源码为 `0.3.0` MVP1 桌宠工程预览。** 已接入参考 N.E.K.O. 的白裙 **YUI Lolita 猫娘**和本项目 Electron 宿主：透明桌面角色、待机动画、拖动、大小与置顶设置、伴随文字面板、流式回复、停止生成、历史恢复、Tavily 搜索及公开正文来源、托盘找回和退出。未配置模型时也能显示猫娘，收起聊天后她仍留在桌面。

本轮已完成 macOS 上 Python **291 passed / 1 skipped**、桌面宿主 **12 项测试**及 **79 个资源/许可文件**核验，并实际显示 YUI。**本轮 Windows CI、Windows ZIP 发布尚待执行；Windows 11 真机、真实模型连续 10 轮及真实搜索 3 次仍未验收。** M0/M1 总体保持 Partial，最新证据以 [REVIEW](REVIEW.md) 为准。M2 长期事实/人格记忆、语音尚未实现；不做键鼠代操作或控制其他软件。

[GitHub Releases](https://github.com/FrigidCrow/ai-neko/releases) 提供按版本下载。**获取 `0.3.0` 对应桌宠 ZIP 后，完整解压并双击根目录 `ai-neko.exe`**；包内包含 Electron、Python、猫娘和运行依赖，使用者无需安装 Python、Node 或 uv。首次启动阅读并接受 Live2D SDK 条款后加载角色，在猫娘旁的设置中配置自己的模型和搜索凭据。桌面宿主自动启动、退出自己的后端。

已发布的 [v0.2.0-alpha.1](https://github.com/FrigidCrow/ai-neko/releases/tag/v0.2.0-alpha.1) 是历史网页工程预览，仍会打开浏览器，不能用它体验桌宠；`v0.1.0-alpha.1` 仅为基础诊断包。

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

当前闭环为：启动见猫娘 → 点击输入 → 身旁流式回复 → 停止/继续聊天/查攻略 → 重开续接会话。实现范围见 [MVP1 规格](docs/MVP1-DESKTOP-PET.md)，当前继续验证 Windows 产物和真实服务；后续再加入长期记忆、角色表现扩展、语音与打断、视觉及主动陪伴。

本地会话记录与 LangGraph checkpoint 已有，二者都不能代替 M2 的长期事实/人格记忆。跨会话用户偏好的召回、修改和删除仍属后续工作。

窗口与托盘采用本项目最小 Electron 宿主；猫娘资源从只读 N.E.K.O. 参考中提取，来源、逐文件哈希及独立 SDK 许可见 [资源记录](docs/MVP1-ASSETS.md)。不会读取或迁移原工程的配置、运行数据、凭据或服务。

本地仓库创建不等于 Codex 侧栏已经登记。可将本目录作为新项目打开；后续开发从这个目录继续。
