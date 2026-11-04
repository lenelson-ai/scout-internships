"""Public iCIMS classic card/row portals. Full double traversal; never auth or private APIs."""
import json,re
from urllib.parse import urlsplit,urljoin,parse_qs,urlencode,unquote
from datetime import datetime
from .base import SourceError,Snapshot,html_page
from .detail_policy import needs_details
from ..html_tree import Tree
from ..model import Job,canonical_url,date
HOST=re.compile(r'(?!www\.|internal(?:spanish)?[-.]|employees?[-.]|apply-internal(?:spanish)?[-.])([a-z0-9-]+)\.icims\.com$',re.I)
JOB=re.compile(r'^/jobs/(\d+)/(?:[^/]+/)?(?:job|login)/?$',re.I)
def configuration(source):
 p=urlsplit(source.get('careers_url') or '')
 if p.scheme!='https' or not HOST.fullmatch(p.hostname or '') or p.username:raise SourceError('ATS_CHANGED','Not an external public iCIMS portal')
 if p.path.rstrip('/')=='/jobs/login':raise SourceError('ATS_CHANGED','Candidate account sign-in URL is not a verified recruiting publication')
 if p.path and not (re.match(r'^/(?:jobs|career-home)(?:/|$)',p.path) or p.path=='/') or '\\' in p.path:raise SourceError('ATS_CHANGED','iCIMS asset/vendor/document path is not a recruiting publication')
 return p.hostname.lower(),'https://'+p.hostname.lower()+'/jobs/search'
def posting_key(url):
 p=urlsplit(url);m=JOB.fullmatch(p.path)
 return ((p.hostname or '').lower(),m[1]) if m and '..' not in unquote(p.path) and HOST.fullmatch(p.hostname or '') else None
def fields(node):
 out={}
 for group in node.all(cls='iCIMS_JobHeaderTag'):
  key=group.all('dt');value=group.all('dd')
  if len(key)==len(value)==1:out[key[0].text().strip().lower()]=value[0].text()
 return out
def place(raw):
 locations=[];countries=[]
 for value in re.split(r'\s*\|\s*',raw):
  m=re.fullmatch(r'([A-Z]{2})(?:-([A-Z]{2}))?-(.+)',value.strip())
  if m:
   countries.append(m[1]);locations.append(m[3]+(', '+m[2] if m[2] else ''))
  elif value.strip():locations.append(value.strip())
 return locations,sorted(set(countries))
def listing(body,url):
 tree=Tree(body).root;headers=tree.all(cls='iCIMS_SubHeader_Jobs');text=' '.join(x.text() for x in headers)
 page=re.findall(r'\b(?:Page|Página)\s+(\d+)\s+(?:of|de)\s+(\d+)\b',text,re.I)
 cards=tree.all(cls='iCIMS_JobCardItem');variant='CLASSIC_CARDS'
 if not cards:cards=tree.all(cls='iCIMS_JobListingRow');variant='CLASSIC_ROWS'
 empty=bool(re.search(r'Sorry, no jobs were found that match your search criteria',tree.text(),re.I))
 if not cards and empty:return dict(page=1,pages=1,rows=[],next={},variant='CLASSIC_EMPTY',empty=True)
 if len(set(page))!=1 or not cards:raise SourceError('PARSER_ERROR','iCIMS public inventory lacks explicit page census and job rows (unsupported public variant or blocked content)')
 current,pages=map(int,page[0])
 if not 1<=current<=pages<=500:raise SourceError('PARSER_ERROR','iCIMS page census exceeds safety bounds')
 rows=[]
 for card in cards:
  anchors=[a for a in card.all('a') if posting_key(urljoin(url,a.attrs.get('href','')))]
  keys={posting_key(urljoin(url,a.attrs.get('href',''))) for a in anchors}
  if len(keys)!=1:raise SourceError('PARSER_ERROR','iCIMS row lacks one published posting identity')
  anchor=anchors[0];key=next(iter(keys));titles=anchor.all('h3') or anchor.all('h2');title=titles[0].text() if titles else anchor.text().removeprefix('Title ').strip();meta=fields(card)
  if not title:raise SourceError('PARSER_ERROR','iCIMS title absent')
  locs=card.all(cls='header');raw=next((re.sub(r'^.*?(?=[A-Z]{2}(?:-[A-Z]{1,3})?-)', '',x.text()) for x in locs if re.search(r'[A-Z]{2}(?:-[A-Z]{1,3})?-',x.text())),meta.get('job locations') or meta.get('ubicación primaria') or '')
  locations,countries=place(raw);descs=card.all(cls='description')
  rows.append(dict(native_id=key[1],title=title,url=canonical_url(urljoin(url,anchor.attrs['href']).split('?')[0]),external_id=meta.get('id') or meta.get('identificación') or key[1],locations=locations,countries=countries,department=meta.get('category') or meta.get('function') or '',description=descs[0].text() if descs else ''))
 nexts={}
 for a in tree.all('a'):
  href=urljoin(url,a.attrs.get('href',''));p=urlsplit(href);q=parse_qs(p.query)
  if p.hostname==urlsplit(url).hostname and p.path=='/jobs/search' and q.get('pr',[''])[0].isdigit():
   index=int(q['pr'][0])+1
   if 1<=index<=pages:nexts[index]=href
 return dict(page=current,pages=pages,rows=rows,next=nexts,variant=variant,empty=False)
