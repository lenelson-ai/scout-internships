import json,unittest,tempfile
from pathlib import Path
from datetime import datetime,timezone
from unittest.mock import patch
from scout.db import connect,company,source
from scout.career_mapping import Links,find_careers,discover_public_sources
from scout.domain_evidence import DomainEvidenceIndex,official_home
from scout.recovery import inspect,navigation_priority,apply_inspection,certify
from scout.sources.base import Snapshot,request_metrics,record_request

class DiscoveryTests(unittest.TestCase):
 def test_domain_is_evidence_not_guessed_slug(self):
  self.assertEqual(official_home('https://www.firm.com/about'),'https://www.firm.com')
  for u in ('https://jobs.lever.co/firm','https://firm.icims.com/jobs','https://linkedin.com/company/firm','https://u:p@firm.com','localhost'):
   self.assertIsNone(official_home(u),u)
 def test_published_script_navigation_and_embedded_campus(self):
  p=Links('https://firm.com');p.feed('''<script>{"links":[{"label":"Careers","url":"https://talent.firm.com/careers"},{"url":"/early-careers"},{"url":"/jobs.js"}]}</script><iframe title="Campus recruiting" src="https://jobs.lever.co/firmcampus"></iframe>''')
  urls=[x[0] for x in p.links];self.assertIn('/early-careers',urls);self.assertIn('https://talent.firm.com/careers',urls);self.assertNotIn('/jobs.js',urls)
  self.assertEqual(discover_public_sources('https://firm.com','',p.links)[0]['provider'],'lever')
 def test_privacy_and_marketing_students_do_not_enter_recruiting_scope(self):
  for path,label in (('/privacy','Internet Privacy Policy'),('/studentloans','Student Loans'),('/blog/intern-success','Intern stories'),('/careers/cookies','Cookies')):
   self.assertIsNone(navigation_priority('https://firm.com'+path,label,True))
  self.assertEqual(navigation_priority('https://firm.com/careers/students','Students',True),0)
  self.assertEqual(navigation_priority('https://firm.com/careers','Careers'),1)
  self.assertFalse(find_careers('https://careers.firm.com',[('/privacy','Privacy',{})]))
 def test_unrelated_script_external_url_not_official_handoff(self):
  p=Links('https://firm.com');p.feed('<script>{"sponsor":"https://unrelated.com/careers"}</script>');self.assertFalse(p.links)
 def test_negative_cache_keeps_original_failure_age(self):
  from scout.discovery_cache import DiscoveryCache
  from scout.sources.base import SourceError
  c=connect(':memory:');self.addCleanup(c.close);cache=DiscoveryCache(c);cache.entries={'https://firm.com':dict(at=datetime.now(timezone.utc).isoformat(),error='HTTP 403')};events=[]
  with patch('scout.discovery_cache.get_page',side_effect=AssertionError('No retry')):
   with self.assertRaises(SourceError):cache.getter(events)('https://firm.com')
  self.assertEqual(events[0]['cache'],'NEGATIVE');self.assertEqual(events[0]['original_fetched_at'],cache.entries['https://firm.com']['at'])
 def test_request_metrics_are_context_local(self):
  record_request()
  with request_metrics() as m:record_request();record_request(20)
  self.assertEqual(m['requests'],1);self.assertEqual(m['response_bytes'],20)
 def test_explicit_publisher_website_is_unambiguous(self):
  c=connect(':memory:');self.addCleanup(c.close);cid=company(c,'Fixture');index=DomainEvidenceIndex(c);index.profiles[cid]=[dict(url='https://publisher.test/profile')]
  def get(url,**kw):return url,'',[('https://firm.com','Website',{}),('https://other.com','Website',{})],'sha'
  entries,evidence=index.enrich_profiles(cid,get);self.assertFalse(entries);self.assertEqual(evidence[0]['status'],'NO_UNAMBIGUOUS_WEBSITE_FIELD')
 def test_merged_owner_domain_is_reused(self):
  c=connect(':memory:');self.addCleanup(c.close);cid=company(c,'Canonical');old=company(c,'Historical Name')
  with c:c.execute('update companies set canonical_company_id=?,domain=? where id=?',(cid,'firm.com',old))
  index=DomainEvidenceIndex(c);self.assertIn('https://firm.com',[x['url'] for x in index.entries(cid)])
 def test_known_campus_provider_blocks_certification(self):
  c=connect(':memory:');self.addCleanup(c.close);cid=company(c,'Fixture');sid=source(c,cid,'lever','firm','https://jobs.lever.co/firm')
  from scout.ingest import run_source
  from scout.source_roles import set_role
  with c:set_role(c,sid,'AUTHORITATIVE',dict(official_url='https://firm.com/careers',reason='Fixture'))
  run_source(c,dict(c.execute('select * from sources').fetchone()),lambda _:Snapshot([]))
  r=dict(company_id=cid,attempts=[dict(url='https://firm.com/careers')],configured_sources=[sid],unsupported_providers=['avature'],unresolved_surfaces=[],unvisited_recruiting_urls=[])
  certify(c,r);self.assertNotEqual(r['outcome'],'SOURCE_RESOLVED_AND_VALIDATED')
 def test_inspection_stops_at_unsupported_board(self):
  calls=[]
  def get(u,**kw):
   calls.append(u)
   return u,'',[('/jobs/more','More careers',{})],'sha'
  r=inspect(dict(id=1,name='Fixture',careers_url='https://career.successfactors.com/career?company=fixture',domain=None),[],get=get)
  self.assertEqual(len(calls),1);self.assertTrue(r['unsupported_providers'])

