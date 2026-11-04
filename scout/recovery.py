"""Bounded official recruiting-surface recovery with inspectable certification evidence."""
import json,re
from urllib.parse import urljoin,urlsplit
from .career_mapping import get_page,find_careers,discover_public_sources
from .recruiting_discovery import board_identity
from .entities import domain_key
from .model import canonical_url
from .sources import ADAPTERS
from .sources.base import SourceError
from .db import canonical_id,source
from .source_roles import set_role,review_scope
from .model import now
OUTCOMES={'SOURCE_RESOLVED_AND_VALIDATED','SOURCE_RESOLVED_SCOPE_INCOMPLETE','SUPPORTED_PROVIDER_CONFIGURATION_FAILED','UNSUPPORTED_PROVIDER','CUSTOM_SOURCE_REVIEW','IDENTITY_AMBIGUOUS','NO_OFFICIAL_SOURCE_FOUND','TEMPORARY_ACCESS_FAILURE'}
STUDENTS=re.compile(r'\b(?:interns?|internships?|students?|campus|early.careers?|graduates?)\b',re.I)

def navigation_priority(url,label='',career_context=False):
 """Recruiting navigation only; never product, legal, editorial or job-detail links."""
 path=urlsplit(url).path; text=path+' '+label
 if re.search(r'privacy|cookie|student.?loans?|student.?debt|/blog(?:/|$)|/news(?:/|$)|\.(?:pdf|css|js|png|svg)$|/job/|/jobs/(?:page/|[a-z0-9]+/)',path,re.I):return None
 if re.search(r'kids?.?(?:and|&|-)?.?students?|student.?banking',text,re.I):return None
 if re.search(r'internships?|early.?careers?|students?(?:-| )(?:and|&)(?:-| )graduates?|campus.?recruit|university.?recruit|graduate.?opportunit',text,re.I):return 0
 if career_context and STUDENTS.search(text):return 0
 if re.search(r'/(?:careers?|jobs|search-jobs|job-search)/?$',path,re.I) or re.search(r'^\s*(?:careers?|jobs|(?:search|current|view|all|open) (?:jobs?|job openings|positions)|join (?:our )?team|employment opportunities)\s*$',label,re.I):return 1
 return None

def excluded_icims_surface(url):
 p=urlsplit(url or '');host=(p.hostname or '').lower()
 if not (host=='icims.com' or host.endswith('.icims.com')):return None
 if host in ('icims.com','www.icims.com'):return 'Vendor website/legal content, not an employer publication'
 if re.match(r'^(?:internal(?:spanish)?-|employees?-|apply-internal-)',host):return 'Explicit employee/internal-only portal; external public recruiting scope excludes it'
 if p.path.startswith('/icims2/'):return 'Vendor asset/document route, not a public job inventory'
 return None

