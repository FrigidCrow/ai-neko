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

独立构建审查补充 Windows Git 行尾问题：fallback 许可证按原始字节校验 SHA256，因此增加 `.gitattributes` 的 `packaging/licenses/* -text`，防止自动 CRLF 转换破坏上游字节；该属性文件纳入源码/构建摘要。针对性 37 项打包/探针测试通过。图册同步 M0 CI/诊断 ZIP、M1 可下载网页试用、M6 长期升级验收；20 图重新 render/build/check，1440/390 离线加载通过，新增 00/18 图截图并目视检查。

远端首跑 `36121055844` 在行尾修复提交推送后被并发策略取消，不记为通过。随后 `ecb911d649221d47e05131d0b9e6fd7d4b941102` 的运行 `36121211813`：Linux 通过，Windows Server 2022 的源码 pytest 达到原 180 秒总限额，退出 124，JUnit 尚未生成，0 项不能解释为通过；构建和发布均被阻止。原始脱敏报告保存 `docs/evidence/m0/windows-ci-timeout.json`。继续增加去参数测试名、阶段、结果和耗时的实时进度，超时保留最后用例；总预算增至 600 秒，不修改 PASS 规则。

许可证进一步按 uv 0.11.8 实际选中的 PBS 20260414 Windows 发行包核查：install_only 主 LICENSE 不含部分原生库通知。补充同版 full archive 的 9 份原文，记录 archive/source/file SHA；构建校验 python311.dll、主 LICENSE 与 OpenSSL/libffi 摘要，避免套错版本许可。实际 Windows archive 字节匹配和复制验证通过，没有在 Mac 执行 Windows 代码。两次 CLI 测试 subprocess 调用补 30 秒上限；19 项打包测试再次通过。

独立审查核实 uv/CPython 官方 launcher 源码，确认 Windows venv 会启动真实 Python 子进程，Popen PID 不等于应用 os.getpid。源服务测试原先强制比较两者，会把健康服务当成旧实例等待超时。现改为启动前快照、要求新 instance_id 与 token，再做鉴权健康检查；保留旧描述符保护。Windows 崩溃/兜底仅用 taskkill /T /F 清理本次启动 PID 的子树，保留有界等待。21 项定向服务测试通过，包含 3 项身份/PID 回归；是否解决远端超时仍以新 CI 为准。

Root 另修包探针的合成环境：清除真实 USERPROFILE 后，为子进程提供临时合成 USERPROFILE/LOCALAPPDATA，满足 Windows Path.home 安全校验，仍不继承个人配置。21 项探针测试通过。进度记录专项 7 项通过，含真实子进程超时、半截 JSON/XML、阶段识别和参数/异常正文脱敏；超时不能把部分进度变为整套通过。

整合后 Mac 完整 smoke 为 **160 passed / 0 failed / 0 errors / 0 skipped**，保存 `docs/evidence/m0/macos-ci-recovery.json`。Windows 原发行通知保留自身 CRLF 字节，`.gitattributes` 同时保留原文空白；入库的 Windows JSON 只将 CRLF 转为 LF，证据字段不变。推送 `d10d6c5e767b5655c10e772a51c14fe0a93dc085` 后，远端运行 `36122354111`：Windows Server 2022 **160 passed，46.328 秒**；Linux **160 passed，35.789 秒**；两者均无失败/错误/跳过，源工作区干净。Windows PyInstaller 原生 EXE 构建成功，但随包许可证检查发现该平台 `ormsgpack==1.12.2` wheel 许可文本未被识别，构建 job 失败，未输出可下载包或发布。正在补齐对应版本来源后重跑。

构建失败进一步查明是 ormsgpack 的 Windows wheel RECORD 使用反斜杠，许可证实际存在；改为规范化分隔符后提取 basename，无需新增 fallback。50 个锁定 Windows/通用 wheel 的来源 hash 与许可文件已只读核查，同类缺项仅已有记录的 langsmith/sqlite-vec。补正/反斜杠端到端复制回归，21 项打包测试通过。构建日志还显示可选导入把开发依赖带入分析，故 build job 改为 `--no-dev --group build`，spec 排除 Pygments/setuptools 等不在运行依赖闭包内的构建/测试工具；实际冻结运行继续由包验收裁定，不只凭静态分析通过。

最终代码 `c745193e4a24f44489d99564aac0e08b4a3fd0dc` 的普通构建 `36123110092` 全通过：Windows/Linux 各 162 passed、0 failed/errors/skipped，包内 exe 13/13 PASS；Windows 50.750 秒、Linux 33.774 秒。先下载 package-evidence 核对来源、全项 PASS、工作区 clean，再执行 `git tag v0.1.0-alpha.1 c745193e4a24f44489d99564aac0e08b4a3fd0dc` 与 `git push origin v0.1.0-alpha.1`。没有覆盖任何既有 tag/Release。

tag 运行 `36123430471` 再次通过全部测试、原生构建和解压 exe 验收，发布 job 用运行期 GitHub token 自动创建 prerelease。实际执行 `gh release view v0.1.0-alpha.1 --repo FrigidCrow/ai-neko --json tagName,isPrerelease,url,assets,publishedAt`、`gh release download v0.1.0-alpha.1 --repo FrigidCrow/ai-neko --dir artifacts/releases/v0.1.0-alpha.1`。6 项资产逐个核对 GitHub digest/size、SHA256SUMS、ZIP 内外构建信息、测试报告的 exe/ZIP SHA、PE AMD64 头与 162/13 项通过；未在 Mac 运行 Windows exe。Release 已于 2026-09-25T10:23:25Z 发布，ZIP 21,419,567 字节，SHA256 `d3aad33aa6e692eda305cb3eac552a7c7f24c889704f8eb60c248722d9cf351d`；tag API 指向同一代码 SHA。

结果写入 `docs/evidence/m0/release-v0.1.0-alpha.1-verification.json`；构建和包检查另存同目录，Windows JSON 入库仅转换行尾。README/PLAN/REVIEW/WINDOWS-M0/CI-RELEASES 同步实际状态。最终交付文档提交使用 `[skip ci]`，仅文档和已验证证据变化；已发布源码、tag 和二进制保持不变，不为这些文字修改再跑同一套产品测试。M0 总体 Partial（Windows 11 真机等仍待），M1 Pending；已交付 CI/CD 和 M0 基础诊断下载版。


## 2026-09-25 — 用户授权 M0 收尾、M1 与 CI/CD

先读 AGENTS、PLAN、ARCHITECTURE 和上次 release 证据，登记 PLAN 第 12 节后实施。使用 engineering-ai-engineer / engineering-devops-automator，网页子任务使用 engineering-frontend-developer。并行 ownership 为 Provider/只读工具/凭据、LangGraph+SessionRuntime、网页；Root 集成 API/启动/打包/CI/证据。没有读取原版凭据或迁入其运行源码/素材；全局记忆未写入。

实际执行（本节为已运行命令，不代表最终 Windows 结论）：

- `uv lock`、`uv sync --locked`：版本升 0.2.0，httpx 0.28.1 显式列为运行依赖，仍共 61 个锁包。
- `uv run --locked pytest -q tests/test_m1_api.py tests/test_server_process.py`：网页 app.js 尚未落盘时有 1 个静态资产 500，其他 23 项通过；网页完成后重跑 M1 API 通过。没有把开发中失败记为通过。
- Provider 分组最终 82 passed / 1 Windows-only skipped；runtime/chat 38 passed。两组均为合成测试，Windows Credential Manager 的真实往返只在 Windows 执行。
- `uv run --locked python scripts/m0_smoke.py --output artifacts/m1/macos-smoke.json`：初次全套 285 passed / 1 skipped，0 failure/error；报告正确为 PARTIAL，但原入口把任何 skip 当成 CI 失败。补充仅允许非 Windows 的明确 vault case 进入 CI 的检查，仍保留 PARTIAL 和真实 skip 计数；其他跳过或 Windows 跳过仍阻止发布，新增精确门控回归。
- 包探针的新增聊天/攻略/取消链先对独立源码子进程执行，3 轮正常：流式 ACK、取消、完整回复、搜索 Key 缺失及私网正文拒绝；外部调用 0。该项仅验证探针逻辑，不称已执行冻结 exe。
- 一次真实公开网页读取 `https://docs.python.org/3/library/asyncio.html` 通过，检验 DNS 固定 IP + TLS SNI；实际模型/搜索调用 0，记录 `artifacts/m1/public-page-read.json`。
- `uv run --locked python scripts/m1_live.py --model not-configured --output artifacts/m1/live-provider-acceptance.json`：退出 2，明确缺少两个本项目 Key，实际外部调用 0；不把缺凭据替换成 fake 通过。
- `uv run --locked ruff check src tests scripts packaging`、`ruff format`、`git diff --check`：根据实际发现修复；最终结果在交付证据补记。

独立审查修复：取消后未发送草稿冻结、逐轮 checkpoint namespace 防迟到覆盖、旧来源编号改为需重新检索、API错误具体提示保留、历史完成但未读事件恢复。根 .gitignore 的 `runtime/` 改为 `/runtime/`，防止新 `src/ai_neko/runtime` 被意外忽略。文档/发布将 M0 与 M1 总体状态及真机/真实服务缺口分别保留。


最终 Mac 后端/源码预检执行 `uv run --locked python scripts/m0_smoke.py --output artifacts/m1/macos-final-smoke.json`：286 passed / 0 failure/error / 1 Windows vault skip，34.215 秒，source_unchanged=true、ci_gate=PASS，status 保留 PARTIAL。随后网页交互修正由浏览器补验，最终发布源码另以 Windows/Linux CI 的 clean commit 为准。`python3 docs/diagrams/tools/validate-docs.py` 通过 206 来源指纹、链接、图解和阶段一致性；原参考仓库 tracked diff/status 未变。公开页与缺凭据报告复制至 docs/evidence/m1，保留原始事实与时间。


网页最终使用 `artifacts/m1-ui/check_ui.py`（本机 Playwright + Chrome 153）检查 14 项，通过真实本地服务及明确 renderer fixture；源码 `app.js` SHA256 `524fa115ca0bc3a5e8785c226acfa9198ef5148a14fdc9d3881c77a0671cb196`。Root 另目视桌面截图。增强脚本曾因测试 harness lambda 捕获可变 argv、旧 DOM 等待条件而失败，修复 harness 后重跑通过，不归为产品通过证据的一部分。报告与四截图复制 docs/evidence/m1。执行 `node --check src/ai_neko/web/app.js` 和最终 Ruff/diff 检查后冻结发布源码。


