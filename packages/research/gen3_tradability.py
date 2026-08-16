"""Strict, evidence-bound Phase-1 tradability contracts; no acquisition."""
from __future__ import annotations
from dataclasses import dataclass
from datetime import date,datetime,timezone
from hashlib import sha256
from zoneinfo import ZoneInfo
import json,math,re
SCHEMA='gen3-tradability-draft/v3'; START=date(2015,1,1);END=date(2026,8,5);DOMAINS=('daily_observation','daily_price_limits','listing_delisting','st_status','suspension','trade_calendar');H=re.compile(r'^sha256:[0-9a-f]{64}$'); REASONS=frozenset({'listed','not_listed','suspended','at_up_limit','at_down_limit','st','market_closed'})
def j(x):return json.dumps(x,sort_keys=True,separators=(',',':'),ensure_ascii=False,allow_nan=False).encode()
def h(x):return 'sha256:'+sha256(j(x)).hexdigest()
def hh(x,n='hash'):
 if not isinstance(x,str) or not H.fullmatch(x):raise ValueError(n+' invalid')
def finite(x,n):
 if type(x)not in(int,float) or isinstance(x,bool) or not math.isfinite(x) or x<=0:raise ValueError(n+' invalid')
def aware(x,n):
 if not isinstance(x,datetime) or x.tzinfo is None or x.utcoffset() is None:raise ValueError(n+' must be aware')
 return x.astimezone(timezone.utc)
@dataclass(frozen=True)
class TradabilityPolicy:
 universe_policy_hash:str;universe_predecision_hash:str;universe_hash:str;research_start:date;research_end:date;source_hashes:tuple[tuple[str,str],...];policy_hash:str;is_formal:bool=False;schema_version:str=SCHEMA
 def payload(self):return {'schema_version':self.schema_version,'universe_policy_hash':self.universe_policy_hash,'universe_predecision_hash':self.universe_predecision_hash,'universe_hash':self.universe_hash,'research_start':self.research_start.isoformat(),'research_end':self.research_end.isoformat(),'source_hashes':[list(x) for x in self.source_hashes],'is_formal':self.is_formal}
 def verify(self):
  if self.schema_version!=SCHEMA or self.is_formal or (self.research_start,self.research_end)!=(START,END) or tuple(x[0] for x in self.source_hashes)!=DOMAINS:raise ValueError('policy domains invalid')
  for x in (self.universe_policy_hash,self.universe_predecision_hash,self.universe_hash,self.policy_hash):hh(x)
  for d,x in self.source_hashes:
   if d not in DOMAINS:raise ValueError('policy domain invalid')
   hh(x)
  if self.policy_hash!=h(self.payload()):raise ValueError('policy hash invalid')
def make_policy(up,pre,universe,source_hashes):
 b=TradabilityPolicy(up,pre,universe,START,END,tuple(sorted(source_hashes)),'sha256:'+'0'*64);r=TradabilityPolicy(**{**b.__dict__,'policy_hash':h(b.payload())});r.verify();return r
