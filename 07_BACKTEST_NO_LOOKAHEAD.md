# 历史回测、防未来函数与研究可信度

## 1. 回测目标

回测用于验证“如果在当时只能看到当时可用数据，按明确执行规则会发生什么”。它不是收益承诺，也不能自动证明未来有效。

MVP 支持日线/较低频 bar、单标的与简单等权组合。事件驱动和复杂组合风控放到 V1。

## 2. 不可变 Backtest Manifest

```yaml
backtest_run:
  run_id: uuid
  code_commit: git_sha
  engine_version: 0.4.0
  rule_revision_id: uuid
  rule_semantic_hash: sha256
  parameters: {}
  dataset_snapshot_id: uuid
  universe_snapshot_id: uuid
  calendar_version: ...
  corporate_action_policy: split_adjusted
  feature_manifest_id: uuid
  start: 2015-01-01
  end: 2025-12-31
  decision_time: bar_close
  execution_time: next_bar_open
  costs:
    commission_bps: 3
    slippage_model: fixed_bps
    slippage_bps: 5
  seed: 42
```

manifest 创建后不可编辑；重跑创建新 run，并可指向父 run。

## 3. 时间语义

每条数据同时考虑：

- 事件何时发生；
- 数据供应商何时发布；
- 系统何时可获得；
- 策略何时计算；
- 订单最早何时成交。

默认：

- 日线形态在收盘后才完整；
- 使用收盘形成的信号，最早在下一可交易 bar 开盘执行；
- 若使用下一日确认，信号时点移动到确认 bar 完成之后；
- 基本面、成分股和公司行动使用 point-in-time 可用时间。

## 4. 防未来函数强制措施

### 4.1 代码层

- DSL offset 只能 `<= 0`。
- 特征 API 必须显式 `as_of`，返回 `available_at <= decision_time` 的数据。
- rolling 默认右闭于当前时点，绝不居中。
- 标签/未来收益存放在独立 namespace，运行时策略模块不可导入。
- 回测 Worker 使用最小数据视图，避免把未来列一起交给策略。

### 4.2 数据层

- 使用点时 universe，纳入退市标的。
- 数据修订有 vendor revision；回测固定 snapshot。
- 复权处理避免把未来已知的分红信息提前注入。
- 缺失不以未来值插补。

### 4.3 测试层

- **前缀不变性**：追加未来数据后，过去时点的信号不应变化。
- **截断测试**：在每个 decision_time 截断数据，结果与全量计算的该时点结果一致。
- **时间平移测试**：合成序列移动时间后逻辑不变。
- **未来扰动测试**：任意修改未来 bar 不改变过去结果。
- **执行延迟测试**：收盘信号不以同一收盘价无成本成交。
- **成分股测试**：未来加入指数的标的不能提前进入 universe。

任何一项失败都阻止回测报告标记为 valid。

## 5. 执行与成本

MVP 模型：

- 市价单在下一 bar open 成交；
- 固定 bps 手续费和滑点；
- 成交价受 high/low 合理范围校验；
- 停牌/涨跌停/无流动性以市场适配规则处理；
- 仓位和现金不允许隐式为负；
- 分红/拆股与持仓数量一致处理。

V1 增加成交量参与率、价差、冲击、延迟和部分成交。成本模型必须作为 manifest 一部分。

## 6. 信号、订单、成交和持仓分离

实体：

- `SignalEvent`：规则在何时匹配。
- `OrderIntent`：策略决定的方向和目标仓位。
- `Fill`：模拟成交。
- `PositionLot`：持仓批次与成本。
- `PortfolioSnapshot`：现金、持仓、市值、权益。

分离有助于审计“规则正确但执行假设错误”的问题。

## 7. 统计报告

最低输出：

- 总收益、年化收益、最大回撤、波动、Sharpe/Sortino（说明无风险率和频率）；
- 胜率、盈亏比、持有期、换手、成本；
- 交易数和每年/每市场分布；
- 相对基准与买入持有；
- 权益曲线、回撤曲线、月度表、交易明细；
- 参数、数据、代码、规则、成本和警告。

若交易数太少、样本跨度不足或数据质量差，报告必须显著提示。

## 8. 研究偏差控制

- 时间序列切分：train/validation/test，边界留 embargo。
- 参数选择只看 train/validation；test 最多用于最终一次评估。
- Walk-forward：每段只用过去拟合，向前验证。
- 多重检验：记录尝试次数，报告参数敏感性和 data snooping 风险。
- 稳定性：按市场、年份、波动环境、参数邻域分层。
- 负对照：随机信号、错位信号和简单基准。
- 幸存者、前视、选择、样本、交易成本偏差逐项 checklist。

## 9. 可重复性

- 固定随机种子、容器 digest、代码 commit 和依赖锁文件。
- 结果表和报告对象有哈希。
- 相同 manifest 在支持的平台上数值误差内一致。
- 失败 run 保留日志和阶段，不生成“部分成功”的有效报告。

## 10. DoD

- 合成市场的手算交易与引擎逐笔一致。
- 所有未来函数测试通过，且能故意注入泄漏验证测试会失败。
- 成本、停牌、缺失、公司行动、时区和边界条件覆盖。
- 报告可从 manifest 一键重现。
- UI 显示研究局限，不以颜色或文案暗示历史收益保证。

