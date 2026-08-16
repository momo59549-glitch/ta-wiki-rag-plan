# Gen3 市场数据质量隔离

状态：**只读质量隔离基础设施。`trend_cache_adjusted` 的全量审计已完成且
为 `complete_blocked`；这不是数据修复工具、正式 PIT manifest 或特征/候选/
回测授权。**

## 真实的受限探针事实

2026-08-12 的有界样本检查只读取了两个显式文件，结论仅适用于这两个
文件，绝不能外推为全市场数据结论：

| 本地根目录 | 样本 | 行数 | 日期顺序 | 发现的 OHLC 异常 |
| --- | --- | ---: | --- | --- |
| `H:\股票模型\Model\data\local_cache` | `000001.parquet` | 8,407 | 0 个重复，严格递增 | `1992-08-03`：raw `open=46.6 > high=45.6` |
| `H:\股票模型\Model\data\trend_cache` | `000001.parquet` | 8,407 | 0 个重复，严格递增 | `1992-08-03`：复权 `open=115.2418 > high=112.7688` |

上述是早期有界探针；之后对 `trend_cache_adjusted` 完成了全量审计，结果见
[市场语料质量活动](GEN3_MARKET_QUALITY_CAMPAIGN.md)。没有修改任何源文件。
样本内或一次活动的结果都不能证明其他缓存、其他来源或未来增量满足同一
条件。

全量活动确认 3,464 个文件、14,036,309 行中有 19 行被隔离，涉及 13 个证券；
所有问题均为 1991–1994 年的 `ohlc_bounds`。活动状态为 `complete_blocked`，
不占用 42 个策略试验预算。其 snapshot、campaign 和 aggregate hash 以及完整
证券/日期清单均在 [市场语料质量活动](GEN3_MARKET_QUALITY_CAMPAIGN.md) 记录。

## 隔离政策

- **quarantine, not mutate**：发现 null、非有限数、非正价格、负成交量、
  OHLC 边界、重复日期、倒序日期或非法日期类型时，只生成内存报告；不
  自动修复价格、不重写 Parquet、不更新 manifest。
- 每个坏行最多记录一个问题，固定优先级为：日期类型/null、重复/倒序、
  数值 null/type/non-finite、非正价格、负成交量、OHLC 边界。这样同一
  输入有稳定、可审计的结果。
- 报告仅从合同映射的六个源列和文件名证券代码构造证据哈希；额外列不会
  改变该哈希。NaN 和正负无穷使用显式 canonical sentinel。
- 正常的 naive datetime 日期仅在**内存行**中转为 date；带时区 datetime
  或字符串日期被隔离为 `invalid_session_type`。

## 独立替换证据

隔离不是解除隔离。若需要重新取得某一行，必须从与原始问题不同的独立
来源获取，并保存：原 issue hash、独立 source ID、替换内容 hash、已验证
market canonical row hash 和带时区的 observation 时间。只有相同证券和
相同交易日的替换行可以形成 `VerifiedReplacement`；该对象仍只在内存中，
不会改写原文件，也不会让原问题自动恢复为可用数据。

## 实现边界

`packages.research.gen3_quality` 只接受 `LocalParquetFileContract` 约束下
的根目录直接子文件，按批最多读取合同的六列，并受 `max_rows` 和
`max_issues` 限制。它不递归目录、不联网、不下载、不运行候选/回测、不
读锁箱，也不写入 `data/`。

这一层建立在 [Gen3 Phase 1 PIT 合约](GEN3_PHASE1_PIT.md) 之上；即使已完成
全量质量扫描，footer+size 绑定也不是内容不可变的 PIT manifest。PIT 来源
正确性、内容证明、可交易性和正式 source manifest 仍需后续独立验收。
