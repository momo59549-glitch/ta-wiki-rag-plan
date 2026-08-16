# Gen3 VectorBT 一次性候选粗筛

这是一次受限、预注册的 engine screen，不是正式候选发现框架。严格 loader 冻结主板状态后，按
symbol 选择其 trend content identity 覆盖 `2015-01-01..2021-12-31` 的前 200 个成员；该规则只依赖
已冻结覆盖，不读取收益结果。发现期为 2015–2018，确认期为 2019–2021，脚本不把 2022+ 行放入
指标或宽表。为复验 identity，底层 loader 可能读取完整文件 footer/content identity；这不是研究期使用。
完整性下界固定为该窗口的首个观察交易日 `2015-01-05`：`2015-01-01` 是闭市日，不能被错误地要求
存在行情行。

候选表固定为 20 个语义 hash：SMA/EMA cross、RSI、20/60/120 动量、20/55 breakout、Bollinger
reversion 和量价确认。T 收盘信号在 T+1 open 以 VectorBT 执行；可交易 gate 需要原始观察行、正量、
非 ST 且非 OHLC 一字板。估值 close 可仅为 VectorBT valuation 前填，绝不作为行情、信号或成交。
计划 exit 如果 T+1 gate 不可用，会确定性延迟到下一 eligible 原始行。

每个候选都同时输出 base（fees .0003/slippage .0005）与 2× stress、shared cash、fixed value orders，
以及固定样本等权 buy-and-hold approximation benchmark。它不是官方指数，也带有固定样本幸存者偏差。
预先门槛为每期年化 >8%、相对 benchmark excess total return >3%、max drawdown <30%、2× 年化 >0、
至少 30 trades；只有发现和确认都满足才标为 survivor。survivor 仍不是候选、锁箱结果或试验预算结果。

所有输出为 gitignored `data/vectorbt_screens/` write-once JSON，固定 approximate/nonofficial/nonadjudicable、
无 trial budget、无 lockbox。VectorBT 不能裁决 A 股停牌和涨跌停；本 screen 只复用保守 observed-row
gate，不产生正式可交易性结论。

## 已执行的固定 200 证券批次

早期 `v1` 工件 `19a55c8361f3cceb1485568c36e358f23eb047c641a98b9c0155f7225693b27e` 已标记
`invalidated_by_period_leakage_and_rsi_boundary`，仅保留为审计记录，不能用于任何判断：它错误地把
完整 2015--2021 矩阵交给每个期别 portfolio，可能使发现期持仓和期后价格进入确认或发现统计，且 RSI
在零 loss 边界错误地产生 NaN。

后续的 `v2` 工件 `596e0b904fbea2c997603fd8cb1667c460d2ce0a1fc4c51dd6a49cc7accc8955` 也已标记
`invalidated_by_annualization_benchmark_delay_and_signal_ffill`，仅保留审计记录，不能用于判断：它仍以
VectorBT 默认日历年口径年化、基准 T+1 不可成交时会丢失首笔订单，且部分技术信号错误使用了估值
forward-fill close。

修正后的 `v3` 仍使用相同冻结样本、候选和门槛，但仅用全历史 close 作指标预热；每个 period 的
VectorBT portfolio（open、valuation、entries、exits）都严格切到该期 session，确认期从独立初始现金
开始。期末未平仓以该期最后一个估值价 mark-to-market，未来 exit 成本没有预扣；2× 成本门槛仍同时
适用。所有年化均直接调用 VectorBT `year_freq="252 days"`。RSI 定义为：正 gain 且零 loss 为 100，
gain/loss 均零为 50。基准的首次建仓同样作为 close 信号，从 T+1 起延迟至首个 eligible open。指标
只读取 raw close/volume（`pct_change(fill_method=None)`）；forward-fill close 仅用于组合估值。

`v1` 的 2026-08-13 固定 200 证券运行耗时 395.8 秒、20 个预注册语义候选中 0 个满足全部门槛；
这不是修正后结果，不能被引用为筛选结论。修正批次完成后将以独立 write-once `v2` identity 记录。

无效的 `v2` 批次耗时 404.3 秒，工件为
`vectorbt-screen-596e0b904fbea2c997603fd8cb1667c460d2ce0a1fc4c51dd6a49cc7accc8955.json`，身份为
`sha256:596e0b904fbea2c997603fd8cb1667c460d2ce0a1fc4c51dd6a49cc7accc8955`。相同 20 个预注册候选中，
0 个同时满足发现期和确认期的全部预先门槛；该计数不能被引用。`v3` 完成后会以独立 write-once
identity 记录，且不会占用 trial budget、进入候选账本或锁箱。

`v3` 批次已完成，耗时 400.8 秒，工件为
`vectorbt-screen-69428095be883ea57ef1d827d22ce13629d97770f19c9106419a58f52a13ebc3.json`，身份为
`sha256:69428095be883ea57ef1d827d22ce13629d97770f19c9106419a58f52a13ebc3`。相同 20 个预注册候选中，
0 个同时满足发现期与确认期的全部固定门槛。因此到此停止；不会基于这个结果增加候选、改变门槛或
扩展到 3400 证券。它仍是 approximate、nonofficial、nonadjudicable 的 engine screen，不构成任何
策略结论。
