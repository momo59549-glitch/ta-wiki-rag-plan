# Gen3 acquisition dry-run contract

Status: draft planning infrastructure only. It cannot download, read a token,
create a target directory, or write data. Any large acquisition requires a
separate explicit user authorization.

Priority is local market data first; Tushare may be evaluated for fundamentals,
index history and tradability; announcements should prefer CNInfo or exchange
sources (SSE/SZSE); and news requires an authorized historical provider. These
are priorities, not claims that any provider has verified revision/PIT coverage.
Every capability in a dry-run spec is a caller declaration only; before any
download it needs source-specific documentary and schema evidence.

The user may need to confirm licensing, historical revision availability,
publication/effective-time fields, content-hash provenance, rate/cost limits and
the permitted storage root. Credentials are referenced only by a validated
environment-variable *name*, never stored or read by the dry-run tool.
`endpoint_or_dataset_id` is a provider dataset identifier, not an official URL;
official terms use a strict HTTPS URL with no query, fragment or userinfo.

## Official-source capability research (documentation review only)

This matrix is a **planning research note**, not a claim that any endpoint has
been probed, licensed, or admitted to Gen3. “Documented” means the linked
official page names the data or fields. “Inference” means a field appears
useful but its exact point-in-time semantics still need a read-only probe.
“Unknown” means the official page reviewed did not establish it. Web pages for
CNInfo/SSE/SZSE are disclosure portals, not a promise of a stable public API.

| Domain | Candidate official source and documented fields | Revision/PIT assessment | Canonical-schema gap | Minimal future read-only probe |
| --- | --- | --- | --- | --- |
| Market | Tushare [daily](https://tushare.pro/document/2?doc_id=27): `ts_code`, `trade_date`, OHLC, `vol`, `amount`; it documents daily ingestion around 15:00–16:00, base-tier 500 calls/minute, 6,000 rows/call, about 23 years per stock, and no records during suspensions. These statements apply to `daily` only. [adj_factor](https://tushare.pro/document/2?doc_id=28); [daily_basic](https://tushare.pro/document/2?doc_id=32) includes valuation/liquidity fields. | `trade_date` and OHLC are documented. Historical correction/revision identity remains **unknown from this review**. | Explicit symbol/session mapping is straightforward; any valuation factor remains non-PIT until its availability is evidenced. | One explicitly selected symbol/date request; inspect schema only and compare to the approved mapping. |
| Fundamentals | Tushare [income statement](https://tushare.pro/document/2?doc_id=33) and [financial indicators](https://tushare.pro/document/2?doc_id=79) document announcement/end-date-style financial fields. | Announcement-related date fields are documented; whether they are immutable first-publication timestamps, include revisions, and satisfy `available_at`/content hash is **unknown**. | Need source record ID, availability/effective session, revision ID and content hash; existing `NOTICE_DATE` alone is insufficient. | Read one permitted record’s schema/metadata, then test whether revisions and all PIT fields can be constructed without guessing. |
| Announcements | Official disclosure portals: [CNInfo](https://www.cninfo.com.cn/new/index), [SSE listed-company announcements](https://www.sse.com.cn/disclosure/listedinfo/announcement/), [SZSE company notices](https://www.szse.cn/disclosure/notice/company/index.html). | Publication pages are official disclosure evidence. Stable machine API, first-publication timestamp, revision linkage and historical bulk coverage are **unknown** until each platform’s documented terms/interface are reviewed. | Need structured `source_record_id`, publication/availability/effective timestamps, revision ID and bytes/content hash. | Manually choose one public announcement and inspect only its official metadata/terms; do not scrape a portal as an assumed API. |
| News | Tushare [news interface](https://tushare.pro/document/2?doc_id=195) documents a multi-source news dataset. | Text/time fields are documented at interface level; original-source licensing, historical start, corrections/revisions and reliable `available_at`/content hash are **unknown**. | Need licensed historical text plus source record/revision/content provenance. | Query no data yet; first confirm entitlement and documented field/retention terms with the provider. |
| Historical index constituents | Tushare [index weights](https://tushare.pro/document/2?doc_id=96) is the relevant candidate for dated membership/weight history. | Date-related constituent/weight data are the intended use; historical start, constituent effective interval semantics and revision history are **unknown from this review**. | Need `index_symbol`, `constituent_symbol`, `effective_from`, `effective_to`, and revision evidence. | Inspect one index/date schema under authorized read-only access; verify whether dates describe publication, rebalance, or effective membership. |
| Tradability | Tushare [trade calendar](https://tushare.pro/document/2?doc_id=26), [suspension](https://tushare.pro/document/2?doc_id=214), and [daily price limits](https://tushare.pro/document/2?doc_id=183) are candidate inputs; stock basic metadata is documented by [stock_basic](https://tushare.pro/document/2?doc_id=25). | Calendar/suspension/limit concepts are documented. Historical ST status, next-session fillability and corrections are **unknown** without per-dataset field/semantics verification. | Need one same-session canonical row with `is_st`, suspension, limit-up/down and `can_buy`/`can_sell`; the latter two may need a deterministic later rule, not a vendor field. | Inspect schemas for one session/symbol in each permitted dataset; do not derive fillability until price-limit and execution assumptions are formally specified. |

### Access, history and cost limits

Tushare’s official documentation is the only source in this review that exposes
named programmatic datasets. Some Pro interfaces may require points/permission;
the exact entitlement, rate limit, historical start and cost are **unknown here
unless confirmed on the specific official interface/account page**. CNInfo, SSE
and SZSE portal pages establish disclosure provenance but this review found no
promise that page retrieval is a stable bulk API. No provider capability above
should be copied into `supports_*` flags until a permitted, source-specific
schema/terms probe records the evidence.

### Recommended next decision (no purchase decision)

1. Start with the already local market cache only after its explicit mapping and
   one-file schema admission pass; this avoids new cost while still remaining
   `partial`/not PIT-factor-ready.
2. Seek a minimal authorized Tushare entitlement assessment for calendar,
   suspension, limits, financial announcement/revision fields and dated index
   membership. Do not provide a token to this project; record only an approved
   environment-variable name in a dry-run spec.
3. Treat announcements and news as separate provenance projects. Prefer an
   official disclosure source for announcement bytes/metadata, and obtain a
   licensed historical news source before considering news features.

Large downloads, API calls, portal extraction, data purchase, or credential use
remain out of scope and need separate explicit approval.
