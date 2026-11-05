"""SQLite leases and fair bounded scanning; successful inventories commit individually."""
import json,time,uuid
from datetime import datetime,timezone,timedelta
from concurrent.futures import ThreadPoolExecutor,wait,FIRST_COMPLETED
from .model import now
from .reconcile import prepare_source
from .sources import fetch
from .sources.base import request_budget,request_metrics
from .ingest import run_source

def apply_cadences(db,policy):
 from .scan_policy import apply
 return apply(db,policy)

def initialize(db):
 db.executescript('''CREATE TABLE IF NOT EXISTS scan_queue(source_id INTEGER PRIMARY KEY REFERENCES sources(id),due_at TEXT NOT NULL,lease_until TEXT,lease_token TEXT,attempts INTEGER NOT NULL DEFAULT 0,last_outcome TEXT);CREATE TABLE IF NOT EXISTS scan_batches(id TEXT PRIMARY KEY,started_at TEXT NOT NULL,finished_at TEXT,metrics TEXT);CREATE TABLE IF NOT EXISTS scan_attempts(id TEXT PRIMARY KEY,batch_id TEXT NOT NULL,source_id INTEGER NOT NULL REFERENCES sources(id),started_at TEXT NOT NULL,finished_at TEXT,status TEXT NOT NULL,metrics TEXT NOT NULL);CREATE TABLE IF NOT EXISTS provider_pauses(provider TEXT PRIMARY KEY,until_at TEXT NOT NULL,reason TEXT NOT NULL,bursts INTEGER NOT NULL DEFAULT 1);CREATE INDEX IF NOT EXISTS scan_attempts_source ON scan_attempts(source_id,started_at);''')

def eligible_sources(db):
 ids=set()
 for r in db.execute("select r.required_source_ids from recruiting_scope_reviews r join companies c on c.id=r.company_id where c.active=1 and c.canonical_company_id is null and r.state='VALIDATED'"):ids.update(json.loads(r[0]))
 # Board-level monitoring is useful even while another employer surface is unresolved.
 # This never certifies the employer and never admits an unproven/incomplete board.
 from .sources import ADAPTERS
 for s in db.execute("select s.* from sources s join companies c on c.id=s.company_id where s.active=1 and c.active=1 and s.source_role='AUTHORITATIVE' and s.last_successful_check_at is not null"):
  if s['ats_type'] not in ADAPTERS:continue
  census=db.execute('select jobs_returned from crawl_runs where source_id=? and authoritative=1 order by id desc limit 1',(s['id'],)).fetchone()
  count=db.execute('select count(*) from source_inventory where source_id=? and present=1',(s['id'],)).fetchone()[0]
  if census and count==census[0]:ids.add(s['id'])
 ids.update(r[0] for r in db.execute('select source_id from source_onboarding'))
 # Old scope reviews can retain a source ID after evidence marks it duplicate.
 # Preserve its queue/history row, but never dispatch a retired publication.
 return {sid for sid in ids if db.execute("select 1 from sources where id=? and active=1 and source_role='AUTHORITATIVE'",(sid,)).fetchone()}

def sync(db,at=None):
 initialize(db);stamp=at or now();ids=eligible_sources(db)
 with db:
  for sid in ids:
   s=db.execute('select * from sources where id=? and active=1 and source_role=\'AUTHORITATIVE\'',(sid,)).fetchone()
   if not s:continue
   due=(datetime.fromisoformat(s['last_successful_check_at'])+timedelta(seconds=s['interval_seconds'])).isoformat() if s['last_successful_check_at'] else stamp
   db.execute('insert or ignore into scan_queue(source_id,due_at) values(?,?)',(sid,due))
   # A shorter current cadence must not inherit a later old-policy due time.
   # Preserve retries/cooldowns and live leases; never postpone already due work.
   if s['health_status'].startswith('SUCCESS') and s['last_successful_check_at']:
    db.execute('update scan_queue set due_at=min(due_at,?) where source_id=? and attempts=0 and lease_token is null',(due,sid))
   # A complete recovery crawl can satisfy a due scan without recrawling it.
   # Never touch a live lease or infer success from a failed/partial inventory.
   latest_dispatch=db.execute('select max(finished_at) from scan_attempts where source_id=?',(sid,)).fetchone()[0]
   external_check=s['last_checked_at'] and (not latest_dispatch or s['last_checked_at']>latest_dispatch)
   if external_check and s['health_status']=='BLOCKED':
    delay=3600 if s['last_response_status']==429 else 86400
    not_before=(datetime.fromisoformat(s['last_checked_at'])+timedelta(seconds=delay)).isoformat()
    db.execute("update scan_queue set due_at=max(due_at,?),last_outcome='BLOCKED',attempts=max(attempts,1) where source_id=? and lease_token is null",(not_before,sid))
   elif external_check and not s['health_status'].startswith('SUCCESS'):
    q=db.execute('select attempts from scan_queue where source_id=?',(sid,)).fetchone()
    delay=min(86400,900*2**min(max((q[0] if q else 0)-1,0),7))
    not_before=(datetime.fromisoformat(s['last_checked_at'])+timedelta(seconds=delay)).isoformat()
    db.execute('update scan_queue set due_at=max(due_at,?),last_outcome=?,attempts=max(attempts,1) where source_id=? and lease_token is null',(not_before,s['health_status'],sid))
   if latest_dispatch and s['health_status'].startswith('SUCCESS') and s['last_successful_check_at'] and s['last_successful_check_at']>latest_dispatch:
    db.execute("update scan_queue set due_at=?,attempts=0,last_outcome=? where source_id=? and lease_token is null and (due_at<? or (last_outcome is not null and last_outcome not like 'SUCCESS%'))",(due,s['health_status'],sid,s['last_successful_check_at']))
 return ids

