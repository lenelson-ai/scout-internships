"""Unverified external observations are separate from employer facts and Scout jobs."""
import re,json,hashlib,math
from collections import Counter,defaultdict
from datetime import datetime,timezone,timedelta
from pathlib import Path
from .entities import resolve
from .db import canonical_id
from .model import now
STOP=set('intern internship internships summer winter fall spring program programme analyst associate student graduate graduates class of the and in us 2026 2027 2028'.split())
def tokens(s):return {w for w in re.findall(r'[a-z0-9]+',s.lower()) if w not in STOP and len(w)>1}
def normalize(text):
 observations=[];industry='UNKNOWN'
 for line in text.splitlines():
  if line.startswith('**') and not line.startswith('**IMPORTANT'):industry=line.strip('*')
  m=re.match(r'^(\d+)\. (.+?) — (.+?) — (.+)$',line)
  if not m:continue
  n=int(m[1]);company,title,tail=m.group(2,3,4);parts=tail.split(' — ');opening=None
  try:opening=datetime.strptime(parts[0],'%B %d, %Y').date().isoformat()
  except ValueError:pass
  close=next((x.split(': ',1)[1] for x in parts if x.startswith('Listed close:')),None)
  if close:close=datetime.strptime(close,'%B %d, %Y').date().isoformat()
  location=next((x for x in parts[1:] if x in ('New York','Boston')),None)
  if 'Colombia Remote' in title:location='Colombia Remote'
  year=re.findall(r'20\d{2}',title);grad=bool(re.search('Graduates',title,re.I))
  key=re.sub(r'[^a-z0-9]+',' ',company.lower()+' '+title.lower()).strip()
  observations.append(dict(observation_id=f'external-{n:03}',ordinal=n,source='Original tracker' if n<=99 else 'Jobright batch 1' if n<=107 else 'Jobright batch 2',original_text=line,company=company,program=title,recruiting_years=[] if grad else sorted(set(year)),graduation_years=year if grad else [],listed_opening_date=opening,listed_closing_date=close,listed_relative_age=None if opening else parts[0],location=location,external_status='OFFERS_OUT_REPORTED' if 'Offers Out' in tail else 'UNVERIFIED',industry=industry if n<=99 else 'Business / technology',opportunity_key=hashlib.sha256(key.encode()).hexdigest()[:16],date_authority='EXTERNAL_REPORT_NOT_SCOUT_TIMESTAMP'))
 seen=set()
 for r in observations:
  key=r['opportunity_key']
  if key in seen and 'Duplicate tracker entry' not in r['original_text']:
   r['possible_duplicate_group']=key
   r['opportunity_key']=hashlib.sha256((key+r['observation_id']).encode()).hexdigest()[:16]
  seen.add(key)
 return observations

def match_score(b,p):
 title=p.get('title','');bt=tokens(b['program']);pt=tokens(title);by=set(b['recruiting_years']);py=set(re.findall(r'20\d{2}',title))
 if by and py and not by&py:return 0
 if b['location'] and p.get('locations') and b['location'].lower() not in ' '.join(p['locations']).lower():return 0
 if not re.search(r'intern|summer (analyst|associate)|skillbridge|fellow',title,re.I):return 0
 if not bt:return .4 if by&py else .2
 overlap=bt&pt;coverage=len(overlap)/len(bt)
 return coverage*(.9 if by&py else .8) if overlap else 0

