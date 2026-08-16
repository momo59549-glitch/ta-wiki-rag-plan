# Gen3 VectorBT 小样本集成 spike

这是一个引擎集成验证，不是策略发现、全市场筛选、候选登记、锁箱验证或 42 次试验的一部分。
它使用冻结主板 universe 的严格 loader，只取按 symbol 排序、且已冻结 trend content entry 覆盖完整
窗口的前 20 个成员和固定窗口 `2019-01-01..2021-12-31`；不随机抽样，也不按收益挑选证券或策略。
该 coverage 规则只排除未覆盖窗口的历史/晚上市成员，不依据价格表现。

宽表只包含 OHLCV，三组预先固定且全部输出的 close-time 演示信号是：10/30 日均线交叉、
14 日 RSI（30/55）与 20 日动量符号。每个 T 收盘信号被显式 shift 到 T+1，且 entry 和 exit 均在
T+1 开盘使用 open fill price。RSI 的滚动 loss 为零时按缺值处理，不人为生成极端 RSI 信号。
VectorBT `0.27.3` 实际运行 shared-cash/grouped portfolio，初始现金 100,000、每订单固定 value
5,000、`call_seq=auto`，且报告 orders、trades、
total return 和 max drawdown；这些数值并不用于宣布赢家或选择参数。

VectorBT 不原生裁决 A 股停牌、涨跌停或真实可成交性。它也不应替代已有的 conservative
exploratory filter；未来探索性执行必须先应用该过滤器，且仍不得称为官方可交易性。输出只写入
gitignored `data/vectorbt_spikes/`，并固定为 approximate、non-adjudicable、无试验预算、无锁箱。

## 实际 spike

此前 `vectorbt-spike-d496f2236cd27b39dd440fba5ddfda29340d8ca46c40641b3ad9654118561ede.json`
使用了错误的 exit close price 和隐式无限订单大小，现标为
`invalidated_by_execution_price_and_sizing_fix`。它保留以便审计，但不得用于任何判断。

修复后的新 write-once artifact 会单独记录；旧 artifact 的 orders、trades、return 和 drawdown
不得引用、比较或用于下一步筛选。

修复后的实际 artifact 为
`vectorbt-spike-1a7c40393bb89700d788cc30c69798d45fe224f6cfe51255a01b3916fda63584.json`，hash 为
`sha256:1a7c40393bb89700d788cc30c69798d45fe224f6cfe51255a01b3916fda63584`，实际耗时约 19.263 秒。
它冻结了 T+1 open entry/exit、100,000 initial cash、5,000 value orders 与 `call_seq=auto`；三组
预提交演示都记录在该 artifact 中，但数值仍不得解释为候选收益、赢家或下一步筛选依据。
