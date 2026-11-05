import unittest,json,tempfile,subprocess,os,gzip
from pathlib import Path
from unittest.mock import patch
from scout.db import connect,company,source
from scout.model import Job,publication_evidence
from scout.ingest import run_source
from scout.sources.base import Snapshot,SourceError
from scout.source_roles import set_role
class ProductionTests(unittest.TestCase):
 def setUp(self):
  self.c=connect(':memory:');self.addCleanup(self.c.close);self.cid=company(self.c,'Fixture');self.sid=source(self.c,self.cid,'greenhouse','f','https://boards.greenhouse.io/f')
  with self.c:set_role(self.c,self.sid,'AUTHORITATIVE',dict(official_url='https://fixture.test/careers',reason='Fixture official handoff'))
 def job(self,n):return Job(title='Finance Intern',external_job_id=str(n),canonical_apply_url='https://boards.greenhouse.io/f/jobs/'+str(n),description='Currently pursuing a bachelor degree.',locations=['Boston, MA'],countries=['US'])
 def crawl(self,jobs):return run_source(self.c,dict(self.c.execute('select * from sources where id=?',(self.sid,)).fetchone()),fetcher=lambda _:Snapshot(jobs))
 def test_events_only_after_complete_inventory_and_distinguish_initial_reappearance(self):
  self.crawl([self.job(1)]);self.crawl([self.job(1),self.job(2)]);self.crawl([self.job(1)]);self.crawl([self.job(1),self.job(2)])
  kinds=[r[0] for r in self.c.execute('select kind from discovery_events order by id')];self.assertEqual(kinds,['INITIAL_DISCOVERY','NEW_SINCE_PREVIOUS_SUCCESSFUL_INVENTORY','REAPPEARED']);self.assertEqual(json.loads(self.c.execute('select classification from discovery_events limit 1').fetchone()[0])['observation']['title'],'Finance Intern')
 def test_failed_snapshot_rolls_back_metrics_and_jobs(self):
  self.crawl([self.job(1)]);before=self.c.execute('select count(*) from source_censuses').fetchone()[0]
  r=run_source(self.c,dict(self.c.execute('select * from sources where id=?',(self.sid,)).fetchone()),fetcher=lambda _:Snapshot([self.job(2)],authoritative=False));self.assertEqual(r['status'],'PARSER_ERROR');self.assertEqual(self.c.execute('select count(*) from source_censuses').fetchone()[0],before);self.assertEqual(self.c.execute('select count(*) from jobs where active=1').fetchone()[0],1)
 def test_publication_precision_does_not_invent_timezone(self):
  self.assertEqual(publication_evidence('2026-11-04')['precision'],'DATE');self.assertFalse(publication_evidence('2026-11-04T10:00:00')['timezone_verified']);self.assertTrue(publication_evidence('2026-11-04T10:00:00Z')['timezone_verified'])
 def test_seven_days_not_completed_by_two_runs(self):
  from scout.operations import begin,report
  with patch.dict(os.environ,{'GITHUB_ACTIONS':'true','GITHUB_RUN_ID':'test','GITHUB_EVENT_NAME':'workflow_dispatch'}):begin(self.c);begin(self.c)
  r=report(self.c);self.assertFalse(r['evaluation']['completed']);self.assertEqual(r['evaluation']['completed_days'],[]);self.assertEqual(r['environments']['HOSTED']['runs'],2)
 def test_additive_configuration_never_copies_seed_health_or_history(self):
  from scout.deployment_config import manifest,adopt
  from scout.source_roles import review_scope
  self.crawl([self.job(1)])
  with self.c:review_scope(self.c,self.cid,[self.sid],dict(official_url='https://fixture.test/careers',scope_reason='Fixture known surfaces'))
  cfg=manifest(self.c);d=connect(':memory:');self.addCleanup(d.close);other=company(d,'Other fixture');source(d,other,'greenhouse','different','https://boards.greenhouse.io/different');cid=company(d,'Fixture');r=adopt(d,cfg);newid=r['source_mapping'][self.sid];self.assertNotEqual(newid,self.sid);self.assertEqual(d.execute('select count(*) from jobs').fetchone()[0],0);self.assertFalse(d.execute('select health_status from sources where id=?',(newid,)).fetchone()[0].startswith('SUCCESS'));self.assertEqual(d.execute('select count(*) from recruiting_scope_reviews').fetchone()[0],0)
  from scout.scan_queue import eligible_sources
  self.assertIn(newid,eligible_sources(d));self.assertTrue(adopt(d,cfg)['already_applied'])
 def test_restore_network_failure_cannot_fall_back_to_seed(self):
  from scripts.deployment_state import restore
  with tempfile.TemporaryDirectory() as t,patch('scripts.deployment_state.git',side_effect=subprocess.CalledProcessError(128,['git'])):
   with self.assertRaises(subprocess.CalledProcessError):restore(t)
   self.assertFalse((Path(t)/'scout.sqlite3').exists())
 def test_metrics_failure_rolls_back_source_ingestion(self):
  with patch('scout.discovery_metrics.observe',side_effect=RuntimeError('Injected metric failure')):r=self.crawl([self.job(1)])
  self.assertFalse(r['status'].startswith('SUCCESS'));self.assertEqual(self.c.execute('select count(*) from jobs').fetchone()[0],0);self.assertEqual(self.c.execute('select count(*) from source_censuses').fetchone()[0],0)
 def test_imported_scope_requires_live_inventory_and_no_unknown_surface(self):
  from scout.deployment_config import manifest,adopt,activate
  from scout.source_roles import review_scope
  self.crawl([self.job(1)])
  with self.c:review_scope(self.c,self.cid,[self.sid],dict(official_url='https://fixture.test/careers',scope_reason='Fixture'))
  cfg=manifest(self.c);d=connect(':memory:');self.addCleanup(d.close);cid=company(d,'Fixture');adopt(d,cfg);self.assertEqual(activate(d),[]);sid=d.execute('select id from sources').fetchone()[0];run_source(d,dict(d.execute('select * from sources where id=?',(sid,)).fetchone()),fetcher=lambda _:Snapshot([self.job(1)]))
  extra=source(d,cid,'custom','unknown','https://fixture.test/students');self.assertEqual(activate(d),[])
  with d:set_role(d,extra,'DISABLED',dict(reason='Fixture confirms informational page'))
  self.assertEqual(activate(d),[cid])
 def test_date_only_posting_never_gets_exact_latency(self):
  from scout.operations import begin,deliveries
  self.crawl([self.job(1)]);j=self.job(2);j.posted_at='2026-11-01';j._publication_evidence=publication_evidence('2026-11-01');self.crawl([self.job(1),j]);rid=begin(self.c)['id'];deliveries(self.c,rid,True);result=json.loads(self.c.execute('select result from discovery_deliveries').fetchone()[0]);self.assertIsNone(result['latency_seconds']);self.assertEqual(result['publication']['precision'],'DATE')
 def test_local_runs_cannot_complete_hosted_evaluation(self):
  from scout.operations import begin,report
  begin(self.c)
  with self.c:self.c.execute("update reliability_evaluations set window_start='2026-10-01T00:00:00-04:00',first_hosted_at='2026-10-01T00:00:00+00:00'")
  result=report(self.c);self.assertEqual(len(result['evaluation']['completed_days']),7);self.assertFalse(result['evaluation']['completed'])
 def test_added_required_board_invalidates_old_certification_until_live_census(self):
  from scout.deployment_config import manifest,adopt
  from scout.source_roles import review_scope
  self.crawl([self.job(1)]);extra=source(self.c,self.cid,'greenhouse','students','https://boards.greenhouse.io/students')
  with self.c:set_role(self.c,extra,'AUTHORITATIVE',dict(official_url='https://fixture.test/careers',reason='Known student surface'))
  run_source(self.c,dict(self.c.execute('select * from sources where id=?',(extra,)).fetchone()),fetcher=lambda _:Snapshot([]))
  with self.c:review_scope(self.c,self.cid,[self.sid,extra],dict(official_url='https://fixture.test/careers',scope_reason='Both required'))
  cfg=manifest(self.c);d=connect(':memory:');self.addCleanup(d.close);cid=company(d,'Fixture');sid=source(d,cid,'greenhouse','f','https://boards.greenhouse.io/f')
  with d:set_role(d,sid,'AUTHORITATIVE',dict(official_url='https://fixture.test/careers',reason='Original'));review_scope(d,cid,[sid],dict(official_url='https://fixture.test/careers',scope_reason='Original scope'))
  adopt(d,cfg);self.assertEqual(d.execute('select state from recruiting_scope_reviews').fetchone()[0],'UNRESOLVED')
 def test_failed_later_crawl_does_not_masquerade_as_current_verification(self):
  from scout.operations import begin,deliveries
  self.crawl([self.job(1)]);self.crawl([self.job(1),self.job(2)])
  def fail(_):raise SourceError('BLOCKED','Access denied',403)
  run_source(self.c,dict(self.c.execute('select * from sources where id=?',(self.sid,)).fetchone()),fetcher=fail);rid=begin(self.c)['id'];deliveries(self.c,rid,True);result=json.loads(self.c.execute('select result from discovery_deliveries').fetchone()[0]);self.assertTrue(result['retained_inventory_membership']);self.assertFalse(result['current_membership']);self.assertFalse(result['in_full_inventory'])
 def test_failure_categories_keep_network_and_identity_separate(self):
  from scout.operations import failure_category
  self.assertEqual(failure_category('NETWORK_ERROR','nodename nor servname provided'),'DNS');self.assertEqual(failure_category('PARSER_ERROR','Opaque requisition lacks identity'),'REQUISITION_IDENTITY');self.assertEqual(failure_category('BLOCKED','HTTP Error 429'),'RATE_LIMIT');self.assertEqual(failure_category('PARSER_ERROR','Census count differs'),'INVENTORY_COMPLETENESS')
 def test_manual_reruns_of_scheduled_events_do_not_fill_natural_trigger_quota(self):
  from scout.operations import begin,report
  begin(self.c)
  with self.c:
   self.c.execute("update reliability_evaluations set window_start='2026-10-01T00:00:00-04:00',first_hosted_at='2026-10-01T00:00:00+00:00'")
   for day in range(1,8):
    for attempt in range(2,6):
     stamp=f'2026-10-{day:02d}T12:00:00+00:00';self.c.execute("insert into monitoring_runs values(?,'HOSTED','schedule',?,?,'fixture',?,?,'COMPLETED','{}')",(f'{day}-{attempt}',str(day),str(attempt),stamp,stamp))
  r=report(self.c);self.assertFalse(r['evaluation']['completed']);self.assertTrue(all(x['natural_scheduled_runs']==0 for x in r['evaluation']['completed_days']))
 def test_job_after_verified_empty_census_is_new_not_initial_onboarding(self):
  self.crawl([]);self.crawl([self.job(1)])
  self.assertEqual(self.c.execute('select kind from discovery_events').fetchone()[0],'NEW_SINCE_PREVIOUS_SUCCESSFUL_INVENTORY');self.assertEqual(self.c.execute('select discovery_kind from source_inventory').fetchone()[0],'NEW_SINCE_PREVIOUS_SUCCESSFUL_INVENTORY')
 def test_delivery_follows_canonical_merge_without_claiming_known_job_as_new(self):
  from scout.operations import begin,deliveries
  from scout.platform_health import discoveries
  self.crawl([self.job(1)]);self.crawl([self.job(1),self.job(2)])
  event=self.c.execute("select * from discovery_events where kind='NEW_SINCE_PREVIOUS_SUCCESSFUL_INVENTORY'").fetchone();canonical=self.c.execute("select id from jobs where external_job_id='1'").fetchone()[0]
  with self.c:
   self.c.execute("update jobs set first_seen_at=? where id=?",((__import__('datetime').datetime.now(__import__('datetime').timezone.utc)-__import__('datetime').timedelta(days=1)).isoformat(),canonical))
   self.c.execute('insert into job_merges values(?,?,?,?)',(event['job_id'],canonical,'2026-01-01T00:00:00Z','Fixture alias'))
   self.c.execute('update source_inventory set job_id=? where job_id=?',(canonical,event['job_id']))
   self.c.execute('update jobs set active=0 where id=?',(event['job_id'],))
  rid=begin(self.c)['id'];deliveries(self.c,rid,True);d=json.loads(self.c.execute('select result from discovery_deliveries').fetchone()[0]);self.assertEqual(d['canonical_job_id'],canonical);self.assertTrue(d['known_job_before']);self.assertTrue(d['in_full_inventory']);self.assertTrue(d['in_recent_feed']);r=discoveries(self.c);self.assertEqual(r['genuinely_new_opportunities'],0);self.assertEqual(r['verified_delivered'],1)
 def test_shortened_cadence_pulls_healthy_queue_forward_but_preserves_retry_and_lease(self):
  from scout.scan_queue import sync
  from datetime import datetime,timedelta,timezone
  self.crawl([self.job(1)]);sync(self.c);stamp=datetime.now(timezone.utc);future=(stamp+timedelta(days=4)).isoformat()
  with self.c:self.c.execute('update sources set interval_seconds=3600 where id=?',(self.sid,));self.c.execute('update scan_queue set due_at=? where source_id=?',(future,self.sid))
  sync(self.c);last=self.c.execute('select last_successful_check_at from sources where id=?',(self.sid,)).fetchone()[0];self.assertEqual(self.c.execute('select due_at from scan_queue where source_id=?',(self.sid,)).fetchone()[0],(datetime.fromisoformat(last)+timedelta(hours=1)).isoformat())
  with self.c:self.c.execute('update scan_queue set due_at=?,attempts=2 where source_id=?',(future,self.sid))
  sync(self.c);self.assertEqual(self.c.execute('select due_at from scan_queue where source_id=?',(self.sid,)).fetchone()[0],future)
  with self.c:self.c.execute("update scan_queue set attempts=0,lease_token='fixture',lease_until=? where source_id=?",(future,self.sid))
  sync(self.c);self.assertEqual(self.c.execute('select due_at from scan_queue where source_id=?',(self.sid,)).fetchone()[0],future)
