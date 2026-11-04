"""Explicit capacity allocations; active recruiting cadence is never silently stretched."""
import json,math,statistics
from datetime import datetime,timezone,timedelta
from .db import canonical_id
from .model import now
from .scan_queue import initialize,eligible_sources
from .public_site import business_candidate

def apply(db,policy):
 initialize(db)
 db.executescript('''CREATE TABLE IF NOT EXISTS source_scan_policy(source_id INTEGER PRIMARY KEY REFERENCES sources(id),requested_interval_seconds INTEGER NOT NULL,effective_interval_seconds INTEGER NOT NULL,estimated_runtime_seconds REAL NOT NULL,reason TEXT NOT NULL,planned_at TEXT NOT NULL);CREATE TABLE IF NOT EXISTS scan_policy_events(id INTEGER PRIMARY KEY,source_id INTEGER NOT NULL REFERENCES sources(id),old_interval_seconds INTEGER,new_interval_seconds INTEGER NOT NULL,reason TEXT NOT NULL,recorded_at TEXT NOT NULL);''')
 ids=eligible_sources(db);costs={};provider_costs={}
 for r in db.execute("select * from scan_attempts where status like 'SUCCESS%' order by started_at"):
  m=json.loads(r['metrics']);cost=m.get('total_seconds')
  if cost is not None:costs.setdefault(r['source_id'],[]).append(cost);provider_costs.setdefault(m.get('provider'),[]).append(cost)
 cap=policy.get('capacity',{});reserve=cap.get('retry_reserve_fraction',.2);daily_runs=cap.get('runs_per_day',4);start_budget=policy.get('max_sources_per_run',80)*daily_runs*(1-reserve);worker_budget=policy.get('max_run_seconds',1500)*daily_runs*policy.get('workers',4)*(1-reserve);provider_budget=policy.get('max_run_seconds',1500)*daily_runs*policy.get('per_provider_concurrency',2)*(1-reserve)
 planned=[];cutoff=(datetime.now(timezone.utc)-timedelta(days=14)).isoformat()
 for row in db.execute('select * from sources'):
  s=dict(row);owner=db.execute('select * from companies where id=?',(canonical_id(db,s['company_id']),)).fetchone();active=False
  for j in db.execute("select j.title,j.function category,i.classification from source_inventory i join jobs j on j.id=i.job_id where i.source_id=? and i.present=1 and j.active=1 and j.internship_confidence>=.8 and j.geography_status='US'",(s['id'],)):
   state=json.loads(j['classification'] or '{}').get('eligibility',{}).get('status')
   if state not in ('VERIFIED_GRADUATE_ONLY','RESTRICTED') and business_candidate(j):active=True;break
  recent=db.execute("select j.title,j.function category,i.classification from source_inventory i join jobs j on j.id=i.job_id where i.source_id=? and i.discovery_kind='NEW_SINCE_PREVIOUS_SUCCESSFUL_INVENTORY' and i.first_seen_at>=?",(s['id'],cutoff)).fetchall()
  signal=any(business_candidate(r) and json.loads(r['classification']).get('internship_confidence',0)>=.8 and json.loads(r['classification']).get('geography_status')=='US' and json.loads(r['classification']).get('eligibility',{}).get('status') not in ('VERIFIED_GRADUATE_ONLY','RESTRICTED') for r in recent)
  requested=policy['tier_intervals_seconds'][str(owner['coverage_tier'])]*(1 if active or signal else policy.get('inactive_interval_multiplier',2));sample=costs.get(s['id'],[])[-5:] or provider_costs.get(s['ats_type'],[])[-20:];estimate=max(5,statistics.median(sample) if sample else cap.get('unmeasured_source_seconds',30))
  if s['id'] in ids:planned.append(dict(source_id=s['id'],tier=owner['coverage_tier'],score=owner['coverage_score'],provider=s['ats_type'],active=active or signal,requested=requested,effective=requested,estimate=estimate))
  else:
   with db:db.execute('update sources set interval_seconds=? where id=?',(requested,s['id']))
 def load():
  starts=sum(86400/r['effective'] for r in planned);work=sum(86400/r['effective']*r['estimate'] for r in planned);providers={}
  for r in planned:providers[r['provider']]=providers.get(r['provider'],0)+86400/r['effective']*r['estimate']
  return starts,work,providers
 before=load();changed=[]
 if cap.get('enabled',False):
  candidates=sorted([r for r in planned if not r['active'] and r['tier']>1],key=lambda r:(-r['tier'],r['score'],-r['estimate'],r['source_id']))
  for r in candidates:
   while True:
    starts,work,providers=load();over=starts>start_budget or work>worker_budget or any(v>provider_budget for v in providers.values())
    maximum=cap.get('inactive_max_interval_seconds',{}).get(str(r['tier']),r['requested'])
    if not over or r['effective']>=maximum:break
    r['effective']=min(maximum,r['effective']*2)
 after=load();stamp=now()
 with db:
  for r in planned:
   reason='ACTIVE_OR_RECENT_RECRUITING' if r['active'] else 'NO_CURRENT_OR_RECENT_RELEVANT_INTERNSHIPS'
   if r['effective']>r['requested']:reason+='; EXPLICIT_CAPACITY_ALLOCATION'
   old=db.execute('select * from source_scan_policy where source_id=?',(r['source_id'],)).fetchone()
   if not old or old['effective_interval_seconds']!=r['effective'] or old['requested_interval_seconds']!=r['requested']:
    db.execute('insert into scan_policy_events(source_id,old_interval_seconds,new_interval_seconds,reason,recorded_at) values(?,?,?,?,?)',(r['source_id'],old['effective_interval_seconds'] if old else None,r['effective'],reason,stamp));changed.append(r['source_id'])
   db.execute('insert into source_scan_policy values(?,?,?,?,?,?) on conflict(source_id) do update set requested_interval_seconds=excluded.requested_interval_seconds,effective_interval_seconds=excluded.effective_interval_seconds,estimated_runtime_seconds=excluded.estimated_runtime_seconds,reason=excluded.reason,planned_at=excluded.planned_at',(r['source_id'],r['requested'],r['effective'],r['estimate'],reason,stamp));db.execute('update sources set interval_seconds=? where id=?',(r['effective'],r['source_id']))
 return dict(planned_at=stamp,scheduled_sources=len(planned),requested_daily_starts=before[0],allocated_daily_starts=after[0],planned_start_budget=start_budget,nominal_start_ceiling=policy.get('max_sources_per_run',80)*daily_runs,retry_reserve_fraction=reserve,estimated_worker_seconds=after[1],worker_seconds_budget=worker_budget,estimated_provider_seconds=after[2],provider_seconds_budget=provider_budget,allocation_satisfied=after[0]<=start_budget and after[1]<=worker_budget and all(v<=provider_budget for v in after[2].values()),changed_sources=changed,policy_reductions=sum(r['effective']>r['requested'] for r in planned),notes='Capacity is a local planning bound, not demonstrated hosted throughput. Active/recent recruiting intervals are never lengthened. Inactive allocation changes are explicit; due queue history is retained.')
