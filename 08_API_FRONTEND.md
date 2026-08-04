# API 契约、异步任务与前端体验

## 1. API 原则

- REST/JSON 为 MVP；OpenAPI 是契约真相。
- API 版本前缀 `/api/v1`。
- 所有写请求支持 `Idempotency-Key`（适用时）。
- 分页使用 cursor；时间和数值语义明确。
- 错误返回稳定代码，不把内部堆栈、SQL 或受限文本泄露。
- 响应包含 `request_id`，异步任务包含 `job_id`。

统一错误：

```json
{
  "error": {
    "code": "EVIDENCE_ACCESS_DENIED",
    "message": "无权查看该证据。",
    "request_id": "req_...",
    "details": {}
  }
}
```

## 2. 核心 API

### 2.1 来源与证据

| 方法 | 路径 | 用途 |
|---|---|---|
| POST | `/sources/uploads` | 创建安全上传会话 |
| POST | `/sources/{id}/ingestion-jobs` | 开始导入 |
| GET | `/sources/{id}` | 来源/授权/处理状态 |
| GET | `/pages/{id}` | 页码和版面元数据 |
| GET | `/pages/{id}/image` | 短期签名图像 |
| GET | `/evidence/{id}` | 证据片段与定位 |
| POST | `/evidence/{id}/revisions` | OCR/区域纠正 |

### 2.2 Wiki 与 RAG

| 方法 | 路径 | 用途 |
|---|---|---|
| GET | `/wiki` | 搜索/过滤条目 |
| GET | `/wiki/{slug}` | 当前或指定 revision |
| POST | `/wiki/{id}/revisions` | 创建草稿 |
| POST | `/wiki/{id}/reviews` | 批准/退回 |
| POST | `/search` | 返回检索结果与证据 |
| POST | `/answers` | 创建带引用回答 |
| GET | `/answers/{id}` | 读取可复现回答 |

### 2.3 规则、扫描、视觉与回测

| 方法 | 路径 | 用途 |
|---|---|---|
| GET/POST | `/rules` | 列表/新建草稿 |
| POST | `/rules/{id}/compile` | 校验并产生 IR |
| POST | `/rules/{id}/evaluate` | 对小型序列解释 |
| POST | `/scans` | 创建异步扫描 |
| GET | `/scans/{id}/signals` | 分页候选 |
| POST | `/chart-uploads` | 上传截图 |
| POST | `/chart-uploads/{id}/parse` | 创建视觉任务 |
| POST | `/backtests` | 以 manifest 创建回测 |
| GET | `/backtests/{id}` | 状态和摘要 |
| GET | `/backtests/{id}/artifacts` | 报告/明细 |

### 2.4 任务

`GET /jobs/{id}`、`POST /jobs/{id}/cancel`。SSE `/jobs/{id}/events` 推送阶段和进度；断线后可按 event ID 恢复。

## 3. 契约示例：创建回测

```json
{
  "rule_revision_id": "uuid",
  "parameters": {"min_lower_shadow_body": 2.0},
  "dataset_snapshot_id": "uuid",
  "universe_snapshot_id": "uuid",
  "period": {"start": "2015-01-01", "end": "2025-12-31"},
  "execution": {"decision": "bar_close", "fill": "next_bar_open"},
  "costs": {"commission_bps": 3, "slippage_bps": 5}
}
```

服务端补齐 engine/code/calendar 版本，规范化后展示 manifest 预览；用户确认才排队。

## 4. 前端信息架构

### 4.1 页面

- Dashboard：导入、审校、扫描、回测任务状态。
- Library：书籍、版次、授权和处理状态。
- Evidence Review：页面图 + OCR/版面 + 页码映射。
- Wiki：条目、关系、来源差异、规则映射。
- Ask：问题、回答、引用卡和证据抽屉。
- Rules：自然语言语义、DSL、参数、测试向量、版本。
- Scanner：市场/周期/规则选择、候选表和图表解释。
- Chart Analyzer：上传、校准、叠加层、候选与不确定性。
- Backtests：manifest 构建、任务、报告和对比。
- Admin：用户、授权、数据源、模型、审计和删除任务。

## 5. 关键交互

### 5.1 证据查看器

- 页图与高亮框；
- 文件页/书内页并列；
- 证据短文本、OCR 置信度、审校状态；
- 前后文按权限展开；
- 报错/纠正入口；
- 引用复制时自动带版次。

### 5.2 规则解释卡

每个条件显示：

- 人类描述；
- 实际值、阈值、单位；
- 通过/失败/模糊/数据不足；
- 来源证据与工程近似说明；
- 当前规则与参数版本。

### 5.3 截图校准

用户可拖动主图边界、选择涨跌颜色、确认周期、点击目标蜡烛。重新解析保留前一 revision 和差异。

### 5.4 回测报告

首屏先显示 manifest、数据质量和偏差警告，再显示绩效图；不得用“推荐买入”“高胜率机会”等误导性文案。

## 6. 状态、空态和失败态

- 长任务显示当前阶段、完成量、估计可选、取消。
- 无结果区分：“规则无匹配”“数据不足”“权限过滤”“任务失败”。
- RAG 无证据显示检索到什么、建议如何限定问题。
- 截图无法解析说明具体原因和可操作修复。
- 回测因因果检查失败时不展示为有效结果。

## 7. 可访问性与国际化

- WCAG 2.1 AA 目标；键盘可操作、焦点、ARIA、对比度。
- 不能只用红绿表达涨跌/通过状态，同时用形状和文字。
- 中文优先，术语支持中英别名；日期、数值、时区本地化。
- 图表提供摘要表和可下载数据（受权限限制）。

## 8. 前端安全

- 不在浏览器保存长效对象存储凭据。
- 证据图片 URL 短期有效且与权限绑定。
- 富文本白名单渲染，禁止不可信 HTML。
- CSRF、CSP、SameSite cookie；不把 token 放 localStorage（若可避免）。
- 导出和截图加可配置水印、来源与用户审计标识。

## 9. 契约测试与 DoD

- OpenAPI 自动生成/验证客户端；CI 检查 breaking change。
- 每个错误代码有 UI 行为。
- 关键流程 E2E：导入→审校→Wiki→问答；规则→扫描→解释→回测；截图→校准→验证。
- 慢任务不阻塞 HTTP，不因刷新丢失状态。
- 权限和来源撤销即时影响页面、API、缓存和下载。

