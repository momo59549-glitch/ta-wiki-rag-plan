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
- PostgreSQL + pgvector 为权威库，Redis 只作缓存/信号；
- MinIO + Parquet 保存数据快照和产物；
- AKShare/Tushare 数据适配；
- vectorbt 主回测，Backtrader 可选复核；
- LlamaIndex 只服务证据检索与知识更新；
- Streamlit 先交付内部研究台，Next.js 后续产品化；
- MLflow 记录实验；Qdrant、Celery、DVC 按指标再引入。

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

## 不可绕过的门禁

- LLM 不直接执行任意代码或发布规则；
- 回测前冻结实验协议；
- 规则发布必须经过 QA 和人工批准；
- 所有结论必须能追溯到数据快照、规则、代码、实验和证据；
- 失败、拒绝和证据不足是正式结果。
