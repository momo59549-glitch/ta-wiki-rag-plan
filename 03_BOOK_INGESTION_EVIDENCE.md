# 书籍导入、OCR、页码映射与原文证据库

> v2 定位：保留本证据管线，但不再把整本书蒸馏作为闭环 MVP 前置条件；优先提取高价值规则、限制和反例。

## 1. 目标

对每个知识结论建立可复核证据链：

`原文件哈希 → 版本/版次 → PDF 物理页 → 印刷页码 → 页面区域 → OCR 文本 revision → 证据片段 → Wiki/规则引用`

原文件、页面图和 OCR 派生物都保留谱系；任何纠错产生新 revision，不原地覆盖。

## 2. 支持格式与导入门禁

MVP 支持 PDF、单页 PNG/JPEG/TIFF；EPUB/DOCX 后续通过转换适配。上传阶段：

1. 校验 MIME 与魔数，拒绝伪装扩展名。
2. 限制大小、页数、像素和压缩比，防止解压炸弹。
3. 恶意文件扫描；解析器在无网络、低权限容器内运行。
4. 计算 SHA-256，查重并记录上传者、授权依据、保留策略。
5. 识别文本 PDF、扫描 PDF、混合 PDF和加密 PDF。
6. 加密或损坏文件进入人工处理，不绕过保护。

## 3. 来源与版次模型

`SourceAsset` 表示具体二进制；`SourceEdition` 表示出版版本。至少记录：

- 书名、作者、译者、出版社、ISBN、语言、出版日期、版次；
- 所有权/授权类型、允许用户、允许用途、过期日、导出限制；
- 文件哈希、页数、上传时间、解析器版本；
- 封面、扉页、版权页的证据页；
- 内容状态：`quarantined/processing/active/revoked/deleted`。

同名书不同译本、版次绝不共享页码或证据 ID。跨版本知识由 Wiki 关系连接。

## 4. 页面管线

### 4.1 页面渲染

- PDF 页索引以 0 开始保存为 `pdf_page_index`，UI 显示时可加 1。
- 渲染 200–300 DPI 页面图，保留原宽高、旋转、裁剪框。
- 生成缩略图与高分辨率受限图；每个派生物记录哈希。
- 页面坐标统一为归一化 `[0,1]` 左上原点，同时保留原像素坐标。

### 4.2 文本层优先

- 对原生 PDF 先提取字符、字体、坐标和阅读顺序。
- 检测乱码、缺字、异常字符密度；低质量页转 OCR。
- 混合页可组合文本层与 OCR，但必须保存每个 token 的来源。

### 4.3 图像预处理与 OCR

- 自动旋转、去倾斜、去噪、对比度、分栏检测。
- OCR 引擎可插拔；记录引擎名、模型、语言包和配置。
- 保存 token/line/block 的文本、bbox、置信度、阅读顺序。
- 表格、图注、页眉页脚、脚注和插图单独标记。
- 低置信度、罕见字符、跨栏顺序异常进入审校队列。

## 5. 三重页码映射

禁止单字段 `page`。页面实体至少包含：

| 字段 | 含义 | 示例 |
|---|---|---|
| `pdf_page_index` | 文件内部 0-based 索引 | 27 |
| `physical_page_number` | 文件查看器 1-based 页序 | 28 |
| `printed_page_label` | 页面上印刷的页码字符串 | `15`、`xii`、空 |
| `printed_page_numeric` | 可解析时的数值 | 15 |
| `page_section` | front_matter/body/appendix | body |
| `mapping_confidence` | 自动映射置信度 | 0.97 |

### 5.1 映射算法

1. 检测页面底部/顶部候选数字和罗马数字。
2. 用连续性动态规划选择最可信序列，允许空白页和插页。
3. 从目录和章节首页提取锚点，交叉验证偏移。
4. 发现跳号、重复、版面旋转或附录重置时分段建映射。
5. 人工确认关键锚点：正文第一页、每章首页、末页、附录首页。

UI 同时显示：“文件第 28 页 / 书内第 15 页”。引用导出包含版次和两种页码。

## 6. 阅读顺序与分块

分块不只按 token 数：

- 先按标题、段落、列表、图注、表格、脚注形成版面块；
- 再沿章节结构形成语义段；
- 对超长段按句界切分，重叠 1–2 句；
- 保存 `prev_span_id/next_span_id/parent_section_id`；
- 每个 chunk 保留原始文本、规范化文本和检索文本；
- 页眉页脚可索引为元数据，但默认不进入正文检索；
- 绝不跨来源版本拼成一个 evidence span。

推荐 chunk 目标 250–600 中文字符，实际以语义完整和引用精度为先。

## 7. EvidenceSpan Schema

```yaml
evidence_span:
  id: uuid
  source_edition_id: uuid
  source_page_id: uuid
  region_ids: [uuid]
  revision: 3
  raw_text: "..."
  normalized_text: "..."
  start_anchor: {block_id: uuid, char_offset: 12}
  end_anchor: {block_id: uuid, char_offset: 98}
  union_bbox: [x0, y0, x1, y1]
  content_type: paragraph|caption|table|footnote|heading
  ocr_confidence: 0.94
  review_status: draft|reviewed|verified|rejected
  supersedes_id: uuid|null
  generator_manifest_id: uuid
```

证据片段默认短小且能独立支撑一个 claim；相邻上下文通过 API 按权限展开。

## 8. 人工审校台

页面左侧显示图像，右侧显示 OCR/版面树；支持：

- 框选区域、调整阅读顺序、合并/拆分块；
- 修改文本但保留 diff、审校者和原因；
- 设置印刷页码和映射锚点；
- 标记图注、示例、定义、警告、作者观点；
- 批准/拒绝 AI 提取的 Wiki 条目和引用；
- 只显示低置信度或异常页的优先队列。

双人审校只用于高价值黄金集和关键规则证据，其余使用风险分层抽检。

## 9. 失败处理与幂等

- 导入任务以 `asset_hash + pipeline_version + config_hash` 作为幂等键。
- 页级失败不阻断其他页面；资产状态显示部分成功和失败页。
- OCR 重跑生成新的 pipeline run；旧证据仍可解析。
- 如果页面 revision 改变，引用进入 `needs_revalidation`，不得静默漂移。
- 来源撤销后，禁止新查询命中，排队删除索引和派生缓存；审计元数据按政策保留。

## 10. 验收样本与 DoD

准备至少：

- 20 页原生 PDF；
- 20 页扫描中文；
- 10 页双栏/图文混排；
- 前言罗马页码、正文重置、空白页、旋转页、附录页；
- 受损、加密、超限、重复文件。

DoD：

- 页面数和渲染数 100% 对齐或有明确错误记录。
- 黄金页页码映射准确率达到 `10_TEST_EVAL_ACCEPTANCE.md` 门槛。
- 任意引用能在 2 次交互内打开页面并高亮正确区域。
- OCR 修订、回滚、重跑和来源撤销有端到端测试。
- 不具授权的身份无法通过 API、缩略图或对象 URL 读取内容。
