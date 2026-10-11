# Windows 下载与 GitHub CI/CD

## 当前：0.4.0-alpha.2 发布候选，尚未发布

2026-10-11 已完成修复代码审查、本地完整回归和 GPT-6.1 Sol 定向复测。[候选发布说明](releases/v0.4.0-alpha.2.md)仍保持 **`release-readiness: pending`**：素材/运行依赖分发条件、最终提交对应的远端 CI、重建包核验与 Sol 补充验收尚未完成，当前没有新 Release 下载结果。

本地受测源码为 `1ed2e2f52ce9b914c9349e27476f895001905e2a`；ZIP 为 `ai-neko-0.4.0-alpha.2-windows-x64.zip`，192,308,939 字节，SHA-256 `8082aefe7fe45e4daf23d99b9de5382a912344cef7e20bc6a94779e036e46944`。该包通过 56 项同包合成检查；Python 2,005 通过 / 13 主机符号链接权限跳过，桌面 339 通过，lint/格式及 43 项 JS 语法检查通过。13 项跳过须在适用 CI 环境实际执行，不能算通过。

Sol 已验证两次明确同一事实、引用完整旧记录的真实自动纠正，事实身份/key、修订和来源保留，新会话召回及遗忘后旧快照不复活；压缩网页的准确错误提示和既定英文充分证据暖问也通过。原 D1 阻塞仅在这些观察场景内关闭。首轮 47 族并非全部通过，本次也未全量重跑；一般实体语义、中文/词形/格式指令覆盖和未观察到的并发/取消/冲突时序仍为 PARTIAL。一次取证时观察到未收齐尾部；现存事件含完整回复，确定性回放可恢复，未取得当次最终可见界面样本，不能据此宣称一般回复质量通过。

候选观察画面仅支持明确选中的 Windows 窗口，失效或撤销后不回退到整屏或其他窗口。本轮定向复测不替代完整原生托盘、DPI/多屏、游戏窗口兼容与活跃期撤销验收。**语音接入与真实语音验收继续暂停**：保留合成回归，默认 CI 不安装专用语音环境、不下载模型或执行真实推理，记录 `SKIPPED_PAUSED`；本轮不选择 `run_real_voice=true`。实验性 Qwen 代码不等于已验收可用功能。

发布沿用 `release/v0.4.0-alpha.2` 分支和新的预发布标签 `v0.4.0-alpha.2`，基础版本保持 `0.4.0`，不覆盖旧标签或用旧默认分支替换候选。说明更新会产生新提交，必须验证最终提交的精确 CI SHA；后续 CI/Release 重建 ZIP 与本地受测 ZIP 分别记录摘要，不能宣称字节相同或自动继承全部实测结论。

发布分为两层：解决候选审查、说明、分发许可和发布连接等前置条件后，`release-readiness: ready` 仅授权 tag 流水线进入 **draft prerelease 暂存**，不授权公开。tag 的全部必需检查通过后产生新 CI ZIP 和草稿附件；Sol 再对该精确 ZIP 补充验收，发布负责人核对后公开同一草稿及原附件，不重新构建或替换，最后下载公开附件核对摘要。当前仍为 `pending`，没有本轮草稿或正式公开发布成功的结论。

### 发行资格门禁：当前关闭

包含本次修改的源码提交在普通 push、PR 和默认手动运行时只执行无需 Core 的合成测试及 JSON 证据保存；13 个符号链接用例仍在完整 pytest 内，非预期 skip 继续阻止 CI。显式 `run_real_voice=true` 仍保留 test job 原有的 native voice 导入检查，不会打开受限 job，本轮不触发。`windows-preview.yml` 的 `package` 与 `release`、`desktop-diagnostics.yml` 的 `probe` 均有字面 `false` 的 job 条件。整个 job 被阻止，内部 `always()` 上传也不会执行；不会经这些路径下载 Core、构建完整应用、上传模型/Core 包或截图/音频附件、创建 draft 或 public Release。没有新增 secrets、仓库变量或可打开门禁的 dispatch 参数。`ready` 和 `--draft` 均不能解除资格门禁。