def inspect(company,sources,max_pages=10,get=get_page,hints=(),entries=()):
 from .coverage import provider_hint
 result=dict(company_id=company['id'],employer=company['name'],attempted_at=now(),attempts=[],boards=[],unresolved_surfaces=[],outcome='NO_OFFICIAL_SOURCE_FOUND');queue=[];seen=set();boards={}
 def enqueue(u,reason):
  if u:
   try:
    if board_identity(u)[0]=='icims':u=board_identity(u)[2]
   except Exception:pass
  excluded=excluded_icims_surface(u)
  if excluded:
   result.setdefault('excluded_surfaces',[]).append(dict(url=u,reason=excluded));return
  if u and u.startswith(('https://','http://')) and u not in seen and all(u!=x[0] for x in queue):queue.append((u,reason))
 for e in entries:
  if e['kind'] not in ('RECORDED_CAREERS','RECRUITING_EVIDENCE'):enqueue(e['url'],'official domain evidence: '+e['kind'])
 for h in hints:enqueue(h['url'],h['evidence'])
 # Recorded official corporate entry first; known student/native sources remain in scope.
 enqueue(company.get('careers_url'),'recorded employer careers entry')
 domain=company.get('domain')
 if domain:
  host=domain_key(domain)
  if host and not any(s in host for s in ('myworkday','greenhouse','lever.co','oraclecloud','smartrecruiters')):enqueue('https://'+host,'recorded official domain')
 for s in sources:
  if s['source_role'] not in {'DUPLICATE','LEGACY','DISABLED','SUPPLEMENTAL'}:enqueue(s.get('careers_url'),'existing source '+str(s['id']))
 while queue and len(result['attempts'])<max_pages:
  u,reason=queue.pop(0)
  if u.rstrip('/') in seen:continue
  seen.add(u.rstrip('/'))
  try:
   final,h,links,sha=get(u,timeout=10);seen.add(final.rstrip('/'))
   from .career_mapping import published_redirect
   native=board_identity(final)
   migrated=published_redirect(h) if native[0]!='icims' or urlsplit(final).path in ('','/','/jobs','/jobs/','/jobs/search') else None
   if not migrated and native[0]=='icims':
    frames=[urljoin(final,x) for x,l,attrs in links if attrs.get('embedded') and urlsplit(urljoin(final,x)).hostname==urlsplit(final).hostname and urlsplit(urljoin(final,x)).path=='/jobs/search']
    if frames:
     ff,fh,fl,fs=get(frames[0],timeout=10);result['attempts'].append(dict(url=frames[0],final_url=ff,reason='Public iCIMS inventory iframe',sha256=fs,sources=[]));migrated=published_redirect(fh)
   if migrated:
    result['attempts'].append(dict(url=u,final_url=final,reason=reason,sha256=sha,sources=[],published_redirect=migrated));result.setdefault('migrations',[]).append(dict(old_url=native[2] if native[0]=='icims' else final,new_url=migrated,evidence_url=final,sha256=sha));enqueue(migrated,'explicit official careers/student link from '+final);queue.insert(0,queue.pop());continue
   found=discover_public_sources(final,h,links)
   # Native recorded career roots are inspectable recruiting publications themselves.
   p,i,root=board_identity(final)
   if p in ADAPTERS:found.append(dict(provider=p,identifier=i,url=root,evidence_url=final,method='Recorded employer careers surface resolves to native public board'))
   result['attempts'].append(dict(url=u,final_url=final,reason=reason,sha256=sha,sources=found))
   for b in found:
    key=(b['provider'],b['identifier'].casefold())
    official=bool(domain_key(b.get('evidence_url','')) and (domain_key(b.get('evidence_url','')) in {domain_key(e['url']) for e in entries if domain_key(e['url'])} or reason.startswith('explicit official careers/student link')) )
    if b['provider']=='icims' and official:b=dict(b,official_handoff=True)
    if key not in boards or b.get('official_handoff') or not boards[key].get('official_handoff'):boards[key]=b
   if p in ADAPTERS:continue
   if p not in ('custom','custom_unidentified'):
    result['unresolved_surfaces'].append(dict(url=final,provider=p,reason='Known unsupported recruiting provider requires an inventory contract; no employer-wide certification'))
    continue
   candidates=[]
   career_context=bool(re.search(r'career|jobs|recruit|work.with',urlsplit(final).path,re.I) or re.search(r'<title[^>]*>[^<]*(?:Career|Job|Recruit)',h,re.I))
   for href,label,attrs in links:
    v=urljoin(final,href)
    if not v.startswith('https://') or v.split('#')[0]==final.split('#')[0]:continue
    vp,vi,_=board_identity(v)
    if vp not in set(ADAPTERS)|{'custom','custom_unidentified'} and vi and not re.search(r'privacy|legal|loginOnly|/login',urlsplit(v).path,re.I):
     result.setdefault('unsupported_links',[]).append(dict(url=v,provider=vp,evidence_url=final,label=label))
    lp=provider_hint(v)[0]
    if career_context and lp not in set(ADAPTERS)|{'custom','custom_unidentified','unconfigured'} and not re.search(r'privacy|legal',urlsplit(v).path,re.I):
     result.setdefault('unsupported_links',[]).append(dict(url=v,provider=lp,evidence_url=final,label=label))
    priority=-2 if attrs.get('recruiting_inventory_root') else navigation_priority(v,label,career_context)
    if vp in ADAPTERS:v=board_identity(v)[2];priority=-1
    if priority is not None:candidates.append((priority,v))
   for _,v in reversed(sorted(set(candidates))):
    # Follow explicit employer career handoffs, never generic external social/aggregators.
    vh=urlsplit(v).hostname or ''
    if any(x in vh for x in ('linkedin.com','indeed.com','facebook.com','instagram.com','youtube.com','jobright.ai')):continue
    enqueue(v,'explicit official careers/student link from '+final)
    if queue and queue[-1][0]==v:queue.insert(0,queue.pop())
   search_surface=bool(re.search(r'/(?:jobs|search-jobs)/?$',urlsplit(final).path,re.I) or re.search(r'<title[^>]*>\s*(?:Jobs|Job Search)\b',h,re.I))
   if not found and (search_surface or (re.search(r'job search|search jobs|open positions|vacancies',h,re.I) and not candidates)):result['unresolved_surfaces'].append(dict(url=final,reason='Explicit recruiting/search surface without supported native handoff; linked informational pages do not establish full inventory'))
  except Exception as e:result['attempts'].append(dict(url=u,reason=reason,error=str(e)))
 result['boards']=list(boards.values());result['unvisited_recruiting_urls']=[u for u,_ in queue if board_identity(u)[0] not in ADAPTERS and navigation_priority(u,'',True) is not None]
 errors=[a for a in result['attempts'] if 'error' in a]
 result['entry_evidence']=list(entries)
 from .coverage import provider_hint
 result['unsupported_providers']=sorted({x['provider'] for x in result.get('unsupported_links',[])}|{provider_hint(a.get('final_url',a['url']))[0] for a in result['attempts'] if provider_hint(a.get('final_url',a['url']))[0] not in set(ADAPTERS)|{'custom','custom_unidentified','unconfigured'}})
 result['outcome']='UNSUPPORTED_PROVIDER' if result['unsupported_providers'] and not boards else 'SOURCE_RESOLVED_SCOPE_INCOMPLETE' if boards else 'TEMPORARY_ACCESS_FAILURE' if errors else 'CUSTOM_SOURCE_REVIEW' if result['unresolved_surfaces'] else 'NO_OFFICIAL_SOURCE_FOUND'
 return result

