# 免费本地语音：来源与实现边界

核查日期：2026-10-03。用户已选择免费语音；实际实现采用 SenseVoice Small 中文识别与 Kokoro v1.1 中文女声朗读，经 sherpa-onnx 在本机 CPU 推理。首次下载需要网络，后续推理不使用 API Key、云账户或按量计费服务。本文说明下载来源、隔离方式与已运行证据，不代替真人语音或 Windows 11 真机验收。

## 固定资源

| 资源 | 固定版本 / 文件 | 大小 | SHA-256 |
| --- | --- | --- | --- |
| ASR | [SenseVoice int8 2024-07-17](https://github.com/k2-fsa/sherpa-onnx/releases/download/asr-models/sherpa-onnx-sense-voice-zh-en-ja-ko-yue-int8-2024-07-17.tar.bz2) | 163002883 B | `7d1efa2138a65b0b488df37f8b89e3d91a60676e416f515b952358d83dfd347e` |
| TTS | [Kokoro int8 multi-lang v1.1](https://github.com/k2-fsa/sherpa-onnx/releases/download/tts-models/kokoro-int8-multi-lang-v1_1.tar.bz2) | 147031220 B | `a1e94694776049035c4f2c6529f003aaece993c76aae9a78995831c3c4dcafc6` |
| uv macOS arm64 | [0.11.8 tar.gz](https://github.com/astral-sh/uv/releases/download/0.11.8/uv-aarch64-apple-darwin.tar.gz) | 20800166 B | `c729adb365114e844dd7f9316313a7ed6443b89bb5681d409eebac78b0bd06c8` |
| uv Windows x64 | [0.11.8 zip](https://github.com/astral-sh/uv/releases/download/0.11.8/uv-x86_64-pc-windows-msvc.zip) | 23530194 B | `c84629a56e0706b69a47ea35862208af827cb6fbfa1d0ca763c52c67594637e8` |
| uv Linux x64 | [0.11.8 tar.gz](https://github.com/astral-sh/uv/releases/download/0.11.8/uv-x86_64-unknown-linux-gnu.tar.gz) | 24147124 B | `56dd1b66701ecb62fe896abb919444e4b83c5e8645cca953e6ddd496ff8a0feb` |

两个模型压缩包合计 310034103 B（约 296 MiB）。还需下载独立 Python、安装器和运行库；当前 Mac 实际资源目录含缓存约 1.0 GiB，建议预留至少 2 GiB。下载进度字节数涵盖模型与安装器，独立 Python/wheel 下载显示“运行环境”阶段，不把它们冒充已纳入精确总量。

独立 [运行环境锁](../src/ai_neko/media/local_runtime/uv.lock) 固定 `sherpa-onnx==1.13.8`、`sherpa-onnx-core==1.13.8` 和 `numpy==2.4.4`，由官方 PyPI wheel 的大小与 SHA 校验。仅允许 wheel，不在用户机器构建未知源码依赖。CPython 固定 3.11.15，由 uv 0.11.8 内置的 [下载校验清单](https://github.com/astral-sh/uv/blob/0.11.8/crates/uv-python/download-metadata.json) 管理，构建日期为 20260414；Mac arm64、Windows x64、Linux x64 包摘要分别为：

- `7089d127a9933d860b3e4ae704234c664d2713825f27c0c6b89dd399adabbdf6`
- `71ffdf290e0483f0881e02518ecb9cedb449807856ae7dc76aa630e5acd00919`
- `b702a19b26cbd007abf9ccbaa45dfdff99e9dbd646d89c9f3c9bb7b501aea44f`

## 许可与引用

| 部分 | 许可和来源 |
| --- | --- |
| sherpa-onnx / sherpa-onnx-core | [v1.13.8 上游](https://github.com/k2-fsa/sherpa-onnx/tree/v1.13.8)，Apache-2.0；native TTS 同时包含 eSpeak NG GPL-3.0 等第三方组件，不能把整个运行环境说成“全 Apache” |
| SenseVoice 权重 | [FunASR Model Open Source License Agreement v1.1](https://github.com/modelscope/FunASR/blob/main/MODEL_LICENSE)，由 [SenseVoiceSmall 模型卡](https://huggingface.co/FunAudioLLM/SenseVoiceSmall) 指向；与 SenseVoice 代码仓库 Apache 许可区分 |
| Kokoro 权重 | [Kokoro-82M-v1.1-zh](https://huggingface.co/hexgrad/Kokoro-82M-v1.1-zh)，Apache-2.0；下载包的原始 LICENSE 保留 |
| NumPy | BSD-3-Clause 及 wheel 内的依赖声明；原始 dist-info/license 保留 |
| uv | MIT OR Apache-2.0，两个全文均保留 |
| CPython | PSF 许可，managed Python 的原始许可文件保留 |

[许可说明与全文](../src/ai_neko/media/local_runtime/LICENSES.md) 随独立 worker 源码交付，安装时复制到本应用 `assets/voice/runtime/licenses/`。主应用的依赖锁保持独立，PyInstaller 仅收集本地语音 worker 源码、私有环境锁及许可文本，不把 sherpa、NumPy、eSpeak native 库或模型冻结进主程序。资源由明确安装操作从原发布者下载，不复制 N.E.K.O 或其它工程的模型、虚拟环境、配置、密钥。

Python API 使用官方 [offline-tts 示例](https://github.com/k2-fsa/sherpa-onnx/blob/v1.13.8/python-api-examples/offline-tts.py) 和当前 Python bindings：SenseVoice `OfflineRecognizer.from_sense_voice`；Kokoro `OfflineTtsKokoroModelConfig` + `GenerationConfig`。当前固定 `sid=46`（v1.1 中文女声范围内），中文词典以及日期/数字/电话号码 FST 来自同一模型包。没有声纹克隆和用户声音训练。

## 隔离、取消与输入边界

[LocalVoice](../src/ai_neko/media/local_voice.py) 构造与状态查询不创建目录、不访问网络。明确安装后才在本项目数据根 `assets/voice` 写带 ownership marker 的私有资源；Python、venv、uv 缓存、临时目录均位于这里。启动使用环境变量白名单，屏蔽其它 Python/uv/PIP 配置、代理、Key 和索引；不依赖全局 uv 或现有虚拟环境。

所有下载校验固定大小和 SHA；解包逐项拒绝绝对路径、`..`、Windows 路径、符号链接、硬链接和特殊设备文件，并限制文件总数与膨胀大小。失败/取消没有 ready 标记，缺少模型必需文件重新变为 missing，可以重装。推理使用独立进程的有界 stdin/stdout JSON/base64，不开启 HTTP 端口、不写入用户录音、输入文字或输出音频文件。

每次推理最多两条 CPU 线程。初版每请求冷启动；2026-10-04 工作树优化为单个受控常驻 worker，ASR/TTS 按需加载并复用，历史开发包不因此更新。并行请求仍串行处理以限制内存；120 秒总时限包含锁等待和推理，排队超时后不会再启动推理或取消其他请求。识别接受 60 秒以内、16 kHz 单声道 PCM16 WAV，最大 8 MiB；朗读最多 600 字，返回单声道 PCM16 WAV，最大 8 MiB。活动请求超时、取消、协议异常、关闭均终止并等待子进程退出，包括取消发生在进程创建过程的情况；失败请求不自动重放，下次请求可重建。运行实际 managed Python 解释器并仅显式加载私有 venv 的 site-packages，绕过 Windows venv launcher 的二级进程；每次协议返回的原生 PID 和请求序号均须匹配。父进程保留所有者管道；worker 通过管道关闭检测退出，Linux 另设内核 parent-death signal，Windows 持父进程 HANDLE 防 PID 重用。native constructor/decode/generate 释放 Python GIL，使监控线程能在推理中运行。推理 worker 安装 socket 审计拒绝网络请求。 Windows 原生 eSpeak/kaldifst 文件读取仍使用窄字符接口，因此仅在独立 worker 内用 Python 切换到模型目录，再向 native 库传固定 ASCII 相对路径；中文用户目录保留原位，主应用工作目录不变。

固定 v1.1 的[官方声线映射](https://k2-fsa.github.io/sherpa/onnx/tts/all/Chinese-English/kokoro-multi-lang-v1_1.html)含 103 个编号；本地 `local_tts_speaker_id` 只接受整数 0–102，默认 46（`zf_079`），中文女声为 3–57。声线来自同一已安装资源，不额外下载或变调。前端最多预取下一段，按原顺序播放；换代、取消和保存新本地声线会停止旧队列。阶段观测只保留有界的数值计时、阶段及序号，不写正文、录音、凭据或图片；播放器启动事件不等于物理声音已被听到。

## 当前验证

本地引擎的确定性测试由 [test_local_voice.py](../tests/test_local_voice.py) 执行，不下载大模型；覆盖只读状态、外来目录、环境隔离、归档路径、下载 hash/大小/跳转、取消与真实子进程回收、正常退出父管道保持打开、缺失资源重装和 WAV 完整性。根工程的服务/API/桌面与 Windows CI 证据由主验证报告另行汇总。

2026-10-03 在本机 Mac M4 / Python 3.11.15 完成实际固定资源安装与短句推理。`artifacts/free-voice/sample.wav` 为合成测试音频，`artifacts/free-voice/probe.json` 为测量，不采用户麦克风。最终配置短句“你好，我是小猫。今天也一起学习吧。”生成 24000 Hz、224740 B、4.681 秒 WAV，冷启动朗读约 4.939 秒，重采样到 16 kHz 后识别约 0.799 秒，识别文本为“你好，我是小猫，今天来一起学习吧。”，存在“也→来”识别差异。它证明实际本地 ASR/TTS 链路工作，不代表真人、嘈杂环境、游戏负载或 Windows 11 音频体验已通过。

Windows 窄字符路径兼容修复后，在同一私有环境再次运行真实短句，原报告保留；`artifacts/free-voice/unicode-path-probe.json` 记录 worker SHA-256 `0a35004f9c11d6bfbf8f7d676a1374f3b62b792d74743193d3467c7337cb37e1`、TTS 约 4.947 秒、ASR 约 0.732 秒。该复测在 Mac 完成；中文根路径下的相对路径解析另有无模型子进程回归，Windows 实际 native 验证仍以 Windows CI 结果为准。

Windows 冻结程序使用编译期 `win-amd64` ABI 选择安装器，不依赖 `PROCESSOR_*` 环境变量。创建独立 uv/Python 子进程期间，按 [PyInstaller 外部进程说明](https://pyinstaller.org/en/stable/common-issues-and-pitfalls.html#launching-external-programs-from-the-frozen-application)临时清空继承的 DLL 搜索目录并立即恢复，避免专用运行组件加载主程序的同名库；成功、创建失败与取消回收均有回归覆盖。第一次实际包失败发生在架构判断阶段，尚不能将 DLL 继承机制说成当时已发生的冲突。

Windows owner 管道使用非阻塞 `PeekNamedPipe` 检查，避免在导入 NumPy/Sherpa 前留下阻塞的 stdin 读取；完成时停止监控并关闭自己持有的父进程句柄。依据为 [NumPy 的 Windows 同类问题](https://github.com/numpy/numpy/issues/24290)及 [Microsoft 同步句柄说明](https://devblogs.microsoft.com/oldnewthing/20111202-00/?p=8983/)。独立 CI 前置检查仅安装固定三个 wheel、使用18个占位资源验证完整 worker 协议与实际 native 导入，不下载模型，也不计为真实语音推理；真正的模型安装/中文合成/识别仍由解压 Windows 包的11项报告单独验证。


## Qwen Base0.6B 可选适配器（2026-10-06 工作树）

复用本工程已获准安装的 `Qwen/Qwen3-TTS-12Hz-0.6B-Base` 固定 revision `5d83992436eae1d760afd27aff78a71d676296fc`。原始13个文件共2,516,106,051B，清单见 `reports/qwen3-base-06b-20261005/model-manifest.json`。主权重SHA256为 `180b3b10eb1c9f1b4db7806d5475bae3071c0243c299d49926bab1da3b6946f6`，speech tokenizer权重为 `836b7b357f5ea43e889936a3709af68dfe3751881acefe4ecf0dbd30ba571258`。不导入完整参考聊天系统。

独立环境为Python3.11.15、qwen-tts0.1.1、Torch/Torchaudio2.10.0+cu128、Transformers4.57.3，完整解析版本保留在该实验 `run-manifest.json`。Qwen上游 Apache-2.0 许可全文保留为 `reports/qwen3-base-06b-20261005/LICENSE-Qwen3-TTS`；来源是官方QwenLM/Qwen3-TTS commit `022e286b98fbec7e1e916cb940cdf532cd9f488e`，已有运行依赖各自保留dist-info许可。适配代码通过已安装官方Python API调用，没有修改已安装第三方库，也不把模型或CUDA库打进主解释器。

已验收参考是本工程原创生成的 `01_playful_sweet.wav`，SHA256 `e585b2cbd22722618532d5a2ca610fd2f3a594e8349d576fe07c79df1d6e58bf`；可复用prompt文件SHA256 `c39fe66d150c565e45adb36213bee8c417bbda054fde9d1ca87b871589cf8e5e`。它不是用户或第三方真人声音采集。模型、环境与prompt固定路径经过明确CLI登记后才能使用；登记校验大小和摘要，日常状态检查只比对小清单及文件元数据。唯一已批准uv版本别名只允许解析到固定3.11.15安装目录，不放开其它junction或符号链接。

主服务保持轻量，独立受控worker按需加载Base及prompt；weights_only加载已有prompt，保留原参考文本及采样参数，BF16/SDPA、inference_mode沿用已验收实验。stdin/stdout协议、返回WAV、输入和总120秒时限均有界；排队取消不终止别的活动请求，活动取消/超时、释放、切换和退出均回收本应用worker。默认空闲300秒释放。公开诊断仅数值耗时/资源与安全状态，不暴露正文、音频、私有路径或Key。

升级需重新固定和验证资源、环境、prompt兼容性及新黑盒声音结果；不能仅修改版本字段或把现有基准当新版本证据。目前没有新模型、第三方推理框架、量化、驱动或系统配置变更。性能候选只在独立profile脚本，未实测通过前不作为生产默认。真实应用及Windows冻结包验收状态以 `REVIEW.md` 和本轮黑盒报告为准。
