# N.E.K.O 参考映射与提取边界

本文件是独立工程的规划输入。我们参考 N.E.K.O 的功能实现、故障处理和已有教程，在新工程内使用 LangGraph 组织对话流程。**建立引用关系不代表代码已经移植、依赖已经安装或功能已经验收。** 首版范围由本工程 PLAN 决定；完整 24 课是按需查阅的知识地图，不是首版必须实现的功能清单。

参考源码位于 `/Users/frigidcrow/Dev/neko-companion`，当前源码基线为 `90ccf79c95e80f899b9bf3395fa8cd9a9bfe29be`。教程来自这个目录的未提交工作树，不能仅凭源码 commit 还原。相应路径、状态和 SHA256 见 [reference-manifest.json](/Users/frigidcrow/Dev/ai-neko/docs/reference-manifest.json)。该清单记录文件快照，不记录配置、运行记忆或凭据；本次没有复制教程或生产源码。

## 使用这份地图

- **重写编排**：把对话选择、模型调用与工具循环写入本工程 LangGraph；原版会话管理器只作为行为参考。
- **提取后复用**：保留适用算法或组件，通过本工程的配置、模型、存储和事件接口接入；尚未验证可直接独立运行。
- **选择性扩展**：先登记能力与限制，只有进入本工程计划后再实现。
- **待核实外部依赖**：缺少源码、许可或接口证据的组件，不计入现有可移植代码。

以下“验证建议”是未来提取工作的验收输入，均未在新工程执行。原教程的合成实验可提供场景与断言思路，不作为新产品的通过证据。

当前 M0 为 Partial，M1–M6 仍 Pending；参考映射不代表对应功能已实现。2026-09-25 已将查攻略/资料查询提前至 M1，键鼠/电脑控制当前不做：

| 实施阶段 | 主要参考组 | 提取边界 |
| --- | --- | --- |
| M0 隔离、版本与接口 | R01、R09、R10 | 固定模型与存储接口，核实桌面来源，落实独立运行边界 |
| M1 文字/查攻略/工具图 | R01、R02、R07、R08 | 唯一图编排，真实搜索与网页正文读取、带来源回答 |
| M2 本地记忆 | R03、R09 | 写入、召回、纠正／遗忘和 checkpoint 旧副本联合验证 |
| M3 桌面与角色 | R04、R10 | 正式聊天界面、一个可用许可的角色路径与桌面宿主 |
| M4 语音与打断 | R02、R05 | ASR → 文本图 → TTS，媒体轮次与取消统一 |
| M5 视觉、主动与查询联动 | R06、R07、R08 | 图像和活动门控统一入图，复用 M1 查询能力 |
| M6 分发与长期运行 | R09、R10 | Windows 便携包、升级恢复与实际观察证据 |

R08 中其余娱乐／渠道能力和完整插件管理等仍属于后续选择项，不能由这个对应表自动扩大首版承诺。

## R01 对话图与模型适配

教程：L01、L03；完整入口见后面的 24 课表。

源码：[模型工厂](/Users/frigidcrow/Dev/neko-companion/utils/llm_client/factory.py)、[消息类型](/Users/frigidcrow/Dev/neko-companion/utils/llm_client/messages.py)、[OpenAI 协议客户端](/Users/frigidcrow/Dev/neko-companion/utils/llm_client/openai_client.py)、[供应商参数](/Users/frigidcrow/Dev/neko-companion/config/providers.py)、[工具注册与执行](/Users/frigidcrow/Dev/neko-companion/main_logic/tool_calling.py)、[原版工具循环](/Users/frigidcrow/Dev/neko-companion/main_logic/omni_offline_client/_tools.py)、[上下文注入](/Users/frigidcrow/Dev/neko-companion/main_logic/core/context_append.py)。

**目标边界：重写图编排，选择性提取模型与工具适配。** 原版 `utils.llm_client.ChatOpenAI` 是项目自有的 SDK 封装，不是 `langchain_openai.ChatOpenAI`；同名类不意味着消息对象、工具协议或 Runnable 接口相同。新图应调用明确的模型端口，先选一条供应商路径，转换消息、流式片段和工具结果，不并排保留两套自动模型选择和工具循环。`_tools.py` 用于理解行为，不能作为一个带完整循环的黑盒再嵌进图循环。

