# Windows 下载与 GitHub CI/CD

本次发布目标为 [v0.4.0-alpha.1](https://github.com/FrigidCrow/ai-neko/releases/tag/v0.4.0-alpha.1)，提供可见白裙YUI猫娘、流式聊天、可编辑人格、当轮桌面视觉、按键语音输入/输出、共享联网查询与本地长期记忆。发布须通过版本tag对应的全部必需CI检查；真实服务和Windows11游戏真机验收单独记录。

## 下载和运行

- 在版本页选择 **`ai-neko-0.4.0-alpha.1-windows-x64.zip`**。GitHub自动生成的Source code是源码，不是应用。
- 完整解压到独立程序文件夹，保留根目录`ai-neko.exe`、`resources`和其他文件。包内含Electron、Python、猫娘及运行依赖，无需另装Python、Node或uv。
- 双击根目录`ai-neko.exe`或`Start ai-neko.cmd`。首次阅读并选择是否接受Live2D条款；接受后显示猫娘，点击她打开聊天。在猫娘设置中配置模型、搜索与ASR/TTS。观察画面需要支持图片的模型。
- 先选择具体窗口或屏幕，再启用观察并核对预览。每个新问题取新图；点击“说话”开始录音，再点一次提交。默认按需联网，兼容模型共用一份搜索配置；无搜索Key仍可普通聊天。
- 长期记忆可以明确保存、查看依据、纠正、遗忘及管理快照。自动整理默认关闭，开启后使用所配置模型，可能产生服务费用；快照不包含服务凭据或全部应用数据。
- 收起聊天面板保留桌宠；托盘可找回、恢复到主屏和退出。退出会结束自己的后端。`Check foundation.cmd`只运行合成基础自检；服务启动/停止脚本仅供诊断，不要对同一数据根同时运行多个入口。
- 默认数据根为`%LOCALAPPDATA%\ai-neko`，可通过`AI_NEKO_DATA_DIR`设置独立绝对路径。程序目录和数据根分开；不读取或迁移原N.E.K.O.配置、数据、凭据。更新时退出旧程序，将新版解压到新文件夹，保留现有数据根；不要把程序解压到数据根内。

详细功能与边界见[五项能力使用说明](COMPANION-IMPLEMENTATION.md)。[版本列表](https://github.com/FrigidCrow/ai-neko/releases)保留历史包：0.3.0-alpha.1为基础桌宠版，0.2.0-alpha.1为网页预览，0.1.0-alpha.1为基础诊断版。

## 文件与校验

Release提供应用ZIP、`build-info.json`、`SHA256SUMS.txt`、`package-smoke.json`、`desktop-smoke.json`、`companion-smoke.json`、两平台源码报告和桌面截图。在PowerShell执行：

```powershell
Get-FileHash .\ai-neko-0.4.0-alpha.1-windows-x64.zip -Algorithm SHA256
```

将结果与`SHA256SUMS.txt`比较。`build-info.json`记录源码commit、版本、锁文件、构建环境及许可摘要。YUI素材和N.E.K.O.记忆组件的许可、NOTICE及来源记录随包提供，详见[素材记录](MVP1-ASSETS.md)和[记忆复用](MEMORY-REUSE.md)。本版尚未签名。

## 流水线

[workflow](../.github/workflows/windows-preview.yml)使用固定action commit、uv 0.11.8、Node.js 24.21.0、Python锁版本和`desktop/package-lock.json`，Electron锁定44.4.5。

1. push、PR和手动运行在Ubuntu 24.04及Windows Server 2022执行依赖锁安装、素材/许可检查、桌面宿主单元测试、Python格式和合成测试。失败或非预期跳过阻止后续；Linux的Windows凭据专属用例可跳过，报告保留PARTIAL并另列ci_gate。
2. 两平台通过后，在Windows原生构建PyInstaller后端并与Electron、角色及许可证组装。根目录`ai-neko.exe`是桌面入口，后端在`resources/backend/ai-neko.exe`，前端在`resources/app/`。
3. 对同一ZIP解压后的真实程序检查鉴权、中文路径、实例/端口、退出、图恢复和流式聊天；随后执行桌宠基线及人格/记忆/视觉/语音闭环。测试使用合成资料和服务，不读取用户麦克风、桌面或凭据。
4. 普通构建上传带源码commit的Actions开发产物，保留14天，通常需登录GitHub。版本tag在所有必需检查通过后自动创建新的prerelease，附包、报告、截图和摘要。只有发布job具有`contents: write`权限。

版本标签为`v<基础版本>-<预发布后缀>`，本次为`v0.4.0-alpha.1`。Python/Electron及锁文件基础版统一为0.4.0；构建版本保留完整alpha后缀。已发布版本不覆盖，后续修复使用新tag。稳定发布、签名、自动升级与完整迁移仍需后续验收。

## 验证记录

发布前五项能力CI [36230897663](https://github.com/FrigidCrow/ai-neko/actions/runs/36230897663)已通过：Windows576项/0跳过、Linux575项/1平台跳过，宿主58项、冻结后端16项、实际桌宠9项、陪伴闭环18项。此记录是发布前基线；本次0.4.0版本tag构建与Release下载核对完成后追加对应证据。

Windows Server CI不等于Windows11用户真机。真实人格/游戏建议质量、实际游戏截图识别、麦克风/音色、三次完整联网问答、记忆模型正确使用及20次取消p95仍待验收。免按键、多角色、主动陪伴与长期运行不是本次发布的已完成能力。各阶段保持[PLAN](PLAN.md)所列状态，实际命令与结果见[WORKLOG](../WORKLOG.md)及[REVIEW](../REVIEW.md)。