def source_identity(s):
 if s['ats_type']=='icims' and not s['careers_url']:
  identifier=s['ats_identifier'];return 'icims',identifier if identifier.endswith('.icims.com') else identifier+'.icims.com'
 if s['ats_type']=='talentbrew':return 'talentbrew',s['ats_identifier'].split('#scope=')[0]
 if s['ats_type']=='phenom':return 'phenom',s['ats_identifier']
 if s['ats_type']=='eightfold':
  identifier=s['ats_identifier']
  if '|' not in identifier:identifier=(urlsplit(s['careers_url'] or '').hostname or '')+'|'+identifier
  return 'eightfold',identifier.casefold()
 return board_identity(s['careers_url'])[:2]

def apply_inspection(db,result):
 cid=result['company_id'];configured=[];identities={}
 for b in result['boards']:
  if b['provider'] not in ADAPTERS:continue
  if b['provider']=='icims' and not b.get('official_handoff'):
   result.setdefault('ownership_unverified_sources',[]).append(b);result['outcome']='IDENTITY_AMBIGUOUS';continue
  key=(b['provider'],b['identifier'].casefold());owners=[]
  for s in db.execute('select * from sources where active=1 and careers_url is not null'):
   try:p,i=source_identity(s)
   except Exception:continue
   if (p,i.casefold())==key and canonical_id(db,s['company_id'])!=cid:owners.append(s['company_id'])
  if owners:result['outcome']='IDENTITY_AMBIGUOUS';result.setdefault('identity_conflicts',[]).append(dict(board=b,owners=owners));continue
  matches=[]
  for s in [s for s in db.execute('select * from sources where active=1') if canonical_id(db,s['company_id'])==cid]:
   try:p,i=source_identity(s)
   except Exception:continue
   if (p,i.casefold())==key:matches.append(dict(s))
  sid=min(matches,key=lambda s:(s['ats_type']!=b['provider'],s['source_role']!='AUTHORITATIVE',not s['health_status'].startswith('SUCCESS'),s['id']))['id'] if matches else source(db,cid,b['provider'],b['identifier'],b['url'])
  selected=db.execute('select * from sources where id=?',(sid,)).fetchone()
  if selected['ats_type']=='custom' and b['provider']!='custom':
   if not db.execute('select 1 from source_inventory where source_id=?',(sid,)).fetchone():
    db.execute("update sources set ats_type=?,ats_identifier=?,careers_url=?,health_status='UNKNOWN',last_checked_at=NULL,last_error=NULL,last_response_status=NULL where id=?",(b['provider'],b['identifier'],b['url'],sid))
   else:sid=source(db,cid,b['provider'],b['identifier'],b['url'])
   selected=db.execute('select * from sources where id=?',(sid,)).fetchone()
  if selected['source_role']=='DUPLICATE' and selected['canonical_source_id']:
   target=db.execute('select * from sources where id=?',(selected['canonical_source_id'],)).fetchone()
   if target and target['active'] and target['source_role']=='AUTHORITATIVE' and canonical_id(db,target['company_id'])==cid:sid=target['id']
  if b['provider']=='icims':
   db.execute('update sources set careers_url=? where id=?',(b['url'],sid))
  set_role(db,sid,'AUTHORITATIVE',dict(official_url=b.get('evidence_url') or b['url'],reason='Explicit official recruiting surface/native handoff inspected; inventory validation pending',attempt=result['attempted_at']));identities[key]=sid;configured.append(sid)
 result['configured_sources']=sorted(set(configured));return result

