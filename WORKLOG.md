# ai-neko 工作记录

## 2026-09-25 — 独立工程与计划（更名前的历史记录）

用户明确要求单独建立项目，使用 LangGraph，具体实现参照既有 N.E.K.O 教程和代码。沿用 Windows 11 x64 优先、记忆本地保存/允许云模型，以及复用已有界面/语音/角色能力的方向；新工程独立于 N.E.K.O fork 和 Personal OS。

本轮采用 engineering-software-architect skill 组织职责、设计取舍和验收。只创建工程与计划；没有实施应用、安装依赖、复制第三方代码/素材、启动模型或迁移资料。

实际动作：

- 确认 `/Users/frigidcrow/Dev/langgraph-companion` 原先不存在，检查父目录规则后创建该目录与 `docs/`。
- 执行 `git init -b codex/initial-plan /Users/frigidcrow/Dev/langgraph-companion`，创建独立 `.git`；没有配置 remote、提交或推送。
- 创建 README、AGENTS、gitignore、PLAN、ARCHITECTURE、REVIEW 和本记录；另由独立工作者整理教程与源码映射及 SHA256 清单。
- 读取参考仓库当前源 HEAD `90ccf79c95e80f899b9bf3395fa8cd9a9bfe29be`、工作树状态、构建入口与已有教材。教程是未提交工作树内容，来源快照与源码版本分别记录。
- 读取 LangGraph 官方 persistence、checkpointers、streaming、interrupts 文档，确认 checkpoint 与长期记忆、图暂停与媒体打断的边界。
- 执行独立桌面仓库 `gh repo view` 查询，当前账号返回无法解析；保留条件方案，不把原版安装包替换后端视为已验证复用。
- 调用 Codex `list_projects`，确认新目录尚未在侧栏登记；工具发现未找到目录登记能力。工程磁盘创建不依赖侧栏登记。

设计中明确单一对话编排、单一长期记忆权威、回合归属、取消、来源版本及隔离。第一段实现是文字与记忆；完整路线继续覆盖桌面、语音、视觉、主动行为和 Windows 分发。

收尾独立规划评审由 `/root/review_l15_final` 完成，指出两个会影响接口落地的问题：checkpoint 身份与内部 ID 绑定不够明确、图取消后部分回合结算缺少责任方。已修入 ARCHITECTURE 和 PLAN，明确所有状态入口校验、Runtime 独立持久收尾、幂等与崩溃恢复，并补提交前/中/后取消验收；复核后 PASS。参考映射内部引用和路径检查亦通过，独立评审未运行产品或重审全部原始源码。

Root 执行内联 `python3` 标准库检查：逐个检查本地 Markdown 链接、源码行号、表格列数、代码围栏、尾随空白；对所有新 Markdown 执行 `git diff --no-index --check -- /dev/null <文件>`，覆盖未跟踪文件；解析参考清单并重算 206 份来源 SHA256，核对 10 组/24 课及独立复核锚点；核对 PLAN/REVIEW 各七项 M0–M6 均 Pending。检查结果写入 [初始化验证记录](docs/validation-initial.json)，仅支持文档与引用正确性。

参考映射作者另行检查 113 个本地链接、206 个文件指纹与 24 个复核锚点。清单包含 84 个 tracked-clean 与 122 个 untracked 文件；`build_frontend.bat` 因 checkout 的 CRLF 规则与 commit 原始字节不同，清单单列为 `checkout-crlf-only`，不误报源码改动。生成清单的首轮脚本曾因组标题匹配断言失败而未写文件，修正后重新完整生成并检查通过。

执行环境 Darwin arm64，文档检查使用系统 Python 3.9.6；产品拟用 Python 3.11 的依赖组合尚未安装验证。Git 根、分支、独立 `.git` 与空 remote 均已核对；参考 N.E.K.O 的 Git 状态和 tracked diff SHA256 与本轮开始相同。源码未改动，生产代码未导入；没有安装依赖、读取用户运行数据、调用模型、构建 Windows 包或提交/推送。

P0 仅工程和计划完成。后续从 M0 的隔离、版本与复用接口验证开始，所有产品阶段继续 Pending。

## 2026-09-25 — 改名 ai-neko、技能调研与 20 张图