def compare(db,observations,verifications=None,entity_map=None):
 verifications=verifications or {};entity_map=entity_map or {};public=Path('site/data/feed.json');feed=json.loads(public.read_text()) if public.exists() else dict(jobs=[]);feedids={j['scout_job_id'] for j in feed['jobs']}
 groups=defaultdict(list)
 for b in observations:groups[b['opportunity_key']].append(b)
 results=[]
 for key,obs in groups.items():
  b=obs[0];cid,near,basis=resolve(db,b['company']);mapping=entity_map.get(b['company'])
  if mapping:cid=mapping['company_id'];basis=mapping['reason'];near=[]
  employer=dict(db.execute('select * from companies where id=?',(cid,)).fetchone()) if cid else None
  ss=[dict(s) for s in db.execute('select * from sources where active=1') if cid and canonical_id(db,s['company_id'])==cid]
  auth=[s for s in ss if s['source_role']=='AUTHORITATIVE'];candidates=[]
  if cid:
   for r in db.execute('select * from source_inventory where company_id=?',(cid,)):
    p=json.loads(r['normalized_payload']);score=match_score(b,p)
    if score:candidates.append(dict(score=score,source_id=r['source_id'],identity=r['identity'],external_id=r['external_job_id'],job_id=r['job_id'],title=p.get('title'),locations=p.get('locations'),url=p.get('canonical_apply_url') or r['canonical_url'],present=bool(r['present']),first_seen_at=r['first_seen_at'],last_seen_at=r['last_seen_at'],classification=json.loads(r['classification']),posted_at=p.get('posted_at')))
   known={c['job_id'] for c in candidates if c['job_id']}
   for r in db.execute('select * from jobs where company_id=? and id not in(select duplicate_job_id from job_merges)',(cid,)):
    if r['id'] in known:continue
    p=dict(r);p['locations']=json.loads(r['locations']);score=match_score(b,p)
    if score:candidates.append(dict(score=score,job_id=r['id'],title=r['title'],locations=p['locations'],url=r['canonical_apply_url'],present=bool(r['active']),first_seen_at=r['first_seen_at'],last_seen_at=r['last_seen_at'],external_id=r['external_job_id'],classification=dict(function=r['function'],internship_confidence=r['internship_confidence']),posted_at=r['posted_at']))
  candidates.sort(key=lambda c:(c['score'],c['present'],c['last_seen_at']),reverse=True);top=candidates[0] if candidates else None
  strong=bool(top and top['score']>=.79 and len(tokens(b['program'])&tokens(top['title']))>=2)
  v=dict(verifications.get(key,dict(status='NOT_INDEPENDENTLY_VERIFIED',eligible=None,evidence=[])))
  if v['status']=='VERIFIED_OPEN' and (not v.get('verified_at') or datetime.fromisoformat(v['verified_at'])<datetime.now(timezone.utc)-timedelta(hours=24)):
   v.update(previous_status='VERIFIED_OPEN',status='VERIFICATION_STALE',eligible=None)
  confirmed=v.get('matched_scout_job_id') or (top.get('job_id') if strong else None)
  merged_ids=set()
  while confirmed:
   m=db.execute('select canonical_job_id from job_merges where duplicate_job_id=?',(confirmed,)).fetchone()
   if not m:break
   if confirmed in merged_ids:raise ValueError('Cyclic job merge history')
   merged_ids.add(confirmed);confirmed=m[0]
  if v.get('matched_scout_job_id'):
   top=next((c for c in candidates if c.get('job_id')==confirmed),top)
  if not cid:gap='INSUFFICIENT_EVIDENCE' if near else 'EMPLOYER_MISSING'
  elif not auth:gap='SOURCE_NOT_CONFIGURED'
  elif not any(s['ats_type'] in __import__('scout.sources',fromlist=['ADAPTERS']).ADAPTERS for s in auth):gap='UNSUPPORTED_PROVIDER'
  elif any(not s['health_status'].startswith('SUCCESS') for s in auth):gap='INCOMPLETE_INVENTORY' if any('PARSER_ERROR'==s['health_status'] for s in auth) else 'SOURCE_CRAWL_FAILURE'
  elif not confirmed:gap='JOB_INGESTION_FAILURE' if v['status']=='VERIFIED_OPEN' else 'INSUFFICIENT_EVIDENCE'
  elif top and top['classification'].get('internship_confidence',0)<.4:gap='CLASSIFICATION_MISS'
  else:gap=None
  if v['status']=='VERIFIED_CLOSED':gap='CLOSED_OR_STALE_LISTING'
  verified=v['status']=='VERIFIED_OPEN' and v.get('eligible') is True
  missed=verified and not confirmed
  reason=(gap or 'JOB_INGESTION_FAILURE') if missed else None
  flags=[]
  if b['listed_closing_date'] and b['listed_closing_date']<datetime.now(timezone.utc).date().isoformat():flags.append('EXTERNAL_DEADLINE_EXPIRED_NOT_EMPLOYER_VERIFIED')
  if b['external_status']!='UNVERIFIED':flags.append(b['external_status'])
  if any(int(y)>datetime.now(timezone.utc).year for y in b['recruiting_years']):flags.append('FUTURE_PROGRAM_YEAR_NOT_PROOF_CLOSED')
  if not v.get('eligible') and v['status']=='VERIFIED_OPEN':flags.append('ELIGIBILITY_UNVERIFIED_OR_OUT_OF_SCOPE')
  results.append(dict(opportunity_key=key,observations=[o['observation_id'] for o in obs],external_sources=sorted({o['source'] for o in obs}),company=b['company'],program=b['program'],industry=b['industry'],company_id=cid,canonical_employer=employer['name'] if employer else None,tier=employer['coverage_tier'] if employer else None,entity_resolution_basis=basis,entity_candidates=near,authoritative_sources=[dict(source_id=s['id'],provider=s['ats_type'],url=s['careers_url'],status=s['health_status'],inventory_complete=s['health_status'].startswith('SUCCESS'),inventory_fresh=bool(s['last_successful_check_at'] and datetime.fromisoformat(s['last_successful_check_at'])>=datetime.now(timezone.utc)-timedelta(hours=24)),supported=s['ats_type'] in __import__('scout.sources',fromlist=['ADAPTERS']).ADAPTERS,last_success=s['last_successful_check_at']) for s in auth],other_source_candidates=[dict(source_id=s['id'],provider=s['ats_type'],role=s['source_role'],url=s['careers_url'],status=s['health_status']) for s in ss if s not in auth],candidate_matches=candidates[:5],matching_confidence='HIGH_TITLE_SEASON_EVIDENCE' if strong else 'TENTATIVE_PROGRAM_ONLY' if top else 'NO_CANDIDATE',scout_job_id=confirmed,observed_in_inventory=bool(top),confirmed_job_capture=bool(confirmed),internship_detected=bool(top and top['classification'].get('internship_confidence',0)>=.4),function=top['classification'].get('function') if top else None,in_public_export=bool(confirmed in feedids),feed_window_eligible=bool(top and any(d and datetime.fromisoformat(d)>=datetime.now(timezone.utc)-timedelta(days=7) for d in (top.get('first_seen_at'),top.get('posted_at')))),scout_first_seen_at=top.get('first_seen_at') if top else None,employer_posted_at=top.get('posted_at') if top else None,match_active=top.get('present') if top else None,verification=v,potential_gap=gap,verified_open_eligible=verified,verified_miss=missed,miss_reason=reason,flags=flags,recommended_action='Verify exact employer-controlled position, location and eligibility; candidate matches are not proof of an exact posting.' if v['status']=='NOT_INDEPENDENTLY_VERIFIED' else v.get('next_action','Maintain complete source inventories; feed absence alone is not a miss.')))
 return results