class OpaqueWorkdayTests(unittest.TestCase):
 def test_exact_public_search_can_repair_missing_path_without_guessing(self):
  from scout.sources.workday import fetch
  calls=[]
  def get(url,p=None):
   calls.append(p)
   if p['searchText']:return dict(total=1,jobPostings=[dict(title='Staff Engineer',externalPath='/job/NY/Staff-Engineer_R1',bulletFields=['R1'])])
   return dict(total=1,jobPostings=[dict(bulletFields=['R1'])])
  snapshot=fetch(dict(careers_url='https://firm.wd1.myworkdayjobs.com/External'),get=get)
  self.assertEqual(len(snapshot.jobs),1);self.assertEqual(snapshot.jobs[0].external_job_id,'R1');self.assertIn('/job/NY/Staff-Engineer_R1',snapshot.jobs[0].source_url)
 def test_ambiguous_or_unavailable_lookup_fails_entire_inventory(self):
  from scout.sources.workday import fetch
  from scout.sources.base import SourceError
  for matches in ([],[dict(title='Staff Engineer',externalPath='/job/a',bulletFields=['R1']),dict(title='Staff Engineer',externalPath='/job/b',bulletFields=['R1'])]):
   def get(url,p=None):return dict(total=1,jobPostings=matches if p['searchText'] else [dict(bulletFields=['R1'])])
   with self.assertRaises(SourceError):fetch(dict(careers_url='https://firm.wd1.myworkdayjobs.com/External'),get=get)

class ResumeTests(unittest.TestCase):
 def test_preconfiguration_inspection_checkpoint_resumes_without_network(self):
  from scripts.recover_monitoring import main
  from scout.ingest import run_source
  from scout.source_roles import set_role
  with tempfile.TemporaryDirectory() as directory:
   root=Path(directory);dbpath=root/'state.sqlite3';c=connect(dbpath);cid=company(c,'Resume Firm');sid=source(c,cid,'lever','resumefirm','https://jobs.lever.co/resumefirm')
   with c:set_role(c,sid,'AUTHORITATIVE',dict(official_url='https://firm.com/careers',reason='Fixture handoff'))
   run_source(c,dict(c.execute('select * from sources').fetchone()),lambda _:Snapshot([]));c.close()
   checkpoint=root/'inspection.json';checkpoint.write_text(json.dumps([dict(company_id=cid,employer='Resume Firm',attempted_at=datetime.now(timezone.utc).isoformat(),boards=[dict(provider='lever',identifier='resumefirm',url='https://jobs.lever.co/resumefirm')],attempts=[dict(url='https://firm.com/careers')],unresolved_surfaces=[],unvisited_recruiting_urls=[],discovery_cache=[],discovery_cost=dict(requests=8,response_bytes=10))]))
   hold=root/'holdout.json';hold.write_text('{"observations":[]}')
   args=['recover','--db',str(dbpath),'--output',str(root/'out'),'--holdout',str(hold),'--resume',str(checkpoint)]
   with patch('sys.argv',args),patch('scripts.recover_monitoring.fetch',side_effect=AssertionError('Recent census should be reused')),patch('scout.public_site.build'),patch('builtins.print'):main()
   c=connect(dbpath)
   try:
    self.assertEqual(c.execute('select state from recruiting_scope_reviews').fetchone()[0],'VALIDATED');self.assertEqual(c.execute('select source_id from scan_queue').fetchone()[0],sid)
    batch=json.loads(next((root/'out').glob('batch-*.json')).read_text());self.assertEqual(batch['requests_initiated'],0)
   finally:c.close()