用户要求将新项目改名 `ai-neko`，调查流程图及其他图形 skills，并为计划和每个模块提供图解。先更新 PLAN 的 P1 范围与验收，再制作本轮交付；仍未进入产品实现。

实际动作与决定：

- 使用 Python `Path.rename` 将 `/Users/frigidcrow/Dev/langgraph-companion` 整体改为 `/Users/frigidcrow/Dev/ai-neko`，保留独立 `.git`。当前名称、拟定应用 ID/显示名与数据根统一为 `ai-neko`，配置覆盖名为 `AI_NEKO_DATA_DIR`，未来包路径为 `src/ai_neko/`。历史初始化命令保留旧名称，原验证快照移动为 `docs/validation-initial.json`，不伪改已发生记录。
- 原参考目录仍为 `/Users/frigidcrow/Dev/neko-companion`；教程与源文件引用保持原路径。只更新 reference-manifest 的 project 元数据，不改 206 份来源指纹。
- 调查 6 个 skills：visualize、engineering-software-architect、insert-mermaid-diagrams、Excalidraw Diagram Skill、design-visual-storyteller、graphify。graphify 只读相关节，其余阅读范围见 DIAGRAM-SKILLS。采用已安装 visualize 的静态选图规则与架构分层思路；两个外部 skills 仅阅读，未安装、未执行其脚本、未上传项目资料。
- 新建 20 份 Mermaid 图源与 20 份 SVG，覆盖计划、总架构、完整回合、API、对话图、Runtime 状态、取消、模型、工具、checkpoint、记忆写读/关系/遗忘、语音、视觉主动、角色界面、隔离、桌面启动、Windows 交付和教程迁入。17 行覆盖表对应所有规划职责；三份 captions 提供输入/处理/输出、失败边界与课程。
- 创建离线 HTML 图册、Markdown 图册、SVG 大图与可编辑图源；提供桌面目录、手机目录、窄屏横向阅读与大图入口。本地阅读无 CDN 和网络请求，不需要安装运行工具。
- 文档工具单独位于 `docs/diagrams/tools/`。执行 `npm view @mermaid-js/mermaid-cli version engines --json`，固定 CLI 12.0.0；`npm install --ignore-scripts --no-fund --no-audit` 安装文档依赖与 lockfile。使用现有 Chrome，未下载浏览器，未安装产品 LangGraph/Python 依赖。环境为 Darwin arm64、Node 25.9.0、Python 3.9.6。

实际验证命令（除最后一条外在图册 tools 目录）：

```sh
npm run render
npm run build
npm run check
python3 docs/diagrams/tools/validate-docs.py
```

最后一条在 ai-neko 根目录运行。渲染、图册生成和检查有重复执行，因为修复了实际发现：11 图 ER 别名声明语法、14 图 Mermaid 保留词、SVG 百分比尺寸导致的放大、浏览器滚动定位选择器，以及生成 Markdown 尾部空行。源仓库状态指纹统一使用原始验证的 `git status --porcelain=v1 -z` 字节格式，避免换行格式不同造成误报。

独立审查由未参与图源编写的 `/root/diagram_skills_research` 执行，核对 20 图、20 captions 与 PLAN/ARCHITECTURE：单一编排、记忆权威、scope 校验、独立取消结算、生成/播放完成分开和桌面复用条件一致。反馈中的图册相对引用已统一为 `../REFERENCES.md`；记忆版本校验与权威写入明确处于同一受控提交边界，不虚构跨所有文件的全局事务。未发现阻断语义问题。该审查不是产品测试，也不是用户学习掌握的证明。

全部 20 图实际渲染，生成物与图源 SHA256 可追踪。禁网浏览器检查 1440px 和 390px 两个视口：20 图均加载、目录锚点有效、页面无横向溢出；20 SVG 的文字未超出画布。Root 查看桌面/手机图册、系统总览、状态图、取消时序、ER 图与语音视图截图，修正图形尺寸后复测。复杂图窄屏使用内部横向滚动，不把文字缩成不可读小字。

验证证据见 `docs/validation.json`、`docs/diagrams/render-validation.json`、`docs/diagrams/browser-validation.json` 及 `docs/diagrams/evidence/`。206 来源文件重新计算 SHA256；原仓库 tracked diff 和 NUL 分隔状态指纹保持初始化快照值。旧工程目录已不存在，新 Git 根为 ai-neko，分支仍 `codex/initial-plan`；无远程、提交或推送。Codex 侧栏登记不在本轮声称完成的范围内。

