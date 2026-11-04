import unittest
from scout.db import connect,company,source
from scout.ingest import run_source
from scout.sources.base import Snapshot
from scout.model import Job

class FreshnessInventoryTests(unittest.TestCase):
 def test_first_and_subsequent_discovery_preserve_benchmark_fields(self):
  db=connect(':memory:')
  try:
   cid=company(db,'Fixture');sid=source(db,cid,'greenhouse','fixture')
   def job(i):return Job(title='Finance Intern',external_job_id=str(i),description='Undergraduate finance internship',locations=['New York, NY'],source_url=f'https://example.com/jobs/{i}',canonical_apply_url=f'https://example.com/jobs/{i}/apply',posted_at='2026-10-28T00:00:00+00:00')
   s=dict(db.execute('select * from sources where id=?',(sid,)).fetchone())
   run_source(db,s,fetcher=lambda _:Snapshot([job(1)]),stamp='2026-11-01T12:00:00+00:00')
   s=dict(db.execute('select * from sources where id=?',(sid,)).fetchone())
   run_source(db,s,fetcher=lambda _:Snapshot([job(1),job(2)]),stamp='2026-11-02T12:00:00+00:00')
   rows=list(db.execute('select * from source_inventory order by external_job_id'))
   self.assertEqual([r['discovery_kind'] for r in rows],['INITIAL_DISCOVERY','NEW_SINCE_PREVIOUS_SUCCESSFUL_INVENTORY'])
   self.assertEqual(rows[0]['first_seen_at'],'2026-11-01T12:00:00+00:00')
   self.assertEqual(rows[0]['last_seen_at'],'2026-11-02T12:00:00+00:00')
   import json
   payload=json.loads(rows[0]['normalized_payload'])
   self.assertEqual(payload['posted_at'],'2026-10-28T00:00:00+00:00')
   self.assertEqual(payload['external_job_id'],'1');self.assertEqual(rows[0]['source_id'],sid)
   self.assertEqual(payload['canonical_apply_url'],'https://example.com/jobs/1/apply')
  finally:db.close()

class ScopeCoverageIntegrationTests(unittest.TestCase):
 def test_reviewed_scope_ignores_legacy_but_new_campus_invalidates_review(self):
  from scout.source_roles import set_role,review_scope
  from scout.universe_qa import health_report
  from scout.model import now
  db=connect(':memory:')
  try:
   cid=company(db,'Fixture Canonical Employer');sid=source(db,cid,'greenhouse','fixture')
   old=source(db,cid,'custom','obsolete','https://example.com/old')
   set_role(db,sid,'AUTHORITATIVE',dict(official_url='https://example.com/careers',reason='Official full board'))
   set_role(db,old,'LEGACY',dict(reason='Superseded by official board'))
   db.execute("UPDATE sources SET health_status='SUCCESS',last_successful_check_at=? WHERE id=?",(now(),sid))
   db.execute("UPDATE sources SET health_status='BLOCKED' WHERE id=?",(old,))
   review_scope(db,cid,[sid],dict(official_url='https://example.com/careers',scope_reason='Official full board',scope_state='PROVISIONALLY_VALIDATED'))
   self.assertTrue(next(e for e in health_report(db)['employers'] if e['company_id']==cid)['healthy'])
   campus=source(db,cid,'greenhouse','campus')
   set_role(db,campus,'AUTHORITATIVE',dict(official_url='https://example.com/students',reason='New separate required campus board'))
   result=next(e for e in health_report(db)['employers'] if e['company_id']==cid)
   self.assertFalse(result['healthy']);self.assertFalse(result['scope_validated'])
  finally:db.close()
