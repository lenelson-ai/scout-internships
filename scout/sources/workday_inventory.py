"""Conservative capped-CXS partitions with independently corroborated census counts."""
from .base import SourceError,rows,required

SINGLE_VALUE={'jobFamilyGroup','workerSubType','timeType'}
def _enumerate_inventory(endpoint,get):
 def query(facets,offset=0):return get(endpoint+'/jobs',dict(appliedFacets=facets,limit=20,offset=offset,searchText=''))
 root=query({});partitions=0
 def values(page,key):
  fs=[f for f in page.get('facets',[]) if f.get('facetParameter')==key]
  vv=fs[0].get('values',[]) if len(fs)==1 else []
  if not vv or any(not v.get('id') or type(v.get('count')) is not int or v['count']<=0 for v in vv):return []
  if len({v['id'] for v in vv})!=len(vv):return []
  return vv
 def parse(page):
  pp=rows(page,'jobPostings');paths=[]
  from .workday import posting_path
  for p in pp:paths.append(posting_path(required(p,'externalPath')));required(p,'title')
  if len(paths)!=len(set(paths)):raise SourceError('PARSER_ERROR','Repeated Workday postings within page')
  return pp
 def walk(first,facets,declared=None,force_partition=False):
  nonlocal partitions
  total=first.get('total')
  if type(total) is not int or total<0 or total>200000:raise SourceError('PARSER_ERROR','Workday total is missing or invalid')
  corroborated_large=total>2000 and sum(sum(v['count'] for v in values(first,k))==total for k in SINGLE_VALUE if values(first,k))>=2
  if total==2000 or force_partition or corroborated_large:
   sums={k:sum(v['count'] for v in values(first,k)) for k in SINGLE_VALUE if k not in facets and values(first,k)}
   if declared is None and total!=2000:declared=total
   if declared is None:
    # A capped total cannot be trusted. Require two independent single-value
    # provider facets to expose the same full cardinality; otherwise fail closed.
    agreed=[n for n in set(sums.values()) if n>=2000 and list(sums.values()).count(n)>=2]
    if len(agreed)!=1:raise SourceError('PARSER_ERROR','Workday cap suspected: no corroborated exhaustive facet census; closure withheld')
    declared=agreed[0]
   if declared>200000:raise SourceError('PARSER_ERROR','Workday census safety bound exceeded')
   all_sums={f.get("facetParameter"):sum(v["count"] for v in values(first,f.get("facetParameter"))) for f in first.get("facets",[]) if f.get("facetParameter") not in facets and values(first,f.get("facetParameter"))}
   eligible=sorted(k for k,n in all_sums.items() if n==declared and len(values(first,k))>1)
   if not eligible:raise SourceError('PARSER_ERROR','Workday capped partition lacks exhaustive facet split')
   key=min(eligible,key=lambda k:max(v['count'] for v in values(first,k)))
   output=[];seen=set()
   for v in values(first,key):
    partitions+=1
    if partitions>500:raise SourceError('PARSER_ERROR','Workday partition safety limit exceeded')
    child=dict(facets,**{key:[v['id']]});pp=walk(query(child),child,v['count'])
    ids=[p['externalPath'] for p in pp]
    if seen.intersection(ids):raise SourceError('PARSER_ERROR','Workday facet partitions overlap; closure withheld')
    seen.update(ids);output.extend(pp)
   if len(output)!=declared:raise SourceError('PARSER_ERROR','Workday partition union differs from facet census')
   if not {p['externalPath'] for p in parse(first)}.issubset(seen):raise SourceError('PARSER_ERROR','Workday partition union omits unfiltered postings')
   check=query(facets)
   if check.get('total')!=total:raise SourceError('PARSER_ERROR','Workday partition total changed during enumeration')
   if not {p['externalPath'] for p in parse(check)}.issubset(seen):raise SourceError('PARSER_ERROR','Workday final root page contains unenumerated postings')
   for k,n in all_sums.items():
    if {(v['id'],v['count']) for v in values(check,k)}!={(v['id'],v['count']) for v in values(first,k)}:raise SourceError('PARSER_ERROR','Workday facet census changed during enumeration')
   return output
  if declared is not None and total!=declared:raise SourceError('PARSER_ERROR',f'Workday partition count differs from declared facet count: expected {declared}, returned {total}, facets {facets}')
  pp=parse(first);seen={p['externalPath'] for p in pp}
  while len(pp)<total:
   nextpage=query(facets,len(pp));t=nextpage.get('total')
   if type(t) is not int or t not in (0,total):raise SourceError('PARSER_ERROR','Workday total changed during pagination')
   chunk=parse(nextpage)
   if not chunk:raise SourceError('PARSER_ERROR','Workday pagination ended before total')
   for p in chunk:
    if p['externalPath'] in seen:raise SourceError('PARSER_ERROR','Workday repeated posting across pages; snapshot incomplete')
    seen.add(p['externalPath'])
   pp.extend(chunk)
  if len(pp)!=total:raise SourceError('PARSER_ERROR','Workday inventory exceeded total')
  if total:
   check=query(facets);front=parse(check)
   if check.get('total')!=total or not front or [p['externalPath'] for p in front]!=[p['externalPath'] for p in pp[:len(front)]]:raise SourceError('PARSER_ERROR','Workday inventory changed during pagination; closure withheld')
  return pp
 try:return walk(root,{})
 except SourceError as exc:
  if root.get("total")!=2000 and exc.status=="PARSER_ERROR" and any(t in str(exc) for t in ("repeated posting across pages","changed during pagination","pagination ended before")):
   partitions=0
   return walk(query({}),{},force_partition=True)
  raise


def enumerate_inventory(endpoint,get):
 """One fresh census retry for observed churn; never accept differing counts."""
 for attempt in range(2):
  try:return _enumerate_inventory(endpoint,get)
  except SourceError as exc:
   churn=exc.status=='PARSER_ERROR' and 'partition count differs' in str(exc)
   if attempt or not churn:raise