P1 仅完成改名、技能调研与图解文档；M0–M6 产品阶段继续 Pending。Windows 启动、模型调用、长期记忆与语音行为均未在新项目执行。

## 2026-09-25 — M0 独立运行基础

用户表示开始第一个阶段，按 PLAN 从 M0 开始；先更新本阶段任务、验收和 AGENTS 的授权范围，再实施源码。M1–M6 未开始。所有开发命令均从 `/Users/frigidcrow/Dev/ai-neko` 执行（只读来源命令使用显式 `git -C`）。没有新增远程、commit、push、发布或全局记忆。

Root 实现路径/配置/CLI/本机服务与总验收；`/root/m0_graph` 实现 scoped 合成图及测试；`/root/m0_acceptance` 编写独立服务子进程验收与跨平台报告入口；`/root/m0_reuse` 完成来源审计后独立审查生产代码。文件职责分开，未借用参考工程配置、数据、凭据、端口或虚拟环境。

环境实际检查：`uv --version` 为 0.11.8；`uv python list --only-installed` 与 `uv python find 3.11.15` 确认工具链；Mac Darwin arm64，测试 Python 3.11.15。现有文档工具 Node 25.9.0 / npm 11.12.1 不等于已测试产品候选 Node 24.21.0。来源 HEAD 仍为 `90ccf79c95e80f899b9bf3395fa8cd9a9bfe29be`。

实际实现/验证命令：

```sh
uv lock
uv sync --locked
uv run --locked ai-neko paths
uv run --locked pytest tests/test_config.py -q
uv run --locked pytest tests/test_graph.py -q
uv run --locked pytest -q tests/test_server_process.py
uv run --locked ruff check src tests scripts
uv run --locked ruff format src tests scripts
uv lock --check
uv pip check
uv run --locked python scripts/m0_smoke.py --output docs/evidence/m0/macos-smoke.json
```

首次解析兼容范围后把直接依赖固定 patch，再更新 `uv.lock`。独立 `.venv` 当前平台安装 53 个分发包（含 editable ai-neko），lock 共 54 项；未安装的平台条件包在 JSON 单列。图操作显式禁用 tracing，传递依赖 LangSmith SDK 存在不表示使用其云服务。没有加载模型 Key 或发起真实模型调用。

子进程验收实际启动 `python -m ai_neko serve --data-dir <temporary absolute root> --port 0`，使用真实 HTTP/WS 请求，再用 `/shutdown` 或 `python -m ai_neko stop --data-dir ...` 清理；强制结束案例只结束测试自己创建的进程。没有在默认个人数据根持久启动服务；`paths` 只显示位置。图测试用四个新解释器执行 `python -m ai_neko.graph ...` 的 run/get/resume/get；同一磁盘 checkpoint 与 scope 映射跨进程恢复。

发现与修复：

- import 探针最初把 `socket.socket` 类换成函数，造成标准库 SSL 导入失败；改为 audit hook 拒绝 bind/connect 后通过。这是测试探针问题。
- 服务测试第一轮 14 passed / 1 failed：测试错误地把 shutdown 的 202 Accepted 当失败；修正并补充边界后 18 passed。
- 独立审查复现硬链接截断外部文件、宽松配置接受错误类型/外部凭据名两个 P2。共享路径检查拒绝 symlink/reparse、多个硬链接及特殊文件；严格检查版本、端口、模型字段、URL 和 credential namespace。新增回归后，审查者用原复现确认修复。
- 首轮完整 smoke 为 109 passed；收尾检查补 stop 禁 HTTP redirect 和真实本机 302 回归用例后，重新完整运行，最终 **110 passed / 0 failed / 0 errors / 0 skipped**；真实模型测试 **0**。

