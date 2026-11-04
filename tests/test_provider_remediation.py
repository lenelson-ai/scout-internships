import unittest,json
from scout.sources import phenom,oracle,talentbrew
from scout.sources.workday import configuration,board_key
from scout.sources.workday_inventory import enumerate_inventory
from scout.sources.base import SourceError
from urllib.parse import parse_qs,urlsplit

class WorkdayRemediationTests(unittest.TestCase):
 def test_workdaysite_alias_keeps_tenant_cluster_board(self):
  url='https://wd1.myworkdaysite.com/en-US/recruiting/fmr/FidelityCareers/login'
  self.assertEqual(board_key(url),board_key('https://fmr.wd1.myworkdayjobs.com/fidelitycareers'))
  self.assertEqual(configuration(dict(careers_url=url))[1: ],('fmr','FidelityCareers'))
 @staticmethod
 def census(overlap=False):
  def get(url,p):
   facet=p['appliedFacets'];offset=p['offset']
   if not facet:return dict(total=2000,jobPostings=[dict(title='Role',externalPath='/job/0')],facets=[dict(facetParameter=k,values=[dict(id='a',count=1000),dict(id='b',count=1001)]) for k in ('workerSubType','jobFamilyGroup')])
   v=next(iter(facet.values()))[0];count=1000 if v=='a' else 1001;start=0 if v=='a' or overlap else 1000
   return dict(total=count if offset==0 else 0,jobPostings=[dict(title='Role',externalPath=f'/job/{start+i}') for i in range(offset,min(count,offset+20))])
  return get
 def test_corroborated_disjoint_full_partition(self):self.assertEqual(len(enumerate_inventory('https://example.com',self.census())),2001)
 def test_overlapping_partitions_rejected(self):
  with self.assertRaises(SourceError):enumerate_inventory('https://example.com',self.census(True))
 def test_uncorroborated_cap_rejected(self):
  with self.assertRaises(SourceError):enumerate_inventory('https://example.com',lambda u,p:dict(total=2000,jobPostings=[],facets=[]))

class ProviderTests(unittest.TestCase):
 def test_phenom_full_inventory_dates_multiple_locations(self):
  cfg=dict(widgetApiEndpoint='https://example.com/widgets',baseUrl='https://example.com/us/en/',locale='en_us',country='us')
  html='var phApp = phApp || '+json.dumps(cfg)+';'
  def get(u,p):
   if p.get('ddoKey')=='jobDetail':return dict(jobDetail=dict(status=200,data=dict(job=dict(jobSeqNo='ID1',description='Undergraduate finance internship'))))
   return dict(refineSearch=dict(status=200,totalHits=1,data=dict(jobs=[dict(jobSeqNo='ID1',jobId='1',title='Finance Intern',multi_location=['New York, NY','Boston, MA'],postedDate='2026-11-01',applyUrl='https://example.com/apply/1')])) )
  r=phenom.fetch(dict(careers_url='https://example.com'),get,lambda u: (u,html,[],''))
  self.assertEqual(len(r.jobs),1);self.assertEqual(len(r.jobs[0].locations),2);self.assertIsNotNone(r.jobs[0].posted_at)
 def test_phenom_failure_is_not_empty(self):
  html='phApp = phApp || '+json.dumps(dict(widgetApiEndpoint='https://example.com/widgets',locale='en_us',country='us'))
  with self.assertRaises(SourceError):phenom.fetch(dict(careers_url='https://example.com'),lambda u,p:dict(status='failure'),lambda u:(u,html,[],''))
 def test_oracle_pagination_preserves_ids_and_unknown_dates(self):
  def get(u):
   
   if '/recruitingCEJobRequisitionDetails/' in u:return dict(Id=u.rsplit('/',1)[1],Title='Finance Intern',ExternalDescriptionStr='Undergraduate finance internship')
   finder=parse_qs(urlsplit(u).query)['finder'][0];offset=int(finder.split('offset=')[1]);part=[dict(Id=str(offset+1),Title='Finance Intern',PrimaryLocation='New York, NY')] if offset<2 else []
   return dict(items=[dict(SiteNumber='CX_1',Offset=offset,TotalJobsCount=2,requisitionList=part)])
  r=oracle.fetch(dict(careers_url='https://example.oraclecloud.com/hcmUI/CandidateExperience/en/sites/CX_1'),get)
  self.assertEqual([j.external_job_id for j in r.jobs],['1','2']);self.assertIsNone(r.jobs[0].posted_at)
 def test_oracle_missing_envelope_rejected(self):
  with self.assertRaises(SourceError):oracle.fetch(dict(careers_url='https://example.oraclecloud.com/hcmUI/CandidateExperience/en/sites/CX_1'),lambda u:dict(items=[]))
 def test_talentbrew_repeated_page_rejected(self):
  html='<section id="search-results" data-total-job-results="2" data-ajax-url="/search-jobs/results" data-current-page="1"><a href="/job/city/title/1/123"><h2>Analyst</h2><span class="job-location">New York</span></a></section>'
  with self.assertRaises(SourceError):talentbrew.fetch(dict(careers_url='https://example.com/search-jobs'),lambda u:dict(results=html),lambda u:(u,html,[],''))
 def test_talentbrew_count_verified_empty(self):
  html='<section id="search-results" data-total-job-results="0" data-ajax-url="/search-jobs/results"></section>'
  self.assertEqual(talentbrew.fetch(dict(careers_url='https://example.com/search-jobs'),lambda u:dict(results=html),lambda u:(u,html,[],'' )).jobs,[])

