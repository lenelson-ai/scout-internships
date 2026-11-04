"""Official entry evidence from existing registry, primary datasets and explicit publisher websites."""
import hashlib,json,re
from collections import defaultdict
from urllib.parse import urlsplit,urljoin
from .db import canonical_id
from .entities import name_key,domain_key
from .sources import detect
from .career_mapping import get_page
from .model import now
from pathlib import Path
from .universe_sources import ROOT

NON_EMPLOYER={'linkedin.com','facebook.com','instagram.com','youtube.com','glassdoor.com','indeed.com','jobright.ai','vault.com','legacy.vault.com','firsthand.co','fortune.com','us500.com'}
ATS=re.compile(r'myworkday|myworkdaysite|greenhouse|lever\.co|ashbyhq|smartrecruiters|oraclecloud|icims|tal\.net|avature|successfactors|ultipro|dayforce|csod\.com|recsolu|adp\.com',re.I)
def official_home(value):
 if not value:return None
 value=str(value).strip()
 if '://' not in value:value='https://'+value
 p=urlsplit(value)
 if p.scheme not in ('http','https') or not p.hostname or p.username or p.password:return None
 host=p.hostname.lower().removeprefix('www.')
 if host in NON_EMPLOYER or any(host.endswith('.'+d) for d in NON_EMPLOYER) or ATS.search(host):return None
 if host in ('localhost','example.com') or host.endswith('.local'):return None
 return 'https://'+p.hostname.lower()

class DomainEvidenceIndex:
 def __init__(self,db):
  self.db=db;self.items=defaultdict(list);self.profiles=defaultdict(list);self.names=defaultdict(set)
  for r in db.execute('select * from companies'):
   cid=canonical_id(db,r['id']);self.names[cid].add(name_key(r['name']))
   self.add(cid,r['domain'],'CANONICAL_DOMAIN',dict(company_id=r['id']))
   if r['careers_url']:self.add(cid,r['careers_url'],'RECORDED_CAREERS',dict(company_id=r['id']),home=False)
  for r in db.execute('select * from company_aliases'):self.names[canonical_id(db,r['company_id'])].add(name_key(r['alias']))
  aliases={}
  for cid,names in self.names.items():
   for n in names:aliases.setdefault(n,set()).add(cid)
  for r in db.execute('select * from company_domains'):self.add(canonical_id(db,r['company_id']),r['domain'],'VERIFIED_DOMAIN_RECORD',dict(evidence=r['evidence']))
  for r in db.execute('select * from raw_organizations where active=1'):
   cid=canonical_id(db,r['company_id']) if r['company_id'] else None
   if not cid:
    owners=aliases.get(name_key(r['name']),set())
    if len(owners)!=1:continue
    cid=next(iter(owners))
   origin=dict(raw_id=r['id'],dataset=r['dataset_id'],name=r['name']);self.add(cid,r['domain'],'PUBLISHER_OR_REGULATORY_DOMAIN',origin)
   meta=json.loads(r['metadata'])
   for key in ('WEBADDR','website','company_website','official_website','homepage','Website'):
    if meta.get(key):self.add(cid,meta[key],'EXPLICIT_WEBSITE_FIELD',dict(origin,field=key))
   if meta.get('profile_url'):self.profiles[cid].append(dict(url=meta['profile_url'],**origin))
  for r in db.execute('select * from recruiting_evidence'):
   u=r['source_url']
   if u and official_home(u):self.add(canonical_id(db,r['company_id']),u,'RECRUITING_EVIDENCE',dict(evidence_id=r['id'],kind=r['evidence_type']),home=r['evidence_type']!='OFFICIAL_UNDERGRAD_PROGRAM')
  evidence_file=Path(ROOT)/'config/official_entry_evidence.json'
  if evidence_file.exists():
   for e in json.loads(evidence_file.read_text()):
    if not db.execute('select 1 from companies where id=?',(e['company_id'],)).fetchone():continue
    cid=canonical_id(db,e['company_id'])
    if name_key(e['employer']) not in self.names[cid]:raise ValueError('Official entry evidence employer identity mismatch')
    if not official_home(e['url']) or domain_key(e['url'])!=domain_key(e['source_url']):raise ValueError('Official entry requires same-employer primary-page provenance')
    self.add(cid,e['url'],'PRIMARY_CAREERS_RESEARCH',e,home=False)
    self.add(cid,e['url'],'PRIMARY_DOMAIN_RESEARCH',e)
  provider_file=Path(ROOT)/'config/provider_company_websites.json'
  if provider_file.exists():
   for e in json.loads(provider_file.read_text()):
    if not db.execute('select 1 from companies where id=?',(e['company_id'],)).fetchone():continue
    cid=canonical_id(db,e['company_id'])
    if name_key(e['published_hiring_organization']) not in self.names[cid]:raise ValueError('Provider website metadata does not match canonical recruiting employer')
    self.add(cid,e['website'],'PROVIDER_PUBLISHED_COMPANY_WEBSITE',e)
  for r in db.execute('select * from career_mapping_attempts'):
   try:meta=json.loads(r['evidence'] or '{}')
   except (ValueError,TypeError):continue
   if isinstance(meta,dict) and meta.get('domain'):self.add(canonical_id(db,r['company_id']),meta['domain'],'PREVIOUS_OFFICIAL_MAPPING',dict(mapping_id=r['id']))
 def add(self,cid,value,kind,evidence,home=True):
  if not value:return
  url=official_home(value) if home else str(value)
  if url and url.startswith(('https://','http://')):self.items[cid].append(dict(url=url,kind=kind,evidence=evidence))
 def entries(self,cid):
  priority={'PROVIDER_PUBLISHED_COMPANY_WEBSITE':1,'PRIMARY_DOMAIN_RESEARCH':0,'PRIMARY_CAREERS_RESEARCH':1,'VERIFIED_DOMAIN_RECORD':0,'PUBLISHER_OR_REGULATORY_DOMAIN':1,'EXPLICIT_WEBSITE_FIELD':1,'PREVIOUS_OFFICIAL_MAPPING':1,'CANONICAL_DOMAIN':2,'RECORDED_CAREERS':3,'RECRUITING_EVIDENCE':4};seen=set();result=[]
  for r in sorted(self.items[cid],key=lambda r:(priority[r['kind']],r['url'])):
   key=r['url'].rstrip('/').lower()
   if key not in seen:seen.add(key);result.append(r)
  return result
 def signature(self,cid):return hashlib.sha256(json.dumps(dict(entries=self.entries(cid),profiles=self.profiles[cid]),sort_keys=True).encode()).hexdigest()
 def enrich_profiles(self,cid,get=get_page,max_profiles=2):
  evidence=[];entries=[]
  for p in self.profiles[cid][:max_profiles]:
   try:
    final,html,links,sha=get(p['url'],timeout=8);found=[]
    for href,label,attrs in links:
     if re.search(r'visit (?:the )?website|company website|^website$|official website',label+' '+str(attrs.get('class','')),re.I):
      u=official_home(urljoin(final,href))
      if u:found.append(u)
    unique=sorted(set(found))
    evidence.append(dict(profile=p['url'],sha256=sha,websites=unique,status='EXPLICIT_WEBSITE' if len(unique)==1 else 'NO_UNAMBIGUOUS_WEBSITE_FIELD'))
    if len(unique)==1:entries.append(dict(url=unique[0],kind='PUBLISHER_WEBSITE_LINK',evidence=dict(**p,profile_sha256=sha)))
   except Exception as e:evidence.append(dict(profile=p['url'],error=str(e),status='PROFILE_ACCESS_FAILURE'))
  return entries,evidence
