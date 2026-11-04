"""Checkpointed official-source recovery using existing evidence, adapters and durable scheduler."""
import argparse,json,time,hashlib
from pathlib import Path
from datetime import datetime,timezone,timedelta
from concurrent.futures import ThreadPoolExecutor,as_completed,wait,FIRST_COMPLETED
from scout.db import connect,canonical_id
from scout.entities import name_key,domain_key
from scout.domain_evidence import DomainEvidenceIndex,official_home
from scout.discovery_cache import DiscoveryCache
from scout.recovery import inspect,apply_inspection,certify,consolidate_phenom_contracts
from scout.sources import fetch,ADAPTERS
from scout.sources.base import request_budget,request_metrics
from scout.ingest import run_source
from scout.reconcile import prepare_source
from scout.model import now
from scout.scan_queue import sync

def arguments():
 p=argparse.ArgumentParser();p.add_argument('--plan-only',action='store_true');p.add_argument('--limit',type=int,default=25);p.add_argument('--pages',type=int,default=12);p.add_argument('--workers',type=int,default=6);p.add_argument('--resume');p.add_argument('--force-live',action='store_true');p.add_argument('--db',default='data/scout.sqlite3');p.add_argument('--retry-company-id',type=int,action='append',default=[]);p.add_argument('--only-company-id',type=int,action='append',default=[]);p.add_argument('--tiers',default='1,2,3');p.add_argument('--output',default='reports/scale-250');p.add_argument('--holdout-digests');p.add_argument('--holdout',default='data/benchmarks/scale250-holdout/manifest.json');return p.parse_args()

