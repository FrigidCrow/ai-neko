# M0 合成图验证记录

日期：2026-09-25。状态：**Mac 上 37 项确定性测试通过；Windows 11 x64 尚未执行**。本模块没有模型、工具、音频或长期记忆实现。

## 实际交付

- `src/ai_neko/graph/service.py`：`prepare → 条件边 → respond / END`。空白输入直接结束；普通输入输出合成文本；`pause=True` 在 `respond` 中等待 `interrupt()`，由 `Command(resume=...)` 恢复。
- `get_stream_writer()` 发出 `prepared`、`text_delta` 和 `generation_done`；`graph.stream(..., stream_mode="custom")` 实际消费这些事件。调用方可使用 `on_event` 逐个消费，返回结果亦保留本次事件数组。
- `checkpoints/graph.sqlite` 使用文件 `SqliteSaver`；`checkpoints/scopes.sqlite` 保存所属关系；`checkpoints/.graph.lock` 排斥其他进程同时打开同一组数据库。锁文件可留存，锁释放由操作系统和 `filelock` 完成。
- 不接受原始 LangGraph config、namespace 或任意内部线程配置。`Scope(user_id, character_id, external_thread_id)` 映射到 UUID4 句柄；所有五个状态接口均先检查完整所属关系，再访问 checkpoint。
- `tracing_context(enabled=False)` 覆盖所有图操作，避免开发者 shell 中全局 LangSmith tracing 设置自动上传内容。导入和构造函数不打开数据库；进入上下文才打开已验证目录中的专属文件。
- checkpoint 目录拒绝符号链接和 Windows reparse point/junction；数据库、SQLite sidecar 和锁文件在打开前统一检查，拒绝符号链接、reparse point、硬链接及特殊文件，避免修改其他位置的文件。应用数据根和目录所有权由 `initialize_data_root` 校验。此检查不构成针对同一操作系统用户持续恶意替换路径的竞态防护。

## 接口

```python
from ai_neko.config.paths import initialize_data_root
from ai_neko.graph import GraphService, Scope

paths = initialize_data_root("/absolute/path/to/new-ai-neko-demo-data")
scope = Scope("synthetic-user", "cat-a", "demo-session")
with GraphService(paths.checkpoints) as service:
    handle = service.open_thread(scope)
    paused = service.run(scope, handle, "synthetic input", pause=True)
    resumed = service.resume(
        scope, handle, "synthetic confirmation",
        checkpoint_id=paused["checkpoint_id"],
    )
```

| 方法 | 行为 |
| --- | --- |
| `open_thread(scope, create=True)` | 创建/读取该 scope 的稳定内部句柄；`create=False` 时不创建缺失映射 |
| `get(scope, handle, checkpoint_id=None)` | 读取最新状态，或读取该线程中指定的 checkpoint |
| `history(scope, handle, checkpoint_id=None, limit=50)` | 新到旧的有界历史；指定 checkpoint 时作为排他的 before 游标 |
| `run(scope, handle, text, pause=False, checkpoint_id=None, on_event=None)` | 执行新合成输入；存在待恢复工作则拒绝覆盖 |
| `resume(scope, handle, response, checkpoint_id=None, on_event=None)` | 只恢复当前正在等待外部输入的中断 |
| `delete(scope, handle, checkpoint_id=None)` | 删除该线程的 checkpoint/writes 后撤销所属映射；重新创建同 scope 得到新句柄 |

`run/resume/delete` 的可选 `checkpoint_id` 表示预期的当前版本；旧版本拒绝，避免无意覆盖或恢复历史分支。每种入口都拒绝另一用户/角色/会话的句柄和 checkpoint。没有 checkpoint 的新映射可读取空状态。错误不透露其他 scope 的所属字段或内容。

这里的 `Scope` 必须由受信任调用方建立，它不承担身份认证。本阶段 CLI 由本机操作员显式提供合成身份；未来 HTTP/WS 适配不得直接相信用户任意提交的身份字段。M0 没有暴露图 HTTP API。

## 可运行 CLI

在项目根目录执行。每条命令启动独立 Python 进程；相同三元组自动找回内部句柄。

```bash
uv run --locked python -m ai_neko.graph --data-root /absolute/path/to/new-ai-neko-demo-data --user synthetic-user --character cat-a --thread demo-session run --text "restart evidence" --pause
uv run --locked python -m ai_neko.graph --data-root /absolute/path/to/new-ai-neko-demo-data --user synthetic-user --character cat-a --thread demo-session get
uv run --locked python -m ai_neko.graph --data-root /absolute/path/to/new-ai-neko-demo-data --user synthetic-user --character cat-a --thread demo-session resume --response "after process exit"
uv run --locked python -m ai_neko.graph --data-root /absolute/path/to/new-ai-neko-demo-data --user synthetic-user --character cat-a --thread demo-session history
```

`run` 和 `resume` 可加 `--stream` 逐行输出自定义事件，末行输出完整结果。全局可选 `--handle` 与 `--checkpoint-id` 要放在子命令前。`delete` 删除当前明确指定 scope 的测试线程。示例中的绝对目录需替换为本机新建的专属测试位置。

