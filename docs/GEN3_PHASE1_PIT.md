# Gen3 Phase 1 — PIT Contract Draft

Status: **draft infrastructure only. This is not a formal data manifest or
formal protocol, and it authorizes no data ingestion, feature calculation,
candidate registration, backtest, ledger write, or lockbox read.**

`packages.research.gen3_pit` defines the smallest immutable provenance record
needed before a later local adapter may admit data into research:

- source and source-record identity;
- publication, availability, effective-session and ingestion timestamps;
- revision and content hashes;
- versioned symbol mapping; and
- a canonical record hash verified against a strict field whitelist.

All datetimes are timezone-aware. Ordering is
`published_at <= available_at <= ingested_at`. `effective_session` is a date
and must be strictly after the later of publication and availability converted
to a Shanghai market date. Duplicate identities fail closed and bind source,
source record, revision and content hash; reusing one revision with a different
content hash is rejected as a conflict. Canonical record hashes normalize every
datetime to UTC, so equivalent instants with different offsets have the same
hash.

`compute_effective_session` accepts only a caller-supplied, strictly
increasing frozen calendar, Asia/Shanghai and timezone-aware timestamps. Every
source waits until the **next** session strictly after the later publication or
availability local date. Thus before-close and after-close announcements have
the same next-session outcome. The cutoff is validated and retained as a future
policy hook, but is intentionally inert until a later formal protocol explicitly
permits intraday use.

Provider adapters and the feature registry are intentionally out of scope for
this first Phase 1 increment. No local data were read while implementing it.

## Local provider schema admission draft

`packages.research.gen3_providers` adds a local-only schema gate. It does not
create PIT records or assert that any source is available. A source must provide
an explicit `SourceFieldMapping` whose keys exactly match one versioned
canonical domain schema; mappings never infer `date`, `trade_date`, or any
other source column by name.

| Domain | Exact required canonical fields |
| --- | --- |
| Market | `symbol`, `session`, `open`, `high`, `low`, `close`, `volume` |
| Fundamentals | `source_record_id`, `symbol`, `value_name`, `value`, `published_at`, `available_at`, `effective_session`, `revision_id`, `content_hash`, `ingested_at`, `symbol_mapping_version` |
| Announcements | `source_record_id`, `symbol`, `published_at`, `available_at`, `effective_session`, `revision_id`, `content_hash`, `ingested_at`, `symbol_mapping_version`, `event_type` |
| News | `source_record_id`, `symbol`, `published_at`, `available_at`, `effective_session`, `revision_id`, `content_hash`, `ingested_at`, `symbol_mapping_version`, `title`, `content` |
| Index constituents | `index_symbol`, `constituent_symbol`, `effective_from`, `effective_to` |
| Tradability | `symbol`, `session`, `is_st`, `is_suspended`, `is_limit_up`, `is_limit_down`, `can_buy`, `can_sell` |

Each canonical field maps to one unique non-empty source column. The source ID
comes from the mapping itself, so the three PIT domains have every field needed
to construct a `PITRecord` without guessing. `schema_version` must exactly be
`gen3-provider-draft/v1` and `file_format` exactly `parquet`. Extra or
missing keys, duplicate source columns and absent observed columns fail closed.
The only successful status is `schema_compatible_not_pit_verified`; financial,
announcement and news sources still require per-row PIT, hash and revision
validation through the PIT contract. All six domains also retain
`row_validation_required=true`; schema compatibility never establishes value
validity. Admissions include a canonical SHA-256 mapping hash binding source,
domain, schema version, root, format and sorted field mappings.

The optional Parquet inspector accepts one explicit existing `.parquet` file
only and reads just `ParquetFile.schema` plus file size. It does not accept a
directory, recurse, read rows or write files. The legacy `fin_cache` helper
intentionally fails: `NOTICE_DATE`, `ROEJQ`, and `symbol` cannot fabricate the
missing availability, effective-session, revision, and content-hash fields.

## Feature identity and dependency draft

`packages.research.gen3_features` is a pure-memory registry contract: it does
not execute a transform, open a file, calculate a factor, or create a candidate.
Every dependency binds a domain, provider mapping hash, sorted canonical fields,
the domain's fixed availability field, and a non-negative integer lag. The fixed
availability fields are `session` for market/tradability, `effective_session`
for fundamentals/announcements/news, and `effective_from` for index members.
The corresponding availability field must itself be required. Financial,
announcement and news dependencies additionally retain `source_record_id`,
`effective_session`, `content_hash`, and `revision_id`; a feature cannot discard
its time or revision provenance.

A feature specification has a strict draft schema, a lower-case identifier and
version, a canonical feature hash, lookback/minimum-observation bounds, output
and null policies, and fixed `signal_session_close` evaluation semantics. Its
families constrain allowable domains: technical/control only market,
tradability or index and must include market or tradability; single-factor must
include fundamentals and otherwise use only those technical domains;
announcement events/news must include their own event domain and otherwise use
only technical domains.

