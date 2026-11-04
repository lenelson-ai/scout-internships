import unittest,json
from datetime import datetime,timezone,timedelta
from scout.db import connect,company,source
from scout.source_roles import set_role,review_scope
from scout.scan_queue import apply_cadences,claim,finish,scan,initialize
from scout.sources.base import Snapshot,SourceError
class CapacityPolicyTests(unittest.TestCase):
 def test_thousand_board_plan_is_bounded_and_explicit(self):
  c=connect(':memory:');self.addCleanup(c.close)
  with c:
   for n in range(1000):
    cid=company(c,'Capacity fixture '+str(n));c.execute('update companies set coverage_tier=2 where id=?',(cid,));sid=source(c,cid,'lever','firm'+str(n),'https://jobs.lever.co/firm'+str(n));set_role(c,sid,'AUTHORITATIVE',dict(official_url='https://firm.test/careers',reason='Fixture'));review_scope(c,cid,[sid],dict(official_url='https://firm.test/careers',scope_reason='Fixture'))
  policy=dict(tier_intervals_seconds={'1':43200,'2':86400,'3':86400},inactive_interval_multiplier=2,max_sources_per_run=100,max_run_seconds=1500,workers=4,per_provider_concurrency=2,capacity=dict(enabled=True,retry_reserve_fraction=.2,inactive_max_interval_seconds={'2':345600,'3':604800}))
  p=apply_cadences(c,policy);self.assertTrue(p['allocation_satisfied']);self.assertLessEqual(p['allocated_daily_starts'],320);self.assertGreater(p['policy_reductions'],0);self.assertEqual(c.execute('select count(*) from source_scan_policy').fetchone()[0],1000);self.assertLessEqual(c.execute('select max(interval_seconds) from sources').fetchone()[0],345600)
 def setup_source(self,c):
  cid=company(c,'Fixture');sid=source(c,cid,'lever','f','https://jobs.lever.co/f')
  with c:set_role(c,sid,'AUTHORITATIVE',dict(official_url='https://f.test/careers',reason='Fixture'));review_scope(c,cid,[sid],dict(official_url='https://f.test/careers',scope_reason='Fixture'))
  return sid
 def test_access_denied_has_daily_cooldown_not_fifteen_minutes(self):
  c=connect(':memory:');self.addCleanup(c.close);sid=self.setup_source(c);token,_=claim(c,at='2026-11-04T12:00:00+00:00')
  with c:c.execute('update sources set last_response_status=403 where id=?',(sid,))
  finish(c,sid,token,'BLOCKED',at='2026-11-04T12:00:00+00:00');self.assertEqual(c.execute('select due_at from scan_queue').fetchone()[0],'2026-11-05T12:00:00+00:00')
 def test_interrupted_attempt_is_audited_when_lease_recovers(self):
  c=connect(':memory:');self.addCleanup(c.close);sid=self.setup_source(c);token,_=claim(c,at='2026-11-04T12:00:00+00:00',lease_seconds=60)
  with c:c.execute("insert into scan_attempts values('a','b',?,'2026-11-04T12:00:00+00:00',NULL,'RUNNING',?)",(sid,json.dumps(dict(lease_token=token))))
  _,rr=claim(c,at='2026-11-04T12:01:01+00:00');self.assertEqual(len(rr),1);self.assertEqual(c.execute('select status from scan_attempts').fetchone()[0],'INTERRUPTED')
 def test_paused_provider_does_not_consume_claims_or_starve_others(self):
  c=connect(':memory:');self.addCleanup(c.close);self.setup_source(c);initialize(c)
  with c:c.execute("insert into provider_pauses values('lever','2026-11-05T12:00:00+00:00','DNS burst',1)")
  self.assertEqual(claim(c,at='2026-11-04T12:00:00+00:00')[1],[])
 def test_metrics_persist_actual_queue_delay_and_request_cost(self):
  c=connect(':memory:');self.addCleanup(c.close);sid=self.setup_source(c)
  r=scan(c,fetcher=lambda _:Snapshot([]));row=c.execute('select * from scan_attempts').fetchone();m=json.loads(row['metrics']);self.assertIn('queue_delay_seconds',m);self.assertIn('total_seconds',m);self.assertFalse(m['retry_attempt']);self.assertEqual(row['status'],'SUCCESS_NO_MATCHING_JOBS')

 def test_external_complete_recovery_satisfies_due_queue_without_recrawl(self):
  from scout.scan_queue import sync
  c=connect(':memory:');self.addCleanup(c.close);sid=self.setup_source(c);initialize(c)
  with c:
   c.execute("insert into scan_queue(source_id,due_at) values(?,'2026-11-04T10:00:00+00:00')",(sid,))
   c.execute("insert into scan_attempts values('old','batch',?,'2026-11-04T09:00:00+00:00','2026-11-04T09:01:00+00:00','TIMEOUT','{}')",(sid,))
   c.execute("update sources set health_status='SUCCESS',last_successful_check_at='2026-11-04T12:00:00+00:00' where id=?",(sid,))
  sync(c);self.assertGreater(c.execute('select due_at from scan_queue').fetchone()[0],'2026-11-04T12:00:00+00:00')
  with c:c.execute("update sources set health_status='PARSER_ERROR' where id=?",(sid,));c.execute("update scan_queue set due_at='2026-11-04T10:00:00+00:00'")
  sync(c);self.assertEqual(c.execute('select due_at from scan_queue').fetchone()[0],'2026-11-04T10:00:00+00:00')
