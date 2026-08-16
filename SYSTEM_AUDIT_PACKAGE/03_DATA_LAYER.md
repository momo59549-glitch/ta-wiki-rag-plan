# 03 · 数据层审计

## 数据源与存储

| 数据/来源 | 当前实现与路径 | 主要字段/身份 | PIT/限制 |
|---|---|---|---|
| 外部本地 OHLCV | `H:\股票模型\Model\data\trend_cache`，由 `configs/gen3_trend_cache_quality.json` 绑定 | `date, open, high, low, close, volume`；可选 `amount, is_st`；`Candle` 还携带 `prev_close, available_at`。 | 核心源为复权 `trend_cache`；并非供应商版本化 PIT 原始源。 |
| Tushare 日线/复权因子 | `packages/market_data/tushare_daily.py` → 独立 `tushare_daily_cache` / `tushare_incremental_cache` | 日线 OHLCV/amount、`adj_factor`、原始价字段；每 symbol Parquet。 | 写到独立 cache，不覆盖 base；需 `TUSHARE_TOKEN`；历史修订/可得时间未完整保存。 |
| 合成行情 | `CompositeParquetMarketData` | 同一 symbol 的 base + increment overlay；最后重复日胜出。 | `adj_factor` 可重算调整价；当前是文件合成，不是历史版本数据库。 |
| 历史股票池 | `data/universes/a_share_history.jsonl`，`packages/market_data/universe.py` | `symbol, active_from, active_to, source`；同步 manifest 另含 `ts_code/list_status`。 | `load_point_in_time_universe()` 按日期筛选；资料本身的完整性/供应商 PIT 证明另有审计。 |
| ST 时间线 | `packages/market_data/st_status.py`，输出 JSONL | `symbol, active_from, active_to, name, source=tushare_namechange`。 | 以名称包含 `ST/*ST` 推断；不是交易所风险警示正式事件流，空/漏 namechange 会造成漏判。 |
| 停牌/涨跌停近似 | `packages/research/execution.py`、`comparison_panel.py` | `suspended, volume/amount, prev_close, is_st, open/close`。 | 日线推断；没有订单簿、封单、盘中停复牌事件或精确 tick 时间。 |
| 基准 | 本地 `etf_cache`/代码 `000001`（由 `PipelineConfig` 指定） | 开收盘、基准收益。 | 若不配置/文件缺失，超额收益为空；不是统一官方基准表。 |
| 财务/公告/新闻 | **未在当前可执行策略链中实现**。 | 无正式财务发布日期、公告发布时间、新闻事件数据库。 | README/目标态提及不等于实现。 |
| 书籍证据 | `data/books`、`data/manifests`，`packages/evidence/{epub,pdf}_importer.py` | asset hash、页、区域、文本、revision、审校状态。 | 适用于知识/RAG；不作为股票行情或策略因子。 |

## 复权

- `LocalParquetMarketData.candles_from_frame()` 直接读 `trend_cache` 的 `open/high/low/close`；源码注释声明该 cache 是 adjusted。
- `CompositeParquetMarketData.load()` 在同时存在 base 和 incremental overlay 且具备 `raw_open/raw_high/raw_low/raw_close/adj_factor` 时，按最后有效 factor 把原始价转换为可比调整价。
- 风险：`prev_close` 由当前 frame `close.shift(1)` 生成；若 base 与 overlay 的复权口径不一致，涨跌停比较可能失真。`docs/GEN3_*` 多次标为研究内容快照而非供应商历史复权 PIT。

## 历史成分股、退市、ST、停牌、涨跌停

- 历史股票池：`UniverseMembership.active_on()` 支持 `active_from <= as_of <= active_to`。Pipeline 在每个 observation 日期调用 `active_on()`（`packages/research/pipeline.py`），没有 manifest 时明确写为 `survivorship_unsafe`。
- 退市：`a_share_history.jsonl` 的 `active_to` 可表达退市/失效；`docs/STRATEGY_TEST_READINESS.md` 声称有 338 个退市样本，但此审计未重算该数字。
- ST：`build_st_timeline()` 请求 Tushare `namechange`，生成 ST 区间，`audit_is_st()` 可与 Parquet `is_st` 列核对。当前是名称代理，非监管状态的完整 PIT。
- 停牌：`assess_execution()` 只在 bar 有 `suspended=True`、无有效价、零/无效 volume/amount 时阻断；开盘下单会设 `require_session_liquidity=False`，避免看 T+1 收盘量。
- 涨跌停：`limit_pct_for()` 用代码前缀与 ST 标记推导 5%/10%/20%，`assess_execution()` 用 `prev_close` 与 open/close 比较。没有正式每日涨跌停价表，也无法验证新股、特殊标的、临时规则和排队成交。

## Point-in-Time 处理结论

| 项目 | 状态 |
|---|---|
| bar 可得时间 | 已实现：`Candle.available_at=bar close`，规则 `observed_at=bar_close`、`executable_from=next_bar_open`。 |
| 日期级历史股票池 | 已实现接口并在 pipeline 可执行。 |
| 强内容快照 | 已实现：`build_strong_snapshot()` SHA-256 绑定选择文件。 |
| 数据版本/供应商修订历史 | 未实现。内容快照只能证明运行时文件字节，不证明当日可得。 |
| 财务发布日期/公告时刻 | 未实现。 |
| 盘中公告、停复牌、涨跌停/PIT 订单可成交性 | 未实现；仅日线保守近似。 |
| 真实历史指数成分股/行业分类 PIT | 未实现。 |

因此“PIT manifest 已加载”不能被解释为“全因子、全市场、全交易约束 PIT 已完成”。
