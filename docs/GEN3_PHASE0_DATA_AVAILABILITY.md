# Gen3 Phase 0: Data Availability Audit

Status: **the constrained actual inventory is complete. Policy records remain
conservatively `unverified`, and this does not authorize a backtest.**

This document is deliberately separate from the Gen3 discovery plan.  It
records metadata evidence before any candidate can be generated, evaluated, or
registered.  It is not a data manifest and does not authorize a backtest.

## Scope and safety boundary

- Inspection is directory, manifest, checkpoint, and small schema-sample only.
- No OHLCV, factor, announcement, or news rows are scanned to calculate a
  signal or return.
- No future validation or lockbox files are read.
- No trial ledger is written and no candidate is registered.

## Required evidence matrix

| Domain | Minimum source evidence | Required observed timestamps | Status in this scaffold |
| --- | --- | --- | --- |
| Market prices | local path, format, first/last session | `trading_day` | unverified |
| Fundamentals | local path, format, first/last disclosure | `published_at`, `available_at`, `effective_session`, `revision_id`, `content_hash` | unverified |
| Announcements | local path, format, first/last publication | `published_at`, `available_at`, `effective_session`, `revision_id`, `content_hash` | unverified |
| News | local path, format, first/last publication | `published_at`, `available_at`, `effective_session`, `revision_id`, `content_hash` | unverified |
| Index constituents | local path, format, historical membership range | effective membership session | unverified |
| Tradability | local path, format, first/last session | `trading_day` | unverified |

`available` may be assigned only when the listed fields are observed in a
small schema sample, with an auditable local path, format, and coverage bounds.
`partial` still requires path, format, and bounds.  A filename, report period,
or vendor claim is insufficient point-in-time evidence.

## Phase 0 output contract

The companion module `packages.research.gen3_policy` provides only:

1. `DataSourceAuditRecord` and `DataAvailabilityReport` for the metadata
   inventory;
2. `Gen3PolicyDraft`, a reviewable non-binding policy with the proposed
   214-used/42-remaining ledger allocation; and
3. validation that event/news/fundamental sources contain the fields needed
   to prevent information leakage before they are called available.

It deliberately provides no I/O, candidate generator, ledger writer, hash,
backtest entry point, or immutable-contract creation API.  A later phase must
replace every `unverified` matrix entry with observed metadata and receive
explicit review before creating any formal Gen3 contract.

## Actual bounded Phase 0 evidence (2026-08-12)

Status: **bounded metadata-only inventory completed. This is neither a formal
contract nor authorization to generate, register, or backtest a candidate.**

| Domain | Evidence path / format | Evidence type | Fields / coverage observed | PIT conclusion | Status |
| --- | --- | --- | --- | --- |
| Market | `H:\股票模型\Model\data\local_cache` / Parquet | sample-observed: 4,302 files; `000001` sample | OHLCV, valuation fields, `date`; 8,407 sample rows, 1991-04-04–2026-08-05 | price dates observed; whole-root coverage and PIT valuation semantics unverified | `partial` |
| Market (secondary) | manifest `data/tushare_sync/a_share_daily.progress.json` → external `tushare_daily_cache` / Parquet | manifest-claimed: 2,410/2,410 complete; sample-observed `001220` | `trade_date`, raw/adjusted OHLC, volume, amount, `adj_factor`, `is_st`; sample 120 rows, 2026-02-03–2026-08-04 | format sample cannot establish full historical coverage | `partial` |
| Fundamentals | `H:\股票模型\Model\data\fin_cache` / Parquet | sample-observed: 104 files; `000001` sample | `NOTICE_DATE`, `ROEJQ`, `symbol`; 118 sample rows | missing `published_at`, `available_at`, `effective_session`, `revision_id`, `content_hash`; NOTICE_DATE semantics unverified | `partial` |
| Announcements | no identifiable dedicated local PIT dataset | missing in restricted listing | — | no publication/effective-time evidence | `missing` |
| News | no identifiable dedicated local PIT dataset | missing in restricted listing | — | no publication/effective-time evidence | `missing` |
| Index constituents | `data/universes/a_share_history.jsonl` | sample-observed | listing status fields `active_from`, `active_to`, `fetched_at`; companion coverage ≈0.5896 / `incomplete_price_history` | listing history is not index membership; no membership `effective_session` | `missing` |
| Tradability | `H:\股票模型\Model\data\trend_cache` / Parquet | sample-observed: 3,464 files; `000001` sample | raw/adjusted OHLCV, `adj_factor`, `is_st`, `date`; 8,407 sample rows, 1991-04-04–2026-08-05 | `is_st` only; suspension, price-limit and next-session fillability not evidenced | `partial` |

“sample-observed” and “manifest-claimed” are intentionally different: no
claim above is extrapolated from a sample to every file, and no full table scan
was performed. `available` remains prohibited until schema, coverage and PIT
semantics are independently verified.

The `partial` labels in this matrix are **observation-level** findings. They
are not `DataSourceAuditRecord` availability claims: the repeatable inventory
intentionally emits `unverified` policy records until independently verified
coverage bounds are supplied.

## Ledger and Gen2 r3 read-only evidence

- Global policy is 256 trials; legacy unique logic is 206. Entry
  `g_20260810_01` has budget 8 and status `preregistered_no_screen_run`, so
  **used is 214 and remaining is 42**; it says `final_lockbox_read=false`.
- The r3 directory contains `code_snapshot.json` and `stage2_contract.json`.
  Its contract says `preregistered_contract_only` and
  `final_lockbox_read=false`; bounded evidence found no source manifest, date
  receipt, run artifact, or outcomes.

No lockbox data were opened in Phase 0; these are ledger/contract metadata
claims, not performance results.

## Repeatable safe inventory

`packages.research.gen3_inventory` has no default or machine-specific roots.
Callers must pass explicit roots. Its defaults inspect no more than 50 file
entries and two schema samples per root, prohibit recursion, never materialize
Parquet rows and never write an artifact. Even a sampled root stays `partial`
because the utility cannot prove coverage or PIT semantics.
