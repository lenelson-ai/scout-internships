import unittest
from scout.sources import detect
from scout.recruiting_discovery import board_identity,directory_rows
from scout.entities import resolve_recruiting_unit
from scout.db import connect,company,source
class DiscoveryTests(unittest.TestCase):
 def test_embedded_greenhouse_is_board_not_embed(self):
  self.assertEqual(detect('https://boards.greenhouse.io/embed/job_app?for=firmx&token=12'),('greenhouse','firmx'))
  self.assertEqual(detect('https://boards.greenhouse.io/embed/job_app?token=12'),('custom',''))
 def test_workdaysite_and_oracle_actual_configuration(self):
  self.assertEqual(detect('https://wd1.myworkdaysite.com/recruiting/firm/Careers')[0],'workday')
  self.assertEqual(detect('https://firm.oraclecloud.com/en/sites/CX_1')[0],'oracle')
 def test_board_identity_ignores_deep_link_locale_but_not_board(self):
  a=board_identity('https://firm.wd1.myworkdayjobs.com/en-US/Jobs/job/NY/X_R1');b=board_identity('https://firm.wd1.myworkdayjobs.com/Jobs');self.assertEqual(a,b)
  self.assertNotEqual(a,board_identity('https://firm.wd1.myworkdayjobs.com/Campus'))
 def test_board_relationship_does_not_merge_multiple_owners(self):
  db=connect(':memory:');self.addCleanup(db.close);a=company(db,'Alpha');b=company(db,'Beta')
  for cid in (a,b):source(db,cid,'greenhouse','shared','https://job-boards.greenhouse.io/shared')
  cid,candidates,_=resolve_recruiting_unit(db,'New Brand','https://job-boards.greenhouse.io/shared','https://example.com/careers');self.assertIsNone(cid);self.assertEqual(set(candidates),{a,b})
 def test_directory_dates_are_not_employer_dates(self):
  r=directory_rows('| [Firm](https://directory.example/firm) | Finance Intern Summer 2027 | New York, NY | 2d | [Apply](https://jobs.lever.co/firm/12) |')[0]
  self.assertEqual(r['external_age'],'2d');self.assertNotIn('posted_at',r);self.assertNotIn('first_seen_at',r)
 def test_mfa_parser_excludes_service_providers_and_keeps_unknown_websites(self):
  from scout.recruiting_discovery import mfa_members
  html=''.join(f'<article id="post-{i}" class="members_level-fund-manager-members"><div class="elementor-heading-title elementor-size-default">Fund {i}</div></article>' for i in range(50))
  html+='<article id="post-999" class="members_level-capital-service-provider"><div class="elementor-heading-title elementor-size-default"><a href="https://vendor.example">Vendor</a></div></article>'
  rows=mfa_members(html);self.assertEqual(len(rows),50);self.assertTrue(all(r['domain']=='' for r in rows))
 def test_undergraduate_accounting_titles_retained_but_software_qa_excluded(self):
  from scout.classify import classify
  from scout.model import Job
  for title in ('Tax Intern Summer 2027','Audit Summer Internship 2027','Assurance Intern - Summer 2027'):
   r=classify(Job(title=title,locations=['Chicago, IL'],description='Currently pursuing a bachelors degree in accounting'))
   self.assertTrue(r['retain']);self.assertEqual(r['function'],'FINANCE')
  self.assertFalse(classify(Job(title='Software Quality Assurance Intern',locations=['Chicago, IL']))['retain'])
 def test_reclassification_uses_original_inventory_detection_not_repair_date(self):
  from scout.classify import classify
  from scout.model import Job
  from scout.ingest import run_source
  from scout.sources.base import Snapshot
  from unittest.mock import patch
  db=connect(':memory:');self.addCleanup(db.close);cid=company(db,'Fixture Accounting');sid=source(db,cid,'greenhouse','fixture','https://job-boards.greenhouse.io/fixture');s=dict(db.execute('select * from sources where id=?',(sid,)).fetchone());j=Job(title='Tax Intern',external_job_id='T1',locations=['Chicago, IL'],description='Undergraduate accounting program',source_url='https://example.com/T1',canonical_apply_url='https://example.com/T1')
  first='2026-10-01T12:00:00+00:00';later='2026-11-03T12:00:00+00:00'
  with patch('scout.ingest.classify',side_effect=lambda j:dict(classify(j),retain=False)):run_source(db,s,lambda _:Snapshot([j]),first)
  run_source(db,s,lambda _:Snapshot([j]),later)
  self.assertEqual(db.execute('select first_seen_at from jobs').fetchone()[0],first)
  self.assertEqual(db.execute('select discovery_kind from source_inventory').fetchone()[0],'INITIAL_DISCOVERY')
 def test_workday_path_to_requisition_enrichment_preserves_discovery(self):
  from scout.classify import classify
  from scout.model import Job
  from scout.ingest import run_source
  from scout.sources.base import Snapshot
  from unittest.mock import patch
  db=connect(':memory:');self.addCleanup(db.close);cid=company(db,'Fixture Firm');sid=source(db,cid,'workday','firm/External','https://firm.wd1.myworkdayjobs.com/External');s=dict(db.execute('select * from sources where id=?',(sid,)).fetchone());path='/job/Chicago/Tax-Intern_JR1';url='https://firm.wd1.myworkdayjobs.com/en-US/External'+path
  j=Job(title='Tax Intern',external_job_id=path,locations=['Chicago, IL'],description='Undergraduate accounting internship',source_url=url,canonical_apply_url=url+'/apply')
  first='2026-10-01T12:00:00+00:00';later='2026-11-03T12:00:00+00:00'
  with patch('scout.ingest.classify',side_effect=lambda j:dict(classify(j),retain=False)):run_source(db,s,lambda _:Snapshot([j]),first)
  j.external_job_id='JR1';j.canonical_apply_url=j.canonical_apply_url.replace('/en-US/','/')
  result=run_source(db,s,lambda _:Snapshot([j]),later)
  self.assertNotIn('INVENTORY_NEW',result['events']);self.assertEqual(db.execute('select first_seen_at from jobs').fetchone()[0],first)
  current=db.execute("select * from source_inventory where identity='id:JR1'").fetchone();self.assertEqual(current['discovery_kind'],'INITIAL_DISCOVERY');self.assertEqual(current['first_seen_at'],first)

 def test_new_real_requisition_on_reused_url_does_not_inherit_enrichment_origin(self):
  from scout.ingest import run_source
  from scout.sources.base import Snapshot
  from scout.model import Job
  db=connect(':memory:');self.addCleanup(db.close);cid=company(db,'Fixture Reposts');sid=source(db,cid,'workday','firm/Jobs','https://firm.wd1.myworkdayjobs.com/Jobs');s=dict(db.execute('select * from sources where id=?',(sid,)).fetchone())
  j=Job(title='Finance Intern',external_job_id='/job/NY/Intern_JR1',locations=['New York, NY'],source_url='https://firm.wd1.myworkdayjobs.com/Jobs/job/NY/Intern_JR1',canonical_apply_url='https://firm.wd1.myworkdayjobs.com/Jobs/job/NY/Intern_JR1/apply')
  run_source(db,s,lambda _:Snapshot([j]),'2026-10-01T12:00:00+00:00');j.external_job_id='JR1'
  run_source(db,s,lambda _:Snapshot([j]),'2026-10-02T12:00:00+00:00');j.external_job_id='JR2'
  run_source(db,s,lambda _:Snapshot([j]),'2026-11-03T12:00:00+00:00')
  r=db.execute("select first_seen_at,discovery_kind from source_inventory where identity='id:JR2'").fetchone();self.assertEqual(r['first_seen_at'],'2026-11-03T12:00:00+00:00');self.assertEqual(r['discovery_kind'],'NEW_SINCE_PREVIOUS_SUCCESSFUL_INVENTORY')
