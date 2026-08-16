# Gen3 研究期派生市场数据准入

状态：**Phase 1 草案基础设施。它产生本地研究期内容身份快照，不是 PIT
manifest、锁箱、正式研究协议，也不授权候选生成或回测。**

## 时间窗口与质量前提

固定研究起点为 `2015-01-01`。研究终点必须显式指定且不晚于
`2026-08-31`；当前本地数据覆盖约至 `2026-08-05`。时间角色为：2015–2020
发现、2021 历史资格开发证据、2022–2026-08 已见稳健性检查。

该层从已经完成的质量 run 严格加载全部报告，绑定 source contract、snapshot、
campaign 和 aggregate hash。只有所有报告无截断、扫描行等于 footer 行数，且
所有隔离问题都有有效日期并严格早于 2015-01-01 时，才得到
`eligible_for_content_snapshot`。研究期内问题、无日期问题或未完成质量 run
均为 `blocked`。

`trend_cache_adjusted` 的 19 个问题都位于 1991–1994，因此能在不修复源文件
的前提下被**逻辑排除**出 2015 起的研究窗口；这不是删除或修改。若未来把
研究起点前移，必须重新阻断并重新审核隔离行。

## 已完成的真实研究期内容运行

在固定窗口 `2015-01-01..2026-08-05` 上，主审已完成一次真实但仍非 PIT 的
内容身份运行：

| 项目 | 结果 |
| --- | --- |
| policy | `sha256:e034ed42538969ac80439d07f2cf37fc11219e6e613093d2c969a804d3b0884b` |
| decision | `sha256:469f3ba7e5ed60917a22edee78007ce746a43794a55b0f42a051ba9447db46f5` |
| run ID | `market-content-9cacd85cb002278f03b15d4618875b09fc5be1b7ce61cc04e6ffa3a32ebdbf77` |
| 完成 files | 3,464 / 3,464 |
| 研究期有行 files | 3,381 |
| 研究期零行 entries | 83 |
| 研究期 rows | 7,870,690 |
| 排除的研究期前 issues | 19 |
| status | `historical_market_content_snapshot_complete` |
| content snapshot | `sha256:c561e0ee1526398c347ffba01cb10fbc61b05390b5fc26c0d22c0a73545da759` |
| write-once reports/entries | 2,557,867 bytes |

运行目录没有残留 `.lock` 或 `.tmp`，并保留 1 份 v1→v2
migration/recovery receipt。该迁移没有修改既有 274 个 entries；生成运行目录
受 Git ignore 保护，不提交到 Git。

## 内容身份快照

每个 snapshot 文件流式读取合同的六个字段，仅选择
`research_start <= session <= research_end` 的行。每文件都被冻结，包括研究
期零行文件：证券代码、选择行数、首末日期和 canonical row hash 序列的
SHA-256；全体 entry 再形成内容快照 hash。研究期内的非法日期、日期非严格
递增、NaN/无穷、非正价格、负量或 OHLC 边界错误都会 fail closed。

扫描前后重新比对同一文件 footer 与大小，阻断替换、schema/行数/row-group
变化。内容 hash 也会发现“同 footer、同大小但 OHLCV 值变了”的情形；它只
是在扫描时的内容身份，不能证明历史可得性、供应商修订历史或原始 source
bytes 的永久不可变性，因此仍不是 PIT source manifest。

运行目录只写在显式允许的 workspace 下，使用 write-once entry、独占锁和
每批最多 100 文件。状态为 `waiting`、`accumulating` 或完整的
`historical_market_content_snapshot_complete`；部分结果不会产生完整内容快照。
CLI 需要显式 `prepare`/`status`/`execute`，其中 execute 必须给出
`--confirm-read-source`；只有 `prepare` 接收研究终点。

`prepare` 是唯一会重新计算研究期准入行数的命令。已有 run 的
`status`、`execute`、`recover-lock` 和显式 `migrate-run` 从 run 内的 write-once
policy/decision/snapshot 元数据加载，再用绑定的质量 run 重建并比对 footer 快照；
它们不重扫研究期数据。旧版 v1 run 没有 `recoveries/` 时不能被静默接受，必须以
`migrate-run --confirm-v1-to-v2` 先逐项校验既有元数据与 entries，并写入不可覆盖的
迁移收据。该迁移只新建 `recoveries/v1-to-v2.json`，不修改任何旧工件或源数据；空的
迁移目录可在崩溃后被安全续接，其他额外根目录文件、锁或临时文件都会阻断。

真实运行期间发现并修复了三类执行层问题，并均有回归覆盖：状态/执行命令曾
重复全扫研究期数据；旧 v1 run 必须显式迁移而非静默接纳；以及 Windows 默认
编码会让中文路径中的 quality-run 目录发生 hash mismatch。持久 JSON 现统一
以 UTF-8 读取，且 v1→v2 仍只添加 receipt，不改旧 entries。

中断恢复不是删除审计证据：新的运行锁是带 UTC 创建时间、PID、运行/策略/
决策/快照哈希的严格 JSON。`recover-lock` 必须明确确认进程已结束、提供精确
锁内容 SHA-256，并限定为 `external_timeout` 或 `interrupted_process` 原因；
先写入 write-once recovery receipt，后再次确认锁字节未变才删除锁。旧版
`b'lock'` 仅在额外 `--allow-legacy-lock` 下可恢复。恢复收据永久保留在运行
目录，不能用“直接删锁”替代。

这项质量与内容身份工作不占用 Gen3 的 42 个策略试验预算，也不改变 Gen2。
它仍不是 PIT manifest、不是锁箱，**不授权**回测、候选生成或账本写入。下一
个数据准入阻断仍是 tradability、index constituents 与其他需要 PIT 证明的来源。

市场范围另由 [GEN3_MAINBOARD_UNIVERSE.md](GEN3_MAINBOARD_UNIVERSE.md) 收窄为
沪深主板；内容快照本身不等于可交易主板 universe，也不包含创业板、科创板、北交所
或其他板块。

该范围层已在本地完成 3,400 个活跃主板成员的内容归属，universe hash 为
`sha256:019754716649c9c5b1322ccaa55ec4ffdb81e426db9ce1e08ad53740a7a145a0`；它仍
不解决每日 tradability、benchmark/index constituent 或 PIT 问题，因此不授权回测。
