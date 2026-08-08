# Agent Prompt、研究与消息模板

## 1. 通用 Prompt

当前九 Agent 的核心节点是确定性代码；以下 Prompt 只在未来启用 LLM 辅助的 Research/Knowledge/Report 时使用，Prompt 输出不能直接改变状态或发布内容。

```text
你是 {agent_type}，只能执行声明的职责和工具。
输出必须符合 {output_schema_version}。
区分数据事实、外部观点、统计结果、模型推断。
引用只能使用工具返回的稳定 ID。
不得修改冻结的 OutcomeProtocol 或 Experiment Manifest。
证据不足返回 insufficient_evidence。
不得给出个性化投资建议，不得发布 Rule Revision。
```

```yaml
status: completed|insufficient_evidence|needs_human|failed
summary: string
facts: [{claim: string, source_ids: [uuid]}]
inferences: [{claim: string, basis_ids: [uuid], confidence: 0.0}]
warnings: [string]
next_action: string|null
schema_version: 1
```

## 2. 专用 Prompt

Coordinator：只按 transition table 和 required artifacts 路由；预算不足时暂停。

Research：只做预声明分层，输出可证伪陈述、总体、指标、基线、方向、样本外、多重检验、停止条件和混杂；只能是 draft。

Knowledge：只消费 approved Rule、passed QA、verified Evidence；结论必须带 scope/sample/period/version/limitations。

Report：必须同时展示问题、版本、方法、结果、反例、限制和审批状态。

QA：逐项检查谱系、时间、成本、样本外、复现、引用、权限、审批；强制项失败即 reject。

## 3. Hypothesis

```yaml
hypothesis:
  title: string
  falsifiable_claim: string
  population: {market: CN_A, universe_id: uuid, timeframe: 1d, date_range: []}
  rule_revision_id: uuid
  primary_metric: benchmark_excess_return_5d
  baseline: string
  expected_direction: positive|negative|different
  stratifications: [market_regime, liquidity_bucket]
  train_validation_test: {}
  multiple_testing_correction: BH
  minimum_sample_size: 300
  cost_model_id: uuid
  stop_conditions: [string]
  source_ids: [uuid]
  status: draft
```

## 4. Experiment Manifest

```yaml
experiment:
  id: uuid
  hypothesis_revision_id: uuid
  dataset_snapshot_id: uuid
  universe_snapshot_id: uuid
  rule_revision_id: uuid
  outcome_protocol_id: uuid
  engine: vectorbt
  engine_version: string
  code_commit: sha
  environment_lock_hash: sha256
  parameter_grid: {}
  split_plan: {}
  benchmark_id: uuid
  cost_model_id: uuid
  seed: 42
  preregistered_at: timestamp
  manifest_sha256: hex
```

## 5. Observation / Outcome

```yaml
observation:
  instrument_id: uuid
  timeframe: 1d
  signal_at: timestamp
  tradable_at: timestamp
  dataset_snapshot_id: uuid
  rule_revision_id: uuid
  condition_values: [{name: string, value: number, threshold: number, passed: true}]
  outcome_protocol_id: uuid
```

```yaml
outcome:
  observation_id: uuid
  horizon_bars: 5
  entry_price_basis: next_tradable_open
  raw_return: number|null
  benchmark_excess_return: number|null
  mfe: number|null
  mae: number|null
  execution_flags: [string]
  status: complete|censored|data_error
```

## 6. Approval

```yaml
approval:
  gate: hypothesis_review|rule_review|knowledge_publish
  target_id: uuid
  target_revision: integer
  decision: approve|reject|request_changes
  checklist:
    data_lineage: pass|fail|na
    preregistration: pass|fail|na
    out_of_sample: pass|fail|na
    costs_and_biases: pass|fail|na
    reproducibility: pass|fail|na
    citations: pass|fail|na
  reason_codes: [string]
  comment: string
  approver_id: uuid
```

## 7. Event

```json
{
  "schema_version": "event-envelope/v1",
  "event_id": "evt_<stable-id>",
  "event_type": "job.succeeded",
  "occurred_at": "ISO-8601 UTC",
  "job_id": "job_<id>",
  "case_id": "case_<id>|null",
  "correlation_id": "job-or-case-id",
  "causation_id": "event-id|null",
  "idempotency_key": "string",
  "payload": {},
  "payload_sha256": "sha256:<hex>",
  "delivery_status": "pending",
  "delivery_attempts": 0
}
```

## 8. 报告目录

问题；审批状态；数据快照与样本；Rule/Protocol；预注册方法；样本内/外与 walk-forward；成本/参数敏感性；失败与反证；限制；事实/推断结论；Rule 决定；引用与复现 Manifest。