默认源码 CI 继续写 `Windows-voice-import-smoke.json` 的暂停记录；package job 被关闭期间不会生成 `free-voice-smoke.json` 或任何包验收报告。关闭不等于检查通过，也不代表已具备完整可分发制品。

门禁仅适用于包含修改的新提交；不能阻止远端旧分支/旧标签或历史工作流重跑。后续只推送经核验的源码分支新 HEAD，不附带旧标签或执行旧工作流。仓库原有 YUI 素材及历史不会因此消失，因此准确范围是“源码/文档增量，不生成新应用包”，不是宣称整个仓库没有第三方素材。

解除前必须记录实际产品范围、发行主体及相应许可依据。当前渲染入口固定加载 YUI，没有用户模型导入/选择功能，但解压包内资源可手工替换，不能只凭无按钮、低营收或本机接受条款判定发行豁免。[Live2D 条款 §1.5、§2.2、§6.3](https://www.live2d.com/eula/live2d-proprietary-software-license-agreement_en.html)及[Expandable 发行说明](https://www.live2d.com/en/sdk/license/expandable/)要求先区分产品类别；若为 Expandable，个人/小规模发行者也须事先获得审查和适用协议。需要澄清分类时，可由发行负责人向官方询问以下问题，当前未发送：

> ai-neko 是本地 Windows AI 伴侣，发行包固定包含一个 YUI 模型，没有模型导入、选择、生成、多作品入口或市场功能；解压目录中的模型文件可被用户手工替换，兼容性不作保证。请确认这种产品是否属于 §1.5 的 Expandable Application；如不属于，实际发行主体满足 General Users/Small-Scale 条件时是否适用 §2.2；如属于，需要哪些事先审查和发行协议？

有资格依据后，以独立审阅的新提交解除三个 job 门禁；Git 登录、个人身份/营收回复或既有用户同意本身不自动打开。该精确 SHA 的 CI 必须通过，新 CI ZIP 须单独记录身份并经 Sol 验收，随后才公开同一 draft 原附件。若为解决分类而改动产品能力，另做对应功能验收，不能继承旧包结论。

## 历史下载：2026-10-03 免费本地语音开发包

以下链接、使用说明、数字和附件均对应此前的 [MVP2 Windows 开发包](https://github.com/FrigidCrow/ai-neko/actions/runs/37135192000/artifacts/11278787928) `0.4.0-dev.b443768df012`，不是本次 alpha.2 候选。该历史包已通过当时的 Windows CI、真实免费语音测试及实际下载核对，提供可见白裙 YUI 猫娘、流式聊天、人格、当轮视觉、按键语音、长期记忆，以及攻略保存/采用/复用、当前对局管理、免费 AnySearch 搜索和无需 Key 的本地中文语音。当时为分支开发包，未创建新 tag 或 Release；Windows 11 x64 真机、聊天模型建议质量和用户麦克风/音色验收仍待完成，不能据此宣称完整 MVP2 通过。完整历史证据见 [Windows 验证报告](MVP2-WINDOWS-VERIFICATION.md)。

### 历史包下载和运行

- 在上方 Actions 链接下载 `ai-neko-windows-x64` 产物；解开产物容器，取其中 **`ai-neko-0.4.0-dev.b443768df012-windows-x64.zip`**。通常需登录 GitHub，产物保留至 **2026-10-17 16:14:17 UTC**。GitHub 自动生成的 Source code 是源码，不是应用。
- 完整解压到独立程序文件夹，保留根目录`ai-neko.exe`、`resources`和其他文件。包内含Electron、Python、猫娘及运行依赖，无需另装Python、Node或uv。
- 双击根目录`ai-neko.exe`或`Start ai-neko.cmd`。首次阅读并选择是否接受Live2D条款；接受后显示猫娘，点击她打开聊天。在猫娘设置中配置聊天模型、搜索和语音。观察画面需要支持图片的模型。
- 免费搜索在 **设置 → 共享联网搜索 → AnySearch（免费免 Key）** 中启用，核对基础地址 `https://api.anysearch.com/v1` 并保存。已有自定义地址不会随切换自动覆盖；匿名模式不发送已保存的 Tavily Key，也不自动清除它。Tavily 仍需自己的 Key。聊天模型另行配置，条件与实测见[免费服务说明](FREE-SERVICES-AND-WINDOWS-VM.md)。
- 免费语音在 **设置 → 语音输入与输出 → 免费本地语音（无需 Key）** 中选择，点击 **“下载并启用免费语音”**。两个模型约 310 MB，另需下载专用运行组件，建议预留 2 GiB；准备完成后点击 **“试听已保存的音色”**。无需账号或语音 Key，独立试听也不需要聊天模型；原有云服务配置保留。初版每次请求加载模型，实际短句合成约 17.297 秒，不能承诺即时响应。步骤、资源与费用边界见[免费语音说明](FREE-VOICE.md)。
- 先选择具体窗口或屏幕，再启用观察并核对预览。每个新问题取新图；点击“说话”开始录音，再点一次提交，勾选“朗读回复”启用语音输出。默认按需联网，兼容模型共用搜索配置；选择“仅聊天”可关闭联网，已采用的本地资料仍可用于有依据的问题。
- 聊天中的“攻略库”用于保存公开网页正文、采用/切换资料、查看原文来源和管理攻略快照；“管理本局”用于开始/更新/结束对局、明确复盘旧局。新局清空旧动态状态，保留攻略选择和个人偏好；重启后局势待更新。保存攻略不等于离线运行模型。
- 长期记忆可以明确保存、查看依据、纠正、遗忘及管理快照。自动整理默认关闭，开启后使用所配置模型，可能产生服务费用；快照不包含服务凭据或全部应用数据。
- 收起聊天面板保留桌宠；托盘可找回、恢复到主屏和退出。退出会结束自己的后端。`Check foundation.cmd`只运行合成基础自检；服务启动/停止脚本仅供诊断，不要对同一数据根同时运行多个入口。
- 默认数据根为`%LOCALAPPDATA%\ai-neko`，可通过`AI_NEKO_DATA_DIR`设置独立绝对路径。程序目录和数据根分开；不读取或迁移原N.E.K.O.配置、数据、凭据。更新时退出旧程序，将新版解压到新文件夹，保留现有数据根；不要把程序解压到数据根内。

详细功能与边界见[五项能力使用说明](COMPANION-IMPLEMENTATION.md)。[版本列表](https://github.com/FrigidCrow/ai-neko/releases)保留历史包：0.4.0-alpha.1 为早期陪伴版，0.3.0-alpha.1 为基础桌宠版，0.2.0-alpha.1 为网页预览，0.1.0-alpha.1 为基础诊断版；这些历史 Release 均不包含本次 MVP2 攻略、对局和免费搜索能力。

## 历史包文件与校验

Actions 应用产物提供应用 ZIP、`build-info.json`、`SHA256SUMS.txt` 和五份运行报告。同次运行的 `evidence-Windows` / `evidence-Linux` 产物保留源码测试、冻结 30 题与 200 篇检索报告；`package-evidence` 保留包报告、截图及真实合成 WAV，Windows 源码证据另含原生导入前置报告。五份包报告均实际执行同一个 ZIP 解压后的程序：

| 报告 | 本次结果 | 范围 |
| --- | --- | --- |
| `package-smoke.json` | 16/16 | 冻结后端、鉴权、中文路径、进程/端口与图恢复 |
| `desktop-smoke.json` | 9/9 | 桌宠、设置、流式聊天及桌面生命周期 |
| `companion-smoke.json` | 21/21 | 人格、记忆、视觉、语音与免费搜索设置闭环 |
| `g6-acceptance.json` | 8/8 | 采用后重启新聊天、两轮五问零搜索、旧局/新局/复盘与来源定位 |
| `free-voice-smoke.json` | 11/11 | 真实本地模型安装、中文 TTS/ASR、重启后就绪无需重下 |

前四套测试使用合成资料和服务；免费语音 11 项使用固定测试文本及真实模型生成的音频，没有调用付费 API 或采集用户麦克风。

在 PowerShell 核对里面的应用 ZIP（不是外层 Actions 产物容器）：

```powershell
Get-FileHash .\ai-neko-0.4.0-dev.b443768df012-windows-x64.zip -Algorithm SHA256
```

应用 ZIP 为 **192,176,491 字节**，SHA-256 为 `f5035842285b15c4009485556a56d38862ce352588ffa31a0bf5caeb8776fb91`。将结果与 `SHA256SUMS.txt` 比较；对应源码为 `b443768df012fa4c88036f2fc13321b1e79d1d84`。[实际下载核对](evidence/mvp2/free-voice/windows/windows-download-verification.json)已验证 ZIP CRC、包内外构建信息、226 份源码输入、AMD64 程序、66 份许可、79 份素材及 17 张截图摘要一致；文本按 Windows 检出换行校验。本机 Mac 只核对下载文件，Windows 程序的实际执行证据来自 CI。

`build-info.json` 记录源码 commit、版本、锁文件、构建环境及许可摘要。YUI 素材和 N.E.K.O. 记忆组件的许可、NOTICE 及来源记录随包提供，详见[素材记录](MVP1-ASSETS.md)和[记忆复用](MEMORY-REUSE.md)。本版尚未签名。

## 流水线

[workflow](../.github/workflows/windows-preview.yml)使用固定action commit、uv 0.11.8、Node.js 24.21.0、Python锁版本和`desktop/package-lock.json`，Electron锁定44.4.5。

1. push、PR和手动运行在Ubuntu 24.04及Windows Server 2022执行依赖锁安装、素材/许可检查、桌面宿主单元测试、Python格式和完整合成测试，以及冻结30题评测和200篇攻略检索基准。失败或非预期跳过阻止后续；Linux的Windows凭据专属用例可跳过，报告保留PARTIAL并另列ci_gate。语音源码的合成回归仍执行。Windows 专用语音环境安装和真实 NumPy/Sherpa 导入仅在手动显式选择 `run_real_voice=true` 时执行；默认记录 `Windows-voice-import-smoke.json` 为 `SKIPPED_PAUSED`，专用语音组件/模型下载与真实语音调用均为0。
2. **当前以下打包/发布步骤受发行资格门禁关闭。** 解除后，两平台通过才在Windows原生构建PyInstaller后端并与Electron、角色及许可证组装。根目录`ai-neko.exe`是桌面入口，后端在`resources/backend/ai-neko.exe`，前端在`resources/app/`。
3. 对同一ZIP解压后的真实程序检查鉴权、中文路径、实例/端口、退出、图恢复和流式聊天；随后执行桌宠基线及人格/记忆/视觉/语音合成闭环。G6 使用 `--archive` 继续验证采用后退出、新进程新聊天复用、配置与禁用搜索各五问，以及旧局/新局/明确复盘隔离。以上四套测试使用合成资料和服务，并全部保持必需门禁；不能证明真实窗口截图、供应商服务或语音效果。免费语音模型安装与 TTS/ASR 仅在手动显式选择 `run_real_voice=true` 时执行，默认 `free-voice-smoke.json` 记录 `SKIPPED_PAUSED`、未执行、零下载/调用，不能记为真实语音通过。
4. 普通构建上传带源码 commit 的 Actions 开发产物，保留14天，通常需登录 GitHub。版本 tag 在全部必需检查通过且同一源码 commit 中的 `docs/releases/v<版本>.md` 标记 `release-readiness: ready` 后，只创建新的 **draft prerelease**，附精确 tag CI 产生的包、报告、安全合成证据与摘要。`ready` 仅授权草稿暂存；说明缺失或 pending 仍阻止该步骤，既有版本不覆盖，只有发布 job 具有 `contents: write` 权限。
5. 下载并核对草稿的精确 ZIP、源码 commit、包内外构建信息和摘要；由 Sol 对这份新 CI ZIP 补充验收。通过后发布负责人公开同一既有 draft，保留原 tag 与全部已验附件，不重建、不替换；再下载公开附件复核 SHA-256。草稿生成成功或旧本地包通过均不等于这一步已完成。

版本标签为`v<基础版本>-<预发布后缀>`，截至2026-10-10核查，已有最新Release为`v0.4.0-alpha.1`，`v0.4.0-alpha.2`仍是候选。Python/Electron及锁文件基础版统一为0.4.0；分支包附带源码commit，tag包保留完整alpha后缀。已发布版本不覆盖，后续发布使用新tag。稳定发布、签名、自动升级与完整迁移仍需后续验收。

## 验证记录

### 历史：免费本地语音 Windows 开发包，2026-10-04

[CI 37135192000](https://github.com/FrigidCrow/ai-neko/actions/runs/37135192000)三个必需 jobs 全部通过，发布 job 因非 tag 按设计跳过。CI 与下载发生于 2026-10-03 UTC，本地记录日期为 2026-10-04。源码为 `b443768df012fa4c88036f2fc13321b1e79d1d84`，构建时工作区干净。Windows Server 2022 x64 的 Python 测试 **1,673 通过 / 0 跳过，473.937 秒**；Ubuntu 24.04 为 **1,672 通过 / 1 项 Windows 凭据平台跳过，105.268 秒**；两平台桌面单测各 **106/106**。Linux 报告保留 PARTIAL，`ci_gate=PASS`。

两平台冻结 30 题均为 **24/24 top3 依据命中、6/6 缺口拒绝**，错误游戏、已知冲突版本和禁止来源均为零。200 篇攻略、100 个样本的本地检索 p95 为 **Windows 66.5625ms、Linux 12.566144ms**，低于未变的 150ms 门槛；该基准排除模型、网络、ASR/TTS 和建库耗时。

[原生导入前置检查](evidence/mvp2/free-voice/windows/Windows-voice-import-smoke.json)用 18 个占位资源验证 NumPy/Sherpa 和完整 worker 协议，0.391 秒自然退出；它不加载真实模型。五份实际包报告通过 **16/9/21/8/11** 项。独立的[真实免费语音报告](evidence/mvp2/free-voice/windows/free-voice-smoke.json)记录模型及运行组件安装约 **37.5 秒**；固定 24 字测试句合成 **17.297 秒**，生成 **6.6095 秒**的单声道 24 kHz、16 bit PCM WAV；转换到 16 kHz 后识别 **2.219 秒**。归一化 CER 为 **0**，但原始识别多了一个逗号，不能称逐字一致。两个不同后端进程确认重启后 ready、无需重新下载。每次请求仍独立加载模型，单句 CI 耗时不代表用户实时体验。

[实际下载核对报告](evidence/mvp2/free-voice/windows/windows-download-verification.json)为 PASS；[实际模型合成音频](evidence/mvp2/free-voice/windows/free-voice-smoke-speech.wav)、目视截图、依赖版本和边界见 [Windows 验证报告](MVP2-WINDOWS-VERIFICATION.md)。真实聊天服务、用户麦克风和 Windows 11 硬件验收仍待完成。

### 历史：MVP2 攻略与免费搜索开发包，2026-09-30

以下记录对应 `0.4.0-dev.50227627e8d4`，不包含免费本地语音。源码为 `50227627e8d4c7c5574b4c52ebc12189d63988f9`，ZIP 192,076,777 字节，SHA-256 `a79b04b543dd61ba7b36f990d8f4456edacff847a0398ca275251e038a240edc`；旧报告、失败记录和来源身份保留在 [Windows 历史记录](MVP2-WINDOWS-VERIFICATION.md#历史50227627e8d4-攻略与免费搜索开发包)。

[CI 36716659417](https://github.com/FrigidCrow/ai-neko/actions/runs/36716659417)全部必需 jobs 通过，发布 job 因非 tag 按设计跳过。Windows Server 2022 x64 的 Python 测试 **1,581 通过 / 0 跳过，521.5 秒**；Ubuntu 24.04 为 **1,580 通过 / 1 项 Windows 凭据平台跳过，118.407 秒**；桌面单测两平台各 **92/92**。Linux 报告保留 PARTIAL，允许的平台跳过对应 `ci_gate=PASS`。

两平台冻结 30 题均为 **24/24 top3 依据命中、6/6 缺口拒绝**；覆盖题零搜索/取页，错误游戏、已知冲突版本和来源身份错误均为零。200 篇攻略、100 个样本的本地检索 p95：**Windows 80.3016ms、Linux 26.84471ms**，均满足 150ms 门槛。该基准排除模型、网络、ASR/TTS 和建库耗时，不代表端到端冷暖性能。

四份实际包报告通过 **16/9/21/8** 项；G6 启动了两个不同 PID 的实际 Windows exe，记录的 25 个普通问题完成，搜索/取页均为零，另覆盖明确历史复盘。模型请求由合成 HTTP 服务记录，用于核对同一攻略/版本原文、上下文隔离及来源定位，不评判真实模型建议质量。[下载核对报告](evidence/mvp2/windows/windows-download-verification.json)为 PASS；详细范围及截图见 [Windows 验证报告](MVP2-WINDOWS-VERIFICATION.md)。

### 历史：MVP1 收尾包，2026-09-27

历史 [MVP1 修复开发包](https://github.com/FrigidCrow/ai-neko/actions/runs/36316166347/artifacts/10931091498)版本为 `0.4.0-dev.748bab516488`，产物保留至 2026-10-11 11:42 UTC；不包含本次 MVP2 能力。以下数字与摘要仅对应该旧包。

2026-09-27，[MVP1收尾CI 36316166347](https://github.com/FrigidCrow/ai-neko/actions/runs/36316166347)全部必需jobs通过，修复源码 `748bab5164882c044b4108d41a7fa8df82a99a32`。Windows641/0skip、Linux640/1平台skip；宿主64、冻结后端16、桌宠9、陪伴闭环20通过，分支构建的发布job按设计跳过。[本轮下载核对](evidence/companion/mvp1-closeout-verification.json)PASS：191,878,279字节ZIP，SHA256 `53eb5bfad5187094558dbfcd2ee814ceb4ceea8310aa638da7d0c1b32c8db65f`；CRC、源码、内外构建信息和AMD64程序摘要匹配。文本按Windows CRLF检出字节校验，不将跨平台换行差异当作依赖漂移。完整说明见[MVP1收尾报告](MVP1-CLOSEOUT.md)。

该历史包的Windows20次WebAudio停止p95约0.20ms，测量点击到实际stop调用返回，使用合成音频，不代表声学或完整输入到输出延迟。真实云服务、用户屏幕/麦克风采集均0。

### 历史：v0.4.0-alpha.1 Release，2026-09-26

[发布CI 36233199073](https://github.com/FrigidCrow/ai-neko/actions/runs/36233199073)全部通过，源码为`f359826bd60139a6a9efcef8d959f56c385228ba`，版本为`v0.4.0-alpha.1`。Windows576项/0跳过、Linux575项/1平台跳过，宿主58项、冻结后端16项、实际桌宠9项、陪伴闭环18项；额外Windows桌面启动诊断也通过。Release于2026-09-26发布，含15个附件。

[Release下载核对](evidence/companion/release-v0.4.0-alpha.1-verification.json)为PASS：实际下载ZIP为191,844,465字节，SHA256为`0e133684466e13d481f7de7988f5c23cef240291740cf362640c7b280f7ac3f6`。15个附件与GitHub资产摘要一致；ZIP CRC、包内外build-info、Electron应用0.4.0、完整release版本、源码commit、AMD64程序及实际执行报告匹配。Mac只核对文件，未运行Windows程序。

## 仍待真实环境验收

Windows Server CI 不等于 Windows 11 x64 用户真机。真实人格/游戏建议质量、实际游戏截图识别、用户麦克风/扬声器音色、三次完整带来源模型问答、记忆模型正确使用、端到端冷暖性能及20次硬件停止延迟 p95 仍待验收。AnySearch 匿名搜索和公开网页正文读取已[单独实测](FREE-SERVICES-AND-WINDOWS-VM.md)，不能计为完整模型问答；本地检索基准、合成 WebAudio 停止耗时和固定语句的真实本地语音成功也不能替代这些真实指标。

[真实验收记录工具](MVP2-LIVE-ACCEPTANCE.md)用于保留失败样本和未填证据，未取得的声音、费用或真机结果继续保持 PENDING。免按键、多角色、主动陪伴与长期运行不是本次开发包的已完成能力。各阶段保持[PLAN](PLAN.md)所列状态，实际命令与结果见[WORKLOG](../WORKLOG.md)及[REVIEW](../REVIEW.md)。
