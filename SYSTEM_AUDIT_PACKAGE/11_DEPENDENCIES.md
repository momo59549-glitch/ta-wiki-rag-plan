# 11 · 依赖与外部服务

来源：`pyproject.toml`、`compose.yaml`、`.env.example`、源码 import。

## 运行时

| 分类 | 当前依赖/服务 | 用途 |
|---|---|---|
| Python | `>=3.12` | 项目最低版本。 |
| Web/API | FastAPI `>=0.115,<1`、Pydantic v2、Uvicorn | API、schema、服务。 |
| 数据 | pandas、pyarrow | DataFrame、Parquet。 |
| 测试 | pytest | 单元/集成/回归。 |
| 研究 optional extra | vectorbt、skfolio、statsmodels、backtrader、tushare | 组合复核、walk-forward、FDR、独立复核、下载。 |
| 编排 optional extra | prefect、langgraph | 批处理/状态图。 |
| UI optional extra | streamlit、httpx | 内部研究台。 |
| 知识 optional extra | llama-index-core、llama-index-retrievers-bm25、anthropic、pypdf | BM25、LLM、PDF。 |

## 外部服务/数据

| 名称 | 配置/代码 | 用途与状态 |
|---|---|---|
| 本地 `Model/data` | `MODEL_DATA_PATH`、`configs/gen3_trend_cache_quality.json` | 权威外部只读 Parquet 行情根。 |
| Tushare | `TUSHARE_TOKEN`、`tushare_daily.py`、`st_status.py` | 历史补缺、daily/adj factor、股票池、namechange ST；实际权限由 token 决定。 |
| DeepSeek Anthropic-compatible | `ANTHROPIC_BASE_URL` 默认 `https://api.deepseek.com/anthropic`；`TA_WIKI_LLM_MODEL=deepseek-v4-flash` | 受证据约束 Wiki 问答；可关闭/无 key 时降级。 |
| Docker Compose | `compose.yaml` | api:8000、UI:8501、worker、Prefect:4200；仅 loopback 端口绑定。 |
| Prefect local server | Compose 或 `scripts/serve_prefect.py` | 调度/flow；不是数据/交易服务。 |

## 数据库与向量数据库

- 当前主存储：**无 SQL**，JSON/JSONL/Parquet 文件。
- 当前向量数据库：**无**；LlamaIndex BM25 每次对 published cards 构建内存 retriever。
- 试验性 SQLite：Qlib 脚本通过 MLflow ExpManager 创建局部 `mlflow.db`；不是系统主数据库，也不用于 API/Case 真相。
- 目标/未部署：PostgreSQL、pgvector、Qdrant、MinIO、Redis、Celery、Kafka、DVC、Kubernetes（见 `MASTER_IMPLEMENTATION_PLAN.md`）。

## 密钥与安全

- `TA_API_KEY`：FastAPI 认证。
- `TUSHARE_TOKEN`：只应由环境变量提供；同步 checkpoint 设计为不保存 token。
- `ANTHROPIC_API_KEY`/`ANTHROPIC_AUTH_TOKEN`：Wiki LLM；`start_local_stack.ps1 -PromptForWikiApiKey` 可交互输入。
- `.env.example` 只是变量名/占位符；本审计未读取实际密钥或运行环境变量。
