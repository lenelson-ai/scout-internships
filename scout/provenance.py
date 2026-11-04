"""Audit captured internships independently of current feed trust; never infer open status."""
import json
from datetime import datetime,timezone,timedelta
from .model import Job
from .eligibility import eligibility
from .public_site import business_candidate

def audit(db,at=None):
 at=at or datetime.now(timezone.utc);cutoff=(at-timedelta(hours=24)).isoformat();rows=[]
 by_job={}
 for r in db.execute('select i.job_id,i.source_id,i.present,i.last_seen_at,s.source_role,s.health_status,s.active from source_inventory i join sources s on s.id=i.source_id where i.job_id is not null'):by_job.setdefault(r['job_id'],[]).append({k:r[k] for k in r.keys() if k!='job_id'})
 for j in db.execute("select j.*,c.name employer from jobs j join companies c on c.id=j.company_id where j.active=1 and j.internship_confidence>=.8 and j.geography_status='US' and j.id not in (select duplicate_job_id from job_merges)"):
  if not business_candidate(dict(title=j['title'],category=j['function'])):continue
  degree=eligibility(Job(title=j['title'],description=j['description']))['status']
  if degree in ('VERIFIED_GRADUATE_ONLY','RESTRICTED'):continue
  memberships=by_job.get(j['id'],[])
  trusted=[r for r in memberships if r['present'] and r['active'] and r['source_role']=='AUTHORITATIVE' and r['health_status'].startswith('SUCCESS') and r['last_seen_at']>=cutoff]
  reason='TRUSTED_CURRENT_INVENTORY' if trusted else 'NO_INVENTORY_MEMBERSHIP' if not memberships else 'ABSENT_FROM_LATEST_INVENTORY' if not any(r['present'] for r in memberships) else 'SOURCE_ROLE_UNVALIDATED' if not any(r['source_role']=='AUTHORITATIVE' for r in memberships if r['present']) else 'SOURCE_UNHEALTHY' if not any(r['health_status'].startswith('SUCCESS') for r in memberships if r['present'] and r['active'] and r['source_role']=='AUTHORITATIVE') else 'INVENTORY_STALE'
  rows.append(dict(job_id=j['id'],company_id=j['company_id'],employer=j['employer'],title=j['title'],requisition=j['external_job_id'],apply_url=j['canonical_apply_url'],first_seen_at=j['first_seen_at'],eligibility=degree,reason=reason,memberships=memberships,open_status='Confirmed only by current trusted inventory' if trusted else 'Unknown; active database flag is not independent proof of current openness'))
 from collections import Counter
 return dict(generated_at=at.isoformat(),captured_target_internships=len(rows),trusted_current=sum(r['reason']=='TRUSTED_CURRENT_INVENTORY' for r in rows),reasons=dict(Counter(r['reason'] for r in rows)),rows=rows)