源码提交 `987d8b0a99d29332bd1972147803dd8af9057e96` 已推送 `origin/codex/initial-plan`，运行 [36126906103](https://github.com/FrigidCrow/ai-neko/actions/runs/36126906103) 全部必要 jobs SUCCESS。实际下载 source/package evidence：Windows 287 passed / 0 skipped（80.922 秒），包含 Credential Manager 原生往返；Linux 286 passed / 1 专属 skip（53.254 秒），无 failure/error，clean source commit 一致；16 项冻结包检查全部 PASS。基于该已验证 SHA 执行 `git tag v0.2.0-alpha.1 987d8b0a99d29332bd1972147803dd8af9057e96` 和 `git push origin v0.2.0-alpha.1`，触发 [tag workflow 36127322450](https://github.com/FrigidCrow/ai-neko/actions/runs/36127322450)。发布完成与实际下载核对在下文补记。


[tag workflow 36127322450](https://github.com/FrigidCrow/ai-neko/actions/runs/36127322450) 已于本轮成功完成测试/构建/解压验证/发布，仍为同一源码 SHA。实际执行 `gh release download v0.2.0-alpha.1 --repo FrigidCrow/ai-neko --dir artifacts/releases/v0.2.0-alpha.1`、`gh api repos/FrigidCrow/ai-neko/releases/tags/v0.2.0-alpha.1`、tag ref 查询，以及 `python3 artifacts/verify-m1-release.py`：6 项资产全部 hash/size 一致，源/ZIP/exe/网页身份对应，Windows 287 / Linux 286+1skip / 包 16 项与报告一致。ZIP 21,541,629 字节，SHA256 `549aec1047932520d5f68f728aea81c2fb7a05750d852f4abd980acdca14d0d5`，发布 UTC 2026-09-25T11:07:04Z。验证报告/构建清单/包报告复制 docs/evidence/m1。

最后仅更新 README/PLAN/REVIEW/CI-RELEASES/WINDOWS-M0 和证据，执行文档/链接/指纹与 diff 检查并以 `[skip ci]` 提交同步；不更改已验证发布源码、tag 或产物，不重复跑未变化的产品测试。M0/M1 Partial 的待验收边界保留，不写全局记忆。

## 2026-09-25 — MVP1 改为可见猫娘桌宠最小闭环（仅规划）

用户明确项目主体为桌面猫娘，要求先规划 MVP1 的桌宠、文字交互和流式输出。使用 product-manager 技能整理产品范围；只读工作者核对现有实现与缺项，图册工作者同步路线。默认建议单一 Live2D 猫娘，已询问表现形式偏好；截至本轮文档编写无回复，按建议标为规划假设，未选定素材/宣称许可通过。未继续先前普通桌面窗口实现设想，未新增产品代码或发布。

新增 `docs/MVP1-DESKTOP-PET.md`：可见角色的用户路径、八项必需范围、A–D 实施关卡和 V01–V09 验收；基础桌宠/单角色/基本托盘前移 M1，M2 长期记忆后置，M3 改为表现扩展。同步 PLAN/README/ARCHITECTURE/REVIEW；旧 alpha.1 试用说明明确仅适用于网页工程预览。现有 release/tag/二进制保持历史实值。

读取官方 Electron custom-window-styles/security 与 Live2D sample 说明，仅用于规划透明/穿透边界、有限桌面权限和素材逐项核查；没有安装 SDK 或导入模型。首个 transparent-window 旧路径无法读取，改用实际可读的 custom-window-styles。

实际执行：

- `git status --short --branch`、定向 `rg`/`sed` 阅读当前计划、审计和实现边界；只读参考仓库，不读取运行资料。
- `npm --prefix docs/diagrams/tools run render`、`npm --prefix docs/diagrams/tools run build`、`npm --prefix docs/diagrams/tools run check`（图册工作者执行）：20 图渲染与离线 1440/390 两视口检查通过。
- `git diff --check`、`python3 docs/diagrams/tools/validate-docs.py`：PASS；206 参考文件、306 本地链接、阶段状态和图源/产物指纹通过，参考仓库 tracked diff/status 未变。

本轮为文档/图册变更，无产品源码、运行依赖、CI 工作流变更，因此不重复运行产品测试。没有新增真实模型、搜索、Windows 11 或桌宠执行证据；M0/M1 保持 Partial，M2–M6 Pending。

最终独立复核反馈已闭环：修正 ARCHITECTURE 的陈旧查询状态，M0-REUSE-AUDIT 加历史阶段映射说明；Root 实际查看 `docs/diagrams/evidence/00-roadmap.png`。再次执行文档验证与 `git diff --check`，仅记录实际文档检查，不改变产品验收状态。

## 2026-09-26 — MVP1 桌宠实现与本地验证

按用户继续实现和从 N.E.K.O 取猫娘的授权，先登记 PLAN 第 14 节，再分工资源/宿主/renderer。使用 engineering-devops-automator 技能落实锁定工具链和失败阻止发布。用户确认最终 YUI 白裙猫娘，外观固定。参考仓库仅只读源码/美术/许可，没有读取其运行配置、资料、Key 或虚拟环境。

实际命令与结果：

- 锁定 Python 3.11.15、Electron 44.4.5、Playwright 1.63.0；`npm --prefix desktop ci`、`node desktop/vendor/fetch-core.cjs --verify`：79 项资源通过。Core 固定来源下载并核验，源码不跟踪 Core，完整应用包包含；YUI 原始 61 文件保留字节。
- `uv run --locked ruff format src tests scripts packaging`、`ruff check`：PASS。`uv run --locked python scripts/m0_smoke.py --output artifacts/mvp1/macos-source-smoke.json`：291 passed、1 明确 Windows-only skip、0 失败/错误；真实调用 0。该报告对应当时工作树，本轮最终 Windows 源码以 CI clean commit 为准。
- `npm --prefix desktop test`：12 passed；`uv run --locked pytest -q tests/test_packaging.py tests/test_package_smoke.py`：44 passed（独立审查者执行）；`tests/test_desktop_backend.py tests/test_server_process.py` 此前 24 passed。
- `node scripts/desktop_smoke.cjs --output artifacts/mvp1/desktop-smoke.json`：最终 9/9 PASS，实际 Electron、合成流模型、独立临时中文数据根；包含正常退出与真实 host 强制终止/后端管道退出/重开无自动请求重放。开发过程中旧等待条件、退出后 Playwright 对象销毁、重启历史加载 race 曾失败，修复后重跑；未将失败运行计入通过。
- `python3 docs/diagrams/tools/validate-docs.py`：文档、20 图和 206 参考指纹通过；`git diff --check` 与新增 JS 语法检查通过。Root 查看正确猫娘和聊天截图；Mac 不执行 Windows exe。

更新 Windows 构建为根 GUI exe、resources/backend 冻结后端及内置 YUI/vendor；新增实际解压 GUI 测试，保留原 16 项后端包检查与原版本 Release。待远端构建/发布结果按实际补记，不写成已完成。

首次桌宠源码 `fe20e171cca18dbde63a920771797e2ad7e97cb7` 的 Windows 292/0skip、Linux 291/1skip 通过，构建和冻结后端 16 项通过；[36170950261](https://github.com/FrigidCrow/ai-neko/actions/runs/36170950261) 的实际桌宠启动失败（Windows native exit 0xC0000005），正确阻止上传已验证包和发布。新增只含合成窗口的独立诊断流水线，对照原生/Playwright、隔离 profile/PATH 和 GPU 标志；取消没有修复的重复主流程 36171710691，保留失败事实。

独立排查发现主进程先异步等待 Python 初始化路径，存在 Electron ready 先发生的明确风险。改为有界同步路径初始化，在首个 await 前设置独立 userData/sessionData；保留 30 秒超时、16KiB 输出上限和身份校验。宿主回归增至 15 项通过，真实路径初始化 228ms；`node scripts/desktop_smoke.cjs --output artifacts/mvp1/desktop-sync-smoke.json` 本地仍 9/9 PASS。此时尚未确认该问题就是 Windows native crash 根因。

Windows 对照 [36172266079](https://github.com/FrigidCrow/ai-neko/actions/runs/36172266079) 实际证明普通 tiny 窗口的原生/Playwright 启动成功；改 profile/PATH 的 tiny 崩溃，禁 GPU 仍失败；同步路径修复后的项目源码在两种环境均能打开许可窗口。报告精简为 [启动对照](docs/evidence/mvp1/windows-startup-diagnostics.json)，诊断 workflow 的 success 只表示报告生成，不把失败 case 写成通过。早期诊断遇到未有界退出的窗口，取消该轮并为自己创建的进程补上限清理后重跑。

随后 [36172265880](https://github.com/FrigidCrow/ai-neko/actions/runs/36172265880) 的原生 Windows 包实际通过前 8 项桌宠检查，已能显示 YUI 并完成流式聊天、停止、设置、正常退出重开与单实例。Root 查看包内实际桌宠流式截图。第 9 项真实强杀 GUI 后仍有后端连接文件，15 秒等待超时；发布继续阻止。这说明 Windows 的继承 stdin EOF 不足以独自保证宿主崩溃收尾，需补一次捕获的原生父进程 HANDLE 监督，不能靠静态 PID 文件或结束同名程序。

已增加 Windows HANDLE 监督：宿主传入本次 PID，后端在建数据/监听之前仅以 SYNCHRONIZE 权限捕获一次 HANDLE 并验活；监控固定内核对象，退出或 wait 失败走同一收尾，停止线程后才释放 HANDLE。正常 EOF 保留，stdin 改为 os.read 避免被阻塞的 daemon 持有缓冲锁。非法/自身 PID、无 pipe 模式与 Windows 缺 PID 均拒绝。独立回归 desktop/server 34 passed，宿主 15 passed；Root 为实际服务监控用例补健康检查就绪条件，重跑 desktop_backend 13 passed（3.11 秒），Ruff 通过；`desktop-handle-smoke.json` 本地实际 Electron 仍 9/9 PASS。Windows native 分支留待最终 CI，不把 Mac 的明确 fake HANDLE 当作原生 Windows 通过。

[36173890266](https://github.com/FrigidCrow/ai-neko/actions/runs/36173890266) 的 Windows 302/0skip 和 Linux 301/1skip 均通过，包含 Windows 原生 HANDLE 真实服务回归；但冻结桌宠仍在强杀后的描述文件清理处失败。进一步核对 [libuv Windows 源码](https://github.com/libuv/libuv/blob/v1.x/src/win/process.c#L65-L91)：默认非 detached 子进程加入宿主关闭即强杀的 job，能够解释后端清理未及运行。Windows 服务启动改为 detached，但保留私有管道、子进程引用和原生父句柄监督，不 unref；正常退出仍等待所属进程结束。验收未放松，仍要求连接描述删除、后端真实退出及重开不重放，并补充无凭据的进程/生命周期事件诊断。15 宿主测试通过，`desktop-detached-smoke.json` 本地仍 9/9 PASS；最终是否修复由下一轮 Windows 包实测判断。

最终源码 `023bee38f29d385bdfc690e2b5c4ea00d4ee5ecb` 的 [36174872767](https://github.com/FrigidCrow/ai-neko/actions/runs/36174872767) 全部必要 jobs SUCCESS：Windows 302 passed/0skip、Linux 301 passed/1专属skip、宿主15、冻结后端16/16、实际桌宠9/9。强杀后 host_gone=true、descriptor_present=false、stopped 事件存在，随后 backend_exit_confirmed=true，重开无额外模型调用；说明此前 job 强杀路径已修复。Root 查看 Windows 包内重开历史截图。

实际下载该 run 的 7 项开发资产与截图，运行 `.venv/bin/python artifacts/verify-mvp1-release.py artifacts/mvp1/dev-023bee3 --mode ci --source 023bee38f29d385bdfc690e2b5c4ea00d4ee5ecb --version 0.3.0-dev.023bee38f29d`：PASS，核对摘要、GUI/后端 PE x64、同一 clean 源码与 build/test 输入、79 文件和 YUI61引用、许可与无运行资料。证据见 [开发包核对](docs/evidence/mvp1/dev-023bee3-verification.json)。随后按原授权对该 SHA 执行 `git tag v0.3.0-alpha.1 023bee38f29d385bdfc690e2b5c4ea00d4ee5ecb` 与 `git push origin v0.3.0-alpha.1`，触发 [36175618386](https://github.com/FrigidCrow/ai-neko/actions/runs/36175618386)；发布完成与最终下载核对另记下文。

最终 tag workflow [36175618386](https://github.com/FrigidCrow/ai-neko/actions/runs/36175618386) 全部必要 jobs SUCCESS，同一源码再次通过 Windows302/0skip、Linux301/1专属skip、宿主15、冻结后端16和实际桌宠9项；`v0.3.0-alpha.1` 于 `2026-09-25T18:51:55Z` 自动发布。实际执行：

```sh
gh release download v0.3.0-alpha.1 --repo FrigidCrow/ai-neko --dir artifacts/releases/v0.3.0-alpha.1
gh api repos/FrigidCrow/ai-neko/releases/tags/v0.3.0-alpha.1 > artifacts/releases/v0.3.0-alpha.1/release-api.json
gh api repos/FrigidCrow/ai-neko/git/ref/tags/v0.3.0-alpha.1 > artifacts/releases/v0.3.0-alpha.1/tag-ref.json
gh run download 36175618386 --name package-evidence --dir artifacts/mvp1/tag-evidence
.venv/bin/python artifacts/verify-mvp1-release.py artifacts/releases/v0.3.0-alpha.1 --source 023bee38f29d385bdfc690e2b5c4ea00d4ee5ecb --version 0.3.0-alpha.1
```

最后一条在复制同run的5张PNG到下载目录后执行，PASS。核对7项GitHub资产digest/size、tag/source/build/report一致性、PE x64、GUI/后端分离入口、96桌面源文件、79导入文件、YUI61文件闭包、57依赖许可通知和无用户数据；截图摘要全部匹配并目视正确白裙猫娘。ZIP191,723,563字节，SHA256 `adadafa71d67f705b2fdf16131889974f17e77f98dc770374931957ef83d26b1`。验证/构建/包报告和Windows截图归档 `docs/evidence/mvp1/`，Mac未执行Windows程序。

本轮最后只更新交付文档及证据；按原授权在文档验证与diff检查后以 `[skip ci]` 提交同步。已发布tag和源码保持不变，不因文档更新重复运行产品测试。桌面预览与CI/CD交付PASS，M0/M1总体Partial；真实服务与Windows11真机等未验收项保留。

最终执行 `python3 docs/diagrams/tools/validate-docs.py > artifacts/mvp1/docs-final-validation.json`：PASS，330本地链接、20图、206参考文件与阶段一致性通过，参考仓库tracked diff/status未变。`git diff --check` 通过；独立文档工作者核对下载页、ZIP名称、启动入口及真实服务/真机边界。

## 2026-09-26 — 人格、视觉与语音陪玩下一步方案

用户提出三个能力和实际游戏提问场景。使用 product-manager 技能将其整理为单一可验收闭环；先询问模型服务/模型名及画面是在本机、模拟器、投屏还是手机。未要求提供Key或读取已有凭据。默认建议按键说话、当轮截图与短句语音，免按键另验游戏音/回声；未将建议当成已确认偏好。

实际执行定向 `rg`、`rg --files`、`sed`、`nl`，核对本工程PLAN/ARCHITECTURE/Runtime与参考人格、选源/图片、采集/播放、TTS基础模块；只读人格工作者核实预设与长期记忆边界。另核对Electron desktopCapturer及MDN getUserMedia官方说明。新建 `docs/NEXT-GAME-COMPANION.md`，同步PLAN第15节、ARCHITECTURE与REVIEW；没有新增产品代码、依赖、素材导入、运行原版或真实API调用，未写全局记忆。文档检查结果在本节续记。

执行 `python3 docs/diagrams/tools/validate-docs.py > artifacts/mvp1/game-companion-plan-validation.json`：PASS，21文档、335本地链接、参考仓库206文件指纹/工作区状态及M0–M6阶段一致性通过。`git diff --check`通过。独立只读审查确认三项需求、未定条件、人格/局内/长期记忆分层及目标值未冒充测量结果；明确A–C即可形成按键版下载交付，后续免按键不阻塞。只有方案文档变化，未重跑产品测试、提交推送或发布新版。

用户补充第4点网络查询打算用DeepSeek。实际检索/打开DeepSeek官方模型能力、Vision、Claude Code联网说明、Anthropic/Responses兼容表、Thinking Mode与Tool Calls。Anthropic直开超时后通过官方搜索结果正文取得兼容表；未把搜索文档中的示例当真实请求。本轮官网已明确Flash原生视觉及不同协议的搜索差异，因此不沿用旧印象称DeepSeek无视觉，也不把兼容Responses等同支持内置web_search。只读工作者核对当前ModelAdapter/Graph/API缺口；Root更新陪玩方案6.1、PLAN与REVIEW。询问官方/第三方渠道，未要求Key；真实服务调用0，未改产品代码或运行参考工程。

执行 `python3 docs/diagrams/tools/validate-docs.py > artifacts/mvp1/deepseek-plan-validation.json`：PASS，21文档/335本地链接与参考指纹、阶段一致性通过；`git diff --check`通过。保留已有未提交规划修改，本轮不提交、推送或发布；新增DeepSeek能力仍为待适配/待真实联调。

用户追问通用联网搜索是否需要逐个模型接入。定向核对当前共享工具/模型适配边界，复用搜索审查工作者独立只读确认；修正NEXT-GAME-COMPANION第6节、PLAN、ARCHITECTURE与REVIEW为应用统一搜索优先。明确当前Tavily Key配置一次、模型共用；原生搜索是可选后端，模型协议仍需兼容。未修改产品代码、替换服务或调用真实API，验证结果续记。

执行 `python3 docs/diagrams/tools/validate-docs.py > artifacts/mvp1/shared-search-plan-validation.json`：PASS，21文档、335本地链接、206参考文件指纹与阶段一致性通过；`git diff --check`通过。独立审查确认当前图尚无不支持工具调用模型的初始检索兜底，该能力仅列为后续方案；没有提交、推送或发布。

## 2026-09-26 — 五项能力实施与本地运行

用户持续目标把人格、视觉、语音、查询和长期记忆全部授权实施；先写PLAN第16节，再并行完成MemoryService/人格、媒体服务、桌面UI，Root集成Runtime/LangGraph/API/协议。用户强调参照N.E.K.O后，记录只读源码commit并实际提取纯检索组件，许可随源码和Windows打包保留。参考项目未运行，未读取其配置/凭据/数据。自动提取可显式开启，使用已配置模型；默认关闭不影响用户明确保存和召回。

实际执行`.venv/bin/pytest -q > artifacts/mvp1/companion-python-tests-final.txt`：457 passed/1 Windows Credential Manager skip，40.03s。`.venv/bin/ruff check src tests scripts packaging`与`ruff format --check`通过。最后添加不含图像字节的回合版本/来源元数据后，运行Runtime/companion integration/adversarial/protocol四文件62项通过（`companion-final-integration.txt`）。

`node --test desktop/tests/*.test.cjs`：28/28；桌面工作者运行`node scripts/desktop_smoke.cjs --output artifacts/mvp1/companion-baseline-smoke.json`：9/9，含实际杀宿主后后端退出；`node desktop/tests/companion.smoke.cjs`最终9/9，含合成窗口、fake麦克风、实际MP3解码/播放/停止、音量口型、独立播放ACK和隐藏面板显示ACK=0。4次合成模型、2次合成ASR、3次合成TTS；真实服务0、用户屏幕/麦克风0。单次停播17ms不能算p95。报告与截图复制至docs/evidence/companion，来源是未提交工作区，旧HEAD不冒充新代码的不可变commit。

核对DeepSeek官方Thinking Mode及Oh My Pi兼容说明后，补官方端点max_tokens与工具往返内部上下文；普通模型继续通用工具。`tests/test_companion_protocol.py`等35项通过，未使用真实账户。直接打开Chat Completions文档曾超时，使用其他可读官方页面核实字段，不把超时当成功。

安全边界回归发现并修复过程及未完成项见REVIEW。新增Windows ZIP的companion smoke门禁与NEKO memory许可证复制测试；本机未运行Windows程序，未发布新版。当前完整目标尚不满足真实服务和Windows验收，保持active。

`python3 docs/diagrams/tools/validate-docs.py`：PASS，24文档、349本地链接、206参考文件指纹与阶段一致性通过；参考仓库tracked diff/status未变。沿用此前对同一仓库上传与CI的授权，将通过检查的五项能力提交至独立`codex/companion-five-capabilities`分支并运行Windows CI；不创建发布tag。远端运行结果另行追加，未执行前保持Pending。


### 按需联网与首轮Windows复验修正

已推送功能分支commit `393e3655890a4e1759fc0981931dc3294f1d6432`，首轮CI [36224678803](https://github.com/FrigidCrow/ai-neko/actions/runs/36224678803) Linux通过；Windows为455 passed、2 failed、1 skipped，打包正确未启动。`gh run view --log-failed`与`gh run download -n evidence-Windows`取得脱敏报告，定位截图落盘检查及记忆组件许可hash两项失败，报告未保留原始traceback。

修复新增许可文件的Git行尾策略，`.gitattributes`将`src/ai_neko/memory/licenses/*`设为字节保留；`git -c core.autocrlf=true cat-file --filters`输出SHA与记录一致，打包测试24项通过。截图测试根据Windows字节锁行为改成活跃时只读mmap、关闭后普通全量读取；包含所有锁/SQLite/WAL/SHM，并增加取消/下一轮/裸Base64检查及8个真实持锁文件扫描回归。Windows不再无条件跳过记忆数据库symlink检查，只有实际缺少创建权限时才记skip，CI仍拒绝未通过的必要检查。

只读搜索审查指出语音默认仅聊天不能按需查询，已修正默认明确显示“按需联网”，保留“仅聊天”。缺Key不阻止普通聊天，实际工具错误才提示；规划只显示思考，实际工具执行才显示查询；无工具不注入空证据，缺凭据不徒劳重试。历史与重试遵循当前明确模式。对应graph协议18项通过。打包README同步新功能和真实验收缺口，旧Release保持不变。

最终本地全套`.venv/bin/python -m pytest -q`：467 passed、1项Windows凭据库专属skip，37.82s。Ruff检查/格式和diff检查通过；真实云调用仍为0。

`node desktop/tests/companion.smoke.cjs --output artifacts/mvp1/companion-on-demand-smoke.json`最终13/13 PASS；`node scripts/desktop_smoke.cjs --output artifacts/mvp1/desktop-on-demand-baseline.json`9/9 PASS，宿主28/28。新增默认语音/图片按需工具、无Key普通聊天、打开历史/失败重试遵循当前模式；重试用真实合成HTTP503触发，随后仅聊天只发1次不带tools的模型请求。报告和截图更新至docs/evidence/companion；合成模型12、ASR3、TTS5，实际用户采集及云服务调用0。


第二轮源码 `7c408b99f791abc5d67e1855a8e0576de8fdaf5d` 已推送，[CI 36225439869](https://github.com/FrigidCrow/ai-neko/actions/runs/36225439869) Windows468 passed/0skip、Linux467 passed/1平台skip。已下载两份evidence核对同一clean源码、PASS gate，上一轮截图/许可两项及真实Windows symlink检查均通过。Windows ZIP构建成功，但冻结后端探针只通过8/16，停在packaged_chat_stream_ack_and_cancel；桌面两套检查尚未运行，未上传已验证包/发布。已下载package-evidence并发现旧探针仍要求缺凭据后额外规划一次，正在用本地真实服务复现定位。

新增`test_chat_probe_against_real_source_service`实际启动源码后端并执行同一`Probe.check_chat()`：旧断言准确复现失败于`len(model.requests)==5`，此前流式/ACK/取消/两工具错误检查已通过；改后1 passed。修正为4次精确协议断言（2普通chat、1带工具规划、1无工具回答），检查最终输入包含两项实际工具错误、私网来源不可读/正文空。`tests/test_package_smoke.py tests/test_companion_protocol.py tests/test_m1_api.py`31 passed，Ruff/格式/diff通过；未修改产品代码或删除旧ACK/SSRF检查。


第三轮`7bd14343178a5066182ac75007cf9137fafe6074`推送后，[36225891452](https://github.com/FrigidCrow/ai-neko/actions/runs/36225891452)必要jobs全部SUCCESS：Windows469 passed/0skip、Linux468 passed/1平台skip、冻结后端16/16、实际桌宠9/9、新增陪伴闭环13/13。发布job因非tag正常skipped。`gh run download`取得两平台源码证据和package-evidence，逐项核对同一源码及报告PASS；Root目视Windows新功能截图，白裙YUI和按需联网显示正确。合成模型12、ASR3、TTS5，单次停止70ms，不称p95；真实服务/用户采集0。Windows Server2022不等于Windows11真机。

最终开发包已实际下载，191,838,005字节，SHA256 `2f995145581ec22a59f1ba7de01a38832169214dea67c41c2c9871f9d33dd4e9`。核对SHA256SUMS、两平台同一clean源码、包内外build-info、GUI/后端AMD64 PE与执行报告摘要、记忆组件通知、桌面新模块及截图摘要通过；Mac未运行Windows exe。下载核对见 `docs/evidence/companion/windows/download-verification.json`。


## 2026-09-26 — 快照入口与已听上下文收尾

先在PLAN第16节登记完成审计缺口，再实现桌宠记忆快照创建/列表/确认恢复/删除，API沿用认证和受限IPC。Memory快照增加来源scope、读取大小和结构检查；恢复保留纠正/遗忘，同事务保存被移除事实及来源ID。Runtime先停止生成/语音/提取任务并写入恢复清理意图，清理受影响对话、播放回执与checkpoint；失败时禁止读取旧事件/标题，重启继续清理。

语音范围用原文Unicode码点绑定实际播放回执；仅连续完成的片段进入已听上下文。后端分别计算显示与已听内容，处理URL/引用被显示ACK截断的投影；旧无范围回执不虚构已听。桌宠停止音源立即执行，新问题提交前有界等待回执。参考N.E.K.O既有分句/取消边界，未启动参考工程或读取其数据。

实际执行 `.venv/bin/python -m pytest -q > artifacts/mvp1/companion-snapshot-heard-python-final.txt`：544 passed / 1 Windows凭据库专属skip，40.27s；最后清理失败期间API拒读与重启可读断言另在25项快照API测试通过。`.venv/bin/ruff check src tests scripts packaging`与`ruff format --check`通过。

桌面工作者执行 `node --test desktop/tests/*.test.cjs`：31/31；`node desktop/tests/companion.smoke.cjs --output artifacts/mvp1/companion-snapshot-heard-smoke.json`：16/16，含快照创建/取消确认/过期revision/恢复/删除/损坏项及隐藏面板首句完成、第二句停止后下一轮正确上下文。Root执行 `node scripts/desktop_smoke.cjs --output artifacts/mvp1/desktop-snapshot-heard-baseline.json`：9/9。Root实际查看快照确认界面截图，仍为白裙YUI；Mac报告归档至docs/evidence/companion。16模型/3ASR/9TTS调用均合成，真实服务与用户采集0；本次Windows包验证仍待运行。更新未来tag发布说明的功能范围，不创建tag或发布新版。


本轮首个Windows CI [36227635761](https://github.com/FrigidCrow/ai-neko/actions/runs/36227635761)未通过：源码ba77e55，Linux544/1平台skip；Windows540 passed、5 failed、0skip，打包未启动。下载源码证据后确认五项失败集中于test_memory_backups的旧格式、损坏快照和非法数据快照删除；报告未包含原始异常堆栈。代码检查发现SQLite测试上下文只提交、未关闭连接，现改为显式closing，在删除/恢复/VACUUM前释放测试连接；迁移/清理测试同步释放连接，新进程探针加30秒上限。产品代码不变，需以Windows复跑确认修正，不仅凭Mac通过推定。


第二轮[36227919089](https://github.com/FrigidCrow/ai-neko/actions/runs/36227919089)Windows544通过、1失败；上轮5项快照删除用例均通过。唯一失败为旧播放回执测试的列表排序断言。Root将测试时间戳固定相等、片段ID固定为逆序，在本机准确复现旧断言返回started/completed顺序；按稳定segment_id逐一检查完成/开始以及重启后完成/中断状态后通过，未改生产排序或添加sleep。同步更正包内说明为拖动角色、开启观察后核对预览。Windows最终效果仍以下轮CI为准。


### 快照与已听上下文 Windows 交付通过

最终产品源码 `5871f2d94e2f176a863262ae12c86edf2e05066c` 的 [CI 36228220001](https://github.com/FrigidCrow/ai-neko/actions/runs/36228220001) 必要 jobs 全部 SUCCESS，非 tag 发布正常跳过。Windows 545 passed/0 skip、Linux 544 passed/1 平台 skip；宿主 31 项，冻结后端 16/16、实际桌宠基线 9/9、新增陪伴闭环 16/16。前两轮快照文件句柄与同时间戳排序断言修正已在 Windows 实际通过。

Root 用 gh run download 分别取得两平台源码、package-evidence 和 ai-neko-windows-x64；比对重复报告后，运行 `python3 artifacts/ci/verify-companion-download.py artifacts/ci/36228220001/verified 5871f2d94e2f176a863262ae12c86edf2e05066c` 为 PASS。核对 SHA256SUMS、clean 源 commit、ZIP 内外 build-info、GUI/后端 AMD64 PE、实际执行摘要、N.E.K.O 记忆通知、桌面模块与两张截图摘要；实际查看 Windows 快照界面。ZIP `ai-neko-0.3.0-dev.5871f2d94e2f-windows-x64.zip` 为 191,849,807 字节，SHA256 `af1f20aecd0673421d35c914b3e240f3aeca7a36b9cadf068af610676a355015`。

[可下载开发包](https://github.com/FrigidCrow/ai-neko/actions/runs/36228220001/artifacts/10902330067)：展开 Actions 产物后完整解压内层 ZIP，运行 ai-neko.exe。证据更新至 docs/evidence/companion/windows；仓库 JSON 仅统一 LF 行尾，原始下载文件保留在 artifacts。该版含快照管理和隐藏面板已听前缀续聊，旧 v0.3.0-alpha.1 保持不变。合成调用 16 模型/3 ASR/9 TTS，单次停音 34ms，不是 p95；真实云服务、用户采集及 Windows 11 真机验收未进行，完整目标仍在进行中。

Final documentation check: python3 docs/diagrams/tools/validate-docs.py > artifacts/mvp1/snapshot-heard-final-docs.json returned PASS (24 documents, 360 local links, 20 diagrams, 206 unchanged reference files). Archived JSON content matches downloaded originals after LF normalization. git diff --check passed.

## 2026-09-26 — 录音生命周期与视觉撤销

完成核查发现真实前端竞态后，先更新PLAN再修复。继续参考N.E.K.O的采集代次与尝试内流归属，未启动原版或读取配置。录音在取消旧回合前登记归属，旧ASR在等待提交期间仍可撤销；视觉同请求重试复用原图，关闭或换源通过持久请求编号取消未知接受结果和活动回合；每次模型请求及流式事件复核图片时效。

独立审查用慢HTTP图片上传和真实记忆恢复等待边界复现：旧取消条件在恢复期间返回409，迟到图片被202接受并调用模型1次。取消编号现在允许在恢复期间写入，回归为取消200、迟到图片409、模型0次；旧条件只在独立测试进程内还原，没有改回产品文件。`.venv/bin/pytest -q tests/test_request_cancellation.py`最终6项通过，视觉生命周期及相关定向53项通过。

实际Electron首跑发现开启观察立即取消勾选，已补准备状态与取消选源的回归；第二次语音闭环已经产生合成ASR/模型/TTS，但识别结果提示被内部交接的“停止录音”覆盖，已区分内部静默交接和用户停止。失败原始报告留在artifacts，不记通过。

初次源码全套573项通过/1平台skip；新增恢复回归后574项通过/1skip，但扫描期间前端仍被修改，m0_smoke正确拒绝CI门禁，不能记为最终通过。待源码停止变化后再执行完整门禁。`node scripts/desktop_smoke.cjs --output artifacts/mvp1/desktop-lifecycle-baseline.json`实际桌面基线9/9通过，最终前端闭环和Windows包以下方证据补记。真实云服务与用户采集均0。

最终`.venv/bin/python scripts/m0_smoke.py --output artifacts/mvp1/companion-lifecycle-source.json`：574 passed、1 Windows凭据专属skip、0失败/错误，source_unchanged_during_run=true、ci_gate=PASS，报告如实保留PARTIAL。`node --test desktop/tests/*.test.cjs`：58/58；`node desktop/tests/companion.smoke.cjs --output artifacts/mvp1/companion-lifecycle-ui.json`：18/18，新增实际IPC撤销后延迟图片请求409及中途关闭观察取消活动模型、无晚到文字。实际查看白裙YUI截图，报告与截图归档至docs/evidence/companion。合成17模型/3ASR/9TTS，单次停音15ms非p95；Ruff检查/格式与diff检查通过。提交并推送既有开发分支运行Windows CI，不创建tag。

产品源码`669a8f181e45f838239bf7900bb20765588bbbd8`推送后，[CI 36229884609](https://github.com/FrigidCrow/ai-neko/actions/runs/36229884609)终态SUCCESS，Windows575/0skip、Linux574/1skip，宿主58、冻结后端16/16、实际桌宠基线9/9与陪伴闭环18/18均通过；非tag发布正常跳过。`gh run download`分别下载四个产物，合并时逐字节比对重复文件。`python3 artifacts/ci/verify-companion-download.py artifacts/ci/36229884609/verified 669a8f181e45f838239bf7900bb20765588bbbd8`为PASS：ZIP及清单、同一clean源码、内外build-info、GUI/后端AMD64 PE、实际执行摘要、NEKO记忆通知、两张截图及本轮撤销证据一致。

开发包`ai-neko-0.3.0-dev.669a8f181e45-windows-x64.zip`为191,856,745字节，SHA256 `d803d6e68dcbfbc90a92e48c2501f1c98bc302dec01d8dd97d26fbe8fafbf849`，[下载](https://github.com/FrigidCrow/ai-neko/actions/runs/36229884609/artifacts/10902556977)。Root实际查看Windows桌宠截图，白裙YUI、观察关闭和迟到文字缺席可见；报告证实延迟图片409及模型中途取消。Windows合成17模型/3ASR/9TTS，单次停音40ms，真实服务和用户采集0。更新Windows归档时仅将JSON行尾统一LF，原始文件保留artifacts；当前文档和README下载入口同步，不运行Windows程序于Mac，也不发布新Release。

最终`python3 docs/diagrams/tools/validate-docs.py > artifacts/mvp1/lifecycle-final-docs.json`为PASS：24文档、364本地链接、20图和206参考文件，参考仓库tracked状态未变。逐项比对Windows归档JSON内容及截图与原始下载一致，`git diff --check`通过；证据与当前下载说明另提交`[skip ci]`，产品源码仍为上方已验证commit。

## 2026-09-26 — 真实正文与十项新会话记忆补证

上轮是有效进展：产品669a8f1和证据b6d8bd0已推送，Windows CI36229884609终态SUCCESS、下载摘要核对通过。本轮重新读取当前工作树、计划与CI确认；本机仍为Darwin，可访问的ai-neko数据根无有效归属标记，四种项目专用服务Key环境变量均未配置。未初始化/接管该目录，也未读取其他项目配置或Key。完整真实模型/搜索/语音及Windows11验收继续缺少环境条件。

通过未修改的生产`WebTools({}, None).execute('read_web_page', ...)`真实访问Python venv、Git git-switch、LangGraph overview官方文档。第一组3次核对读取状态/关键词；第二组3次进一步核对正文位置与片段，前后正文SHA一致。实际正文分别20000（工具上限）、12089、6277字符，来源URL/id、读取时间和正文指纹均记录；页面没有可识别内容日期，因此保留null。局部原文只保留20词证据，完整提取文本仅位于artifacts；[归档报告](docs/evidence/companion/public-page-reads.json)为PASS。合计6次真实页面读取，真实搜索、模型、ASR、TTS调用仍为0，不能替代三次完整联网问答。

独立M2审查发现原十项新进程测试只证明list_facts保留数据，逐项召回/注入证据不足。先更新PLAN，再新增`tests/test_memory_recall_acceptance.py`：两个实际Python子进程，前者写5偏好+5事件并退出，后者创建10个新会话，分别提问并核对有效事实、来源原文、实际模型请求和数据库checkpoint thread_id。移除图上下文的临时负控确实失败。`.venv/bin/pytest -q tests/test_memory_recall_acceptance.py tests/test_memory.py tests/test_companion_integration.py`：37 passed in2.48s；Ruff与格式通过。确定性适配器只观察输入，不证明模型理解正确或Windows电脑重启。生产源码、桌面代码、依赖和构建配置均未修改；将新增回归交由既有Windows/Linux CI验证。

提交`2a41fc0fe4da520d5fe36ae9f3a622c5f82bc29b`并推送后，[CI36230897663](https://github.com/FrigidCrow/ai-neko/actions/runs/36230897663)终态SUCCESS。下载Windows/Linux源码报告，新增十项回归分别2.992s/1.434s通过；整套Windows576/0skip、Linux575/1skip。下载package-evidence核对同一source与archive摘要，冻结后端16/16、桌宠9/9、陪伴闭环18/18。精选结果与原始报告摘要见[记忆补证CI](docs/evidence/companion/memory-recall-ci.json)，原件保留artifacts/ci/36230897663；本次没有重新下载ZIP。`git diff --exit-code 669a8f1 HEAD -- src desktop packaging scripts .github pyproject.toml uv.lock`通过，既有已核对下载包仍有效；不把新构建报告当成本机下载校验。

仅依据源代码和已有用例核对默认路径初始化顺序：先同步校验/创建后端所有权标记，再创建desktop子目录并设置Electron profile，未发现正常启动会自行污染根目录的证据；未读取或修改实际默认目录。其已有无标记状态不作归因。本轮没有待运行的真实服务测试或Windows11会话，完整目标尚缺外部配置与实际场景质量验收，不启动无凭据调用或扩展到后续VAD/多角色功能。

## 2026-09-26 — v0.4.0-alpha.1 发布准备

用户明确要求发布新的Release。本轮登记PLAN发布验收项，将Python、Electron及两份锁文件中的应用版本统一为0.4.0，版本标签计划为v0.4.0-alpha.1；不升级依赖。沿用已验证的两平台测试→Windows打包→实际桌面检查→tag发布门禁，Release补充桌面截图和五项能力说明。

实际执行`uv lock --offline`、`npm --prefix desktop version 0.4.0 --no-git-tag-version --ignore-scripts`与`uv sync --locked --offline`成功；六处版本声明和构建器alpha标签校验一致。`.venv/bin/pytest -q tests/test_packaging.py tests/test_package_smoke.py tests/test_provider_config.py`为67 passed / 1 Windows凭据专属skip。此时尚未创建标签、运行本次tag CI或发布；后续按实际结果补记。真实服务和Windows11验收继续单列。

发布前Ruff检查与63个文件格式检查通过；桌面宿主58/58通过；文档校验PASS、无issues，git diff --check通过。

发布准备提交`f359826bd60139a6a9efcef8d959f56c385228ba`，创建并推送注释标签`v0.4.0-alpha.1`，未覆盖旧版本。[发布CI36233199073](https://github.com/FrigidCrow/ai-neko/actions/runs/36233199073)双平台测试、Windows打包和发布全部SUCCESS；附加桌面启动诊断36233199048也SUCCESS。Windows576/0skip、Linux575/1平台skip、宿主58、冻结后端16/16、桌宠9/9、陪伴闭环18/18。

GitHub Release 397176556于2026-09-26T09:41:25Z发布为prerelease，15个附件包含应用ZIP、摘要、构建信息、5份报告及7张桌面截图。实际读取Release API、核对远端tag解引用commit、下载附件并查看本次白裙YUI截图；大ZIP下载核对仍在进行，最终结果随后补记。发布说明补充勾选“朗读回复”这一操作步骤；未改tag或二进制。

`gh release download v0.4.0-alpha.1 --repo FrigidCrow/ai-neko --dir artifacts/releases/v0.4.0-alpha.1/download`已完成；运行`python3 artifacts/releases/v0.4.0-alpha.1/verify-release.py`为PASS。脚本调用既有下载验证器并追加GitHub全部15个asset digest/字节数、tag CI源码、基础/发布/Electron版本及ZIP CRC核对。应用ZIP为191,844,465字节，SHA256 `0e133684466e13d481f7de7988f5c23cef240291740cf362640c7b280f7ac3f6`。精选结果归档至docs/evidence/companion/release-v0.4.0-alpha.1-verification.json，原始下载与API/CI记录保留artifacts。合成模型17/ASR3/TTS9；单次停音110ms不是p95；真实模型/语音/用户采集0，Windows11仍待验收。发布证据与说明另提交，源tag和二进制不变。

发布后`python3 docs/diagrams/tools/validate-docs.py > artifacts/releases/v0.4.0-alpha.1/docs-final.json`返回PASS且issues为空，`git diff --check`通过；历史v0.3试用页已明确标注历史属性，新入口指向0.4发布与五项能力说明。


## 2026-09-26 — 下一阶段优化plan

用户要求基于上轮最佳实践写下一阶段计划。只读核对当前PLAN/架构/陪玩方案、Memory Service及Runtime/网页裁剪/来源路径，读取记忆中的本地持久化和单一权威约束。沿用2026-09-26已查官方LangGraph记忆/RAG、LlamaIndex文档处理和DeepSeek缓存说明；本轮未新增外部服务请求或产品依赖。

先更新PLAN第17节，再写docs/NEXT-GUIDE-COMPANION.md，登记G1–G6顺序、数据/版本/对局契约、11类自动化验收、标注集、分段时延和真实Windows场景。独立只读审查指出正文裁剪、旧回合来源编号、跨局历史污染和生命周期回写边界，已写入计划。同步README、架构和原陪玩方案入口，纠正当前状态文字并保留历史记录；没有开始实施或推送发布。

独立计划审查补充“控制操作成功提示绑定新修订”及“旧攻略派生建议不能经聊天历史回流”两项契约，已更新第7/8节和A06。首次`python3 docs/diagrams/tools/validate-docs.py > artifacts/planning/guide-companion-docs.json`返回PASS，`git diff --check`通过；修订后再次执行最终文档校验。

最终校验`python3 docs/diagrams/tools/validate-docs.py > artifacts/planning/guide-companion-docs.json`为PASS：25份Markdown、386个本地链接、206个参考文件，issues为空且只读参考指纹未变；`git diff --check`通过。仅文档变更，没有执行产品测试；已将计划保存到本工作区。

## 2026-09-26 — MVP2 前评审问题修复

依据 docs/MVP2-READINESS-REVIEW.md 逐项修复：遗忘级联改为本回合真实引用+证据扫描；checkpoint 按 thread 精确清理；recall/快照/备份/恢复/遗忘/记忆提取移出事件循环；流式事件缓冲刷盘；list_facts 去 N+1、分词缓存；restore 显式列回灌；快照列表去全树 integrity 扫描；凭据回滚保留未存储语义；turns 索引与 cancelled_requests 清理；0600 创建即终态；视觉黑屏检测稀疏采样；快捷键占用提示；M0 demo 图显式标记；ruff 扩 B/SIM 并修复全部 47 处（含 validate-docs.py 21 处）；11 个环境敏感子进程用例接 sandbox_compatible 探测（查明根因为宿主沙箱对二次 mkdir(exist_ok=True) 抛 EEXIST）。

实际命令：`uv run pytest tests/ -q`、`npm --prefix desktop test`、`uv run ruff check .`；新增回归用例 test_forgetting_one_fact_preserves_unrelated_turns_in_same_session。结果与未完成项见 REVIEW.md 本轮记录。

## 2026-09-27 — 修复批次 Windows CI 验证 + G1 前准备批次

修复批次（235342d/7ff0117）推送触发 [Windows CI 36258099207](https://github.com/FrigidCrow/ai-neko/actions/runs/36258099207)：Ubuntu/Linux 测试、Windows 577 passed/0 skipped、打包与三项冒烟全部通过。

按 PLAN §17.1 执行 G1 前准备批次：P-1 攻略库落位独立 guides.sqlite（数据根新增 guides/ 目录，NEXT-GUIDE-COMPANION §4/§7 修订）；P-2 迁移框架 `config/schema.py`（user_version 台账/幂等步骤/迁移前备份/失败回滚/中断续跑），收编 conversation 与 long-term 两处内联 ALTER；P-3 埋点 `config/telemetry.py`（内存缓冲+定量刷盘 logs/metrics.jsonl+分位数），埋 memory_recall_ms/first_text_ms/turn_total_ms/memory_extraction_ms；P-4 `retrieval.py`（Citation/Retriever 协议 + MemoryRetriever 适配）。

实际命令：`uv run pytest tests/ -q`（584 passed/12 skipped）、`npm --prefix desktop test`（58/58）、`uv run ruff check .` 与 `ruff format --check`（0）。新增 test_schema_migration/test_telemetry/test_retrieval 共 19 项；test_audio_context 旧 schema 用例改为连同 user_version 一起回退以模拟无台账旧库。

## 2026-09-27 — WorkBuddy 最新提交合并回原项目目录

用户要求把WorkBuddy最新代码合并回 `/Users/frigidcrow/Dev/ai-neko`。先更新PLAN合并任务，核实WorkBuddy工作树干净且HEAD为 `fd95b0a55f51471853223f36d21ae3f31d2cf3a0`；原目录三个未提交文件用 `git stash push -m 'ai-neko local edits before WorkBuddy integration 2026-09-27' -- desktop/renderer/app.js docs/NEXT-GUIDE-COMPANION.md docs/PLAN.md` 保存，备份对象 `3e77be31a370800c2759dac10f6ce6b6ddcbbbda` 保留。

执行 `git merge --ff-only fd95b0a`，再用 `git stash apply 3e77be31a370800c2759dac10f6ce6b6ddcbbbda` 恢复本地修改，无冲突。`git diff --exit-code 3e77be31a370800c2759dac10f6ce6b6ddcbbbda -- desktop/renderer/app.js` 通过，证实原桌面WIP完整保留；`git merge-base --is-ancestor fd95b0a HEAD` 通过。同步PLAN/NEXT/HANDOFF当前状态，保留独立guides.sqlite及快照决策。未修改WorkBuddy目录、未推送或发布。

原目录执行 `PYTHONPATH=src .venv/bin/python -m pytest -q --basetemp="$(mktemp -d /tmp/ai-neko-merged-tests.XXXXXX)"`：595 passed、1 Windows专属skip，44.27秒；在desktop运行 `npm test`：58通过；`.venv/bin/ruff check src tests scripts packaging` 与 `.venv/bin/ruff format --check src tests scripts packaging` 均通过。环境Darwin arm64、Python3.11.15、ruff0.16.9、Node25.9.0，依赖锁文件未改。

文档检查 `.venv/bin/python docs/diagrams/tools/validate-docs.py` 首次发现历史visualize1.0.39本机链接失效；将链接更新到已确认存在的1.0.41，并保留历史阅读版本说明，随后复验。既有取消竞态登记为下一项修复，未在本次合并顺带实施。

最终文档复验PASS：27份Markdown、391个本地链接、206个参考文件，issues为空，参考源码指纹保持不变；报告由校验器更新到 `docs/validation.json`，`git diff --check`通过。

## 2026-09-27 — MVP1 缺陷收束与CI/CD（执行中）

用户授权取消修复、问题收束、CI/CD与MVP1工程结束后准备MVP2。先登记PLAN第18节，分别处理Runtime/API、Memory Service、桌面生命周期和迁移框架，并做独立只读审查。没有启动G1–G6，没有读取参考工程配置/凭据或修改参考工程。

修复范围：异步recall返回后取消/关闭/并发/记忆修订校验，跟踪实际SQLite worker而非只取消await；人格/记忆API避免主循环等待记忆锁；同时间戳更正后的索引缓存失效及遗忘/恢复/关闭清理；原始用户历史与实际注入助手文本的依赖分开持久化；已遗忘占位不污染新历史；未来schema拒绝、连续迁移检查、备份与清理连接显式关闭；成功初始化清理固定名自动迁移备份，失败保留。桌面统一取消纯文字和视觉的未确认请求，停止后不展示/ACK迟到事件，记忆操作等待持久撤销。MVP2未完成UI补丁另存，原保护stash保留。

首轮全量 `PYTHONPATH=src .venv/bin/python -m pytest -q --basetemp="$(mktemp -d /tmp/ai-neko-closeout.XXXXXX)"` 为630通过/1失败/1平台skip：实际注入仅含无关用户问题时被错误挂上后台召回依赖。保留日志 `artifacts/mvp1/closeout-python-first-failed.txt`，未修改原测试预期；精细化用户与助手依赖后该测试和新增转述/重启/重复遗忘用例均通过，随后重跑全量。

专项：memory与迁移131项、Runtime/API最后目标116项通过；桌面 `npm --prefix desktop test` 64通过。`node desktop/tests/companion.smoke.cjs --output artifacts/mvp1/companion-closeout-final.json` 实际Electron20项通过，合成18模型/3ASR/29TTS，真实服务及用户采集均0，renderer errors为空；20次点击到WebAudio stop返回p95约0.10ms，不是声学/端到端或Windows11延迟。此前一次harness加载require错误保留失败报告并已修复复跑，不计入产品缺陷。

环境Darwin arm64、Python3.11.15、Node25.9.0/Electron44.4.5，锁版本LangGraph1.2.12/checkpoint-sqlite3.1.1/FastAPI0.141.1/httpx0.28.1/pytest9.1.1/ruff0.16.9未升级。`.venv/bin/ruff check src tests scripts packaging`、`ruff format --check src tests scripts packaging`与`git diff --check`通过；全量最终及Windows CI结果后续补记。用户授权本轮提交推送，未变更GitHub默认分支。

最终复核又补共享来源的真实原话分类：删除前持久化原话依赖，Memory返回真实存在的source IDs，避免把合成清理标记当原文，同时覆盖长文本、无ACK和跨库提交后崩溃恢复。最后执行 `PYTHONPATH=src .venv/bin/python -m pytest -q --basetemp="$(mktemp -d /tmp/ai-neko-closeout-release.XXXXXX)"`，结果640 passed、1 Windows凭据专属skip，40.74秒，日志 `artifacts/mvp1/closeout-python-final.txt`；ruff检查、72文件格式检查及diff检查通过。`node scripts/desktop_smoke.cjs --output artifacts/mvp1/closeout-desktop-baseline.json` 的实际Electron基线9项通过。接着执行文档验证，提交修复并推送当前分支触发CI。

### MVP1 CI/CD 与下载核对完成

文档验证PASS（28份Markdown、400个本地链接、206个参考文件，参考工程状态/差异指纹未变）后，执行 `git commit -m 'Fix MVP1 cancellation, memory erasure and upgrade lifecycle'` 得到 `748bab5164882c044b4108d41a7fa8df82a99a32`，`git push origin codex/companion-five-capabilities` 成功。`gh run list`按完整SHA定位运行36316166347，`gh run watch 36316166347 --repo FrigidCrow/ai-neko --exit-status --interval 15`返回0。Windows641/0skip、Linux640/1平台skip、宿主64、冻结后端16、桌宠9、陪伴20通过。

执行 `gh run download 36316166347 --repo FrigidCrow/ai-neko -n <产物名> -D artifacts/mvp1/closeout-ci-36316166347/<产物名>`，四份产物分别为evidence-Linux、evidence-Windows、package-evidence、ai-neko-windows-x64；同时用 `gh run view --json`、`gh api .../actions/runs/36316166347/artifacts` 和 `gh run view --log` 保存状态、产物元数据和日志。`artifacts/mvp1/verify_closeout_download.py`核对实际ZIP及源码、锁文件、build-info、PE架构/摘要、许可、报告和截图；首次LF/CRLF原始摘要比较失败，确认仅Windows Git换行转换后逐文件重建检出字节核对，最终PASS，证据写入 `docs/evidence/companion/mvp1-closeout-verification.json`。

ZIP版本0.4.0-dev.748bab516488、191,878,279字节、SHA256 `53eb5bfad5187094558dbfcd2ee814ceb4ceea8310aa638da7d0c1b32c8db65f`。源报告/截图另存 `docs/evidence/companion/mvp1-closeout-windows/`；独立目视复核白裙YUI、按需联网与记忆界面正常。本次Windows20次WebAudio停止p95约0.20ms，边界仅点击到实际stop返回；真实服务、用户采集与Windows11体验未执行。更新README、PLAN、HANDOFF、MVP2基线和收尾状态；没有实现G1–G6，没有改默认分支或创建tag，旧Release保留。最后仅提交文档与证据，使用[skip ci]避免对相同产品源码重复构建。

最终文档复验PASS：28份Markdown、414个本地链接、206个参考文件，issues为空，参考工程指纹未变；`git diff --check`通过。GitHub完整日志再次确认两平台测试job及Windows打包job的桌面单测均64通过/0失败，已纳入下载核对记录。产品代码保持CI验证的748bab5不变，最终提交仅文档、摘要和合成截图证据。


## 2026-09-27 — MVP2 G1 正文入库实施

用户确认“Mvp1结束了，开始mvp2”。先更新PLAN17.3任务和验收，再并行实现独立攻略库、公开网页采集边界，并冻结G6独立语料；Root接入Memory Service所有权、实际LangGraph/Runtime保存回调与来源状态。当前基线HEAD为e830730，本批次产品与测试尚未提交；没有推送、打tag或创建Release。

新增guides.sqlite文档/版本/核查记录/段落，50,000字符保留、100MiB逻辑载荷及LRU保护、scope隔离、迁移和跨进程读取。网页保持SSRF/响应限额，保存先于6,000/4,000字符模型预算；网页不进入个人事实库或其快照。桌面/Web仅增加准确保存/部分内容状态，不计G5管理入口。

首轮产品路径定向检查3失败：两处测试夹具分别误用了httpx预消费Response和缺少新正文完整性契约，修正夹具；另一次真实失败复现工作线程取消后报错覆盖取消信号、继续调用模型，修复_finish_task并添加写成功/失败关闭回归。独立审查再发现同正文前缀去重掩盖新增未读内容、回答节点4,000字符裁剪未置标；分别以核查coverage旁表和逐节点截断标记修复，保留回归。库专项最终48，采集22，Runtime8，冻结HTML生产入库12，合计90新增通过。

实际关键命令与结果：

- `uv run pytest tests/test_guide_ingestion.py tests/test_chat.py tests/test_request_cancellation.py tests/test_runtime_lifecycle.py -q --basetemp=/tmp/ai-neko-g1-integration-20260927b`：46 passed（审查补项之前）。
- `uv run pytest tests/test_guides.py tests/test_guide_web.py tests/test_guide_ingestion.py tests/test_web_tools.py tests/test_chat.py -q --basetemp=/tmp/ai-neko-g1-reviewed-20260927f`：132 passed；随后12篇独立HTML入库专项通过。
- `uv run pytest tests/ -q --basetemp=/tmp/ai-neko-g1-final-20260927g --junitxml=artifacts/mvp2/g1-python-final.xml > artifacts/mvp2/g1-python-final.txt 2>&1`：730 passed、1 Windows凭据专属skip，41.60秒；最后产品/测试源码冻结后执行。
- `npm --prefix desktop test > artifacts/mvp2/g1-desktop-unit.txt 2>&1`：64 passed。
- `node desktop/tests/companion.smoke.cjs --output artifacts/mvp2/g1-companion-final.json > artifacts/mvp2/g1-companion-final.txt 2>&1`：实际Electron既有闭环20/20，renderer errors为空；合成18模型/3ASR/29TTS，真实服务与用户采集0。只算既有陪伴回归。
- `python3 tests/fixtures/guides/fixture_loader.py --verify`：12篇、24有依据/6缺依据、4追问，摘要/独立标签校验通过；A03与模型质量NOT_RUN。
- `uv run ruff check .`、`uv run ruff format --check src tests scripts packaging`：通过，79文件已格式化；两份app.js的`node --check`与`git diff --check`通过。

从最终JUnit、Electron报告和当前文件生成`docs/evidence/mvp2/g1-local-verification.json`，含基线SHA、dirty说明、16份产品/测试/fixture摘要、锁摘要、环境和原始日志摘要。本机macOS26.6.2 arm64/Python3.11.15/Node25.9.0，依赖锁没有修改；原始日志保留artifacts/mvp2。G1报告、README、PLAN、NEXT-GUIDE-COMPANION、ARCHITECTURE和交接同步；下一步G2采用/切换/刷新/删除及独立攻略快照，整体目标active。Windows与真实服务验收未运行。

G1最后文档校验PASS：30份Markdown、424个本地链接、206个参考文件、issues为空，参考源码指纹未变。最终测试后16份产品/测试/fixture SHA-256复核一致，git diff --check通过。

## 2026-09-27 — MVP2 G2 采用管理与删除恢复

按已授权完整MVP2继续，先登记PLAN17.4，分工实现guides v3控制/采用/账本/删除标记、独立攻略快照、鉴权API与桌面路由白名单；Root实现Runtime v4依赖/异步抓取/持久清理意图及图修订门禁。固定采用版本、刷新不暗换版本、最多4个异步抓取、202操作回执；指南资料仍独立于用户个人事实。没有增加依赖、导入参考内容、读取参考配置或推送发布。

首轮Runtime定向检查37通过/4失败：两处旧夹具不接受新expected_revision参数、两处迁移版本/临时备份路径预期落后，修正后51通过。真实API联调发现provider配置异常写了running行但没有任务，调整为验证adapter后才落任务；另一失败是测试误将正文刷新视为控制修订变更，明确刷新不变更采用关系后修正预期。

独立只读审查用实际Runtime复现并补回归：同轮用户偏好被攻略删除级联误删；取消回执先返回而SQLite线程随后保存；同URL新正文保存失败后来源副本漏清；写清理意图失败后运行时永远mutating。修复为助手证据与用户事实分开、保存/取消共用变更锁、注入前记录来源哈希、意图写入纳入失败结算。控制意图保留已提交结果和全部字段，事件已清而checkpoint清理失败也可重启续清。重启还可用攻略账本恢复已提交但操作日志未更新的抓取。

快照坏头复现正常API永久阻塞，增加写文件前持久scope归属登记；已登记损坏文件和中断sidecar可安全清理，跨scope保留，未知归属外来坏文件明确人工恢复。七项边界测试首轮6通过/1失败进一步定位checkpoint空闲页残留（活行已无正文）；保留强字节断言，将所有checkpoint写连接设secure_delete并在定向删除后压缩，其他活行仍保留。修复后联合边界/Runtime/lifecycle35通过。

实际关键命令与结果：

- `uv run pytest tests/test_guide_runtime.py tests/test_guide_ingestion.py tests/test_runtime_lifecycle.py tests/test_schema_migration.py -q --basetemp=/tmp/ai-neko-g2-runtime-20260927i`：51通过。
- `uv run pytest tests/test_guide_runtime.py tests/test_guide_api.py tests/test_guide_ingestion.py tests/test_runtime_lifecycle.py tests/test_schema_migration.py -q --basetemp=/tmp/ai-neko-g2-runtime-20260927j`：143通过。
- `uv run pytest tests/test_guide_runtime.py tests/test_guide_runtime_boundaries.py tests/test_runtime_lifecycle.py -q --basetemp=/tmp/ai-neko-g2-boundaries-20260927l`：35通过。
- `uv run pytest tests/test_guide_runtime.py tests/test_guide_snapshots.py tests/test_guide_management.py -q --basetemp=/tmp/ai-neko-g2-recovery-20260927m`：122通过。
- `uv run pytest -q --basetemp=/tmp/ai-neko-g2-final-20260927n --junitxml=artifacts/mvp2/g2-pytest-final.xml > artifacts/mvp2/g2-pytest-final.txt 2>&1`：**951通过、1 Windows凭据专属skip，46.02秒**。
- `npm --prefix desktop test > artifacts/mvp2/g2-desktop-final.txt 2>&1`：**67通过**。
- `node desktop/tests/companion.smoke.cjs --output artifacts/mvp2/g2-companion-final.json > artifacts/mvp2/g2-companion-final.txt 2>&1`：**实际Electron既有陪伴20通过**，renderer errors为空；合成18模型/3ASR/29TTS，真实服务与用户采集0。
- `uv run ruff check src tests scripts packaging`、`uv run ruff format --check src tests scripts packaging`、`node --check desktop/lib/security.cjs`、`git diff --check`：通过，87文件格式通过。

从最终JUnit、Electron和前后源码清单生成`docs/evidence/mvp2/g2-local-verification.json`：232份产品/测试/构建文件在最终测试期间SHA-256一致；G2新增专项221（管理56/快照58/API92/Runtime8/边界7）。基线HEAD仍e830730，G1/G2未提交，锁摘要未变；环境macOS26.6.2 arm64/Python3.11.15/Node25.9.0。原始日志留artifacts/mvp2/g2-*，报告、README、PLAN、NEXT、架构、交接与REVIEW同步更新。下一批G3本地优先检索；完整MVP2、A01–A11、G5播放边界、Windows和真实服务均未宣称完成。

最终`python3 docs/diagrams/tools/validate-docs.py > artifacts/mvp2/g2-docs-final.json`：PASS，31份Markdown、435个本地链接、206个参考文件，issues为空且参考指纹未变。文档使用系统Python3.9.6，产品测试使用独立uv环境Python3.11.15，未混算；最终232份源码摘要再次核对一致。

## 2026-09-28 — MVP2 G3 本地优先检索与来源复用

实现前登记PLAN17.5。分工完成固定采用版本的有界BM25、搜索TTL/并发合并/条件核查、冻结30题实际图评测；Root接入唯一LangGraph、Runtime来源依赖、版本重核、流式补查和来源卡。没有新增依赖或参考项目导入，没有使用真实凭据、推送或发布。G4仅提供显式当前对局context接缝，未持久实现对局。

先以实际图和真实Runtime验证本地命中无需搜索配置、聊天模式可用、独立进程5问、来源定位、304/新内容/失败、切换时检索取消及旧派生历史排除。首次冻结Q13/Q18不达标保留在`artifacts/guides-evaluation/initial-fail.json`，修复通用中文问题语气词处理；冻结题、标签、80%单段覆盖和数字断言不变。定向审查补上未采用资料时明确最新绕缓存、清理意图同进程恢复失效缓存、未知版本304不解除门禁、首次采用排除旧网页建议。补查分支改为首文字立即流式投递，工具先到才抑制计划文字；晚到工具延后，仍只有一个图循环。

实际关键命令与结果：

- `uv run pytest tests/test_chat.py tests/test_guide_runtime.py tests/test_guide_api.py tests/test_guide_ingestion.py -q --basetemp=/tmp/ai-neko-g3-integration-20260928a`：120通过。
- `uv run pytest tests/test_guide_local_runtime.py tests/test_guide_evaluation.py -q --basetemp=/tmp/ai-neko-g3-local-20260928b`：52通过；随后扩充版本/流式与来源边界。
- `uv run pytest tests/test_search_cache.py tests/test_guide_local_runtime.py tests/test_guide_evaluation.py -q --basetemp=/tmp/ai-neko-g3-review-20260928c`：80通过。
- `uv run pytest tests/test_guide_local_runtime.py tests/test_guide_runtime.py tests/test_chat.py -q --basetemp=/tmp/ai-neko-g3-stream-20260928d`：31通过。
- `uv run pytest tests/test_search_cache.py tests/test_guide_local_runtime.py tests/test_guide_retrieval.py tests/test_guide_evaluation.py -q --basetemp=/tmp/ai-neko-g3-reviewed-20260928e`：160通过。
- `uv run pytest -q --basetemp=/tmp/ai-neko-g3-final-20260928f --junitxml=artifacts/mvp2/g3-pytest-final.xml > artifacts/mvp2/g3-pytest-final.txt 2>&1`：**1,143通过/1 Windows凭据专属skip，47.68秒**。G3新增192项：检索75、刷新32、缓存29、Runtime11、评测45。
- `npm --prefix desktop test > artifacts/mvp2/g3-desktop-final.txt 2>&1`：**67通过**。
- `node desktop/tests/companion.smoke.cjs --output artifacts/mvp2/g3-companion-final.json > artifacts/mvp2/g3-companion-final.txt 2>&1`：首轮在历史模式提示处断言旧文案失败，保留`g3-companion-first-fail.*`；同步两处遗漏的精确提示预期后同命令重跑 **20/20**，合成18模型/3ASR/29TTS，renderer errors为空。
- `node desktop/tests/guide-local.smoke.cjs --output artifacts/mvp2/g3-guide-ui-final.json > artifacts/mvp2/g3-guide-ui-final.txt 2>&1`：**5/5**，实际设置页配置本机合成模型且搜索未配置，真实IPC采用/切换，UI提问、来源卡、首段未结束可见、S1重映射和旧建议排除通过；3模型请求/0工具，截图实际查看，非G5管理按钮验收。
- `uv run python scripts/evaluate_guides.py --output artifacts/mvp2/g3-evaluation-final.json > artifacts/mvp2/g3-evaluation-final.txt 2>&1`：**24/24 top3命中、6/6缺口识别**，错误游戏/已知冲突版本/禁止来源0，覆盖题0搜索/0取页/0规划；最多6段5,798字符，另3个仅聊天检查通过。实际图注入，合成模型不计回答质量。
- `uv run python scripts/benchmark_guides.py --output artifacts/mvp2/g3-benchmark-final.json > artifacts/mvp2/g3-benchmark-final.txt 2>&1`：正式200篇，共2,041,900字符/2,242段，采用49,900字符/51段；预热5+测100，**p95 20.3945ms**，SQLite14,852,096字节，零网络。此前初跑20.817417ms保留在`artifacts/guides-benchmark/initial.json`；最终采用完整冻结版本单独复测。
- `uv run ruff check src tests scripts packaging`、`uv run ruff format --check src tests scripts packaging`、两份app.js与两份Electron harness的`node --check`、`git diff --check`：通过，97文件格式通过。

`uv run python artifacts/mvp2/record-g3.py`汇总JUnit、Electron、评测、基准及摘要至`docs/evidence/mvp2/g3-local-verification.json`。243份产品/测试/构建文件在最终Electron与基准期间摘要一致；Python全套之后唯一改动是`desktop/tests/companion.smoke.cjs`两处文案预期，产品/Python源码未改，原始Python起点摘要单独保留。HEAD仍e830730，当前G1–G3未提交；锁文件未改。macOS26.6.2/M4、Python3.11.15、Node25.9.0，真实模型/搜索/音频与用户采集均0。

下一阶段G4持久对局与动态模型上下文；G5管理/语音控制和客户端停音、G6真实服务与Windows11继续Pending。报告、README、PLAN、NEXT、架构、交接及REVIEW同步，A03只计检索/注入，未把上下文夹具或Mac性能替代完整MVP2验收。

最终`python3 docs/diagrams/tools/validate-docs.py > artifacts/mvp2/g3-docs-final.json`：PASS，32份Markdown、452个本地链接、206个参考文件，issues为空，参考指纹未变；文档运行Python3.9.6。机器证据已补文档结果，243份产品/测试/构建文件最终再次核对一致。

## 2026-09-28 — MVP2 G4 对局连续性与动态证据

先登记PLAN17.6，再并行实现MatchStore、图观察/上下文、API/IPC，Root接入实际Runtime、持久取消结算、历史隔离、投递/已听建议和遗忘依赖。conversation schema v5新增scope/session对局、修订与幂等账本、观察、每轮绑定、取消意图和实际目标版本消费者。图像只走同一LangGraph中的单帧结构化观察，不保存图像字节；动态证据120秒，模型流中到期也撤销。新局排除旧用户与助手动态历史，显式复盘独立标记且不自动联网。

独立回归发现并修复：泛化追问无法召回适用游戏偏好；取消已写行状态而真实任务仍退出，重试漏等待；改目标前消费者漏清；Memory提交删除/恢复后丢失返回值导致目标/消费者残留；Runtime重试接纳bool/float修订。预写仅含ID/hash的候选依赖由实际删除IDs激活，拒绝恢复及保留事实不能误删。故障注入限定原来源实际移除后才抛错，避免测试依赖set遍历顺序；数据库字节清除与无关资料保留均检查。

前期失败保留：新测试最初使用项目未安装的pytest asyncio插件，改成既有asyncio.run；随后两处夹具和迁移v5预期修正。`g4-integration-initial.txt`为129通过/2失败（实际偏好召回+旧schema预期），修复后`g4-integration-fixed.txt`152通过。`g4-boundaries-fixed.txt`41通过，初始全套`g4-pytest-initial.xml`1,436/1skip仅作中途证据。`g4-erasure-candidates.txt`因误写不存在的tests/test_runtime_memory.py未运行任何测试；改正路径后`g4-erasure-candidates-fixed.txt`44通过/3失败（故障注入源顺序和新增候选连接数），保留闭合连接断言并校正夹具，`g4-erasure-lifecycle-final.txt`36通过，独立目标遗忘14通过。未将这些中途结果冒充最终冻结源码。

最终实际命令：

- 保存`git ls-files --cached --others --exclude-standard`中src/tests/scripts/packaging/desktop/.github/pyproject.toml/uv.lock共255份文件SHA-256为`artifacts/mvp2/g4-source-before.json`。
- `uv run pytest -q --basetemp=/tmp/ai-neko-g4-final-20260928l --junitxml=artifacts/mvp2/g4-pytest-final.xml > artifacts/mvp2/g4-pytest-final.txt 2>&1`：**1,457通过、1 Windows凭据专属skip，55.43秒**；G4新增314项。
- `npm --prefix desktop test > artifacts/mvp2/g4-desktop-final.txt 2>&1`：**70通过**。
- `node desktop/tests/companion.smoke.cjs --output artifacts/mvp2/g4-companion-final.json > artifacts/mvp2/g4-companion-final.txt 2>&1`：实际Electron **20/20**，合成18模型/3ASR/29TTS，renderer errors为空。
- `node desktop/tests/guide-local.smoke.cjs --output artifacts/mvp2/g4-guide-ui-final.json > artifacts/mvp2/g4-guide-ui-final.txt 2>&1`：实际Electron **5/5**，3模型/0工具，来源定位/固定版本/旧建议排除；实际查看切换后来源卡与陪伴截图。不是G5新按钮验收。
- `uv run python scripts/evaluate_guides.py --output artifacts/mvp2/g4-evaluation-final.json > artifacts/mvp2/g4-evaluation-final.txt 2>&1`：冻结30题仍24/24 top3命中、6/6缺口正确，覆盖题0网络/0规划、禁止来源0，另3仅聊天检查通过。
- `uv run ruff check src tests scripts packaging`、`uv run ruff format --check src tests scripts packaging`、`node --check desktop/lib/security.cjs`、`git diff --check`：通过，108文件已格式化。
- `uv run python artifacts/mvp2/record-g4.py`：255份源码摘要前后完全一致，机器证据写入`docs/evidence/mvp2/g4-local-verification.json`。

真实Runtime测试包括G01网页提取/采用/用户原话/ACK/“下一步”完整链、A旧建议→切B→11新轮→显式复盘A→删除A清派生答，以及两个独立Python进程重启后的实际模型输入。合成模型只证明路由/来源/隔离，不证明视觉识别或建议质量。Mac测试、当前未提交源码与旧Windows包分列；锁文件未改，未推送/发布，真实服务和用户采集均0。G4报告与PLAN/NEXT/架构/交接/REVIEW同步，下一阶段G5，G6及MVP2整体继续进行。

最终`python3 docs/diagrams/tools/validate-docs.py > artifacts/mvp2/g4-docs-final.json`通过：33份Markdown、462本地链接、206参考文件，issues为空、参考指纹未变；系统Python3.9.6。机器证据补齐文档结果，255份产品/测试/构建文件再次核对一致。

## 2026-09-28 — MVP2 G5 实施中（初始集成，非最终验收）

先登记PLAN17.7，再并行实现攻略/对局面板、唯一图的明确控制意图与Runtime独立确认、真实Electron管理闭环。Root接入app/companion/media的对局绑定、媒体取消和控制结果跟随。目标是完整G5入口，不把已有G4后端和旧Electron回归当作G5完成。

Root初始 `npm --prefix desktop test > artifacts/mvp2/g5-desktop-initial.txt 2>&1` 为54通过/16失败：既有合成companion桩缺新绑定方法，另发现把新会话创建提前到截图返回之前破坏取消边界。保持“取图期间停止不创建会话”的原断言，把普通文字的会话建立留在截图返回后；录音开始时绑定现有或明确创建的新会话。补齐桩协议与有效绑定参数，新增实际ASR原绑定传递/迟到识别不提交的回归，`g5-desktop-binding-final.txt`为72通过。这是阶段回归，尚未冻结全部G5代码。

`node desktop/tests/companion.smoke.cjs --output artifacts/mvp2/g5-companion-integration.json > artifacts/mvp2/g5-companion-integration.txt 2>&1`：实际Electron旧闭环20通过，renderer errors为空；仍只作既有能力回归。独立G5新harness的用户动作通过真实按钮/表单/假麦克风，不以直接API写入替代UI验收，初始报告保留在`artifacts/guides-evaluation/g5-integration-*.json`。

真实G5集成05定位确认自取消：控制响应的events也携control_job_id，前端将其再次跟随，重复beginTurn取消了自己的TTS。修复为只有原请求跟随、每个独立响应只入队一次；06已通过三种语音采用/切换/新局及独立确认，均无额外模型调用。后续发现删除攻略会清理包含它的快照，UI快照列表却未刷新；保留真实失败并修界面，测试恢复顺序按既有purge_deleted语义调整，不把旧快照不存在伪装成可恢复。

后端审查补两处边界：请求撤销在原图完成、job待提交时也要取消控制；无活动局的match revision 0不能独自阻止旧TTS，加入持久攻略修订、已投递状态和控制取消校验。当前仍在后端全套、新帧视觉修订、实际Electron完整恢复流程验收，最终结果后续登记，不宣称G5完成。

## 2026-09-28 — MVP2 G5 最终冻结验证

实际集成07进一步定位开启观察自取消：companion已关闭旧来源，closeObservation进入runControl再次disableVision，visionEpoch被重复推进。内部已完成本地关闭的路径传visionAlreadyDisabled，管理面板直接关闭仍执行本地失效；用户等待期间再次取消/换来源仍能阻止迟到开启。集成08的17项全部通过，保留05/06/07失败报告，不把服务桩错误或快照测试顺序修正计作产品功能。

独立客户端VM复现改选B后重试仍指A、纯文字旧请求未退役、失败撤销后下一输入绕过。Root修复目标比较和统一tombstone结算，同来源仅目录修订变化或目录刷新清目标仍重试原载荷。补后端重启replayed回执不播、fetch取消迟到completed不续采用、未知控制回执同ID/CAS重试。control-client最终13项；guide-panel新增6项验证save/adopt先验正文、刷新不换pin、快照旧GET失效和状态清理。最终截图发现空控制助手行与残留进度，修复后再次实际目视确认。

后端首轮全套1493通过/1skip/2失败：旧schema预期5改6；旧G4测试用裸“842”判断泄漏，随机session UUID偶然包含该串，改为完整原文“842金币”，仍断言无观察/建议。另加入持有mutation lock时关闭控制worker不会死锁。后端代理末轮1496/1skip及39专项只保存终端工具输出，没有正式日志/JUnit，未据此伪造文件；Root下列统一冻结执行补齐正式证据。

最终实际命令与输出：

- 按`git ls-files --cached --others --exclude-standard -z`收集src/tests/scripts/packaging/desktop/.github/pyproject.toml/uv.lock，共263文件，SHA-256写`artifacts/mvp2/g5-source-before.json`；验证后逐一重算，完全一致。
- `uv run pytest -q --basetemp=/tmp/ai-neko-g5-final-20260928 --junitxml=artifacts/mvp2/g5-pytest-final.xml > artifacts/mvp2/g5-pytest-final.txt 2>&1`：**1496 passed/1 Windows凭据skip**，终端55.77秒（JUnit55.74秒）；控制Runtime/API专项39包含在内。
- `npm --prefix desktop test > artifacts/mvp2/g5-desktop-final.txt 2>&1`：**91/91**，746.64ms。
- `node desktop/tests/g5-management.smoke.cjs --output artifacts/mvp2/g5-management-final.json > artifacts/mvp2/g5-management-final.txt 2>&1`：**17/17**，无renderer/provider错误；11模型/5ASR/8TTS合成HTTP，9截图、两个实际进程、真实WebAudio、0用户变更API捷径。
- `node desktop/tests/companion.smoke.cjs --output artifacts/mvp2/g5-companion-final.json > artifacts/mvp2/g5-companion-final.txt 2>&1`：**20/20**，18模型/3ASR/29TTS；20次点击到WebAudio.stop返回p95 0.20ms，仅合成播放中软件边界，不冒称Windows/硬件停止性能。
- `node desktop/tests/guide-local.smoke.cjs --output artifacts/mvp2/g5-guide-ui-final.json > artifacts/mvp2/g5-guide-ui-final.txt 2>&1`：**5/5**，3模型/0工具；来源卡和S1重映射回归通过。
- `uv run python scripts/evaluate_guides.py --output artifacts/mvp2/g5-evaluation-final.json > artifacts/mvp2/g5-evaluation-final.txt 2>&1`：**24/24 top3、6/6缺口**；覆盖题零网络/规划，错误游戏/版本/禁止来源0，最多6段5798字符，3个仅聊天检查通过。冻结标签未改。
- `uv run ruff check src tests scripts packaging`、`uv run ruff format --check src tests scripts packaging`输出到g5-ruff-final.txt/g5-format-final.txt；全部通过，112文件。`node --check`检查app/companion/media/guide-panel/security五份JS，`git diff --check`通过。
- `uv run python artifacts/mvp2/record-g5.py`汇总JUnit、三份Electron报告、冻结检索和文件摘要到`docs/evidence/mvp2/g5-local-verification.json`；263份摘要一致，源HEAD仅历史基线，G1–G5未提交。

没有新增依赖/素材或改锁，没有调用真实模型/搜索/音频或采集用户媒体。README、PLAN、NEXT、ARCHITECTURE、HANDOFF、REVIEW与G5报告同步，实际API/schema v6和恢复/取消边界已记。下一阶段G6；只读审计已在HANDOFF留下真实旧版库升级、同聊天五问/搜索对照、冷暖记录和Windows包门禁缺口，本轮未提前实施或宣布G6通过。

最终`python3 docs/diagrams/tools/validate-docs.py > artifacts/mvp2/g5-docs-final.json`通过：34份Markdown、472本地链接、206参考文件，issues为空、参考指纹未变；系统Python3.9.6。机器证据补文档检查和日志摘要，最终再次核对263份文件与冻结起点一致。

## 2026-09-30：G6联合验收、真实旧版升级与测量工具

G6于9月28日在PLAN§17.8先登记，额度中断后9月30日确认没有仍在运行的测试进程，继续原工作区和原分工。基线HEAD仍e830730，G1–G6未提交；不重启工程、不套回历史草稿，不读取参考配置/凭据。当前Mac没有本工程模型/搜索/ASR/TTS环境Key及正常配置文件；仅检查存在性。已询问拟使用的服务与Windows11环境，未收到回复；先推进独立可完成部分。

- `uv run python scripts/generate_v04_fixture.py --output /tmp/ai-neko-v04-frozen-2`生成真实旧结构；源tag v0.4.0-alpha.1/f359826bd60139a6a9efcef8d959f56c385228ba，原样归档36份旧src、5份合成payload共274475字节。冻结在tests/fixtures/v04，CI不依赖旧Git对象/外网；未以降PRAGMA代替旧程序。
- `uv run pytest tests/test_v04_upgrade.py -q --junitxml=artifacts/mvp2/g6-v04-final-2.xml > artifacts/mvp2/g6-v04-final-2.txt 2>&1`：6通过/1.98秒。此前故障注入因来源顺序在目标提交前抛错，g6-v04-final失败保留；改为目标真实提交后再注入，没有产品变更或降低断言。
- `uv run pytest tests/test_guides_integrated.py -q --junitxml=artifacts/mvp2/g6-integrated-20260930.xml > artifacts/mvp2/g6-integrated-20260930.log 2>&1`：4通过/0.67秒。实际Runtime/HTTPMock/模型请求涵盖6852与21823后段、53146→50000字符、304/去重/索引重建、恶意正文控制隔离、新旧/未知/119/121秒/关闭重开观察和持久文件无8份合成媒体标记。初始版本字段、SQL列名、短数字和音频fixture错误保留，不当产品修复。
- `node desktop/tests/g6-acceptance.smoke.cjs --output artifacts/guides-evaluation/g6-electron-04.json`：8场景通过，11.874秒；26回合（25普通问加1明确复盘）、36合成模型HTTP、8截图、真实进程79442→79463，错误为空。来源采用后新进程新聊天、同聊天两遍五题/搜索开关对照、十旧局/新局/偏好/复盘及S1重映射均核对实际请求。01–03失败保留：搜索端点须HTTPS、本地足够仍允许补充tools schema、缺口规划加回答可有两次模型请求；没有放宽0工具执行要求。
- Provider新增显式request_usage，默认关闭；按官方OpenAI/DeepSeek流协议读取空choices的最后usage，仅保留数值白名单，重复快照不相加，中断不报完整总量。不支持时不自动重试付费调用。`uv run pytest tests/test_provider_usage.py tests/test_providers.py -q`：26通过/0.05秒，日志g6-usage-initial.txt。官方依据为OpenAI how_to_stream_completions与DeepSeek create-chat-completion。
- 初始20对探针g6-paired-probe-initial全部失败：误调用不存在的memory.update_settings；改为Runtime.update_memory_preferences。随后g6-paired-probe-fixed的10/20路由不合格，发现真实生产漏检：短混合名“灯芯10”被共享分词当整词，而长中文正文只有二/三字片段。修复guide_retrieval覆盖门禁，对单个中文/数字混合名称要求完整字面与数字/字母边界，不改上游分词、不把“灯芯100”当“灯芯10”。`uv run pytest tests/test_guide_query_numeric.py tests/test_guide_retrieval.py -q --junitxml=artifacts/mvp2/g6-numeric-initial.xml > artifacts/mvp2/g6-numeric-initial.txt 2>&1`：83通过/0.66秒，新增8回归。`uv run --locked python scripts/evaluate_guides.py --output artifacts/mvp2/g6-numeric-evaluation.json`仍24/24命中、6/6缺口，冻结题和标签未改。
- 测量审查发现构造/close错误会丢失后续pair，以及失败search后成功直接read会被错误记合格；修复逐pair异常结算、保留20样本、成功search/read资格。失败日志g6-measurement-before-fixes-20260930.log保留；6新回归加7usage共13通过，20对合成记录流程通过。各阶段检索配对和人工真实验收记录器随后补齐；首个有效文字/语音和费用尚未知，不拿首字/合成延迟做性能结论。
- Windows工作流新增冻结30题、200篇基准及实际解压exe G6门禁，门禁失败阻止verified ZIP上传；静态YAML/AST/JS检查通过。`--archive`只允许Windows x64，检查包内commit、实际app.isPackaged/execPath和ZIP/exe摘要，中文路径与移除PATH Python；本机没有执行新Windows包，也没有推送/tag/发布。

最终统一冻结和全套结果在后续补记。独立报告、用户入口和A01–A11矩阵需待当前变更全部稳定后同步；尚不将本节初步结果称G6全部通过。

### G6最终冻结与回归

记录器补cold/warm各自guide_retrieve_ms及turn归属，调用数明确为adapter/tool attempts，不等于底层HTTP连接数。人工记录器固定20/10/3/20数量、保留失败与原始probe，补ASR转写/请求、TTS请求/人工监听、同钟及账单依据。独立审查复现并修复声音早于首字、导入失败外层覆盖、跨turn指标、bool/非法类型与巨整数；10回归通过。`uv run pytest`最初因直接from scripts导入失败，改用明确文件路径importlib加载，`g6-record-invocation-fixed.txt`记录普通入口10通过/0.08秒。

数字主体判定收窄为中文名称加数字/ASCII后缀，普通数量问句不要求整句字面相等。`uv run pytest tests/test_guide_query_numeric.py tests/test_guide_retrieval.py -q --junitxml=artifacts/mvp2/g6-numeric-refined.xml > artifacts/mvp2/g6-numeric-refined.txt 2>&1`：84通过/0.49秒，包括新增普通数量句真实检索。

`uv run python artifacts/mvp2/record-g6.py freeze`冻结284份产品/测试/构建文件。第一次全量`uv run --locked pytest -q --basetemp=/tmp/ai-neko-g6-final-20260930 --junitxml=artifacts/mvp2/g6-pytest-final.xml > artifacts/mvp2/g6-pytest-final.txt 2>&1`得到1失败/1537通过/1平台skip（62.97秒）。原因是旧test_match_runtime把observation-e1529379…里的937认作旧金币，非实际观察内容泄漏。改为完整“937金币”原话、空观察和新帧fields精确断言，并将同文件913/711–720裸数字断言改为完整描述，保持旧局建议排除。定向16通过/1.02秒。原日志和起始摘要移为g6-first-failed-*，唯一改动tests/test_match_runtime.py及前后摘要写g6-freeze-repair.json；生产/桌面/构建均未变。

重新冻结284份文件后实际命令：

- `uv run --locked pytest -q --basetemp=/tmp/ai-neko-g6-final2-20260930 --junitxml=artifacts/mvp2/g6-pytest-final.xml > artifacts/mvp2/g6-pytest-final.txt 2>&1`：**1538通过/1 Windows凭据skip**，61.24秒。G6新增42项（6+4+9+7+6+10）已含总数。
- `npm --prefix desktop test > artifacts/mvp2/g6-desktop-final.txt 2>&1`：**91/91**，725.605ms。
- `node desktop/tests/companion.smoke.cjs --output artifacts/mvp2/g6-companion-final.json`：**20/20**，18模型/3ASR/29TTS合成HTTP；实际软件播放停止证据。
- `node desktop/tests/guide-local.smoke.cjs --output artifacts/mvp2/g6-guide-ui-final.json`：**5/5**，3模型/0工具，3截图。
- `node desktop/tests/g5-management.smoke.cjs --output artifacts/mvp2/g6-management-final.json`：**17/17**，11模型/5ASR/8TTS，9截图、实际进程84529→84560。
- `node desktop/tests/g6-acceptance.smoke.cjs --output artifacts/mvp2/g6-acceptance-final.json`：**8/8**，26回合/36模型，8截图、进程84628→84638。四份Electron均错误为空、产品/脚本摘要未变，实际在旧测试断言修正前已完成；修正仅测试文件，所以不重复无关UI运行。
- `uv run --locked python scripts/evaluate_guides.py --output artifacts/mvp2/g6-evaluation-final.json`：**24/24、6/6**，24覆盖题0搜索/取页/规划；错误游戏/版本/禁止来源/来源身份0，冻结标签未改。
- `uv run --locked python scripts/measure_guides.py --output artifacts/mvp2/g6-probe-final.json`：**20/20 SYNTHETIC_PIPELINE_PASS**，冷40工具尝试/暖0，40回合独立检索指标。有效文字/语音和费用仍null，ASR/TTS未运行。
- `uv run --locked python scripts/record_guides_acceptance.py init --probe artifacts/mvp2/g6-probe-final.json --output artifacts/mvp2/g6-live-record-final.json`后运行`check artifacts/mvp2/g6-live-record-final.json --output artifacts/mvp2/g6-live-record-check-final.json`：四部分均**PENDING**、退出2，符合合成资料不能升级成真实验收的要求，非脚本错误。
- 待Python和所有Electron结束后，`uv run --locked python scripts/benchmark_guides.py --output artifacts/mvp2/g6-benchmark-final.json`：200篇/5预热/100测量，**p95 19.720625ms≤150ms**，0网络/验证错误，临时数据已移除。
- `uv run --locked ruff check src tests scripts packaging`、`uv run --locked ruff format --check src tests scripts packaging`：通过、123文件。6份JS node --check、三份Python AST、工作流YAML/门禁解析和git diff --check通过，证据g6-static-final.json。以上运行标准输出另存同名-final.txt。
- `uv run python artifacts/mvp2/record-g6.py record`汇总为docs/evidence/mvp2/g6-local-verification.json；最终284份摘要一致，唯一旧测试修正前后信息另列。真实模型/搜索/音频、用户采集、Windows执行均0；未改锁/素材、未推送/tag/发布。

README、PLAN、NEXT、ARCHITECTURE、HANDOFF、G6报告、真实记录手册和REVIEW同步；本机联合结果通过，完整MVP2保留真实服务/Windows交付缺口。

最终`python3 docs/diagrams/tools/validate-docs.py > artifacts/mvp2/g6-docs-final.json`：37份Markdown、511本地链接、206参考文件通过，issues为空，参考仓库指纹不变（系统Python3.9.6）。检查结果与日志摘要补入G6机器证据，重新核对284份最终源码全部一致。报告已通过Codex文件面板请求展示（返回queued，不冒称已打开）。

### G6完成条件复核（继续目标）

上轮是实质进展：完成实现、全量和联合运行、修复实际漏检及记录器问题。本轮先按当前文件重算机器报告中的284份摘要，全部相同；仅检查项目专用环境变量及正常配置存在性，模型/搜索/ASR/TTS Key与本工程正常配置仍未就绪，平台仍Darwin。没有新的推送授权，也没有正在运行的Windows构建。

两条只读审查分别核对NEXT§4–5/G1–G3，以及§6–10/G4–G6和PLAN17.8。结论：未发现遗漏的本机功能或明确自动化验收；现有源证据覆盖权威、入库/版本/完整性、采用、检索/缓存、对局/动态证据、删除恢复、界面/媒体、旧版升级与测量工具。真实服务质量、20对有效文本/语音与费用、公开来源三次、Windows11十轮和硬件停音、新Windows包仍需实际环境或授权，不以合成PASS代替。

修正PLAN顶部/§17、NEXT顶部/§8和ARCHITECTURE中“G6待实施”的旧当前状态；将NEXT的20,000字符描述明确归为MVP1历史基线，当前50,000入库在裁剪之前，已核对web/graph/guides代码。旧阶段报告和历史数量不重写。只有文档及审计元数据变化，不重复运行已通过且源码未变的产品测试。

`python3 docs/diagrams/tools/validate-docs.py > artifacts/mvp2/g6-completion-audit-docs.json`重新通过：37份Markdown、515本地链接、206参考文件、issues为空。再次核对284份产品/测试/构建摘要相同。完整MVP2尚不能标完成，等待现有推送授权问题及实际服务/Windows11环境信息；不重复提问、不自动推送。

### 2026-09-30 用户确认推送/CI，核查免费服务与Windows虚拟机

用户明确确认推送授权，询问免费服务及旧N.E.K.O的接入，并要求试Windows虚拟机。先登记PLAN§17.9，更新当前交接的授权状态。`git diff --check`通过；`gh repo view FrigidCrow/ai-neko --json nameWithOwner,defaultBranchRef,isPrivate,viewerPermission`核实既有public仓库、ADMIN权限与默认分支codex/initial-plan，继续现有codex/companion-five-capabilities，不改默认分支。

两条并行只读调查分别核查参考工程tracked源码/官方服务文档，以及本机VM平台/存储与官方兼容性。未读旧凭据或配置，未启动参考服务，未购买或接受许可。VM实查Mac mini M4/24GB，空闲约36GiB，无已安装虚拟机；存储选择已向用户提出，CI可独立继续。

推送前重算284份G6源码摘要完全相同；84个新增文件共2,679,121字节，无超过1MB文件，凭据模式检查无命中，v04五份数据均为已冻结合成fixture。`git fetch origin`后本地/远端差异0/0，文档验证及diff检查通过。`git add .github README.md REVIEW.md WORKLOG.md desktop docs scripts src tests`、`git commit -m 'Implement MVP2 local guide reuse and continuous match context'`、`git push origin codex/companion-five-capabilities`成功，提交`7aeffd19312518749754aab67c0848d1c02f7c19`，触发CI36703688975。

首次CI结论failure：Linux1538通过/1平台skip，冻结30题与200篇步骤成功；Windows收集阶段1 error、未执行测试，打包跳过。下载两平台artifact与日志到`artifacts/mvp2/ci-36703688975`，模拟CRLF复现冻结文件摘要不一致。更新PLAN后在`.gitattributes`将`tests/fixtures/guides/**`和`tests/fixtures/v04/**`设为`-text -whitespace`；用`git -c core.autocrlf=true cat-file --filters --path=<file> HEAD:<file>`核对16文件逐字一致，同时普通pyproject.toml实际转CRLF，报告`fixture-windows-checkout.json`。没有修改冻结标签、manifest或放宽摘要。

免费服务核查见[免费服务与VM记录](docs/FREE-SERVICES-AND-WINDOWS-VM.md)。AnySearch一次独立httpx匿名探针成功后，先在PLAN登记再实现search_provider枚举、旧三字段兼容、显式免费设置和匿名协议；旧Key不进入匿名请求，402正文可能敏感因此不读取、不重试、不采用自动账号。新增CLI选择保留live/20对/模型Key门槛；缓存、SSRF过滤、取消与搜索摘要边界沿用原实现。后端专项141通过/1平台skip。

实际`ProviderStore`临时独立数据根→`WebTools.execute`产品网络路径匿名搜索5条成功/3,399.813ms，正文读取成功；初始探针取了旧字段名导致字符统计无效，报告明确删除无效数字并单独重读。第二次真实读取57,980→50,000字符/228.648ms，保留partial原因，不保存网页正文到验证报告。结果`artifacts/mvp2/anysearch-product-live.json`；累计外部httpx搜索1、产品搜索1/读页2，未改用户正常配置。

桌面VM测试新增匿名保存不传/不删除旧Key、默认地址切换和自定义地址保留；第一次仅因mock缺既有clearKeys方法失败，补mock后`npm --prefix desktop test`92/92。实际Electron陪伴脚本新增免费搜索选项保存、后端就绪、重新打开和切回Tavily的真实UI场景。完整Python/实际Electron及静态回归随后执行，结果另记。

最终`uv run --locked pytest -q --basetemp=/tmp/ai-neko-free-search-final-20260930 --junitxml=artifacts/mvp2/free-search-pytest.xml`：1578通过/1 Windows凭据skip，69.34秒；`ruff check`/`ruff format --check`124份Python通过，三份JS语法与文档验证通过。`node desktop/tests/companion.smoke.cjs --output artifacts/mvp2/free-search-companion.json`21/21，renderer_errors为空；已目视核对免费设置截图，仅改匿名输入框占位提示后重跑桌面92及该实际Electron脚本。Python源码/结果未变。旧G6完整结果仍为历史冻结快照；本次新增40项Python、1桌面unit、1实际Electron场景单独记录在[免费服务机器证据](docs/evidence/mvp2/free-services/local-verification.json)。

免费搜索及Windows换行修复已提交推送为`5732eaaa01e2479c521a115f3a503ba263f4eebc`，触发CI36705026368。独立UI审查确认匿名Key隔离/设置恢复/地址切换/状态正确；仅发现网页备用入口仍有旧搜索配置门控，新提示不应承诺此入口在无搜索配置时可直接提交本地资料问题，删去该句。桌宠本地资料路径不受影响。这是单句提示修正，`node --check src/ai_neko/web/app.js`通过，不重复未变产品测试；对应源码摘要单独更新，真实测量手册补AnySearch CLI选项。

提示与手册修正提交`9184c119f063462291d97a1bc8191ac9486ff7bf`已推送，最终CI36705331960；旧36705026368因同分支新提交按工作流取消，不计产品失败。最终run的Linux1578通过/1平台skip，138.02秒；冻结30题24/24+6/6，200篇100次检索p95 54.252796ms，0网络。Windows结果及产物仍等实际执行。

CI36705331960的Windows在600.281秒总超时（exit124）：诊断529项已完成全passed，第530项`test_direct_conditional_read_has_a_total_deadline_and_releases_http_request`停在call；未生成完整JUnit/Windows包。进度不是整套通过。独立子进程将事件循环`_clock_resolution`设0.015625复现挂起，3秒外部watchdog终止；DNS已完成、HTTP进入时`current_task.cancelling()==1`。另一路源码/确定性取消审查确认Python3.11.15的`asyncio.wait_for`在子任务已done时可返回结果吞掉父任务取消。

先登记PLAN，再补有界coarse-clock回归（HTTP最多0.1秒返回，避免测试自身无限等待）：`uv run --locked pytest tests/test_guide_refresh.py::test_completed_dns_does_not_swallow_deadline_with_windows_clock_resolution -q`在旧代码1 failed/0.17秒，错误返回ok，证据`windows-dns-race-before.txt`。`network.pin_url`将DNS5秒门限改为`asyncio.timeout(5)`内直接await，保留网络策略和外部CancelledError。相关`test_guide_refresh/test_web_tools/test_anysearch`99/99通过；同一独立脚本快速退出，HTTP进入时cancelling=0、随后正确释放并返回timeout（`coarse-clock-reproduction-after.txt`）。没有扩大600秒时限或跳过Windows测试。

网络策略公共路径变化后重新运行完整`uv run --locked pytest -q --basetemp=/tmp/ai-neko-dns-cancel-final-20260930 --junitxml=artifacts/mvp2/dns-cancel-pytest.xml`，输出`dns-cancel-pytest.txt`；ruff检查/124文件格式及diff检查通过。此次未改桌面源码，不重复桌面实际Electron验证；Windows重新构建将覆盖相同21+8场景。

取消修复后的完整回归：1579 passed / 1 Windows凭据skip，60.32秒；机器记录`docs/evidence/mvp2/dns-cancellation-fix.json`固定修前失败、修后99专项、完整回归及两份改动源码摘要。准备正常提交推送并重新运行Windows门禁。

CI36707083966失败：Linux1579/1skip、冻结30题通过、200篇p95 53.760353ms；Windows原DNS及新coarse-clock用例已通过，600.188秒时1358项完成（1357通过、1失败）。按setup/call/teardown重算594.024秒，之前finish重复汇总不采用。唯一失败为合成媒体持久化联合用例，现有脱敏输出无失败行，尚不能断言根因。PLAN登记后将smoke预算1200秒/job25分钟并保留严格完整JUnit门禁，新增Windows该用例短traceback前置诊断；它只处理合成媒体/临时资料，无真实服务或凭据。

`uv run --locked pytest tests/test_m0_progress.py tests/test_guides_integrated.py -q --tb=short`：12 passed/8.90秒；两份Python Ruff/格式、文档及diff检查通过。此提交用于准确定位Windows失败，尚未修复未知根因，不声称Windows通过。

诊断提交5896e491348230ddbd1ce0f346942f14f1e8a683触发CI36708814272；Windows定向失败明确在`test_guides_integrated.py:493`运行中扫描→346`Path.read_bytes()`→PermissionError，原异常不含路径。filelock3.32.7 Windows实现对进程锁offset0独占，与Mac advisory flock不同。PLAN登记后仅对已确认持有的`.conversation.lock`严格断言stat大小0，避免跨句柄读空锁；全部数据/WAL/日志仍读字节，关闭后无例外全量扫描。其余OS错误附相对路径后失败，不做泛化PermissionError忽略。下一次Windows运行确认；产品代码未改。

修正后本地12项联合/进度测试通过，Ruff/格式和文档/diff检查通过。仅测试扫描方法变化，远端全套将重新验证；没有调整产品隔离、持久化或媒体字节断言。

修正提交18e1dcc19a58b0e125719d69223bc1a71b43cb37已推送，CI36709092334的Windows定向媒体用例通过，完整套件继续运行。等待时复核免费服务说明，补当前入口、Tavily条件和旧Release区别。另用生产WebTools/网络策略验证中文关键词“王者万象棋 新手 攻略”/zh-CN：匿名5条成功4,438.54ms；读取首条腾讯官方新手指引3,294字符成功679.277ms，保留images_unread。此次1搜索/1取页，无模型；全轮累计3搜索/3取页，不算真实三次问答验收，结果存免费服务证据目录。

CI36709092334完整Windows结果：1580 collected，1575 passed/5 failed，547.953秒，JUnit完整且非timeout。媒体用例已通过；失败分别为搜索HTTP合并/短等待者独立超时1项，以及冻结v0.4原程序读取、新进程升级、两个迁移恢复参数共4项。未打包；扩展Windows前置短traceback至三个合成文件定位，不以部分进度或Linux通过替代Windows通过。

诊断提交7673440529844533f7285af524cc2e61f85fcb54/CI36710350044的Windows定向34通过/5失败，17.86秒，取得完整短traceback。缓存用例实际返回miss：独立粗时钟重放证明第二waiter注册太晚、第一已超时回收flight；测试先预排两个Task、sleep(0)后明确断言同flight.waiters==2，固定15.625ms再检原10ms超时、long继续、单HTTP/coalesced/后续hit，生产缓存未改。

旧版三个路径在driver禁网钩子拦截Windows asyncio内部socketpair；candidate路径在Path.home因精简环境无USERPROFILE失败。保持冻结driver/source/database/manifest完全不动（fixture diff为空），当前测试bootstrap用runpy加载driver，只先创建Runner事件循环，再装原deny_network并用sys.audit确认三类事件拒绝，最后运行旧程序。运行代码仍须来自原source目录。两个子进程用测试目录中的isolated-home作HOME/USERPROFILE/LOCALAPPDATA，未继承实际用户配置；-I配显式-X utf8和UTF-8输出解码。

`uv run --locked pytest tests/test_guides_integrated.py tests/test_search_cache.py tests/test_v04_upgrade.py -q --tb=short --junitxml=artifacts/mvp2/windows-fixture-fixes.xml`：39 passed/2.10秒，日志同名txt；Ruff/两文件格式及diff通过。完整suite由下一Windows/Linux CI重跑；不把Mac专项当Windows证明。

独立只读审查确认bootstrap旧源码身份、原禁网边界和临时Windows用户目录保持，修复提交7e1cefc已推送。包内README仍是MVP1/Tavily单一路径，随本次构建同步更新为攻略陪玩预览，补AnySearch免Key步骤、额度边界和攻略/对局操作；只改包内说明，不改产品逻辑或验收标准。