def fetch(source,page=html_page):
 host,root=configuration(source);cache={};evidence=[]
 def read(url):
  final,body,links,sha=page(url)
  if urlsplit(final).hostname!=host:raise SourceError('ATS_CHANGED','iCIMS publication redirected to another host')
  if '/login' in urlsplit(final).path or re.search(r'access denied|verify (?:that )?you are human|<title[^>]*>[^<]*captcha',body,re.I):raise SourceError('BLOCKED','Public iCIMS inventory access is restricted')
  # Follow only a same-host iframe actually published in the employer portal.
  frames=[urljoin(final,u) for u,l,a in links if a.get('embedded') and urlsplit(urljoin(final,u)).hostname==host and urlsplit(urljoin(final,u)).path=='/jobs/search']
  if frames and not parse_qs(urlsplit(final).query).get('in_iframe'):
   final,body,links,sha=page(frames[0])
   if urlsplit(final).hostname!=host or '/login' in urlsplit(final).path:raise SourceError('BLOCKED','Public iCIMS iframe requires authentication')
  from ..career_mapping import published_redirect
  migration=published_redirect(body)
  if migration:raise SourceError('ATS_CHANGED','Public iCIMS portal publishes migration to '+migration)
  try:parsed=listing(body,final)
  except SourceError:
   forms=[f for f in Tree(body).root.all('form') if f.attrs.get('id')=='searchForm' and any(x.attrs.get('name')=='ss' and x.attrs.get('value')=='1' for x in f.all('input'))]
   if len(forms)!=1 or parse_qs(urlsplit(final).query).get('ss')==['1']:raise
   action=urljoin(final,forms[0].attrs.get('action',''));p=urlsplit(action)
   if p.hostname!=host or p.path!='/jobs/search':raise SourceError('PARSER_ERROR','iCIMS public search form changes publication')
   q=parse_qs(p.query,keep_blank_values=True);q.update(ss=['1'],in_iframe=['1']);final,body,links,sha=page('https://'+host+p.path+'?'+urlencode(q,doseq=True))
   if urlsplit(final).hostname!=host or '/login' in urlsplit(final).path:raise SourceError('BLOCKED','Submitted public search requires authentication')
   parsed=listing(body,final)
  evidence.append(dict(url=final,sha256=sha,page=parsed['page'],pages=parsed['pages'],variant=parsed['variant'],rows=len(parsed['rows'])))
  return parsed,final
 entry=[root]
 def census():
  first,url=read(entry[0]);entry[0]=url;pages=first['pages'];size=len(first['rows']);found=[];seen=set();paths={1:url};paths.update(first['next']);variants=set()
  for number in range(1,pages+1):
   if number==1:p=first
   else:
    if number not in paths:raise SourceError('PARSER_ERROR','iCIMS declared page has no published navigation link')
    p,_=read(paths[number])
   if p['page']!=number or p['pages']!=pages or (not p['rows'] and pages>1):raise SourceError('PARSER_ERROR','iCIMS page census changed or terminated unexpectedly')
   if number<pages and len(p['rows'])!=size or number==pages and len(p['rows'])>size:raise SourceError('PARSER_ERROR','iCIMS page row cardinality is inconsistent with exhaustive pagination')
   variants.add(p['variant']);paths.update(p['next'])
   for row in p['rows']:
    if row['native_id'] in seen:raise SourceError('PARSER_ERROR','Repeated iCIMS posting across pages; inventory incomplete')
    seen.add(row['native_id']);found.append(row)
  if len({r['external_id'] for r in found})!=len(found):raise SourceError('PARSER_ERROR','Repeated iCIMS requisition identity within publication')
  return found,pages,variants
 items,pages,variants=census();again,again_pages,_=census()
 signature=lambda rr:[(r['native_id'],r['external_id'],r['title'],r['url']) for r in rr]
 if pages!=again_pages or signature(items)!=signature(again):raise SourceError('PARSER_ERROR','iCIMS complete repeated census differs; closure withheld')
 jobs=[]
 for item in items:
  j=Job(title=item['title'],external_job_id=item['external_id'],locations=item['locations'],countries=item['countries'],department=item['department'],description=item['description'],source_url=item['url'],canonical_apply_url=item['url'])
  if needs_details(j,source):
   final,body,links,sha=page(item['url']+'?in_iframe=1');key=posting_key(final)
   if key!=(host,item['native_id']):raise SourceError('PARSER_ERROR','iCIMS detail changed publication/posting identity')
   ld=[]
   for value in re.findall(r'<script[^>]*type=["\']application/ld\+json["\'][^>]*>(.*?)</script>',body,re.S|re.I):
    try:d=json.loads(value)
    except ValueError:continue
    queue=d if isinstance(d,list) else [d]
    while queue:
     d=queue.pop(0)
     if not isinstance(d,dict):continue
     if d.get('@type')=='JobPosting':ld.append(d)
     queue.extend(d.get('@graph',[]))
   if len(ld)!=1 or not ld[0].get('description') or posting_key(ld[0].get('url',''))!=(host,item['native_id']):raise SourceError('PARSER_ERROR','iCIMS detail lacks exact structured job posting')
   d=ld[0];tree=Tree(body).root;meta=fields(tree)
   if meta.get('id') and meta['id']!=item['external_id']:raise SourceError('PARSER_ERROR','iCIMS detail requisition differs from inventory')
   if d.get('title')!=item['title']:raise SourceError('PARSER_ERROR','iCIMS detail title differs from census')
   j.description=d['description'];j.employment_type=d.get('employmentType','');j.canonical_apply_url=canonical_url(d['url']);locs=[];countries=[]
   for loc in d.get('jobLocation',[]) if isinstance(d.get('jobLocation'),list) else [d.get('jobLocation',{})]:
    addr=loc.get('address',{});label=', '.join(str(addr[k]) for k in ('addressLocality','addressRegion') if addr.get(k) and addr[k]!='UNAVAILABLE');country=addr.get('addressCountry')
    if isinstance(country,dict):country=country.get('name') or country.get('identifier')
    if label:locs.append(label)
    if country and country!='UNAVAILABLE':countries.append(str(country))
   if locs:j.locations=locs
   if countries:j.countries=countries
   # Some public iCIMS schema emits query-time +/- year fallback dates. Only
   # corroborated visible absolute Posted Date fields establish posting dates.
   explicit=meta.get('posted date','');m=re.search(r'\b(\d{1,2}/\d{1,2}/\d{4})\b',explicit)
   visible=date(explicit)
   if m:
    try:visible=date(datetime.strptime(m[1],'%m/%d/%Y').date().isoformat())
    except ValueError:pass
   if visible and (not d.get('datePosted') or date(d['datePosted']) and date(d['datePosted'])[:10]==visible[:10]):j.posted_at=visible;j.posted_at_confidence='EMPLOYER_EXPLICIT';j._publication_evidence=dict(raw=explicit,precision='DATE',timezone_verified=False)
  jobs.append(j.clean())
 snapshot=Snapshot(jobs);snapshot.inventory_evidence=dict(provider='icims',variants=sorted(variants),complete_passes=2,declared_pages=pages,unique_requisitions=len(jobs),pages=evidence);return snapshot
