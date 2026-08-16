# Survivorship-bias coverage audit

This bounded audit reconstructs 2019--2021 mainboard membership retrospectively from the current 10-field
`tushare.stock_basic` manifest's listing/delisting dates.  It is **not** a PIT universe: the current manifest revision
does not establish what was known at any historical date.  It does not train, predict, backtest, tune, consume trial
budget, or read market rows after 2021-12-31.

For each year it includes every SH/SZ user-allowed mainboard record whose active interval intersects that calendar year,
including subsequently delisted securities.  It then attributes rows only to the frozen trend/supplement sources and
classifies each historical member as complete, partial, or zero against the local observed-session union for that year.
That union is a coverage denominator, not an official trading calendar.  The audit explicitly records missing symbols,
current-active intersection, non-active/delisted membership, and whether the original/holdout 200 are survivorship-biased.

It can support a future request to run one fixed historical-universe rerun only if annual coverage is adequate and formal
PIT/tradability/benchmark blockers are separately resolved.  A positive coverage result does not authorize that rerun.
# Real read-only audit result (2026-08-13)

The bounded retrospective audit completed without reading market rows after
2021-12-31.  Its write-once artifact is
`data/qlib_spikes/survivorship-coverage-audit-2019-2021-v1.json`, with
identity `sha256:eed0f3540bd8e47441e0d205d924cea597074091cae3c3d5f0bde6e52565acd4`.
It is an audit of the current manifest's listing/delisting fields, not a PIT
membership reconstruction.

| Year | Historical main-board members | Current-active intersection | Non-active/delisted | Complete OHLCV | Partial OHLCV | Zero OHLCV | Complete coverage |
| --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: |
| 2019 | 2,909 | 2,718 | 191 | 2,449 | 457 | 3 | 84.19% |
| 2020 | 3,043 | 2,862 | 181 | 2,500 | 541 | 2 | 82.16% |
| 2021 | 3,155 | 2,983 | 172 | 2,653 | 498 | 4 | 84.09% |

The frozen original 200-symbol sample and its 200-symbol cross-sectional
holdout are each current-survivor samples: every symbol in each sample is in
the current-active intersection for all three years.  The audit used frozen
trend attribution only; the local supplement has no rows in this window.

This is not sufficient to authorize a historical-universe rerun.  Hundreds of
historical members have only partial observations and 2--4 have none in each
year.  A future fixed historical-universe run needs an explicit pre-registered
coverage/exclusion policy and remediation of missing source coverage, while
the existing PIT, official tradability, and benchmark/index-constituent
blockers remain in force.  No model training, backtest, candidate promotion,
lockbox activity, or trial-budget consumption occurred here.

## V2: listing/delisting-aware result

V1's full-year denominator incorrectly counted the portion before a legitimate
IPO and after a legitimate delisting as a coverage failure.  V2 instead uses
each symbol-year's expected interval: `max(year start, list date)` through
`min(year end, delist date when known)`, evaluated against the observed
main-board session union.  That calendar is still only a local approximation,
not an official exchange calendar; manifest dates remain retrospective rather
than PIT evidence.

The thresholds were frozen before execution: observed/expected ratio at least
99%, at most three internal missing sessions, annual adequate ratio at least
95%, non-active/delisted adequate ratio at least 90%, and zero coverage at
most 0.5%.  The write-once v2 artifact is
`data/qlib_spikes/survivorship-coverage-audit-2019-2021-v2.json`, identity
`sha256:bc7ce2f1a53d6d6af68314d2f1d869a889009b0eac8239430673eb4b03ba1966`.

| Year | Historical | Adequate | Adequate ratio | Non-active | Non-active adequate | Non-active ratio | Zero | Pass |
| --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: | --- |
| 2019 | 2,909 | 2,716 | 93.37% | 191 | 154 | 80.63% | 3 | No |
| 2020 | 3,043 | 2,855 | 93.82% | 181 | 143 | 79.01% | 2 | No |
| 2021 | 3,155 | 2,994 | 94.90% | 172 | 138 | 80.23% | 4 | No |

Thus correcting structural partial years materially improves the interpretation
but does not meet the pre-registered gate: annual adequate coverage is below
95% and non-active/delisted coverage is substantially below 90% in all years.
No historical-universe rerun is authorized.  The current original and holdout
samples also remain survivor-biased: their current-active membership is,
respectively, 180/200 and 191/200 in every audited year.

## Targeted local recovery policy

The next bounded step may inspect only v2's inadequate/zero symbol-years and
their exact missing sessions.  It first compares a candidate local source with
the frozen adjusted trend source on at least 20 overlapping sessions.  The
median close scale must be time-invariant to relative dispersion `<= 1e-6`,
and every same-date OHLC scale must agree with that close scale at the same
tolerance.  Any time-varying scale, duplicate session, invalid OHLCV row, or
insufficient overlap is incompatible.  No future-calibrated adjustment is
allowed.

Accepted rows are emitted only as a gitignored, write-once overlay with source
path, full-file hash, session, OHLCV, and row hash; source caches are never
modified.  Coverage is then recomputed as frozen v2 totals plus only the exact
targeted overlay deltas.  This remains a retrospective/non-PIT recovery and
does not authorize training or backtesting by itself.

An all-null post-delisting placeholder is treated as an absent observation, not
as a valid candidate row or compatibility overlap.  Any partially populated or
otherwise non-finite OHLCV row still rejects that source.

### Local recovery result

The bounded local audit completed with no source mutation and no market reads
after 2021.  The valid write-once result is
`data/survivorship_gap_overlays/gap-recovery-overlay-v2.json`, identity
`sha256:df3aabed659a89d416d2e0f61c55dd64f5e06593cb2dad7eada19ec513365c44`.
It found zero eligible overlay rows.  `local_cache` had an adequate overlap for
60 targeted symbols but supplied none of their exact missing sessions; the
remaining local files had insufficient overlap.  `tushare_incremental_cache`
also had only insufficient/absent overlap for these targets, and
`tushare_daily_cache` had no targeted files.  This is a source-availability
result, not evidence that missing historical trading did not occur.

Consequently v3 aggregate coverage is unchanged from v2: 93.37%, 93.82%, and
94.90% adequate in 2019--2021; non-active/delisted adequate coverage remains
80.63%, 79.01%, and 80.23%.  All pre-registered gates remain blocked.  An
earlier `gap-recovery-overlay-v1.json` is superseded by v2 because it treated
all-null post-delisting placeholders as malformed observations rather than
absent rows; it must not be used.