def claim(db,limit=40,lease_seconds=900,at=None):
 ids=sync(db,at);stamp=at or now()
 with db:
  for attempt in db.execute("select * from scan_attempts where status='RUNNING'").fetchall():
   q=db.execute('select * from scan_queue where source_id=?',(attempt['source_id'],)).fetchone();meta=json.loads(attempt['metrics'])
   if not q or not q['lease_until'] or q['lease_until']<=stamp or q['lease_token']!=meta.get('lease_token'):
    meta['recovered_at']=stamp;meta['interrupted']=True;db.execute("update scan_attempts set status='INTERRUPTED',finished_at=?,metrics=? where id=?",(stamp,json.dumps(meta),attempt['id']))
 token=uuid.uuid4().hex;until=(datetime.fromisoformat(stamp)+timedelta(seconds=lease_seconds)).isoformat()
 db.execute('BEGIN IMMEDIATE')
 try:
  rows=[dict(r) for r in db.execute('''select q.*,c.coverage_tier from scan_queue q join sources s on s.id=q.source_id join companies c on c.id=s.company_id where q.due_at<=? and (q.lease_until is null or q.lease_until<=?) order by q.due_at,c.coverage_tier,q.source_id''',(stamp,stamp)) if r['source_id'] in ids and not db.execute('select 1 from provider_pauses p join sources s on s.ats_type=p.provider where s.id=? and p.until_at>?',(r['source_id'],stamp)).fetchone()][:limit]
  for r in rows:db.execute('update scan_queue set lease_token=?,lease_until=? where source_id=?',(token,until,r['source_id']))
  db.commit()
 except BaseException:db.rollback();raise
 return token,rows

def finish(db,sid,token,status,at=None,retry_after_seconds=None):
 stamp=at or now();r=db.execute('select * from scan_queue where source_id=? and lease_token=?',(sid,token)).fetchone()
 if not r:raise RuntimeError('Scan lease ownership lost')
 s=db.execute('select * from sources where id=?',(sid,)).fetchone();attempts=0 if status.startswith('SUCCESS') else r['attempts']+1
 delay=s['interval_seconds'] if not attempts else min(86400,900*2**min(attempts-1,7))
 if status=='BLOCKED' and s['last_response_status']!=429:delay=max(delay,min(259200,86400*2**min(attempts-1,2)))
 if s['last_response_status']==429:delay=max(delay,3600)
 if retry_after_seconds is not None:delay=max(delay,retry_after_seconds)
 with db:db.execute('update scan_queue set due_at=?,lease_until=null,lease_token=null,attempts=?,last_outcome=? where source_id=? and lease_token=?',((datetime.fromisoformat(stamp)+timedelta(seconds=delay)).isoformat(),attempts,status,sid,token))

