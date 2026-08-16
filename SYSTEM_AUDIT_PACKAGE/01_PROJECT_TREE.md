# 01 · 项目目录树

审计对象：`H:\股票模型\ta-wiki-rag-plan`。以下树状图按当前工作区可见内容整理；大体量运行数据以目录概览表示，未导出行情。

```text
ta-wiki-rag-plan/
├─ apps/
│  ├─ api/main.py                    # FastAPI 控制面、RBAC、研究/知识 API
│  └─ research_ui/
│     ├─ app.py                       # Streamlit 内部研究台
│     └─ api_client.py                # UI→API 客户端
├─ packages/
│  ├─ contracts/models.py             # Candle、RuleDefinition、Observation、Outcome 等领域契约
│  ├─ market_data/                    # Parquet、Tushare、ST、PIT universe、快照、质量检查
│  ├─ rule_dsl/                       # JSON DSL 编译、语义/逻辑 hash
│  ├─ rule_engine/engine.py           # 单 bar 规则求值
│  ├─ rules/catalog.py                # 6 条显式注册规则
│  ├─ backtest/engine.py              # 极简单标的 T+1 日内回测
│  ├─ research/                       # Pipeline、搜索、验证、组合比较、候选/试验治理
│  ├─ agents/team.py                  # 九角色确定性研究 Case 协调器
│  ├─ orchestration/                  # 文件 Job/Outbox/Worker/Prefect/LangGraph 状态适配
│  ├─ knowledge/                      # 知识卡、BM25、受证据约束的 Wiki Answer
│  ├─ evidence/                       # PDF/EPUB 导入、页/区域/证据模型
│  ├─ governance/                     # 文件审批与审计日志
│  └─ integrations/                   # 模型目录与外部模型适配接口
├─ scripts/
│  ├─ run_research.py                 # 基础事件研究
│  ├─ run_team.py                     # 九角色 Case 闭环
│  ├─ run_rule_search.py              # 预注册规则搜索
│  ├─ run_auto_discovery.py           # Gen1 受限 grammar 自动发现
│  ├─ run_gen2_*.py                   # Gen2 预注册/未来验证骨架
│  ├─ run_gen3_*.py                   # Gen3 数据质量、主板范围、PIT/可交易性审计
│  ├─ run_vectorbt_*.py               # vectorbt 筛选/烟雾验证
│  ├─ run_qlib_*.py                   # Qlib 试验/近似 Top-K 脚本
│  ├─ sync_tushare_*.py               # Tushare 历史补缺、增量同步、股票池
│  └─ start_local_stack.ps1           # 本地 API/UI/Worker 启停
├─ data/                              # Git ignore 的文件型运行时/研究工件（约 50GB+）
│  ├─ universes/                      # `a_share_history.jsonl` 等历史股票池
│  ├─ tushare_sync/                   # Tushare 同步状态/清单
│  ├─ research_cases*/                # Case、分片 Observation/Outcome、QA、报告
│  ├─ strategy_test_campaigns/        # 强数据/代码快照与预注册 Campaign
│  ├─ strategy_test_executions/       # 执行 shards、adjudication（当前最大目录）
│  ├─ rule_search/                    # 搜索协议、候选、FDR 统计、试错台账
│  ├─ auto_discovery/                 # 受限 DSL 自动发现代次、registry、trial ledger
│  ├─ candidate_comparisons/          # 候选比较面板、逐日 portfolio ledger、结果
│  ├─ gen2_*/ gen3_*/                 # Gen2/Gen3 数据、PIT、质量与可交易性工件
│  ├─ knowledge/ books/ manifests/    # 书籍/证据导入及知识卡
│  └─ control/ audit/ runtime/        # 文件 Job、事件、审计、运行状态
├─ docs/
│  ├─ IMPLEMENTATION_STATUS.md        # 当前文件型运行时的权威状态说明
│  ├─ RESEARCH_CONCLUSIONS.md         # 已冻结的负面研究结论
│  ├─ VALIDATION_AND_ENGINE_REFACTOR.md
│  ├─ GEN2_*.md / GEN3_*.md           # 未来发现、PIT、质量与交易性工作
│  └─ archive/plans/                  # 已归档的旧计划
├─ configs/                           # 数据合同，例如 `gen3_trend_cache_quality.json`
├─ tests/                             # pytest 单元、集成、钢丝测试和回归测试
├─ compose.yaml / Dockerfile          # 可选本地 Compose 拓扑
├─ pyproject.toml                     # Python 包与 optional extras
├─ README.md                          # 现状、运行方式、边界
└─ MASTER_IMPLEMENTATION_PLAN.md      # 目标态；不是当前部署事实
```

## 目录角色判定

`packages/` 是实际实现；`scripts/` 是操作入口；`data/` 是当前权威运行状态，但为 JSON/JSONL/Parquet 文件而非数据库；`docs/` 同时包含当前事实与目标态文档，阅读时须优先 `README.md`、`docs/IMPLEMENTATION_STATUS.md`。`MASTER_IMPLEMENTATION_PLAN.md` 与 `02_ARCHITECTURE_REPOSITORY.md` 中的 PostgreSQL/pgvector 拓扑是迁移目标，不是本机现状。

审计时发现工作树已有未提交改动（例如 `packages/research/vectorbt_adapter.py`、`tests/test_vectorbt_adapter.py` 与一组 Gen3 文件）；本审计没有修改它们。
