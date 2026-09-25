# MVP1 猫娘素材与渲染依赖

核查日期：2026-09-26。用户明确要求从只读参考 N.E.K.O. 取猫娘资源。本次只提取角色资源和必要渲染依赖，没有复制用户配置、运行数据、凭据，也没有执行参考工程脚本。

## 实际角色与提取结果

**采用真正的默认猫娘 YUI Lolita。** 初次扫描 `static/` 误选了历史 Live2D 示例 Mao；实际 Electron 截图显示其为橙发巫师少女，不能满足猫娘验收。随后从 `config/character_defaults.py:46` 核实默认值 `yui-lolita`，找到构建时才解压的 `assets/yui-lolita.tar.gz`。Mao 已从本项目移除，不以“去掉帽子”或添加临时猫耳伪装符合要求。

| 项目 | 已核实值 |
| --- | --- |
| 只读参考 checkout | `90ccf79c95e80f899b9bf3395fa8cd9a9bfe29be` |
| 源归档 | `assets/yui-lolita.tar.gz`，11633909 字节 |
| 归档最后修改 commit | `d359d7afeec2b0e97bfaddb23085f0f54d95104d` |
| 归档 SHA256 | `9b24ec87b20e81751808f9f444844cfa678610f7e2d3ef9535da8b5379570e58` |
| 上游加入 YUI Lolita | [PR 2768](https://github.com/Project-N-E-K-O/N.E.K.O/pull/2768)，作者/合并者均 `wehos`，merge `37fa070ac1da7e4df595f7a4e69da4479c6f79df` |
| 当前入口 | `desktop/assets/yui-lolita/yui-lolita.model3.json` |
| 提取范围 | model3 直接引用的模型、纹理、物理、参数说明、表情、动作，共 61 文件；字节未改 |
| 未提取项 | 原包的 VTube Studio 配置、固定挂件配置及未引用资源；没有复制原应用用户数据 |

来源与逐文件哈希见 [manifest](mvp1-assets-manifest.json) 和 [sources.json](../desktop/vendor/sources.json)。资源完整性通过不代表 Windows 真机视觉验收。

## 角色复用依据

YUI 是 N.E.K.O. 自带的第一方默认角色，不是 Live2D 官方示例模型。**不能把 Mao 的 Live2D Free Material License 套用给 YUI。** Mao 独有样本条款与声明已从分发目录移除。

本次工程复用依据是上游仓库 Apache-2.0 LICENSE、NOTICE，以及 `CONTRIBUTING.md:83-85` 的贡献许可声明；该模型由维护者自己提交、合并并作为默认角色分发，未发现 YUI 专属的排除条款。原 LICENSE、NOTICE 及贡献许可摘录随包保留。归档内没有另附 LICENSE；这个事实本身不等于没有许可。

参考 `docs/guide/cost-and-providers.md:18,24` 说明第三方依赖、素材、商标与服务保留自己的条款，不能认为每个模型都由 N.E.K.O. 许可。这是一般提醒，未明确排除 YUI；本次结合维护者的第一方默认模型贡献记录采用仓库声明进行复用。此结论是工程依据推断，不声称已独立证明原始美术的完整权属，也不声称是上游专门给 ai-neko 的授权。若后续发现 YUI 专属条件，应依该条件修正。应用不借用 N.E.K.O. 品牌冒充原版，也不主张猫娘美术为本项目原创。

## 渲染加载契约

桌面协议根目录对应 `desktop/`。依次加载：

1. `vendor/live2dcubismcore.min.js`
2. `vendor/pixi.min.js`
3. `vendor/pixi-unsafe-eval.min.js`
4. `vendor/cubism4.min.js`
5. 项目 renderer，调用 `PIXI.live2d.Live2DModel.from('ai-neko://app/assets/yui-lolita/yui-lolita.model3.json')`。

Pixi 为 `7.4.3`。`@pixi/unsafe-eval@7.4.3` 是支持禁止 `new Function` 环境的 CSP 补丁，**不启用 unsafe-eval**。参考 `index.min.js` 与官方 npm `pixi-live2d-display-lipsyncpatch@0.5.0-ls-6` 同名分发文件哈希相同，但硬性要求旧 Cubism 2 runtime；本项目选同版本原样的 `cubism4.min.js`，不引入无用旧 SDK。

wrapper npm gitHead 为 `c169f7b80a46691ea3e8084e430d119ffdef8afa`，其 Cubism 子模块为 `RaSan147/CubismWebFramework@5e779915650c5564803bc8a14b5688c391c4920c`。保留该版本 framework LICENSE 和 Live2D Open Software 许可，MIT 标签不能覆盖内含框架。

模型动作：`Idle[0..3]`、`happy[0..11]`、`neutral[0..8]`、`sad[0..4]`、`angry[0..7]`、`surprised[0..5]`。表情包括 `neutral_xxy`、`neutral_wuyu`、`neutral_by` 等，详见 model3。`EyeBlink` 参数为 `ParamEyeLOpen` / `ParamEyeROpen`。没有语音输出，不把文字状态动作当作口型实现。

## Core 获取与独立许可

Core 原始文件为 207155 字节，SHA256 `25ae938cb4fe282ce189b357bcc97e603d1e1f7ec78bf04150d401c23cdc792f`。在隔离 Node vm 中提供浏览器基础对象后，`Version.csmGetVersion()` 返回 `83951616`，即 `5.1.0`；这只是版本 API 运行检查，不是视觉渲染测试。参考文件最后修改 commit `0816fd9e7ce70fe3890cfd5af1b53b49782a5053` 的固定 GitHub 文件实际下载成功，字节与用户指定本地参考相同。

官方 `https://cubism.live2d.com/sdk-web/cubismcore/live2dcubismcore.min.js` 当前返回 HTTP 403，未把参考副本称作已和官方下载比对。Core 不单独提交本项目 Git，由 [fetch-core.cjs](../desktop/vendor/fetch-core.cjs) 从参考仓库的固定 commit 获取，最终随完整应用包含。该做法不声称官方 EULA 一概禁止应用源码仓库包含 Redistributable Code。

构建准备：`node desktop/vendor/fetch-core.cjs`；离线检查：`node desktop/vendor/fetch-core.cjs --verify`。下载只接受固定 URL 的 HTTP 200，限制大小与超时、验证固定哈希；不跟随重定向、不自动升级、不覆盖哈希不匹配的已有文件。同时校验模型、依赖与许可文件。

SDK 采用自己的许可，不能被 N.E.K.O. 或本项目根许可覆盖：

- [Core EULA 5.1、5.2](https://www.live2d.com/eula/live2d-proprietary-software-license-agreement_en.html) 允许在具有主要功能的衍生作品中原样包含 Redistributable Code，并要求终端用户同意等效保护条款。首次启动显示 [完整终端条款](../desktop/vendor/licenses/Live2D-END-USER.txt)，接受后再加载角色/Core，拒绝退出。
- [官方 SDK 许可页](https://www.live2d.com/en/sdk/license/) 的 AI/Chatbot 部分先判断 Expandable Applications；非该类的 native AI/chatbot 进入常规计划。Core EULA 2.2 规定 General Users / Small-Scale 条件的豁免，Expandable 不豁免。当前个人项目固定单角色无模型导入，按这些条款判断；不宣称未来任意企业/扩展平台免费免许可。
- 原始许可网页以 `.html.txt` 留存，防止外部脚本作为网页执行；纯文本版本供终端阅读。未联系权利人、购买、申请或替用户提交商业信息。

## 升级与已做检查

模型升级需重选参考 commit/归档，只提取当前固定模型引用资源，重新生成哈希、检查路径引用、保留许可与来源，再做实际角色、动作、点击、缩放测试。Pixi/wrapper 升级使用精确 npm 版本并重审内含 Cubism framework；禁止偷偷改用 latest。

已执行：只读源码默认值/归档/参数核查，官方 SDK 许可阅读，npm 分发文件比对，归档中所需文件逐字节提取、引用完整性与哈希核验，Core 固定副本下载/版本 API 检查，资源 helper 与 manifest 检查。实际 Electron 截图识别到 Mao 选错，已据此改选 YUI。最终猫娘视觉、透明窗口、托盘和 Windows 运行由桌面验收报告单列，资产检查不能代替。
