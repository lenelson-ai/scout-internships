"""Public Phenom refineSearch inventory; no keywords/facets restrict the census."""
import json,re,html
from urllib.parse import urljoin
from .base import SourceError,Snapshot,get_json
from ..model import Job,date
from ..classify import classify
from .detail_policy import needs_details

def configuration(html):
 m=re.search(r'phApp\s*=\s*phApp\s*\|\|\s*(\{)',html)
 if not m:raise SourceError('PARSER_ERROR','No public Phenom configuration')
 try:return json.JSONDecoder().raw_decode(html[m.start(1):])[0]
 except Exception as e:raise SourceError('PARSER_ERROR','Invalid Phenom configuration') from e

def fetch(source,get=get_json,page=None):
 if page is None:
  from .base import html_page
  page=html_page
 final,body,_,_=page(source['careers_url']);cfg=configuration(body)
 endpoint=cfg.get('widgetApiEndpoint');base=cfg.get('baseUrl') or final
 if not endpoint or not endpoint.startswith('https://'):raise SourceError('ATS_CHANGED','Missing Phenom widget endpoint')
 def query(offset):
  payload=dict(lang=cfg['locale'],country=cfg['country'],deviceType='desktop',pageName='search-results',ddoKey='refineSearch',jobs=True,counts=True,size=500,all_fields=[],sortBy='Most recent',subsearch='');payload['from']=offset;payload['sort']={'order':'desc','field':'postedDate'};payload['global']=True
  raw=get(endpoint,payload);r=raw.get('refineSearch',{})
  if r.get('status')!=200 or type(r.get('totalHits')) is not int or not isinstance(r.get('data',{}).get('jobs'),list):raise SourceError('PARSER_ERROR','Phenom response lacks explicit complete search result data')
  return r['totalHits'],r['data']['jobs']
 total,chunk=query(0);initial=[str(r.get('jobSeqNo') or r.get('jobId')) for r in chunk];jobs=[];seen=set()
 while True:
  if not chunk and len(jobs)<total:raise SourceError('PARSER_ERROR','Phenom empty page before declared total')
  for r in chunk:
   identity=str(r.get('jobSeqNo') or r.get('jobId') or '')
   if not identity or identity in seen or not r.get('title'):raise SourceError('PARSER_ERROR',f'Phenom missing/repeated identity or title: {identity} at row {len(jobs)}')
   seen.add(identity)
   url=urljoin(base,'job/'+identity)
   locations=r.get('multi_location') or [r.get('location') or ', '.join(str(r.get(k) or '') for k in ('city','state','country'))]
   jobs.append(Job(title=r['title'],external_job_id=identity,description=r.get('description') or r.get('descriptionTeaser') or '',locations=locations,countries=[r.get('country','')],department=r.get('category',''),employment_type=r.get('type',''),source_url=url,canonical_apply_url=html.unescape(r.get('applyUrl') or url),posted_at=date(r.get('postedDate')),posted_at_confidence='EMPLOYER_EXPLICIT' if date(r.get('postedDate')) else 'UNKNOWN').clean())
  if len(jobs)==total:break
  if len(jobs)>total or len(jobs)>100000:raise SourceError('PARSER_ERROR','Phenom count exceeded total or safety bound')
  current,chunk=query(len(jobs))
  if current!=total:raise SourceError('PARSER_ERROR','Phenom inventory total changed')
 check,front=query(0)
 if check!=total or not {str(r.get('jobSeqNo') or r.get('jobId')) for r in front}.issubset(seen):raise SourceError('PARSER_ERROR','Phenom inventory changed during enumeration')
 for job in jobs:
  if needs_details(job,source):
   raw=get(endpoint,dict(lang=cfg['locale'],country=cfg['country'],deviceType='desktop',pageName='job',ddoKey='jobDetail',jobId=job.external_job_id,jobSeqNo=job.external_job_id))
   envelope=raw.get('jobDetail',{});detail=envelope.get('data',{}).get('job',{})
   if envelope.get('status')!=200 or str(detail.get('jobSeqNo'))!=job.external_job_id or not detail.get('description'):raise SourceError('PARSER_ERROR','Phenom internship detail missing or identity changed')
   job.description=detail['description']
   if detail.get('applyUrl'):job.canonical_apply_url=html.unescape(detail['applyUrl'])
   if date(detail.get('postedDate')):job.posted_at=date(detail['postedDate']);job.posted_at_confidence='EMPLOYER_EXPLICIT'
   job.clean()
 return Snapshot(jobs)