def reconcile_frontends(db,result):
 """Resolve informational predecessors only through inspected handoff evidence."""
 def key(url):
  try:return canonical_url(url or '')
  except ValueError:return ''
 edges={};official_domains=set()
 def publication(url):
  try:return key(board_identity(url)[2])
  except Exception:return key(url)
 def edge(a,b):
  if a and b:edges.setdefault(key(a),set()).add(key(b))
 for a in result['attempts']:
  if a.get('error'):continue
  final=a.get('final_url',a['url']);edge(a['url'],final)
  if domain_key(a['url']) and domain_key(final):official_domains.update((domain_key(a['url']),domain_key(final)))
  if a.get('reason','').startswith('explicit official careers/student link from '):edge(a['reason'].split(' from ',1)[1],a['url'])
  if a.get('published_redirect'):edge(final,a['published_redirect'])
  for b in a.get('sources',[]):edge(final,b['url'])
 for b in result.get('boards',[]):edge(b.get('evidence_url'),b['url'])
 for m in result.get('migrations',[]):edge(m['old_url'],m['new_url'])
 terminal=set()
 for sid in result.get('configured_sources',[]):
  s=db.execute('select * from sources where id=?',(sid,)).fetchone()
  if not s or not s['health_status'].startswith('SUCCESS'):continue
  census=db.execute('select jobs_returned from crawl_runs where source_id=? and authoritative=1 order by id desc limit 1',(sid,)).fetchone();count=db.execute('select count(*) from source_inventory where source_id=? and present=1',(sid,)).fetchone()[0]
  if census and census[0]==count:
   terminal.add(key(s['careers_url']))
   try:terminal.add(key(board_identity(s['careers_url'])[2]))
   except Exception:pass
 def reaches(url):
  todo=[key(url)];seen=set()
  while todo:
   u=todo.pop()
   if u in terminal:return True
   if u in seen:continue
   seen.add(u);todo.extend(edges.get(u,[]))
  return False
 if not terminal:return
 for s in db.execute("select * from sources where active=1 and source_role in ('UNKNOWN','AUTHORITATIVE')").fetchall():
  if canonical_id(db,s['company_id'])!=result['company_id']:continue
  url=s['careers_url'] or '';migration=next((m for m in result.get('migrations',[]) if key(m['old_url'])==publication(url) and reaches(m['new_url'])),None) if url else None
  if migration:
   set_role(db,s['id'],'LEGACY',dict(reason='Published native-portal migration reaches a completely enumerated current publication; old inventory history retained',migration=migration));result.setdefault('retired_frontend_urls',[]).append(url);continue
  if s['source_role']!='UNKNOWN' or s['ats_type']!='custom' or not domain_key(url):continue
  if reaches(url):set_role(db,s['id'],'SUPPLEMENTAL',dict(reason='Inspected careers frontend reaches the required native inventory through documented official links',official_url=url));continue
  old=next((a for a in result['attempts'] if key(a['url'])==key(url) and a.get('error')),None);host=urlsplit(url).hostname or '';path=urlsplit(url).path
  generic=bool(re.match(r'^(?:careers|jobs)\.',host) or re.search(r'/careers?/?$',path))
  owned=any(host==d or host.endswith('.'+d) for d in official_domains)
  unavailable=old and any(x in old['error'] for x in ('404','410','nodename nor servname','Name or service not known'))
  current_handoff=any(domain_key(a.get('final_url',a['url'])) in official_domains and a.get('sources') and not a.get('error') for a in result['attempts'])
  if generic and owned and unavailable and current_handoff:
   set_role(db,s['id'],'LEGACY',dict(reason='Unavailable historical marketing careers frontend is superseded by inspected current same-domain careers handoffs; not a distinct recruiting publication',official_url=url,evidence=old));result.setdefault('retired_frontend_urls',[]).append(url)

