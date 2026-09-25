# ai-neko 图解技能调研

调研日期：2026-09-25。目标是帮助读者理解 [实现计划](PLAN.md) 与 [模块设计](ARCHITECTURE.md)。图中描述的是拟实现的结构和流程；M0–M6 的产品能力仍需开发与验收。

本轮选择 **Mermaid 保存图的文字源，生成 SVG 供直接阅读，再汇入离线图册**。采用已安装 `visualize` 的选图原则，并参考 `engineering-software-architect` 的架构分层方法。外部 Mermaid 和 Excalidraw skills 仅调研，没有安装，也没有执行其脚本。图册实际生成与检查结果以 [REVIEW.md](../REVIEW.md) 为准。

## 1. 先分清四个概念

| 名称 | 它是什么 | 在 ai-neko 中的作用 |
|---|---|---|
| Skill | 给 AI 的工作说明，约定如何选图、写图、检查图 | 帮助把已有计划画得准确、易读；本身不是绘图软件 |
| Mermaid | 用文本描述节点、连线和交互，由渲染器生成图形 | 保存可追踪的 `.mmd` 源文件，便于随计划一起修改与评审 |
| Excalidraw | 可拖拽编辑、具有手绘风格的白板工具 | 适合讨论草图、自由布局；本轮不新增这种文件格式 |
| C4 | 从系统环境、容器、组件到代码逐层解释软件结构的方法 | 借鉴“先看整体、再看局部”的顺序；不要求画齐四层，也不等于必须使用某个特定语法 |

