# 文件型研究团队运行手册

## 本机启动

在项目根目录执行：

```powershell
python -m pip install -e ".[research,orchestration,ui,knowledge]"
powershell -ExecutionPolicy Bypass -File scripts\start_local_stack.ps1
```

四个 `.ps1` 运维脚本只使用 ASCII 源码，兼容 Windows PowerShell 5.1 对无 BOM UTF-8 的旧解码行为。启动脚本会阻止重复运行；停止脚本会核对 PID 对应的命令行归属，停止后把状态归档为 `runtime.last-stopped.json`，避免 PID 复用误停其他进程。

API 为 `http://127.0.0.1:8000`，研究台为 `http://127.0.0.1:8501`。启动脚本同时拉起文件 Job Worker；API 创建的排队任务会被自动领取。裸 `uvicorn`、`streamlit`、`prefect` 可能不在 PATH，统一使用 `python -m ...`。

### Wiki Answer Agent（DeepSeek Anthropic API）

推荐使用交互式启动，密钥输入不会回显，也不会写入仓库、运行状态、日志或 `.env` 文件：

```powershell
powershell -ExecutionPolicy Bypass -File scripts\start_local_stack.ps1 -PromptForWikiApiKey
```

启动脚本会为子进程设置 `ANTHROPIC_BASE_URL=https://api.deepseek.com/anthropic`、`TA_WIKI_LLM_MODEL=deepseek-v4-flash` 和 `TA_WIKI_LLM_ENABLED=true`。也可在启动前自行设置 `ANTHROPIC_API_KEY` 或 `ANTHROPIC_AUTH_TOKEN`。

不要把真实密钥写入 `.env.example`、PowerShell 历史、任务 payload 或工件。没有密钥时，`POST /api/v1/wiki/answer` 仍返回已审校知识卡的证据摘录；模型失败时同样安全降级。模型只接收已发布 KnowledgeCard 的标题、claim 和 limitations，不接收原始书籍全文、本地 manifest 路径、行情文件、研究案例或系统密钥。

也可以在 Streamlit 的“Wiki 问答”表单中填写“本次调用 DeepSeek Key”。该字段使用密码输入，按请求在 UI/API 内存中传递，不写入运行状态或审计日志；适合无法操作启动终端时使用。长期运行仍推荐进程环境变量或交互式启动。

停止：

```powershell
powershell -ExecutionPolicy Bypass -File scripts\stop_local_stack.ps1
```

非本机地址启动前必须显式设置 `TA_API_KEY`。不要把 Key、Tushare Token 或其他密钥写入 `.env.example`、日志、检查点或 Git。

## 策略测试 Campaign 准备（不执行策略）

以下命令只创建强数据/代码快照、预注册协议、数据质检和 readiness 报告：

```powershell
python scripts\prepare_strategy_test_campaign.py `
  --universe-manifest data\universes\a_share_history.jsonl `
  --rule hammer --start 2010-01-01 --oos-start 2022-01-01 `
  --end 2026-08-04 --lockbox-start 2026-09-01
```

只有 `readiness_report.json` 为 `ready` 才能开始该协议下的策略测试。修改规则、参数、期间、成本、股票池、代码或数据后必须生成新的 Campaign；不得复用旧 protocol ID。首次全量质检约 40 分钟。

若只有代码发生变化，而上一 Campaign 的强数据快照和全量质检仍适用，可从已就绪 Campaign 安全派生新 Campaign：

```powershell
python scripts\derive_strategy_test_campaign.py `
  --from-campaign data\strategy_test_campaigns\campaign_20260805T155410Z
```

该命令不覆盖旧 Campaign、不执行策略，会重新逐文件验证强数据快照并重跑 14 项门禁。输出的 `campaign_derivation.json` 必须为 `strategy_executed=false`。

Job Worker 会设置 `TA_PROJECT_ROOT`，并在本机检测默认的 `H:\股票模型\Model\data` 作为 `TA_MODEL_DATA_ROOT`。非默认目录必须在启动前显式设置：

```powershell
$env:TA_MODEL_DATA_ROOT="D:\market-data"
```