def scan(db,limit=40,workers=4,provider_limit=2,budget=180,max_seconds=1500,fetcher=fetch):
 if workers<1 or provider_limit<1:raise ValueError('Positive worker/provider bounds required')
 start=time.monotonic();token,rows=claim(db,limit,lease_seconds=int(max_seconds+budget+120));batch=uuid.uuid4().hex
 allocated=lambda:db.execute('pragma page_count').fetchone()[0]*db.execute('pragma page_size').fetchone()[0]
 storage_before=allocated()
 with db:db.execute('insert into scan_batches values(?,?,NULL,NULL)',(batch,now()))
 queue_rows={r['source_id']:r for r in rows};pending=[prepare_source(db,db.execute('select * from sources where id=?',(r['source_id'],)).fetchone()) for r in rows];active={};counts={};results=[];released=0;dns_failures={};paused=set()
 def check(s):
  with request_metrics() as cost:
   try:
    with request_budget(budget):value=fetcher(s)
   except Exception as e:value=e
  return value,dict(cost)
 with ThreadPoolExecutor(max_workers=workers) as pool:
  while pending or active:
   if time.monotonic()-start>=max_seconds:
    with db:
     for s in pending:db.execute('update scan_queue set lease_until=null,lease_token=null where source_id=? and lease_token=?',(s['id'],token))
    released+=len(pending);pending=[]
   for s in pending[:]:
    p=s['ats_type']
    if p in paused:
     with db:db.execute('update scan_queue set lease_until=null,lease_token=null where source_id=? and lease_token=?',(s['id'],token))
     pending.remove(s);released+=1;continue
    if len(active)>=workers:break
    if counts.get(p,0)>=provider_limit:continue
    pending.remove(s);counts[p]=counts.get(p,0)+1;attempt=uuid.uuid4().hex;stamp=now();q=queue_rows[s['id']];late=max(0,(datetime.fromisoformat(stamp)-datetime.fromisoformat(q['due_at'])).total_seconds())
    cadence=max(0,(datetime.fromisoformat(stamp)-datetime.fromisoformat(s['last_successful_check_at'])).total_seconds()-s['interval_seconds']) if s['last_successful_check_at'] else None
    meta=dict(provider=p,lease_token=token,scheduled_due_at=q['due_at'],queue_delay_seconds=late,cadence_late_seconds=cadence,effective_interval_seconds=s['interval_seconds'],retry_attempt=q['attempts']>0,prior_attempts=q['attempts'])
    with db:db.execute("insert into scan_attempts values(?,?,?,?,NULL,'RUNNING',?)",(attempt,batch,s['id'],stamp,json.dumps(meta)))
    active[pool.submit(check,s)]=(s,attempt,meta)
   if not active:break
   completed,_=wait(active,return_when=FIRST_COMPLETED)
   for f in completed:
    s,attempt,meta=active.pop(f);counts[s['ats_type']]-=1;value,cost=f.result()
    def fetched(_):
     if isinstance(value,Exception):raise value
     return value
    if not db.execute('select 1 from scan_queue where source_id=? and lease_token=?',(s['id'],token)).fetchone():continue
    t=time.monotonic();result=run_source(db,s,fetcher=fetched);ingest_seconds=time.monotonic()-t;finish(db,s['id'],token,result['status'],retry_after_seconds=getattr(value,'retry_after_seconds',None));meta.update(request_cost=cost,fetch_seconds=cost['runtime_seconds'],ingest_seconds=ingest_seconds,total_seconds=cost['runtime_seconds']+ingest_seconds)
    if not isinstance(value,Exception) and getattr(value,'inventory_evidence',None):meta['inventory_evidence']=value.inventory_evidence
    with db:db.execute('update scan_attempts set finished_at=?,status=?,metrics=? where id=?',(now(),result['status'],json.dumps(meta),attempt))
    if isinstance(value,Exception) and ('nodename' in str(value) or 'name resolution' in str(value)):
     from urllib.parse import urlsplit
     origins=dns_failures.setdefault(s['ats_type'],set());origins.add(urlsplit(s['careers_url'] or '').hostname)
     if len(origins)>=3:
      paused.add(s['ats_type']);old=db.execute('select bursts from provider_pauses where provider=?',(s['ats_type'],)).fetchone();bursts=(old[0] if old else 0)+1;until=(datetime.now(timezone.utc)+timedelta(seconds=min(14400,900*2**min(bursts-1,4)))).isoformat()
      with db:db.execute('insert into provider_pauses values(?,?,?,?) on conflict(provider) do update set until_at=excluded.until_at,reason=excluded.reason,bursts=excluded.bursts',(s['ats_type'],until,'Repeated DNS failures across at least three distinct publication hosts; defer, preserve sources and due history',bursts))
    result['operational_metrics']=meta;results.append(result);print(json.dumps(result),flush=True)
 stamp=now();eligible=sync(db);backlog=sum(r['source_id'] in eligible for r in db.execute('select source_id from scan_queue where due_at<=?',(stamp,)))
 metrics=dict(batch_id=batch,claimed=len(rows),attempted=len(results),success=sum(r['status'].startswith('SUCCESS') for r in results),failed=sum(not r['status'].startswith('SUCCESS') for r in results),skipped_capacity=released,backlog=backlog,runtime_seconds=round(time.monotonic()-start,3),allocated_database_growth_bytes=allocated()-storage_before,paused_providers=sorted(paused),results=results)
 with db:db.execute('update scan_batches set finished_at=?,metrics=? where id=?',(stamp,json.dumps(metrics),batch))
 return metrics
