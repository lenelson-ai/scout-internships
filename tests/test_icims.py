import unittest,json
from scout.sources.icims import configuration,posting_key,listing,fetch
from scout.sources.base import SourceError,Snapshot
from scout.sources import detect
from scout.recruiting_discovery import board_identity
from scout.model import Job

def row(n,title='Staff Engineer',style='iCIMS_JobCardItem'):
 return f'<li class="{style}"><div class="header"><span>Job Locations</span>US-MA-Boston</div><a href="https://careers-fixture.icims.com/jobs/{n}/role/job?in_iframe=1"><h3>{title}</h3></a><div class="iCIMS_JobHeaderTag"><dt>ID</dt><dd>2026-{n}</dd></div></li>'
def page_html(ids,page=1,pages=1,title='Staff Engineer',style='iCIMS_JobCardItem'):
 return f'<h2 class="iCIMS_SubHeader_Jobs">Search Results Page {page} of {pages}</h2><ul class="iCIMS_JobsTable">'+''.join(row(n,title,style) for n in ids)+'</ul>'+''.join(f'<a href="/jobs/search?pr={p-1}&in_iframe=1">Page {p}</a>' for p in range(1,pages+1))
def response(url,body):return url,body,[],'fixture'
class IcimsTests(unittest.TestCase):
 def test_canonical_external_publication_identity(self):
  for u in ('https://careers-fixture.icims.com/jobs/search?ss=1&searchKeyword=intern','https://careers-fixture.icims.com/jobs/1/title/job'):
   self.assertEqual(board_identity(u),('icims','careers-fixture.icims.com','https://careers-fixture.icims.com/jobs/search'))
  self.assertNotEqual(board_identity('https://ug-fixture.icims.com/jobs/search'),board_identity('https://grad-fixture.icims.com/jobs/search'))
 def test_internal_and_vendor_documents_are_not_board_configs(self):
  for u in ('https://www.icims.com/legal/privacy','https://fixture.icims.com/icims2/servlet/icims2','https://employees-fixture.icims.com/jobs/search','https://internal-fixture.icims.com/jobs','https://apply-internal-fixture.icims.com/jobs/dashboard','https://careers-fixture.icims.com/jobs/login?loginOnly=1'):
   with self.assertRaises(SourceError):configuration(dict(careers_url=u))
   self.assertEqual(detect(u)[0],'custom')
 def test_complete_card_census_traversed_twice(self):
  calls=[]
  def get(u):
   calls.append(u);p=2 if 'pr=1' in u else 1;return response(u,page_html([p],p,2))
  s=fetch(dict(careers_url='https://careers-fixture.icims.com/jobs/search'),page=get)
  self.assertEqual(len(s.jobs),2);self.assertEqual(len(calls),4);self.assertEqual(s.inventory_evidence['complete_passes'],2);self.assertEqual(s.jobs[0].external_job_id,'2026-1');self.assertEqual(s.jobs[0].countries,['US'])
 def test_classic_rows_variant(self):
  s=fetch(dict(careers_url='https://careers-fixture.icims.com/jobs/search'),page=lambda u:response(u,page_html([1],style='iCIMS_JobListingRow')))
  self.assertEqual(s.inventory_evidence['variants'],['CLASSIC_ROWS'])
 def test_explicit_empty_is_repeated_not_missing_content(self):
  s=fetch(dict(careers_url='https://careers-fixture.icims.com/jobs/search'),page=lambda u:response(u,'Sorry, no jobs were found that match your search criteria. Please try other selections.'))
  self.assertEqual(s.jobs,[])
  with self.assertRaises(SourceError):fetch(dict(careers_url='https://careers-fixture.icims.com/jobs'),page=lambda u:response(u,'<html>Welcome</html>'))
 def test_duplicate_page_and_changed_second_census_fail(self):
  for change in (False,True):
   calls=[]
   def get(u):
    calls.append(u);p=2 if 'pr=1' in u else 1;n=(p if len(calls)<3 else p+10) if change else 1
    return response(u,page_html([n],p,2))
   with self.assertRaises(SourceError):fetch(dict(careers_url='https://careers-fixture.icims.com/jobs/search'),page=get)
 def test_missing_published_page_link_fails(self):
  h=page_html([1],1,2).replace('<a href="/jobs/search?pr=1&in_iframe=1">Page 2</a>','')
  with self.assertRaisesRegex(SourceError,'published navigation'):fetch(dict(careers_url='https://careers-fixture.icims.com/jobs'),page=lambda u:response(u,h))
 def test_explicit_iframe_only_not_private_endpoint_guess(self):
  calls=[]
  def get(u):
   calls.append(u)
   if 'in_iframe' not in u:return u,'branding',[('/jobs/search?in_iframe=1','',dict(embedded=True))],'shell'
   return response(u,page_html([1]))
  self.assertEqual(len(fetch(dict(careers_url='https://careers-fixture.icims.com/jobs'),page=get).jobs),1);self.assertEqual(len(calls),3)
 def test_blocked_or_login_redirect_never_empty(self):
  for body,url in [('Access denied','https://careers-fixture.icims.com/jobs/search'),('Log in','https://careers-fixture.icims.com/jobs/login')]:
   with self.assertRaises(SourceError):fetch(dict(careers_url='https://careers-fixture.icims.com/jobs'),page=lambda u:(url,body,[],'sha'))
 def test_detail_hydration_and_date_default_not_trusted(self):
  def get(u):
   if '/jobs/1/' not in u:return response(u,page_html([1],title='Finance Intern'))
   d=dict(**{'@type':'JobPosting'},title='Finance Intern',description='Currently pursuing a bachelor degree.',url='https://careers-fixture.icims.com/jobs/1/role/job',datePosted='2024-11-04T12:34:56Z',validThrough='2027-11-04T12:34:56Z',jobLocation=[dict(address=dict(addressCountry='US',addressLocality='Boston',addressRegion='MA'))])
   return response(u,'<script type="application/ld+json">'+json.dumps(d)+'</script>')
  s=fetch(dict(careers_url='https://careers-fixture.icims.com/jobs'),page=get);self.assertIn('bachelor',s.jobs[0].description);self.assertIsNone(s.jobs[0].posted_at);self.assertEqual(s.jobs[0].locations,['Boston, MA'])
 def test_failed_detail_is_not_authoritative_partial_inventory(self):
  def get(u):return response(u,'broken detail' if '/jobs/1/' in u else page_html([1],title='Finance Intern'))
  with self.assertRaises(SourceError):fetch(dict(careers_url='https://careers-fixture.icims.com/jobs'),page=get)
 def test_requisition_scope_for_legacy_matching(self):
  from scout.reconcile import scope,url_key
  self.assertEqual(scope('https://careers-fixture.icims.com/jobs/1/title/job'),('icims','careers-fixture.icims.com'))
  self.assertEqual(url_key('https://careers-fixture.icims.com/jobs/1/title/job?in_iframe=1'),url_key('https://careers-fixture.icims.com/jobs/1/other-title/login'))

