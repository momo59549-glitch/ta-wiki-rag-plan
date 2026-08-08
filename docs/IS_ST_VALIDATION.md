# is_st 字段独立验证方案

更新时间：2026-08-08。本文档说明"Tushare 补齐缓存的 is_st 字段尚未独立验证"
这一 QA 限制的来源、修复方案与执行步骤。

## 问题来源

- Tushare 日线/复权因子接口不返回 ST 状态；此前 `tushare_daily.py` 对
  `tushare_daily_cache` 与 `tushare_incremental_cache` 写入的
  `is_st` 是**硬编码 False**；
- `trend_cache`（Model 项目缓存）有 `is_st` 列，但来源未在本框架内独立核验；
- 结论：ST 状态（决定 5%/10% 涨跌停限制）在补齐/增量缓存上不可信，相关案例
  QA 只能停在 `passed_with_limitations`，不能进入审批。

## 独立来源与修复

采用 Tushare `namechange`（证券更名历史）作为独立证据：名称包含 `ST` / `*ST`
的时间区间即为 ST 状态区间。产出与点时股票池同构的 JSONL 时间线
（`symbol` / `active_from` / `active_to` / `source`），可复用
`load_universe_memberships` + `active_on`。

已落地的改动：

- `packages/market_data/st_status.py`：
  - `build_st_timeline()`：按股票池逐只拉取 namechange，生成 ST 时间线清单；
  - `audit_is_st()`：逐 bar 对比缓存 `is_st` 与时间线，输出覆盖率与不一致率；
- `packages/market_data/tushare_daily.py`：补齐/增量同步接受 `st_manifest`，
  有清单时真实写入 is_st，并在结果中记录 `is_st_source`；
- `scripts/sync_tushare_daily.py` / `scripts/sync_tushare_incremental.py`：
  新增 `--st-manifest`；
- `scripts/audit_is_st.py`：审计 CLI。

## 执行步骤（需要 TUSHARE_TOKEN）

```powershell
# 1. 构建 ST 时间线（一次性，5,874 只约需 20–40 分钟）
$env:TUSHARE_TOKEN = "..."   # 或写入 .env，不落盘
python scripts/build_st_timeline.py --model-data H:\股票模型\Model\data --output data\manifests\st_timeline.jsonl

# 2. 重新补齐/增量同步，写入真实 is_st
python scripts/sync_tushare_daily.py --st-manifest data\manifests\st_timeline.jsonl
python scripts/sync_tushare_incremental.py --st-manifest data\manifests\st_timeline.jsonl

# 3. 审计（trend_cache 与补齐/增量缓存均可对照）
python scripts/audit_is_st.py --st-manifest data\manifests\st_timeline.jsonl --symbol-limit 2000
```

审计结果 `data/audit/is_st_audit.json` 状态：

- `validated`：时间线覆盖且逐 bar 零不一致；
- `mismatch`：存在不一致，需修数据或时间线；
- `unvalidated`：未提供时间线，仅报告缓存覆盖，不能视为已验证。

## 门禁影响

- 审计通过（`validated`）之前，行情相关 QA 维持 `passed_with_limitations`；
- 修改任一缓存后必须重建强内容快照（快照哈希会变），再派生/重跑 Campaign；
- 本次改动已提交；时间线构建需要真实 Token，当前环境未执行，属于待办。
