# M0 来源、复用边界与后续工具链核查

核查日期：2026-09-25。状态：**静态来源核查完成；实际提取、前端集成、音频设备和 Windows 桌面验证未执行**。本文件对应 PLAN 的 M0，不把原工程功能或依赖元数据视为 ai-neko 的运行证据。

本轮只读参考 `/Users/frigidcrow/Dev/neko-companion` 的选定源码、构建声明和许可证。其 HEAD 为 `90ccf79c95e80f899b9bf3395fa8cd9a9bfe29be`，上游为 `https://github.com/Project-N-E-K-O/N.E.K.O`。逐文件当前字节 SHA256、与 HEAD 比较、引用行及命令结果见 [m0-reuse-manifest.json](m0-reuse-manifest.json)。所有命令从 ai-neko 根目录执行；没有导入参考工程的 Python 模块、加载其 JavaScript、读取其配置/数据/凭据、使用其虚拟环境或启动其服务。

**本轮第三方运行代码和素材导入数量为 0，manifest 的 `imports` 明确为空。** 下文接口是后续提取要求，不是已经实现的适配器。

## 1. 四类组件处置

| 组件 | 取得与许可证据 | 当前处置 | 后续落地阶段 |
| --- | --- | --- | --- |
| 桌面窗口/托盘 | 主仓库 CI 另检出 `Project-N-E-K-O/N.E.K.O.-PC`；本轮账号查询无法解析该仓库，未取得其代码、版本或许可 | **采用 ai-neko 自有最小 Electron 宿主**；只承担窗口、托盘、启动/停止本工程后端、有限 preload 桥接。以后取得外部壳且许可核实后再比较，不阻塞其他组件提取 | M3 实现、M6 分发 |
| 音频 | ASR/TTS Python worker、浏览器采集/播放源码已取得；主代码 Apache-2.0；额外模型和解码器有独立义务 | 选一种 ASR/TTS 路径提取；必须注入配置和回调，拆离全量供应商注册、旧会话和遥测。M0 不安装音频模型或测试设备 | M4 |
| 渲染与聊天 UI | React 挂载/消息协议、Live2D/VRM/MMD/PNGTuber 包装源码可读；SDK、字体和角色资源不由根许可证全部覆盖 | 优先提取 React 展示组件；角色路径待 SDK 与单一测试素材核清后选择。Live2D 保留首选候选；未核清前不承诺打包其 Core 或现有角色 | M3 |
| 长期记忆 | recent/facts/timeindex/hybrid_recall/outbox 源码可读、主代码 Apache-2.0；依赖原配置、日志、云存档和服务运行态 | 提取所需算法和存储实现进入唯一 Memory Service；重写依赖注入和 ai-neko 的范围绑定，不直接启动旧 memory_server | M2 |

桌面证据：`.github/workflows/build-desktop.yml:933–939` 明确另行 checkout 并使用仓库访问令牌；`scripts/build-desktop-release.ps1:171–179` 要求独立 Electron 目录、后端产物及 `package.json`。实际命令 `gh repo view Project-N-E-K-O/N.E.K.O.-PC --json nameWithOwner,url,visibility,licenseInfo,defaultBranchRef` 退出码 1，返回 `Could not resolve to a Repository`。此结果仅说明当前账号无法确认访问，不能证明仓库永远不存在或不可取得。

## 2. 静态导入与启动副作用

以下行号属于固定参考 HEAD，文件字节身份在 manifest 中。采用文本阅读和 Python AST 解析；**没有运行原模块，不能声称原模块导入无副作用**。区分“加载即发生”和“创建对象/调用方法后发生”。

