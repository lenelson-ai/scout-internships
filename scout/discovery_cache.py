"""Bounded public-page cache with retained timestamps and negative retry windows."""
import json,time,threading
from urllib.parse import urlsplit
from urllib.error import HTTPError
from pathlib import Path
from datetime import datetime,timezone,timedelta
from .career_mapping import get_page,Links
from .universe_sources import ROOT
from .sources.base import SourceError
class DiscoveryCache:
 def __init__(self,db,positive_hours=6,negative_hours=24):
  self.blocked_origins={};self.lock=threading.Lock()
  self.entries={};self.positive_hours=positive_hours;self.negative_hours=negative_hours
  p=Path(ROOT)/'data/universe/career_mapping_snapshots/index.jsonl'
  if p.exists():
   for line in p.read_text().splitlines():
    try:r=json.loads(line);self.entries[r['url']]=r
    except ValueError:continue
  if db.execute("select 1 from sqlite_master where name='source_recovery_attempts'").fetchone():
   for row in db.execute('select attempted_at,evidence from source_recovery_attempts order by id'):
    try:r=json.loads(row['evidence'])
    except ValueError:continue
    for a in r.get('attempts',[]):
     old=self.entries.get(a['url']);at=a.get('original_fetched_at') or row['attempted_at']
     if old and old.get('at','')>=at:continue
     self.entries[a['url']]=dict(a,at=at)
 def getter(self,events,budget_seconds=70):
  deadline=time.monotonic()+budget_seconds
  def get(url,**kw):
   r=self.entries.get(url);current=datetime.now(timezone.utc)
   if r and r.get('at'):
    age=(current-datetime.fromisoformat(r['at'])).total_seconds();ttl=(self.negative_hours if r.get('error') else self.positive_hours)*3600
    if age<ttl:
     if r.get('error'):
      events.append(dict(url=url,cache='NEGATIVE',original_fetched_at=r['at']));raise SourceError('CACHED_ACCESS_FAILURE',r['error'])
     if r.get('sha256'):
      p=Path(ROOT)/'data/universe/career_mapping_snapshots'/(r['sha256']+'.html')
      if p.exists():
       h=p.read_text(errors='replace');parser=Links(r.get('final_url',r.get('final',url)));parser.feed(h);events.append(dict(url=url,cache='POSITIVE',original_fetched_at=r['at']));return r.get('final_url',r.get('final',url)),h,parser.links,r['sha256']
   host=urlsplit(url).hostname
   with self.lock:blocked=self.blocked_origins.get(host,0)>=3
   if blocked:
    events.append(dict(url=url,cache='ORIGIN_ACCESS_BACKOFF'))
    raise SourceError('CACHED_ACCESS_FAILURE','Three access-restricted responses from this origin in the batch; further discovery deferred')
   remaining=deadline-time.monotonic()
   if remaining<=0:raise SourceError('TIMEOUT','Discovery budget exhausted; unvisited recruiting surfaces remain unresolved')
   kw['timeout']=min(kw.get('timeout',10),remaining);events.append(dict(url=url,cache='NETWORK'))
   try:return get_page(url,**kw)
   except HTTPError as e:
    if e.code in (401,403,429):
     with self.lock:self.blocked_origins[host]=self.blocked_origins.get(host,0)+1
    raise
  return get
