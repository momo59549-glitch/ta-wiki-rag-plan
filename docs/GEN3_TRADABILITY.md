# Gen3 可交易性证据充分性

本阶段只建立 draft/nonformal 合约，不能把日线 observed row 当作可交易事实，不能把
无行当停牌，不能从成交量推买卖，也不能只由收盘价推涨跌停。完整准入需要 trade
calendar、daily observation、ST、suspension、daily price limits 和 listing/delisting
六类带 PIT revision/content/available-at 证据；缺任一类即
`blocked_missing_authoritative_sources`。

每份证据的事实值也必须进入 canonical record hash：日历冻结交易所与开闭市，日线
冻结开盘价，涨跌停冻结上下限，上市/ST/停牌各冻结其布尔事实。每日可交易状态只能从
这六项 facts 和已绑定的交易日历派生；闭市日必为不可交易并带 `market_closed`，而不是
由调用方传入任意买卖标志。特征研究仍遵守 next-session-only；09:30 的执行判断只接受
该时点之前可得的证据。

当前本地 trend/supplement schema 可观察 `is_st`、raw_prev_close、OHLCV，但没有独立
trade_cal、suspend、daily limit 或可信 ST 历史文件，故仍 blocked。后续须由用户授权
官方 API probe/download；草案接口名为 `trade_cal`、`suspend_d`、`stk_limit`，ST 历史
候选 `namechange` 的 PIT 能力仍是 unknown。官方逐日历史 ST 接口为 `stock_st`
（doc 397；3000 积分、约 09:20 更新），但其数据自 2016-01-01 起，故研究窗 2015
全年仍有缺口并继续阻断；`namechange` 只能作为 2015 补充候选，须另证公告时间与
覆盖，不能直接 admit。官方文档：trade_cal doc 26、suspend_d doc 214、stk_limit
doc 183。本阶段不联网、无
数据写入、不授权回测/候选，也不占 42 次预算。

本机安全检查未发现可用 `TUSHARE_TOKEN`，因此没有发出网络请求。若官方 probe 在
未来获授权但仍不能得到完整证据，可使用**仅合成 fixture**的保守 exploratory fallback：
T+1 无行不买、卖出延后至下一可观察行并记录 delay、非正量/ST/一字或疑似封板不成交，
并使用至少 2 倍成本压力。输出固定为 `approximate_tradability=true`、
`official_tradability_verified=false`、`adjudicable=false`；不能用于真实策略回测、候选、
锁箱或 42 次预算。未来 probe 产物只可 write-once 存在 Git ignore 的
`data/gen3_tradability_probe/`，不得包含 token 或响应正文。

备用 evaluator 采用显式、有序交易日历和 OHLCV 行映射：买入无行立即拒绝；卖出从计划日
开始跨越无行、零量、ST 与内部由 OHLC 精确相等派生的一字/疑似封板行，直到下一合格行
或日历结束未解决。它输出基础成本和 2 倍成本压力净收益。框架未来可用于显式标记的
exploratory 研究；当前 CLI 仅运行内存 synthetic scenario，绝不扫描真实市场源，且始终
不可裁决。