建议先建立 `prepare_context → generate → tools / finish` 的单一流程；记忆读取由 Memory Service 提供，工具失败与无命中分别返回。首版验证参数拒绝、调用 ID 对齐、循环上限、只读查询结果与来源。外部副作用的重试与防重放只留作未来扩展参考，当前不实施对应执行框架；图能恢复不自动保证外部动作恰好执行一次。

## R02 会话、流式输出与取消

教程：L02。

源码：[WebSocket 入口](/Users/frigidcrow/Dev/neko-companion/main_routers/websocket_router.py)、[流式输入分流](/Users/frigidcrow/Dev/neko-companion/main_logic/core/streaming.py)、[会话生命周期](/Users/frigidcrow/Dev/neko-companion/main_logic/core/lifecycle.py)、[回合收口](/Users/frigidcrow/Dev/neko-companion/main_logic/core/turn.py)、[原版离线会话取消](/Users/frigidcrow/Dev/neko-companion/main_logic/omni_offline_client/_lifecycle.py)。

**目标边界：复用状态约束，重写与图之间的会话协调层。** 会话协调层负责连接、运行任务、`turn_id` / generation、取消和媒体归属；LangGraph 负责对话决策。不要把原版 `LLMSessionManager` 整体接入图节点形成第二个决策中心。语音打断必须停止播放、处理生成取消、清理队列，并丢弃旧轮输出；图的 `interrupt` 暂停／恢复不是停止扬声器的命令。

验证取消后已排队的旧片段、旧完成回调、等锁期间切换轮次、窗口重连、退出清理。首版文字流也应使用轮次归属协议，给后续 TTS 留同一套约束。

## R03 长期记忆写入、召回与纠错

教程：L09、L10、L11。

源码：[近期记录](/Users/frigidcrow/Dev/neko-companion/memory/recent.py)、[事实提取和保存](/Users/frigidcrow/Dev/neko-companion/memory/facts.py)、[时间与 FTS 索引](/Users/frigidcrow/Dev/neko-companion/memory/timeindex.py)、[混合检索](/Users/frigidcrow/Dev/neko-companion/memory/hybrid_recall.py)、[本地向量服务](/Users/frigidcrow/Dev/neko-companion/memory/embeddings.py)、[去重与冲突](/Users/frigidcrow/Dev/neko-companion/memory/fact_dedup.py)、[持久任务队列](/Users/frigidcrow/Dev/neko-companion/memory/outbox.py)、[回合后处理](/Users/frigidcrow/Dev/neko-companion/app/memory_server/post_turn.py)、[反思合成](/Users/frigidcrow/Dev/neko-companion/memory/reflection/synthesis.py)、[人格纠正](/Users/frigidcrow/Dev/neko-companion/memory/persona/corrections.py)、[服务入口](/Users/frigidcrow/Dev/neko-companion/app/memory_server/routes.py)。

**目标边界：提取已有实现，组成唯一 Memory Service。** 按本工程 ARCHITECTURE 第 3 节定义的 `commit_turn`、`retrieve`、`correct`、`forget` 等契约及角色／主体隔离，选择可提取实现；接口名称和字段以该节为准，不因参考映射增加一套并行接口。原版由 JSON、SQLite 索引及 NDJSON 恢复状态共同组成，并非一个可原样替换的纯函数库：例如 `recent.py` 引用全局配置、语言、模型工厂和云存档维护状态，服务入口还承担后台任务调度。需注入本工程的数据根、模型端口、时钟、任务调度和文件锁；禁止直接导入参考工程的服务初始化。

LangGraph checkpoint 只保存会话执行状态。事实、事件、来源和修订关系由 Memory Service 持有，索引是可重建数据；允许为实现需要选择 SQLite 等存储，但不能因为采用 LangGraph 就自动推翻原版记忆实现重写全部算法。调用云模型整理或聊天时，选中的内容会发送到配置的端点；“本地保存”不等于“完全离线”。

