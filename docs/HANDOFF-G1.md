# 交接单：MVP2 G6联合验收与真实环境入口

日期：2026-09-30。写给接手的 session：本文件、`AGENTS.md` 与 [MVP2计划](NEXT-GUIDE-COMPANION.md) 是必需上下文；以当前代码和各阶段报告为准，不依赖历史对话推断完成状态。沿用原文件名，便于已有入口继续访问。

## 1. 当前状态与证据边界

- 唯一开发目录为 `/Users/frigidcrow/Dev/ai-neko`，当前分支 `codex/companion-five-capabilities`。WorkBuddy提交已合入，旧副本仅供回溯，不从旧副本继续开发。
- MVP1工程收尾、G1正文入库、G2采用管理和G3本地检索是历史基线，分别见 [MVP1收尾报告](MVP1-CLOSEOUT.md)、[G1报告](MVP2-G1-REPORT.md)、[G2报告](MVP2-G2-REPORT.md)和[G3报告](MVP2-G3-REPORT.md)。历史测试数量、源码摘要及Windows产物不能证明当前工作区。
- G4已落地Runtime持久对局、严格回合绑定、图内单次视觉观察、有效上下文注入、历史隔离与清理依赖；历史数字见[G4报告](MVP2-G4-REPORT.md)。G5已接入来源采用、攻略/快照与对局管理、文字/语音明确控制及独立确认，沿同一Runtime/媒体机制执行；全量最终计数与边界见[G5报告](MVP2-G5-REPORT.md)，实现及本机合成验收通过。
- G6已补真实旧版资料升级、实际Electron重启/连续五问对照、长文与证据时效联合场景、A01–A11证据矩阵及可执行测量工具；最终统一验证与具体边界见[G6报告](MVP2-G6-REPORT.md)。真实模型/搜索/音频、新Windows构建及Windows11真机仍待验，当前不是MVP2完整交付声明。
- 用户于2026-09-30明确确认提交推送及Windows CI，当前按PLAN§17.9执行；不创建tag或Release。既有Release不等于MVP2交付，远端验证结果随后单独留证。
- 历史桌面WIP在 `artifacts/mvp2-deferred/desktop-guides-wip.patch`，保护stash为 `3e77be31a370800c2759dac10f6ce6b6ddcbbbda`，仅当前本机保留。它是未完成草稿，不直接套回现有取消与来源流程。

## 2. G3继续沿用的接点

| 接点 | 当前实现与接手约束 |
| --- | --- |
| 攻略权威 | Memory Service拥有独立 `guides.sqlite`；正文、固定采用版本、来源和删除标记沿用G1/G2。攻略不写入个人事实，也不混入个人记忆快照 |
| 问题检索 | `memory/guide_retrieval.py` 的 `GuideRetriever` 复用有界BM25，先按采用关系及游戏条件过滤，最多6段/8,000字符；带文档/版本/段落ID和原文范围。缺口、版本冲突、未核实版本和截断不能冒称充分依据 |
| 唯一对话图 | `runtime/guide_query.py` 接入 `chat/graph.py` 的local节点；G4在它前面增加可选observe节点，G5再增加整句明确控制分支。资料足够直接回答，仅聊天也可用本地资料，`LazyWebTools`到实际联网时才创建适配器 |
| 流式回答与补查 | 充分本地命中的模型如需补充，必须在文字开始前提出公开工具调用；文字已经开始后不执行晚到工具，发 `supplement_deferred`。沿用最多3轮、每轮3次工具，不另建循环 |
| 条件核查 | 24小时核查和显式最新支持ETag/Last-Modified；304只更新核查时间，重核仍执行覆盖、版本与预算门禁。变化正文另存版本，不自动替换采用修订；失败保留旧资料并标待核查 |
| 搜索缓存 | 120秒、最多64项，键含scope、配置代次、query、locale；成功摘要才缓存，同键共享，最后等待者取消才排空。配置、控制与清理使缓存失效，显式最新即使无采用资料也绕缓存 |
| 来源与耗时 | 每轮重新生成 `[S#]`，实际来源先登记 `turn_guides / turn_guide_sources`。`guide_retrieve_ms`只计本地检索，`guide_revalidate_ms`单列网络核查，`guide_query_ms`计整个查询；不能替代整端延迟 |

