# Qlib cross-sectional holdout

This is one frozen cross-sectional holdout, not a new temporal OOS test.  It uses the next 200 securities after the
original ordered 200 whose frozen trend-content metadata spans 2015-01-05 through 2021-12-31.  Selection is based only
on that coverage metadata; it records the scanned prefix and all coverage exclusions before any model fit, prediction,
or portfolio result.  It must be disjoint from the original 200 and cannot replace a failed symbol after outcomes are
known.

The original 200 alone fit the fixed Alpha158 reduced-OHLCV LGB model on 2015--2017 and validate it on 2018.  Since the
old MLflow record preserved predictions but not a fitted model object, this run allows exactly one deterministic refit
bound to source result `sha256:8c975f44c12663c1ac53e7dd5569db37179ae53ff99eb5242ca9e38b8d0d8e33`; no holdout label,
processor fit, early stopping segment, or parameter tuning is permitted.  The fixed C portfolio is Topk=50/drop=1,
T-close prediction to T+1 open, 100-share units, the existing observed gates, and base/2x cost.  It reports official
Qlib IC/RankIC by year, transparent costless daily equal-weight benchmark diagnostics, and official Qlib reports.

Pass is pre-registered: IC and RankIC positive in every 2019--2021 year; base annualized return >8%, max drawdown
under 30%, base excess annualized >3%, stress excess annualized >0, and positive base annual excess in every year.
It remains non-PIT, survivor-biased, nonadjudicable, non-candidate, outside the lockbox and ledger.  No 2022+ data or
parameter variant is authorized.

## Completed v4 result

The completed write-once result is
`sha256:88de97497321c3f549b8b07af57cc7cbc8e0633d2beccfd8a636300e00a69d69`.  The frozen scan consumed a 584-symbol
ordered prefix: original 200 plus a disjoint next 200 holdout, with 184 coverage-only exclusions.  It selected no symbol
from a return, label, IC, or portfolio outcome.

Holdout annual IC means were 0.0536 (2019), 0.0448 (2020), and 0.0222 (2021); RankIC means were 0.0453, 0.0533, and
0.0342.  The fixed C Topk=50/drop=1 base run reported official Qlib annualized return 31.8032%, max drawdown -22.7834%,
and excess annualized return 7.6054%; 2x-cost absolute/excess annualized returns were 30.4314%/6.4762%.  Base annual
arithmetic excess was +14.9844% (2019), +3.3086% (2020), and +12.3156% (2021), so it passes the pre-registered holdout
gate.  This result is still a cross-sectional check, **not fresh temporal OOS**, and cannot become a candidate or
lockbox result without the blocked formal data/PIT/tradability/benchmark work.

The initial v1/v2 direct-script runs stopped on two package-import defects; v3 was deliberately interrupted before the
required direct-CLI synthetic E2E audit.  They are preserved as gitignored non-results.  A direct-CLI synthetic E2E then
successfully covered train, holdout prediction, official backtest, annual aggregation and write-once result publication
before v4 was allowed to run.
