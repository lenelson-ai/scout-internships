"""Allowlisted public feed; publication is atomic and never exports tracker payloads."""
import json,re,os
from pathlib import Path
from datetime import datetime,timezone
from urllib.parse import urlsplit
from .feed import feed
from .model import now

def season(title):
 m=re.search(r'\b(Summer|Fall|Spring|Winter)\s+(20\d{2})\b',title,re.I)
 return m.group(0).title() if m else None

def business_candidate(j):
 title=j['title'].lower()
 business=bool(re.search(r'quantitative (?:trading|research)|quant research|trader|financ|account|bank|invest|wealth|asset|portfolio|strateg|consult|business|operations|marketing|sales|procure|supply chain|human resources?|corporate|risk|audit|controller|commercial|product manag|offering manag|it leaders|project manag',title))
 if re.search(r'\bmba\b|\bphd\b|\bph[.]d|graduate leadership|masters|master.s',title):return False
 if re.search(r'teller.*co.op city',title):return False
 if not business and re.search(r'engineer|clinical|oncology|neuroscience|bioscience|biopharma|cytometry|microbiology|research|science|technology|cyber|software|programming|medical|cell therapy|patient|pharmacy|r&d',title):return False
 return j['category']!='OTHER' or business or bool(re.fullmatch(r'(20\d{2} )?(?:(summer )?(corporate )?intern(ship)?( program)?( - amers)?|summer analyst(?: intern)?(?: - .*)?|summer associate(?: - .*)?|undergraduate associate|student associate)',title))

def build(db,output):
 path=Path(output);previous=json.loads(path.read_text()) if path.exists() else {}
 data=feed(db,'7d',include_other=True);jobs=[j for j in data['jobs'] if business_candidate(j)];states={r['id']:dict(r) for r in db.execute('select * from sources')}
 # Keep prior public rows from failed/stale sources, visibly marked unverified.
 ids={j['scout_job_id'] for j in jobs}
 for j in previous.get('jobs',[]):
  s=states.get(j['source_id'])
  if s and (not s['health_status'].startswith('SUCCESS') or not s['last_successful_check_at'] or (datetime.now(timezone.utc)-datetime.fromisoformat(s['last_successful_check_at'])).total_seconds()>86400) and j['scout_job_id'] not in ids:
   jobs.append(j);ids.add(j['scout_job_id'])
 for j in jobs:
  u=urlsplit(j['canonical_apply_url'] or '')
  if u.scheme!='https' or not u.hostname or u.username or u.password:raise ValueError('Unsafe application URL')
  j['season']=season(j['title']);s=states[j['source_id']]
  j['source_current']=s['health_status'].startswith('SUCCESS') and bool(s['last_successful_check_at']) and (datetime.now(timezone.utc)-datetime.fromisoformat(s['last_successful_check_at'])).total_seconds()<86400
 jobs.sort(key=lambda j:(j['discovery_kind']=='NEW_SINCE_PREVIOUS_SUCCESSFUL_INVENTORY',j['first_seen_at']),reverse=True)
 tier=[r for r in db.execute('select * from companies where coverage_tier=1 and active=1 and canonical_company_id is null')]
 healthy=0
 for c in tier:
  review=db.execute('select * from recruiting_scope_reviews where company_id=?',(c['id'],)).fetchone()
  if not review or review['state']!='VALIDATED':continue
  req=json.loads(review['required_source_ids']);healthy+=int(bool(req) and all(i in states and states[i]['health_status'].startswith('SUCCESS') and states[i]['last_successful_check_at'] and (datetime.now(timezone.utc)-datetime.fromisoformat(states[i]['last_successful_check_at'])).total_seconds()<86400 for i in req))
 result=dict(schema_version=1,generated_at=now(),last_successful_inventory=max((s['last_successful_check_at'] for s in states.values() if s['last_successful_check_at']),default=None),priority_total=len(tier),priority_monitored=healthy,sources_unverified=sum(not j['source_current'] for j in jobs),timezone='America/New_York',jobs=jobs)
 # A total scanner outage must not replace the last valid shared snapshot.
 if previous and healthy==0:raise ValueError('No fresh certified employers; preserve last known good feed')
 inventory=dict(schema_version=1,generated_at=now(),definition='All current business internship inventory; original discovery and publication dates retained.',jobs=[j for j in feed(db,'inventory',include_other=True)['jobs'] if business_candidate(j)])
 path.parent.mkdir(parents=True,exist_ok=True)
 invpath=path.with_name('inventory.json');invtemp=invpath.with_suffix('.tmp');invtemp.write_text(json.dumps(inventory,separators=(',',':')));os.replace(invtemp,invpath)
 temp=path.with_suffix('.tmp');temp.write_text(json.dumps(result,separators=(',',':')));os.replace(temp,path)
 return result
