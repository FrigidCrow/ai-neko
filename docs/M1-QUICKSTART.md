# M1 文字与查攻略试用

M1 是本地网页加 Windows 便携后端。M0 诊断仍保留；桌宠、托盘、语音、长期事实记忆和键鼠控制尚未实现。当前证据和未完成项以 [REVIEW](../REVIEW.md) 为准。

## 启动与配置

从 [GitHub Releases](https://github.com/FrigidCrow/ai-neko/releases) 下载 `0.2.0` 版本线的 Windows x64 ZIP，完整解压后双击 `Start ai-neko.cmd`。服务自动分配 loopback 端口并打开浏览器；不要分开移动 `_internal` 或在压缩包中直接运行。关闭浏览器不等于退出服务，请使用页面退出按钮或 `Stop service.cmd`。再次启动前先退出旧实例。

在设置中配置：

- 模型接口：OpenAI 兼容 Chat Completions 的 base URL，例如 `https://api.openai.com/v1`，以及你有权限使用的模型名称/API Key。模型需支持流式返回；攻略模式还需支持 function tools。显式 loopback HTTP 本地模型可不填 Key。
- 搜索接口：默认 `https://api.tavily.com`，填写 Tavily API Key；也可配置兼容其 `/search` 协议的公开 HTTPS 服务。模型 API 不自动附带搜索权限。
- 供应商网络可达性、模型权限和费用由你配置的服务决定。普通聊天不需要搜索 Key；查攻略需要搜索服务和可读取的公开网页。

Windows Key 写入 ai-neko 专属 Windows Credential Manager 项；不同数据根独立。Mac/Linux 开发版只在当前服务进程保存 Key，重启需重新输入或显式设置 `AI_NEKO_MODEL_API_KEY` / `AI_NEKO_SEARCH_API_KEY`。页面与配置 JSON 不保存 Key，不加载通用 `.env` 或其他项目资料。

## 聊天、攻略与来源

新建会话后发送文字；普通聊天逐步显示回复。查攻略模式应提供游戏/软件名称、平台、版本和目标。关键条件不足时模型应先询问，不把抓取时间当发布日期。模型质量仍需真实服务验收。

来源卡片区分搜索摘要、已读取正文和无法读取，并给出链接、正文片段、内容日期（未知则未知）与抓取时间。来源冲突、无命中、登录墙或读取失败时不能当作已经核实。网页不提供浏览器登录、Cookie 或键鼠操作；外站文字不会获得新工具权限。只允许公开 HTTP(S)，每跳重新检查 DNS/地址并固定连接 IP；正文提取为有大小/时间上限的公开可见文本，不是付费/登录内容获取器。

模型接收当前输入及有限的本会话上下文；搜索服务只接收模型构造的有限查询词；公开网页抓取不携带个人浏览器 Cookie。不要在查询词中包含不希望发送给搜索方的信息。

停止生成会结算已接受的输入和已显示内容；新回合不会接收旧任务结果。意外退出后保留会话，并把未完成回合标记为中断，不自动重复外部请求。会话日志和 LangGraph 执行 checkpoint 分开保存，两者都不是 M2 的长期事实记忆。

## 开发与验收

```sh
uv sync --locked
uv run --locked python -m ai_neko start
uv run --locked pytest -q
```

开发使用本工程独立环境；测试用临时合成数据。全套源码报告仍由历史入口 `scripts/m0_smoke.py` 生成，它现在同时收集 M0/M1 测试并明确真实模型数为 0。冻结包检查独立运行包内 exe，通过本地合成 OpenAI 协议服务验证网页/API、流式、取消和重开，不能用这一结果声称真实攻略正确。

真实验收应单独完成：同一会话连续 10 轮；至少 3 次公开搜索→读取正文→带来源答案；逐项对照平台/版本/步骤/来源原文。Windows 11 x64 真机另测启动、中文路径、配置凭据重开、原版共存及退出。未执行的项目保留 Pending，不计入 CI PASS。


显式提供本项目凭据后，可以运行 `uv run --locked python scripts/m1_live.py --model 你的模型名 --model-base-url 你的接口地址 --output artifacts/m1/新的真实验收记录.json`。该入口会产生实际服务调用和费用，仅从两项专属环境变量取 Key；使用临时合成会话，不读取真实聊天/旧配置。即使调用完成，报告仍标 `manual_review_required`，必须人工逐项核对输出和来源，不自动宣布攻略正确。

## 实现依据

本轮核对了 [OpenAI Chat Completions](https://developers.openai.com/api/reference/resources/chat)、[Tavily Search](https://docs.tavily.com/documentation/api-reference/endpoint/search)、[HTTPX transport](https://www.python-httpx.org/advanced/transports/) 与 [HTTPCore 网络后端](https://www.encode.io/httpcore/network-backends/)。固定 IP/TLS SNI 行为另以本项目锁版本源码及一次真实 HTTPS 正文读取验证。

Windows 凭据绑定依据 [CredWriteW](https://learn.microsoft.com/en-us/windows/win32/api/wincred/nf-wincred-credwritew)、[CredReadW](https://learn.microsoft.com/en-us/windows/win32/api/wincred/nf-wincred-credreadw) 和 [CREDENTIALW](https://learn.microsoft.com/en-us/windows/win32/api/wincred/ns-wincred-credentialw)。合成测试命名空间在 finally 中删除，不向 CI 提供用户 Key。
