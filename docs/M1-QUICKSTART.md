# MVP1 桌宠工程预览试用（历史 v0.3）

新版的下载、配置和五项能力操作请查看[Windows下载与CI/CD](CI-RELEASES.md)及[五项能力使用说明](COMPANION-IMPLEMENTATION.md)。下面保留v0.3的历史步骤和验收，不能作为新版功能列表。

本页对应已发布的 [v0.3.0-alpha.1](https://github.com/FrigidCrow/ai-neko/releases/tag/v0.3.0-alpha.1) 和 [MVP1 桌宠目标](MVP1-DESKTOP-PET.md)：白裙 YUI Lolita 猫娘在桌面显示，输入、流式回复和攻略来源出现在她身旁。Electron 宿主管理窗口、托盘和自己的本地后端。**Windows 构建、打包桌面检查和发布 CI 已通过**；下面是此版本的 Windows 试用步骤。历史 `v0.2.0-alpha.1` 仍是网页预览，不包含桌宠。

当前已有文字、会话和搜索实现；语音、M2 长期事实/人格记忆、多角色导入与键鼠控制未实现。实现和验收状态分开记录，以 [REVIEW](../REVIEW.md) 为准。

## 启动与配置

1. 从 [v0.3.0-alpha.1 下载页](https://github.com/FrigidCrow/ai-neko/releases/tag/v0.3.0-alpha.1) 下载 `ai-neko-0.3.0-alpha.1-windows-x64.zip`，不要选择 Source code。完整解压到独立文件夹，保留所有文件和 `resources` 目录。
2. 双击 ZIP 根目录的 `ai-neko.exe`，或使用 `Start ai-neko.cmd`。使用者无需安装 Python、Node 或 uv；不要从压缩包内直接运行，也不要单独搬走 exe。
3. 首次启动阅读 Live2D SDK 条款，选择接受后加载猫娘；拒绝则退出。角色来源与组件署名可在设置中查看，完整记录随包提供。
4. 点击猫娘或她下方的名字打开文字面板，在设置中填写模型配置。未填 Key 时猫娘仍可显示，配置完成后开始聊天。

应用会自动启动自己的后端、选择本机端口。使用托盘的“退出 ai-neko”或设置中的“退出应用”结束程序和后端；收起面板、隐藏桌宠不会退出。若仍有旧网页版服务占用同一数据目录，先正常退出旧版，再启动桌宠。

模型与搜索设置：

- 模型接口：OpenAI 兼容 Chat Completions 的 base URL，例如 `https://api.openai.com/v1`，以及你有权限使用的模型名称/API Key。模型需支持流式返回；攻略模式还需支持 function tools。显式 loopback HTTP 本地模型可不填 Key。
- 搜索接口：默认 `https://api.tavily.com`，填写 Tavily API Key；也可配置兼容其 `/search` 协议的公开 HTTPS 服务。模型 API 不自动附带搜索权限。
- 供应商网络可达性、模型权限和费用由你配置的服务决定。普通聊天不需要搜索 Key；查攻略需要搜索服务和可读取的公开网页。

Windows Key 写入 ai-neko 专属 Windows Credential Manager 项；不同数据根独立。Mac/Linux 开发版只在当前服务进程保存 Key，重启需重新输入或显式设置 `AI_NEKO_MODEL_API_KEY` / `AI_NEKO_SEARCH_API_KEY`。界面存储与配置 JSON 不保存 Key，不加载通用 `.env` 或其他项目资料。Windows 默认数据根为 `%LOCALAPPDATA%\ai-neko`；程序文件与数据分开，不读取或迁移原 N.E.K.O. 数据。

## 桌面操作

- **聊天与收起：** 单击角色或名字展开聊天；收起面板后猫娘继续留在桌面。设置、历史与长回复都在角色旁显示。
- **发送与停止：** Enter 发送，Shift + Enter 换行。回复随模型片段逐步出现；点击“停止回复”结束本轮，已经收到的内容保留。
- **移动与大小：** 拖动角色移动位置；在设置中调节角色大小和置顶。角色右键或“···”打开菜单。
- **历史：** 打开“最近对话”继续已有会话，也可新建话题；重开应用后恢复本地记录。
- **找回与退出：** 托盘可显示/隐藏桌宠、打开聊天、设置或恢复到主屏；需要结束程序时选“退出 ai-neko”。

## 聊天、攻略与来源

“聊聊天”使用模型回答，不进行联网搜索。“查攻略”通过配置的搜索服务查找资料并读取公开正文；建议提供游戏/软件名称、平台、版本和目标。关键条件不足时模型应先询问，不把抓取时间当发布日期。模型表现和攻略质量仍需真实服务验收。

来源卡片区分搜索摘要、已读取正文和无法读取，并给出链接、正文片段、内容日期（未知则未知）与抓取时间。点击来源可在系统浏览器查看原文。来源冲突、无命中、登录墙或读取失败时不能当作已经核实。当前读取公开 HTTP(S) 网页，不接管浏览器登录，也不携带个人 Cookie；外站文字不会获得新工具权限。

模型接收当前输入及有限的本会话上下文；搜索服务只接收模型构造的有限查询词；公开网页抓取不携带个人浏览器 Cookie。不要在查询词中包含不希望发送给搜索方的信息。

停止生成会结算已接受的输入和已显示内容；新回合不会接收旧任务结果。意外退出后保留会话，并把未完成回合标记为中断，不自动重复外部请求。会话日志和 LangGraph 执行 checkpoint 分开保存，两者都不是 M2 的长期事实记忆。

## 源码开发启动

以下命令仅供开发者使用；Windows 下载包的使用者不需要这些工具。在本项目根目录准备 uv 和 Node.js `24.21.0`，然后执行：

```sh
uv sync --locked
npm --prefix desktop ci --no-fund --no-audit
node desktop/vendor/fetch-core.cjs
npm --prefix desktop start
```

Electron 开发入口会使用本项目 `.venv` 启动后端，不使用参考 N.E.K.O. 的环境。构建准备脚本取得固定哈希的 Cubism Core，并验证角色与许可证文件；角色资源已在本项目中。测试入口：

```sh
uv run --locked pytest -q
npm --prefix desktop test
node desktop/vendor/fetch-core.cjs --verify
node scripts/desktop_smoke.cjs --output artifacts/mvp1/desktop-smoke.json
```

开发测试使用本工程独立环境和临时合成数据。`scripts/m0_smoke.py` 是历史命名的全套 Python 源码报告入口；`scripts/package_smoke.py` 检查冻结后端，`scripts/desktop_smoke.cjs` 启动实际 Electron，检查角色、流式界面和宿主行为。合成协议服务不调用真实云模型，不能用结果声称真实攻略正确。

## 验收进度

本轮 [发布 CI 运行 36175618386](https://github.com/FrigidCrow/ai-neko/actions/runs/36175618386) 对源码 `023bee38f29d385bdfc690e2b5c4ea00d4ee5ecb` 的 Windows 构建、测试和发布已通过：Windows Python **302 passed / 0 skipped**，Linux **301 passed / 1 skipped**，桌面宿主 **15 项通过**，冻结后端 **16/16**，实际打包 Electron 桌面 **9/9**，**79 个资源/许可文件**哈希核验通过。Linux 跳过项是 Windows 专属凭据用例，不计通过。桌面检查已包含 YUI 实际窗口、强制结束 GUI 后清理及重启无重放。

真实模型对话和真实搜索验收目前均为 **0**。仍需单独完成：同一会话真实模型连续 10 轮；至少 3 次真实公开搜索→读取正文→带来源答案，并逐项核对平台、版本、步骤和来源；p95 性能验收。Windows 11 x64 真机另测启动、中文路径、透明角色、拖动/缩放、托盘、多屏找回、配置凭据重开、原版共存与退出清理。Windows Server CI 和 macOS 实验不能代替这些验收；未执行项保留 Pending。


显式提供本项目凭据后，可以运行 `uv run --locked python scripts/m1_live.py --model 你的模型名 --model-base-url 你的接口地址 --output artifacts/m1/新的真实验收记录.json`。该入口会产生实际服务调用和费用，仅从两项专属环境变量取 Key；使用临时会话，不读取真实聊天/旧配置。即使调用完成，报告仍标 `manual_review_required`，必须人工核对输出和来源。桌宠中的真实交互还需实际操作核验。

## 实现依据

本轮核对了 [OpenAI Chat Completions](https://developers.openai.com/api/reference/resources/chat)、[Tavily Search](https://docs.tavily.com/documentation/api-reference/endpoint/search)、[HTTPX transport](https://www.python-httpx.org/advanced/transports/) 与 [HTTPCore 网络后端](https://www.encode.io/httpcore/network-backends/)。固定 IP/TLS SNI 行为另以本项目锁版本源码及一次真实 HTTPS 正文读取验证。

Windows 凭据绑定依据 [CredWriteW](https://learn.microsoft.com/en-us/windows/win32/api/wincred/nf-wincred-credwritew)、[CredReadW](https://learn.microsoft.com/en-us/windows/win32/api/wincred/nf-wincred-credreadw) 和 [CREDENTIALW](https://learn.microsoft.com/en-us/windows/win32/api/wincred/ns-wincred-credentialw)。合成测试命名空间在 finally 中删除，不向 CI 提供用户 Key。