The in-memory registry verifies each hash, rejects duplicate or conflicting
feature identities and duplicate hashes, sorts independently of input order,
and produces its own canonical registry hash. Direct construction is also
verified: empty, unsorted, duplicated or tampered registries fail closed. It is
still draft infrastructure,
not a feature manifest or a signal catalogue.

## Local market sample adapter

The draft local-market adapter is sample-only. A contract binds an explicit
root, explicit date (`date` or `trade_date`) and OHLCV columns, and the fixed
six-digit filename-symbol rule. It accepts only a single direct child Parquet
file of that root, reads at most 10,000 rows through bounded batches, and
requires strictly increasing sessions. It does not scan a directory or upgrade
`local_cache`/Tushare cache to `AVAILABLE`; per-file and coverage validation
remain future work.

## Canonical row validation and draft source manifests

`packages.research.gen3_rows` is the final Phase 1 draft-validation layer. It
accepts only an explicit provider mapping and one in-memory raw row, keeps only
mapped columns, validates domain value rules, binds PIT rows to a verified
`PITRecord` hash, and produces a canonical row hash. Extra source columns never
enter the canonical row or its hash. It validates OHLC/volume, tradability
constraints, index membership intervals, and PIT payload types without claiming
that a supplied content hash was recomputed from provider bytes.

`DraftSourceManifest` is also in memory only. It binds one source, domain,
mapping hash and validation version to sorted unique row hashes and the relevant
session range (`effective_session`, `session`, or `effective_from`). It rejects
empty, mixed, duplicate, unsorted or tampered construction. This is **draft
validation infrastructure, not a formal source manifest**: no real source has
been promoted to `AVAILABLE`, and no Phase 1 component reads or writes real
data. `CanonicalRow.verify` itself revalidates the complete canonical domain
schema and values, source/mapping/row hashes, and (for PIT domains) reconstructs
the PIT record to check its bound hash; non-PIT rows cannot carry a PIT hash.
Manifest `verify` can only check its own structure and hash because it does not
retain row content. Per-row verification happens during manifest construction.

## Market data quality isolation

Before a later local-market adapter treats a sampled row as usable, it must
pass the read-only quarantine layer in
[GEN3_DATA_QUALITY.md](GEN3_DATA_QUALITY.md). The layer records only one
prioritized issue per bad row, never repairs source prices, and allows an
independently sourced replacement only as in-memory verification evidence.
It is not a formal source manifest and does not establish market coverage or
tradability.

The deliberately bounded corpus-level extension is documented in
[GEN3_MARKET_QUALITY_CAMPAIGN.md](GEN3_MARKET_QUALITY_CAMPAIGN.md). Its CLI
creates a footer-only metadata plan by default; only an explicit single-file
option reads market rows, and no default command starts a corpus-wide audit.

The `trend_cache_adjusted` quality campaign has now completed all 3,464 files
and is `complete_blocked`: 19 `ohlc_bounds` rows across 13 symbols remain
quarantined. This is a quality result, not market-data admission. No source
file was repaired, the campaign consumes none of the 42 strategy trials, and
footer-plus-size evidence remains weaker than an immutable content/PIT source
manifest. See [GEN3_MARKET_QUALITY_CAMPAIGN.md](GEN3_MARKET_QUALITY_CAMPAIGN.md)
for the canonical snapshot, campaign, aggregate hashes and isolated rows.

The next, still non-PIT, research-window content identity layer is documented
in [GEN3_MARKET_ADMISSION.md](GEN3_MARKET_ADMISSION.md). A real
`2015-01-01..2026-08-05` run now completed all 3,464 files and produced content
snapshot `sha256:c561e0ee1526398c347ffba01cb10fbc61b05390b5fc26c0d22c0a73545da759`.
It logically excluded 19 pre-start quality issues without repairing any source
file, but remains neither a PIT manifest nor a lockbox, and authorizes no
backtest or candidate. Tradability, index constituents, and the other required
PIT-provenanced sources remain admission blockers.

The market scope is now explicitly restricted to the Shanghai/Shenzhen main
boards. [GEN3_MAINBOARD_UNIVERSE.md](GEN3_MAINBOARD_UNIVERSE.md) defines the
draft local-only universe gate and rejects ChiNext, STAR, Beijing and every
other board. A local run completed 3,400 active main-board members with no
missing coverage (3,381 trend rows, 2 explicit historical zero entries and 17
local supplements), producing universe
`sha256:019754716649c9c5b1322ccaa55ec4ffdb81e426db9ce1e08ad53740a7a145a0`.
It remains non-PIT and does not authorize a backtest.

The next blocked evidence layer is [GEN3_TRADABILITY.md](GEN3_TRADABILITY.md):
daily market rows alone do not establish tradability.

The completed exploratory feasibility audit is documented in
[GEN3_TRADABILITY_AUDIT.md](GEN3_TRADABILITY_AUDIT.md). A small, fixed and
non-adjudicable VectorBT engine integration spike is documented separately in
[GEN3_VECTORBT_SPIKE.md](GEN3_VECTORBT_SPIKE.md); neither is a PIT manifest,
candidate screen, lockbox result, or authorization for backtesting.
