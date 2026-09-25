# 图册维护

这是文档工具目录，不是 ai-neko 的运行依赖。直接打开上一级 `index.html` 即可离线阅读，无需安装 Node、Python 或 Mermaid。SVG、CSS 和说明均保存在项目内；教程/源码引用仍依赖原本机参考目录。

编辑顺序：更新 `docs/PLAN.md` / `ARCHITECTURE.md` → 修改 `../src/*.mmd` 与三份 `*-captions.json` → 渲染 → 生成图册 → 验证 → 更新 REVIEW / WORKLOG。`../index.html`、`../../DIAGRAMS.md`、`../atlas-manifest.json` 由脚本生成，不单独维护正文。

文档工具固定 `@mermaid-js/mermaid-cli` 12.0.0，锁文件固定其依赖；该版本要求 Node >=22.13.0。验证使用 Mac 的 Node 25.9.0 与本机 Chrome。工具重建在 Windows 上尚未实际运行，也不能据此声称产品支持 Windows。

在本目录执行：

```sh
npm ci --ignore-scripts --no-fund --no-audit
npm run render
npm run build
npm run check
```

安装忽略生命周期脚本，不下载新的浏览器；使用已安装的 Chrome/Chromium。默认查找常见位置，非默认位置可设置环境变量 `PUPPETEER_EXECUTABLE_PATH`。不要修改系统 HOME 或 CODEX_HOME。浏览器以独立临时配置运行，不访问个人浏览器会话。

在项目根可执行 `python3 docs/diagrams/tools/validate-docs.py` 复核文档链接、源参考哈希、身份更名和产品阶段状态。该检查依赖本机原参考工程，不属于可搬移图册的打开条件。

`render` 逐一解析并生成 SVG，记录源文件与产物 SHA256。`build` 生成离线 HTML、Markdown 与模块覆盖表。`check` 在禁用网络的浏览器中验证本地文件显示、目录锚点与大/小视口，并记录截图。

本项目使用的是 Mermaid CLI 官方导出 API，已锁版本；升级时须重新核对 API 并完整渲染。SVG 不嵌入系统字体，使用本地中文字体回退。没有添加远程 CDN、图床或上传流程。

全部图为设计。计划尚无可靠工时估计，因此路线图表达依赖，不编造甘特图日期。ER 图表示概念关系，M2 才确定最终存储字段与布局。必要时拆图优先于缩小文字。
