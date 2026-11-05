"""Read-only GitHub evidence; persisted independently of scanner success records."""
import json,os,urllib.request
from datetime import datetime,timedelta,timezone
from zoneinfo import ZoneInfo
from .model import now
from .sources.transport import tls_context

CRON='17 1,7,13,19 * * *'
HOURS=(1,7,13,19)

def fetch(path):
 token=os.environ.get('GH_TOKEN')
 headers={'Accept':'application/vnd.github+json','User-Agent':'Scout-health','X-GitHub-Api-Version':'2022-11-28'}
 if token:headers['Authorization']='Bearer '+token
 with urllib.request.urlopen(urllib.request.Request('https://api.github.com'+path,headers=headers),context=tls_context(),timeout=30) as r:return json.load(r)

def collect(db,repository,get=fetch):
 root='/repos/'+repository;stamp=now();records=[]
 try:
  workflow=get(root+'/actions/workflows/scout.yml');repo=get(root)
  # Page through the evaluation history, not only the latest few dispatches.
  for page in range(1,21):
   rows=get(root+f'/actions/workflows/scout.yml/runs?per_page=100&page={page}')['workflow_runs']
   for r in rows:
    row={k:r.get(k) for k in ('id','run_attempt','event','status','conclusion','created_at','run_started_at','updated_at','head_sha','html_url','path')}
    previous=db.execute('select payload from platform_workflow_runs where run_id=? and run_attempt=?',(str(r['id']),r.get('run_attempt',1))).fetchone()
    if previous and json.loads(previous[0]).get('status')=='completed':row=json.loads(previous[0])
    else:
     jobs=get(root+f"/actions/runs/{r['id']}/attempts/{r.get('run_attempt',1)}/jobs?per_page=100")['jobs']
     row['jobs']=[{k:j.get(k) for k in ('name','status','conclusion','started_at','completed_at','steps')} for j in jobs]
    records.append(row)
   if len(rows)<100:break
  else:raise RuntimeError('Workflow history exceeded 2000 records; evidence collection incomplete')
  with db:
   for row in records:db.execute('insert or replace into platform_workflow_runs values(?,?,?,?)',(str(row['id']),row.get('run_attempt',1),stamp,json.dumps(row)))
   observation=dict(repository=repository,default_branch=repo['default_branch'],workflow_state=workflow['state'],cron=CRON,history_complete=True)
   db.execute('insert into platform_observations(observed_at,success,payload) values(?,1,?)',(stamp,json.dumps(observation)))
  return observation
 except Exception as e:
  with db:db.execute('insert into platform_observations(observed_at,success,payload) values(?,0,?)',(stamp,json.dumps(dict(error=str(e),history_complete=False))))
  return dict(history_complete=False,error=str(e))

def step_state(row,name):
 steps=[s for j in row.get('jobs',[]) for s in j.get('steps',[]) if s['name']==name]
 return steps[-1].get('conclusion') if steps else None

def verification(row):
 checks=dict(restore=step_state(row,'Restore durable scanner state (fail closed)'),scan=step_state(row,'Scan due sources and validate publication'),checkpoint=step_state(row,'Persist committed work even after scan or export failure'),publication=next((j.get('conclusion') for j in row.get('jobs',[]) if j['name']=='publish'),None))
 return dict(workflow_id=str(row['id']),url=row.get('html_url'),trigger=row['event'],run_attempt=row.get('run_attempt',1),status=row['status'],conclusion=row.get('conclusion'),checks=checks,verified=row['event']=='schedule' and row['status']=='completed' and row.get('conclusion')=='success' and all(v=='success' for v in checks.values()))

def evaluation(db,start,at=None):
 at=at or datetime.now(timezone.utc);zone=ZoneInfo('America/New_York')
 runs=[json.loads(r[0]) for r in db.execute('select payload from platform_workflow_runs')]
 natural=[r for r in runs if r['event']=='schedule' and r.get('run_attempt',1)==1]
 obs=db.execute('select * from platform_observations order by id desc limit 1').fetchone();observation=dict(obs) if obs else None
 if observation:observation['payload']=json.loads(observation['payload'])
 days=[]
 if start:
  first=datetime.fromisoformat(start).date()
  for n in range(7):
   day=first+timedelta(days=n)
   if day>=at.astimezone(zone).date():break
   rr=[r for r in natural if datetime.fromisoformat(r['created_at'].replace('Z','+00:00')).astimezone(zone).date()==day]
   done=[r for r in rr if r['status']=='completed'];success=[r for r in done if r.get('conclusion')=='success'];checks=[verification(r) for r in rr]
   days.append(dict(date=str(day),expected_natural_workflows=4,observed_natural_workflows=len(rr),successful_natural_workflows=len(success),failed_natural_workflows=len(done)-len(success),missing_natural_workflows=max(0,4-len(rr)) if observation and observation['success'] else None,unobserved_expected_slots=max(0,4-len(rr)),pending_natural_workflows=len(rr)-len(done),history_query_success=bool(observation and observation['success']),evidence_complete=len(rr)>=4 and len(done)==len(rr) and all(v['verified'] for v in checks) and bool(observation and observation['success']),workflow_verification=checks))
 candidates=[(at+timedelta(days=d)).replace(hour=h,minute=17,second=0,microsecond=0) for d in (0,1) for h in HOURS];upcoming=min(t for t in candidates if t>at)
 verified=[verification(r) for r in natural if verification(r)['verified']]
 return dict(status='VERIFIED_NATURAL_EXECUTION' if verified else 'PENDING NATURAL SCHEDULE VERIFICATION',cron=CRON,next_schedule_utc=upcoming.isoformat(),next_schedule_local=upcoming.astimezone(zone).isoformat(),last_successful_natural=next(iter(sorted(verified,key=lambda r:int(r['workflow_id']),reverse=True)),None),latest_platform_observation=observation,workflow_runs=runs,completed_days=days,completed=len(days)==7 and all(d['evidence_complete'] for d in days))

