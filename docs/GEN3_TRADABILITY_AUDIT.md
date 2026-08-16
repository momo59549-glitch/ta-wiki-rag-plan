# Gen3 探索性执行可行性审计

此层只统计冻结主板 universe 上 OHLCV 观察覆盖、零量、ST、一字板和缺观察日。它分为两个
可恢复、write-once 阶段：先逐证券写 `calendar_reports/<symbol>.json`，在所有冻结成员完成后才
生成唯一的 `observed_calendar.json`（排序 union、hash、最小/最大日期和数量）；随后才写
`reports/<symbol>.json`。因此每个缺观察日都相对于同一个 run 级 session union，而非证券自身的
行集合。

该 calendar 固定标为 `observed_session_calendar_approximation`，不是官方交易日历，也不能把
缺行解释为停牌。源文件在每个扫描前后复验 footer/content identity；status 与 finalize 只校验
write-once 工件，不重扫行情。锁是绑定 run、policy、PID、UTC 创建时间及自身 hash 的结构化 JSON；
残锁一律保留并要求人工审查，当前没有“自动删除/恢复”命令。临时文件、未知根目录工件和
hash/归属篡改都会 fail closed。

fallback execution policy 的实际成本参数和 2x multiplier 也随 run 冻结，不能用任意 hash 代替。
它不是策略回测：不产生收益、年化、alpha、赢家或候选，不占试验预算且不可裁决。

## 已完成的真实只读审计

真实 run `tradability-audit-69c14830116a17d250f3100754e7ac670293019242266e4851caafab02870a75`
已完成 3,400/3,400 calendar 与 3,400/3,400 audit reports。其 policy 为
`sha256:730b16047b749bf201e0462dc173589a883b3c235268f552726073c012e6f155`，绑定的
mainboard universe 是 `sha256:019754716649c9c5b1322ccaa55ec4ffdb81e426db9ce1e08ad53740a7a145a0`。
冻结 observed calendar 有 2,816 个 sessions，hash 为
`sha256:889d9539d4a20f25f9938f2258bc23eb4eb0f15754107df8a6a8ee1d54ad13b4`；最终审计 hash 为
`sha256:2635430954e33e29dcfa9b8a5dc856ab0635ae2c0e21c0a6d59d3d7a599ae605`。

汇总为 7,871,966 observed rows、1,702,434 missing observations、4 zero-volume rows、627,588
ST rows 与 37,818 one-price-board rows。完成时没有 lock、tmp 或未知根工件，finalize 两次输出一致。
结果仍严格是 approximate、non-official、non-adjudicable：它不能把无行推断为停牌，也不提供
官方涨跌停/停牌/交易日历证据。审计完成后的关机未执行，且该运行状态与研究资格无关。
