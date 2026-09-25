# Windows 下载与 GitHub CI/CD

M1 的 `0.2.0` 预览线提供本地文字页面、会话历史/取消、模型配置、Tavily 搜索、公开正文读取与来源卡片。每次发布必须通过 Windows/Linux 合成测试及解压 exe 探针；这些结果与真实服务质量、Windows 11 真机分开。

[版本列表](https://github.com/FrigidCrow/ai-neko/releases)保留旧版。`v0.1.0-alpha.1` 是历史 M0 诊断包，没有聊天页面；对应 [历史核对](evidence/m0/release-v0.1.0-alpha.1-verification.json)。M1 的实际发布结果记 REVIEW，不把计划发布写成已完成。

## 下载和运行

- 版本下载：[GitHub Releases](https://github.com/FrigidCrow/ai-neko/releases)。选择对应预发布版本中的 `ai-neko-版本-windows-x64.zip`，不是 GitHub 自动生成的 Source code。
- 开发构建：[GitHub Actions](https://github.com/FrigidCrow/ai-neko/actions/workflows/windows-preview.yml)。成功运行下的 `ai-neko-windows-x64` artifact 保留 14 天；通常需要登录 GitHub。解开 artifact 后，里面的 ZIP 才是应用包。
- 将应用 ZIP 完整解压到独立文件夹；保留 `ai-neko.exe` 和 `_internal` 的相对位置。无需自行安装 Python、Node 或 uv。
- 双击 `Check foundation.cmd` 运行合成基础自检。`status: passed` 表示包内图恢复和隔离检查通过；临时测试资料随后清除。
- 双击 `Start ai-neko.cmd` 启动服务并打开聊天页面。`Start service.cmd` 保留无浏览器诊断入口，`Stop service.cmd` 退出服务。默认数据根 `%LOCALAPPDATA%\ai-neko`，可用专属环境变量 `AI_NEKO_DATA_DIR` 设置绝对路径。不会读取或迁移原 N.E.K.O 资料。

当前包未做代码签名。每版附 `build-info.json`、`SHA256SUMS.txt`、`package-smoke.json` 和源码测试报告。可在 PowerShell 执行 `Get-FileHash .\ai-neko-版本-windows-x64.zip -Algorithm SHA256` 与清单比较；出现下载、启动或测试失败时，记录版本和错误，不记为通过。

## 流水线和版本

[workflow](../.github/workflows/windows-preview.yml) 使用固定 action commit、uv 版本与 `.python-version`：

1. push、PR、手动运行：Ubuntu 24.04 和 Windows Server 2022 x64 同步 `uv.lock`，检查格式并运行合成测试。失败或跳过阻止后续构建，始终尝试保存脱敏 JSON 证据。
2. 两平台测试通过后，在 Windows 原生 PyInstaller onedir 构建。ZIP 含 Python、所需运行依赖与许可证、程序入口和来源清单。
3. 解压到含中文和空格的临时路径，使用包内 exe 实测路径、HTTP/WS 鉴权、端口冲突、第二实例、退出清理、跨新进程图暂停/恢复和身份隔离。应用子进程清除 Python 源码路径和配置/Key 环境，PATH 仅保留系统目录；不借用开发环境启动应用。
4. 普通构建提供带 commit 的开发版 artifact；版本 tag 经同一套检查后自动创建 GitHub prerelease。只有发布 job 具有 `contents: write`，测试/PR 不接收模型或搜索 Key。

tag 格式为 `v<pyproject 版本>` 加可选预发布后缀，如 `v0.2.0-alpha.1`。基础版本必须与 `pyproject.toml` 一致；构建清单分别记录 `app_version` 和 `release_version`。同一版本不可覆盖，已有 Release 会导致发布步骤失败；修复使用新版本 tag。稳定发布和签名尚未接入。

维护者发布示例（在已检查的目标 commit 上执行；这两条不是下载用户的安装步骤）：

```sh
git tag v0.1.0-alpha.1 <已经核对的完整commit>
git push origin v0.1.0-alpha.1
```

发布失败先看 Actions job 和脱敏 evidence；不要把 token、connection.json、个人配置/数据库或原始凭据日志传到 GitHub。保留历史 Release 供选版下载，替换程序目录时保留独立数据根；自动升级与迁移/恢复仍属于 M6 验收。

## 验证边界

CI 包测试不是“干净 Windows 11 真机已通过”的证据：runner 仍是安装过开发工具的 Windows Server。清空应用 PATH 并调用冻结 exe 验证了不借用源码解释器的运行链，但 Windows 11 交互、ACL/junction、无开发工具机器、原版共存、桌面和音频仍需实际执行。开发源码测试方法见 [Windows M0 取证](WINDOWS-M0.md)；每次远端运行和发布结果见 [REVIEW](../REVIEW.md)。
