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

## 当前能力

- 默认以 `etf_cache/000001.parquet` 计算同日开盘到收盘的基准和净超额收益；
- 默认扣除单边 3 bps 佣金、5 bps 滑点，可通过 CLI 修改；
- `--oos-start` 将结果隔离成样本内与样本外汇总，不将二者混写为一个结论；

- 已有停牌、涨跌停、T+1 开盘可见性和非法价格门禁；
- 已有 Research Case 状态机、9 Agent 审计记录、Job/Event/Outbox/Dead-letter；
- 已有 Hypothesis 与 Rule Version 两道独立人工审批；
- 已有 FastAPI、Streamlit、文件 Worker、Prefect 适配、LangGraph interrupt/checkpoint 适配；
- 已有 KnowledgeCard 的 claim-evidence 校验和本地 BM25 检索；
- 已有 Tushare 历史补缺缓存及每日增量覆盖层，均不覆盖 `trend_cache`；
- JSONL/JSON/Parquet 是当前可迁移权威文件，不需要 PostgreSQL。

## 当前限制

- 策略验证按当前要求冻结；保留既有安全测试，不继续寻找收益参数；
- 文件控制面适合单机/小团队；多人高并发达到门槛后再迁移 PostgreSQL；
- Docker 未在当前工作站安装，Compose 已提供但尚未完成本机容器运行验收；
- Prefect Server 的常驻部署需在实际运行环境完成验收；无 Prefect 时文件 Worker 可独立执行；
- MLflow、MinIO、Qdrant、Celery、Kafka、DVC 均未达到引入门槛。

## 下一实施切片

1. 增加 Job payload 的逐类型契约与端到端验收；
2. 增加 Worker 租约心跳、审计校验 CLI 与备份恢复演练；
3. 更新全部架构/Prompt/Backlog 文档中的实现状态；
4. 在不执行策略验证的前提下完成启动栈烟雾测试；
5. 最后按并发量、数据量和团队规模评估是否需要 PostgreSQL。
