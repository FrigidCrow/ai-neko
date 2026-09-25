# M0 Windows 11 x64 取证

此入口验证源码环境中的本机服务与合成 LangGraph 基础。已增加独立的 [CI 包构建/下载/解压 exe 验证流程](CI-RELEASES.md)；本文的源码 smoke 不替代它。Mac 或 Windows Server 结果均不代表 Windows 11 真机通过；窗口、托盘、角色、音频与真实模型不在本次 smoke 范围。

## 执行条件

- Windows 11 x64；独立复制或检出本工程，建议使用含中文和空格的目录。
- 安装 `uv`，由本工程 `.python-version` 选择 Python 3.11 patch 版本，依赖按 `uv.lock` 安装到本工程 `.venv`。
- 不复制 N.E.K.O 的配置、凭据、数据或虚拟环境；不需要设置模型 Key。
- 每个运行实例使用测试临时目录。本入口不会读写默认的个人数据根；退出后保留的证据仅含版本、摘要和测试结果。

在工程目录执行：

```powershell
uv sync --locked
uv run --locked python scripts/m0_smoke.py --output artifacts/m0/windows-smoke.json
```

也可执行等效脚本：

```powershell
.\scripts\windows-m0.ps1 -Output artifacts/m0/windows-smoke.json
```

若组织策略禁止运行 PowerShell 脚本，使用上方两条命令，不修改全局执行策略。

## 证据与判定

生成的 JSON 记录操作系统、CPU 架构、Python 版本、锁文件 SHA256、锁定与实际安装的依赖版本、Git commit（存在时）、源码清单摘要及每个测试的 JUnit 结果。源码含未提交文件时显示 `git_dirty: true`；不存在首个 commit 时保留 `git_commit: null`，使用源码清单摘要确定本次代码，不能声称有发布构建来源。

`status: PASS` 要求 pytest 成功、至少一项通过、无跳过、Python 与项目固定版本相同、已安装依赖与 lock 一致且测试期间源码未变化。有平台条件跳过且其余检查通过时记为 `PARTIAL`，报告列出被跳过的测试；失败记为 `FAILED`。两者均返回非零退出码，不能记为完整通过。`scope.windows_11_x64_execution` 只有在 Windows 11 x64 的 64 位 Python 上实际执行并全部通过时才是 `PASS`。`real_model_tests` 固定为 0，表示仅使用合成测试。机器上未安装的平台条件依赖记录为 `installed: null`，不冒称已验证。此报告是开发运行证据，不是 exe/ZIP 构建产物。

测试覆盖：非法数据根、中文空格路径、端口占用、同根第二实例、两根同时运行、HTTP 令牌、WebSocket Origin 与首帧鉴权、鉴权超时、退出清理、强制结束后重开与令牌更新，以及本阶段图/checkpoint 的独立测试。

失败时报告保留测试名和状态，不保存异常局部变量、原始日志或 JUnit 文本，避免意外保存令牌。使用 `uv run --locked pytest -q` 在本机查看具体错误和跳过原因；不要公开包含 `runtime/connection.json` 的目录。记录错误与未完成项目后，修复并重跑，不能把失败或跳过记为完成。输出必须是 `.json`，已存在的文件只有确认为本工具生成的 M0 证据才可覆盖；源码和普通文档目录禁止作为输出位置。

## 本阶段仍需单独记录

- 实际 Windows 11 x64 的此份 JSON 与执行日期；未运行时状态保持 Pending。
- 若原版 N.E.K.O 同时运行，手动记录它在 smoke 前后的进程与功能状态；自动测试的两个 ai-neko 数据根共存不等于原版共存实测。
- Windows Credential Manager 的实际存取与删除验证、窗口/托盘/音频/桌宠、打包及升级分别归对应后续验收。本次不声称这些通过。

报告路径只指向本工程证据目录；发布或上传证据需遵循用户另行授权。
