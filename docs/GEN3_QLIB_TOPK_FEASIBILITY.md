# Qlib TopkDropoutStrategy 官方可行性审计

这是一页源码/API 时序审计，不是收益回测。审计对象为已安装的 Microsoft Qlib/pyqlib 0.9.7；未运行
Topk、Exchange 或任何真实收益计算。

## 时序结论

官方 `TopkDropoutStrategy.generate_trade_decision` 在当前 `trade_step = d` 取得当前交易区间，随后以
`get_step_time(trade_step, shift=1)` 取 **更早一个交易步** 的 prediction。Qlib
`TradeCalendarManager.get_step_time` 使用 `calendar_index = start_index + trade_step - shift`，所以正 shift
是向前一根 bar。对于模型 prediction index 为 T（特征含 T 收盘）的日频流程，Topk 在 T+1 的交易区间
读取 T 的 signal：不应把 prediction index 再整体 +1，否则会变成 T+2。

CN 初始化的官方默认 `deal_price` 是 `close`，所以该默认路径为“用 T 收盘信号，在 T+1 close 成交”，而非
同日成交。若未来的独立可交易性准入允许使用 T+1 open，应显式设 `deal_price="$open"`；Topk 的 `shift=1`
不变。`Exchange.get_deal_price` 若指定价格为空会回退 `$close`，因此该配置还必须将缺失 open 的会话作为
不可成交并由权威停牌/涨跌停证据控制，不能把回退当成统一 open 执行。

## Exchange 字段与当前来源

`Exchange.__init__` 的必要查询字段是 buy/sell deal price、`$close`、`$change`、`$factor`、`$volume`；CN
默认 `limit_threshold=0.095`，`_update_limit` 用 `$change` 和 close 缺失判断限价/停牌。

| 字段/表达式 | 当前 OHLCV | 审计结论 |
| --- | --- | --- |
| `$open`, `$close`, `$volume` | 有绑定原始行 | 可直接转换，仍受权威可交易性 gate 约束 |
| `$change` | 可由相邻冻结 close 数学导出 | 不是权威 daily-limit/ST/停牌记录；不能单独作为 A 股限制证据 |
| `$factor` | 无 | 不能从 OHLCV 严格得到复权/公司行动因子；缺失时 Qlib 会退到 adjusted-price mode，不能支持 A 股 100 股单位的正式结论 |
| `limit_buy`, `limit_sell` | 无 | 需要权威每日涨跌停与停牌/ST 覆盖；现有 observed-row audit 不足 |
| official trading calendar | 无 | 现有 calendar 只是 observed-session approximation，不能作为正式交易日历 |
| benchmark/index | 无 | 不能生成可裁决超额收益或门槛 |

用户现已授权一项更窄的 **approximate/nonadjudicable** 实施验证：固定同一 200 只、2019--2021，复用已保存且
哈希验证过的 prediction，不重训；使用官方 `TopkDropoutStrategy(topk=30, n_drop=3, only_tradable=True)`、官方
`SimulatorExecutor`、`deal_price="$open"`、`trade_unit=100`。Topk 内置 shift 仍保持 prediction T → T+1 trade。

薄 provider 只补 Qlib 所需的字段：`factor=1` 仅表示本地已复权价格，`change` 严格取相邻真实 close 的
`pct_change(fill_method=None)`；不伪造首日变动。缺观察行、零成交量、本地 `is_st=true` 或 OHLC 一字行会写入
`limit_buy/limit_sell`，由官方 Exchange 的 tuple limit gate 阻断。它们不是官方停牌、涨跌停或完整 ST 事实，故仍不
能称为可交易性验证。使用 base（open 0.05%、close 0.15%、min 5）和 2x 成本；没有独立官方 benchmark 时只输出
绝对官方 Qlib 指标，不伪造 excess。

该产物仅可标记 `worth_data_upgrade`（预先固定为 base 年化 >8%、最大回撤 <30%、2x 年化 >0）以决定是否值得补数据，
绝不是候选、锁箱、42 次试验或正式收益结论。缺少官方交易日历、逐日停牌/涨跌停/ST、公司行动/PIT receipt 和独立基准
仍是下一准入阻断。

## 已完成的一次固定近似运行

唯一完成的运行是 `topk-approx-fixed200-2019-2021-v4`，结果哈希为
`sha256:8af3784ae0bb577257ee518c6b9830ed586a720e0091e1545d9f0cd6a6e7955a`。它复用诊断 prediction
`sha256:6afaa3f1d43d35b51350488596cbb10a794e3f6c2bfd462e6315e1ac14c9f375`，包含固定有完整窗口的有序前 200 只，
不重训、不读 2022+。pyqlib 0.9.7 的日执行器需要一个随后的 provider calendar 项，因此实际交易步为
2019-01-01--2021-12-30；2021-12-31 只作边界，未产生该日交易步。

