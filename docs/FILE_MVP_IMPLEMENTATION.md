# 无 SQL 文件型 MVP 实现说明

## 已实现

`H:\股票模型\Model\data` 是只读行情源，系统不复制约 1.6GB 的历史数据。

```text
Model/data/trend_cache/*.parquet
  -> LocalParquetMarketData
  -> Rule DSL / Scanner
  -> Observation
  -> 1/3/5/10/20 日 Outcome
  -> JSONL 明细 + Markdown 报告
```

运行产物：

```text
data/research_runs/<run_id>/
  config.json          # 规则、标的、区间、数据源
  run.json             # 快照、语义哈希、数量和跳过标的
  observations.jsonl   # 信号及逐条件解释
  outcomes.jsonl       # entry/exit/return/MFE/MAE
  report.md            # 分周期描述性汇总
```

## 数据选择

默认使用 `trend_cache`，因为它同时包含前复权 OHLC、原始价格、成交量、成交额、复权因子、ST 标记和名称。`local_cache` 可作为基本面扩展，`etf_cache` 后续增加单独适配器。

## 文件身份

当前 `dataset_snapshot_id` 基于 dataset、证券代码、文件大小和修改时间生成，适合快速本地迭代。规则使用 canonical JSON 的 SHA-256 语义哈希。正式发布或共享研究结果前，应建立全量 manifest 哈希和数据授权卡。

## 当前能力与限制

- 默认以 `etf_cache/000001.parquet` 计算同日开盘到收盘的基准和净超额收益；
- 默认扣除单边 3 bps 佣金、5 bps 滑点，可通过 CLI 修改；
- `--oos-start` 将结果隔离成样本内与样本外汇总，不将二者混写为一个结论；

- 尚未模拟停牌、涨跌停和成交量容量；
- Outcome 仍是描述性结果，尚未加入行业基准和显著性检验；
- 只有锤子线 v1 规则；
- 尚未实现 Hypothesis 审批、样本外切分和 MLflow；
- JSONL 是可迁移的权威文件，当前不需要 PostgreSQL。

## 下一实施切片

1. 增加市场状态和流动性分层；
3. 把现有 `Model` 策略包装成统一 Rule/Experiment adapter；
4. 实现文件型 Hypothesis/Approval；
5. 最后才评估是否需要数据库和 LangGraph。