Mermaid 的文本定义与渲染方式见 [Mermaid 官方介绍](https://mermaid.js.org/intro/)；Excalidraw 的定位见 [官方仓库](https://github.com/excalidraw/excalidraw)。C4 的层级和按需要选用图层的原则见 [C4 官方说明](https://c4model.com/diagrams)。

## 2. 实际调查了哪些 skills

“本地已有”表示当前环境存在该 `SKILL.md`，不代表技能可能依赖的所有软件都已安装；“阅读”也不代表执行了其工作流。

| Skill 与来源 | 阅读和安装状态 | 擅长的图或工作 | 本次选择及原因 |
|---|---|---|---|
| [visualize](/Users/frigidcrow/.codex/plugins/cache/openai-bundled/visualize/1.0.39/skills/visualize/SKILL.md) | 本地已有；全文阅读 | 静态关系用 Mermaid；有必要时用交互图解释变化 | **采用选图与可读性原则**。本次交付是项目文档图册，按独立项目文件组织；不把它误当作聊天内的 HTML 片段 |
| [engineering-software-architect](/Users/frigidcrow/.codex/skills/engineering-software-architect/SKILL.md) | 本地已有；全文阅读 | 模块边界、依赖方向、C4 分层与架构取舍 | **采用架构组织方法**。帮助区分桌面宿主、对话决策、实时任务和长期记忆各自负责什么；它不是专用渲染器 |
| [insert-mermaid-diagrams](https://github.com/Kracozebr/agent-skill-mermaid-diagrams/blob/main/skills/insert-mermaid-diagrams/SKILL.md) | 外部来源；全文阅读；未安装、未运行脚本 | 从需求或现有图整理流程、时序、状态、ER 图，写入 Markdown 或 `.mmd` | **作为调研参考**。强调按事实画图、把大图拆小，与本次需要匹配；已有工具足够，无需再安装一套相似工作流 |
| [Excalidraw Diagram Skill](https://github.com/iizcm/excalidraw-skill/blob/main/SKILL.md) | 外部来源；全文阅读；未安装、未运行脚本 | 用 JSON 生成手绘风格的架构图、流程图、序列图和概念图 | **本轮不采用，保留备选**。将来要自己拖动方框、现场讨论布局时有价值；这次优先让图源与计划一起维护 |
| [design-visual-storyteller](/Users/frigidcrow/.codex/skills/design-visual-storyteller/SKILL.md) | 本地已有；全文阅读 | 视觉叙事、信息图、视频分镜和跨媒介表达 | **不启用完整工作流**。主要面向叙事与品牌传播，本次核心是模块、事件与数据关系 |
| [graphify](/Users/frigidcrow/.codex/skills/graphify/SKILL.md) | 本地已有；阅读用途、命令和主流程节；未执行 | 从已有代码与文档提取可查询的知识图谱及关系网络 | **本轮不运行**。以后分析已经实现的代码关系时再考虑；当前设计图不能包装成实际调用关系的扫描结果 |

外部 Mermaid skill 的质量规则尤其适合本次任务：只画来源中存在的关系，并且实际预览前不宣称渲染成功。它的 Markdown 更新辅助脚本本轮没有执行，图源由项目自己的文档流程维护。[技能原文](https://raw.githubusercontent.com/Kracozebr/agent-skill-mermaid-diagrams/main/skills/insert-mermaid-diagrams/SKILL.md)

外部 Excalidraw skill 含有可选上传流程。本次只阅读了说明，没有上传项目资料、执行上传脚本或生成分享链接。若以后采用它，仍需单独验证文件能否打开、标签是否正确绑定和中文显示是否清楚。[技能原文](https://raw.githubusercontent.com/iizcm/excalidraw-skill/main/SKILL.md)

## 3. 每种图分别回答什么问题

| 图种 | 看图时问的问题 | ai-neko 的具体用途 | 读图时的边界 |
|---|---|---|---|
| 总体架构图 | “有哪些部分，各自负责什么？” | 区分 Windows 桌面界面、SessionRuntime、LangGraph、模型、记忆和云服务 | 方框表示职责，不意味着每个框都要拆成一个进程或微服务 |
| 流程图 | “从这里开始，下一步做什么？什么条件走分支？” | 单次对话、记忆提取、工具执行、视觉与主动事件接入 | 箭头表达控制或数据流，须在图中标清；不能据此推断耗时 |
| 时序图 | “谁先通知谁？什么时候返回或取消？” | 文本流式回复、麦克风到声音播放、用户插嘴后的取消和清理 | 从上往下看发生顺序；纵向距离不是实际毫秒数 |
| 状态图 | “现在处于什么状态？什么事件能改变它？” | 会话运行、生成、播放、取消、结束等生命周期 | 状态转换表示允许的行为；“生成停止”和“声音停止”需要分别处理 |
| ER / 数据关系图 | “哪些记录属于哪个用户、角色、会话和轮次？” | 内部会话 ID、turn、checkpoint、长期记忆、后台记忆任务之间的关系 | 当前是概念模型，不是最终 SQLite DDL，也不宣称数据库已经创建 |
| 依赖 / 路线图 | “必须先完成什么，后面的阶段才能开始？” | P0 与 M0–M6 的先后关系和可并行准备工作 | 没有可靠估时就不画带虚构起止日期的甘特图；阶段节点不等于已经完成 |
| 部署 / 启动图 | “安装后哪些东西运行在本机，如何启动和连接？” | Windows 应用宿主、本地后端、数据目录、端口发现和云模型边界 | 是目标部署设计；Mac 上能生成此图不能证明 Windows 已运行 |
| 数据生命周期图 | “资料从哪里来、怎样保留、更正和删除？” | MemoryService 写入、检索、修正、遗忘和重启恢复 | 不把 checkpoint 当长期事实库；删除还要覆盖派生索引、旧副本和未完成任务 |

Mermaid 官方文档列有流程、时序、状态、ER 等图种；这里的“架构”“依赖”“部署”和“生命周期”是解释目的，可以用常规流程图语法画出，并不要求各自新增一个库。[官方图种与介绍](https://mermaid.js.org/intro/)

## 4. 本轮为何选 Mermaid 图册

计划的主要信息是步骤、职责、消息顺序和数据关系，Mermaid 可以直接表达这些内容。文字源适合 Git 评审；SVG 可以放大；离线图册让读者顺着目录查看，不需要先学习语法。

代价是自由排版能力较弱。遇到交叉线过多或一页过密，应拆成“总览 + 一个具体场景”，而不是继续往同一张图加框。Excalidraw 在自由布局上更适合讨论草图，但多了一套坐标和图形对象需要维护。本轮没有必要同时维护两套同义图。

渲染工具选择 Mermaid 官方 CLI。它接受 Mermaid 定义文件并输出 SVG、PNG 或 PDF；本项目只需把 `.mmd` 转成 SVG。CLI 是文档工具，和 ai-neko 的 Python/LangGraph 产品依赖分开管理。实际使用的锁定版本、命令与结果应进入项目工作记录，不凭在线文档里的版本号推断本地已安装版本。[Mermaid CLI 官方仓库](https://github.com/mermaid-js/mermaid-cli)

## 5. 图解的验收方法

1. **能对应计划**：每张图说明负责解释哪个阶段或模块；模块名、边界和事件名与架构文档一致。
2. **能看到图**：保留 Mermaid 源文件，并实际生成和打开图形；语法通过不自动等于布局清楚。
3. **能读懂关键路径**：主流程、失败分支、取消和持久化边界可见；过密的图拆开。
4. **能追踪来源**：设计依据来自本工程计划；复用依据指回 N.E.K.O 教程与源码清单。没有实现的路径明确写为设计。
5. **能持续更新**：修改计划时同步图源和渲染产物；在 REVIEW 中分别记录文档检查与产品验收。

推荐阅读顺序是：阶段路线 → 系统总览 → 一次完整对话 → LangGraph 与 SessionRuntime → 记忆 → 媒体和桌面 → Windows 交付。先理解一条消息如何走完，再学习各模块内部实现。
