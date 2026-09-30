# MVP2 真实验收记录

`scripts/record_guides_acceptance.py` 创建并审核人工 JSON 记录，不调用模型、搜索、ASR、TTS，不打开游戏、麦克风、屏幕或凭据库，也不上传文件。它只读取命令行明确指定的 JSON。所有证据均留在本工程独立目录。

**审核 PASS 只表示所填写的记录满足一致性和数量规则，不表示工具自动听到了声音、看懂了游戏画面或验证了账单。** 证据路径、时间和 SHA256 是供人工复核的索引；工具不打开或认证这些外部文件。实际服务、硬件及质量结论仍由执行者负责。合成记录永远不能通过真实验收。

## 创建、填写与审核

在工程根目录执行，输出必须是新文件：

```sh
.venv/bin/python scripts/record_guides_acceptance.py init --output artifacts/live-guides/record-01.json
.venv/bin/python scripts/record_guides_acceptance.py check artifacts/live-guides/record-01.json --output artifacts/live-guides/audit-01.json
```

Windows 使用 `.venv\Scripts\python.exe`。空模板审核为 **PENDING**。`init` 成功创建返回 0；`check` 的 PASS/PENDING/FAIL 分别返回 0/2/1。非法 JSON 或参数返回 2，并打印错误，不生成成功报告。CLI 从不覆盖现有输出；修订时保存为新的记录和报告，保留失败样本及原文件，不删除不达标的样本凑数量。

模板预留 20 对冷暖、10 轮游戏、3 次公开来源流程和 20 次停止。`id` 必须按序唯一，不能少填、重复或增加后择优筛选。未知值保持 `null`，不要填 0 或空字符串。结果字段 `result` 只能填 `completed` 或 `failed`；失败仍留在原序号。

报告列出 `issues` 的精确字段路径和 PENDING/FAIL 原因；每个部分分别报告状态。已知失败优先于缺项，不能因为其他部分尚未填写就掩盖 FAIL。时延摘要展示可用样本数，少量已填数据的 p95 不意味着整项通过。

## 证据与运行环境

`environment` 填写 OS、版本、架构、实际设备、`real_hardware`、构建标识、源码 commit 和包 SHA256。Windows 游戏部分要求 `os="Windows"`、`release="11"`、`arch="x64"`。Mac 或 Windows Server 的实验可保留，但不代替 Windows 11。

所有 `evidence` 字段使用相同格式：

```json
{
  "path": "artifacts/live-guides/session-01/recording.mp4",
  "recorded_at": "2026-09-30T15:00:00+09:00",
  "sha256": "填写该文件的64位小写SHA256"
}
```

记录只放必要的截图、音频/录屏片段、请求归属日志和人工备注。不要放 API Key、Authorization 头或参考工程数据。证据时间必须含时区。审核工具不会自动解析录屏，执行者应在旁附文件内时间位置、对应 turn/request、设备与监听结论。

## 20 对冷暖

每对 `cold`、`warm` 使用相同问题、model/ASR/TTS 供应商和模型 ID、相同网络条件。不同回合的 `turn_id` 必须不同。每个回合需要：

- `web_calls` 保留实际工具尝试，包括失败。冷路径必须成功 `search_web` 和 `read_web_page`；暖路径必须是空列表。这里统计工具尝试，不声称统计底层 HTTP 连接、DNS、重定向或字节数。
- `retrieval_metrics` 使用真实 `guide_retrieve_ms` 日志，`value` 单位为毫秒，`turn_id` 必须属于该回合，`retrieval_metrics_status="recorded"`。不把冷回合的检索时间复制给暖回合。
- `model_calls` 保留每次尝试及实际供应商 usage，不能用估算冒充上报 token；费用填写数额、货币、账单或价格核算依据和证据，覆盖本轮模型、搜索、ASR、TTS。未知费用保持 null；免费也需要明确的费用依据。
- `asr` 记录实际 `request_id`、转写原文、审核者和证据，转写必须与本回合问题一致。仅填相同 ASR 模型名不是实际转写证据。
- `output` 和 `text_deliveries` 保存原始后端文字交付轨迹；不要删去“我先看看”等占位文字。`first_text_ms` 只是第一段后端文字，不是首个有用答案，也不是 UI 渲染时间。

人工读完答案后，在 `useful_text.end_offset` 填写**第一段足以使用的答案结束位置**：从 0 开始计数的 Unicode 字符边界，不含该位置之后的字符，不是 UTF-8 字节数或 JavaScript UTF-16 单元数。比如占位语先在 100ms 交付，有效答案结束位置到 700ms 才被 `text_deliveries[].text_end` 覆盖，则工具推导有效后端文字时间为 700ms。填写 reviewer 和人工判断证据；工具不会自行判断哪句话有用。

`voice.first_useful_ms` 必须由实际监听标记第一段有用声音的起点，填写 TTS `request_id`、`listened=true`、`actual_hardware_playback=true`、reviewer 和证据。文字与声音须以相同 `clock_id` 和同一回合时间原点计时，单位固定 `ms`；不能直接减去两个设备各自的未同步时间。声音不能早于首个后端文字；有用声音起点可以早于整段有效文字结束位置。没有实际声音、同钟证据或监听，不得填写通过。

