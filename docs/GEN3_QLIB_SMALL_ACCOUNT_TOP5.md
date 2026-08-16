# Qlib 30,000 RMB weekly Top-5 diagnostic

This is one fixed, approximate implementation diagnostic: not a candidate,
lockbox result, temporal OOS result, or formal budget-free experiment.  It is
implementation trial 6; if formally counted against the global 42 trials it
consumes one additional trial.

The account is 30,000 RMB.  It reserves at least 5,000 RMB cash, caps total
target risk capital at 25,000 RMB, caps each position including estimated open
cost at 5,000 RMB, and uses official Qlib 100-share lot rounding.  The first
weekly rebalance with a prior-session signal may build up to five positions;
later weekly rebalances may issue at most one sale and one purchase.  A ranked
security that cannot afford one lot plus cost is skipped and lower ranks are
considered.  Qlib Exchange/Position/SimulatorExecutor still execute fills,
costs, cash, and reports.

The installed pyqlib 0.9.7 provider is daily.  Therefore the implementation
uses a thin `TopkDropoutStrategy` subclass which returns empty decisions except
on the first observed session of an ISO week.  It reads the prior observed
session's prediction, so close-time information at T is ordered for the next
week's first observed-session open, never the same day.  This is not an
official Chinese exchange calendar or tradability proof.

Both the frozen original 200 and cross-sectional holdout 200 are reported
separately, with base and 2x costs, annual returns, equal-weight diagnostic
benchmark excess, drawdown, holdings, cash, skipped high-price orders and
minimum-fee-bound planned orders.  Neither sample is fresh temporal OOS, both
remain survivor-biased and non-PIT, and no 2022+ rows are used.

The initial v1 artifact is preserved but superseded because it omitted the
available official indicator `count` column from its trade-count field.  V2
reruns the identical frozen configuration solely to publish this required
official metric; it is still the same implementation trial 6, not a seventh
parameter or model trial.

## Fixed trial-6 result

The execution result is
`data/qlib_spikes/small-account-top5-weekly-fixed-original-holdout-2019-2021-v1/result.json`,
identity `sha256:61d224d9c526376bb291b020efd9488f088cae1a276a4f35824bbfa4643ce961`.
The hash-verified, read-only reporting correction is
`data/qlib_spikes/small-account-top5-weekly-fixed-original-holdout-2019-2021-v1-reporting-correction-v1.json`,
identity `sha256:125470dc4afa2802a76f86f4eaee11bfe2622b6cf6aed9bc0a38e661593e899a`.
It changed only the formerly omitted official trade count; no backtest was
rerun for that correction.

| Sample | Cost | Ann. return | Max DD | Ann. excess | Trades | Min cash | Median holdings |
| --- | --- | ---: | ---: | ---: | ---: | ---: | ---: |
| Original 200 | Base | 7.38% | -29.86% | -9.45% | 305 | 6,101 RMB | 5 |
| Original 200 | 2x | 5.74% | -31.54% | -10.79% | 305 | 5,328 RMB | 5 |
| Holdout 200 | Base | 13.61% | -17.86% | -8.94% | 299 | 9,007 RMB | 5 |
| Holdout 200 | 2x | 12.17% | -19.30% | -10.07% | 299 | 8,982 RMB | 5 |

Both samples fail the pre-registered feasibility gate: their costless,
survivor-biased equal-weight diagnostic benchmark had negative annualized
excess, while original200 also misses the base annual-return and 2x drawdown
requirements.  The run kept non-negative cash, made the initial five-position
build on 2019-01-07, and never exceeded one sale and one purchase on later
weekly decisions.  High-price skips/minimum-fee-bound planned buys were
227/155 for original200 and 124/152 for holdout200.  These diagnostics do not
make the configuration a candidate or authorize a lockbox/OOS run.

The official indicator records report 305 original200 and 299 holdout200
trades under both cost schedules.  Mean daily turnover is 5.35%/5.49% for
original base/2x and 4.30%/4.39% for holdout base/2x.  Cash ranged from 30,000
RMB before deployment down to the minima in the table; median cash was 12,094/
11,384 RMB and 20,235/19,320 RMB respectively.  Holdings ranged from zero
before the first permitted signal to five, with median five.  The artifact
records `minimum_fee_bound_planned_buys`, not a separate counter of every
candidate rejected solely by minimum commission; the high-price skip count is
therefore the conservative combined affordability counter and must not be
decomposed after the fact.