class FeedTests(unittest.TestCase):
 def test_today_labels_initial_and_later_discovery_without_changing_posted_date(self):
  from scout.db import connect,company,source
  from scout.source_roles import set_role
  from scout.ingest import run_source
  from scout.sources.base import Snapshot
  from scout.model import Job
  from scout.feed import feed
  from datetime import datetime,timezone
  db=connect(':memory:')
  try:
   cid=company(db,'Fixture');sid=source(db,cid,'greenhouse','fixture')
   set_role(db,sid,'AUTHORITATIVE',dict(official_url='https://example.com',reason='Fixture official board'))
   def job(i):return Job(title='Finance Intern',external_job_id=str(i),description='Undergraduate finance internship',locations=['New York, NY'],countries=['US'],source_url=f'https://example.com/job/{i}',posted_at='2026-11-01T12:00:00+00:00')
   ss=lambda:dict(db.execute('SELECT * FROM sources WHERE id=?',(sid,)).fetchone())
   run_source(db,ss(),fetcher=lambda _:Snapshot([job(1)]),stamp='2026-11-03T11:00:00+00:00')
   run_source(db,ss(),fetcher=lambda _:Snapshot([job(1),job(2)]),stamp='2026-11-03T12:00:00+00:00')
   at=datetime(2026,11,3,20,tzinfo=timezone.utc)
   today=feed(db,'today',at);week=feed(db,'7d',at)
   self.assertEqual({r['external_id'] for r in today['jobs']},{'1','2'});self.assertEqual(week['total'],2)
   flags={r['external_id']:r['new_since_previous_successful_inventory'] for r in today['jobs']};self.assertEqual(flags,{'1':False,'2':True})
   self.assertTrue(today['jobs'][0]['new_to_scout']);self.assertFalse(today['jobs'][0]['employer_posted_recently'])
  finally:db.close()

class EightfoldTests(unittest.TestCase):
 def test_publication_id_preserves_multiple_locations_and_detail(self):
  from scout.sources import eightfold
  def get(u):
   if 'position_details' in u:return dict(status=200,data=dict(id=1,jobDescription='Undergraduate finance internship'))
   return dict(status=200,data=dict(count=1,positions=[dict(id=1,name='Finance Intern',postedTs=1793664000,standardizedLocations=['New York, NY, US','Boston, MA, US'],positionUrl='/careers/job/1')]))
  result=eightfold.fetch(dict(careers_url='https://example.com/careers',ats_identifier='example.com'),get)
  self.assertEqual(len(result.jobs[0].locations),2);self.assertIn('Undergraduate',result.jobs[0].description)
 def test_failed_search_not_empty(self):
  from scout.sources import eightfold
  with self.assertRaises(SourceError):eightfold.fetch(dict(careers_url='https://example.com/careers',ats_identifier='example.com'),lambda u:dict(status=403))