| 源文件与行 | 观察结果 | 提取要求 |
| --- | --- | --- |
| `memory/recent.py:15–48,330–337,351–362` | 模块加载调用 `setup_logging`；构造器读取全局配置和角色数据路径；引用云存档维护状态与原 LLM 工厂 | 禁止原样 import；改为传入本项目 storage、logger、ModelAdapter、clock、scope |
| `utils/logger_config.py:438–450,533–560,854–874` | `setup_logging` 建立日志配置/文件 handler；`get_module_logger` 本身只取命名 logger，不能把两者混同 | 所有初始化放显式启动入口，导入阶段只定义类型/函数 |
| `memory/facts.py:36–86,339–340` | 依赖原 config、时间索引、语言和云存档；构造 FactStore 获取全局配置 | 注入本项目路径、范围、模型和删除版本控制；不读取原 facts |
| `memory/timeindex.py:15–33,455–510` | 构造器保留锁/引擎状态；数据库懒初始化；可写连接路径函数会 mkdir，路径仍来自原 ConfigManager | 保留懒初始化/关闭约束，但仅接受 ai-neko 显式 scoped path；执行前校验 user/character |
| `memory/hybrid_recall.py:98–112,385,676` | 模块级缓存、原 config/logger 依赖；不是无状态检索函数 | 缓存键必须包含本项目主体、角色、memory_revision；按范围过滤后再排序 |
| `memory/outbox.py:52–56,79–126` | 构造时读取全局配置；访问 outbox 路径会创建角色目录；写入 NDJSON 后 flush/fsync | 保留稳定 job_id 思路；不得声称它与 JSON/SQLite 跨文件原子提交；统一 Memory Service 任务入口 |
| `app/memory_server/runtime.py:41–79`、`routes.py:68–72,3730–3733`、`post_turn.py:574` | 服务运行态创建 FastAPI app；路由和后处理在加载时注册全局 handler；runtime 引入整个 memory 包与云存档 bootstrap | 不提取旧服务入口；仅由本项目服务创建/销毁组件 |
| `memory/embeddings.py:71–84,942–980` | 门面依赖其内部 lifecycle/hardware/profiles/schema；嵌入服务仍是独立组件，而非把文件复制即可工作的纯函数 | M2 先确定实际召回需求，再显式初始化可选 embedding；模型权重、加载时资源与 Windows 支持另验 |
| `main_logic/asr_client/__init__.py:27–58`、`workers/openai.py:19–34` | 入口一次导入多个供应商；具体 OpenAI worker 依赖 numpy/soxr/websockets 与原 session/transport 契约 | 提取一个 worker 及必要契约；音频格式、任务归属、结束/取消结果显式适配 |
| `main_logic/tts_client/__init__.py:32–143,450–626` | 入口导入全量 worker，模块加载时向共享 provider registry 注册多个供应商 | 不沿用公共入口；将单一 worker 的配置、凭据、队列和错误回调作为参数 |
| `main_logic/core/tts_runtime.py:39–63,794–800,1118,1245–1252` | 依赖旧 omni/core facade，运行方法启动线程和异步响应任务 | 只提取媒体管理规则；由 Runtime 发 `start/cancel/dispose`，不嵌入旧对话中心 |
| `static/audio-processor.js:190` | 脚本加载即注册 AudioWorklet processor | 仅由本工程授权采集流程显式加载；采样/重采样输出以合成音频验证 |
| `static/app/app-audio-playback.js:12–19,325–355,2106–2127` | IIFE 立即取 `window.appState/appConst`、注册 storage/轮次监听并设置全局导出；含旧 `neko_selected_speaker` 键 | 改为实例化播放器，独立 `ai-neko` 存储键/事件；dispose 移除监听，cancel 同时停声并丢弃旧轮片段 |
| `static/live2d/live2d-core.js:16–35,5606–5628` | 加载即要求 PIXI/Live2D，全局赋值并注册帧率/画质事件 | 通过明确 renderer instance 和 ResourceResolver 接入；所有监听和 GPU/模型资源可释放 |
| `frontend/react-neko-chat/src/mount.tsx:1–9,22–39`、`src/styles.css:1–8` | 导入组件/CSS并建立 WeakMap，只有 `mount` 才 createRoot；CSS 引用 KaTeX 与原站字体绝对路径 | 提取显示和 schema，改资源路径/事件 props；不要继承原全局桥。传递依赖的副作用尚需提取后测量 |

ASR/TTS 原入口的依赖树未穷尽。上述事实足以排除“直接在 ai-neko import 原模块”方案；不能据此声称所有风险已经消除。M2/M3/M4 提取时须做独立进程导入探针、文件/网络/线程观察和必要的合成集成测试。

## 3. 提取接口与验收输入

沿用 [ARCHITECTURE 第 3 节](ARCHITECTURE.md)，不建立第二套主协议：

