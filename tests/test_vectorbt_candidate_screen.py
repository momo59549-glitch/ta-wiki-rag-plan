from datetime import date
import unittest
import pandas as pd
from unittest.mock import patch

from scripts.run_vectorbt_candidate_screen import CANDIDATES, benchmark, candidate_table, delay_exits, execution, period_slice, portfolio, signals, stats


class VectorbtCandidateScreenTests(unittest.TestCase):
    def setUp(self):
        self.index=pd.date_range("2019-01-01",periods=140)
        self.close=pd.DataFrame({"000001":range(10,150)},index=self.index,dtype=float)
        self.raw={"open":self.close*.99,"close":self.close,"volume":pd.DataFrame({"000001":[100.]*140},index=self.index)}
        self.eligible=pd.DataFrame({"000001":[True]*140},index=self.index)

    def test_preregistered_table_is_sorted_unique_and_hashed(self):
        table=candidate_table();self.assertEqual(len(table),len(CANDIDATES));self.assertEqual([x["semantic_id"] for x in table],sorted(x["semantic_id"] for x in table));self.assertTrue(all(x["semantic_hash"].startswith("sha256:") for x in table))

    def test_delayed_exit_waits_for_next_eligible_observation(self):
        planned=pd.DataFrame({"000001":[False,True,False,False]},index=self.index[:4]);eligible=pd.DataFrame({"000001":[True,False,False,True]},index=self.index[:4])
        actual=delay_exits(planned,eligible);self.assertTrue(actual.iloc[3,0]);self.assertEqual(int(actual.sum().sum()),1)

    def test_no_observation_cannot_create_signal_or_fill(self):
        eligible=self.eligible.copy();eligible.iloc[30]=False
        definition=next(x for x in CANDIDATES if x[0]=="mom20")
        entries,exits=execution(self.raw,self.close,eligible,definition,(date(2019,1,1),date(2019,12,31)))
        self.assertFalse(entries.iloc[30,0]);self.assertFalse(exits.iloc[30,0])

    def test_missing_raw_close_does_not_use_valuation_ffill_for_signal(self):
        raw={key:value.copy() for key,value in self.raw.items()};raw["close"].iloc[40,0]=float("nan")
        definition=next(x for x in CANDIDATES if x[0]=="mom20")
        entries,_=execution(raw,raw["close"].ffill(),self.eligible,definition,(date(2019,1,1),date(2019,5,20)))
        # A forward-filled close would make the 40->60 momentum comparison
        # positive; raw pct_change(fill_method=None) correctly leaves it false.
        self.assertFalse(entries.iloc[61,0])

    def test_execution_is_next_open_and_period_isolated(self):
        definition=next(x for x in CANDIDATES if x[0]=="mom20")
        entries,_=execution(self.raw,self.close,self.eligible,definition,(date(2019,1,1),date(2019,3,1)))
        self.assertFalse(entries.iloc[0,0]);self.assertFalse(entries.loc[entries.index.date>date(2019,3,1)].any().any())

    def test_signals_are_not_simultaneous(self):
        for definition in CANDIDATES:
            entries,exits=signals(self.close,self.raw["volume"],definition);self.assertFalse((entries.fillna(False)&exits.fillna(False)).any().any())

    def test_rsi_boundaries_are_100_on_rise_and_50_when_flat(self):
        idx=pd.date_range("2019-01-01",periods=30);volume=pd.DataFrame({"000001":[1.]*30},index=idx)
        rising=pd.DataFrame({"000001":range(1,31)},index=idx,dtype=float);flat=pd.DataFrame({"000001":[10.]*30},index=idx)
        definition=next(x for x in CANDIDATES if x[0]=="rsi14_30_55")
        self.assertTrue(signals(rising,volume,definition)[1].iloc[-1,0]) # RSI=100
        self.assertFalse(signals(flat,volume,definition)[0].iloc[-1,0]) # RSI=50, neither side
        self.assertFalse(signals(flat,volume,definition)[1].iloc[-1,0])

    def test_period_slice_prevents_post_period_price_leakage(self):
        idx=pd.date_range("2019-01-01",periods=4);v=pd.DataFrame({"000001":[10.,11.,12.,13.]},index=idx);o=v.copy();e=pd.DataFrame({"000001":[True,False,False,False]},index=idx);x=~e & False
        args=period_slice(o,v,e,x,period=(date(2019,1,1),date(2019,1,2)));before=stats(portfolio(*args,.0,.0))
        v.loc[idx[-1],"000001"]=1_000_000.;args=period_slice(o,v,e,x,period=(date(2019,1,1),date(2019,1,2)));after=stats(portfolio(*args,.0,.0))
        self.assertEqual(before,after)

    def test_confirmation_portfolio_excludes_discovery_return_and_marks_end(self):
        idx=pd.date_range("2019-01-01",periods=4);v=pd.DataFrame({"000001":[1.,100.,10.,11.]},index=idx);o=v.copy();e=pd.DataFrame({"000001":[False,False,True,False]},index=idx);x=~e & False
        args=period_slice(o,v,e,x,period=(date(2019,1,3),date(2019,1,4)));result=stats(portfolio(*args,.0,.0))
        self.assertGreater(result["total_return"],0.0) # open position is period-end marked
        self.assertLess(result["total_return"],0.01) # the pre-period 1->100 move is absent

    def test_benchmark_first_entry_is_delayed_to_next_eligible_open(self):
        idx=pd.date_range("2019-01-01",periods=4);raw={"open":pd.DataFrame({"000001":[10.]*4},index=idx)};v=raw["open"].copy();eligible=pd.DataFrame({"000001":[True,False,True,True]},index=idx);seen=[]
        class P:
            def annualized_return(self,**_):return 0
            def total_return(self,**_):return 0
            def max_drawdown(self,**_):return 0
            class trades:records=[]
        def fake(*args):seen.append(args[2]);return P()
        with patch("scripts.run_vectorbt_candidate_screen.portfolio",side_effect=fake):benchmark(raw,v,eligible,(date(2019,1,1),date(2019,1,4)),0,0)
        self.assertTrue(seen[0].iloc[2,0]);self.assertFalse(seen[0].iloc[1,0])

    def test_vectorbt_252_trading_day_annualization(self):
        import vectorbt as vbt
        idx=pd.date_range("2019-01-01",periods=253);close=pd.Series([100.]*252+[121.],index=idx)
        p=vbt.Portfolio.from_holding(close,init_cash=100_000,freq="1D")
        self.assertAlmostEqual(float(p.total_return()),.21,places=6)
        # VectorBT annualizes the 253 sampled bars over its exact duration;
        # this is approximately 21%, rather than calendar-day annualization.
        self.assertAlmostEqual(float(p.annualized_return(year_freq="252 days")),.21,places=2)
