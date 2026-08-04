# Multi-Agent 研究团队分工与运行协议

## 1. 原则

Agent 是受控节点：固定输入/输出 Schema、工具白名单、预算、超时和状态转移。确定性计算交给代码，LLM 只做证据综合、异常归因、假设草拟和报告表达。

LangGraph 管理单个 Research Case 的状态、条件路由和人工暂停；Prefect 管理定时与批处理。PostgreSQL 是唯一业务真相。

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
draft -> data_pending -> data_ready -> scanning -> observation_ready
-> outcome_waiting -> outcome_ready -> hypothesis_draft
-> hypothesis_review -> backtest_queued -> backtesting -> backtest_ready
-> qa_review -> rule_review -> approved -> knowledge_pending -> published
```

旁路状态：`needs_data_fix`, `rejected`, `failed`, `cancelled`, `superseded`。每次转移记录 actor、reason、input hash 和时间；重试创建新 run，不覆盖历史。

## 4. 队列与事件

Prefect queues：`data-io`, `scan-cpu`, `backtest-cpu`, `llm-low`, `qa`, `maintenance`。

```text
dataset.snapshot_ready -> scan.completed -> observation.created
-> outcome.due -> outcome.evaluated -> hypothesis.submitted
-> backtest.completed -> qa.completed -> rule.approved
-> rule.published -> knowledge.updated
```

Postgres Outbox 与业务事务同提交。事件包含 event/correlation/causation/idempotency/schema version/payload hash；消费者幂等，失败进 dead-letter。

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