def main():
 a=arguments();c=connect(a.db);out=Path(a.output);out.mkdir(parents=True,exist_ok=True);started=now();clock=time.monotonic();batch=str(time.time_ns());prefix=out/('recovery-'+batch)
 c.execute('create table if not exists source_recovery_attempts(id integer primary key,company_id integer not null references companies(id),attempted_at text not null,outcome text not null,evidence text not null)');c.commit()
 held=json.loads(Path(a.holdout).read_text())['observations'];excluded={r.get('baseline_company_id',r.get('company_id')) for r in held};names={name_key(r['employer']) for r in held};heldkeys=set()
 for r in held:
  if r.get('provider') and r.get('identifier'):heldkeys.add((r['provider'],r['identifier'].casefold()))
 if (out/'holdout-initial.json').exists():
  for r in json.loads((out/'holdout-initial.json').read_text()):
   for b in r.get('boards',[]):heldkeys.add((b['provider'],b['identifier'].casefold()))
 reserved=json.loads(Path(a.holdout_digests).read_text()) if a.holdout_digests else {};reserved_ids=set(reserved.get('company_ids',[]));reserved_boards=set(reserved.get('board_keys',[]))
 index=DomainEvidenceIndex(c);cache=DiscoveryCache(c);all_sources={};latest={};past_urls={};allrecords=[]
 for s in c.execute('select * from sources where active=1'):all_sources.setdefault(canonical_id(c,s['company_id']),[]).append(dict(s))
 for row in c.execute('select * from source_recovery_attempts order by id'):
  r=json.loads(row['evidence']);latest[row['company_id']]=r;allrecords.append(r);past_urls.setdefault(row['company_id'],set()).update(x['url'].rstrip('/').lower().replace('://www.','://') for x in r.get('attempts',[]));past_urls[row['company_id']].update(x['profile'].rstrip('/').lower().replace('://www.','://') for x in r.get('profile_evidence',[]) if x.get('profile'))
 hints=json.loads(Path('config/recovery_evidence_hints.json').read_text());tiers={int(t) for t in a.tiers.split(',')};companies=[]
 for row in c.execute('select * from companies where active=1 and canonical_company_id is null'):
  r=dict(row);cid=r['id'];ss=all_sources.get(cid,[]);entries=index.entries(cid);profiles=index.profiles[cid]
  if hashlib.sha256(str(cid).encode()).hexdigest() in reserved_ids:continue
  if a.only_company_id and cid not in a.only_company_id:continue
  if r['coverage_tier'] not in tiers or cid in excluded or name_key(r['name']) in names:continue
  if cid not in a.retry_company_id and c.execute("select 1 from recruiting_scope_reviews where company_id=? and state='VALIDATED'",(cid,)).fetchone():continue
  if not entries and not profiles and not any(s['careers_url'] for s in ss):continue
  urls={e['url'].rstrip('/').lower().replace('://www.','://') for e in entries}|{e['url'].rstrip('/').lower().replace('://www.','://') for e in profiles};new=urls-past_urls.get(cid,set());old=latest.get(cid)
  retry=cid in a.retry_company_id or old and old['outcome']=='TEMPORARY_ACCESS_FAILURE' and datetime.fromisoformat(old['attempted_at'])<datetime.now(timezone.utc)-timedelta(days=7)
  if old and not retry and (old.get('domain_input_signature')==index.signature(cid) or not new):continue
  relevant=c.execute('select count(*) from recruiting_evidence where company_id=?',(cid,)).fetchone()[0];supported=any(s['ats_type'] in ADAPTERS for s in ss);verified=any(e['kind'] in ('VERIFIED_DOMAIN_RECORD','PUBLISHER_OR_REGULATORY_DOMAIN','EXPLICIT_WEBSITE_FIELD') for e in entries)
  r.update(_domain_entries=entries,_identity_names=sorted(index.names[cid]),_domain_signature=index.signature(cid),_new_evidence_urls=sorted(new),_priority=(r['coverage_tier'],not supported,not verified,-min(relevant,10),-r['coverage_score'],cid));companies.append(r)
 selected=sorted(companies,key=lambda r:r['_priority'])[:a.limit];(out/('recovery-plan-'+batch+'.json')).write_text(json.dumps(selected,indent=2))
 if a.plan_only:
  print(json.dumps(dict(candidates=len(companies),selected=[dict(id=r['id'],name=r['name'],tier=r['coverage_tier']) for r in selected])));c.close();return
 results=[];eligible_before=sync(c)
 from scout.universe_qa import health_report
 healthy_before={e['company_id'] for e in health_report(c)['employers'] if e['healthy']};jobs_before={r[0] for r in c.execute('select id from jobs')}
 certified_before={r[0] for r in c.execute("select company_id from recruiting_scope_reviews where state='VALIDATED'")}
 def work(company):
  cid=company['id'];events=[];getter=cache.getter(events);entries=company['_domain_entries'];profile_evidence=[]
  with request_metrics() as cost:
   # Publisher websites are used only when a company lacks a usable official domain,
   # or prior recorded domains failed to resolve a supported source.
   have_domain=any(e['kind']!='RECORDED_CAREERS' for e in entries)
   if not have_domain:
    added,profile_evidence=index.enrich_profiles(cid,get=getter);entries=entries+added
   r=inspect(company,all_sources.get(cid,[]),max_pages=a.pages,get=getter,hints=hints.get(str(cid),[]),entries=entries)
   if not r['boards'] and index.profiles[cid] and not profile_evidence:
    added,profile_evidence=index.enrich_profiles(cid,get=getter)
    if added:
     # Reuse pages already inspected during this employer attempt.
     loaded={x['url']:x for x in r['attempts'] if x.get('sha256')}
     def again(url,**kw):
      if url in loaded:
       from scout.career_mapping import Links
       x=loaded[url];h=(Path('data/universe/career_mapping_snapshots')/(x['sha256']+'.html')).read_text(errors='replace');p=Links();p.feed(h);return x.get('final_url',url),h,p.links,x['sha256']
      return getter(url,**kw)
     r=inspect(company,all_sources.get(cid,[]),max_pages=a.pages,get=again,hints=hints.get(str(cid),[]),entries=entries+added)
  r.update(domain_input_signature=company['_domain_signature'],profile_evidence=profile_evidence,discovery_cache=events,discovery_cost=dict(cost),tier=company['coverage_tier'],new_evidence_urls=company['_new_evidence_urls']);cached={e['url']:e.get('original_fetched_at') for e in events if e.get('original_fetched_at')}
  for attempt in r['attempts']:
   if attempt['url'] in cached:attempt['original_fetched_at']=cached[attempt['url']]
  return r
 if a.resume:
  results=[r for r in json.loads(Path(a.resume).read_text()) if not r.get('_persisted')]
  for r in results:
   r['crawls']=[]
   r['checkpoint_discovery_cost']=r.get('discovery_cost',{});r['discovery_cost']=dict(requests=0,response_bytes=0,runtime_seconds=0)
   if any((b['provider'],b['identifier'].casefold()) in heldkeys or hashlib.sha256((b['provider']+'|'+b['identifier'].casefold()).encode()).hexdigest() in reserved_boards for b in r['boards']):r.update(evaluation_excluded=True,configured_sources=[],exclusion_reason='Reserved independent validation board')
   else:
    with c:apply_inspection(c,r);consolidate_phenom_contracts(c,r)
 else:
  with ThreadPoolExecutor(max_workers=a.workers) as pool:
   futures={pool.submit(work,r):r for r in selected}
   for future in as_completed(futures):
    r=future.result();results.append(r);(out/('recovery-inspections-'+batch+'.json')).write_text(json.dumps(results,indent=2))
    if any((b['provider'],b['identifier'].casefold()) in heldkeys or hashlib.sha256((b['provider']+'|'+b['identifier'].casefold()).encode()).hexdigest() in reserved_boards for b in r['boards']):r.update(evaluation_excluded=True,configured_sources=[],outcome='SOURCE_RESOLVED_SCOPE_INCOMPLETE',exclusion_reason='Reserved independent validation board; employer withheld from remediation')
    else:
     with c:apply_inspection(c,r);consolidate_phenom_contracts(c,r)
    r['crawls']=[];print('Inspected',r['employer'],len(r.get('configured_sources',[])),'boards',flush=True)
 todo={}
 for r in results:
  for sid in r.get('configured_sources',[]):
   s=prepare_source(c,c.execute('select * from sources where id=?',(sid,)).fetchone());recent=s.get('last_successful_check_at') and (datetime.now(timezone.utc)-datetime.fromisoformat(s['last_successful_check_at'])).total_seconds()<21600;census=c.execute('select jobs_returned from crawl_runs where source_id=? and authoritative=1 order by id desc limit 1',(sid,)).fetchone();count=c.execute('select count(*) from source_inventory where source_id=? and present=1',(sid,)).fetchone()[0]
   failure_recent=s.get('last_checked_at') and (datetime.now(timezone.utc)-datetime.fromisoformat(s['last_checked_at'])).total_seconds()<21600 and not s['health_status'].startswith('SUCCESS')
   if failure_recent and not a.force_live:r['crawls'].append(dict(source_id=sid,status=s['health_status'],error=s['last_error'],reused_negative_attempt_at=s['last_checked_at']))
   elif recent and not a.force_live and s['health_status'].startswith('SUCCESS') and census and count==census[0]:r['crawls'].append(dict(source_id=sid,status=s['health_status'],jobs_returned=count,reused_last_complete_inventory_at=s['last_successful_check_at']))
   else:todo[sid]=s
 def persist(r):
  with c:
   certify(c,r);c.execute('insert into source_recovery_attempts(company_id,attempted_at,outcome,evidence) values(?,?,?,?)',(r['company_id'],r['attempted_at'],r['outcome'],json.dumps(r)))
  r['_persisted']=True;prefix.with_suffix('.json').write_text(json.dumps(results,indent=2));print(r['employer'],r['outcome'],[(x['source_id'],x['status'],x.get('jobs_returned')) for x in r['crawls']],flush=True)
 for r in results:
  if all(sid not in todo for sid in r.get('configured_sources',[])):persist(r)
 def fetched(s):
  with request_metrics() as metrics:
   try:
    with request_budget(180):value=fetch(s)
   except Exception as e:value=e
  return value,dict(metrics)
 with ThreadPoolExecutor(max_workers=4) as pool:
  pending=list(todo.values());active={};counts={}
  while pending or active:
   for s in pending[:]:
    if len(active)>=4:break
    if counts.get(s['ats_type'],0)>=2:continue
    pending.remove(s);counts[s['ats_type']]=counts.get(s['ats_type'],0)+1;active[pool.submit(fetched,s)]=s
   if not active:break
   done,_=wait(active,return_when=FIRST_COMPLETED)
   for future in done:
    s=active.pop(future);counts[s['ats_type']]-=1;value,metrics=future.result()
    def from_network(_):
     if isinstance(value,Exception):raise value
     return value
    v=run_source(c,s,fetcher=from_network);v['request_cost']=metrics
    if not isinstance(value,Exception) and getattr(value,'inventory_evidence',None):v['inventory_evidence']=value.inventory_evidence
    if not isinstance(value,Exception) and getattr(value,'publication_contract',None):v['publication_contract']=value.publication_contract
    for r in results:
     if s['id'] in r.get('configured_sources',[]):r['crawls'].append(v)
     if not r.get('_persisted') and len(r['crawls'])==len(r.get('configured_sources',[])):persist(r)
 from scout.ingest import consolidate_native_apply_duplicates
 merges=consolidate_native_apply_duplicates(c)
 from scout.scan_queue import apply_cadences
 apply_cadences(c,json.loads(Path('config/scan_policy.json').read_text()))
 eligible_after=sync(c);current={r[0] for r in c.execute("select company_id from recruiting_scope_reviews where state='VALIDATED'")};requests=sum(r['discovery_cost']['requests']+sum(x.get('request_cost',{}).get('requests',0) for x in r['crawls']) for r in results);cost=sum(r['discovery_cost']['response_bytes']+sum(x.get('request_cost',{}).get('response_bytes',0) for x in r['crawls']) for r in results)
 healthy_after={e['company_id'] for e in health_report(c)['employers'] if e['healthy']}
 metrics=dict(cross_provider_merges=len(merges),remaining_evidence_candidates=len(companies)-len(selected),new_healthy_certified_employers=len(healthy_after-healthy_before),lost_healthy_employers=len(healthy_before-healthy_after),new_job_records=sum(r[0] not in jobs_before for r in c.execute('select id from jobs')),new_scheduled_source_ids=sorted(eligible_after-eligible_before),batch_id=batch,started_at=started,finished_at=now(),runtime_seconds=round(time.monotonic()-clock,2),employers_attempted=len(results),official_domains_available=sum(any(e['kind']!='RECORDED_CAREERS' for e in r.get('entry_evidence',[])) for r in results),sources_resolved=sum(bool(r['boards']) for r in results),supported_configurations=len({sid for r in results for sid in r.get('configured_sources',[])}),successful_live_sources=len({x['source_id'] for r in results for x in r['crawls'] if x['status'].startswith('SUCCESS') and not x.get('reused_last_complete_inventory_at')}),new_scope_certifications=len(current-certified_before),new_scheduled_boards=len(eligible_after-eligible_before),requests_initiated=requests,response_bytes=cost,cache_hits=sum(e['cache']!='NETWORK' for r in results for e in r['discovery_cache']))
 (out/('batch-'+batch+'.json')).write_text(json.dumps(metrics,indent=2));print(json.dumps(metrics),flush=True)
 from scout.public_site import build
 try:build(c,'site/data/feed.json')
 except ValueError as e:print('Prior public feed preserved:',e,flush=True)
 c.close()

if __name__=='__main__':main()