@dataclass(frozen=True)
class SourceEvidence:
 domain:str;source_id:str;symbol:str|None;session:date;coverage:str;available_at:datetime;revision_id:str;content_hash:str;facts:tuple[tuple[str,object],...];record_hash:str
 def payload(self):return {'domain':self.domain,'source_id':self.source_id,'symbol':self.symbol,'session':self.session.isoformat(),'coverage':self.coverage,'available_at':aware(self.available_at,'available_at').isoformat(),'revision_id':self.revision_id,'content_hash':self.content_hash,'facts':[list(x) for x in self.facts]}
 def verify(self):
  if self.domain not in DOMAINS or not isinstance(self.source_id,str) or not self.source_id or self.symbol is not None and not re.fullmatch(r'\d{6}',self.symbol) or type(self.session)is not date or self.coverage not in {'explicit_symbol_session','complete_symbol_session_coverage','complete_exchange_session_calendar'} or not isinstance(self.revision_id,str) or not self.revision_id:raise ValueError('evidence invalid')
  required={'trade_calendar':'complete_exchange_session_calendar','suspension':'complete_symbol_session_coverage','st_status':'complete_symbol_session_coverage','daily_observation':'explicit_symbol_session','daily_price_limits':'explicit_symbol_session','listing_delisting':'explicit_symbol_session'}
  if self.coverage!=required[self.domain] or (self.domain=='trade_calendar')!=(self.symbol is None):raise ValueError('evidence coverage invalid')
  keys={'trade_calendar':('exchange','is_open'),'daily_observation':('open_price',),'daily_price_limits':('down_limit','up_limit'),'listing_delisting':('is_listed',),'st_status':('is_st',),'suspension':('is_suspended',)}[self.domain]
  if tuple(x[0] for x in self.facts)!=keys:raise ValueError('evidence facts invalid')
  values=dict(self.facts)
  if self.domain=='trade_calendar' and (values['exchange'] not in {'SSE','SZSE'} or type(values['is_open']) is not bool):raise ValueError('calendar facts invalid')
  if self.domain in {'listing_delisting','st_status','suspension'} and type(next(iter(values.values()))) is not bool:raise ValueError('boolean facts invalid')
  if self.domain=='daily_observation':finite(values['open_price'],'open_price')
  if self.domain=='daily_price_limits':
   finite(values['up_limit'],'up_limit');finite(values['down_limit'],'down_limit')
   if values['up_limit']<=values['down_limit']:raise ValueError('limit facts invalid')
  hh(self.content_hash);hh(self.record_hash)
  if self.record_hash!=h(self.payload()):raise ValueError('evidence hash invalid')
def build_evidence(domain,source_id,symbol,session,coverage,available_at,revision_id,content_hash,facts):
 b=SourceEvidence(domain,source_id,symbol,session,coverage,available_at,revision_id,content_hash,tuple(sorted(facts.items())),'sha256:'+'0'*64);r=SourceEvidence(**{**b.__dict__,'record_hash':h(b.payload())});r.verify();return r
@dataclass(frozen=True)
class ExchangeSession:
 exchange:str;session:date;is_open:bool;evidence:SourceEvidence;source_content_hash:str;policy_hash:str;record_hash:str
 def payload(self):return {'exchange':self.exchange,'session':self.session.isoformat(),'is_open':self.is_open,'evidence_hash':self.evidence.record_hash,'source_content_hash':self.source_content_hash,'policy_hash':self.policy_hash}
 def verify(self):
  self.evidence.verify();hh(self.policy_hash);hh(self.record_hash)
  cut=datetime.combine(self.session,datetime.min.time(),tzinfo=ZoneInfo('Asia/Shanghai')).replace(hour=9,minute=30).astimezone(timezone.utc)
  if self.exchange not in {'SSE','SZSE'} or type(self.is_open)is not bool or not START<=self.session<=END or self.evidence.domain!='trade_calendar' or self.evidence.symbol is not None or self.evidence.session!=self.session or self.evidence.available_at.astimezone(timezone.utc)>cut or self.source_content_hash!=self.evidence.content_hash or self.record_hash!=h(self.payload()):raise ValueError('exchange session invalid')
def build_session(evidence,policy):
 policy.verify();evidence.verify()
 session=evidence.session;exchange=dict(evidence.facts)['exchange'];is_open=dict(evidence.facts)['is_open'];cut=datetime.combine(session,datetime.min.time(),tzinfo=ZoneInfo('Asia/Shanghai')).replace(hour=9,minute=30).astimezone(timezone.utc)
 if not START<=session<=END or evidence.available_at.astimezone(timezone.utc)>cut:raise ValueError('calendar evidence unavailable by open')
 if dict(policy.source_hashes)['trade_calendar']!=evidence.content_hash:raise ValueError('calendar policy content hash mismatch')
 b=ExchangeSession(exchange,session,is_open,evidence,evidence.content_hash,policy.policy_hash,'sha256:'+'0'*64);r=ExchangeSession(**{**b.__dict__,'record_hash':h(b.payload())});r.verify();return r
