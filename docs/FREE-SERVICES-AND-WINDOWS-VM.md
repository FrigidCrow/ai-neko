# 免费服务与 Windows 虚拟机核查

日期：2026-09-30。对应用户确认推送/CI、询问免费服务与旧N.E.K.O实现，并要求尝试Windows虚拟机。实施登记见[PLAN §17.9](PLAN.md#179-mvp2远端验证与免费服务虚拟机核查2026-09-30已授权)。额度与模型以供应商当前账户页面为准；下表区分已调用与仅查文档。

## 旧 N.E.K.O 确实有免费服务

只读参考源码commit `90ccf79c95e80f899b9bf3395fa8cd9a9bfe29be` 的受版本控制文件，没有读取运行配置/凭据或启动参考系统。

- `config/api_profiles.py`：免费Core走Lanlan Realtime，免费Assist走Chat Completions；不是必须寻找某个已保存的私人Key。[官方费用说明](https://project-neko.online/zh-CN/guide/cost-and-providers)确认随附免费配置无需用户Key，但运行在远端，额度可调整。
- `main_logic/asr_client/_registry_meta.py`：免费Core不支持独立ASR。它的转录来自Realtime会话，不能直接填进本项目HTTP转录接口。
- `main_logic/tts_client/workers/free.py`与`_step_protocol.py`：免费语音使用专用WebSocket/PCM协议，不能直接当成本项目的HTTP MP3接口。
- `plugin/plugins/web_search`：AnySearch匿名请求，另有百度/DDG回退。本项目复用公开协议接入AnySearch，不复制参考系统的完整工具循环。

没有找到旧N.E.K.O托管服务向任意第三方应用提供长期接入的公开承诺，也未据此断言禁止接入。其源码许可与远端运营服务是两个问题；本次未调用其免费代理。此前“本工程缺四类Key所以只能等待配置”的表述过于绝对。

## ai-neko 可用的免费路径

| 能力 | 路径 | 本轮状态和必要条件 |
| --- | --- | --- |
| 搜索 | AnySearch匿名API | 独立探针HTTP200；实际ProviderStore→WebTools→网络策略也已成功搜索5条并读取官方正文，详见下方 |
| 模型/图像 | Groq免费层，候选`qwen/qwen3.8-27b` | 需用户自己的免费账户Key；官方当前列为Preview，支持图像/工具调用，中文攻略质量未测 |
| ASR | Groq `whisper-large-v3-turbo` | 同样需自己的Key；已有OpenAI兼容转录接口具备接入基础，本轮未实际调用 |
| 中文TTS | 本地Kokoro-FastAPI | 无云API账单，仍需下载模型和计算资源；支持中文与`/audio/speech` MP3。本轮未安装、测速或确认听感 |
| 既有搜索 | Tavily免费层 | 仍可使用现有适配，需注册Key；官方列每月1,000 credits，basic搜索每次1credit |

AnySearch操作入口：设置→共享联网搜索→“AnySearch（免费免 Key）”；官方基础地址为`https://api.anysearch.com/v1`，核对后点击保存；切换时已有自定义地址不会自动覆盖。匿名模式不会发送已保存的Tavily Key，不自动清除旧Key；切换回Tavily可继续使用原凭据。按IP限流和每日匿名额度，不把价格页的per-key数字当作匿名额度承诺；额度耗尽明确报错，不读取/保存可能含自动账号密码的402正文，不自动注册或续付费请求。

本轮真实产品探测：独立临时数据根只配置AnySearch，查询公开的`Python official documentation pathlib`，搜索成功5条/3,399.813ms；其中[Python官方正文](https://docs.python.org/3/library/pathlib.html)读取成功，提取57,980字符、保留50,000字符，正确标记`images_unread/body_limit`部分读取。最初记录器误取旧字段，已删去无效字符数并注明；另一次正文读取228.648ms记录正确统计。累计独立httpx搜索1次、产品搜索1次、产品读页2次，没有真实模型/ASR/TTS调用或用户采集。这证明匿名搜索和正文读取可用，不证明完整攻略回答质量、持续稳定性或语音延迟。

另补中文实际使用查询“王者万象棋 新手 攻略”，`zh-CN`匿名搜索成功5条/4,438.54ms，首条为腾讯官方新手指引；产品读取该页成功3,294字符/679.277ms，图片内容未读取，明确保持`partial/images_unread`。此次新增1次搜索、1次取页，没有调用模型或推断攻略正确性；[中文探测记录](evidence/mvp2/free-services/anysearch-chinese-live.json)保留来源与实际结果。全轮累计3次搜索、3次取页，不能计为三次完整带来源模型问答。

Groq接入参数：模型与ASR基础地址均为`https://api.groq.com/openai/v1`，分别在模型设置和语音识别设置中填写对应模型ID及自己的Key。Key只填应用设置或项目专用环境变量，不提交到仓库或聊天。本项目的模型、ASR和TTS仍是独立配置；Groq现有TTS文档只列英语/阿拉伯语，不能据此承诺中文猫娘声音。免费模型8,000 TPM等限额可能限制长攻略冷暖评测，失败应保留。

官方依据：[AnySearch认证](https://www.anysearch.com/docs/auth)、[搜索协议](https://www.anysearch.com/docs/api-endpoints/v1-search)、[Groq免费限额](https://console.groq.com/docs/rate-limits)、[模型](https://console.groq.com/docs/models)、[视觉](https://console.groq.com/docs/vision)、[ASR](https://console.groq.com/docs/speech-to-text)、[TTS语言](https://console.groq.com/docs/text-to-speech)、[Kokoro服务](https://github.com/remsky/Kokoro-FastAPI)、[Tavily额度](https://docs.tavily.com/documentation/api-credits)。

## 本机虚拟机条件

实查为Mac mini M4、10核、24GB内存、arm64；数据卷初查约36GiB空闲，构建期间复查约32GiB，未发现可用外置数据盘。应用目录、Spotlight及PATH没有Parallels/Fusion/UTM/VirtualBox或相应CLI，没有可直接启动的VM。

可以尝试Windows11 ARM运行x64应用的系统仿真；这属于ARM虚拟机证据，不能替代原生Windows11 x64的游戏、驱动与硬件音频验收。[Microsoft仿真说明](https://learn.microsoft.com/en-ca/windows/arm/apps-on-arm-x86-emulation)明确用户态x64应用可以仿真，驱动需要ARM64版本。

| 方案 | 官方当前条件 |
| --- | --- |
| VMware Fusion | 个人/商业/教育免费，支持Apple Silicon的Win11 ARM与DirectX11；下载需Broadcom账户、资料及条款流程 |
| Parallels | 支持M4，14天试用，之后需购买 |
| UTM | 官网/GitHub版免费，但Windows无3D加速，不适合据此判定游戏/Live2D最终性能 |

建议资源为4核/8GB内存/至少64GB虚拟磁盘。Windows官方最低磁盘64GB；薄置备不意味着当前约32GiB实际空闲足够安装、更新、ISO和产物。已向用户询问腾出空间或外置卷；未删除个人文件、下载大镜像、安装VM或接受许可。Windows许可不随免费虚拟机软件自动获得。

官方依据：[Fusion](https://www.vmware.com/products/desktop-hypervisor/workstation-and-fusion)、[下载流程](https://knowledge.broadcom.com/external/article/368667/download-and-license-vmware-desktop-hype.html)、[Parallels试用](https://www.parallels.com/products/desktop/download/)、[UTM限制](https://mac.getutm.app/)、[Windows规格](https://www.microsoft.com/en-us/windows/windows-11-specifications)、[Mac上的Windows许可说明](https://support.microsoft.com/en-us/windows/experience/platform-variants/options-for-using-windows-11-with-mac-computers-with-apple-m1-m2-and-m3-chips)。

当前可以独立推进Windows Server 2022的x64 CI及包验证；虚拟机实际启动继续等待足够存储和所选软件的账户/许可流程。本轮没有Windows11 VM运行结果。