冻结12篇/30题保持原标签和门槛，`scripts/evaluate_guides.py` 经实际解析、入库、采用、检索和模型请求捕获评分。它的显式context接缝仍是G3评测边界；G4新增 `tests/test_match_retrieval_runtime.py` 用真实Runtime开始对局、用户原话与ACK建立context，验证G01短追问命中固定版本原文且没有补查网络。两者均不证明真实模型理解质量。

## 3. G4当前实现

| 接点 | 当前实现与接手约束 |
| --- | --- |
| 持久权威 | `runtime/match_store.py` 使用原会话库 `memory/conversation.sqlite`；对局表在schema v5引入，G5迁移v6增加control_jobs。保存 `matches / match_control / match_requests / match_observations`。`match_id`独立于会话，状态为active、needs_update、ended；scope、游戏条件、目标、state_revision及固定采用关系均由Runtime管理 |
| 回合绑定 | `turn_matches`保存接受时的原始请求和有效绑定。新请求带 `match={match_id,expected_revision}`；无对局也使用当前修订与null ID，仅从未建立对局的revision 0可省略。`input_origin`仅text/voice，`review_match_id`显式选择同session历史局；重试必须与原请求一致，严格整数修订不接受bool/float |
| 控制与取消 | 开始/新局/结束/更新/确认原话/关闭观察均要求request_id及expected_revision。先在同一事务提交新修订和 `match_settlements`，再等待旧回合及已登记语音任务结算；失败回执留库并阻止新输入，重试继续收尾。旧控制重放只返回回执，不撤销新修订回合 |
| 观察 | 普通文字只保留连续明确声明的原话，问题/假设不变成事实；显式观察接口只能引用本局真实用户turn的原话。观察带来源turn、observed_at/expires_at，视觉另带frame_id；动态描述及视觉最多有效120秒，同渠道新观察替换旧观察 |
| 唯一视觉节点 | 仅活动/待更新对局且本轮有图时，observe调用同一model一次，唯一工具为 `report_match_observation`。最多16项、名64字符、字符串值512字符或null，总JSON≤7,000字符；缺失/畸形/自由文字报告全部转未知。只读本帧，忽略图中文字指令，不推测执行，不给缺失字段续期 |
| 请求时效 | 每个真实模型请求重新取有效match_context，作为单独不可信用户资料和固定系统规则注入；不写入图state/checkpoint。请求冻结其观察最早失效时间，流事件与结束时再检查，不用更新context延长已发送证据的寿命。图像在闭包中附到当轮问题，字节不入库 |
| 连续追问 | 目标、有效用户/画面观察和本局已实际投递建议进入检索及回答。文本以ACK确认，voice以完整已听句回执为准；`last_delivered_advice.executed=false`，目标与建议中的数值不会自动转成已执行事实。局内回合不自动抽取为长期个人记忆，独立个人偏好仍可召回 |
| 历史隔离 | 活动局不直接重放旧原始十轮；普通无局聊天也排除局内和复盘回合。显式复盘先按目标match过滤再取最近10轮，标 `historical/history_only`；即使新局已超过10轮仍能读取旧局已确认建议 |
| 复盘与攻略 | 显式复盘的local结果为historical、sources为空，直接回答且无补查工具；不注入当前B攻略到旧A复盘。旧S编号标为历史来源需重查，保留实际旧攻略依赖，使删除A也能清理依据A生成的复盘答复 |
| 重启/关闭观察 | 重启将未结束局标needs_update、移除有效观察并提高修订；不续生成、不重放音频、不恢复媒体字节。关闭观察使视觉证据失效，可继续用有效文字描述；G5同步停止客户端来源并防迟到重开 |

