import unittest,json,tempfile
from pathlib import Path
from scout.db import connect,company,source
from scout.source_roles import set_role,review_scope
from scout.scan_queue import sync,claim,finish,scan
from scout.sources.base import Snapshot,SourceError
from scout.model import Job
from scout.recovery import inspect,apply_inspection,certify
class QueueTests(unittest.TestCase):
 def setUp(self):
  self.db=connect(':memory:');self.addCleanup(self.db.close);self.cid=company(self.db,'Fixture Employer');self.sid=source(self.db,self.cid,'greenhouse','fixture','https://job-boards.greenhouse.io/fixture')
  with self.db:
   set_role(self.db,self.sid,'AUTHORITATIVE',dict(official_url='https://example.com/careers',reason='Explicit first-party link'));review_scope(self.db,self.cid,[self.sid],dict(official_url='https://example.com/careers',scope_reason='Corporate and student sources inspected'))
 def test_leases_recover_and_cannot_overlap(self):
  t,rows=claim(self.db,at='2026-11-03T12:00:00+00:00',lease_seconds=60);self.assertEqual(len(rows),1)
  self.assertFalse(claim(self.db,at='2026-11-03T12:00:30+00:00')[1]);new,rows=claim(self.db,at='2026-11-03T12:01:01+00:00');self.assertEqual(len(rows),1)
  with self.assertRaises(RuntimeError):finish(self.db,self.sid,t,'SUCCESS')
  finish(self.db,self.sid,new,'TIMEOUT',at='2026-11-03T12:02:00+00:00');r=self.db.execute('select * from scan_queue').fetchone();self.assertEqual(r['attempts'],1);self.assertIsNone(r['lease_token'])
 def test_queue_scales_past_55_and_bounds_work(self):
  with self.db:
   for n in range(70):
    cid=company(self.db,'Firm '+str(n));sid=source(self.db,cid,'lever','firm'+str(n),'https://jobs.lever.co/firm'+str(n));set_role(self.db,sid,'AUTHORITATIVE',dict(official_url='https://example.com',reason='Fixture official'));review_scope(self.db,cid,[sid],dict(official_url='https://example.com',scope_reason='Fixture reviewed'))
  token,rows=claim(self.db,limit=10);self.assertEqual(len(rows),10);self.assertEqual(self.db.execute('select count(*) from scan_queue').fetchone()[0],71)
 def test_success_persists_and_failure_cannot_close(self):
  j=Job(title='Finance Intern',external_job_id='R1',locations=['New York, NY'],canonical_apply_url='https://example.com/R1',source_url='https://example.com/R1')
  r=scan(self.db,fetcher=lambda _:Snapshot([j]),limit=1);self.assertEqual(r['success'],1);first=self.db.execute('select first_seen_at from jobs').fetchone()[0]
  with self.db:self.db.execute("update scan_queue set due_at='2000-01-01T00:00:00+00:00'")
  def fail(_):raise SourceError('PARSER_ERROR','Partial pagination')
  r=scan(self.db,fetcher=fail,limit=1);self.assertEqual(r['failed'],1);j=self.db.execute('select * from jobs').fetchone();self.assertEqual(j['active'],1);self.assertEqual(j['first_seen_at'],first)
 def test_multiple_boards_required_and_uncertain_surface_blocks(self):
  result=dict(company_id=self.cid,attempted_at='2026-11-03',attempts=[dict(url='https://example.com/careers',error='Campus inaccessible')],configured_sources=[self.sid],unresolved_surfaces=[],unvisited_recruiting_urls=[])
  from scout.ingest import run_source
  source_record=dict(self.db.execute('select * from sources where id=?',(self.sid,)).fetchone());run_source(self.db,source_record,lambda _:Snapshot([]))
  certify(self.db,result);self.assertEqual(result['outcome'],'SOURCE_RESOLVED_SCOPE_INCOMPLETE')
 def test_official_handoff_and_student_board_are_discovered(self):
  pages={'https://example.com/careers':('https://example.com/careers','', [('https://job-boards.greenhouse.io/fixture','Search jobs',{}),('/students','Students',{})],'a'),'https://example.com/students':('https://example.com/students','',[('https://jobs.lever.co/campus','Apply',{})],'b')}
  def get(url,**kwargs):
   if url in pages:return pages[url]
   return url,'',[],'native'
  r=inspect(dict(id=self.cid,name='Fixture',careers_url='https://example.com/careers',domain=None),[],get=get)
  self.assertEqual({b['provider'] for b in r['boards']},{'greenhouse','lever'});self.assertFalse(r['unvisited_recruiting_urls'])
 def test_conflicting_board_owner_blocks_automatic_certification(self):
  cid=company(self.db,'Other employer');r=dict(company_id=cid,attempted_at='2026-11-03',boards=[dict(provider='greenhouse',identifier='fixture',url='https://job-boards.greenhouse.io/fixture')])
  apply_inspection(self.db,r);self.assertEqual(r['outcome'],'IDENTITY_AMBIGUOUS');self.assertFalse(r['configured_sources'])
