# ai-neko

独立的个人 AI 伴侣项目：以 LangGraph 编排对话，参考 N.E.K.O 的源码和已整理的教程，优先复用其界面、角色、语音与记忆能力。目标是 Windows 11 x64 桌面 App，长期记忆保存在本机，允许使用云模型。

**M0 的源码基础已落地，Mac 的 110 项合成验收通过；阶段总状态为 Partial。** 已建立本工程 Python 依赖锁、数据隔离、鉴权本机服务和可跨进程恢复的合成 LangGraph。Windows 真机与前端组合运行兼容仍待验证；还没有真实聊天、长期记忆、桌面界面或 Windows 产物，没有复制 N.E.K.O 运行代码与素材。

开发入口：[M0 运行说明](docs/M0-QUICKSTART.md)。在本目录执行 `uv sync --locked`，再执行 `uv run --locked pytest -q`，无需模型 Key；测试使用临时合成资料。[Windows 取证入口](docs/WINDOWS-M0.md) 已提供，真机结果仍待执行。

当前产品优先级：**M1 先做好文字聊天和联网查攻略**，能搜索、读取网页正文、按平台/版本整理步骤并附来源。暂不做键鼠代操作或控制游戏/其他软件。此处是已确认需求，联网查询尚未实现。

## 从这里进入

1. [计划与模块图册](docs/DIAGRAMS.md)：20 张图，先看整体，再看每个模块；可直接打开 [离线图册](docs/diagrams/index.html)。
2. [图解技能调研](docs/DIAGRAM-SKILLS.md)：调查了什么、各图种适合解释什么。
3. [实施计划](docs/PLAN.md)：做什么、先做什么、怎样验收。
4. [架构与接口](docs/ARCHITECTURE.md)：LangGraph、记忆服务、桌面与语音各自负责什么。
5. [教程与源码映射](docs/REFERENCES.md)：每个模块参考哪一课、哪个源文件。
6. [验收记录](REVIEW.md) 与 [工作记录](WORKLOG.md)：实际完成状态及命令。

工程位置：`/Users/frigidcrow/Dev/ai-neko`。N.E.K.O 参考工程是 `/Users/frigidcrow/Dev/neko-companion`，二者独立维护。

## 第一段实现路线

固定依赖与数据隔离 → 文字聊天与联网查攻略 → 跨会话本地记忆 → 桌面角色 → 语音与打断 → 视觉/主动陪伴及截图查询 → Windows 成品验证。

M1 的首个文字体验包含固定人设对话和带来源的攻略查询，不必等桌宠或语音完成；M2 再加入关闭应用后新建会话仍能召回、修改和删除用户偏好。这些里程碑不替代后续 Windows 桌面与语音验收。

窗口与托盘对应的原版独立桌面工程本轮仍无法访问，M3 已选本项目最小 Electron 宿主；界面、渲染、音频与记忆继续按 [复用审计](docs/M0-REUSE-AUDIT.md) 提取，素材许可分别核对。

本地仓库创建不等于 Codex 侧栏已经登记。可将本目录作为新项目打开；后续开发从这个目录继续。
