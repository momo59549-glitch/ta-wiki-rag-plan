# Gen3 沪深主板 Universe 准入

状态：仅限本地、只读、非正式的 Phase 1 草案。范围**只包括沪深主板**：`000`、
`001`、`002`、`003`（SZ）及 `600`、`601`、`603`、`605`（SH）。`300/301`
创业板、`688/689` 科创板、北交所 `4/8` 前缀及任何其他代码都明确拒绝。

清单为显式 `a_share_history.jsonl` 的 UTF-8 原始 bytes 身份；每行必须是
`tushare.stock_basic` 的严格 10 字段 schema（含 `name`、`list_status`），并要求
名称、symbol/ts_code/exchange、status、日期和 UTC aware `fetched_at` 合法。合法的
非主板记录会计入 `excluded_by_board` 后跳过；只有主板前缀记录的 exchange/market
自相矛盾才 fail closed（北京交易所 `BSE/BJ` 记录作为合法非主板记录排除）。policy
还冻结原始 manifest record/exclusion counts、trend quality snapshot/source contract 和
两条精确 zero-row 例外。成员定义为
`active_from <= 2026-08-05` 且 active_to 缺失或不早于 `2015-01-01`。这是 2026
抓取到的历史状态证据，**不是** PIT revision manifest。

内容覆盖优先使用已验证的 trend content run。仅当主板成员不在 trend 中时，才
读取 `tushare_daily_cache/<symbol>.parquet` 的明确直接子文件；不枚举补充 root，
也绝不允许补充覆盖已有 trend symbol。补充使用 `trade_date` 和相同 OHLCV 严格
校验，扫描前后检查 footer/size，并冻结行 canonical hash。零行 trend member
仅在 policy 精确冻结的 `000562/2015-01-26` 或 `601268/2015-05-21`、且 trend
文件/entry 完整时才标为
`explicit_no_observed_trading_rows`；它不是“仍在交易但暂时停牌”的推断。

## 已完成的真实主板运行

主审已完成一次本地只读主板 universe 运行：manifest 共 5,875 条，合法非主板
排除 `excluded_by_board=2,394`，研究窗口活跃沪深主板成员为 3,400。完整已验证
trend content snapshot 仍保留 3,464 个 entries；其中 81 个不属于研究期活跃成员，
只保留在源快照身份内，不进入研究 universe。

最终成员归属为：trend 非空 3,381、精确冻结的 zero 例外 2（`000562`、`601268`）、
本地补充 17、missing 0。补充证券均为 2026 新上市，单证券研究期行数为 1–140，
合计 1,276 行，最晚日期 `2026-08-04`。运行工件为 923,472 bytes，且没有残留
`.lock` 或 `.tmp`。

- policy：`sha256:75540472dc39054924b5a26921a3814341cd7fbe67ef35c3e7f31a6106653be7`
- predecision：`sha256:89267c9b570bcd6b20d2af8c877b4742a2cc7d647de7ce7103b42c2e2e16dafb`
- universe：`sha256:019754716649c9c5b1322ccaa55ec4ffdb81e426db9ce1e08ad53740a7a145a0`
- supplement run：`mainboard-supplement-f355b...8100d`

运行未联网、未修改任何源文件；生成目录受 Git ignore 保护且不提交。用户指定的
范围仍严格不包含创业板、科创板、北交所或其他板块。

补充 runner 使用显式 prepare/每批最多 100/锁/write-once entry；CLI execute
需要 `--confirm-read-source`。它不修改既有 content run 或任何源文件。完整
状态 `mainboard_universe_content_complete` 只在每个成员都归属到非空 trend、合法
显式零行或非空补充时产生，并冻结逐证券 source/entry hash 和全体 universe hash。

这不是 PIT、锁箱或回测授权，不占 Gen3 的 42 个策略试验预算。下一数据阻断仍是
tradability（停牌、涨跌停）以及 index constituents / benchmark 的 PIT 证据。
