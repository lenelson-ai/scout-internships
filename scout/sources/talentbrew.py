"""TalentBrew public search contract, including count-verified AJAX pagination."""
import json,re
from html.parser import HTMLParser
from urllib.parse import urljoin,urlencode,urlsplit
from .base import SourceError,Snapshot,get_json
from ..model import Job,date
from ..classify import classify
from .detail_policy import needs_details

class Listing(HTMLParser):
 def __init__(self,html):
  super().__init__();self.attrs=None;self.filter_attrs={};self.filters=[];self.raw=[];self.current=None;self.title=False;self.loc=False;self.anchor=False;self.scope=0;self.feed(html)
  self.jobs=[dict(url=r['url'],title=' '.join(r['title'] or r['text']).strip(),location=' '.join(r['location']).strip()) for r in self.raw]
 def handle_starttag(self,tag,attrs):
  a=dict(attrs)
  if a.get('id')=='search-filters':self.filter_attrs=a
  if tag=='input' and 'filter-checkbox' in a.get('class','').split() and 'checked' in a and 'disabled' not in a:
   try:self.filters.append(dict(ID=a['data-id'],FacetType=int(a['data-facet-type']),Count=int(a.get('data-count',0)),Display=a.get('data-display',''),IsApplied=True,FieldName=a.get('data-field-name','')))
   except (KeyError,ValueError):raise SourceError('PARSER_ERROR','TalentBrew checked filter lacks published identity')
  if a.get('id')=='search-results':self.attrs=a;self.scope=1
  elif self.scope and tag=='section':self.scope+=1
  if not self.scope:return
  if tag=='a' and '/job/' in a.get('href',''):
   self.current=dict(url=a['href'],title=[],text=[],location=[]);self.raw.append(self.current);self.anchor=True
  if self.current:
   if tag in ('h2','h3'):self.title=True
   if 'location' in a.get('class',''):self.loc=True
 def handle_data(self,data):
  if self.current:
   if self.title:self.current['title'].append(data)
   if self.anchor:self.current['text'].append(data)
   if self.loc:self.current['location'].append(data)
 def handle_endtag(self,tag):
  if tag=='section' and self.scope:self.scope-=1
  if tag in ('h2','h3'):self.title=False
  if tag=='span':self.loc=False
  if tag=='a':self.anchor=False
  if tag=='li':self.current=None

def jobposting(html):
 for raw in re.findall(r'<script[^>]*type=["\']application/ld\+json["\'][^>]*>(.*?)</script>',html,re.S|re.I):
  try:data=json.loads(raw)
  except Exception:continue
  todo=data if isinstance(data,list) else [data]
  for item in todo:
   if not isinstance(item,dict):continue
   if item.get('@type')=='JobPosting':return item
   todo.extend(item.get('@graph',[]))
 return {}