官方 Qlib `backtest`/`risk_analysis` 输出：base 成本年化 22.7439%、总收益 87.3308%、最大回撤 -26.0828%、
官方交易计数 4,868；2x 成本年化 15.8715%、总收益 57.0225%、最大回撤 -28.2846%、官方交易计数 4,884。
这使预注册的 `worth_data_upgrade=true`，**仅表示值得优先补齐数据**；并不改变其 `not_candidate`、non-PIT、
近似可交易性、nonadjudicable、无试验预算、无锁箱的状态。没有独立官方基准，所以没有输出或解释超额收益。

为忠实记录兼容过程，`v1` 因 `benchmark=None` 被 pyqlib 回退为不可用的 `SH000300` 而停止；`v2` 使用了官方
Series 报告占位后仍在末日触发日执行器日历越界；`v3` 在本地命令时限内未完成。三个目录均保留在 gitignored
`data/qlib_spikes/`，不可用于任何判断；`v4` 是唯一完成结果。

## 同一假设的 v6 基准与年度诊断

在不重训、不改预测、不改样本或 Topk 参数的前提下，v6 将透明的日度横截面等权 close-to-close return Series
作为 Qlib 官方 `benchmark` 输入。公式为 T 日所有同时拥有原始 adjusted close(T-1) 与 close(T) 的固定样本证券的
`mean(close(T)/close(T-1)-1)`，严格 `pct_change(fill_method=None)`；其内容哈希
`sha256:a92b53427945a6748a8d63101f503f8c1979e754466ae03a7161e926de7ec27f`，基准定义哈希
`sha256:6d774c3a0ee72f88c5db5dd4528bd2e5db0db01217b4709fa1c8b514e23fd460`。这是无成本、幸存者偏差的
日度等权比较序列，**不是指数，也不是 buy-and-hold**。

v6 结果哈希为 `sha256:7ff7bd0dc74978b41286e555624b3b612f0daccd8d4e2e9a8c97764635bf6fa5`。base 的官方 Qlib
excess 年化为 4.9892%、IR 0.6601、最大回撤 -12.1434%；2x 成本 excess 年化 -0.8920%、IR -0.1223、最大回撤
-18.0295%。策略年度净收益/基准收益/算术超额分别为：2019 25.9179%/23.8937%/+2.0242%，2020
6.3098%/11.5034%/-5.1936%，2021 39.9421%/16.5276%/+23.4145%。因此 `positive_excess_each_year=false`，即便
绝对指标仍满足既有的 `worth_data_upgrade` 门槛，也不能据此晋升候选。

v6 保存并冻结了 Qlib 官方 base/2x daily report、positions 与 indicator 的文件身份；它们仅在 gitignored
产物目录中，供复现审计。所有非 PIT、近似可交易性、无独立正式基准、nonadjudicable、无试验预算和非候选限制不变。

## 五项计数 implementation trial

已按预注册的有限表执行五项 Qlib 官方组合 implementation trial，且没有第六项：A control (topk=30, drop=3)、
B (30,1)、C (50,1)、D (50,3)、E (100,3)。全部复用同一 prediction、固定 200 只、2019--2021 窗口、
T+1 open、100 股单位、observed gate、透明 v6 基准及 base/2x 成本。结果表哈希为
`sha256:2247295066fc8a6a4c8945869c43e0e20c2203d4c3926f3e5e2beac7df23727e`，并冻结每项 base/2x 的官方 Qlib
daily report、positions 与 indicator 身份。

这五项是已查看的 implementation trials，**不是免费筛选**：若将来正式采纳计数，它们最多会占 global 42 中的
五项；当前不进入锁箱或 Gen2 ledger，也不得由该结果增加第六项。预注册门槛为 base 绝对年化 >8%、base excess
年化 >3%、base 最大回撤 <30%、2x 绝对和 excess 年化均 >0、且 2019--2021 每年 base excess 均为正；通过者仅按
2x excess 年化降序（同值按 trial id）排序。

只有 C(50,1) 与 E(100,3) 通过全部门槛；C 的 2x excess 年化 8.7665%，高于 E 的 5.7454%，故机械排序结果为
`C_topk50_drop1`。A、B、D 均因至少 2020 年 base excess 为负而失败。这个排序仅决定未来是否值得做数据升级，
不能升级为候选、收益结论、历史 OOS 或锁箱验证。