来源审计实际执行 `gh repo view Project-N-E-K-O/N.E.K.O.-PC --json nameWithOwner,url,visibility,licenseInfo,defaultBranchRef`，退出 1（无法解析）；另用 `git -C ... rev-parse HEAD` / `git show <HEAD>:<path>`、`rg`、`sed`、Python 文本/AST/SHA256 和公开官方元数据核查。33 个选定文件与 HEAD 一致；`docs/m0-reuse-manifest.json` 的 `imports=[]`。未导入原模块或复制素材。桌面选定 M3 最小 Electron 宿主，凭据选定独立 WinVaultKeyring 命名空间（M1 实现）。

文档维护命令：

```sh
node docs/diagrams/tools/build-atlas.mjs
node docs/diagrams/tools/check-atlas.mjs
python3 docs/diagrams/tools/validate-docs.py
```

图源/SVG 保留 P1 设计基线，仅修改入口过时的“所有阶段 Pending”。验证器排除 `.venv` 等生成目录，改查 PLAN/REVIEW 实际状态一致，继续核对来源与图指纹。这组文档检查不代替产品 smoke。

最终环境、运行耗时、源码与 uv.lock SHA256、每项结果见 [Mac smoke](docs/evidence/m0/macos-smoke.json)。尚无首个 Git commit，使用文件清单摘要定位源码，未虚构构建来源。M0 总体 Partial；Windows 11 x64、junction/ACL、前端安装/构建尚无运行证据，Windows 入口见 [WINDOWS-M0](docs/WINDOWS-M0.md)。

收尾结果：Ruff 检查和格式检查通过，14 个 Python 文件符合格式；对最终 smoke 的 source.manifest 逐项重算 SHA256，源码未变化（tree `824bbb62c84636219cf423c332ac5e94dc8cf4d5beaaef9f8901d23518aed967`）。图册 1440/390px 两视口的 20 图均加载、无页面溢出/坏锚点；文档检查 273 本地链接、206 来源指纹和 PLAN/REVIEW 阶段一致性 PASS。参考工程 tracked diff 与状态指纹仍与初始化基线一致。

## 2026-09-25 — 首次上传 GitHub

用户明确指定 `git@github.com:FrigidCrow/ai-neko.git` 并要求上传。已执行 `git ls-remote --symref git@github.com:FrigidCrow/ai-neko.git HEAD 'refs/heads/*'` 和 `gh repo view FrigidCrow/ai-neko --json nameWithOwner,url,visibility,defaultBranchRef,isEmpty`，确认远端公开、为空且 SSH 可访问。保留当前 `codex/initial-plan` 分支，不覆盖任何既有远端历史。

已执行 `git remote add origin git@github.com:FrigidCrow/ai-neko.git`。先在 PLAN 登记上传范围，再更新文档验证器：允许用户指定的 origin SSH/HTTPS 地址，并将实际 fetch/push URLs 写入报告。此前 P0/P1/M0 中无 remote、无 commit 的文字是历史运行记录，保留原样；已有 smoke 的 `git_commit: null` 也是运行当时的真实值。

独立只读审计 `/root/upload_audit` 检查 Git 实际纳入清单：103 个文件，约 3.01 MiB，最大文件约 410 KiB；无凭据、环境文件、数据库、虚拟环境、node_modules 或运行目录。凭据模式仅命中明确的 `example.invalid` 合成测试输入。9 张 PNG 均为已有图解证据。Root 重算 smoke source.manifest，生产代码、测试、依赖锁与 110 passed 记录保持一致，无需重复运行未改动的产品测试。

本轮上传准备命令包含 `git status --short --branch`、`git remote -v`、`git ls-files --others --exclude-standard`、`git check-ignore ...`、`python3 docs/diagrams/tools/validate-docs.py`；初次 `git log -3 --oneline` 因尚无 commit 返回 128，属于预期初始状态。随后将暂存、提交并推送；最终远端提交一致性在上传后记录。

上传结果：文档检查 PASS，103 个文件暂存后 `git diff --cached --check` 通过；执行 `git commit -m "feat: establish isolated ai-neko M0 foundation"` 得到首个提交 `7d77223f7375445988c0767bf6eb5273d7336066`。`git push -u origin codex/initial-plan` 成功，建立 upstream；`git ls-remote origin refs/heads/codex/initial-plan` 与 `git rev-parse HEAD` 一致，首次上传后工作区干净。此段上传结果及 REVIEW/PLAN 状态作为后续文档提交同步到同一分支；不改变已验证产品源码。