class SearchContractTests(unittest.TestCase):
 def test_talentbrew_cached_html_is_not_authoritative_empty(self):
  html='<section id="search-results" data-total-job-results="0" data-ajax-url="/search-jobs/results"></section>'
  with self.assertRaises(SourceError):talentbrew.fetch(dict(careers_url='https://example.com/search-jobs'),lambda u:dict(error='blocked'),lambda u:(u,html,[],''))
 def test_talentbrew_ignores_promoted_jobs_outside_result_section(self):
  html='<a href="/job/city/promo/1/2"><h2>Promo</h2></a><section id="search-results" data-total-job-results="1" data-ajax-url="/results"><a href="/job/city/role/1/3"><h2>Role</h2></a></section>'
  p=talentbrew.Listing(html);self.assertEqual([j['title'] for j in p.jobs],['Role'])

class WorkdaysiteOriginTests(unittest.TestCase):
 def test_workdaysite_uses_official_origin_for_cxs_and_public_url(self):
  from scout.sources import workday
  urls=[]
  def get(url,payload=None):
   urls.append(url);return dict(total=1,jobPostings=[dict(title='Senior Analyst',externalPath='/job/NY/Analyst_R1',bulletFields=['R1'])])
  s=dict(careers_url='https://wd1.myworkdaysite.com/recruiting/example/External')
  result=workday.fetch(s,get)
  self.assertTrue(all(u.startswith('https://wd1.myworkdaysite.com/wday/cxs/example/External') for u in urls))
  self.assertIn('/recruiting/example/External/job/',result.jobs[0].source_url)

class PhenomFacetTests(unittest.TestCase):
 def get(self,missing=False):
  a=dict(jobSeqNo='a',reqId='a',title='A');b=dict(jobSeqNo='b',reqId='b',title='B');c=dict(jobSeqNo='c',reqId='c',title='C')
  def get(url,p):
   selected=p.get('selected_fields',{})
   if not selected:return dict(refineSearch=dict(status=200,totalHits=3,data=dict(jobs=[a],aggregations=[dict(field='category',value={'one':2,'two':2})])))
   jj=[a,b] if selected['category']==['one'] else [b] if missing else [b,c]
   return dict(refineSearch=dict(status=200,totalHits=len(jj),data=dict(jobs=jj)))
  return get
 def test_overlap_deduplicated_against_uncapped_root_count(self):
  n,jobs=phenom.facet_census(dict(locale='en_us',country='us',widgetApiEndpoint='https://example.com/widgets'),self.get())
  self.assertEqual(n,3);self.assertEqual(len(jobs),3)
 def test_missing_facet_publication_rejects_full_inventory(self):
  with self.assertRaises(SourceError):phenom.facet_census(dict(locale='en_us',country='us',widgetApiEndpoint='https://example.com/widgets'),self.get(True))

class DetailPolicyTests(unittest.TestCase):
 def test_pharmacy_training_not_promoted_but_healthcare_finance_remains(self):
  from scout.model import Job
  from scout.classify import classify
  from scout.sources.detail_policy import needs_details
  clinical=Job(title='Pharmacy Intern',locations=['Boston, MA'],countries=['US']).clean()
  finance=Job(title='Pharmacy Finance Intern',locations=['Boston, MA'],countries=['US']).clean()
  self.assertFalse(classify(clinical)['retain']);self.assertFalse(needs_details(clinical,{}));self.assertTrue(needs_details(finance,{}))
 def test_previously_tracked_identity_is_always_hydrated(self):
  from scout.model import Job
  from scout.sources.detail_policy import needs_details
  self.assertTrue(needs_details(Job(title='Pharmacy Intern',external_job_id='OLD'),dict(_legacy_ids=['old'])))

class NetworkSafetyTests(unittest.TestCase):
 def test_rate_limit_is_not_retried_as_a_facet_split(self):
  a=dict(jobSeqNo='a',title='A')
  def get(u,p):
   if p.get('from'):raise SourceError('BLOCKED','Rate limited',429)
   return dict(refineSearch=dict(status=200,totalHits=2,data=dict(jobs=[a],aggregations=[dict(field='category',value={'one':2})] if not p['selected_fields'] else [])))
  with self.assertRaises(SourceError) as cm:phenom.facet_census(dict(locale='en_us',country='us',widgetApiEndpoint='https://example.com'),get)
  self.assertEqual(cm.exception.status,'BLOCKED')
 def test_html_access_denied_is_explicitly_blocked(self):
  from unittest.mock import patch
  from urllib.error import HTTPError
  from scout.sources.base import html_page
  with patch('scout.career_mapping.get_page',side_effect=HTTPError('https://example.com',403,'Denied',{},None)):
   with self.assertRaises(SourceError) as cm:html_page('https://example.com')
   self.assertEqual(cm.exception.status,'BLOCKED');self.assertEqual(cm.exception.response_status,403);cm.exception.__cause__.close()

