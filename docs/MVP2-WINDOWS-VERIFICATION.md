# MVP2 Windows 构建与下载核对

日期：2026-09-30。当前状态：双平台源码门禁已通过，Windows包陪伴验收失败后修正等待竞态，尚未通过下载核对。

上一候选 `d60a8a87c1af36736aeb3444cc49e61e7411ebbf` 的[CI 36710927613](https://github.com/FrigidCrow/ai-neko/actions/runs/36710927613)中Windows1580项及冻结30题均通过，但检索p95 151.2265ms超过固定150ms门槛，因此未打包。性能修正源码 `42c3aadf6eb67e58bdf11cfd919bd285aaf906c6` 的[CI 36713526928](https://github.com/FrigidCrow/ai-neko/actions/runs/36713526928)已通过Windows1581/0skip、Linux1580/1平台skip、双平台桌面92与冻结30题，检索p95分别97.247ms与22.110062ms。

该包已通过构建、解压后端16项和桌宠9项，但陪伴第4项在第二次人格保存后读取记忆失败，G6未运行，没有上传ZIP。验收脚本错误等待上次保存的旧提示；现改为新版本、名字与完成状态，并用显式Promise闸门保留可重复证据。后端并发保护未改，下一次Windows包须完整复验后再交付。

本轮已修复冻结资料CRLF转换、Python 3.11 DNS取消竞态，以及Windows测试中的空锁扫描、缓存并发编排、旧版事件循环禁网顺序与隔离用户目录。冻结旧版源码/数据/manifest未改，媒体字节与迁移断言保留。分词优化仅复用单次调用的重复片段；600组tokens和600组BM25顺序/分数与旧实现完全一致，基准和门槛未改。

此报告只覆盖Windows Server 2022 x64合成CI。Windows 11 ARM虚拟机尚未安装启动，Windows 11 x64用户游戏、真实模型/语音和硬件停止验收仍待完成。匿名搜索及中英文公开正文的独立真实结果见[免费服务记录](FREE-SERVICES-AND-WINDOWS-VM.md)。