Windows PowerShell 可使用独立临时目录；下列命令**尚未在 Windows 执行**：

```powershell
$graphDemoRoot = Join-Path $env:TEMP ("ai-neko-graph-" + [guid]::NewGuid())
uv run --locked python -m ai_neko.graph --data-root $graphDemoRoot --user synthetic-user --character cat-a --thread demo-session run --text "restart evidence" --pause
uv run --locked python -m ai_neko.graph --data-root $graphDemoRoot --user synthetic-user --character cat-a --thread demo-session get
uv run --locked python -m ai_neko.graph --data-root $graphDemoRoot --user synthetic-user --character cat-a --thread demo-session resume --response "after process exit"
uv run --locked pytest tests/test_graph.py -q
```

## 实际运行的验证

工作目录：`/Users/frigidcrow/Dev/ai-neko`。运行环境：`macOS-26.6.2-arm64-arm-64bit`，Python `3.11.15`。实际锁版本：LangGraph `1.2.12`，langgraph-checkpoint `4.2.0`，langgraph-checkpoint-sqlite `3.1.1`，LangSmith `0.14.0`，filelock `3.32.7`。

| 实际命令 | 结果 |
| --- | --- |
| `uv run --locked pytest tests/test_graph.py -q` | 第一轮 22 passed，3.45 秒 |
| `uv run --locked ruff check src/ai_neko/graph tests/test_graph.py` | All checks passed |
| `uv run --locked pytest tests/test_graph.py -q` | 补充文件隔离与生命周期用例后，27 passed，2.49 秒 |
| `uv run --locked pytest tests/test_graph.py -q` | 独立审查发现硬链接越界后修复并补充验证，37 passed，2.81 秒 |
| `uv run --locked python -c 'import platform; from importlib.metadata import version; print(platform.platform()); print(platform.python_version()); print({name: version(name) for name in ("langgraph", "langgraph-checkpoint", "langgraph-checkpoint-sqlite", "langsmith", "filelock")})'` | 输出上列实际环境与版本 |

测试证据位于 `tests/test_graph.py`：

- `test_fresh_process_recovers_paused_graph_and_scope_mapping` 连续运行四个独立解释器：暂停 → 读取 → 恢复 → 再读，并检查暂停的 checkpoint/interrupt 和恢复后的 checkpoint。映射从磁盘恢复。
- 15 个参数化组合覆盖三个不同 scope（用户、角色、外部会话）× 五种操作；每项同时拒绝外部句柄、外部 checkpoint、把 checkpoint 充作句柄的输入，并确认合法线程未被改变。
- 验证真实自定义事件、条件边空输入分支、重复恢复拒绝、旧版本覆盖/删除拒绝、删除后的行清除和句柄撤销。
- 独立子进程尝试打开正在使用的 checkpoint 目录，确认拒绝；关闭后重新打开成功。
- 导入/构造不创建应用文件；开启两个全局 tracing 环境变量同时禁止 socket 连接后仍能运行合成图。
- 4 种数据库/锁重定向路径拒绝且目标文件未修改；Windows 若没有创建符号链接的权限会明确 skip，此项不得计为 Windows 通过。
- 9 种数据库、SQLite sidecar 与锁硬链接均在创建锁或数据库前拒绝，检查外部文件字节完全未改变；另验证 checkpoint 目录符号链接拒绝。Windows reparse/junction 检测已使用公共路径检查，真机结果仍待验证。

模型真实调用：**0**。确定性测试：**37 passed**。Windows 运行、打包、音频停止、长期记忆及副作用恰好一次：**未验证/未实现**。图中断只说明图在等待输入，不表示播放器停止。该同步适配器是 M0 本机验证基线，后续桌面发行仍需并发、崩溃、数据库迁移、增长和恢复验收。

## 官方依据

于 2026-09-25 在线读取，随后以本项目锁版本实际运行验证：

- [Persistence](https://docs.langchain.com/oss/python/langgraph/persistence)：文件 checkpoint 与长期记忆的职责不同。
- [Checkpointers](https://docs.langchain.com/oss/python/langgraph/checkpointers)：按内部 thread ID 获取当前状态和历史。
- [Streaming](https://docs.langchain.com/oss/python/langgraph/streaming)：`get_stream_writer` 与 `stream_mode="custom"`。
- [Interrupts](https://docs.langchain.com/oss/python/langgraph/interrupts)：持久暂停、`Command(resume=...)` 及恢复时节点重入。
- [SqliteSaver](https://reference.langchain.com/python/langgraph.checkpoint.sqlite/SqliteSaver)：本机轻量同步存储的适用边界。
- [from_conn_string](https://reference.langchain.com/python/langgraph.checkpoint.sqlite/SqliteSaver/from_conn_string)：文件数据库与上下文关闭方式。
- [delete_thread](https://reference.langchain.com/python/langgraph.checkpoint.sqlite/SqliteSaver/delete_thread)：清除指定内部线程的 checkpoint 与 writes。