class DeliveryTests(unittest.TestCase):
 def test_eligibility_uncertainty_does_not_become_graduate_exclusion(self):
  from scout.eligibility import eligibility
  from scout.model import Job
  cases=[('Finance Intern','Currently pursuing a bachelors degree','VERIFIED_UNDERGRADUATE'),('MBA Finance Intern','', 'VERIFIED_GRADUATE_ONLY'),('SkillBridge Finance Intern','', 'RESTRICTED'),('Business Operations Intern','', 'LIKELY_UNDERGRADUATE'),('Campus Analyst','', 'UNCERTAIN')]
  for title,description,status in cases:self.assertEqual(eligibility(Job(title=title,description=description))['status'],status)
 def test_full_inventory_view_preserves_old_discovery_without_fresh_badge(self):
  from scout.ingest import run_source
  from scout.feed import feed,explain
  from scout.model import Job
  db=connect(':memory:');self.addCleanup(db.close);cid=company(db,'Fixture');sid=source(db,cid,'greenhouse','fixture','https://job-boards.greenhouse.io/fixture')
  with db:set_role(db,sid,'AUTHORITATIVE',dict(official_url='https://example.com',reason='Fixture linked board'))
  s=dict(db.execute('select * from sources where id=?',(sid,)).fetchone());job=Job(title='Finance Intern',locations=['New York, NY'],external_job_id='1',source_url='https://example.com/job',canonical_apply_url='https://example.com/job')
  run_source(db,s,lambda _:Snapshot([job]),'2026-10-01T12:00:00+00:00');run_source(db,s,lambda _:Snapshot([job]),'2026-11-03T12:00:00+00:00')
  from datetime import datetime
  at=datetime.fromisoformat('2026-11-03T13:00:00+00:00');self.assertEqual(feed(db,'7d',at,True)['total'],0);rows=feed(db,'inventory',at,True)['jobs'];self.assertEqual(len(rows),1);self.assertFalse(rows[0]['new_to_scout']);self.assertEqual(rows[0]['first_seen_at'],'2026-10-01T12:00:00+00:00');self.assertIn('OUTSIDE_RECENT_DISCOVERY_AND_POSTING_WINDOWS',explain(db,rows[0]['scout_job_id'],at))
class CapacityTests(unittest.TestCase):
 setUp=QueueTests.setUp
 def test_capacity_cutoff_releases_claims_for_next_run(self):
  r=scan(self.db,max_seconds=0,fetcher=lambda _:self.fail('Should not start work'));self.assertEqual(r['attempted'],0);self.assertEqual(len(claim(self.db,limit=1)[1]),1)
 def test_queue_and_job_history_survive_reopen(self):
  with tempfile.TemporaryDirectory() as d:
   path=Path(d)/'state.sqlite3';db=connect(path);cid=company(db,'Durable');sid=source(db,cid,'lever','durable','https://jobs.lever.co/durable')
   with db:
    set_role(db,sid,'AUTHORITATIVE',dict(official_url='https://example.com',reason='Fixture'));review_scope(db,cid,[sid],dict(official_url='https://example.com',scope_reason='Fixture'))
   token,rows=claim(db,at='2026-11-03T12:00:00+00:00',lease_seconds=60);db.close();db=connect(path)
   try:self.assertEqual(len(claim(db,at='2026-11-03T12:01:01+00:00')[1]),1)
   finally:db.close()

 def test_provider_caps_do_not_starve_other_provider(self):
  import threading,time
  with self.db:
   for n,provider in enumerate(('greenhouse','greenhouse','lever')):
    cid=company(self.db,'Additional '+str(n));sid=source(self.db,cid,provider,'fixture'+str(n),'https://example.com/'+str(n));set_role(self.db,sid,'AUTHORITATIVE',dict(official_url='https://example.com',reason='Fixture'));review_scope(self.db,cid,[sid],dict(official_url='https://example.com',scope_reason='Fixture'))
  lock=threading.Lock();counts={};peaks={}
  def fetcher(s):
   p=s['ats_type']
   with lock:counts[p]=counts.get(p,0)+1;peaks[p]=max(peaks.get(p,0),counts[p])
   time.sleep(.01)
   with lock:counts[p]-=1
   return Snapshot([])
  r=scan(self.db,workers=3,provider_limit=1,fetcher=fetcher);self.assertEqual(r['success'],4);self.assertEqual(peaks,{'greenhouse':1,'lever':1})
