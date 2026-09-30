# Windows 下载与 GitHub CI/CD

最新 [MVP2 Windows 开发包](https://github.com/FrigidCrow/ai-neko/actions/runs/36716659417/artifacts/11096433755)为 `0.4.0-dev.50227627e8d4`，已通过 Windows CI 及实际下载核对。提供可见白裙 YUI 猫娘、流式聊天、人格、当轮视觉、按键语音、长期记忆，以及攻略保存/采用/复用、当前对局管理和免费 AnySearch 搜索。本轮是分支开发包，未创建新 tag 或 Release；Windows 11 x64 真机与真实模型/语音验收仍待完成，不能据此宣称完整 MVP2 通过。完整证据见 [Windows 验证报告](MVP2-WINDOWS-VERIFICATION.md)。

## 下载和运行

- 在上方 Actions 链接下载 `ai-neko-windows-x64` 产物；解开产物容器，取其中 **`ai-neko-0.4.0-dev.50227627e8d4-windows-x64.zip`**。通常需登录 GitHub，产物保留至 **2026-10-14 12:59:01 UTC**。GitHub 自动生成的 Source code 是源码，不是应用。
- 完整解压到独立程序文件夹，保留根目录`ai-neko.exe`、`resources`和其他文件。包内含Electron、Python、猫娘及运行依赖，无需另装Python、Node或uv。
- 双击根目录`ai-neko.exe`或`Start ai-neko.cmd`。首次阅读并选择是否接受Live2D条款；接受后显示猫娘，点击她打开聊天。在猫娘设置中配置模型、搜索与ASR/TTS。观察画面需要支持图片的模型。
- 免费搜索在 **设置 → 共享联网搜索 → AnySearch（免费免 Key）** 中启用，核对基础地址 `https://api.anysearch.com/v1` 并保存。已有自定义地址不会随切换自动覆盖；匿名模式不发送已保存的 Tavily Key，也不自动清除它。Tavily 仍需自己的 Key。模型、ASR 和 TTS 另行配置，条件与实测见[免费服务说明](FREE-SERVICES-AND-WINDOWS-VM.md)。
- 先选择具体窗口或屏幕，再启用观察并核对预览。每个新问题取新图；点击“说话”开始录音，再点一次提交，勾选“朗读回复”启用语音输出。默认按需联网，兼容模型共用搜索配置；选择“仅聊天”可关闭联网，已采用的本地资料仍可用于有依据的问题。
- 聊天中的“攻略库”用于保存公开网页正文、采用/切换资料、查看原文来源和管理攻略快照；“管理本局”用于开始/更新/结束对局、明确复盘旧局。新局清空旧动态状态，保留攻略选择和个人偏好；重启后局势待更新。保存攻略不等于离线运行模型。
- 长期记忆可以明确保存、查看依据、纠正、遗忘及管理快照。自动整理默认关闭，开启后使用所配置模型，可能产生服务费用；快照不包含服务凭据或全部应用数据。
- 收起聊天面板保留桌宠；托盘可找回、恢复到主屏和退出。退出会结束自己的后端。`Check foundation.cmd`只运行合成基础自检；服务启动/停止脚本仅供诊断，不要对同一数据根同时运行多个入口。
- 默认数据根为`%LOCALAPPDATA%\ai-neko`，可通过`AI_NEKO_DATA_DIR`设置独立绝对路径。程序目录和数据根分开；不读取或迁移原N.E.K.O.配置、数据、凭据。更新时退出旧程序，将新版解压到新文件夹，保留现有数据根；不要把程序解压到数据根内。

详细功能与边界见[五项能力使用说明](COMPANION-IMPLEMENTATION.md)。[版本列表](https://github.com/FrigidCrow/ai-neko/releases)保留历史包：0.4.0-alpha.1 为早期陪伴版，0.3.0-alpha.1 为基础桌宠版，0.2.0-alpha.1 为网页预览，0.1.0-alpha.1 为基础诊断版；这些历史 Release 均不包含本次 MVP2 攻略、对局和免费搜索能力。

## 文件与校验

Actions 应用产物提供应用 ZIP、`build-info.json`、`SHA256SUMS.txt` 和四份运行报告。同次运行的 `evidence-Windows` / `evidence-Linux` 产物保留源码测试、冻结 30 题与 200 篇检索报告；`package-evidence` 保留包报告和截图。四份报告均实际执行同一个 ZIP 解压后的程序：

| 报告 | 本次结果 | 范围 |
| --- | --- | --- |
| `package-smoke.json` | 16/16 | 冻结后端、鉴权、中文路径、进程/端口与图恢复 |
| `desktop-smoke.json` | 9/9 | 桌宠、设置、流式聊天及桌面生命周期 |
| `companion-smoke.json` | 21/21 | 人格、记忆、视觉、语音与免费搜索设置闭环 |
| `g6-acceptance.json` | 8/8 | 采用后重启新聊天、两轮五问零搜索、旧局/新局/复盘与来源定位 |

在 PowerShell 核对里面的应用 ZIP（不是外层 Actions 产物容器）：

```powershell
Get-FileHash .\ai-neko-0.4.0-dev.50227627e8d4-windows-x64.zip -Algorithm SHA256
```

应用 ZIP 为 **192,076,777 字节**，SHA-256 为 `a79b04b543dd61ba7b36f990d8f4456edacff847a0398ca275251e038a240edc`。将结果与 `SHA256SUMS.txt` 比较；对应源码为 `50227627e8d4c7c5574b4c52ebc12189d63988f9`。[实际下载核对](evidence/mvp2/windows/windows-download-verification.json)已验证 ZIP CRC、包内外构建信息、210 份源码输入、AMD64 程序、依赖及许可摘要一致；文本按 Windows 检出换行校验。本机 Mac 只核对下载文件，Windows 程序的实际执行证据来自 CI。

`build-info.json` 记录源码 commit、版本、锁文件、构建环境及许可摘要。YUI 素材和 N.E.K.O. 记忆组件的许可、NOTICE 及来源记录随包提供，详见[素材记录](MVP1-ASSETS.md)和[记忆复用](MEMORY-REUSE.md)。本版尚未签名。

## 流水线

[workflow](../.github/workflows/windows-preview.yml)使用固定action commit、uv 0.11.8、Node.js 24.21.0、Python锁版本和`desktop/package-lock.json`，Electron锁定44.4.5。

1. push、PR和手动运行在Ubuntu 24.04及Windows Server 2022执行依赖锁安装、素材/许可检查、桌面宿主单元测试、Python格式和合成测试，以及冻结30题评测和200篇攻略检索基准。失败或非预期跳过阻止后续；Linux的Windows凭据专属用例可跳过，报告保留PARTIAL并另列ci_gate。
2. 两平台通过后，在Windows原生构建PyInstaller后端并与Electron、角色及许可证组装。根目录`ai-neko.exe`是桌面入口，后端在`resources/backend/ai-neko.exe`，前端在`resources/app/`。
3. 对同一ZIP解压后的真实程序检查鉴权、中文路径、实例/端口、退出、图恢复和流式聊天；随后执行桌宠基线及人格/记忆/视觉/语音闭环。G6 使用 `--archive` 继续验证采用后退出、新进程新聊天复用、配置与禁用搜索各五问，以及旧局/新局/明确复盘隔离。测试使用合成资料和服务，不读取用户麦克风、桌面或凭据。
4. 普通构建上传带源码commit的Actions开发产物，保留14天，通常需登录GitHub。版本tag在所有必需检查通过后自动创建新的prerelease，附包、报告、截图和摘要。只有发布job具有`contents: write`权限。

版本标签为`v<基础版本>-<预发布后缀>`，已有Release为`v0.4.0-alpha.1`。Python/Electron及锁文件基础版统一为0.4.0；分支包附带源码commit，tag包保留完整alpha后缀。已发布版本不覆盖，后续发布使用新tag；本次MVP2交付没有创建新tag或Release。稳定发布、签名、自动升级与完整迁移仍需后续验收。

## 验证记录

### 当前：MVP2 Windows 开发包，2026-09-30

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

Windows Server CI 不等于 Windows 11 x64 用户真机。真实人格/游戏建议质量、实际游戏截图识别、麦克风/音色、三次完整带来源模型问答、记忆模型正确使用、端到端冷暖性能及20次硬件停止延迟 p95 仍待验收。AnySearch 匿名搜索和公开网页正文读取已[单独实测](FREE-SERVICES-AND-WINDOWS-VM.md)，不能计为完整模型问答；本地检索基准与合成 WebAudio 停止耗时也不能替代这些真实指标。

[真实验收记录工具](MVP2-LIVE-ACCEPTANCE.md)用于保留失败样本和未填证据，未取得的声音、费用或真机结果继续保持 PENDING。免按键、多角色、主动陪伴与长期运行不是本次开发包的已完成能力。各阶段保持[PLAN](PLAN.md)所列状态，实际命令与结果见[WORKLOG](../WORKLOG.md)及[REVIEW](../REVIEW.md)。
