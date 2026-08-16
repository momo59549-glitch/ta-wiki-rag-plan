from datetime import date
import unittest
from packages.research.gen3_tradability_exploratory import *
from scripts.run_gen3_tradability_probe import main
class T(unittest.TestCase):
 def p(self):return ExploratoryExecutionPolicy(.01)
 def r(self,d,**x):return ExploratoryRow(**({'symbol':'000001','session':d,'open':10.,'high':11.,'low':9.,'close':10.5,'volume':100.,'is_st':False}|x))
 def cal(self):return (date(2020,1,2),date(2020,1,3),date(2020,1,6),date(2020,1,7))
 def test_buy_missing(self):self.assertEqual(evaluate('000001',self.cal(),{},self.cal()[0],'buy',self.p()).resolution,'no_observed_row')
 def test_sell_delays(self):
  c=self.cal();rows={c[1]:self.r(c[1],volume=0),c[2]:self.r(c[2],is_st=True),c[3]:self.r(c[3])};x=evaluate('000001',c,rows,c[0],'sell',self.p());self.assertEqual((x.actual_session,x.delay_sessions),(c[3],3))
 def test_sell_unresolved(self):self.assertFalse(evaluate('000001',self.cal(),{},self.cal()[0],'sell',self.p()).fillable)
 def test_one_price_board(self):
  c=self.cal();b=self.r(c[0],open=10.,high=10.,low=10.,close=10.);self.assertFalse(evaluate('000001',c,{c[0]:b},c[0],'buy',self.p()).fillable)
 def test_calendar_order(self):
  with self.assertRaises(ValueError):evaluate('000001',(self.cal()[1],self.cal()[0]),{},self.cal()[0],'buy',self.p())
 def test_ohlcv_validation(self):
  with self.assertRaises(ValueError):self.r(self.cal()[0],volume=-1).verify()
 def test_cost_stress(self):
  x=apply_cost_stress(.1,self.p());self.assertAlmostEqual(x['base_net_return'],.09);self.assertAlmostEqual(x['two_x_net_return'],.08)
 def test_decision_tamper(self):
  x=evaluate('000001',self.cal(),{},self.cal()[0],'buy',self.p())
  with self.assertRaises(ValueError):ExploratoryDecision(**{**x.__dict__,'official_tradability_verified':True}).verify()
 def test_cli_gates(self):self.assertEqual(main(['probe']),2);self.assertEqual(main(['fallback-dry-run']),0)
 def test_symbol_and_mixed_rows_reject(self):
  c=self.cal()
  with self.assertRaises(ValueError):evaluate('ABC',c,{},c[0],'buy',self.p())
  bad=ExploratoryRow('000002',c[0],10.,11.,9.,10.5,1.,False)
  with self.assertRaises(ValueError):evaluate('000001',c,{c[0]:bad},c[0],'sell',self.p())
 def test_ohlc_cross_bounds_reject(self):
  with self.assertRaises(ValueError):self.r(self.cal()[0],open=12.,high=11.).verify()
  with self.assertRaises(ValueError):self.r(self.cal()[0],high=9.,low=10.).verify()
 def test_negative_return_and_decision_state_reject(self):
  self.assertAlmostEqual(apply_cost_stress(-.1,self.p())['two_x_net_return'],-.12)
  x=evaluate('000001',self.cal(),{},self.cal()[0],'buy',self.p())
  with self.assertRaises(ValueError):ExploratoryDecision(**{**x.__dict__,'resolution':'unresolved_no_eligible_observation'}).verify()