@dataclass(frozen=True)
class DailyTradability:
 symbol:str;session:date;is_listed:bool;is_st:bool;is_suspended:bool;market_is_open:bool;exchange_session_hash:str;open_price:float;up_limit:float;down_limit:float;can_buy_open:bool;can_sell_open:bool;reason_codes:tuple[str,...];evidence_hashes:tuple[tuple[str,str],...];policy_hash:str;record_hash:str
 def payload(self):return {'symbol':self.symbol,'session':self.session.isoformat(),'is_listed':self.is_listed,'is_st':self.is_st,'is_suspended':self.is_suspended,'market_is_open':self.market_is_open,'exchange_session_hash':self.exchange_session_hash,'open_price':self.open_price,'up_limit':self.up_limit,'down_limit':self.down_limit,'can_buy_open':self.can_buy_open,'can_sell_open':self.can_sell_open,'reason_codes':list(self.reason_codes),'evidence_hashes':[list(x) for x in self.evidence_hashes],'policy_hash':self.policy_hash}
 def verify(self):
  if not re.fullmatch(r'\d{6}',self.symbol) or type(self.session)is not date or any(type(x)is not bool for x in (self.is_listed,self.is_st,self.is_suspended,self.market_is_open,self.can_buy_open,self.can_sell_open)) or tuple(x[0] for x in self.evidence_hashes)!=DOMAINS:raise ValueError('daily schema invalid')
  for _,x in self.evidence_hashes:hh(x)
  for x,n in ((self.open_price,'open'),(self.up_limit,'up'),(self.down_limit,'down')):finite(x,n)
  hh(self.exchange_session_hash,'exchange session hash');hh(self.policy_hash,'policy hash');hh(self.record_hash,'record hash')
  buy=self.market_is_open and self.is_listed and not self.is_suspended and self.open_price<self.up_limit;sell=self.market_is_open and self.is_listed and not self.is_suspended and self.open_price>self.down_limit
  reasons=set((['listed'] if self.is_listed else ['not_listed'])+(['st'] if self.is_st else [])+(['suspended'] if self.is_suspended else [])+(['market_closed'] if not self.market_is_open else [])+(['at_up_limit'] if self.market_is_open and self.is_listed and not self.is_suspended and self.open_price==self.up_limit else [])+(['at_down_limit'] if self.market_is_open and self.is_listed and not self.is_suspended and self.open_price==self.down_limit else []))
  if self.up_limit<=self.down_limit or (self.can_buy_open,self.can_sell_open)!=(buy,sell) or self.reason_codes!=tuple(sorted(reasons)) or self.record_hash!=h(self.payload()):raise ValueError('daily derivation invalid')
