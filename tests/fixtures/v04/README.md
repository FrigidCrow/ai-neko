# v0.4 真实结构升级夹具

这些文件完全由本工程旧程序与合成输入生成，没有读取用户资料、凭据或参考工程。

- 来源：本地 `v0.4.0-alpha.1`，commit `f359826bd60139a6a9efcef8d959f56c385228ba`。
- `legacy-src.zip` 保存该提交原样的 `src/` 文件；各文件 SHA-256 在 `manifest.json`。旧源码没有按当前代码重写。
- `data/` 保存旧程序实际产生的两个会话、三轮已确认回复、一段已完成播放回执、人格、两条事实、LangGraph checkpoint 与旧记忆快照。四份 SQLite 文件均为原始 `user_version=0`，没有通过降低新版 PRAGMA 伪造。
- `expected.json` 保存旧程序公共接口读回的合成期望；`manifest.json` 保存源版本、驱动/期望/数据库摘要、原始 DDL、行数和生成环境。
- `legacy_driver.py` 在 `python -I` 子进程中只导入指定的归档旧源码，显式指定临时资料根，拦截网络连接/解析，不调用真实模型。
- 数据目录由本目录 `.gitignore` 明确允许纳入版本库；运行中的 WAL、SHM 和 journal 不属于夹具。

冻结夹具最初生成命令：

```sh
uv run python scripts/generate_v04_fixture.py --output /tmp/ai-neko-v04-frozen-2
```

生成器要求目标不存在，不会覆盖证据。需要重新生成时选择新的临时目录，对照新旧清单后再有意替换。普通 CI 只读取已经冻结的文件与源码归档，不调用 Git、不访问外网、不要求存在旧 tag。

定向验收：

```sh
uv run pytest tests/test_v04_upgrade.py -q --junitxml=artifacts/mvp2/g6-v04-final-2.xml > artifacts/mvp2/g6-v04-final-2.txt 2>&1
```

测试核对夹具摘要与旧程序可读性、当前新进程升级后真实请求的人格/事实注入、旧 checkpoint 可读、旧/新记忆快照对新增攻略的隔离、删除后恢复不复活、两种真实迁移失败后的旧程序恢复副本，以及遗忘提交后中断的重启清理。所有运行只修改临时副本。

上述最终运行实际得到 `6 passed in 1.98s`。此前 `g6-v04-final.txt/xml` 保留一次测试故障：注入原先在任意来源首次删除后抛错，来源遍历顺序可能使目标事实尚未提交。修复后仅在目标事实的真实来源提交后注入，完整保留“提交后事实已删除”断言；没有修改产品实现。Ruff 检查、三份 Python 文件格式检查与 diff 检查通过。

这证明合成资料上的升级/恢复边界；不代表 Windows 包、真实服务或整个 MVP2 验收通过。
