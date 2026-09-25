# Windows 下载与 GitHub CI/CD

本页记录 `0.3.0` [MVP1 桌宠](MVP1-DESKTOP-PET.md) 的 Windows 包与流水线。当前代码已实现 Electron 宿主、透明 YUI Lolita 猫娘、伴随文字聊天和查询来源，并已扩展 Windows 构建与实际桌面测试步骤。**本轮 Windows CI、ZIP 和 GitHub Release 尚待执行**；流水线已经写好不代表产物已经构建或通过。

桌宠包提供文字流式回复、停止生成、本地会话恢复、模型配置、Tavily 搜索、公开正文读取与来源卡片，以及拖动、角色大小、置顶、托盘找回和退出。M2 长期事实/人格记忆与语音仍未实现。每次发布必须通过两平台合成测试、冻结后端探针和实际 Electron 检查；真实服务质量和 Windows 11 真机结果另外记录。

[版本列表](https://github.com/FrigidCrow/ai-neko/releases)保留旧版。`v0.1.0-alpha.1` 是 M0 诊断包，见 [历史核对](evidence/m0/release-v0.1.0-alpha.1-verification.json)。已发布的 [v0.2.0-alpha.1](https://github.com/FrigidCrow/ai-neko/releases/tag/v0.2.0-alpha.1) 是网页工程预览，仍会打开浏览器，见 [历史下载核对](evidence/m1/release-v0.2.0-alpha.1-verification.json)。这两个版本都不是本轮桌宠包。

## 下载和运行

- 版本下载：[GitHub Releases](https://github.com/FrigidCrow/ai-neko/releases)。待本轮预览发布后，选择 `0.3.0` 对应版本中的 `ai-neko-版本-windows-x64.zip`，不是 GitHub 自动生成的 Source code。
- 开发构建：[GitHub Actions](https://github.com/FrigidCrow/ai-neko/actions/workflows/windows-preview.yml)。成功运行下的 `ai-neko-windows-x64` artifact 保留 14 天；通常需要登录 GitHub。解开 artifact 后，里面的 ZIP 才是应用包。
- 将应用 ZIP 完整解压到独立文件夹；保留根目录 `ai-neko.exe`、`resources` 及其他运行文件。包内包含 Electron、Python、猫娘与依赖，使用者无需安装 Python、Node 或 uv。
- 双击 `Check foundation.cmd` 运行合成基础自检。`status: passed` 表示包内图恢复和隔离检查通过；临时测试资料随后清除。
- 双击根目录 **`ai-neko.exe`**，或 `Start ai-neko.cmd`。首次启动阅读并选择是否接受 Live2D SDK 条款；接受后加载猫娘，点击她展开文字面板。宿主自动管理自己的后端，不需手动启动服务。
- 关闭聊天面板后猫娘仍在桌面；托盘可以找回、恢复到主屏或退出。退出应用会结束其后端。`Start service.cmd` / `Stop service.cmd` 仅用于后端诊断，不要与桌宠对同一数据目录同时启动。
- 默认数据根 `%LOCALAPPDATA%\ai-neko`，可用专属环境变量 `AI_NEKO_DATA_DIR` 设置绝对路径。不会读取或迁移原 N.E.K.O. 的配置、数据、凭据或服务。详细操作见 [M1 试用](M1-QUICKSTART.md)。

本轮打包未接入代码签名。发布配置会附 `build-info.json`、`SHA256SUMS.txt`、`package-smoke.json`、`desktop-smoke.json` 和源码测试报告。可在 PowerShell 执行 `Get-FileHash .\ai-neko-版本-windows-x64.zip -Algorithm SHA256` 与清单比较。YUI 来源与逐文件哈希、N.E.K.O. 通知和 Live2D SDK 独立许可随包提供，见 [资源记录](MVP1-ASSETS.md)。

## 流水线和版本

[workflow](../.github/workflows/windows-preview.yml) 使用固定 action commit、uv `0.11.8`、Node.js `24.21.0`、Python 锁版本和 `desktop/package-lock.json`。当前锁定 Electron `44.4.5`。本轮配置的执行顺序为：

1. push、PR、手动运行：Ubuntu 24.04 和 Windows Server 2022 x64 同步 Python 与 Node 锁文件，取得固定哈希的 Cubism Core，核验角色/许可资源，运行宿主单元测试、Python 格式检查与合成测试。失败或非预期跳过阻止构建；Linux 的 Windows 凭据专属用例可跳过，报告保留 PARTIAL 并另列 ci_gate，不把跳过计为通过。
2. 两平台测试通过后，在 Windows 原生构建 PyInstaller onedir 后端，与锁定的 Electron 分发、宿主代码、猫娘资源和许可证一起组装。根目录 `ai-neko.exe` 是桌面入口；后端位于 `resources/backend/ai-neko.exe`，界面与资源位于 `resources/app/`。构建再次验证素材及许可证哈希。
3. 将 ZIP 解到含中文和空格的临时路径，使用包内后端验证鉴权、端口/实例冲突、退出清理、图恢复、隔离及 M1 API、合成协议流式/ACK/取消、完整回复、会话重开和攻略失败路径。应用子进程不使用源码路径、用户凭据或开发环境 PATH。
4. 对同一个解压包运行实际 Electron 检查：首次条款、真实角色像素与截图、渲染沙箱及 IPC 边界、伴随聊天、流式和取消、设置/历史与宿主退出等。测试使用本地合成模型，报告与截图单列；这一步仍不验证真实云模型或真实搜索质量。
5. 所有必需检查通过后，普通构建提供带 commit 的开发版 artifact；版本 tag 自动创建新的 GitHub prerelease。只有发布 job 具有 `contents: write`，测试/PR 不接收用户模型或搜索 Key。

tag 格式为 `v<pyproject 版本>` 加可选预发布后缀，例如本轮可用 `v0.3.0-alpha.1`。这个示例不表示该 tag 已发布。基础版本必须与 `pyproject.toml` 一致；构建清单记录 `app_version`、`release_version`、源码 commit、Python/Node 锁文件、Electron/后端摘要及素材 manifest。同一版本不可覆盖，已有 Release 会导致发布步骤失败；修复使用新 tag。稳定发布和签名尚未接入。

维护者发布示例（在已检查的目标 commit 上执行；这两条不是下载用户的安装步骤）：

```sh
git tag v0.3.0-alpha.1 <已经核对的完整commit>
git push origin v0.3.0-alpha.1
```

发布失败先看 Actions job 和脱敏 evidence；不要把 token、connection.json、个人配置/数据库或原始凭据日志传到 GitHub。保留历史 Release 供选版下载，替换程序目录时保留独立数据根；自动升级与迁移/恢复仍属于 M6 验收。

## 验证边界

本轮已记录 macOS 上 Python **291 passed / 1 skipped**、宿主 **12 项通过**、**79 个资源/许可文件核验通过**，并已实际显示 YUI；**本轮 Windows CI 与发布尚待执行**。历史网页包的通过数字不代表新桌宠包已通过。

即使 Windows CI 全部通过，也不等于干净 Windows 11 真机验收完成：runner 是装有开发工具的 Windows Server。需另外检查 Windows 11 的透明窗口、角色拖动/缩放、托盘、多屏、凭据重开、原版共存、独立数据根和退出清理。真实模型连续 10 轮、至少 3 次真实搜索/正文/带来源回答及其质量审核也仍待完成，不计入合成 CI PASS。音频与 M2 长期记忆尚未实现。

开发源码取证方法见 [Windows M0 取证](WINDOWS-M0.md)；每次远端运行、下载校验和发布结果以 [REVIEW](../REVIEW.md) 为准。