class ProviderRecoveryTests(unittest.TestCase):
 def test_utility_and_escaped_paths_are_not_recruiting_boards(self):
  from scout.sources import detect
  for u in ('https://careers.smartrecruiters.com/cdn-cgi','https://careers.smartrecruiters.com/external-referrals','https://jobs.lever.co/fixture'+chr(92)):self.assertEqual(detect(u)[0],'custom')
 def test_cosmetic_phenom_pages_share_explicit_configuration_identity(self):
  from scout.career_mapping import discover_public_sources
  cfg=dict(widgetApiEndpoint='https://example.com/widgets',baseUrl='https://example.com/us/en/',locale='en_us',country='us');body='phApp = phApp || '+json.dumps(cfg)+';'
  a=discover_public_sources('https://example.com/us/en/benefits',body,[]);b=discover_public_sources('https://example.com/us/en/students',body,[])
  self.assertEqual(a[0]['identifier'],b[0]['identifier']);self.assertEqual(a[0]['url'],'https://example.com/us/en')
 def test_quant_trading_retained_and_research_uncertainty_visible(self):
  from scout.classify import classify
  from scout.model import Job
  from scout.public_site import business_candidate
  d=classify(Job(title='Quantitative Trading Intern',locations=['New York, NY']));self.assertTrue(d['retain']);self.assertEqual(d['function'],'FINANCE');self.assertTrue(business_candidate(dict(title='Quantitative Research Intern',category='OTHER')))
class RetryTests(unittest.TestCase):
 setUp=QueueTests.setUp
 def test_server_retry_after_is_not_shortened(self):
  token,_=claim(self.db,at='2026-11-03T12:00:00+00:00')
  finish(self.db,self.sid,token,'BLOCKED',at='2026-11-03T12:01:00+00:00',retry_after_seconds=172800)
  self.assertEqual(self.db.execute('select due_at from scan_queue').fetchone()[0],'2026-11-05T12:01:00+00:00')
class PublicationTests(unittest.TestCase):
 setUp=QueueTests.setUp
 def test_failed_export_preserves_prior_feed_and_durable_scan(self):
  from scout.public_site import build
  from unittest.mock import patch
  j=Job(title='Finance Intern',external_job_id='1',locations=['New York, NY'],source_url='https://example.com/job',canonical_apply_url='https://example.com/job')
  scan(self.db,limit=1,fetcher=lambda _:Snapshot([j]))
  with tempfile.TemporaryDirectory() as d:
   p=Path(d)/'feed.json';p.write_text('{"sentinel":true}')
   with patch('scout.public_site.feed',side_effect=ValueError('Export failed')):
    with self.assertRaises(ValueError):build(self.db,p)
   self.assertEqual(p.read_text(),'{"sentinel":true}');self.assertEqual(self.db.execute('select count(*) from crawl_runs').fetchone()[0],1)
