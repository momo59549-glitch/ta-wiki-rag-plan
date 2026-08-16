# 08 · 数据库/存储模式

## 结论

当前**没有运行中的关系型数据库**。权威状态是 JSON、JSONL 与 Parquet；PostgreSQL/pgvector 仅为 `MASTER_IMPLEMENTATION_PLAN.md` 的目标架构。Qlib/MLflow SQLite 只出现在若干试验脚本中，不是系统主事务库。

## 文件型“表”与关系

| 逻辑实体 | 实际文件/模式 | 主键/关联 | 主要字段 |
|---|---|---|---|
| Candle / 行情 | `Model/data/*/*.parquet` | `symbol + date`（代码验证日期唯一） | date/index, OHLC, volume, amount, is_st, prev_close/adj factor（视数据集而定）。 |
| UniverseMembership | `data/universes/*.jsonl` | `symbol + active_from` | symbol, ts_code（部分）, list_status（部分）, active_from/to, source。 |
| ST timeline | JSONL（`st_status.build_st_timeline`） | symbol + active_from/to | symbol, ST name, active interval, source。 |
| Dataset snapshot | `dataset_snapshot*.json` | `dataset_snapshot_id` | dataset, symbols, file logical path/size/SHA-256, roots。 |
| Experiment protocol | `experiment_protocol.json` | `protocol_id`, `protocol_hash` | rule definition/hash, data snapshot, periods, horizon, costs, validation, analysis, publication block。 |
| Research Case | `data/research_cases*/<case>/case.json` | `case_id` | state, rule, snapshot, research_run, QA, publication。 |
| Case events | `case_events.jsonl` | event_id, case_id, sequence | state transition, event type, payload, idempotency key。 |
| Observation | `research_run/shards/observations/*.jsonl` | observation id | symbol, observed/executable time, rule/version/hash, conditions, snapshot。 |
| Outcome | `research_run/shards/outcomes/*.jsonl` | observation_id + horizon | entry/exit, raw/net/excess return, MFE/MAE, split, regime, execution flags/reasons。 |
| Shard commit | `shards/commits/*.json` | batch_index + execution identity hash | row counts, SHA-256, skipped symbols, commit hash。 |
| Job | `data/control/jobs/*.json` | job_id | kind, payload, status, lease, cancel state。 |
| Event/Outbox | `data/control/events/*.jsonl`, `outbox.jsonl`, dead-letter | event_id/job_id | correlation, causation, payload hash, delivery status/attempts。 |
| Approval | `data/.../approvals` / governance 文件 | case/rule/actor/role | decision, reviewer, comment, timestamp。 |
| Knowledge Card | `data/knowledge/{drafts,published,rejected}/*.json` | card_id | title, claim, evidence refs, artifacts, limitations, review, content SHA。 |
| Evidence | manifest JSON / `JsonEvidenceStore` | UUID | SourceEdition/Asset/Page/Region/EvidenceSpan、revision、review status。 |
| Search/Discovery registry | `data/rule_search`, `data/auto_discovery` | search/generation/candidate semantic hash | frozen boundaries, FDR stats, candidate registry, trial ledger。 |
| Candidate comparison | `data/candidate_comparisons/*` | comparison_id/hash | protocol, OOS panel shards, HAC/FDR, portfolio ledgers, adjudication. |

## 关系图

```text
Universe + Market Parquet → DatasetSnapshot
RuleDefinition → CompiledRule/semantic hash
DatasetSnapshot + Rule + Protocol → ResearchCase → ResearchRun
ResearchRun → Observation (1:N) → Outcome (1:N horizon)
ResearchRun → Statistics / WalkForward / HypothesisDraft / QA
QA passed → HypothesisApproval → RuleApproval → KnowledgeCard draft → ContentReview → published card
published card + EvidenceSpan/EPUB/PDF → BM25 → Wiki Answer
```

## 非实现项

- 没有 SQL DDL、migration、foreign key、事务隔离、行锁或多机并发数据库。
- 没有持久化 vector embedding 表、新闻表、公告表、财务因子表、订单/成交/账户表。
- JSONL rewrite 操作的并发安全由文件协议/锁/单 worker 约束提供，不能等同数据库事务。