报告分别派生首字、有效后端文字、有效声音的统计和冷减暖差值；这些是所填样本的结果，不保证暖路径更快。检索时间和 token/费用保留在每对的 `metrics` 中，不把合成时延算作真实提速。

### 只读导入后端 probe

先验证默认合成流程：

```sh
uv run --locked python scripts/measure_guides.py --output artifacts/live-guides/synthetic-probe-01.json
```

真实后端测量需要先明确项目模型、Tavily兼容搜索配置，以及20个保留顺序的同题对照计划。`plan-20.json`顶层仅含`network_conditions`和`pairs`；pairs恰好20项，每项填写`question`、计划读取的公开`url`、`game`、`platform`、`mode`，所有字段非空。模型必须自行实际搜索并读取计划来源，脚本不会把计划URL直接当成功证据。输出保留失败组；每组临时空库用于冷问，采用真实保存版本后，在新会话中暖问。

显式设置本工程`AI_NEKO_MODEL_API_KEY`和`AI_NEKO_SEARCH_API_KEY`环境变量后，使用已确认的模型与地址执行：

```sh
uv run --locked python scripts/measure_guides.py --live --plan artifacts/live-guides/plan-20.json --model "已确认的模型ID" --model-base-url "已确认的模型地址" --search-base-url "https://api.tavily.com" --output artifacts/live-guides/live-probe-01.json
```

`--live`会实际调用所配置服务并可能计费。脚本不读取正常应用资料根或其他项目Key，不自动重试失败组；供应商不报告usage时保留unknown。当前此入口使用键入问题，不能单独完成含ASR/实际语音的20对验收。它测实际图与Runtime，但不经过ProviderStore共享缓存/条件读取包装，也不测桌面渲染；完整应用现场证据另填以下记录。

已有 `scripts/measure_guides.py` 报告时：

```sh
.venv/bin/python scripts/record_guides_acceptance.py init --probe artifacts/mvp2/g6-measurement-phase-metrics-synthetic-20260930.json --output artifacts/live-guides/imported-01.json
.venv/bin/python scripts/record_guides_acceptance.py check artifacts/live-guides/imported-01.json --output artifacts/live-guides/imported-audit-01.json
```

此示例是合成输入，必然不能真实 PASS。导入只复制已有后端记录，保留完整原始 report、文件 SHA256 和规范化内容摘要，不运行 probe、不再次收费。机器字段及失败结果不能在外层改成成功；需重新测量时创建新的独立记录并保留旧报告。

typed probe 没有实际 ASR、TTS、监听和 UI 渲染证据，相关字段继续 null。单独补写供应商名称不能解除缺口；需要明确对应本回合的问题、请求、人工监听和时钟证据。旧 probe 若缺每回合检索归属，也保持 PENDING；不能手动猜分配混合日志。导入报告的模型/工具次数是 adapter/tool attempts，并非底层 HTTP 请求计数。

## Windows 11 的 10 轮与 3 次公开来源

10 轮游戏需记录实际采用 guide/revision、预期与实际 match_id、人工依据及可执行性判断。`mixed_context=false` 还需人工确认没有混错游戏、攻略、版本或局势；ID 相等不能代替内容审核。actions 合计必须含 `switch_guide`、`new_match`、`restart`，分别对应真实 UI 操作。至少 9/10 轮 `grounded` 和 `actionable` 同时为 true；允许的一轮质量失败也会保留在 `quality_failures`，不能删除。任何混局或操作失败都不通过。

3 次公开来源流程分别记录公开 URL、问题、成功搜索/正文读取、保存、采用及暖复用 0 工具尝试的证据。至少一次 `explicit_latest=true`。`version_status="unknown"` 且没有自称最新时，可依据正确说明缺口获得行为通过；`latest_obtained` 仍为 0。声称已取得最新必须是 `verified` 并给出版本证据，不能因 HTTP 200 或今天抓取就判断最新。

## 20 次实际停止

20 个有效停止样本都必须在**旧句仍由硬件实际播放**时发出取消。`phase` 分别覆盖：旧句播放时后续请求仍在 `generation`、下一句处于 `synthesis`、以及仅有 `playback`。若尚未发声便被取消，应另记 suppressed/NOT_MEASURABLE，不能填 0ms 凑够 20 个停音样本。

每条记录使用同一时钟来源的 `received_cancel_ms` 与 `old_audio_stop_ms`，明确 `unit="ms"`，`clock_id` 与 `audio_clock_id` 相同。必须人工确认旧音频确实停止，填写 `old_audio_was_playing`、`actual_hardware_playback`、`listened`、reviewer 和硬件监听证据。请求结束、UI 状态变化、TTS 返回或图被暂停都不等于实际停音。

工具计算 `old_audio_stop_ms - received_cancel_ms`，按 nearest-rank 取 20 项 p95（排序第 19 项），要求 ≤300ms，失败值仍列出。语音触发时 `input_kind="speech"` 并填写 `utterance_start_ms`；另列 `received_cancel_ms - utterance_start_ms`，不混入应用收到取消后的停音指标。按钮触发用 `input_kind="button"`，开口时间可为 null，并不冒称测了语音识别响应。

本工具和单元测试通过，只证明记录流程可执行。完整真实 G6 仍需完成以上现场录入、证据复核及相应 Windows/供应商验收。