def derive_daily(symbol,exchange_session,evidence,policy):
 policy.verify(); es=tuple(sorted((x.domain,x.record_hash) for x in evidence))
 if tuple(x[0] for x in es)!=DOMAINS or len(es)!=6:raise ValueError('six evidence domains required')
 exchange_session.verify();session=exchange_session.session
 if exchange_session.policy_hash!=policy.policy_hash:raise ValueError('exchange session policy mismatch')
 cut=datetime.combine(session,datetime.min.time(),tzinfo=ZoneInfo('Asia/Shanghai')).replace(hour=9,minute=30).astimezone(timezone.utc)
 for x in evidence:
  x.verify()
  if x.session!=session or x.available_at.astimezone(timezone.utc)>cut or x.domain!='trade_calendar' and x.symbol!=symbol or dict(policy.source_hashes)[x.domain]!=x.content_hash:raise ValueError('evidence unavailable or mismatched')
 calendar=next(x for x in evidence if x.domain=='trade_calendar')
 if calendar.record_hash!=exchange_session.evidence.record_hash:raise ValueError('calendar evidence does not bind exchange session')
 facts={x.domain:dict(x.facts) for x in evidence};listed=facts['listing_delisting']['is_listed'];st=facts['st_status']['is_st'];suspended=facts['suspension']['is_suspended'];open_price=facts['daily_observation']['open_price'];up=facts['daily_price_limits']['up_limit'];down=facts['daily_price_limits']['down_limit']
 buy=exchange_session.is_open and listed and not suspended and open_price<up;sell=exchange_session.is_open and listed and not suspended and open_price>down;reasons=tuple(sorted(set((['listed']if listed else['not_listed'])+(['st']if st else[])+(['suspended']if suspended else[])+(['market_closed']if not exchange_session.is_open else[])+(['at_up_limit']if exchange_session.is_open and listed and not suspended and open_price==up else[])+(['at_down_limit']if exchange_session.is_open and listed and not suspended and open_price==down else[]))))
 b=DailyTradability(symbol,session,listed,st,suspended,exchange_session.is_open,exchange_session.record_hash,open_price,up,down,buy,sell,reasons,es,policy.policy_hash,'sha256:'+'0'*64);r=DailyTradability(**{**b.__dict__,'record_hash':h(b.payload())});r.verify();return r
@dataclass(frozen=True)
class AcquisitionSpec:
 domain:str;interface:str;terms_url:str;permission_status:str;spec_hash:str
 def verify(self):
  allowed={'trade_cal':'trade_calendar','suspend_d':'suspension','stk_limit':'daily_price_limits','stock_st':'st_status','namechange':'st_status'}
  if allowed.get(self.interface)!=self.domain or not self.terms_url.startswith('https://') or self.permission_status!=('unknown' if self.interface=='namechange' else 'requires_authorization') or self.spec_hash!=h({'domain':self.domain,'interface':self.interface,'terms_url':self.terms_url,'permission_status':self.permission_status}):raise ValueError('acquisition spec invalid')
def acquisition_spec(domain,interface,doc_id,status='requires_authorization'):
 url='https://tushare.pro/document/2' if doc_id is None else 'https://tushare.pro/document/2?doc_id='+str(doc_id)
 b=AcquisitionSpec(domain,interface,url,status,'sha256:'+'0'*64);r=AcquisitionSpec(**{**b.__dict__,'spec_hash':h({'domain':domain,'interface':interface,'terms_url':b.terms_url,'permission_status':status})});r.verify();return r
@dataclass(frozen=True)
class LocalTradabilityInventory:
 fields:tuple[str,...];missing_domains:tuple[str,...];status:str;inventory_hash:str
 def payload(self):return {'fields':list(self.fields),'missing_domains':list(self.missing_domains),'status':self.status}
 def verify(self):
  if self.status!='blocked_missing_authoritative_sources' or self.missing_domains!=('daily_price_limits','st_status','suspension','trade_calendar') or self.inventory_hash!=h(self.payload()):raise ValueError('inventory invalid')
def inspect_local_schema(columns):
 b=LocalTradabilityInventory(tuple(sorted(set(columns)&{'is_st','raw_prev_close','open','high','low','close','volume'})),('daily_price_limits','st_status','suspension','trade_calendar'),'blocked_missing_authoritative_sources','sha256:'+'0'*64);return LocalTradabilityInventory(**{**b.__dict__,'inventory_hash':h(b.payload())})
ACQUISITION_SPECS=(acquisition_spec('trade_calendar','trade_cal',26),acquisition_spec('suspension','suspend_d',214),acquisition_spec('daily_price_limits','stk_limit',183),acquisition_spec('st_status','stock_st',397),acquisition_spec('st_status','namechange',None,'unknown'))
