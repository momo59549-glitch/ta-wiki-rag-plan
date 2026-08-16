# Gen3 市场语料质量活动

状态：**受限、只读的 Phase 1 草案基础设施。不是正式数据 manifest、不是
试验登记，也不授权生成特征、候选、回测或锁箱读取。**

## 目标与运行边界

本活动把单文件质量隔离扩展为可审计的有限 corpus 检查，但默认仍只读取：

- 合同根目录的**直接子文件**路径、文件大小和 Parquet footer（schema、行
  数与 row-group 信息）；
- 严格六位数字的 `.parquet` 文件名；任何同级异常 Parquet 名称都会 fail
  closed；不递归子目录；
- 已显式构造的 snapshot 才能交给内存 runner 逐文件调用质量审计。

`max_files` 上限为 5,000。`CampaignContract` 固定每文件行/问题上限，并
绑定 snapshot hash 与 `write_policy=no_source_mutation`。它不是不可变正式
协议，永远不修改源文件、缓存、manifest、账本或候选。

CLI 默认只创建 footer metadata plan；只有用户明确指定 `--audit-file` 时
才审计该单一、已经属于 snapshot 的文件。可恢复 runner 的全量工作也必须
经显式确认后分批执行。所有输出为 JSON，错误不输出 traceback；源数据永远
不写入。

## 聚合准入规则

一个 `AggregatedCampaignReport` 必须与 snapshot 文件一一对应，并重新验证
来源、证券代码、绝对路径、contract hash、mapping hash 与文件大小。缺失、
重复、外来来源、路径/大小不一致或 hash 篡改均拒绝。

只有以下条件对**每一个**文件均成立时，状态才是 `admitted`：

- 没有 sample 截断；
- 没有 issue 截断；
- 没有质量问题；
- 实际扫描行数等于 snapshot footer 的总行数。

任何一个条件不满足，状态为 `blocked_with_quarantine`。汇总对象保留扫描行、
合法行、遇到的问题数、保留问题代码计数和 canonical hash；它不会自动修复
或解除隔离。

## 已完成的单文件与全量审计

以下是明确的单文件报告，不是全市场结论：

| 根目录样本 | report hash | 扫描/合法行 | 问题 |
| --- | --- | ---: | --- |
| `local_cache/000001.parquet` | `sha256:625a972f8ea47ead4b6a5f8d735d9e04082fddb0400493b2fedd2f4d9e5e785b` | 8,407 / 8,406 | 第312行，`1992-08-03`，`ohlc_bounds` |
| `trend_cache/000001.parquet` | `sha256:54824ae010b3a9dda594820f3b5f224bbf00f6997bd96a1593613da9224bd9a7` | 8,407 / 8,406 | 第312行，`1992-08-03`，`ohlc_bounds` |

这些早期单文件结果不是全市场结论。`trend_cache_adjusted` 的完整活动已经
完成；`local_cache` 仍因严格文件名规则被阻断，见下文。

## Footer 探针与可恢复执行层

`source=trend_cache_adjusted` 的真实全量质量审计已完成：

| 指标 | 结果 |
| --- | ---: |
| 文件 | 3,464 / 3,464 |
| 扫描行 | 14,036,309 |
| 合法行 | 14,036,290 |
| clean 文件 | 3,451 |
| quarantined 文件 | 13 |
| 问题 | 19（均为 `ohlc_bounds`） |
| 截断 | 0 |
| 聚合状态 | `complete_blocked` |

- snapshot：`sha256:577b4817819adb6eb694625d02445bf04b772b159ff51b91530e82438d8fc966`
- campaign：`sha256:8d9b44acd4296d1c69790d1f531bd904946f0fc06bbc967c43cda4e4bb9bc0ee`
- aggregate：`sha256:cbcc627f9d312c574640a53d7002e4ffd15a9537be91e6e87befd4de9c7386d1`

