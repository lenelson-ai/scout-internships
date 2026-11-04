"""Durable run/discovery metrics and honest completed-day reliability windows."""
import os,json,uuid,statistics,math
from datetime import datetime,timezone,timedelta
from zoneinfo import ZoneInfo
from collections import Counter
from .model import now

def begin(db):
 rid=uuid.uuid4().hex;hosted=os.environ.get('GITHUB_ACTIONS')=='true';context=dict(id=rid,environment='HOSTED' if hosted else 'LOCAL',trigger=os.environ.get('GITHUB_EVENT_NAME','local_cli'),workflow_run_id=os.environ.get('GITHUB_RUN_ID'),run_attempt=os.environ.get('GITHUB_RUN_ATTEMPT'),repository=os.environ.get('GITHUB_REPOSITORY'),started_at=now())
 with db:
  db.execute("insert into monitoring_runs values(?,?,?,?,?,?,?,NULL,'RUNNING','{}')",tuple(context[k] for k in ('id','environment','trigger','workflow_run_id','run_attempt','repository','started_at')))
  db.execute("insert or ignore into reliability_evaluations(id,initialized_at,timezone) values('hosted-seven-day-v1',?,'America/New_York')",(now(),))
  if hosted:
   row=db.execute("select first_hosted_at from reliability_evaluations where id='hosted-seven-day-v1'").fetchone()
   if not row[0]:
    start=(datetime.now(ZoneInfo('America/New_York'))+timedelta(days=1)).replace(hour=0,minute=0,second=0,microsecond=0).isoformat()
    db.execute("update reliability_evaluations set first_hosted_at=?,window_start=? where id='hosted-seven-day-v1'",(now(),start))
 return context

def history(db):
 return {r['id']:r['first_seen_at'] for r in db.execute('select id,first_seen_at from jobs')}

def deliveries(db,rid,export_ok):
 from .feed import feed,explain
 from .public_site import business_candidate
 full={j['scout_job_id'] for j in feed(db,'inventory',include_other=True)['jobs'] if business_candidate(j)};recent={j['scout_job_id'] for j in feed(db,'7d',include_other=True)['jobs'] if business_candidate(j)}
 counts=Counter()
 # Every new-since event is checked once per observation run, including a prior
 # event whose first export failed; updated/reappeared events remain distinct.
 for e in db.execute("select * from discovery_events where kind='NEW_SINCE_PREVIOUS_SUCCESSFUL_INVENTORY'").fetchall():
  jid=e['job_id'];d=json.loads(e['classification']);degree=d.get('eligibility',{}).get('status');target=d.get('internship_confidence',0)>=.8 and d.get('geography_status')=='US' and degree not in ('VERIFIED_GRADUATE_ONLY','RESTRICTED')
  if jid:
   j=db.execute('select title,function category from jobs where id=?',(jid,)).fetchone();target=bool(target and j and business_candidate(j))
  else:target=False
  member=db.execute('select present,last_seen_at from source_inventory where source_id=? and identity=?',(e['source_id'],e['identity'])).fetchone();proof=json.loads(e['publication']);latency=None;latency_reason='UNKNOWN_OR_DATE_ONLY_PUBLICATION'
  if proof.get('precision')=='TIMESTAMP' and proof.get('timezone_verified') and proof.get('normalized') and not e['known_job_before']:
   delta=(datetime.fromisoformat(e['observed_at'])-datetime.fromisoformat(proof['normalized'])).total_seconds()
   if delta>=0:latency=delta;latency_reason='VERIFIED_TIMESTAMP_AND_TIMEZONE'
   else:latency_reason='PUBLICATION_AFTER_OBSERVATION_CONFLICT'
  result=dict(relevant_undergraduate_or_likely=target,eligibility=degree,current_membership=bool(member and member[0]),last_verified_at=member[1] if member else None,in_full_inventory=jid in full,in_recent_feed=jid in recent,export_valid=export_ok,delivery_reasons=explain(db,jid) if jid else ['NOT_RETAINED_AS_TARGET_INTERNSHIP'],publication=proof,latency_seconds=latency,latency_evidence=latency_reason,known_job_before=bool(e['known_job_before']))
  with db:db.execute('insert or replace into discovery_deliveries values(?,?,?,?)',(e['id'],rid,now(),json.dumps(result)))
  if target:counts['relevant_events']+=1;counts['full_inventory_delivered']+=jid in full;counts['recent_feed_delivered']+=jid in recent;counts['verified_latency_observations']+=latency is not None
 return dict(counts)