接口集中在 `app/api.py`：`GET /api/sessions/{sid}/matches`及详情；`POST .../matches`开始；`POST .../matches/{mid}/new / end / update / observations / close-observation`。具体字段、返回修订与事件见 [架构契约](ARCHITECTURE.md)。ASR/TTS已接受成对的 `session_id + match`，在任务前后核对绑定；客户端必须在录音/取图开始时保留绑定，不能等结果返回再替换成新局绑定。

## 4. 删除、恢复与迁移约束

- 当前攻略schema v3、conversation v6、个人记忆v2；升级沿现有事务迁移、未来格式拒绝和本工程备份规则维护。成功初始化删除固定名自动迁移备份，失败保留。
- `turn_user_history`记录实际观察原话依赖，`turn_history`记录实际已投递建议依赖。`match_goal_evidence`保留实际注入的目标版本，`turn_match_goals`按match_id与目标哈希登记使用者，改目标后仍能定位旧目标影响过的答复。
- `runtime/erasure.py`在破坏性个人Memory写入前生成只含ID/哈希的依赖候选，持久写入清理意图；Memory提交删除标记后才激活对应候选。恢复被拒绝或保留的事实不因为出现在候选表就被删除。该路径覆盖Memory提交后返回值丢失时的续清需求；故障注入证据以G4报告为准。
- 个人遗忘清理对应观察、目标副本、消费者事件及checkpoint；删除攻略沿攻略来源依赖清理助手资料及历史复盘副本，保留独立用户原话和个人偏好。清理失败期间禁止残留资料回答，同进程重试和重启继续结算。
- 网页正文在模型裁剪前最多保存50,000字符；100MiB逻辑额度和已采用版本保护不变。攻略快照独立于个人记忆快照，恢复保留后来的删除标记；任何快照都不将历史局势恢复为实时状态。
- 后端新局仅取消受影响session的旧生成；语音任务注册尚未按session拆分，仍保守结算控制时已登记的语音任务。G5另验证实际WebAudio停止，不以服务端取消代替客户端停音。

## 5. G5桌宠、绑定与语音闭环

- `desktop/renderer/guide-panel.js`管理来源选择、库/快照与对局表单，所有变更经`app.js::runControl`撤销旧输入、停止播放器、等待结算并刷新状态。内部ID不作为用户输入。明确原话通过聊天输入建立观察，独立observations API仍可用；没有专门“确认原话”按钮。
- 输入开始时固定session/match；ASR返回不重标，截图属于该输入。events顶层`match_binding`更新到观察之后的有效修订，再交给TTS。TTS带turn_id，后端合成前后校验持久攻略修订、已投递文本、取消/遗忘与match绑定。
- `chat/control_intent.py`只识别用户整句明确指令，目标来自显式选中来源的`guide_target`。目标不明确则确定性澄清；原图退出后`runtime/controls.py`独立worker执行既有权威操作，避免等待自身。成功确认使用独立response_turn、新绑定、0额外模型调用；不会当成用户已执行建议。
- schema v6控制账本维护取消、已提交结果与恢复。请求tombstone撤销排队控制，取消已提交操作只停止确认；恢复不再次执行或自动播放。客户端防job自循环，旧response只跟随一次，后端重启后的replayed结果只供核对。
- 未收到接受回执时保留原请求；明确改问/换来源前必须成功提交旧请求tombstone。目录刷新清空目标或仅修订变化不改写原重试。失败撤销一直阻止替代请求，不能第二次Enter绕过。
- 关闭观察时本地立即失效，再清后端证据；内部已关闭的调用不得再次递增visionEpoch而取消本次开启。等待期间用户取消或换来源，旧开启不复活。

## 6. G6已实现接点与后续真实验收

PLAN§17.8先登记再实现。G6实际Electron新增采用→退出→新进程→新聊天、同聊天连续五问/配置搜索及关闭搜索两轮、十旧局/新局/偏好/明确复盘与S1重映射；新联合Runtime测试把长文/截断、304/去重/重建、恶意正文和证据时效/媒体不落库串起来。G5的媒体竞态仍由原脚本单独验证，不把G6的8场景等同A01–A11每个排列全部实测。