建议先验收偏好与事件写入、进程重启、新会话召回和明确事实纠正；复杂反思／人格维护按需推进。错误事实的更正与完整遗忘不同：遗忘还需处理派生索引、后台在途任务、checkpoint 内旧消息和备份保留策略。原版 outbox 与其他文件不是同一个数据库事务，不能照搬名称后宣称全局原子或零丢失。

## R04 前端、角色资料与角色渲染

教程：L12、L13、L14。

源码：[聊天消息协议](/Users/frigidcrow/Dev/neko-companion/frontend/react-neko-chat/src/message-schema.ts)、[聊天挂载](/Users/frigidcrow/Dev/neko-companion/frontend/react-neko-chat/src/mount.tsx)、[消息块显示](/Users/frigidcrow/Dev/neko-companion/frontend/react-neko-chat/src/MessageBlockView.tsx)、[旧全局桥接](/Users/frigidcrow/Dev/neko-companion/static/app/app-chat-adapter.js)、[角色字段](/Users/frigidcrow/Dev/neko-companion/config/character_fields.py)、[角色卡接口](/Users/frigidcrow/Dev/neko-companion/main_routers/characters_router/cards.py)、[Live2D](/Users/frigidcrow/Dev/neko-companion/static/live2d/live2d-core.js)、[VRM](/Users/frigidcrow/Dev/neko-companion/static/vrm/vrm-core.js)、[MMD](/Users/frigidcrow/Dev/neko-companion/static/mmd/mmd-core.js)、[PNGTuber](/Users/frigidcrow/Dev/neko-companion/static/pngtuber-core.js)。

**目标边界：提取展示组件，重接事件和资源服务。** 主仓库确有 Web UI 与角色渲染源码，可以独立评估；这不包含已核实的完整 Electron 宿主。聊天组件与消息协议可优先评估，旧 `window.appState`、全局回调、路由和本机路径需要改为本工程接口。首版选择一种角色显示方式，不要求同时移植四套模型格式；角色卡导入和高级动作也不因此成为前置项。

验证流式消息更新、重连去重、取消状态、模型加载失败、中文路径、资源释放及窗口尺寸。角色模型、字体、音色和渲染 SDK 的来源与许可各自核对；不能从主仓库许可推定素材均可重新分发。

## R05 语音输入、身份与输出

教程：L04、L05、L06。

源码：[麦克风采集](/Users/frigidcrow/Dev/neko-companion/static/app/app-audio-capture.js)、[音频处理器](/Users/frigidcrow/Dev/neko-companion/static/audio-processor.js)、[输入归属](/Users/frigidcrow/Dev/neko-companion/main_logic/voice_turn/audio_input.py)、[ASR 公共入口](/Users/frigidcrow/Dev/neko-companion/main_logic/asr_client/__init__.py)、[ASR 端点策略](/Users/frigidcrow/Dev/neko-companion/main_logic/asr_client/provider_policy.py)、[身份策略](/Users/frigidcrow/Dev/neko-companion/main_logic/voice_identity_service/policy.py)、[TTS 公共入口](/Users/frigidcrow/Dev/neko-companion/main_logic/tts_client/__init__.py)、[TTS 运行桥接](/Users/frigidcrow/Dev/neko-companion/main_logic/core/tts_runtime.py)、[音频播放](/Users/frigidcrow/Dev/neko-companion/static/app/app-audio-playback.js)。

**目标边界：提取一种 ASR 和一种 TTS 适配，独立管理媒体生命周期。** 适配器依赖供应商配置、回调、线程／队列、音色元数据和会话取消，不能只复制一个入口文件。图接受已经归属到轮次的文字／语音事件并输出可消费的文本事件，媒体模块负责采集、播放与即时停止，不再自行选择工具或启动另一套完整对话。

先验证采样格式、语音结束、识别超时、分段合成、音频首包、停止与旧片段丢弃，再做真机试听。声纹注册、身份信任和自动唤醒是可选扩展，声纹相似度不等于已认证身份；教程覆盖这些内容不意味着首版必须启用。

