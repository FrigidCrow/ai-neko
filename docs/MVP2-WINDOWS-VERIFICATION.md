# MVP2 Windows 构建与下载核对

日期：2026-10-04（本地；CI 与下载核对为 2026-10-03 UTC）。**包含免费本地中文语音的 Windows 开发包已通过源码与实际包门禁、真实语音模型测试及独立下载核对。** Windows 11 游戏、用户麦克风/音色、真实聊天模型建议质量和完整端到端性能仍待验收，完整 MVP2 未完成。

[下载最新 Windows 开发包](https://github.com/FrigidCrow/ai-neko/actions/runs/37135192000/artifacts/11278787928)，解开 Actions 产物容器，再完整解压其中的 `ai-neko-0.4.0-dev.b443768df012-windows-x64.zip`，双击根目录 `ai-neko.exe`。通常需登录 GitHub，产物到期时间为 **2026-10-17 16:14:17 UTC**。包包含 Electron、Python、YUI 及主应用运行依赖；免费语音首次点击下载后可用，无需语音 Key，聊天模型单独配置。精确入口与步骤见[免费语音说明](FREE-VOICE.md)。本轮为分支开发包，没有创建新 tag 或 Release。

## 当前包来源与下载完整性

- 构建源码：`b443768df012fa4c88036f2fc13321b1e79d1d84`，分支 `codex/companion-five-capabilities`，构建时工作区干净；版本 `0.4.0-dev.b443768df012`。
- [CI 37135192000](https://github.com/FrigidCrow/ai-neko/actions/runs/37135192000)：Linux、Windows 源码测试及 Windows 包三个必需 job 全部 success；tagged 发布按设计 skipped。
- 应用 ZIP 大小 **192,176,491 字节**，SHA-256 **`f5035842285b15c4009485556a56d38862ce352588ffa31a0bf5caeb8776fb91`**。GitHub 外层产物容器大小与此 ZIP 不同。
- 独立下载验证核对 ZIP CRC、内外构建信息、226 份源码输入、两个主应用依赖锁、AMD64 桌面与后端程序摘要、66 份许可文件、79 份素材、17 张截图及同一 ZIP 的五份运行报告。普通文本按 Windows CRLF 检出重建比较；冻结资料保留原始字节。
- [机器核对报告](evidence/mvp2/free-voice/windows/windows-download-verification.json)、[构建信息](evidence/mvp2/free-voice/windows/build-info.json)、[校验清单](evidence/mvp2/free-voice/windows/SHA256SUMS.txt)和[核对脚本副本](evidence/mvp2/free-voice/windows/verify_windows_download.py)已归档。Mac 仅核对下载文件，Windows 程序的实际执行证据来自 CI。后续文档提交不改变包的源码身份。

## 当前包实际通过的门禁

| 范围 | Windows Server 2022 x64 | Linux / 说明 |
| --- | --- | --- |
| Python 完整回归 | 1,673 通过、0 跳过；473.937 秒 | 1,672 通过、1 个 Windows 凭据专属跳过；105.268 秒；PARTIAL、ci_gate=PASS |
| 桌面宿主单测 | 106 通过 | 106 通过 |
| 冻结 30 题 | 24/24 top3 命中，6/6 缺口识别 | 相同；错误游戏/已知冲突版本/禁止来源为 0 |
| 200 篇本地检索 | 5 次预热、100 样本，p95 66.5625ms | p95 12.566144ms；固定门槛 150ms，均 0 网络与 0 检索断言失败 |
| 原生语音导入前置检查 | PASS；0.391 秒自然退出 | [导入报告](evidence/mvp2/free-voice/windows/Windows-voice-import-smoke.json)；18 个占位文件，不加载真实模型 |
| 实际解压后端 | 16/16 | [包报告](evidence/mvp2/free-voice/windows/package-smoke.json) |
| 实际桌宠与流式聊天 | 9/9 | [桌面报告](evidence/mvp2/free-voice/windows/desktop-smoke.json) |
| 人格/记忆/视觉/语音与免费设置 | 21/21 | [陪伴报告](evidence/mvp2/free-voice/windows/companion-smoke.json) |
| 攻略持久化与连续对局联合验收 | 8/8 | [G6 报告](evidence/mvp2/free-voice/windows/g6-acceptance.json)；两个不同进程，25 个记录问题完成且 0 搜索/取页 |
| 真实免费本地语音 | 11/11 | [语音报告](evidence/mvp2/free-voice/windows/free-voice-smoke.json)；实际下载模型、TTS/ASR、重启复用 |

冻结题的 [Windows 评测](evidence/mvp2/free-voice/windows/Windows-guide-evaluation.json)、[Linux 评测](evidence/mvp2/free-voice/windows/Linux-guide-evaluation.json)及检索的 [Windows 基准](evidence/mvp2/free-voice/windows/Windows-guide-benchmark.json)、[Linux 基准](evidence/mvp2/free-voice/windows/Linux-guide-benchmark.json)分别保留。检索基准不含建库、网络、模型或语音耗时，不能代表完整问答速度。

原有四套包报告使用合成服务与测试窗口/麦克风。G6 记录 36 次合成模型 HTTP 请求，核对攻略原文复用和上下文隔离；陪伴报告记录 18 次合成模型、3 次合成 ASR、29 次合成 TTS。20 次 WebAudio 停止采样的 p95 约 0.20ms，仅测点击到 `AudioBufferSourceNode.stop` 返回，不是硬件静音或录音到回复延迟。新增免费语音 11 项独立使用真实本地模型，没有将合成语音服务计为真实推理。

## 真实免费语音证据

同一 ZIP 解压后的后端在包含中文和空格的程序/数据目录中运行，从空白专用数据根安装固定版本运行组件与模型，约 **37.5 秒**后 ready。实际读取私有 `dist-info/METADATA` 的版本与专用锁一致：**NumPy 2.4.4、sherpa-onnx 1.13.8、sherpa-onnx-core 1.13.8**；报告保留 uv 及两个模型下载归档的大小和 SHA-256、worker 与模型清单摘要，不仅记录锁文件声明。识别模型为 SenseVoice INT8 2024-07-17，朗读模型为 Kokoro INT8 multi-lang v1.1。

原生前置检查在独立私有环境中执行 NumPy/Sherpa 导入和完整 worker 协议，stdin 管道保持打开，0.391 秒内自然退出、退出码 0。5 个固定阶段全部到达；18 个各 1 字节占位资源仅验证导入路径，不能作为模型可用证据。真正的模型推理由后续实际包测试完成。

| 实测项目 | 结果 |
| --- | --- |
| 固定 TTS 输入 | `你好，我是小猫。今天我们一起学习，让生活更有趣。`（含标点 24 字符） |
| 合成耗时与音频 | 17.297 秒；6.6095 秒 WAV，单声道 24 kHz、16 bit PCM，158,627 帧、317,298 字节 |
| ASR 输入与耗时 | 转换为单声道 16 kHz、16 bit PCM WAV；2.219 秒 |
| 原始识别文本 | `你好，，我是小猫。今天我们一起学习，让生活更有趣。` |
| 识别差异 | 去标点等归一化后 20 字符、编辑距离 0、CER 0；原文多一个逗号，并非逐字一致 |
| 重启复用 | 后端 PID 3468 → 1112；资源 ready，无需重新下载 |
| 用户与费用边界 | 用户麦克风采集 0、付费 API 调用 0、聊天模型调用 0；未测扬声器播放 |

[实际中文女声 WAV](evidence/mvp2/free-voice/windows/free-voice-smoke-speech.wav)的 SHA-256 为 `93b6b316c951e1ad248bb4aa0097806df4f47f0ef690340492789bb741eec591`。输入为固定测试文本和模型生成音频，不是用户录音。一次固定句成功不代表游戏术语、噪声或任意中文的识别质量；上述耗时来自 CI 机器。初版每次请求独立启动进程并加载模型，第一次和后续请求均有此开销，不能承诺即时语音。

Root 实际查看[免费语音设置](evidence/mvp2/free-voice/windows/companion-smoke-free-voice.png)与[重启后新聊天](evidence/mvp2/free-voice/windows/g6-acceptance-A01-new-process-new-chat.png)：免费模式、下载按钮和麦克风控件可见；重启后采用的攻略与本地原文来源保留，YUI 与控件同屏显示正常。[目视记录](evidence/mvp2/free-voice/windows/visual-review.json)仅覆盖这两张 CI 截图，不代替其他 DPI、用户麦克风或真实游戏验收。

## 当前仍需完成

- 免费语音已具备真实 Windows 包推理证据；仍需用户麦克风、噪声/游戏术语、扬声器音色和硬件真正静音测量。按键语音与朗读步骤见[免费语音说明](FREE-VOICE.md)。
- 聊天模型单独配置；真实模型建议质量、三次完整带来源问答、20 对冷暖问题及用量/费用仍待记录。免费语音不等于对话模型已配置或免费。
- Windows 11 x64 真实游戏至少十轮、切换/新局/重启，以及 20 次跨生成/合成/播放停止仍按[真实验收步骤](MVP2-LIVE-ACCEPTANCE.md)留证。Windows Server 2022 CI、Mac 实测和拟议的 Windows 11 ARM 虚拟机均不替代原生 x64 游戏/驱动/硬件结果。

旧开发包来源、失败记录及当时的 Pending 项完整保留如下；本次免费语音实现与 CI 排错记录见 [WORKLOG](../WORKLOG.md) 和 [REVIEW](../REVIEW.md)。

## 历史：50227627e8d4 攻略与免费搜索开发包

以下为 2026-09-30 原始报告，仅调整标题层级。其语音配置、VM 磁盘情况和待验状态对应当时版本；当前包以以上记录为准，旧包及失败记录不覆盖。

日期：2026-09-30。**MVP2 G1–G6及免费搜索的Windows开发包已通过合成CI与实际下载核对；真实模型/语音、Windows11游戏与硬件验收仍Pending，完整MVP2未完成。**

[下载Windows开发包](https://github.com/FrigidCrow/ai-neko/actions/runs/36716659417/artifacts/11096433755)，解开Actions产物容器，再完整解压其中的`ai-neko-0.4.0-dev.50227627e8d4-windows-x64.zip`，双击根目录`ai-neko.exe`。通常需登录GitHub，产物到期时间为2026-10-14 12:59:01 UTC。包包含Electron、Python、YUI及运行依赖；模型/语音配置和免费搜索入口见[服务说明](FREE-SERVICES-AND-WINDOWS-VM.md)。本轮没有创建tag或Release。

### 来源与下载完整性

- 构建源码：`50227627e8d4c7c5574b4c52ebc12189d63988f9`，分支`codex/companion-five-capabilities`，构建时工作区干净。
- [CI 36716659417](https://github.com/FrigidCrow/ai-neko/actions/runs/36716659417)：Linux、Windows源码测试及Windows包三个必需job全部success；tagged发布按设计skipped。
- 包版本：`0.4.0-dev.50227627e8d4`；实际ZIP大小192,076,777字节，SHA256：`a79b04b543dd61ba7b36f990d8f4456edacff847a0398ca275251e038a240edc`。GitHub外层产物容器大小与此应用ZIP大小不同。
- 独立下载验证核对ZIP CRC、内外构建信息、210份源码输入、两个依赖锁、AMD64桌面与后端程序摘要、66份许可文件、79份素材、16张截图及同一ZIP的四份运行报告。普通文本按实际Windows CRLF检出重建比较；冻结资料保留原始字节。
- [机器核对报告](evidence/mvp2/windows/windows-download-verification.json)、[构建信息](evidence/mvp2/windows/build-info.json)、[校验清单](evidence/mvp2/windows/SHA256SUMS.txt)和[核对脚本副本](evidence/mvp2/windows/verify_windows_download.py)已归档。脚本原始输入和ZIP保留在本机`artifacts/mvp2/ci-36716659417/`；Mac只核对文件，没有运行Windows程序。后续文档提交不改变此构建源码身份。

### 实际通过的门禁

| 范围 | Windows Server 2022 x64 | Linux / 说明 |
| --- | --- | --- |
| Python完整回归 | 1,581通过、0跳过；521.5秒 | 1,580通过、1个Windows凭据专属跳过；118.407秒 |
| 桌面宿主单测 | 92通过 | 92通过 |
| 冻结30题 | 24/24 top3命中，6/6缺口识别 | 相同；错误游戏/已知冲突版本/禁止来源为0 |
| 200篇本地检索 | 5次预热、100样本，p95 80.3016ms | p95 26.84471ms；固定门槛150ms，均0网络与0检索断言失败 |
| 实际解压后端 | 16/16 | [包报告](evidence/mvp2/windows/package-smoke.json) |
| 实际桌宠与流式聊天 | 9/9 | [桌面报告](evidence/mvp2/windows/desktop-smoke.json) |
| 人格/记忆/视觉/语音与免费搜索设置 | 21/21 | [陪伴报告](evidence/mvp2/windows/companion-smoke.json)，renderer错误为空 |
| 攻略持久化与连续对局联合验收 | 8/8 | [G6报告](evidence/mvp2/windows/g6-acceptance.json)，实际解压exe、两个不同进程、25个记录问题完成且0搜索/取页 |

G6通过真实按钮/表单/IPC操作，用户变更的API捷径计数为0；覆盖采用后退出、新进程新聊天复用、连续五问的搜索开关对照、旧局/新局与明确复盘、来源S1重映射。调用36次合成模型HTTP；不证明真实模型理解或所有游戏场景。

陪伴报告含18次合成模型、3次合成ASR、29次合成TTS，以及20次实际WebAudio停止采样，点击到`AudioBufferSourceNode.stop`返回p95约0.20ms。它不是声学停止、麦克风到回复或Windows11硬件延迟。上述CI的真实云服务和用户媒体采集均0；本轮另行进行的AnySearch真实搜索与公开正文读取不计入CI。

Root实际查看[免费搜索设置](evidence/mvp2/windows/companion-smoke-free-search.png)与[重启后新聊天](evidence/mvp2/windows/g6-acceptance-A01-new-process-new-chat.png)：免Key配置、保留的采用版本/本地来源和YUI同屏呈现正常。[目视记录](evidence/mvp2/windows/visual-review.json)只覆盖这两张截图，不代替其它DPI或真实游戏验收。

### 本轮修复与保留的失败记录

先前CI失败已逐次保留在WORKLOG及本机artifacts：冻结资料CRLF转换；Python3.11 DNS等待吞取消的真实竞态；Windows空锁扫描、缓存等待者编排、旧版禁网hook安装顺序及隔离用户目录。冻结旧版源码/数据/manifest未改，媒体字节和迁移断言保留。

CI36710927613的检索p95 151.2265ms超过150ms，因此未打包。随后只优化单次分词调用中重复片段的处理，600组词元和600组BM25结果/分数与旧实现完全一致；未改语料、评分或门槛，见[优化证据](evidence/mvp2/tokenizer-optimization.json)。

CI36713526928源码门禁、后端16项和桌宠9项通过，但陪伴第4项失败：第二次人格保存误等上次成功提示，过早读取记忆。修复仅改验收等待，使用新版本、名字和本次完成状态；显式Promise闸门证明旧谓词误过，再释放真实请求并核对版本2→3。新Windows报告已验证该回归，后端并发保护未放宽；旧CI没有HTTP状态证据，409仅为代码路径推断。

### 仍需完成

- 配置本项目自己的模型/ASR/TTS，完成真实20对冷暖问题、有效语音、用量/费用和建议质量记录。匿名搜索及中英文公开正文已实测，尚不等于三次完整带来源模型问答。
- Windows11真实游戏至少十轮、切换/新局/重启、三次公开来源流程及20次跨生成/合成/播放停止，按[真实验收步骤](MVP2-LIVE-ACCEPTANCE.md)留证。
- 本机M4可尝试Windows11 ARM的x64仿真；当前约32GiB空闲、无已安装VM，仍需存储位置及所选虚拟机的账户/许可流程。VM尚未安装或启动，ARM VM也不替代原生Windows11 x64游戏/驱动/硬件结果。

用户的推送/CI授权阻塞已解除，Windows包交付验证已完成；完整MVP2只在真实服务和Windows11证据齐备后完成。