class LargeInventoryTests(unittest.TestCase):
 def test_corrobated_large_root_partitions_before_unstable_offsets(self):
  from scout.sources.workday_inventory import enumerate_inventory
  def get(url,p):
   if not p['appliedFacets']:
    self.assertEqual(p['offset'],0)
    return dict(total=2101,jobPostings=[dict(title='Role',externalPath='/job/0')],facets=[dict(facetParameter=k,values=[dict(id='a',count=1000),dict(id='b',count=1101)]) for k in ('timeType','jobFamilyGroup')])
   b=next(iter(p['appliedFacets'].values()))[0];count=1000 if b=='a' else 1101;start=0 if b=='a' else 1000;offset=p['offset']
   return dict(total=count if offset==0 else 0,jobPostings=[dict(title='Role',externalPath='/job/'+str(start+i)) for i in range(offset,min(offset+20,count))])
  self.assertEqual(len(enumerate_inventory('https://example.com',get)),2101)
class NativeIdentityTests(unittest.TestCase):
 def test_dedicated_native_apply_url_deduplicates_provider_publications_and_preserves_id(self):
  from scout.ingest import run_source,consolidate_native_apply_duplicates
  db=connect(':memory:');self.addCleanup(db.close);cid=company(db,'Fixture Identity');sources=[]
  for typ,identifier,url in [('workday','firm/External','https://firm.wd1.myworkdayjobs.com/External'),('phenom','https://careers.example.com','https://careers.example.com')]:
   sid=source(db,cid,typ,identifier,url)
   with db:set_role(db,sid,'AUTHORITATIVE',dict(official_url=url,reason='Fixture complete publications'))
   sources.append(dict(db.execute('select * from sources where id=?',(sid,)).fetchone()))
  url='https://firm.wd1.myworkdayjobs.com/en-US/External/job/NY/Finance-Intern_JR1/apply';a=Job(title='Finance Intern',external_job_id='JR1',locations=['New York, NY'],source_url=url,canonical_apply_url=url);b=Job(title='Finance Intern',external_job_id='PHENOM1',locations=['New York, NY'],source_url='https://careers.example.com/job/PHENOM1',canonical_apply_url=url)
  run_source(db,sources[0],lambda _:Snapshot([a]),'2026-10-01T12:00:00+00:00');run_source(db,sources[1],lambda _:Snapshot([b]),'2026-11-03T12:00:00+00:00');self.assertEqual(len(consolidate_native_apply_duplicates(db)),1)
  canonical_id=db.execute('select canonical_job_id from job_merges').fetchone()[0]
  with db:db.execute('delete from job_merges') # Scanner seed may retain canonical memberships without duplicate history rows.
  run_source(db,sources[1],lambda _:Snapshot([b]),'2026-11-04T12:00:00+00:00');canonical=db.execute('select * from jobs where id=?',(canonical_id,)).fetchone();self.assertEqual(canonical['external_job_id'],'JR1');self.assertEqual(canonical['first_seen_at'],'2026-10-01T12:00:00+00:00');self.assertEqual(db.execute('select count(distinct job_id) from source_inventory').fetchone()[0],1)

class StrictScopeTests(unittest.TestCase):
 setUp=QueueTests.setUp
 def test_health_label_without_complete_inventory_cannot_certify(self):
  with self.db:self.db.execute("update sources set health_status='SUCCESS',last_successful_check_at='2026-11-03T12:00:00+00:00' where id=?",(self.sid,))
  r=dict(company_id=self.cid,attempts=[dict(url='https://example.com/careers')],configured_sources=[self.sid],unresolved_surfaces=[],unvisited_recruiting_urls=[])
  certify(self.db,r);self.assertEqual(r['outcome'],'SUPPORTED_PROVIDER_CONFIGURATION_FAILED')
 def test_known_custom_jobs_page_blocks_an_otherwise_complete_native_board(self):
  def get(url,**kw):
   if 'example.com' in url:return url,'<title>Jobs | Fixture</title>',[('/job-area/corporate/','Corporate',{})],'fixture'
   return url,'',[],'board'
  r=inspect(dict(id=self.cid,name='Fixture',careers_url='https://example.com/jobs/',domain=None),[dict(self.db.execute('select * from sources').fetchone())],get=get,max_pages=3)
  self.assertTrue(r['unresolved_surfaces']);self.assertEqual(r['unresolved_surfaces'][0]['url'],'https://example.com/jobs/')
 def test_each_known_authoritative_board_requires_its_own_complete_census(self):
  from scout.ingest import run_source
  sid=source(self.db,self.cid,'lever','students','https://jobs.lever.co/students')
  with self.db:set_role(self.db,sid,'AUTHORITATIVE',dict(official_url='https://example.com/students',reason='Distinct student board'))
  first=dict(self.db.execute('select * from sources where id=?',(self.sid,)).fetchone());second=dict(self.db.execute('select * from sources where id=?',(sid,)).fetchone());run_source(self.db,first,lambda _:Snapshot([]))
  r=dict(company_id=self.cid,attempts=[dict(url='https://example.com/careers')],configured_sources=[self.sid,sid],unresolved_surfaces=[],unvisited_recruiting_urls=[]);certify(self.db,r);self.assertNotEqual(r['outcome'],'SOURCE_RESOLVED_AND_VALIDATED')
  run_source(self.db,second,lambda _:Snapshot([]));certify(self.db,r);self.assertEqual(r['outcome'],'SOURCE_RESOLVED_AND_VALIDATED')
