# M0 开发运行说明

M0 是隔离与运行基础。当前可运行本机连接探针与合成 LangGraph；没有聊天页面或真实模型回答。测试和默认启动均不读取 N.E.K.O 的配置、数据或凭据。

## 1. 同步和验收

在 `/Users/frigidcrow/Dev/ai-neko` 或 Windows 的独立工程目录中执行：

```sh
uv sync --locked
uv run --locked pytest -q
uv run --locked python scripts/m0_smoke.py --output artifacts/m0/local-smoke.json
```

`.python-version` 固定 Python `3.11.15`，`uv.lock` 固定全部运行/测试依赖。直接运行依赖为 FastAPI `0.141.1`、Uvicorn `0.54.0`、websockets `16.1.1`、filelock `3.32.7`、LangGraph `1.2.12`、SQLite checkpointer `3.1.1`。本项目 `.venv` 独立生成。测试使用临时目录和合成输入；真实模型调用为 0。

报告记录源码文件摘要、Git commit（尚无提交时为 null）、系统/CPU/Python、锁定与安装版本、各测试结果；它不包含连接令牌或模型 Key。Windows 的准备条件和通过标准见 [WINDOWS-M0](WINDOWS-M0.md)。Node/前端版本目前仅完成元数据核查，尚无产品前端安装/构建结果，见 [复用审计](M0-REUSE-AUDIT.md)。

## 2. 最小本机服务

```sh
uv run --locked ai-neko paths
uv run --locked ai-neko serve
```

第一条只显示拟用目录；第二条显式初始化本项目目录并保持前台运行。按 Ctrl+C 正常退出，或在另一终端执行：

```sh
uv run --locked ai-neko stop
```

默认 Windows 数据根 `%LOCALAPPDATA%\ai-neko`，Mac 为 `~/Library/Application Support/ai-neko`。也可使用 `AI_NEKO_DATA_DIR` 或 `--data-dir` 明确指定新的绝对专属目录；`serve/stop/paths` 都支持同一参数。禁止使用原工程、原版数据目录、非空未标识目录或用户主目录；已有数据根由 `.ai-neko.json` 标记应用身份和格式版本。

子目录为 `config/`、`memory/`、`checkpoints/`、`logs/`、`backups/`、`runtime/`、`assets/`。M0 的 memory、backups、assets 仅建目录，不代表长期记忆、备份恢复或角色功能已实现。

服务只监听 `127.0.0.1`。默认端口 0 由操作系统分配；可显式使用 `--port` 或 `AI_NEKO_PORT`。指定端口被占用则失败，不扫描其他应用端口。同数据根第二实例退出码为 3，端口绑定失败为 4，路径/配置错误为 2。多个独立数据根可共存；本阶段单实例锁作用域是数据根。

运行地址和随机会话令牌保存在 `runtime/connection.json`；它属于当前操作系统用户的本机连接资料，不应复制或公开。POSIX 文件设为 0600，Windows 依赖用户数据目录 ACL，ACL 实效仍须 Windows 验证。服务不把 token/Key 写到 stdout 或日志。正常退出删除自己的连接文件；强制结束后可能保留旧文件，下次取得锁后重写新 instance ID/token。应用不会按旧 PID 杀其他进程。

## 3. 连接契约

M0 没有公开的图执行 HTTP 接口；本机 API 只验证生命周期和连接边界。

| 入口 | 契约 |
| --- | --- |
| `GET /health` | `Authorization: Bearer <runtime token>`；返回 `app_id/status/version/stage` |
| `POST /shutdown` | 同样校验 token；202 表示已接受退出请求，进程结束及 runtime 文件清理需另确认 |
| `/ws` | Host 为 `127.0.0.1`，Origin 必须等于 descriptor 的 `allowed_origin`；禁止 query 参数携带 token |
| WS 首帧 | 5 秒内发送 `{"type":"auth","token":"..."}`，成功返回 `ready`；失败关闭码 1008 |
| WS 探针 | 鉴权后 `{"type":"ping"}` 返回 `pong`；不是聊天消息 |

HTTP 与 WS 拒绝非本机 Host；WS 同时校验来源与令牌。图的 scope 校验负责防止后端混用会话，不能替代身份认证。未来 M1 的网络入口必须从可信本机身份/角色配置构造 scope，不接受客户端任意声称的 user ID。

## 4. 重启恢复实验

[合成图说明](m0-graph-notes.md) 提供逐进程的 run → get → resume → history 命令和 Python API。图有两个节点、一条条件边；每次命令结束就关闭解释器，文件 checkpoint 和所属映射保留在本项目 checkpoints 目录。

一个外部 thread ID 被不同用户/角色复用时会得到不同内部 UUID。get/history/run/resume/delete 都验证三元组及指定 checkpoint；越界调用拒绝，旧 checkpoint 不能覆盖最新状态。所有操作显式禁用环境中意外开启的 LangSmith tracing。

图暂停只是等待合成确认。M1 的模型流、Runtime 取消与结算、M2 的事实记忆和 M4 的停声都尚未实现；不要用本实验推断它们通过。

## 5. 模型、凭据与桌面决策

首个模型协议选定 OpenAI 兼容 Chat Completions，M1 再实现 ModelAdapter。`config/app.json` 只保存协议和占位配置；M0 不发模型请求。开发期只识别显式 `AI_NEKO_MODEL_API_KEY`，不加载全局 OpenAI Key 或其他工程 `.env`，不持久保存凭据。

Windows 后续采用独立 `ai-neko/model/<profile_id>` 的 Credential Manager 适配器；当前只是有依据的选型，尚未调用系统凭据 API。桌面采用本项目最小 Electron 宿主，UI/媒体/记忆的提取与许可证边界见复用审计。

本机连接实现参考 [FastAPI WebSockets](https://fastapi.tiangolo.com/advanced/websockets/) 和 [filelock](https://py-filelock.readthedocs.io/en/latest/)；实际兼容结果以本项目锁版本测试为准。