## R06 视觉输入与主动陪伴

教程：L07、L08。

源码：[截图工具](/Users/frigidcrow/Dev/neko-companion/utils/screenshot_utils.py)、[多模态轮次](/Users/frigidcrow/Dev/neko-companion/main_logic/core/multimodal_turn.py)、[模型媒体转换](/Users/frigidcrow/Dev/neko-companion/main_logic/omni_offline_client/_media.py)、[主动服务](/Users/frigidcrow/Dev/neko-companion/main_logic/proactive_chat/service.py)、[主动决策](/Users/frigidcrow/Dev/neko-companion/main_logic/proactive_chat/decisions.py)、[活动状态机](/Users/frigidcrow/Dev/neko-companion/main_logic/activity/state_machine.py)、[前端主动事件](/Users/frigidcrow/Dev/neko-companion/static/app/app-proactive.js)。

**目标边界：先手动视觉输入，主动行为作为图的另一种事件入口。** 图片规范化、大小限制和轮次绑定可评估提取；新工程自行定义图输入。已有 proactive service 虽分离成服务，仍引用项目配置和对话服务，不能原样运行后与新图同时生成。定时／活动调度只决定何时提交事件，图统一完成记忆检索、生成及工具选择。

验证截图过期、取消后图片回流、供应商不支持图片、用户正在说话时的主动任务让行和安静时段。持续屏幕采集、凝神与主动多媒体能力留待选入，首版不因此常驻采集屏幕。

## R07 Agent、工具、插件与 MCP

教程：L17、L18、L19、L20；基础工具协议同时参考 L03。

源码：[旧任务执行器](/Users/frigidcrow/Dev/neko-companion/brain/task_executor.py)、[插件公开入口](/Users/frigidcrow/Dev/neko-companion/plugin/sdk/plugin/__init__.py)、[插件宿主](/Users/frigidcrow/Dev/neko-companion/plugin/core/host.py)、[插件注册](/Users/frigidcrow/Dev/neko-companion/plugin/core/registry.py)、[模型网关请求](/Users/frigidcrow/Dev/neko-companion/plugin/server/model_gateway/request.py)、[MCP 调用适配](/Users/frigidcrow/Dev/neko-companion/plugin/plugins/mcp_adapter/invoker.py)、[MCP 序列化](/Users/frigidcrow/Dev/neko-companion/plugin/plugins/mcp_adapter/serializer.py)、[安装所有权记录](/Users/frigidcrow/Dev/neko-companion/plugin/server/application/plugins/installation_transactions/ownership.py)。

**目标边界：先有限工具集合，再评估兼容插件宿主。** 图决定工具调用；工具执行器负责参数、超时、结果封装、授权与幂等记录。旧任务执行器和完整 Agent 会话仅作行为参考，不再启动第二个自主规划循环。可以逐个提取纯工具或 MCP 适配逻辑，但 N.E.K.O 插件还依赖 SDK、宿主、总线、配置和作业协议，不能仅复制插件文件就宣称兼容全部插件。

M1 验证未知工具、错误参数、超时/取消、结果关联、来源证据和只读网页网络边界。插件市场、动态安装和完整管理中心仍是后续可选项；浏览器点击、键鼠/电脑控制按用户当前需求排除。副作用重试和插件退出等教材内容仅留作参考，不因此引入首版执行框架。`bus.memory` 只能提供短期消息，长期事实统一走 Memory Service。

## R08 互动、娱乐、生活工具与跨渠道

教程：L15、L16、L21、L22。

源码：[桌宠工具资料](/Users/frigidcrow/Dev/neko-companion/frontend/react-neko-chat/src/avatar-tools/catalog.ts)、[工具解释器](/Users/frigidcrow/Dev/neko-companion/frontend/react-neko-chat/src/avatar-tools/profileInterpreter.ts)、[音乐播放](/Users/frigidcrow/Dev/neko-companion/main_logic/music_playback.py)、[陪看引擎](/Users/frigidcrow/Dev/neko-companion/main_logic/watch_together/engine.py)、[提醒插件](/Users/frigidcrow/Dev/neko-companion/plugin/plugins/memo_reminder/__init__.py)、[搜索插件](/Users/frigidcrow/Dev/neko-companion/plugin/plugins/web_search/__init__.py)、[QQ 回复管线](/Users/frigidcrow/Dev/neko-companion/plugin/plugins/qq_auto_reply/reply_pipeline.py)、[QQ 记忆桥接](/Users/frigidcrow/Dev/neko-companion/plugin/plugins/qq_auto_reply/memory_bridge.py)。

