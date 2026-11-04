"""Public Eightfold PCS inventory; distinct publication IDs and explicit census."""
from urllib.parse import urlsplit,urlencode,urljoin
from datetime import datetime,timezone
import re
from .base import get_json,SourceError,Snapshot
from ..classify import classify
from .detail_policy import needs_details
from ..model import Job

def fetch(source,get=get_json):
 p=urlsplit(source['careers_url']);root='https://'+(p.hostname or '');domain=source['ats_identifier']
 if '|' in domain:
  configured_host,domain=domain.split('|',1)
  if configured_host.casefold()!=(p.hostname or '').casefold():raise SourceError('ATS_CHANGED','Eightfold configured publication host changed')
 if p.scheme!='https' or not domain or '/' in domain:raise SourceError('ATS_CHANGED','Verified Eightfold host/domain required')
 def query(start):
  r=get(root+'/api/pcsx/search?'+urlencode(dict(domain=domain,query='',start=start,sort_by='timestamp')));d=r.get('data',{})
  if r.get('status')!=200 or type(d.get('count')) is not int or not isinstance(d.get('positions'),list):raise SourceError('PARSER_ERROR','Eightfold explicit search census absent')
  return d['count'],d['positions']
 total,chunk=query(0);front=[str(r.get('id')) for r in chunk];jobs=[];seen=set()
 while True:
  if not chunk and len(jobs)<total:raise SourceError('PARSER_ERROR','Eightfold empty page before count')
  for r in chunk:
   rid=str(r.get('id') or '')
   if not rid or rid in seen or not r.get('name'):raise SourceError('PARSER_ERROR','Eightfold missing/repeated publication ID or title')
   seen.add(rid);url=urljoin(root,r.get('positionUrl') or '/careers/job/'+rid);posted=None
   if isinstance(r.get('postedTs'),(int,float)):posted=datetime.fromtimestamp(r['postedTs'],timezone.utc).isoformat()
   country_codes=[loc.rsplit(',',1)[-1].strip() for loc in r.get('standardizedLocations',[]) if len(loc.split(','))>=3 and re.fullmatch(r'[A-Z]{2}',loc.rsplit(',',1)[-1].strip())]
   jobs.append(Job(title=r['name'],external_job_id=rid,countries=country_codes,locations=r.get('standardizedLocations') or r.get('locations') or [],department=r.get('department') or '',source_url=url,canonical_apply_url=url,posted_at=posted,posted_at_confidence='EMPLOYER_EXPLICIT' if posted else 'UNKNOWN',description=r.get('description') or '').clean())
  if len(jobs)==total:break
  if len(jobs)>total or len(jobs)>100000:raise SourceError('PARSER_ERROR','Eightfold count/safety bound exceeded')
  count,chunk=query(len(jobs))
  if count!=total:raise SourceError('PARSER_ERROR',f'Eightfold count changed during pagination: {total} -> {count} at offset {len(jobs)}')
 count,first=query(0)
 if count!=total or [str(r.get('id')) for r in first]!=front:raise SourceError('PARSER_ERROR','Eightfold first-page consistency check failed')
 for job in jobs:
  if needs_details(job,source):
   raw=get(root+'/api/pcsx/position_details?'+urlencode(dict(domain=domain,position_id=job.external_job_id)));detail=raw.get('data',{})
   if raw.get('status')!=200 or str(detail.get('id'))!=job.external_job_id or not detail.get('jobDescription'):raise SourceError('PARSER_ERROR','Eightfold internship detail missing or identity changed')
   job.description=detail['jobDescription'];job.clean()
 return Snapshot(jobs)