class EightfoldDiscoveryTests(unittest.TestCase):
 def test_published_domain_is_required_for_vanity_configuration(self):
  h='<code id="pcsx-data">{&quot;domain&quot;:&quot;firm.com&quot;}</code>'
  boards=discover_public_sources('https://talent.firm.com/careers',h,[])
  self.assertEqual(boards[0]['provider'],'eightfold');self.assertEqual(boards[0]['identifier'],'talent.firm.com|firm.com')
  self.assertFalse(discover_public_sources('https://talent.firm.com/careers','Powered by Eightfold',[]))

class TalentBrewScopeTests(unittest.TestCase):
 def test_filtered_page_cannot_silently_become_global_inventory(self):
  from scout.sources.talentbrew import fetch
  from scout.sources.base import SourceError
  def page(n):return f'<section id="search-results" data-total-job-results="{n}" data-ajax-url="/results"></section>'
  with self.assertRaisesRegex(SourceError,'publication census differs'):
   fetch(dict(careers_url='https://firm.com/students'),get=lambda u:dict(results=page(100)),page=lambda u:(u,page(5),[],''))

class CadenceTests(unittest.TestCase):
 def test_new_board_registration_uses_canonical_tier_and_activity_policy(self):
  from scout.scan_queue import apply_cadences
  c=connect(':memory:');self.addCleanup(c.close);cid=company(c,'Parent');old=company(c,'Merged historical name');sid=source(c,old,'lever','fixture','https://jobs.lever.co/fixture')
  with c:c.execute('update companies set coverage_tier=2 where id=?',(cid,));c.execute('update companies set canonical_company_id=? where id=?',(cid,old))
  apply_cadences(c,dict(tier_intervals_seconds={'1':43200,'2':86400,'3':86400},inactive_interval_multiplier=2))
  self.assertEqual(c.execute('select interval_seconds from sources where id=?',(sid,)).fetchone()[0],172800)

class ConfigOwnershipTests(unittest.TestCase):
 def test_vanity_provider_ownership_is_not_hidden_by_custom_url_detection(self):
  c=connect(':memory:');self.addCleanup(c.close);a=company(c,'Owner');b=company(c,'Other Employer');source(c,a,'phenom','https://careers.firm.com','https://careers.firm.com')
  r=dict(company_id=b,attempted_at='2026-11-04',boards=[dict(provider='phenom',identifier='https://careers.firm.com',url='https://careers.firm.com')])
  apply_inspection(c,r);self.assertEqual(r['outcome'],'IDENTITY_AMBIGUOUS');self.assertFalse(r['configured_sources'])
 def test_eightfold_publication_hosts_remain_distinct(self):
  from scout.recovery import source_identity
  self.assertNotEqual(source_identity(dict(ats_type='eightfold',ats_identifier='firm.com',careers_url='https://a.firm.com/careers')),source_identity(dict(ats_type='eightfold',ats_identifier='firm.com',careers_url='https://b.firm.com/careers')))

class RetiredQueueTests(unittest.TestCase):
 def test_duplicate_in_an_old_scope_review_is_not_dispatched(self):
  from scout.scan_queue import sync,claim
  from scout.source_roles import set_role,review_scope
  c=connect(':memory:');self.addCleanup(c.close);cid=company(c,'Fixture');a=source(c,cid,'lever','a','https://jobs.lever.co/a');b=source(c,cid,'lever','b','https://jobs.lever.co/b')
  with c:
   for sid in (a,b):set_role(c,sid,'AUTHORITATIVE',dict(official_url='https://firm.com/careers',reason='Fixture'))
   review_scope(c,cid,[a,b],dict(official_url='https://firm.com/careers',scope_reason='Fixture'))
  sync(c)
  with c:set_role(c,b,'DUPLICATE',dict(reason='Captured identical public contract'),a)
  self.assertNotIn(b,sync(c));self.assertEqual([r['source_id'] for r in claim(c)[1]],[a]);self.assertEqual(c.execute('select count(*) from scan_queue').fetchone()[0],2)