`tests/fixtures/v04`冻结真实旧程序生成的原始数据及旧src归档；6项升级测试在独立进程/临时副本核对既有人格、事实、会话、checkpoint、旧/新快照隔离和失败恢复。普通CI不需旧Git对象，不用当前库降低PRAGMA模拟，不读取实际用户或参考数据。

`scripts/measure_guides.py`默认20对合成后端探针，明确`--live`才使用项目专用环境Key。每pair空库冷问，实际搜索/读取并采用后在新session暖问同题，失败不删不重试；用量是完整流的供应商报告，首个有用文字/语音与费用未知时保持null。模型/工具尝试数不是底层HTTP连接数；它没有经过ProviderStore共享搜索缓存/conditional-read分支，也没有实际ASR/TTS/桌面播放。`scripts/record_guides_acceptance.py`用于导入和人工核对真实证据，使用步骤与每项Pending原因见[真实验收记录](MVP2-LIVE-ACCEPTANCE.md)。

仍需完成以下实际环境核查与执行，禁止扩大到持续录屏或键鼠控制：

1. 本项目指定模型/搜索/ASR/TTS配置。当前Mac正常配置与项目专用Key未就绪，不能借用参考工程或其他项目Key。先确定真实服务与20组同题计划，再运行live并核对用量/费用/有效建议和实际听到语音。
2. Windows11用户真机：同一攻略至少10轮，含切换、新局与重启；3公开来源流程含一次明确最新；跨生成/合成/播放20次停止。保留所有失败，不能用本机合成计时替代。
3. 新Windows包：工作流已接冻结30题、200篇基准和`g6-acceptance.smoke.cjs --archive`，静态通过不等于新构建通过。脚本仅在Windows x64使用实际解压exe，检查包commit、进程和摘要。用户已确认推送及CI，按PLAN§17.9执行并留证；旧MVP1包不作MVP2交付，不创建新tag/Release。

## 7. 本机执行与文件入口

- 检索优先 `rg` / `rg --files`；被Git忽略的artifacts需显式包含。pytest使用独立 `--basetemp=/tmp/<唯一名>`。
- 静态门禁为 `uv run ruff check src tests scripts packaging` 与 `uv run ruff format --check src tests scripts packaging`；按改动运行必要回归，命令记WORKLOG，证据与Pending记REVIEW。
- 线程取消、关闭和记忆变更要等待实际SQLite线程结算，不能把取消await当作写入已经停止。

| 内容 | 入口 |
| --- | --- |
| 当前G6结果、联合矩阵与最终数字 | [MVP2-G6-REPORT.md](MVP2-G6-REPORT.md) |
| 真实服务/Windows记录工具与操作步骤 | [MVP2-LIVE-ACCEPTANCE.md](MVP2-LIVE-ACCEPTANCE.md) |
| G5桌宠入口与历史媒体证据 | [MVP2-G5-REPORT.md](MVP2-G5-REPORT.md) |
| G4历史结果与最终数字 | [MVP2-G4-REPORT.md](MVP2-G4-REPORT.md) |
| 全范围、失效契约与A01–A11 | [NEXT-GUIDE-COMPANION.md](NEXT-GUIDE-COMPANION.md) |
| 实施登记：G3 §17.5、G4 §17.6、G5 §17.7及后续 | [PLAN.md](PLAN.md) |
| 精确接口、事件与唯一对话图 | [ARCHITECTURE.md](ARCHITECTURE.md) |
| G3评测/基准与历史G1/G2证据 | [MVP2-G3-REPORT.md](MVP2-G3-REPORT.md)、[MVP2-G1-REPORT.md](MVP2-G1-REPORT.md)、[MVP2-G2-REPORT.md](MVP2-G2-REPORT.md) |
| 工程规则、实际命令与未完成项 | `AGENTS.md`、`WORKLOG.md`、`REVIEW.md` |
