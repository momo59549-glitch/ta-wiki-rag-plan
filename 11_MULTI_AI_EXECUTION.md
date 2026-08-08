# Multi-Agent 研究团队分工与运行协议

## 1. 原则

Agent 是受控节点：固定输入/输出 Schema、工具白名单、预算、超时和状态转移。确定性计算交给代码，LLM 只做证据综合、异常归因、假设草拟和报告表达。

LangGraph 提供单个 Research Case 的条件路由和人工暂停适配；显式文件状态机是当前权威状态。Prefect 管理定时与批处理，文件 Worker 在没有 Prefect Server 时仍可自动领取 Job。当前阶段 JSON/JSONL/Parquet 是唯一业务真相；PostgreSQL 仅在多人并发达到迁移门槛后启用。

## 2. 九类 Agent

| Agent | 责任 | 主要产物 | 禁止 |
|---|---|---|---|
| Coordinator | 建案、路由、预算、恢复 | case transition, agent run | 发布规则 |
| Data | 拉取、标准化、质检、快照 | dataset snapshot, quality issue | 猜测缺失值 |
| Scanner | 执行已发布 DSL | observation | 改参数 |
| Reviewer | 按冻结协议到期复核 | outcome | 结果后改窗口 |
| Research | 比较分层、提出可证伪假设 | hypothesis draft | 任意代码/发布结论 |
| Backtest | 预注册实验与稳健性检验 | backtest run/artifact | 调参到最好 |
| Knowledge | 生成经验卡和 Wiki 草稿 | knowledge draft | 把草稿标已验证 |
| Report | 日报/周报/研究备忘录 | versioned report | 隐藏失败 |
| QA | 数据、偏差、复现、引用和审批门禁 | QA decision | 自批自己提交 |

Research Lead 批准 Hypothesis；Rule Owner 批准 Rule Revision；内容 Reviewer 在受限证据对外发布时审批。LLM 永远没有 publish 权限。

## 3. 状态机

```text
created -> data_ready -> observations_ready -> outcomes_ready
-> hypothesis_drafted -> backtest_reviewed -> knowledge_drafted
-> report_ready -> qa_passed -> awaiting_hypothesis_approval
-> hypothesis_approved -> awaiting_rule_approval -> rule_approved
```

旁路状态：`qa_limited`, `qa_failed`, `needs_more_evidence`, `changes_requested`, `rejected`, `failed`。KnowledgeCard 发布是独立内容审批状态，不伪装成 Research Case 状态。每次转移写入 `case_events.jsonl`，重试创建新 run，不覆盖历史。

## 4. 队列与事件

当前文件队列使用 `data/control/jobs`、排他 claim、租约心跳、进度、取消、超时重排、Outbox 和 Dead-letter。Prefect Deployment 负责工作日调度；达到容量门槛后可映射为 `data-io`、`scan-cpu`、`backtest-cpu`、`llm-low`、`qa`、`maintenance` work pools。

```text
dataset.snapshot_ready -> scan.completed -> observation.created
-> outcome.due -> outcome.evaluated -> hypothesis.submitted
-> backtest.completed -> qa.completed -> rule.approved
-> rule.published -> knowledge.updated
```

文件 Outbox 与 Job 状态写入同一控制目录。事件包含 event/correlation/causation/idempotency/schema version/payload hash；消费者幂等，失败进 dead-letter。迁移 PostgreSQL 后再改为数据库事务 Outbox，事件契约保持不变。

允许的 Job 类型只有：`universe_coverage`、`sync_market_incremental`、`aggregate_market_research`、`render_case_report`。每种 payload 有固定字段和类型；所有路径在执行前解析并限制到项目根目录或显式配置的 `TA_MODEL_DATA_ROOT`。

## 5. 不变量

- Observation 必须绑定数据快照和 Rule Revision；
- Outcome 必须执行 Observation 创建时冻结的协议；
- 未批准 Hypothesis 不得进入正式回测；
- 无样本外结果、QA passed 和人工批准不得发布规则；
- 下游只引用不可变 revision；
- 自然语言不是状态真相；
- 证据不足和拒绝是合法终态。

## 6. 模型与可观测性

Coordinator 尽量用显式路由；Data/Scanner/Reviewer/Backtest 以确定性代码为主；Research 使用强模型但只读；Knowledge/Report 可用较小模型并经引用 QA。

case timeline 展示节点耗时、重试、成本、模型/Prompt 版本、输入输出 hash、审批和 artifact。核心指标是闭环完成率、复现率、引用覆盖、人工等待和每 case 成本。