**目标边界：搜索能力进入 M1 首版必需，其余选择性扩展。** 评估提取搜索插件的请求/响应适配，解除 SDK/宿主/配置耦合，并补网页正文、版本条件和引用契约；不能把搜索摘要直接当完整攻略。是否迁入源码仍须按来源清单逐项验收。音乐、陪看、提醒和渠道回复目前不属于必需移植项，分别涉及平台协议、媒体时钟、外部账号与投递结果；以后接入 QQ 等渠道也须进入同一个图，不复制完整回复管线。

验证生成成功与实际投递分离、未投递草稿不污染长期记忆、提醒任务恢复和重复触发、媒体结束与暂停行为。跨渠道主体隔离需由 Memory Service 和渠道身份适配共同定义，不能仅靠显示名称识别用户。

## R09 数据、配置、凭据与同步

教程：L23。

源码：[数据根解析](/Users/frigidcrow/Dev/neko-companion/utils/config_manager/storage_roots.py)、[存储布局](/Users/frigidcrow/Dev/neko-companion/utils/storage/layout.py)、[位置发现](/Users/frigidcrow/Dev/neko-companion/utils/storage/location_bootstrap.py)、[端口配置](/Users/frigidcrow/Dev/neko-companion/config/network.py)、[云存档边界](/Users/frigidcrow/Dev/neko-companion/utils/cloudsave_runtime/_shared.py)。

**目标边界：借鉴隔离规则，重写本工程启动配置。** 新工程采用独立应用身份、配置、数据根、日志、端口发现和备份目录；源项目的数据迁移／发现分支不能原样启用。LangGraph checkpoint 与 Memory Service 可以处于同一个独立应用数据目录，但分别定义结构、保留与恢复规则。不得读取参考仓库的运行资料、模型 Key、`.venv` 或既有端口配置。

验证与原版同时安装和运行时无资料串用、模型 Key 的显示／日志保护、路径覆盖规则和升级后记忆保留。云模型调用与云端记忆同步是两件事；首版默认不启用自动记忆同步，完整备份工具按计划另定范围。

## R10 Windows 启动、桌面宿主与打包

教程：L24。

源码：[开发启动器](/Users/frigidcrow/Dev/neko-companion/launcher.py)、[进程拓扑](/Users/frigidcrow/Dev/neko-companion/launcher_core/runtime.py)、[单实例机制](/Users/frigidcrow/Dev/neko-companion/utils/single_instance.py)、[父进程退出保护](/Users/frigidcrow/Dev/neko-companion/utils/parent_guard.py)、[端口工具](/Users/frigidcrow/Dev/neko-companion/utils/port_utils.py)、[前端构建入口](/Users/frigidcrow/Dev/neko-companion/build_frontend.bat)、[桌面 CI](/Users/frigidcrow/Dev/neko-companion/.github/workflows/build-desktop.yml)、[桌面发布脚本](/Users/frigidcrow/Dev/neko-companion/scripts/build-desktop-release.ps1)、[后端打包规格](/Users/frigidcrow/Dev/neko-companion/specs/launcher.spec)。

**目标边界：提取运行保障，建立本工程构建流程；外部 Electron 待核实。** 主仓库 CI 另行检出 `Project-N-E-K-O/N.E.K.O.-PC` 才生成完整桌面包。当前只确认主仓库中的集成入口，没有在本工程获得或验证外部源码、许可证、版本和桥接；清单中将它列为未解析外部依赖，不伪造本地文件哈希。访问失败的历史记录也不能证明现在必然不可用。

