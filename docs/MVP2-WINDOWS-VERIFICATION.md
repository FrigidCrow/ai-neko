# MVP2 Windows 构建与下载核对

日期：2026-09-30。**MVP2 G1–G6及免费搜索的Windows开发包已通过合成CI与实际下载核对；真实模型/语音、Windows11游戏与硬件验收仍Pending，完整MVP2未完成。**

[下载Windows开发包](https://github.com/FrigidCrow/ai-neko/actions/runs/36716659417/artifacts/11096433755)，解开Actions产物容器，再完整解压其中的`ai-neko-0.4.0-dev.50227627e8d4-windows-x64.zip`，双击根目录`ai-neko.exe`。通常需登录GitHub，产物到期时间为2026-10-14 12:59:01 UTC。包包含Electron、Python、YUI及运行依赖；模型/语音配置和免费搜索入口见[服务说明](FREE-SERVICES-AND-WINDOWS-VM.md)。本轮没有创建tag或Release。

## 来源与下载完整性

- 构建源码：`50227627e8d4c7c5574b4c52ebc12189d63988f9`，分支`codex/companion-five-capabilities`，构建时工作区干净。
- [CI 36716659417](https://github.com/FrigidCrow/ai-neko/actions/runs/36716659417)：Linux、Windows源码测试及Windows包三个必需job全部success；tagged发布按设计skipped。
- 包版本：`0.4.0-dev.50227627e8d4`；实际ZIP大小192,076,777字节，SHA256：`a79b04b543dd61ba7b36f990d8f4456edacff847a0398ca275251e038a240edc`。GitHub外层产物容器大小与此应用ZIP大小不同。
- 独立下载验证核对ZIP CRC、内外构建信息、210份源码输入、两个依赖锁、AMD64桌面与后端程序摘要、66份许可文件、79份素材、16张截图及同一ZIP的四份运行报告。普通文本按实际Windows CRLF检出重建比较；冻结资料保留原始字节。
- [机器核对报告](evidence/mvp2/windows/windows-download-verification.json)、[构建信息](evidence/mvp2/windows/build-info.json)、[校验清单](evidence/mvp2/windows/SHA256SUMS.txt)和[核对脚本副本](evidence/mvp2/windows/verify_windows_download.py)已归档。脚本原始输入和ZIP保留在本机`artifacts/mvp2/ci-36716659417/`；Mac只核对文件，没有运行Windows程序。后续文档提交不改变此构建源码身份。

## 实际通过的门禁

| 范围 | Windows Server 2022 x64 | Linux / 说明 |
| --- | --- | --- |
| Python完整回归 | 1,581通过、0跳过；521.5秒 | 1,580通过、1个Windows凭据专属跳过；118.407秒 |
| 桌面宿主单测 | 92通过 | 92通过 |
| 冻结30题 | 24/24 top3命中，6/6缺口识别 | 相同；错误游戏/已知冲突版本/禁止来源为0 |
| 200篇本地检索 | 5次预热、100样本，p95 80.3016ms | p95 26.84471ms；固定门槛150ms，均0网络与0检索断言失败 |
| 实际解压后端 | 16/16 | [包报告](evidence/mvp2/windows/package-smoke.json) |
| 实际桌宠与流式聊天 | 9/9 | [桌面报告](evidence/mvp2/windows/desktop-smoke.json) |
| 人格/记忆/视觉/语音与免费搜索设置 | 21/21 | [陪伴报告](evidence/mvp2/windows/companion-smoke.json)，renderer错误为空 |
| 攻略持久化与连续对局联合验收 | 8/8 | [G6报告](evidence/mvp2/windows/g6-acceptance.json)，实际解压exe、两个不同进程、25个记录问题完成且0搜索/取页 |

G6通过真实按钮/表单/IPC操作，用户变更的API捷径计数为0；覆盖采用后退出、新进程新聊天复用、连续五问的搜索开关对照、旧局/新局与明确复盘、来源S1重映射。调用36次合成模型HTTP；不证明真实模型理解或所有游戏场景。

陪伴报告含18次合成模型、3次合成ASR、29次合成TTS，以及20次实际WebAudio停止采样，点击到`AudioBufferSourceNode.stop`返回p95约0.20ms。它不是声学停止、麦克风到回复或Windows11硬件延迟。上述CI的真实云服务和用户媒体采集均0；本轮另行进行的AnySearch真实搜索与公开正文读取不计入CI。

Root实际查看[免费搜索设置](evidence/mvp2/windows/companion-smoke-free-search.png)与[重启后新聊天](evidence/mvp2/windows/g6-acceptance-A01-new-process-new-chat.png)：免Key配置、保留的采用版本/本地来源和YUI同屏呈现正常。[目视记录](evidence/mvp2/windows/visual-review.json)只覆盖这两张截图，不代替其它DPI或真实游戏验收。

## 本轮修复与保留的失败记录

先前CI失败已逐次保留在WORKLOG及本机artifacts：冻结资料CRLF转换；Python3.11 DNS等待吞取消的真实竞态；Windows空锁扫描、缓存等待者编排、旧版禁网hook安装顺序及隔离用户目录。冻结旧版源码/数据/manifest未改，媒体字节和迁移断言保留。

CI36710927613的检索p95 151.2265ms超过150ms，因此未打包。随后只优化单次分词调用中重复片段的处理，600组词元和600组BM25结果/分数与旧实现完全一致；未改语料、评分或门槛，见[优化证据](evidence/mvp2/tokenizer-optimization.json)。

CI36713526928源码门禁、后端16项和桌宠9项通过，但陪伴第4项失败：第二次人格保存误等上次成功提示，过早读取记忆。修复仅改验收等待，使用新版本、名字和本次完成状态；显式Promise闸门证明旧谓词误过，再释放真实请求并核对版本2→3。新Windows报告已验证该回归，后端并发保护未放宽；旧CI没有HTTP状态证据，409仅为代码路径推断。

## 仍需完成

- 配置本项目自己的模型/ASR/TTS，完成真实20对冷暖问题、有效语音、用量/费用和建议质量记录。匿名搜索及中英文公开正文已实测，尚不等于三次完整带来源模型问答。
- Windows11真实游戏至少十轮、切换/新局/重启、三次公开来源流程及20次跨生成/合成/播放停止，按[真实验收步骤](MVP2-LIVE-ACCEPTANCE.md)留证。
- 本机M4可尝试Windows11 ARM的x64仿真；当前约32GiB空闲、无已安装VM，仍需存储位置及所选虚拟机的账户/许可流程。VM尚未安装或启动，ARM VM也不替代原生Windows11 x64游戏/驱动/硬件结果。

用户的推送/CI授权阻塞已解除，Windows包交付验证已完成；完整MVP2只在真实服务和Windows11证据齐备后完成。
