# 自动技术规则发现（第一版）

更新时间：2026-08-09。本模块让研究系统在**预先冻结的、有限的 DSL grammar**中自动产生并筛选技术指标与形态候选。它的目标是建立可证伪的研究线索，不是保证盈利、生成交易指令或自动发布规则。

## 边界与原则

- 不要求研究人员先给出策略。生成器会组合均线、RSI、ROC、突破、成交量、K 线形态、历史上下文和有限的三条件组合；
- 候选生成是 outcome-blind：生成函数没有行情、收益、胜率或排名输入。固定 `generation_id`、seed、grammar version、候选预算、AST/条件上限后，候选空间可重放；
- 每条候选都必须经过正式 DSL 编译器；所有 metric offset 都必须小于或等于零。规则筛选复用既有向量化求值器、交易成本、T+1 开盘进入、固定周期收盘退出、点时股票池、FDR 和信号相关性去重；
- 自动筛选通过只是 `eligible_for_frozen_campaign`，不等于有效、批准、发布或可交易。系统不创建 Campaign、不修改正式规则目录、不下单。

## 代次与不可变协议

一个 discovery generation 是一次不可变试验：`candidate_budget` 是本代总试错预算，而不是可在看到结果后继续追加的配额。运行前会写入：

- `search_protocol.json`：现有筛选协议，绑定候选、时间边界、股票池、成本和数据快照；
- `auto_discovery_protocol.json`：grammar、seed、语义去重、复杂度限制、父代、累计预算、复验策略和锁箱边界；
- `candidate_space.json`：每条候选的正式/跨代语义 hash、AST 节点数、条件数、offset 和指标依赖。

同一输出目录一旦存在即拒绝重跑或覆盖。相同代的结果不能反向改变该代 grammar、窗口或阈值。

若要开展下一代，必须使用新的 `generation_id`、登记父代状态注册表、累计预算，并设定父代研究结束日之后的**新验证窗口**与不同的最终锁箱边界。父代已归档的逻辑语义 hash 会被排除，避免仅改版本号后重复试验。下一代可以依据已归档的负结果调整 grammar，但不能复用本代已看过的验证区间来“修补”本代。

当前 `restricted-ta-grammar/v1` 在默认复杂度限制下只有 **100 条唯一逻辑**，不是无限候选池。若首代归档 64 条，下一代最多剩余 36 条；CLI 会在读取行情前显示并拒绝超过剩余容量的预算。耗尽后必须先发布并冻结新的 `grammar_version`，而不是重新尝试已归档候选。根据父代证据自动提出新 grammar 的能力属于后续版本，不是本 V1 的隐含行为。

## 市场状态专属证据与复验

规则不必同时穿越 bullish/bearish；它可以只在 `bullish`、`bearish` 或 `unknown` 的一个明确状态中作为研究候选。不过，每个可选状态记录都必须有：

- 明确的状态和至少登记数量的不同持有周期；
- 样本量、跨全部候选/分组的 FDR 结果、置信区间和最佳分组；
- 2×/3×（或协议中登记的）成本压力结果；
- `validated_at`、`revalidation_due`/`valid_until` 与完整的状态专属证据。

`select_current_regime_candidates(registry, regime, as_of)` 只返回当前状态精确匹配、仍为 `active` 且未到期的研究候选；不会把 bullish 证据借给 bearish，也不会把任何返回值标记为可执行。到期当日即失效。

`retire_expired_or_drifted(...)` 只接受新样本外窗口的重验证证据，要求显式 `validation_window_id`、`is_new_oos=true`，并拒绝任何触及最终锁箱的输入。样本不足会转为 `needs_revalidation`；FDR 失效、置信区间下界非正（默认）、平均净超额低于协议阈值或相对基线跌幅过大时会标记 `drift_triggered` 并退役。状态不匹配的证据不会影响该状态注册。

注册表使用稳定的 `registry_id`/`origin_registry_hash` 标记初始冻结证据；`registry_hash`/`registry_state_id` 覆盖所有当前选择、代次、时间边界、复验策略和状态内容。每次生命周期检查都会保留 `previous_registry_hash`、递增 `lifecycle_revision` 并计算新的当前状态 hash，因此退役不会伪装成原始注册表未变化。

## 运行

以下命令是已完成 generation `g_20260809_01` 的历史运行记录；对应输出目录已经存在，不得原地重跑或覆盖：

```powershell
python scripts\run_auto_discovery.py `
  --generation-id g_20260809_01 `
  --start 2010-01-01 --end 2023-12-31 --oos-start 2022-01-01 --lockbox-start 2026-09-01 `
  --symbol-limit 300 --candidate-budget 64 --seed 20260809 `
  --output-root data\auto_discovery\g_20260809_01
```

`g_20260809_01` 已完成，并将 RSI、ROC、breakdown 三条人工选择候选送入正式比较；三者最终均为 `research_eliminated_event`。ROC 仅在三者中相对第一，不是可交易结论。正式比较 result hash 为 `sha256:e38d07cabb182c5f8de97a1149d0b1ae172638dd7b44daee43dbdea6cf39cebb`，最终锁箱未读且批准/发布禁止。

Gen2 不得直接套用父代已查看的窗口。截至父代研究结束日（当前治理口径为 2026-08-04）已被筛选、Campaign 或比较流程查看的数据不再是新鲜 OOS；2026-09 之后尚未到来的数据可先预注册为未来验证，但在到来前不得运行。下一代必须按顺序试验治理：先冻结新的 generation/grammar、父代注册表、跨代去重和累计预算，再等待或取得父代研究结束后真正未查看的未来验证窗口，并设置更晚的独立锁箱；条件不满足时不应启动 Gen2。

Gen2 第一阶段的实现位于 `packages/research/gen2_discovery.py` 和 `scripts/run_gen2_discovery.py`，只允许预注册/干跑，不接受行情输入或筛选命令。它以 wrapper 保留单证券 DSL 的接口，在正式筛选入口再执行 benchmark 对齐的 regime/relative-strength 和证券自身波动率/成交量过滤；完整约束见 `GEN2_DISCOVERY.md`。

## 产物与人工门禁

运行还会生成：

- `round.json` 和 `candidates/<hash>.json`：既有筛选层的完整结果；
- `trial_ledger.json`：本代每次试验及其跨代逻辑语义 hash；
- `regime_candidate_registry.json`：与正式人工规则注册表隔离的状态候选注册表；
- `report.md`：按状态汇总的研究报告。

候选即使通过所有筛选门槛，仍只能由人工挑选进入一个单独派生的冻结 Campaign。该 Campaign 还需要强快照、walk-forward/独立复核、未读最终锁箱和正式人工审批。自动发现模块不提供 `--approve`、`--publish` 或执行路径。

## 已知限制

- 当前 grammar 是刻意有限的日线技术规则，不涵盖横截面因子、行业轮动、仓位管理或动态出场；
- `two_candle_reversal_context` 只表示相邻反色 K 线加趋势上下文，不宣称实体已完全吞没；真正的吞没形态须在后续 grammar 版本中显式登记实体覆盖条件；
- 状态定义沿用基准指数相对其移动均线的历史可见口径，不等同于对未来市场的预测；
- 筛选层是快速证据评估，不能替代冻结 Campaign 的完整验证；
- 2026-08-09 修复了搜索层的持有期退出价格错位与 n 元算术向量化差异。此前搜索轮次必须按修复后的口径重跑，不能直接作为晋升依据。
