# 多 Agent 股票研究团队

本仓库已从“TA Wiki + RAG + 股票分析”升级为可验证的多 Agent 研究团队方案。核心资产是研究数据库与闭环经验，不是书籍摘要或聊天记录。

核心闭环：

`Observation → Outcome → Hypothesis → Backtest → Rule Version → Knowledge Update`

## 从这里开始

1. `docs/IMPLEMENTATION_STATUS.md`：当前已运行能力与限制；
2. `MASTER_IMPLEMENTATION_PLAN.md`：长期目标架构与历史实施基线；
3. `02_ARCHITECTURE_REPOSITORY.md`：运行架构与仓库边界；
4. `11_MULTI_AI_EXECUTION.md`：九类 Agent、状态机与审批；
5. `12_ROADMAP_TASKS_RISKS.md`：阶段、Backlog、风险；
6. `13_TEMPLATES.md`：Prompt、消息和研究模板。

历史总览与迁移说明已原样归档至 `docs/archive/plans/MASTER_PLAN.md` 和 `docs/archive/plans/MIGRATION_FROM_CURRENT_PLAN.md`。当前无 SQL 文件运行时以本 README 与 `docs/IMPLEMENTATION_STATUS.md` 为准，不把长期目标架构误写成已部署事实。

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

## 自动规则搜索（受控）

框架新增有界、可审计的自动规则搜索筛选层：预登记 178 个候选（蜡烛形态、均线金叉死叉、MACD、RSI、布林带、动量、突破、放量），使用与正式流水线一致的向量化求值和跨候选 FDR 校正，产出试错台账。**筛选通过不等于规则有效**，任何候选晋升都必须走冻结 Campaign、最终锁箱与人工审批。详见 `docs/RULE_SEARCH.md`。

第一版自动发现可在不先给定策略的前提下，从受限 DSL grammar 生成固定预算的技术规则候选，并将通过筛选的证据登记为状态专属、会到期/可因漂移退役的研究候选。它只生成 `eligible_for_frozen_campaign`，绝不自动发布、下单或保证盈利。运行方式和代次治理见 `docs/AUTO_DISCOVERY.md`：

```powershell
python scripts\run_auto_discovery.py --generation-id g_20260809_01 `
  --start 2010-01-01 --end 2023-12-31 --oos-start 2022-01-01 --lockbox-start 2026-09-01 `
  --candidate-budget 64 --output-root data\auto_discovery\g_20260809_01
```

2026-08-09 已修复筛选层持有期退出价格与正式流水线的错位，以及 n 元算术 DSL 的向量化差异；此前搜索轮次应在修复后重跑，不得直接沿用作晋升依据。

Generation `g_20260809_01` 派生的 RSI、ROC、breakdown 三候选已完成正式预注册比较，三者最终状态均为 `research_eliminated_event`。ROC 仅在三者内部排序相对第一，不代表通过组合门禁、可交易、获批或可发布。正式结果哈希为 `sha256:e38d07cabb182c5f8de97a1149d0b1ae172638dd7b44daee43dbdea6cf39cebb`；最终锁箱未读，批准和发布仍被禁止。下一步若开展 Gen2，必须按顺序试验治理使用新的未来样本；2022–2026 已被研究流程查看，不再是新鲜 OOS。

本轮没有删除任何数据。权威研究 shards、正式 v4 comparison panel、protocol/result/staging 继续保留；合并 JSONL 只是可由 shards 重建的兼容视图。任何删除或压缩清理都需要用户另行确认，详见 `docs/OPERATIONS_RUNBOOK.md`。

Gen2 当前仅完成预注册骨架：跨代去重、全局 trial budget 和由冻结 benchmark 驱动的 context-wrapper 候选已经可 dry-run；父代研究结束日前已查看的数据不能重标为 fresh OOS，2026-09 后尚未到来的数据只能预注册、到来前不能运行；未读取锁箱、未产生新收益结论。见 `docs/GEN2_DISCOVERY.md`。

## 不可绕过的门禁

- LLM 不直接执行任意代码或发布规则；
- 回测前冻结实验协议；
- 规则发布必须经过 QA 和人工批准；
- 所有结论必须能追溯到数据快照、规则、代码、实验和证据；
- 失败、拒绝和证据不足是正式结果。