- **Memory Service**：`retrieve(scope, query, time_range, budget)`、`commit_turn(turn_id, source_messages, delivery_state)`、`correct/forget/inspect`。服务实例/请求上下文绑定 `(user_id, character_id)`；存储、模型、时钟、锁和任务调度由本项目提供。返回来源、有效状态及 memory_revision；M2 验证写入后重启召回、纠正/遗忘与旧 checkpoint 防回写。
- **Media**：Runtime 的 `start/cancel(turn_id)/dispose`；ASR 返回带轮次的文本/结束/失败，TTS 输出带采样格式与序号的音频片段。播放回执沿公共事件信封返回；生成完成与实际播放完成分别处理。M4 验证停声、旧片段/回调拒收、设备释放和实际 Windows 试听。
- **UI / Renderer**：公共事件信封驱动消息和角色表现；内部 renderer adapter 提供 `load(resource)/emotion/mouth/dispose`，资源只来自明确授权的本项目路径。M3 验证中文路径、加载失败、重连、窗口销毁和模型释放。
- **Desktop**：主进程拥有本项目后端子进程和 runtime 文件；preload 只暴露需要的方法。应用身份、userData、端口发现、令牌、日志与退出清理均使用本项目定义；M3/M6 验证窗口/托盘退出和原版共存。

真正导入时，manifest 每条 `imports` 必须新增：来源 commit/path/hash、目标 path、许可/NOTICE、具体改动、对照测试、升级方式。升级以“固定来源版本 + 本项目适配补丁”比较，不把新上游整仓覆盖进项目。

## 4. 代码许可与素材边界