任务输出只能写入项目根目录，行情输入只能来自项目目录或 `TA_MODEL_DATA_ROOT`；API payload 不能用来读写其他磁盘位置。

## Prefect

正式使用时先启动服务：

```powershell
python -m prefect server start
$env:PREFECT_API_URL="http://127.0.0.1:4200/api"
python scripts\serve_prefect.py
```

`serve_prefect.py` 与 `run_prefect_job.py` 会在 API URL 指向 loopback 时自动把 `127.0.0.1,localhost,::1` 加入 `NO_PROXY`。这避免 Windows/Python `httpx` 将本机 Prefect 请求误送到系统代理而返回 502 或长时间挂起。

本机首次 ephemeral server 初始化可能较慢，因此单元测试只验证 Prefect task 业务体；部署验收使用持续运行的 Prefect Server。

Prefect 用于定时和可视化编排，不是单机执行的硬依赖。未启动 Prefect Server 时，`scripts\run_file_worker.py` 仍能执行白名单 Job。Worker 使用排他 claim 和租约；异常退出后，过期 Job 会自动回到队列。

设置 `TUSHARE_TOKEN` 后，每日流程会拉取最近七个自然日中的开放交易日，写入 `tushare_incremental_cache`。它不会覆盖 `trend_cache` 或历史补缺缓存；读取时用原始价与复权因子统一合并。未设置 Token 时明确记录为 `skipped`，覆盖率检查仍继续执行。

## Docker Compose

复制 `.env.example` 为本地 `.env`，设置随机 `TA_API_KEY` 和实际 `MODEL_DATA_PATH` 后运行：

```powershell
docker compose up --build
```

Compose 默认只绑定 `127.0.0.1`。需要手机从局域网访问时，应通过受认证的反向代理或 VPN；不要直接将 8000/8501 暴露到公网。

## 备份与恢复

### 研究数据保留与删除门禁

2026-08-09 本轮整理未删除任何数据。必须保留权威研究 shards，以及 `data/candidate_comparisons/g_20260809_01/` 中正式 `oos_panel_v4`、`comparison_protocol.json`、`comparison_code_snapshot.json`、`comparison_result.json` 和 `comparison_result.staging/`。合并 JSONL 只是从 shards 生成的兼容视图，可以重建，但这不等于已授权删除。任何清理、去重、压缩或归档删除操作都必须先列出精确目标、验证可重建性，并取得用户另行确认；不得把失败的旧目录与正式 v4 产物混为一谈。

```powershell
powershell -ExecutionPolicy Bypass -File scripts\backup_file_mvp.ps1 -Destination "D:\ta-research-backups\20260805"
```

备份包含控制面、知识卡、规则注册表、审计、股票池、书籍/证据 manifest、研究运行与案例、报告、同步检查点和批处理日志，并生成 SHA-256 manifest。`Model\data` 行情缓存属于可重建外部数据，不复制进项目备份。恢复时先停止 API/UI，校验 manifest，再恢复到隔离新目录进行验收；禁止直接覆盖唯一生产副本。完成恢复后至少验证：

```powershell
python scripts\verify_integrity.py --audit data\audit\api_requests.jsonl --backup "D:\ta-research-backups\20260805"
powershell -ExecutionPolicy Bypass -File scripts\restore_file_mvp.ps1 -Backup "D:\ta-research-backups\20260805" -Destination "D:\ta-restore-validation\20260805"
```

恢复脚本只接受不存在或空的目标目录，逐文件校验备份哈希，复制到临时文件后再次校验再原子改名；它不会覆盖现有项目目录。隔离验收通过后，切换生产目录仍需人工操作。

1. `python -m unittest discover -s tests -v`；
2. 审计哈希链有效；
3. Case 数量、Job 数量和规则注册表一致；
4. API `/healthz`、时间线和报告可读取；
5. 未审批规则数量为零。

## 日常检查

- API、UI、Prefect 健康状态；
- `data/control/dead_letter.jsonl` 是否新增；
- Job 是否长期停留在 `running/cancelling`；
- 股票池覆盖率和数据最新交易日；
- `data/audit/api_requests.jsonl` 哈希链；
- 磁盘余量、备份完成时间和恢复抽检。