def certify(db,result):
 if result.get('evaluation_excluded'):return result
 reconcile_frontends(db,result)
 cid=result['company_id'];required=result.get('configured_sources',[]);errors=[a for a in result['attempts'] if a.get('error') and not a.get('reason','').startswith('official domain evidence:')]
 unknown=[]
 for row in [s for s in db.execute('select * from sources where active=1') if canonical_id(db,s['company_id'])==cid]:
  s=dict(row)
  excluded=excluded_icims_surface(s['careers_url'])
  if excluded and s['source_role'] not in ('LEGACY','DUPLICATE','DISABLED'):set_role(db,s['id'],'DISABLED',dict(reason=excluded,official_url=s['careers_url']));continue
  if s['source_role']!='UNKNOWN':continue
  if not s['careers_url'] and not s['ats_identifier']:set_role(db,s['id'],'DISABLED',dict(reason='Empty historical source placeholder; no publication identity'));continue
  try:k=board_identity(s['careers_url'])[:2];k=(k[0],k[1].casefold());same=next((sid for sid in required if (lambda b:(b[0],b[1].casefold()))(board_identity(db.execute('select careers_url from sources where id=?',(sid,)).fetchone()[0]))==k),None)
  except Exception:same=None
  if same and same!=s['id']:set_role(db,s['id'],'DUPLICATE',dict(reason='Same inspected native publication identity as complete required source'),same);continue
  inspected=next((a for a in result['attempts'] if canonical_url(s['careers_url'] or '') in {canonical_url(a['url']),canonical_url(a.get('final_url') or a['url'])} and not a.get('error')),None)
  if inspected and inspected.get('sources') and s['ats_type']=='custom':set_role(db,s['id'],'SUPPLEMENTAL',dict(official_url=s['careers_url'],reason='Inspected official frontend explicitly links the required native board; all discovered student handoffs investigated'));continue
  unknown.append(s['id'])
 all_required=[dict(s) for s in db.execute("select * from sources where active=1 and source_role='AUTHORITATIVE'") if canonical_id(db,s['company_id'])==cid]
 result['required_sources']=[s['id'] for s in all_required];result['unknown_source_ids']=unknown
 def complete_inventory(s):
  if not s['health_status'].startswith('SUCCESS') or not s['last_successful_check_at']:return False
  census=db.execute('select jobs_returned from crawl_runs where source_id=? and authoritative=1 order by id desc limit 1',(s['id'],)).fetchone()
  count=db.execute('select count(*) from source_inventory where source_id=? and present=1',(s['id'],)).fetchone()[0]
  return bool(census) and count==census[0]
 complete=bool(all_required) and all(complete_inventory(s) for s in all_required)
 retired=set(result.get('retired_frontend_urls',[]))
 from .sources.icims import posting_key
 complete_hosts={urlsplit(s['careers_url'] or '').hostname for s in all_required if s['ats_type']=='icims' and complete_inventory(s)}
 errors=[a for a in errors if a['url'] not in retired and not (posting_key(a['url']) and posting_key(a['url'])[0] in complete_hosts)]
 scope_complete=not errors and not unknown and not result.get('unsupported_providers') and not result['unresolved_surfaces'] and not result['unvisited_recruiting_urls'] and not result.get('identity_conflicts') and not result.get('ownership_unverified_sources')
 if complete and scope_complete:
  review_scope(db,cid,result['required_sources'],dict(official_url=result['attempts'][0]['url'],scope_reason='Recorded official corporate and known student surfaces inspected; every discovered supported board completely enumerated. No evidence of another required surface in bounded official inspection.',standard='PROVISIONALLY_VALIDATED',recovery_attempt=result));result['outcome']='SOURCE_RESOLVED_AND_VALIDATED'
 elif any(s['ats_type'] not in ADAPTERS for s in all_required):
  from .coverage import provider_hint
  hints={provider_hint(s['careers_url'] or '')[0] for s in all_required if s['ats_type'] not in ADAPTERS}
  result['outcome']='UNSUPPORTED_PROVIDER' if hints-{'custom_unidentified','custom'}-set(ADAPTERS) else 'CUSTOM_SOURCE_REVIEW'
 elif required and not complete:result['outcome']='SUPPORTED_PROVIDER_CONFIGURATION_FAILED'
 elif required:result['outcome']='SOURCE_RESOLVED_SCOPE_INCOMPLETE'
 return result

