"""Small neutral internship feed backed by successful source inventory observations."""
from datetime import datetime,timedelta,timezone
from zoneinfo import ZoneInfo
import json,csv
from pathlib import Path

def feed(db,window='today',at=None,include_other=False):
 if window not in ('today','7d','inventory'):raise ValueError('Feed window must be today or 7d')
 at=at or datetime.now(timezone.utc)
 boundary=at.astimezone(ZoneInfo('America/New_York')).replace(hour=0,minute=0,second=0,microsecond=0).astimezone(timezone.utc) if window=='today' else at-timedelta(days=7)
 cutoff=boundary.isoformat();end=at.isoformat();items={}
 for r in db.execute('''SELECT j.*,c.name company,s.id source_id,s.ats_type provider,s.careers_url source,i.external_job_id inventory_external_id,i.discovery_kind,i.first_seen_at inventory_first_seen,i.last_seen_at inventory_last_seen FROM jobs j JOIN companies c ON c.id=j.company_id JOIN source_inventory i ON i.job_id=j.id JOIN sources s ON s.id=i.source_id WHERE j.active=1 AND i.present=1 AND s.active=1 AND s.source_role='AUTHORITATIVE' AND s.health_status LIKE 'SUCCESS%' AND j.internship_confidence>=0.8 AND (j.function IN ('STRATEGY','FINANCE','CONSULTING') OR (? AND j.function='OTHER')) AND j.geography_status='US' AND j.id NOT IN (SELECT duplicate_job_id FROM job_merges) ORDER BY s.last_successful_check_at DESC''',(int(include_other),)):
  from .eligibility import eligibility
  from .model import Job
  if eligibility(Job(title=r['title'],description=r['description']))['status'] in ('VERIFIED_GRADUATE_ONLY','RESTRICTED'):continue
  if r['inventory_last_seen']<(at-timedelta(hours=24)).isoformat():continue
  fresh=r['discovery_kind'] in ('INITIAL_DISCOVERY','NEW_SINCE_PREVIOUS_SUCCESSFUL_INVENTORY') and cutoff<=r['first_seen_at']<=end
  recent=bool(r['posted_at'] and cutoff<=r['posted_at']<=end)
  if window!='inventory' and not (fresh if window=='today' else fresh or recent):continue
  if r['id'] in items:continue
  title=r['title'].lower();kind='CO_OP' if any(k in title for k in ('co-op','co op','coop')) else 'INTERNSHIP'
  items[r['id']]=dict(scout_job_id=r['id'],company=r['company'],title=r['title'],location=json.loads(r['locations']),category=r['function'],internship_type=kind,posted_at=r['posted_at'],first_seen_at=r['first_seen_at'],last_seen_at=r['last_seen_at'],canonical_apply_url=r['canonical_apply_url'],source=r['source'],source_id=r['source_id'],provider=r['provider'],external_id=r['inventory_external_id'],active=bool(r['active']),discovery_kind=r['discovery_kind'],new_to_scout=fresh,new_since_previous_successful_inventory=fresh and r['discovery_kind']=='NEW_SINCE_PREVIOUS_SUCCESSFUL_INVENTORY',employer_posted_recently=recent,needs_review=bool(r['needs_review']))
 return dict(window=window,boundary=cutoff,generated_at=end,definition='Today means first discovered by Scout today, including explicitly labeled initial censuses; it does not mean employer-posted today. Last 7 days includes recent Scout discoveries or genuine employer posting dates.',initial_discovery_count=sum(r['new_to_scout'] and r['discovery_kind']=='INITIAL_DISCOVERY' for r in items.values()),new_since_previous_inventory_count=sum(r['new_since_previous_successful_inventory'] for r in items.values()),total=len(items),jobs=sorted(items.values(),key=lambda r:r['first_seen_at'],reverse=True))

def export(db,path,window='today'):
 report=feed(db,window);path=Path(path);path.parent.mkdir(parents=True,exist_ok=True)
 if path.suffix=='.csv':
  fields=['scout_job_id','company','title','location','category','internship_type','posted_at','first_seen_at','last_seen_at','canonical_apply_url','source','source_id','provider','external_id','active','discovery_kind','new_to_scout','new_since_previous_successful_inventory','employer_posted_recently','needs_review']
  with path.open('w',newline='') as f:
   w=csv.DictWriter(f,fieldnames=fields);w.writeheader()
   for r in report['jobs']:w.writerow(dict(r,location='; '.join(r['location'])))
 else:path.write_text(json.dumps(report,indent=2))
 return report


def explain(db,jid,at=None):
 at=at or datetime.now(timezone.utc);j=db.execute('select * from jobs where id=?',(jid,)).fetchone()
 if not j:return ['JOB_NOT_INGESTED']
 reasons=[]
 if not j['active']:reasons.append('JOB_INACTIVE')
 if j['internship_confidence']<.8:reasons.append('INTERNSHIP_UNCERTAIN')
 if j['geography_status']!='US':reasons.append('US_ELIGIBILITY_UNVERIFIED')
 from .eligibility import eligibility
 from .model import Job
 state=eligibility(Job(title=j['title'],description=j['description']))
 if state['status'] in ('VERIFIED_GRADUATE_ONLY','RESTRICTED'):reasons.append(state['status'])
 inv=db.execute("select i.*,s.source_role,s.health_status from source_inventory i join sources s on s.id=i.source_id where i.job_id=? and i.present=1",(jid,)).fetchall()
 if not inv:reasons.append('NO_CURRENT_INVENTORY_PROVENANCE')
 elif not any(r['source_role']=='AUTHORITATIVE' and r['health_status'].startswith('SUCCESS') for r in inv):reasons.append('SOURCE_NOT_TRUSTED_OR_HEALTHY')
 elif not any(r['last_seen_at']>=(at-timedelta(hours=24)).isoformat() for r in inv):reasons.append('STALE_INVENTORY')
 if not any(r['scout_job_id']==jid for r in feed(db,'7d',at,True)['jobs']):
  if not reasons:reasons.append('OUTSIDE_RECENT_DISCOVERY_AND_POSTING_WINDOWS')
 return reasons or ['IN_RECENT_FEED']