class FinalPartitionVerificationTests(unittest.TestCase):
 def test_final_root_new_publication_is_rejected(self):
  original=WorkdayRemediationTests.census();calls=0
  def get(u,p):
   nonlocal calls
   r=original(u,p)
   if not p['appliedFacets']:
    calls+=1
    if calls==2:r['jobPostings']=[dict(title='New',externalPath='/job/new-unseen')]
   return r
  with self.assertRaises(SourceError):enumerate_inventory('https://example.com',get)
 def test_same_total_but_changed_facet_histogram_is_rejected(self):
  original=WorkdayRemediationTests.census();calls=0
  def get(u,p):
   nonlocal calls
   r=original(u,p)
   if not p['appliedFacets']:
    calls+=1
    if calls==2:r['facets'][0]['values'][0]['count']=1001;r['facets'][0]['values'][1]['count']=1000
   return r
  with self.assertRaises(SourceError):enumerate_inventory('https://example.com',get)

class StructuredCountryTests(unittest.TestCase):
 def test_india_country_is_not_indiana(self):
  from scout.classify import classify
  from scout.model import Job
  j=Job(title='Finance Intern',countries=['IN'],locations=['Bengaluru, KA, IN']).clean()
  self.assertEqual(classify(j)['geography_status'],'NON_US')
 def test_indiana_and_explicit_us_multi_location_remain_us(self):
  from scout.classify import classify
  from scout.model import Job
  for j in [Job(title='Finance Intern',countries=['US'],locations=['Indianapolis, IN']),Job(title='Finance Intern',countries=['GB'],locations=['London, UK','New York, United States'])]:self.assertEqual(classify(j.clean())['geography_status'],'US')

class WorkdayPartitionFallbackTests(unittest.TestCase):
 def fixture(self,overlap=False):
  def get(u,p):
   offset=p['offset'];f=p['appliedFacets']
   if not f:
    return dict(total=40,jobPostings=[dict(title='Role',externalPath=f'/job/{i}') for i in range(20)],facets=[dict(facetParameter='Location_Country',values=[dict(id='a',count=20),dict(id='b',count=20)])])
   start=0 if f['Location_Country']==['a'] or overlap else 20
   return dict(total=20,jobPostings=[dict(title='Role',externalPath=f'/job/{i}') for i in range(start,start+20)])
  return get
 def test_unstable_uncapped_pagination_uses_proven_disjoint_geographic_partition(self):
  self.assertEqual(len(enumerate_inventory('https://example.com',self.fixture())),40)
 def test_fallback_does_not_accept_overlapping_geographic_partitions(self):
  with self.assertRaisesRegex(SourceError,'overlap'):enumerate_inventory('https://example.com',self.fixture(True))
 def test_access_error_never_retried_as_partition(self):
  calls=[]
  def get(u,p):
   calls.append(p)
   if p['offset']:raise SourceError('BLOCKED','429')
   return dict(total=40,jobPostings=[dict(title='Role',externalPath=f'/job/{i}') for i in range(20)],facets=[])
  with self.assertRaisesRegex(SourceError,'429'):enumerate_inventory('https://example.com',get)
  self.assertEqual(len(calls),2)

class OpaqueWorkdayTests(unittest.TestCase):
 def check(self,available):
  from scout.sources.workday import fetch
  def get(u,p=None):
   if u.endswith('/jobs'):return dict(total=1,jobPostings=[dict(externalPath='/job/1',bulletFields=['R1'])])
   return dict(jobPostingInfo=dict(title='Accounting Manager',jobReqId='R1',posted=available,externalUrl='https://example.wd1.myworkdayjobs.com/Jobs/job/1'))
  return fetch(dict(careers_url='https://example.wd1.myworkdayjobs.com/Jobs'),get)
 def test_missing_title_requires_same_board_matching_available_detail(self):self.assertEqual(self.check(True).jobs[0].title,'Accounting Manager')
 def test_unavailable_opaque_listing_fails_entire_inventory(self):
  with self.assertRaisesRegex(SourceError,'unavailable'):self.check(False)