## 2026-09-25 — 查攻略优先，暂不做键鼠控制

用户明确暂时不需要键鼠控制，很需要查攻略。先调整 PLAN：真实搜索与公开正文读取从后期扩展提前为 M1 必需能力，步骤回答附可核对来源并处理平台/版本差异；M5 仅补图片与查询联动。键鼠代操作、控制其他软件及个人浏览器账号操作排除在当前范围；桌宠手动拖拽、本应用按钮、语音和用户主动提供的图片保留。没有开始 M1 实现、选择付费查询供应商或调用真实搜索服务。

同步 ARCHITECTURE 的 search_web/read_web_page 契约、README、AGENTS、REFERENCES 和 reference-manifest 的 R07/R08 规划字段，保留所有来源文件哈希。更新 00 路线、08 查询工具、14 视觉联动的 Mermaid 源与 captions，重建 Markdown/HTML 图册。图解不再把副作用执行框架当作首版工具重点。

实际检查命令：

```sh
git status --short --branch
git diff -- src tests scripts pyproject.toml uv.lock .python-version
node docs/diagrams/tools/render.mjs
node docs/diagrams/tools/build-atlas.mjs
node docs/diagrams/tools/check-atlas.mjs
python3 docs/diagrams/tools/validate-docs.py
git diff --check
```

另用现有 `launchBrowser` / Puppeteer 对本地 08 SVG 截图至 `docs/diagrams/evidence/08-tools.png`，并实际查看。图渲染/检查在修正缺口输出与首个里程碑归属后重新执行；最终 20 图渲染、两视口离线加载/锚点/边界检查 PASS。

独立只读工作者 `/root/search_scope_plan_review` 检查范围与验收一致性，指出 M2 被标首个体验以及 R01 仍要求外部副作用执行两个残留；修改并复核后 PASS。产品源码、测试、运行依赖没有差异，因此未重跑产品测试；110 passed 只引用先前 M0 记录，旧 smoke 中包括 AGENTS 的摘要保留历史实值。本次文档变更沿用用户此前授权的 origin 与 `codex/initial-plan` 分支同步；不发布产品，也不把规划通过记为搜索功能可用。

## 2026-09-25 — GitHub CI/CD 与 Windows M0 便携预发布

用户要求先收尾基础阶段，并让 M1 可以在 Windows 下载试用。本轮先更新 PLAN 第 11 节与 M1 验收：M0 交付基础诊断包和 CI，M1 再交付本地文字网页、模型配置和带来源攻略查询。读取 engineering-devops-automator 技能，采用同一 workflow 的跨平台测试 → Windows 原生构建 → 解压 exe 验收 → 版本 tag 预发布；不接模型 Key 或消息通知服务。

本地新增 PyInstaller 6.22.3 build 依赖组并执行 `uv lock`、`uv sync --locked --group build`。打包器仅接受 Windows x64，Mac 调用已验证明确拒绝；诊断使用临时合成数据。增加打包/探针辅助测试，构建入口、许可来源和 workflow 均纳入来源摘要。独立审查发现 setup-python 没有 3.11.15 Windows 下载项，已改固定 uv 0.11.8 的 managed Python，并补充固定上游 commit/hash 的 CPython 许可证后备文本。

首轮全目录 Ruff 命中了旧文档校验脚本的既有格式；CI 将检查范围明确为产品 `src tests scripts packaging`。首轮完整 smoke 为 144 passed / 1 failed，失败是许可证清单新加 CPython 后原测试预期尚未更新；不作为通过证据。后续修复、最终测试与远端结果在下方追加。

修复后执行 `uv run --locked ruff check src tests scripts packaging`、`uv run --locked ruff format --check src tests scripts packaging`、`uv run --locked python scripts/m0_smoke.py --output artifacts/m0/macos-ci-preflight.json`，最终 **147 passed / 0 failed / 0 errors / 0 skipped**，真实模型 0；20 个 Python 文件格式通过，源码测试期间未变化。证据复制至 `docs/evidence/m0/macos-ci-preflight.json`，保留执行时 commit/dirty 实值。许可证测试 17 项包含 Python stdlib 许可证优先和 exact-version fallback；本机 uv Python 的许可证实际位于 stdlib，并非完全缺失。Windows 真实运行留待下方 CI 记录。