本工程 PLAN 已记录 2026-09-25 当前账号查询无法解析该仓库；本参考映射没有重复网络查询，这个结果不等于已证明仓库不存在或永久不可获得。先核实是否可以复用桌面宿主；若不能，补本工程需要的窗口、托盘和后端生命周期能力，同时继续提取已核实的 Web UI 等组件。单独 Python exe 不等于完整桌面 App。原版开发启动方式、主仓库构建脚本均不是新工程已可运行的命令，也不能直接继承旧应用身份、发布凭据或更新频道。

Windows 11 x64 验收需覆盖解压双击启动、无需另装开发工具、窗口／托盘／角色、音频设备、中文路径、端口占用、退出清理和升级保留记忆，并记录新工程 commit、外部宿主 commit（如复用）、锁定依赖、构建环境与产物摘要。Mac 文档或实验检查不能替代这些证据。

## 完整 24 课索引

下表每课只指定一个主参考组；交叉使用不表示要重复实现。答案、实验说明、实验代码及独立复核记录的路径也保存在清单的 `courses` 中，均为参考工程原文件。教程的 `reviewed` 是材料审阅状态，不是本工程功能验收，更不是用户已经掌握。

| 参考组 | 课程入口 | 在新工程中的用途 |
| --- | --- | --- |
| R01 | [L01 模型与配置](/Users/frigidcrow/Dev/neko-companion/docs/learning/lessons/L01-model-architecture.md) | 定义模型端口、配置和协议边界 |
| R02 | [L02 流式会话](/Users/frigidcrow/Dev/neko-companion/docs/learning/lessons/L02-streaming-sessions.md) | 会话归属、取消和输出收口 |
| R01 | [L03 工具与上下文](/Users/frigidcrow/Dev/neko-companion/docs/learning/lessons/L03-tools-context.md) | 图节点、工具结果与上下文协议 |
| R05 | [L04 音频输入](/Users/frigidcrow/Dev/neko-companion/docs/learning/lessons/L04-audio-input.md) | 采集、ASR 与轮次归属 |
| R05 | [L05 语音身份](/Users/frigidcrow/Dev/neko-companion/docs/learning/lessons/L05-voice-identity.md) | 后续身份与激活策略参考 |
| R05 | [L06 语音输出](/Users/frigidcrow/Dev/neko-companion/docs/learning/lessons/L06-speech-output.md) | 分段 TTS、播放、即时停止 |
| R06 | [L07 视觉输入](/Users/frigidcrow/Dev/neko-companion/docs/learning/lessons/L07-vision-input.md) | 图片转化与输入生命周期 |
| R06 | [L08 主动陪伴](/Users/frigidcrow/Dev/neko-companion/docs/learning/lessons/L08-proactive-companion.md) | 后续主动事件、节奏和让行 |
| R03 | [L09 记忆写入](/Users/frigidcrow/Dev/neko-companion/docs/learning/lessons/L09-memory-persistence.md) | 原始记录、提取、持久任务 |
| R03 | [L10 混合召回](/Users/frigidcrow/Dev/neko-companion/docs/learning/lessons/L10-hybrid-retrieval.md) | 关键词／向量、时间和主体过滤 |
| R03 | [L11 记忆生命周期](/Users/frigidcrow/Dev/neko-companion/docs/learning/lessons/L11-memory-lifecycle.md) | 纠错、遗忘；反思／人格按需选入 |
| R04 | [L12 角色资料](/Users/frigidcrow/Dev/neko-companion/docs/learning/lessons/L12-character-cards.md) | 基础角色配置，后续卡片导入 |
| R04 | [L13 前端状态](/Users/frigidcrow/Dev/neko-companion/docs/learning/lessons/L13-frontend-state.md) | 消息展示、连接与多窗口状态 |
| R04 | [L14 角色渲染](/Users/frigidcrow/Dev/neko-companion/docs/learning/lessons/L14-avatar-rendering.md) | 选择一种角色格式提取验证 |
| R08 | [L15 互动小游戏](/Users/frigidcrow/Dev/neko-companion/docs/learning/lessons/L15-interactions-games.md) | 可选规则、动作与演出 |
| R08 | [L16 音乐与陪看](/Users/frigidcrow/Dev/neko-companion/docs/learning/lessons/L16-music-watch-together.md) | 可选媒体及外部平台适配 |
| R07 | [L17 Agent 执行](/Users/frigidcrow/Dev/neko-companion/docs/learning/lessons/L17-agent-execution.md) | 提取执行约束，编排归新图 |
| R07 | [L18 插件生命周期](/Users/frigidcrow/Dev/neko-companion/docs/learning/lessons/L18-plugin-lifecycle.md) | 工具宿主和退出资源管理参考 |
| R07 | [L19 模型网关与 MCP](/Users/frigidcrow/Dev/neko-companion/docs/learning/lessons/L19-model-gateway-mcp.md) | 后续工具连接和协议适配 |
| R07 | [L20 插件管理](/Users/frigidcrow/Dev/neko-companion/docs/learning/lessons/L20-plugin-management.md) | 可选安装／管理能力 |
| R08 | [L21 生活工具](/Users/frigidcrow/Dev/neko-companion/docs/learning/lessons/L21-life-tools.md) | 按需提取单个工具 |
| R08 | [L22 跨渠道](/Users/frigidcrow/Dev/neko-companion/docs/learning/lessons/L22-cross-channel.md) | 后续渠道适配与主体隔离 |
| R09 | [L23 数据与同步](/Users/frigidcrow/Dev/neko-companion/docs/learning/lessons/L23-data-credentials-sync.md) | 首次启动前的数据、身份和端口隔离 |
| R10 | [L24 运行与交付](/Users/frigidcrow/Dev/neko-companion/docs/learning/lessons/L24-runtime-delivery.md) | Windows 宿主、构建和真机验收 |