class AccessBackoffTests(unittest.TestCase):
 def test_restricted_origin_is_not_hammered_across_employers(self):
  from scout.discovery_cache import DiscoveryCache
  from scout.sources.base import SourceError
  from urllib.error import HTTPError
  c=connect(':memory:');self.addCleanup(c.close);cache=DiscoveryCache(c);cache.entries={};events=[]
  def fail(url,**kw):raise HTTPError(url,403,'Forbidden',{},None)
  with patch('scout.discovery_cache.get_page',side_effect=fail) as get:
   for i in range(3):
    with self.assertRaises(HTTPError) as caught:cache.getter(events)('https://publisher.test/'+str(i))
    caught.exception.close()
   with self.assertRaises(SourceError):cache.getter(events)('https://publisher.test/4')
   self.assertEqual(get.call_count,3);self.assertEqual(events[-1]['cache'],'ORIGIN_ACCESS_BACKOFF')

class FilterContractTests(unittest.TestCase):
 def test_checked_campus_facets_use_published_json_post_contract(self):
  from scout.sources.talentbrew import fetch
  body='<section id="search-filters" data-filtered-categories="1"><input class="filter-checkbox" checked data-id="campus" data-facet-type="1" data-count="0" data-display="Students" data-field-name="category"></section><section id="search-results" data-total-job-results="0" data-ajax-url="/results" data-ajax-post-url="/resultspost" data-refined-keywords="[]"></section>'
  calls=[]
  def get(url,payload=None):calls.append((url,payload));return dict(results=body)
  snap=fetch(dict(careers_url='https://firm.com/students'),get=get,page=lambda u:(u,body,[],''))
  self.assertEqual(snap.jobs,[]);self.assertTrue(snap.publication_contract['filtered'])
  self.assertEqual(calls[0][0],'https://firm.com/resultspost');self.assertEqual(calls[0][1]['FacetFilters'][0]['ID'],'campus');self.assertEqual(calls[0][1]['fc'],'1')

class CorrectedPublicationTests(unittest.TestCase):
 def test_scope_repair_does_not_close_prior_broad_memberships_or_reset_discovery(self):
  from scout.recovery import install_corrected_talentbrew_publication
  from scout.ingest import run_source
  from scout.source_roles import set_role
  from scout.model import Job
  c=connect(':memory:');self.addCleanup(c.close);cid=company(c,'Fixture');sid=source(c,cid,'talentbrew','https://firm.com/students','https://firm.com/students')
  with c:set_role(c,sid,'AUTHORITATIVE',dict(official_url='https://firm.com/careers',reason='Historical fixture'))
  old=dict(c.execute('select * from sources').fetchone());jobs=[Job(title='Finance Intern',external_job_id=str(i),source_url='https://firm.com/job/'+str(i),canonical_apply_url='https://firm.com/job/'+str(i),locations=['New York, NY']) for i in range(10)]
  run_source(c,old,lambda _:Snapshot(jobs),'2026-10-01T12:00:00+00:00');snap=Snapshot(jobs[:1]);snap.publication_contract=dict(filtered=True,url=old['careers_url'],filters=[dict(ID='campus')])
  r=install_corrected_talentbrew_publication(c,old,snap);self.assertTrue(r['status'].startswith('SUCCESS'));self.assertNotEqual(r['source_id'],sid)
  self.assertEqual(c.execute('select source_role from sources where id=?',(sid,)).fetchone()[0],'LEGACY');self.assertEqual(c.execute('select count(*) from source_inventory where source_id=? and present=1',(sid,)).fetchone()[0],10)
  self.assertEqual(c.execute('select count(*) from jobs where active=1').fetchone()[0],10);self.assertEqual({r[0] for r in c.execute('select first_seen_at from jobs')},{'2026-10-01T12:00:00+00:00'})

class NavigationAndDedupTests(unittest.TestCase):
 def test_explicit_full_search_form_is_discovered_without_guessed_paths(self):
  p=Links('https://firm.com/campus');p.feed('<form data-selector-name="searchform" action="/en/search-jobs"></form>')
  self.assertTrue(p.links[0][2]['recruiting_inventory_root']);self.assertEqual(p.links[0][0],'/en/search-jobs')
 def test_recovery_resolves_a_proven_duplicate_without_reactivating_it(self):
  from scout.source_roles import set_role
  c=connect(':memory:');self.addCleanup(c.close);cid=company(c,'Fixture');a=source(c,cid,'talentbrew','https://firm.com/jobs','https://firm.com/jobs');b=source(c,cid,'talentbrew','https://firm.com/jobs/page/2','https://firm.com/jobs/page/2')
  with c:set_role(c,a,'AUTHORITATIVE',dict(official_url='https://firm.com/careers',reason='Fixture'));set_role(c,b,'DUPLICATE',dict(reason='Proven same public request contract'),a)
  r=dict(company_id=cid,attempted_at='2026-11-04',boards=[dict(provider='talentbrew',identifier='https://firm.com/jobs/page/2',url='https://firm.com/jobs/page/2')]);apply_inspection(c,r)
  self.assertEqual(r['configured_sources'],[a]);self.assertEqual(c.execute('select source_role from sources where id=?',(b,)).fetchone()[0],'DUPLICATE')

