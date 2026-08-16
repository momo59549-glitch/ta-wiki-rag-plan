from datetime import date,datetime,timezone
import unittest
from packages.research.gen3_tradability import *
H=lambda c:'sha256:'+c*64
class TradabilityTests(unittest.TestCase):
 def policy(self):return make_policy(H('a'),H('b'),'sha256:019754716649c9c5b1322ccaa55ec4ffdb81e426db9ce1e08ad53740a7a145a0',tuple((d,H('abcdef'[i])) for i,d in enumerate(DOMAINS)))
 def evidence(self,*,session=date(2020,1,2),late=False):
  at=datetime(2020,1,2,1 if not late else 2,0,tzinfo=timezone.utc);out=[]
  for i,d in enumerate(DOMAINS):
   coverage='complete_exchange_session_calendar' if d=='trade_calendar' else 'complete_symbol_session_coverage' if d in {'suspension','st_status'} else 'explicit_symbol_session';facts={'trade_calendar':{'exchange':'SSE','is_open':True},'daily_observation':{'open_price':10.},'daily_price_limits':{'up_limit':11.,'down_limit':9.},'listing_delisting':{'is_listed':True},'st_status':{'is_st':False},'suspension':{'is_suspended':False}}[d];out.append(build_evidence(d,'source',None if d=='trade_calendar' else '000001',session,coverage,at,'r1',H('abcdef'[i]),facts))
  return tuple(out)
 def session(self,policy=None,is_open=True,evidence=None):
  policy=policy or self.policy(); evidence=list(evidence or self.evidence());cal=next(x for x in evidence if x.domain=='trade_calendar');
  if not is_open:cal=build_evidence('trade_calendar',cal.source_id,None,cal.session,cal.coverage,cal.available_at,cal.revision_id,cal.content_hash,{'exchange':'SSE','is_open':False})
  return build_session(cal,policy)
 def test_normal_two_sided(self):
  p=self.policy();x=derive_daily('000001',self.session(p),self.evidence(),p);self.assertTrue(x.can_buy_open);self.assertTrue(x.can_sell_open)
 def test_limit_equalities(self):
  p=self.policy();s=self.session(p);up=list(self.evidence());up[0]=build_evidence('daily_observation','source','000001',date(2020,1,2),'explicit_symbol_session',up[0].available_at,'r1',H('a'),{'open_price':11.});down=list(self.evidence());down[0]=build_evidence('daily_observation','source','000001',date(2020,1,2),'explicit_symbol_session',down[0].available_at,'r1',H('a'),{'open_price':9.});self.assertFalse(derive_daily('000001',s,up,p).can_buy_open);self.assertFalse(derive_daily('000001',s,down,p).can_sell_open)
 def test_closed_market_never_tradable(self):
  p=self.policy();e=list(self.evidence());s=self.session(p,False,e);e[5]=s.evidence;x=derive_daily('000001',s,e,p);self.assertEqual((x.can_buy_open,x.can_sell_open,x.reason_codes),(False,False,('listed','market_closed')))
 def test_suspended_and_unlisted_never_tradable(self):
  p=self.policy();s=self.session(p);susp=list(self.evidence());susp[4]=build_evidence('suspension','source','000001',date(2020,1,2),'complete_symbol_session_coverage',susp[4].available_at,'r1',H('e'),{'is_suspended':True});unlisted=list(self.evidence());unlisted[2]=build_evidence('listing_delisting','source','000001',date(2020,1,2),'explicit_symbol_session',unlisted[2].available_at,'r1',H('c'),{'is_listed':False});self.assertFalse(derive_daily('000001',s,susp,p).can_buy_open);self.assertFalse(derive_daily('000001',s,unlisted,p).can_sell_open)
 def test_missing_duplicate_and_late_evidence_reject(self):
  p=self.policy();s=self.session(p)
  with self.assertRaises(ValueError):derive_daily('000001',s,self.evidence()[:-1],p)
  with self.assertRaises(ValueError):derive_daily('000001',s,self.evidence()+self.evidence()[:1],p)
  with self.assertRaises(ValueError):derive_daily('000001',s,self.evidence(late=True),p)
 def test_coverage_calendar_and_policy_binding_reject(self):
  p=self.policy();e=list(self.evidence());bad=SourceEvidence(**{**e[3].__dict__,'coverage':'explicit_symbol_session','record_hash':h({**e[3].payload(),'coverage':'explicit_symbol_session'})});e[3]=bad
  with self.assertRaises(ValueError):derive_daily('000001',self.session(p),e,p)
  cal=next(x for x in self.evidence() if x.domain=='trade_calendar');badcal=SourceEvidence(**{**cal.__dict__,'symbol':'000001','record_hash':h({**cal.payload(),'symbol':'000001'})})
  with self.assertRaises(ValueError):build_session(badcal,p)
  other=make_policy(H('a'),H('b'),'sha256:019754716649c9c5b1322ccaa55ec4ffdb81e426db9ce1e08ad53740a7a145a0',tuple((d,H('fedcba'[i])) for i,d in enumerate(DOMAINS)))
  with self.assertRaises(ValueError):build_session(cal,other)
 def test_session_range_cutoff_and_direct_tamper_reject(self):
  p=self.policy();cal=next(x for x in self.evidence() if x.domain=='trade_calendar');late=SourceEvidence(**{**cal.__dict__,'available_at':datetime(2020,1,2,2,tzinfo=timezone.utc),'record_hash':h({**cal.payload(),'available_at':datetime(2020,1,2,2,tzinfo=timezone.utc).isoformat()})})
  with self.assertRaises(ValueError):build_session(late,p)
  old=build_evidence('trade_calendar','source',None,date(2014,12,31),'complete_exchange_session_calendar',cal.available_at,'r1',cal.content_hash,{'exchange':'SSE','is_open':True})
  with self.assertRaises(ValueError):build_session(old,p)
  s=self.session(p);bad=ExchangeSession(**{**s.__dict__,'policy_hash':'bad','record_hash':h({**s.payload(),'policy_hash':'bad'})})
  with self.assertRaises(ValueError):bad.verify()
 def test_daily_direct_open_flags_reasons_and_hash_reject(self):
  p=self.policy();x=derive_daily('000001',self.session(p),self.evidence(),p)
  for key,value in [('open_price',11.),('can_buy_open',False),('reason_codes',('listed','suspended')),('policy_hash','bad'),('record_hash','bad')]:
   data={**x.__dict__,key:value};
   if key not in {'record_hash'}:data['record_hash']=h({**x.payload(),key:value})
   with self.assertRaises(ValueError):DailyTradability(**data).verify()
 def test_nan_bool_and_limit_order_reject(self):
  p=self.policy();s=self.session(p)
  for facts in ({'open_price':float('nan')},{'open_price':True},{'up_limit':9.,'down_limit':9.}):
   e=list(self.evidence());idx=1 if 'open_price'in facts else 2; base=dict(e[idx].facts)|facts
   with self.assertRaises(ValueError):build_evidence(e[idx].domain,'source','000001',date(2020,1,2),e[idx].coverage,e[idx].available_at,'r1',e[idx].content_hash,base)
 def test_acquisition_domain_pairs(self):
  self.assertEqual({x.interface for x in ACQUISITION_SPECS},{'trade_cal','suspend_d','stk_limit','stock_st','namechange'})
  with self.assertRaises(ValueError):acquisition_spec('suspension','trade_cal',26)
 def test_domain_facts_and_calendar_identity_are_bound(self):
  p=self.policy();e=self.evidence()
  for item in e:
   with self.assertRaises(ValueError):build_evidence(item.domain,item.source_id,item.symbol,item.session,item.coverage,item.available_at,item.revision_id,item.content_hash,dict(item.facts)|{'extra':1})
  other=list(self.evidence());other[5]=build_evidence('trade_calendar','source',None,date(2020,1,2),'complete_exchange_session_calendar',other[5].available_at,'r2',H('f'),{'exchange':'SSE','is_open':True})
  with self.assertRaises(ValueError):derive_daily('000001',self.session(p),other,p)