总入口：[课程说明](/Users/frigidcrow/Dev/neko-companion/docs/learning/README.md)、[课程登记](/Users/frigidcrow/Dev/neko-companion/docs/learning/lesson-materials.json)、[原理资料来源](/Users/frigidcrow/Dev/neko-companion/docs/learning/AI-RESOURCES.md)、[全功能地图](/Users/frigidcrow/Dev/neko-companion/docs/PROJECT-FUNCTIONS.md)。以上是 N.E.K.O 的教材，不是新项目 LangGraph 接口的版本化文档；图 API、checkpoint 和模型适配器版本由本工程另行固定。

## 来源与以后实际提取时的记录

许可参考：[根 LICENSE](/Users/frigidcrow/Dev/neko-companion/LICENSE)、[NOTICE](/Users/frigidcrow/Dev/neko-companion/NOTICE)、[前端第三方声明](/Users/frigidcrow/Dev/neko-companion/static/libs/THIRD_PARTY_NOTICES.md)、[ASR 端点模型声明](/Users/frigidcrow/Dev/neko-companion/main_logic/asr_client/endpointing/models/THIRD_PARTY_NOTICES.md)、[声纹模型声明](/Users/frigidcrow/Dev/neko-companion/main_logic/asr_client/speaker_shadow/models/THIRD_PARTY_NOTICES.md)。这些只是审查入口，尚未形成逐模块、素材和外部宿主的复用许可结论。

每次真正提取组件时，在本工程记录：来源仓库与 commit、原路径、文件指纹、所需许可／声明、目标路径、保留及改写的接口、测试对照、升级合并方式。提取前先读取依赖和初始化副作用；先用合成资料验证组件，再连接真实模型或设备。涉及长期记忆时同时验证持久化结果与回复召回，不能只检查文件出现。

清单的 SHA256 基于当次原文件字节；`matchesBaseline` 只比较与固定源码 commit 的原始字节，`workingTreeState` 和 `gitStatus` 另记 Git 状态。`build_frontend.bat` 由仓库属性检出为 CRLF，Git 工作树干净，但字节不同于提交中的 LF；清单以 `baselineByteRelation: checkout-crlf-only` 区分。教程、功能地图或其他未提交文件使用工作树指纹，不冒充上游已发布版本；以后工作树变化需重算并复核。清单只登记被这份地图或课程登记直接引用的文件，不构成完整依赖树、许可证审计或可运行移植包。