- 主仓库根 `LICENSE` 是 Apache-2.0，`NOTICE` 标明团队与作者；所查 Python 模块有 Apache 标头。后续导入主代码保留原标头、完整 LICENSE/NOTICE，并标明修改；当前未复制生产代码，所以未为尚未取得的模块虚构许可文件。
- `static/libs/THIRD_PARTY_NOTICES.md` 只覆盖列出的 MMD bundle，明确不是整个 libs 目录清单。three-mmd 和所含 babylon-mmd 为 MIT；physics bundle 是本地修改版本，原始上游精确发布版未建立。后续用它需保留声明、许可证及本地补丁来源，不能简单标为未修改 beta.3。
- ASR endpointing 声明列 Silero VAD v6.2.1（MIT）与 Smart Turn v3.2（BSD 2-Clause 文本）；speaker_shadow 声明 CAM++ ONNX（Apache-2.0），权重不在 Git 仓库。只核查声明，未下载、复核权重或执行模型。
- Yozai 字体有 OFL-1.1 文件与保留字体名；它是独立资源。React 组件 CSS 中硬编码其旧资源路径；初期提取可改系统字体，实际分发字体前登记对应字节与许可。
- Live2D wrapper 的根代码许可不能替代 Cubism Core/SDK 与角色模型许可。官方 [SDK 发布许可说明](https://www.live2d.com/en/sdk/license/) 单列 AI/Chatbot 和 Expandable Application 判断。当前 SDK 二进制来源/版本与角色模型授权尚未逐项核实，**Live2D 资源分发为 Pending**；也未认定本项目当然属于免费豁免。VRM/MMD/PNGTuber 角色、贴图、动作和音色同样没有获得批量复制结论。

## 5. 后续桌面/前端的精确版本建议

以下仅为 **2026-09-25 选定的候选组合**，M0 未安装 Node/Electron/前端包，没有 `npm ci`、构建、渲染、打包或 Windows 兼容性通过记录。Python 实际依赖由本项目 `uv.lock` 记录，与本表状态分开。进入 M3 时再复核安全修订，生成本工程 package-lock 后实测，不把本表当已生成锁文件。

| 工具/包 | 选定版本 | 依据 |
| --- | --- | --- |
| Node.js | `24.21.0` LTS | [官方版本索引](https://nodejs.org/dist/index.json) 的 LTS=Krypton，含 win-x64；[发布页](https://nodejs.org/en/blog/release/v24.21.0)确认版本 |
| npm | `11.19.0` | 同一 Node 官方索引标记的 bundled npm |
| Electron | `44.4.5` | [官方 releases.json](https://releases.electronjs.org/releases.json)列出内嵌 Node `24.21.0`、Chromium `152.0.7977.130`、win32-x64；[包元数据](https://registry.npmjs.org/electron/44.4.5)要求 Node >=22.12.0 |
| Vite | `8.3.1` | [发布包元数据](https://registry.npmjs.org/vite/8.3.1)：Node `^20.19.0` 或 `>=22.12.0` |
| @vitejs/plugin-react | `6.1.1` | [发布包元数据](https://registry.npmjs.org/@vitejs/plugin-react/6.1.1)：同上 Node，peer Vite `^8.0.0` |
| React / react-dom | `18.3.1` / `18.3.1` | 保持所查聊天组件 lock 中的版本，避免提取同时切 React 主版本；[react-dom 元数据](https://registry.npmjs.org/react-dom/18.3.1) peer React `^18.3.1` |
| TypeScript | `5.9.3` | 保持参考 lock 中编译器，发布包要求 Node >=14.17；[版本元数据](https://registry.npmjs.org/typescript/5.9.3) |
| @types/react / @types/react-dom | `18.3.12` / `18.3.1` | 与所选 React 主版本一致的初始候选；实际安装后再验证类型检查 |

[Electron 官方 prerequisites](https://www.electronjs.org/docs/latest/tutorial/tutorial-prerequisites)建议开发机使用 LTS，并明确运行时使用 Electron 自带 Node；两者不共享二进制 ABI。上述 engines/peer 范围满足只表示元数据层面可选，不能代替安装/构建验证。当前参考前端 lock 是 Vite `5.4.21`、plugin-react `4.7.0`、TS `5.9.3`、React `18.3.1`；候选 Vite 8 的配置迁移尚待 M3。没有盲目把原 lock 复制过来，也没有承诺原渲染 SDK 与 Electron 44 已兼容。

## 6. Windows 凭据适配器决策

M1 选 `keyring==25.7.0` 的标准 `keyring.backends.Windows.WinVaultKeyring`，通过 ai-neko 的 CredentialStore 接口调用。[官方文档](https://keyring.readthedocs.io/en/stable/)提供按 service/username 的 get/set/delete；[v25.7.0 Windows 实现](https://github.com/jaraco/keyring/blob/v25.7.0/keyring/backends/Windows.py)使用 Win32 Generic Credential 的 CredRead/CredWrite/CredDelete。PyPI 元数据要求 Python >=3.9，与本项目 Python 3.11 范围相符；实际依赖锁定、Windows 打包和系统调用测试留 M1。

建议 service 为 `ai-neko/model/<profile_id>`，username 固定 `api_key`，profile_id 为本项目生成并校验的稳定配置 ID；每配置独立 service，删除只处理同一命名空间。显式选择 Windows backend，初始化失败就返回不可用，禁止悄悄退到明文文件或扫描通用 OpenAI/NEKO 凭据。该 backend 默认 `CRED_PERSIST_ENTERPRISE`，M1 显式设 `persist = 'local machine'`，避免以默认 enterprise 行为冒充严格本机保存；仍需 Windows 真机验证。

M0 未安装/调用 keyring，未查询任何系统凭据。M0 模型 Key 只来自用户显式设置的 `AI_NEKO_MODEL_API_KEY`；不读取其他环境名或参考工程配置。

## 7. 本轮命令与剩余证据

实际从 `/Users/frigidcrow/Dev/ai-neko` 执行：

1. `git -C /Users/frigidcrow/Dev/neko-companion rev-parse HEAD`、`remote -v`，确认来源。
2. 上述 `gh repo view ... --json ...`，记录退出 1 的访问边界。
3. `rg --files` 枚举 LICENSE/NOTICE/package.json；`rg -n`、`sed -n`、Python 标准库文本/AST 读取指定源码。未使用 importlib、exec 或参考 `.venv`。
4. Python 标准库 `urllib.request` 读取 Node 官方 index、Electron 官方 releases.json、指定 npm 发布版本与 PyPI keyring JSON，只获取公开元数据；`hashlib.sha256` 与 `git show <HEAD>:<path>` 比较选定文件。
5. `json.loads` 验证 manifest；逐文件 source SHA256 可重算。没有复制第三方运行文件。

需要后续完成：真实模块提取后无初始化越界的探针；所选渲染 SDK/角色逐项许可；前端 lock 与构建；Windows Credential Manager；Windows 桌面/托盘/音频/打包。它们分别保持 Pending，不以本审计通过替代功能验收。
