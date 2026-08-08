# 书籍知识卡首批审核队列

更新时间：2026-08-05

## 当前状态

- 来源：《日本蜡烛图技术》EPUB 导入清单（77 章，`chapter_href` 定位）。
- 已生成并逐章审核 8 张 KnowledgeCard，均带有可解析的 EPUB 章节引用。
- `published` 数量为 8，`draft` 和 `rejected` 均为 0。
- 审校人：`codex-content-reviewer-2026-08-05`；每张卡均保存独立审校意见。
- 策略验证、回测与参数搜索不属于本批次范围。

## 已发布卡片

| 卡片 | Card ID | 章节证据 |
|---|---|---|
| 锤子线与上吊线 | `kc_605b7134cac5490791a7ab40e7529630` | 伞形线 |
| 吞没形态（抱线形态） | `kc_d86b3d115c7f40cdaaf30fa1c18a0623` | 吞没形态（抱线形态） |
| 乌云盖顶形态 | `kc_7e77654e08d94916a520de306ad5e667` | 乌云盖顶形态 |
| 刺透形态 | `kc_1d634949b67246a6806dc6cd6dbeef2f` | 刺透形态 |
| 启明星形态 | `kc_b10dab12f5174af6bb8b8d7b1d85fc03` | 启明星形态 |
| 黄昏星形态 | `kc_b6cb35954c5d4646b5036c75a0f58d6c` | 黄昏星形态 |
| 流星线与倒锤子线 | `kc_a4ab145777184b1d831f2d4657316934` | 流星形态与倒锤子形态 |
| 孕线与十字孕线 | `kc_349f3477259a4939b0f114563bdec4d9` | 孕线形态 |

## 人工审核标准

逐卡确认以下项目后才能选择 `publish`：

1. claim 与所引章节一致，没有把“潜在警告”写成确定预测。
2. 趋势前提、形态结构、确认条件和失效条件没有被省略或倒置。
3. limitations 清楚说明形态不能单独构成交易指令。
4. 引用中的书名和章节名称可以回到 manifest 定位。
5. 审校意见写明核验范围；不要使用空泛的“同意发布”。

## 审核入口

启动本地栈后，进入 Streamlit 的“Knowledge”页签：

1. 在“审校知识卡”下拉框选择 Card ID。
2. 输入独立的内容审校人标识。
3. 选择 `publish`、`request_changes` 或 `reject`。
4. 填写审校说明后提交。

只有 `published` 卡片会进入本地 BM25 检索。发布后用“乌云盖顶”“锤子线”“孕线”等查询做引用验收。

## 可重复生成

引导器按标题幂等，重复执行不会创建第二份同名卡：

```powershell
$env:PYTHONPATH = (Get-Location).Path
$manifest = Get-ChildItem .\data\manifests -Filter *.epub.json | Select-Object -First 1
python scripts\bootstrap_book_knowledge.py --manifest $manifest.FullName --knowledge-root .\data\knowledge
```