运行的逐文件报告约 4,386,295 bytes，位于被 `.gitignore` 排除的
`data/gen3_quality_runs`，不提交 Git。此前的
`sha256:92a48c9330b5b69d05dcf3fce8e88cb4433fe1456a6a60d19f6400da59c455b7`
仅是修复前、非 canonical 的 footer probe，不能再作为正式 snapshot 引用。

13 个证券中的 19 个隔离行均在 1991–1994 年，且全部为 `ohlc_bounds`：

| 证券 | 日期 |
| --- | --- |
| `000001` | 1992-08-03 |
| `000003` | 1991-11-07、1992-01-31、1992-10-27 |
| `000004` | 1991-10-09、1992-02-25、1993-08-09 |
| `000006` | 1993-08-09 |
| `000011` | 1992-07-13 |
| `000013` | 1992-10-27、1993-08-09 |
| `000014` | 1992-10-12 |
| `000015` | 1992-10-16 |
| `000016` | 1993-08-09 |
| `000019` | 1992-11-30 |
| `000020` | 1993-05-31、1993-06-07 |
| `600690` | 1994-04-04 |
| `600807` | 1994-04-04 |

全量完成不等于数据准入：源数据没有被修复。只有以独立来源形成的
replacement evidence，或经主审批准的正式排除政策，才能让后续阶段考虑
这些隔离行。质量活动不占用 Gen3 的 42 个策略试验预算。

后续的、仍非 PIT 的研究期内容运行已据此使用冻结窗口
`2015-01-01..2026-08-05` 逻辑排除这 19 个研究期前 issues，并完成 3,464 / 3,464
文件的内容身份快照（`sha256:c561e0ee1526398c347ffba01cb10fbc61b05390b5fc26c0d22c0a73545da759`）。
这不改变本活动 `complete_blocked` 的质量结论，也不修源数据；详情和 policy /
decision 身份见 [GEN3_MARKET_ADMISSION.md](GEN3_MARKET_ADMISSION.md)。

`local_cache` 没有进入同一活动：同级的 `_stock_metadata.parquet` 和
`T00018.parquet` 不符合六位证券代码文件名，严格规则将该 root 阻断。不能
通过静默跳过它们来宣称 local corpus 已被审计。

`packages.research.gen3_quality_run` 只在显式允许的 workspace 输出根下建立
可恢复运行目录，并把 snapshot、campaign 与 run manifest 以 write-once
canonical JSON 保存。完成状态只从 write-once、可验证的
`reports/<symbol>.json` 重建，
没有可篡改进度计数器：`waiting`、`accumulating`、`complete_blocked` 或
`complete_admitted`。最后一种只有完整报告一一对应、每文件无问题/无截断、
且扫描和合法行数均等于 footer 行数时才可能出现。

CLI 必须显式使用 `prepare`、`status` 或 `execute` 子命令；`execute` 还必须
提供 `--confirm-read-source` 和不超过 100 的本次文件上限。没有“一键全量”
入口。运行锁覆盖选择、读取、审计与原子发布；残留锁或 `.tmp` 文件均会
fail closed。该层绝不写源 root；`trend_cache_adjusted` 的真实全量行级审计
已完成并如上记录。

每次实际单文件审计前后都会重新读取该文件的 Parquet footer 和文件大小，
并与 snapshot 中对应 entry 严格比较；不一致时不会发布报告。这个绑定能阻断
同名文件被替换、行数/schema/row-group 或大小变化，但**不能**检测“文件大小
和 footer 都保持相同、仅原始数据字节发生变化”的原位改写。因此在正式 PIT
source manifest 与更强内容证明到位前，本地 write-once 运行产物仍不能作为
研究准入依据。

## 与单文件隔离层的关系

本层依赖 [GEN3_DATA_QUALITY.md](GEN3_DATA_QUALITY.md) 的单行优先级、稳定
证据 hash 与独立替换证据约束。独立替换证据仍只是内存验证对象，不能重写
源文件，也不会自动将原始隔离问题标为通过。
