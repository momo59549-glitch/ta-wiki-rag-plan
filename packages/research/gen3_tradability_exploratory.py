"""Synthetic-only conservative sequence evaluator; never official tradability."""
from __future__ import annotations
from dataclasses import dataclass
from datetime import date
import math
SCHEMA='gen3-tradability-exploratory/v2'
def num(x,n,nonnegative=False):
 if type(x)not in(int,float) or isinstance(x,bool) or not math.isfinite(x) or (x<0 if nonnegative else x<=0):raise ValueError(n+' invalid')
@dataclass(frozen=True)
class ExploratoryExecutionPolicy:
 base_round_trip_cost:float;cost_multiplier:float=2.;approximate_tradability:bool=True;official_tradability_verified:bool=False;adjudicable:bool=False;schema_version:str=SCHEMA
 def verify(self):
  num(self.base_round_trip_cost,'cost',True);num(self.cost_multiplier,'multiplier');
  if self.schema_version!=SCHEMA or self.cost_multiplier!=2 or not self.approximate_tradability or self.official_tradability_verified or self.adjudicable:raise ValueError('policy invalid')
@dataclass(frozen=True)
class ExploratoryRow:
 symbol:str;session:date;open:float;high:float;low:float;close:float;volume:float;is_st:bool
 def verify(self):
  if not isinstance(self.symbol,str) or not self.symbol.isdigit() or len(self.symbol)!=6 or type(self.session)is not date or type(self.is_st)is not bool:raise ValueError('row invalid')
  for x,n in ((self.open,'open'),(self.high,'high'),(self.low,'low'),(self.close,'close')):num(x,n)
  num(self.volume,'volume',True)
  if self.high<max(self.open,self.high,self.low,self.close) or self.low>min(self.open,self.high,self.low,self.close) or self.high<self.low:raise ValueError('ohlc invalid')
 def suspected_board(self):return self.open==self.high==self.low==self.close
@dataclass(frozen=True)
class ExploratoryDecision:
 symbol:str;requested_session:date;side:str;fillable:bool;actual_session:date|None;delay_sessions:int;resolution:str;approximate_tradability:bool=True;official_tradability_verified:bool=False;adjudicable:bool=False
 def verify(self):
  if not isinstance(self.symbol,str) or not self.symbol.isdigit() or len(self.symbol)!=6 or self.side not in {'buy','sell'} or type(self.requested_session)is not date or type(self.delay_sessions)is not int or self.delay_sessions<0 or self.approximate_tradability is not True or self.official_tradability_verified is not False or self.adjudicable is not False or self.fillable!=(self.actual_session is not None):raise ValueError('decision invalid')
  allowed={'buy':{'approximate_observed_row','no_observed_row','st_excluded','nonpositive_volume','suspected_one_price_board'},'sell':{'delayed_approximate_observed_row','unresolved_no_eligible_observation'}}
  if self.resolution not in allowed[self.side]:raise ValueError('decision resolution invalid')
  if self.side=='buy' and ((self.fillable and (self.actual_session!=self.requested_session or self.delay_sessions!=0)) or (not self.fillable and (self.actual_session is not None or self.delay_sessions!=0))):raise ValueError('buy decision invalid')
  if self.side=='sell' and (self.fillable and (self.actual_session<self.requested_session or self.delay_sessions<0) or not self.fillable and self.actual_session is not None):raise ValueError('sell decision invalid')
def _decision(*args):
 d=ExploratoryDecision(*args);d.verify();return d
def apply_cost_stress(raw_return,policy):
 policy.verify()
 if type(raw_return)not in(int,float) or isinstance(raw_return,bool) or not math.isfinite(raw_return):raise ValueError('raw return invalid')
 return {'raw_return':raw_return,'base_net_return':raw_return-policy.base_round_trip_cost,'two_x_net_return':raw_return-policy.base_round_trip_cost*policy.cost_multiplier}
def evaluate(symbol,calendar,rows,requested_session,side,policy):
 policy.verify();cal=tuple(calendar)
 if not isinstance(symbol,str) or not symbol.isdigit() or len(symbol)!=6 or not cal or tuple(sorted(cal))!=cal or len(set(cal))!=len(cal) or any(type(x)is not date for x in cal) or requested_session not in cal:raise ValueError('calendar invalid')
 if not isinstance(rows,dict):raise ValueError('rows invalid')
 for d,r in rows.items():
  if d not in cal or not isinstance(r,ExploratoryRow) or r.session!=d or r.symbol!=symbol:raise ValueError('row mapping invalid')
  r.verify()
 if side not in {'buy','sell'}:raise ValueError('side invalid')
 start=cal.index(requested_session)
 if side=='buy':
  r=rows.get(requested_session)
  if r is None:return _decision(symbol,requested_session,side,False,None,0,'no_observed_row')
  if r.is_st:return _decision(symbol,requested_session,side,False,None,0,'st_excluded')
  if r.volume<=0:return _decision(symbol,requested_session,side,False,None,0,'nonpositive_volume')
  if r.suspected_board():return _decision(symbol,requested_session,side,False,None,0,'suspected_one_price_board')
  return _decision(symbol,requested_session,side,True,requested_session,0,'approximate_observed_row')
 for i,d in enumerate(cal[start:],start):
  r=rows.get(d)
  if r is None or r.is_st or r.volume<=0 or r.suspected_board():continue
  return _decision(symbol,requested_session,side,True,d,i-start,'delayed_approximate_observed_row')
 return _decision(symbol,requested_session,side,False,None,len(cal)-1-start,'unresolved_no_eligible_observation')