def report(db):
 from .scan_queue import eligible_sources,initialize
 initialize(db)
 ids=eligible_sources(db);at=datetime.now(timezone.utc);has_policy=db.execute("select 1 from sqlite_master where name='source_scan_policy'").fetchone();query='select q.*,s.interval_seconds,s.last_successful_check_at,'+('p.requested_interval_seconds' if has_policy else 'NULL requested_interval_seconds')+' from scan_queue q join sources s on s.id=q.source_id'+(' left join source_scan_policy p on p.source_id=q.source_id' if has_policy else '');queue=[dict(r) for r in db.execute(query) if r['source_id'] in ids];due=[r for r in queue if r['due_at']<=at.isoformat()]
 def missed(key):return sum(bool(r['last_successful_check_at'] and r[key] and (at-datetime.fromisoformat(r['last_successful_check_at'])).total_seconds()>r[key]) for r in queue)
 runs=[dict(r,metrics=json.loads(r['metrics'])) for r in db.execute('select * from monitoring_runs order by started_at')];attempts=[dict(r,metrics=json.loads(r['metrics'])) for r in db.execute('select a.*,s.ats_type provider from scan_attempts a join sources s on s.id=a.source_id')];known_batches={r['metrics'].get('batch_id') for r in runs};attempts=[r for r in attempts if r['batch_id'] in known_batches]
 def summary(rr):
  values=sorted(r['metrics'].get('total_seconds',0) for r in rr);return dict(attempts=len(rr),complete=sum(r['status'].startswith('SUCCESS') for r in rr),statuses=dict(Counter(r['status'] for r in rr)),mean_seconds=statistics.mean(values) if values else None,p95_seconds=values[max(0,math.ceil(.95*len(values))-1)] if values else None,retries=sum(bool(r['metrics'].get('retry_attempt')) for r in rr),retry_seconds=sum(r['metrics'].get('total_seconds',0) for r in rr if r['metrics'].get('retry_attempt')))
 evaluation=dict(db.execute("select * from reliability_evaluations where id='hosted-seven-day-v1'").fetchone());days=[]
 if evaluation['window_start']:
  first=datetime.fromisoformat(evaluation['window_start']);today=at.astimezone(ZoneInfo(evaluation['timezone'])).date()
  for n in range(7):
   day=(first+timedelta(days=n)).date()
   if day>=today:break
   rr=[r for r in runs if r['environment']=='HOSTED' and datetime.fromisoformat(r['started_at']).astimezone(ZoneInfo(evaluation['timezone'])).date()==day]
   days.append(dict(date=str(day),hosted_runs=len(rr),natural_scheduled_runs=sum(r['trigger']=='schedule' for r in rr),completed=sum(r['status']=='COMPLETED' for r in rr),failed=sum(r['status']=='ERROR' for r in rr),planned_workflow_opportunities=4,evidence_complete=len(rr)>=4 and any(r['trigger']=='schedule' for r in rr),run_ids=[r['workflow_run_id'] for r in rr]))
 evaluation.update(completed_days=days,completed=len(days)==7 and all(r['evidence_complete'] for r in days),status='INITIALIZED' if not days else 'ACCUMULATING_OR_INCOMPLETE_EVIDENCE')
 if evaluation['completed']:evaluation['status']='COMPLETED_OBSERVATION_WINDOW_NOT_A_RELIABILITY_GUARANTEE'
 return dict(generated_at=now(),scheduled_boards=len(ids),queue_backlog=len(due),oldest_overdue_seconds=max([(at-datetime.fromisoformat(r['due_at'])).total_seconds() for r in due]or[0]),missing_requested_cadence=missed('requested_interval_seconds'),missing_effective_cadence=missed('interval_seconds'),provider={p:summary([r for r in attempts if r['provider']==p]) for p in sorted({r['provider'] for r in attempts})},environments={env:dict(runs=len([r for r in runs if r['environment']==env]),attempts=summary([a for a in attempts if a['batch_id'] in {r['metrics'].get('batch_id') for r in runs if r['environment']==env}])) for env in ('LOCAL','HOSTED')},discovery_kinds=dict(Counter(r[0] for r in db.execute('select kind from discovery_events'))),evaluation=evaluation,runs=runs)