class PartialBoardSchedulingTests(unittest.TestCase):
 setUp=QueueTests.setUp
 def test_complete_partial_board_is_scheduled_without_employer_certification(self):
  from scout.ingest import run_source
  with self.db:self.db.execute("update recruiting_scope_reviews set state='UNRESOLVED' where company_id=?",(self.cid,))
  s=dict(self.db.execute('select * from sources where id=?',(self.sid,)).fetchone());run_source(self.db,s,lambda _:Snapshot([]));self.assertIn(self.sid,sync(self.db));self.assertEqual(self.db.execute('select state from recruiting_scope_reviews').fetchone()[0],'UNRESOLVED')
 def test_incomplete_unproven_board_is_not_scheduled(self):
  from scout.ingest import run_source
  with self.db:self.db.execute("update recruiting_scope_reviews set state='UNRESOLVED' where company_id=?",(self.cid,))
  s=dict(self.db.execute('select * from sources where id=?',(self.sid,)).fetchone());run_source(self.db,s,lambda _:Snapshot([],authoritative=False));self.assertNotIn(self.sid,sync(self.db))
class CheckpointTests(unittest.TestCase):
 def test_checkpoint_includes_committed_wal_while_writer_remains_open(self):
  import gzip,sqlite3
  from scripts.checkpoint_shared_state import checkpoint
  with tempfile.TemporaryDirectory() as d:
   source_path=Path(d)/'source.sqlite3';db=connect(source_path)
   try:
    cid=company(db,'Checkpoint');source(db,cid,'lever','checkpoint','https://jobs.lever.co/checkpoint');db.commit();out=Path(d)/'state.gz';checkpoint(source_path,out);restored=Path(d)/'restored.sqlite3';restored.write_bytes(gzip.decompress(out.read_bytes()));r=sqlite3.connect(restored)
    try:self.assertEqual(r.execute('select count(*) from sources').fetchone()[0],1);self.assertEqual(r.execute('pragma integrity_check').fetchone()[0],'ok')
    finally:r.close()
   finally:db.close()
 def test_missing_state_cannot_replace_a_prior_checkpoint(self):
  from scripts.checkpoint_shared_state import checkpoint
  with tempfile.TemporaryDirectory() as d:
   out=Path(d)/'state.gz';out.write_bytes(b'previous-good-state')
   with self.assertRaises(ValueError):checkpoint(Path(d)/'missing.sqlite3',out)
   self.assertEqual(out.read_bytes(),b'previous-good-state')
class HtmlRateLimitTests(unittest.TestCase):
 def test_html_and_json_requests_preserve_server_retry_after(self):
  from scout.sources.base import get_json,html_page,SourceError
  from urllib.error import HTTPError
  from email.message import Message
  from unittest.mock import patch
  headers=Message();headers['Retry-After']='172800'
  for function,target in ((get_json,'urllib.request.urlopen'),(html_page,'scout.career_mapping.get_page')):
   with patch(target,side_effect=HTTPError('https://example.com',429,'limited',headers,None)):
    with self.assertRaises(SourceError) as caught:function('https://example.com')
   self.assertEqual(caught.exception.retry_after_seconds,172800);caught.exception.__cause__.close()