def facet_census(cfg,get):
 """Union public facet leaves, requiring exact unfiltered unique-ID cardinality.

 Facets may overlap; identical publications are deduplicated. Completeness is
 established by the uncapped root count, never by summing overlapping facets.
 """
 def query(selected,offset=0):
  p=dict(lang=cfg['locale'],country=cfg['country'],deviceType='desktop',pageName='search-results',ddoKey='refineSearch',jobs=True,counts=True,size=500,all_fields=['category','country','state','city'],sort={'order':'desc','field':'postedDate'},selected_fields=selected)
  p['from']=offset;p['global']=True
  r=get(cfg['widgetApiEndpoint'],p).get('refineSearch',{})
  if r.get('status')!=200 or type(r.get('totalHits')) is not int or not isinstance(r.get('data',{}).get('jobs'),list):raise SourceError('PARSER_ERROR','Phenom facet census malformed')
  return r
 root=query({});total=root['totalHits'];union={};parts=0
 def walk(r,selected):
  nonlocal parts
  parts+=1
  if parts>500:raise SourceError('PARSER_ERROR','Phenom facet safety bound exceeded')
  jj=r['data']['jobs'];n=r['totalHits']
  # Short refined inventories can be paginated even when the unfiltered list
  # churns. Publish a leaf only after its own complete unique-ID proof.
  if selected and len(jj)<n:
   try:
    collected=list(jj);leaf_ids={str(j.get('jobSeqNo') or j.get('jobId')) for j in jj}
    while len(collected)<n:
     nxt=query(selected,len(collected));chunk=nxt['data']['jobs'];ids=[str(j.get('jobSeqNo') or j.get('jobId')) for j in chunk]
     if nxt['totalHits']!=n or not chunk or len(ids)!=len(set(ids)) or leaf_ids.intersection(ids):raise SourceError('PARSER_ERROR','Unstable Phenom refined pagination')
     collected.extend(chunk);leaf_ids.update(ids)
    check=query(selected)
    if len(collected)!=n or check['totalHits']!=n or not {str(j.get('jobSeqNo') or j.get('jobId')) for j in check['data']['jobs']}.issubset(leaf_ids):raise SourceError('PARSER_ERROR','Phenom refined census changed')
    jj=collected
   except SourceError as exc:
    if exc.status!='PARSER_ERROR':raise
    pass # Split this leaf further; no incomplete rows have entered the union.
  if len(jj)==n:
   local=set()
   for job in jj:
    rid=str(job.get('jobSeqNo') or job.get('jobId') or '')
    if not rid or rid in local or not job.get('title'):raise SourceError('PARSER_ERROR','Phenom opaque/duplicate facet publication')
    local.add(rid)
    if rid in union and (union[rid].get('title'),union[rid].get('reqId'))!=(job.get('title'),job.get('reqId')):raise SourceError('PARSER_ERROR','Phenom conflicting facet publications')
    union.setdefault(rid,job)
   return
  options=[a for a in r['data'].get('aggregations',[]) if a.get('field') in ('category','country','state','city') and a['field'] not in selected and isinstance(a.get('value'),dict) and a['value']]
  if not options:raise SourceError('PARSER_ERROR','Phenom facet leaf remains paginated or incomplete')
  choice=min(options,key=lambda a:max(a['value'].values()));field=choice['field']
  for value in sorted(choice['value']):walk(query(dict(selected,**{field:[value]})),dict(selected,**{field:[value]}))
 walk(root,{})
 if len(union)!=total:raise SourceError('PARSER_ERROR',f'Phenom facet union incomplete: {len(union)} unique IDs vs {total}')
 check=query({})
 if check['totalHits']!=total or not {str(j.get('jobSeqNo') or j.get('jobId')) for j in check['data']['jobs']}.issubset(union):raise SourceError('PARSER_ERROR','Phenom root changed during facet census')
 return total,[union[k] for k in sorted(union)]

# Preserve the simple unfiltered path; fall back only for enumeration instability.
_fetch_direct=fetch
def fetch(source,get=get_json,page=None):
 try:return _fetch_direct(source,get,page)
 except SourceError as exc:
  if not any(s in str(exc) for s in ('missing/repeated identity','inventory changed','total changed')):raise
 if page is None:
  from .base import html_page
  page=html_page
 cfg=configuration(page(source['careers_url'])[1]);total,jobs=facet_census(cfg,get);calls=0
 def stable_get(url,payload):
  nonlocal calls
  if payload.get('ddoKey')!='refineSearch':return get(url,payload)
  if payload.get('from',0)!=0:raise SourceError('PARSER_ERROR','Unexpected facet-census pagination')
  calls+=1
  if calls>1:
   check=get(url,payload).get('refineSearch',{})
   if check.get('status')!=200 or check.get('totalHits')!=total or not isinstance(check.get('data',{}).get('jobs'),list) or (total>0 and not check['data']['jobs']) or not {str(j.get('jobSeqNo') or j.get('jobId')) for j in check.get('data',{}).get('jobs',[])}.issubset({str(j.get('jobSeqNo') or j.get('jobId')) for j in jobs}):raise SourceError('PARSER_ERROR','Phenom final census verification failed')
  return dict(refineSearch=dict(status=200,totalHits=total,data=dict(jobs=jobs)))
 return _fetch_direct(source,stable_get,page)