def fetch(source,get=get_json,page=None):
 if page is None:
  from .base import html_page
  page=html_page
 final,html,_,_=page(source['careers_url']);first=Listing(html)
 if not first.attrs:raise SourceError('PARSER_ERROR','TalentBrew search configuration absent')
 attrs=first.attrs
 def count(p):
  try:return int(p.attrs['data-total-job-results'])
  except Exception:raise SourceError('PARSER_ERROR','TalentBrew explicit job count absent')
 args={''.join(x.title() for x in k[5:].split('-')):v for k,v in attrs.items() if k.startswith('data-') and k!='data-refined-keywords'}
 args.update(ResultsType=1,IsPagination='False',RecordsPerPage=250)
 # Reproduce the provider's published search.js mapSearchOptions contract.
 # Checked and fixed facets belong to this publication, including campus filters.
 try:
  refined=json.loads(attrs.get('data-refined-keywords','[]'))
  fixed=json.loads(first.filter_attrs.get('data-filtered-facets','[]') or '[]')
 except ValueError:raise SourceError('PARSER_ERROR','TalentBrew filtered publication metadata is not JSON')
 if not isinstance(refined,list) or not isinstance(fixed,list) or any(not isinstance(f,dict) or 'ID' not in f or 'FacetType' not in f for f in fixed):raise SourceError('PARSER_ERROR','TalentBrew filter metadata invalid')
 filters=first.filters[:]
 for f in fixed:
  if not any(str(x['ID'])==str(f['ID']) and str(x['FacetType'])==str(f['FacetType']) for x in filters):filters.append(f)
 filtered=bool(filters or refined or any(first.filter_attrs.get('data-filtered-'+k) for k in ('categories','locations','custom-facet','advanced-categories','advanced-locations','advanced-custom-facet')))
 use_post=bool(attrs.get('data-ajax-post-url'))
 if filtered and not use_post:raise SourceError('PARSER_ERROR','TalentBrew filtered publication has no published POST endpoint')
 if use_post:
  if not attrs.get('data-ajax-post-url'):raise SourceError('PARSER_ERROR','TalentBrew filtered publication has no published POST endpoint')
  args.update(FacetFilters=filters,RefinedKeywords=refined)
  for key,suffix in (('fc','categories'),('fl','locations'),('fcf','custom-facet'),('afc','advanced-categories'),('afl','advanced-locations'),('afcf','advanced-custom-facet')):args[key]=first.filter_attrs.get('data-filtered-'+suffix,'')
  for key in ('ActiveFacetId','CurrentPage','TotalPages','TotalResults','TotalContentResults','Distance','SortCriteria','SortDirection','SearchType','FacetType','CategoryFacetType','LocationFacetType'):
   if key in args:
    try:args[key]=int(args[key])
    except (TypeError,ValueError):args[key]=None
  if 'ShowRadius' in args:args['ShowRadius']=str(args['ShowRadius']).lower()=='true'
 def query():
  if use_post:return get(urljoin(final,attrs['data-ajax-post-url']),args)
  return get(endpoint+'?'+urlencode(args))
 endpoint=urljoin(final,attrs['data-ajax-url'])
 args['CurrentPage']=1
 payload=query()
 if not isinstance(payload.get('results'),str):raise SourceError('PARSER_ERROR','TalentBrew live first page absent')
 live=Listing(payload['results'])
 unfiltered_root=not urlsplit(final).query and bool(re.fullmatch(r'/(?:[a-z]{2}(?:-[a-z]{2})?/)?search-jobs/?',urlsplit(final).path,re.I)) and not filtered and not any(attrs.get('data-'+k) for k in ('keywords','location','facet-term','category-facet-term','location-facet-term','postal-code','custom-facet-name'))
 # Root HTML can be edge-cached. The explicit live AJAX census and final
 # repeated first page prove completeness only for a published unfiltered root.
 # Filtered/category/keyword/locale views still require their own count agreement.
 if count(live)!=count(first) and not unfiltered_root:raise SourceError('PARSER_ERROR',f'TalentBrew HTML/AJAX publication census differs: {count(first)} -> {count(live)}; filtered source scope or churn requires remediation')
 first=live;args['RecordsPerPage']=int(first.attrs.get('data-records-per-page',250));total=count(first)
 # Pagination carries forward the freshly returned census, not edge-cached HTML
 # totals. First/final requests are non-pagination searches to recount live jobs.
 args.update(TotalResults=total,TotalJobResults=total,TotalContentResults=0,TotalPages=(total+args['RecordsPerPage']-1)//args['RecordsPerPage'])
 initial=[r['url'] for r in first.jobs];allrows=[];seen=set();current=first;number=1
 while True:
  if count(current)!=total:raise SourceError('PARSER_ERROR','TalentBrew inventory total changed')
  if not current.jobs and len(allrows)<total:raise SourceError('PARSER_ERROR','TalentBrew empty page before total')
  for r in current.jobs:
   key=r['url'].rstrip('/').split('/')[-1]
   if not key or key in seen or not r['title']:raise SourceError('PARSER_ERROR','TalentBrew duplicate/missing job identity or title')
   seen.add(key);allrows.append((key,r))
  if len(allrows)==total:break
  if len(allrows)>total or number>1000:raise SourceError('PARSER_ERROR','TalentBrew count/safety bound exceeded')
  number+=1;args['CurrentPage']=number;args['IsPagination']='True';payload=query()
  if not isinstance(payload.get('results'),str):raise SourceError('PARSER_ERROR','TalentBrew AJAX lacks results')
  current=Listing(payload['results'])
  if not current.attrs or int(current.attrs.get('data-current-page',0))!=number:raise SourceError('PARSER_ERROR','TalentBrew pagination did not advance')
 args['CurrentPage']=1;args['IsPagination']='False'
 verify=query()
 if not isinstance(verify.get('results'),str):raise SourceError('PARSER_ERROR','TalentBrew final verification absent')
 check=Listing(verify['results'])
 if count(check)!=total or [r['url'] for r in check.jobs]!=initial:raise SourceError('PARSER_ERROR','TalentBrew inventory changed during crawl')
 jobs=[]
 for key,r in allrows:
  url=urljoin(final,r['url']);j=Job(title=r['title'],external_job_id=key,locations=[r['location']] if r['location'] else [],source_url=url,canonical_apply_url=url)
  if needs_details(j,source):
   _,body,page_links,_=page(url);detail=jobposting(body)
   if not detail or not detail.get('description'):raise SourceError('PARSER_ERROR','TalentBrew internship detail lacks structured posting')
   j.description=detail['description']
   places=detail.get('jobLocation') or []
   if isinstance(places,dict):places=[places]
   locs=[];countries=[]
   for place in places:
    address=place.get('address',{}) if isinstance(place,dict) else {}
    country=address.get('addressCountry')
    if isinstance(country,dict):country=country.get('name') or country.get('identifier')
    label=', '.join(str(address.get(k) or '') for k in ('addressLocality','addressRegion') if address.get(k))
    if label:locs.append(label)
    if country:countries.append(str(country))
   if locs:j.locations=locs
   if countries:j.countries=countries
   for href,label,link_attrs in page_links:
    if re.fullmatch(r'\s*apply(?:\s+now)?\s*',label,re.I) and urljoin(url,href).startswith('https://'):j.canonical_apply_url=urljoin(url,href);break
   j.posted_at=date(detail.get('datePosted'));j.posted_at_confidence='EMPLOYER_EXPLICIT' if j.posted_at else 'UNKNOWN';j.employment_type=detail.get('employmentType','')
   if isinstance(j.employment_type,list):j.employment_type=', '.join(j.employment_type)
  jobs.append(j.clean())
 snapshot=Snapshot(jobs)
 stable={k:v for k,v in args.items() if k not in {'CurrentPage','RecordsPerPage','TotalPages','TotalResults','TotalContentResults','TotalJobResults'}}
 if 'FacetFilters' in stable:stable['FacetFilters']=sorted([{k:v for k,v in f.items() if k not in ('Count','Display')} for f in stable['FacetFilters']],key=lambda f:json.dumps(f,sort_keys=True))
 snapshot.publication_contract=dict(request_parameters=stable,filtered=filtered,url=final,endpoint=urljoin(final,attrs['data-ajax-post-url']) if use_post else endpoint,filters=[{k:f.get(k) for k in ('ID','FacetType','FieldName')} for f in filters],refined_keywords=refined,keywords=attrs.get('data-keywords',''))
 return snapshot