class SubmittedSearchTests(unittest.TestCase):
 def test_published_search_form_is_submitted_before_zero_inventory(self):
  calls=[]
  def get(u):
   calls.append(u)
   if 'ss=1' not in u:return response(u,'<form id="searchForm" action="/jobs/search"><input type="hidden" name="ss" value="1"></form>')
   return response(u,'Sorry, no jobs were found that match your search criteria.')
  s=fetch(dict(careers_url='https://careers-fixture.icims.com/jobs'),page=get)
  self.assertEqual(s.jobs,[]);self.assertEqual(len(calls),3);self.assertIn('ss=1',calls[-1])

class ScopeRecoveryTests(unittest.TestCase):
 def test_native_record_alone_is_not_an_official_employer_handoff(self):
  from scout.db import connect,company
  from scout.recovery import apply_inspection
  c=connect(':memory:');self.addCleanup(c.close);cid=company(c,'Fixture')
  r=dict(company_id=cid,attempted_at='2026-11-04',boards=[dict(provider='icims',identifier='careers-fixture.icims.com',url='https://careers-fixture.icims.com/jobs/search')]);apply_inspection(c,r)
  self.assertFalse(r['configured_sources']);self.assertTrue(r['ownership_unverified_sources'])
 def test_unknown_native_config_promotes_without_changing_source_id(self):
  from scout.db import connect,company,source
  from scout.recovery import apply_inspection
  c=connect(':memory:');self.addCleanup(c.close);cid=company(c,'Fixture');sid=source(c,cid,'custom','','https://careers-fixture.icims.com/jobs/1/role/job')
  r=dict(company_id=cid,attempted_at='2026-11-04',boards=[dict(provider='icims',identifier='careers-fixture.icims.com',url='https://careers-fixture.icims.com/jobs/search',official_handoff=True,evidence_url='https://firm.test/careers')]);apply_inspection(c,r)
  self.assertEqual(r['configured_sources'],[sid]);self.assertEqual(c.execute('select ats_type from sources').fetchone()[0],'icims');self.assertEqual(c.execute('select careers_url from sources').fetchone()[0],'https://careers-fixture.icims.com/jobs/search')
 def test_literal_script_only_migration_is_not_js_execution(self):
  from scout.career_mapping import published_redirect
  self.assertEqual(published_redirect('<script>window.top.location.href="https:\\/\\/firm.test\\/careers";</script>'),'https://firm.test/careers')
  self.assertIsNone(published_redirect('<script>if (x) window.top.location.href="https://firm.test/careers";</script>'))
 def test_spanish_card_page_census_and_requisition(self):
  body=page_html([1]).replace('Page 1 of 1','Página 1 de 1').replace('<dt>ID</dt>','<dt>Identificación</dt>')
  p=listing(body,'https://careers-fixture.icims.com/jobs/search');self.assertEqual(p['rows'][0]['external_id'],'2026-1')

class LegacySafetyTests(unittest.TestCase):
 def test_generic_program_or_account_link_cannot_close_a_legacy_job(self):
  from scout.reconcile import scope,source_scope
  self.assertIsNone(scope('https://careers-fixture.icims.com/jobs/search'))
  self.assertIsNone(scope('https://careers-fixture.icims.com/jobs/login?loginOnly=1'))
  self.assertEqual(source_scope(dict(ats_type='icims',careers_url='https://careers-fixture.icims.com/jobs/search')),('icims','careers-fixture.icims.com'))