def consolidate_phenom_contracts(db,result):
 """Deduplicate portals only from matching captured endpoint/base/locale/country."""
 from pathlib import Path
 from .sources.phenom import configuration
 from .universe_sources import ROOT
 contracts={}
 for a in result.get('attempts',[]):
  if not a.get('sha256'):continue
  path=Path(ROOT)/'data/universe/career_mapping_snapshots'/(a['sha256']+'.html')
  if not path.exists():continue
  try:
   cfg=configuration(path.read_text(errors='replace'));key=tuple(cfg[k] for k in ('widgetApiEndpoint','baseUrl','locale','country'))
  except (SourceError,KeyError):continue
  for u in (a['url'],a.get('final_url')):contracts[u]=key
 groups={}
 for s in db.execute("select * from sources where ats_type='phenom' and active=1"):
  if canonical_id(db,s['company_id'])!=result['company_id']:continue
  key=contracts.get(s['careers_url'])
  if key:groups.setdefault(key,[]).append(dict(s))
 replace={}
 for key,ss in groups.items():
  ordered=sorted(ss,key=lambda s:(s['source_role']=='DUPLICATE',not s['health_status'].startswith('SUCCESS'),s['id']));canonical=ordered[0]
  for s in ordered[1:]:
   set_role(db,s['id'],'DUPLICATE',dict(reason='Captured public Phenom configuration proves identical widget endpoint, base URL, locale and country; cosmetic portal paths are one inventory',contract=key),canonical['id']);replace[s['id']]=canonical['id']
 result['configured_sources']=sorted({replace.get(sid,sid) for sid in result.get('configured_sources',[]) if db.execute('select source_role from sources where id=?',(sid,)).fetchone()[0] not in ('DISABLED','LEGACY')})
 return replace

def install_corrected_talentbrew_publication(db,old,snapshot):
 """Onboard the corrected scope separately; never close an old broad census by shrinkage."""
 import hashlib
 from .ingest import run_source
 contract=getattr(snapshot,'publication_contract',None)
 if old['ats_type']!='talentbrew' or not contract or not contract.get('filtered') or not snapshot.authoritative:raise ValueError('A complete, explicit filtered TalentBrew contract is required')
 signature=hashlib.sha256(json.dumps(contract,sort_keys=True).encode()).hexdigest()[:20]
 cid=canonical_id(db,old['company_id']);identifier=old['ats_identifier'].split('#scope=')[0]+'#scope='+signature
 sid=source(db,cid,'talentbrew',identifier,old['careers_url'],old['interval_seconds'])
 evidence=dict(official_url=old['careers_url'],reason='Corrected published TalentBrew checked/fixed-facet request; separate census preserves prior broad inventory history',contract=contract,previous_source_id=old['id'])
 with db:set_role(db,sid,'AUTHORITATIVE',evidence)
 from .reconcile import prepare_source
 r=run_source(db,prepare_source(db,db.execute('select * from sources where id=?',(sid,)).fetchone()),fetcher=lambda _:snapshot)
 if r['status'].startswith('SUCCESS'):
  with db:set_role(db,old['id'],'LEGACY',dict(reason='Superseded incorrect broad AJAX request for this filtered publication; historical memberships preserved without closure',corrected_source_id=sid,contract=contract))
 return r