def discoveries(db):
 """One row per event; earliest successful delivery is not erased by later expiry."""
 rows=[]
 for e in db.execute("select * from discovery_events where kind='NEW_SINCE_PREVIOUS_SUCCESSFUL_INVENTORY' order by id"):
  checks=[dict(r,result=json.loads(r['result'])) for r in db.execute('select * from discovery_deliveries where event_id=? order by checked_at',(e['id'],))]
  if not any(x['result'].get('relevant_undergraduate_or_likely') for x in checks):continue
  good=[x for x in checks if all(x['result'].get(k) for k in ('current_membership','in_full_inventory','in_recent_feed','export_valid'))]
  latest=checks[-1]['result'];jid=canonical_job(db,e['job_id']);canonical=db.execute('select first_seen_at from jobs where id=?',(jid,)).fetchone() if jid else None;known_before=bool(e['known_job_before'] or canonical and canonical[0]<e['observed_at']);classification=json.loads(e['classification']);source=db.execute('select c.name,s.ats_type from sources s join companies c on c.id=s.company_id where s.id=?',(e['source_id'],)).fetchone()
  rows.append(dict(event_id=e['id'],canonical_job_id=jid,known_before=known_before,employer=source[0],source_id=e['source_id'],provider=source[1],requisition=e['requisition'],canonical_apply_url=e['canonical_url'],observed_at=e['observed_at'],previous_successful_inventory=e['previous_success_at'],observation=classification.get('observation'),eligibility=classification.get('eligibility'),function=classification.get('function'),publication=json.loads(e['publication']),delivered_verified=bool(good),first_delivery_at=good[0]['checked_at'] if good else None,current_delivery=latest,missing_delivery_reasons=[] if good else latest.get('delivery_reasons'),latency_seconds=next((x['result']['latency_seconds'] for x in checks if x['result'].get('latency_seconds') is not None),None)))
 values=sorted(r['latency_seconds'] for r in rows if r['latency_seconds'] is not None and not r['known_before'])
 import statistics,math
 return dict(new_membership_events=db.execute("select count(*) from discovery_events where kind='NEW_SINCE_PREVIOUS_SUCCESSFUL_INVENTORY'").fetchone()[0],relevant_internships=len(rows),genuinely_new_opportunities=len({r['canonical_job_id'] for r in rows if not r['known_before']}),previously_known_opportunities=sum(r['known_before'] for r in rows),verified_delivered=sum(r['delivered_verified'] for r in rows),delivery_percentage=100*sum(r['delivered_verified'] for r in rows)/len(rows) if rows else None,latency_observations=len(values),latency_seconds=values,median_latency_seconds=statistics.median(values) if len(values)>=2 else None,p95_latency_seconds=values[max(0,math.ceil(.95*len(values))-1)] if len(values)>=20 else None,within_24_hours_percentage=100*sum(v<=86400 for v in values)/len(values) if values else None,latency_limit='Selected timestamp-qualified observations only; median requires 2 and p95 requires 20 samples. Not market recall or an SLA.',opportunities=rows)

def human_report(report):
 p=report['platform'];h=report['monitoring_health'];d=report['discovery'];e=report['evaluation']
 lines=['# Scout production health', '', 'Generated '+report['generated_at'], '', '**'+p['status']+'**', '', 'Next configured opportunity: '+p['next_schedule_local']+' (America/New_York); '+p['next_schedule_utc']+' UTC.', '', f"Healthy certified employers: {h['healthy_certified_employers']}; scheduled boards: {report['scheduled_boards']}; source states: {h['scheduled_board_states']}.", f"Due boards: {report['queue_backlog']}; requested/effective cadence misses: {report['missing_requested_cadence']} / {report['missing_effective_cadence']}.", '', f"New source-membership events: {d['new_membership_events']}; relevant internships: {d['relevant_internships']}; verified public deliveries: {d['verified_delivered']}; timestamp-qualified latency observations: {d['latency_observations']}.", '', f"Seven-day window: {e['window_start']}; completed calendar days: {len(e['completed_days'])}; evaluation complete: {e['completed']}.", '', 'Natural first-attempt workflow records are required. Manual dispatches never satisfy this requirement. Missing API evidence is not proof that no workflow ran. Scanner COMPLETED alone does not prove checkpoint or Pages success.', '', '## Daily evidence', '', '| Date | Expected natural | Observed | Successful | Failed | Missing |', '|---|---:|---:|---:|---:|---:|']
 for day in e['completed_days']:lines.append('|'+ '|'.join(str(day[k]) for k in ('date','expected_natural_workflows','observed_natural_workflows','successful_natural_workflows','failed_natural_workflows','missing_natural_workflows'))+'|')
 lines+=['','## Provider observations','','```json',json.dumps(report['provider'],indent=2),'```','','## Backlog causes','','```json',json.dumps(report['backlog_reasons'],indent=2),'```','','Data are evaluated at this report clock. Historical successful delivery does not promise a job remains open. The checkpoint report precedes its own final GitHub publication; the following run collects its completed platform record.']
 return '\n'.join(lines)+'\n'

def canonical_job(db,jid):
 seen=set()
 while jid is not None:
  if jid in seen:raise ValueError('Job merge cycle')
  seen.add(jid);row=db.execute('select canonical_job_id from job_merges where duplicate_job_id=?',(jid,)).fetchone()
  if not row:return jid
  jid=row[0]
 return None