class OpaqueLookupCompletenessTests(unittest.TestCase):
 def test_one_visible_exact_match_does_not_prove_a_truncated_search_unique(self):
  from scout.sources.workday import fetch
  from scout.sources.base import SourceError
  def get(url,p):return dict(total=25,jobPostings=[dict(title='Staff Engineer',externalPath='/job/a',bulletFields=['R1'])]) if p['searchText'] else dict(total=1,jobPostings=[dict(bulletFields=['R1'])])
  with self.assertRaisesRegex(SourceError,'exhaustive bounded result'):fetch(dict(careers_url='https://firm.wd1.myworkdayjobs.com/External'),get=get)

class UnfilteredTalentBrewCensusTests(unittest.TestCase):
 def test_cached_unfiltered_root_does_not_override_full_consistent_live_census(self):
  from scout.sources.talentbrew import fetch
  def html(n):return f'<section id="search-results" data-total-job-results="{n}" data-ajax-url="/results" data-current-page="1" data-records-per-page="250">'+('' if n==0 else '<li><a href="/job/ny/role/123"><h2>Staff Engineer</h2></a></li>')+'</section>'
  calls=[]
  def get(url):calls.append(url);return dict(results=html(1))
  s=fetch(dict(careers_url='https://firm.com/search-jobs'),get=get,page=lambda u:(u,html(0),[],''))
  self.assertEqual(len(s.jobs),1);self.assertEqual(len(calls),2)
 def test_identical_count_divergence_on_a_category_view_remains_fatal(self):
  from scout.sources.talentbrew import fetch
  from scout.sources.base import SourceError
  h=lambda n:f'<section id="search-results" data-total-job-results="{n}" data-ajax-url="/results"></section>'
  with self.assertRaises(SourceError):fetch(dict(careers_url='https://firm.com/search-jobs?acm=campus'),get=lambda u:dict(results=h(100)),page=lambda u:(u,h(0),[],''))

class LiveRootPostTests(unittest.TestCase):
 def test_explicit_root_post_contract_preserves_unfiltered_live_census(self):
  from scout.sources.talentbrew import fetch
  body='<section id="search-results" data-total-job-results="0" data-ajax-url="/results" data-ajax-post-url="/resultspost" data-search-type="1"></section>'
  calls=[]
  def get(u,p=None):calls.append((u,p));return dict(results=body)
  r=fetch(dict(careers_url='https://firm.com/search-jobs'),get=get,page=lambda u:(u,body,[],''))
  self.assertEqual(r.jobs,[]);self.assertFalse(r.publication_contract['filtered']);self.assertEqual(calls[0][0],'https://firm.com/resultspost');self.assertEqual(calls[0][1]['FacetFilters'],[]);self.assertEqual(calls[0][1]['SearchType'],1)

class TalentBrewPagingProtocolTests(unittest.TestCase):
 def test_pagination_uses_live_total_and_final_nonpagination_recount(self):
  from scout.sources.talentbrew import fetch
  def html(total,page,rid):return f'<section id="search-results" data-total-job-results="{total}" data-ajax-url="/results" data-ajax-post-url="/post" data-total-results="0" data-current-page="{page}" data-records-per-page="1"><li><a href="/job/ny/role/{rid}"><h2>Staff Engineer</h2></a></li></section>'
  calls=[]
  def get(u,p):
   calls.append(dict(p))
   if p['CurrentPage']==2:self.assertEqual(p['TotalResults'],2);self.assertEqual(p['IsPagination'],'True');return dict(results=html(p['TotalResults'],2,'b'))
   self.assertEqual(p['IsPagination'],'False');return dict(results=html(2,1,'a'))
  r=fetch(dict(careers_url='https://firm.com/search-jobs'),get=get,page=lambda u:(u,html(0,1,'old'),[],''))
  self.assertEqual(len(r.jobs),2);self.assertEqual(len(calls),3)
