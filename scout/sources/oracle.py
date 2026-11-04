"""Oracle Cloud Candidate Experience public recruiting census (not Taleo)."""
import re
from urllib.parse import urlsplit,urlencode
from .base import SourceError,Snapshot,get_json
from ..model import Job,date
from ..classify import classify

def configuration(source):
 p=urlsplit(source['careers_url']);parts=p.path.split('/')
 if p.scheme!='https' or not p.hostname or not p.hostname.endswith('.oraclecloud.com') or 'sites' not in parts:raise SourceError('ATS_CHANGED','Verified Oracle Cloud Candidate Experience site required')
 site=parts[parts.index('sites')+1]
 if not re.fullmatch(r'[A-Za-z0-9_-]+',site):raise SourceError('ATS_CHANGED','Invalid Oracle site identifier')
 return 'https://'+p.hostname,site

def fetch(source,get=get_json):
 root,site=configuration(source);base=root+'/hcmRestApi/resources/latest/'
 def query(offset):
  url=base+'recruitingCEJobRequisitions?'+urlencode(dict(onlyData='true',expand='requisitionList',finder=f'findReqs;siteNumber={site},sortBy=POSTING_DATES_DESC,limit=200,offset={offset}'))
  raw=get(url);items=raw.get('items')
  if not isinstance(items,list) or len(items)!=1:raise SourceError('PARSER_ERROR','Oracle search envelope missing')
  r=items[0]
  if type(r.get('TotalJobsCount')) is not int or not isinstance(r.get('requisitionList'),list):raise SourceError('PARSER_ERROR','Oracle job count/inventory absent')
  if r.get('SiteNumber')!=site or r.get('Offset')!=offset:raise SourceError('PARSER_ERROR','Oracle site/offset mismatch')
  return r['TotalJobsCount'],r['requisitionList']
 total,chunk=query(0);front=[str(r.get('Id')) for r in chunk];seen=set();jobs=[]
 while True:
  if not chunk and len(jobs)<total:raise SourceError('PARSER_ERROR','Oracle empty page before total')
  for r in chunk:
   rid=str(r.get('Id') or '')
   if not rid or rid in seen or not r.get('Title'):raise SourceError('PARSER_ERROR',f'Oracle missing/repeated requisition or title: {rid} at row {len(jobs)}')
   seen.add(rid);url=f'{root}/hcmUI/CandidateExperience/en/sites/{site}/job/{rid}'
   j=Job(title=r['Title'],external_job_id=rid,description=r.get('ShortDescriptionStr') or '',locations=[r.get('PrimaryLocation','')],countries=[r.get('PrimaryLocationCountry','')],department=r.get('JobFunction') or r.get('JobFamily') or '',employment_type=r.get('JobType') or r.get('JobSchedule') or '',source_url=url,canonical_apply_url=url,posted_at=date(r.get('PostedDate')),posted_at_confidence='EMPLOYER_EXPLICIT' if date(r.get('PostedDate')) else 'UNKNOWN')
   from .detail_policy import needs_details
   if needs_details(j.clean(),source):
    detail=get(base+'recruitingCEJobRequisitionDetails/'+rid)
    if str(detail.get('Id'))!=rid or detail.get('Title')!=r['Title']:
     raise SourceError('PARSER_ERROR','Oracle public detail identity/title differs from inventory')
    description=detail.get('ExternalDescriptionStr') or ''
    if not description:raise SourceError('PARSER_ERROR','Oracle candidate detail has no public description')
    from ..model import text
    j.description=text(description+' '+(detail.get('ExternalQualificationsStr') or '')+' '+(detail.get('ExternalResponsibilitiesStr') or ''))
   jobs.append(j.clean())
  if len(jobs)==total:break
  if len(jobs)>total or len(jobs)>100000:raise SourceError('PARSER_ERROR','Oracle total/safety bound exceeded')
  count,chunk=query(len(jobs))
  if count!=total:raise SourceError('PARSER_ERROR',f'Oracle count changed during enumeration: {total} -> {count} at offset {len(jobs)}')
 check,first=query(0)
 if check!=total or [str(r.get('Id')) for r in first]!=front:raise SourceError('PARSER_ERROR','Oracle first-page verification changed')
 return Snapshot(jobs)

def discover_configuration(html,official_url):
 """Resolve vanity careers domains using Oracle's explicit public base metadata."""
 from html.parser import HTMLParser
 class Parser(HTMLParser):
  def __init__(self):super().__init__();self.bases=[]
  def handle_starttag(self,tag,attrs):
   if tag=='base':self.bases.append(dict(attrs))
 p=Parser();p.feed(html)
 if len(p.bases)!=1:raise SourceError('ATS_CHANGED','Oracle vanity metadata is absent or ambiguous')
 b=p.bases[0];api=urlsplit(b.get('data-apibaseurl',''));official=urlsplit(official_url);vanity=urlsplit(b.get('data-vanitybaseurl',''));site=b.get('data-sitenumber','')
 if api.scheme!='https' or not api.hostname or not api.hostname.endswith('.oraclecloud.com') or api.username or api.password or api.path not in ('','/') or api.port not in (None,443) or official.scheme!='https' or vanity.hostname!=official.hostname or not re.fullmatch(r'[A-Za-z0-9_-]+',site):raise SourceError('ATS_CHANGED','Unverified Oracle vanity host/site metadata')
 return f'https://{api.hostname}/hcmUI/CandidateExperience/en/sites/{site}'
