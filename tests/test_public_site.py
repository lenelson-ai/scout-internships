import unittest,json,tempfile
from pathlib import Path
from unittest.mock import patch
from scout.public_site import business_candidate,season,build
from scout.db import connect
class PublicSiteTests(unittest.TestCase):
 def test_title_evidence_for_season(self):
  self.assertEqual(season('Finance Intern Summer 2027'),'Summer 2027');self.assertIsNone(season('Finance Intern 2027'))
 def test_clear_nonbusiness_and_graduate_noise(self):
  for title in ('Clinical Flow Cytometry Intern','MBA Finance Intern','Teller Part-Time Co-Op City Branch','Software Engineer Intern'):
   self.assertFalse(business_candidate(dict(title=title,category='OTHER')))
  self.assertTrue(business_candidate(dict(title='Wealth Management Intern',category='OTHER')))
 def test_outage_preserves_previous_bytes(self):
  with tempfile.TemporaryDirectory() as t:
   path=Path(t)/'feed.json';original=json.dumps(dict(jobs=[]));path.write_text(original);db=connect(':memory:');self.addCleanup(db.close)
   with self.assertRaisesRegex(ValueError,'preserve'):build(db,path)
   self.assertEqual(path.read_text(),original)
 def test_unsafe_apply_prevents_publication(self):
  with tempfile.TemporaryDirectory() as t:
   row=dict(scout_job_id=1,title='Finance Intern',category='FINANCE',canonical_apply_url='javascript:alert(1)')
   with patch('scout.public_site.feed',return_value=dict(jobs=[row])):
    db=connect(':memory:');self.addCleanup(db.close)
    with self.assertRaisesRegex(ValueError,'Unsafe'):build(db,Path(t)/'feed.json')
 def test_failed_source_row_retained_but_successful_absence_removed(self):
  from scout.db import company,source
  from scout.model import now
  for failed in (True,False):
   with self.subTest(failed=failed),tempfile.TemporaryDirectory() as t:
    db=connect(':memory:');cid=company(db,'Example');sid=source(db,cid,'greenhouse','example','https://example.com/jobs')
    db.execute('update companies set coverage_tier=1 where id=?',(cid,));db.execute("update sources set health_status='SUCCESS',last_successful_check_at=? where id=?",(now(),sid));db.execute("insert into recruiting_scope_reviews values(?,'VALIDATED',?,'{}',?)",(cid,json.dumps([sid]),now()))
    cid2=company(db,'Other');sid2=source(db,cid2,'greenhouse','other','https://example.org/jobs');db.execute('update sources set health_status=?,last_successful_check_at=? where id=?',('BLOCKED' if failed else 'SUCCESS',now(),sid2))
    row=dict(scout_job_id=9,source_id=sid2,title='Finance Intern',category='FINANCE',first_seen_at=now(),discovery_kind='INITIAL_DISCOVERY',canonical_apply_url='https://example.org/apply')
    path=Path(t)/'feed.json';path.write_text(json.dumps(dict(jobs=[row])))
    with patch('scout.public_site.feed',return_value=dict(jobs=[])):r=build(db,path)
    self.assertEqual(len(r['jobs']),int(failed))
    if failed:self.assertFalse(r['jobs'][0]['source_current'])
    db.close()

class BenchmarkGeographyTests(unittest.TestCase):
 def test_city_and_full_state_name(self):
  from scout.classify import classify
  from scout.model import Job
  for city in ('McLean, Virginia','Evanston, Illinois','Boston, Massachusetts'):
   self.assertEqual(classify(Job(title='Consulting Intern',locations=[city]))['geography_status'],'US')
  self.assertEqual(classify(Job(title='Consulting Intern',locations=['Tbilisi, Georgia'],countries=['GE']))['geography_status'],'NON_US')
  self.assertEqual(classify(Job(title='Consulting Intern',locations=['Tbilisi, Georgia']))['geography_status'],'UNKNOWN')
