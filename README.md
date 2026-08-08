# 多 Agent 股票研究团队

本仓库已从“TA Wiki + RAG + 股票分析”升级为可验证的多 Agent 研究团队方案。核心资产是研究数据库与闭环经验，不是书籍摘要或聊天记录。

核心闭环：

`Observation → Outcome → Hypothesis → Backtest → Rule Version → Knowledge Update`

## 从这里开始

1. `MASTER_IMPLEMENTATION_PLAN.md`：当前唯一实施基线；
2. `MIGRATION_FROM_CURRENT_PLAN.md`：如何保留并迁移旧方案；
3. `02_ARCHITECTURE_REPOSITORY.md`：运行架构与仓库边界；
4. `11_MULTI_AI_EXECUTION.md`：九类 Agent、状态机与审批；
5. `12_ROADMAP_TASKS_RISKS.md`：阶段、Backlog、风险；
6. `13_TEMPLATES.md`：Prompt、消息和研究模板。

`MASTER_PLAN.md` 是 v1 历史基线，保留用于追溯，不再作为实施排期来源。

## 技术基线

- FastAPI + Pydantic；
- LangGraph 管理研究案例状态机；
- Prefect 管理定时与批处理；
- 当前阶段以 JSON/JSONL/Parquet 为文件型权威存储，不要求 SQL；达到多人并发与容量门槛后再迁移 PostgreSQL + pgvector；
- 当前产物使用本地文件和 Parquet；MinIO 是对象存储扩展位，不是单机前置条件；
- AKShare/Tushare 数据适配；
- vectorbt 主回测，Backtrader 可选复核；
- skfolio Walk-forward/Purge 验证，statsmodels FDR 多重检验；
- LlamaIndex 只服务证据检索与知识更新；
- Wiki Answer Agent 可使用 DeepSeek Anthropic 兼容 API；模型仅根据已审校卡片组织回答，无证据时拒答，失败时降级为证据摘录；
- Streamlit 先交付内部研究台，Next.js 后续产品化；
- 实验 manifest 已保留接口；MLflow、Qdrant、Celery、DVC 按容量或治理指标再引入。

## MVP 范围

单市场、日线、固定股票池、1–3 条规则，完成数据同步、扫描、结果复核、假设、回测、QA、人工批准、知识更新与报告。MVP 不做自动下单。

## 当前可运行的无 SQL 闭环

直接复用 `H:\股票模型\Model\data\trend_cache`：

```powershell
python scripts\run_research.py --limit 20 --start 2020-01-01 --end 2026-07-24
```

输出到 `data/research_runs/<run_id>/`，包含运行身份、逐条 Observation、1/3/5/10/20 日 Outcome 和 Markdown 汇总报告。行情适配层只读源 Parquet，不复制或修改 `Model`。

九 Agent 案例入口：

```powershell
python scripts\run_team.py --limit 20 --start 2020-01-01 --end 2026-07-24 --oos-start 2024-01-01
```

详见 `docs/FILE_AGENT_RUNTIME.md`。系统目前只生成草稿和 QA 门禁，绝不自动发布规则。

本机 API、研究台和自动 Job Worker：

```powershell
python -m pip install -e ".[research,orchestration,ui,knowledge]"
powershell -ExecutionPolicy Bypass -File scripts\start_local_stack.ps1
```

启用 Wiki 模型回答时使用隐藏输入，不把密钥写入文件：

```powershell
powershell -ExecutionPolicy Bypass -File scripts\start_local_stack.ps1 -PromptForWikiApiKey
```

详见 `docs/OPERATIONS_RUNBOOK.md`、`docs/IMPLEMENTATION_STATUS.md` 与 `docs/FRAMEWORK_COMPLETION_AUDIT.md`。

策略测试准备已通过首个全量 Campaign 的 14/14 门禁；证据和允许/禁止事项见 `docs/STRATEGY_TEST_READINESS.md`。这表示框架可以开始受控测试，不表示锤子线或任何策略已经有效。

## 回测与验证状态（重要）

当前里程碑已按要求冻结策略验证，不继续搜索参数、扩大形态或生成收益结论；已有防未来函数模块与测试保留为框架安全门禁。

- 信号在 T 日收盘确认，买入资格仅使用 T+1 开盘前可见字段；不会以 T+1 收盘、全天成交量或成交额筛掉开盘订单。
- 当前运行产出 `walk_forward_validation.json`，其 purge 间隔至少等于最长持有周期；候选统计使用 FDR 校正后的 p 值。
- `data/research_cases_full` 的既有全市场结果是探索性/验证性证据，**不是**未见过的最终锁箱；修改规则后必须以新的、未查看时间段做最终复核。
- 见 `docs/VALIDATION_AND_ENGINE_REFACTOR.md`。
- 若要取得可发布级别的股票池证据，使用 `--universe-manifest data/universes/a_share_history.jsonl --universe-as-of YYYY-MM-DD`；不提供清单时 QA 会标记为 `passed_with_limitations`，不能进入批准流程。

## 不可绕过的门禁

- LLM 不直接执行任意代码或发布规则；
- 回测前冻结实验协议；
- 规则发布必须经过 QA 和人工批准；
- 所有结论必须能追溯到数据快照、规则、代码、实验和证据；
- 失败、拒绝和证据不足是正式结果。
