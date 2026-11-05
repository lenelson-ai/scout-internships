import unittest,json
from datetime import datetime,timezone
from scout.db import connect
from scout.platform_health import collect,evaluation,verification

class PlatformHealthTests(unittest.TestCase):
 def setUp(self):
  self.db=connect(':memory:');self.addCleanup(self.db.close)
  with self.db:self.db.execute("insert into platform_observations(observed_at,success,payload) values('2026-10-08T12:00:00Z',1,'{}')")
 def row(self,rid,event='schedule',attempt=1,conclusion='success'):
  steps=[dict(name=n,conclusion='success') for n in ('Restore durable scanner state (fail closed)','Scan due sources and validate publication','Persist committed work even after scan or export failure')]
  return dict(id=rid,run_attempt=attempt,event=event,status='completed',conclusion=conclusion,created_at='2026-10-01T12:00:00Z',html_url='https://github.com/fixture/actions/runs/'+str(rid),jobs=[dict(name='scan',steps=steps),dict(name='publish',conclusion='success')])
 def insert(self,row):
  with self.db:self.db.execute('insert into platform_workflow_runs values(?,?,?,?)',(str(row['id']),row['run_attempt'],'2026-10-02T00:00:00Z',json.dumps(row)))
 def test_platform_failure_before_scanner_is_not_missing_trigger(self):
  row=self.row(1,conclusion='failure');row['jobs']=[];self.insert(row)
  r=evaluation(self.db,'2026-10-01T00:00:00-04:00',datetime(2026,10,2,12,tzinfo=timezone.utc));d=r['completed_days'][0]
  self.assertEqual(d['observed_natural_workflows'],1);self.assertEqual(d['failed_natural_workflows'],1);self.assertEqual(d['missing_natural_workflows'],3);self.assertFalse(r['completed']);self.assertFalse(verification(row)['verified'])
 def test_dispatch_and_rerun_never_satisfy_natural_evidence(self):
  self.insert(self.row(1,'workflow_dispatch'));self.insert(self.row(2,attempt=2))
  r=evaluation(self.db,'2026-10-01T00:00:00-04:00',datetime(2026,10,2,12,tzinfo=timezone.utc));self.assertEqual(r['completed_days'][0]['observed_natural_workflows'],0)
 def test_checkpoint_and_pages_required_not_only_scanner_success(self):
  row=self.row(1);self.assertTrue(verification(row)['verified']);row['jobs'][-1]['conclusion']='failure';self.assertFalse(verification(row)['verified'])
 def test_api_failure_preserves_records_and_marks_evidence_unknown(self):
  self.insert(self.row(1))
  def fail(_):raise RuntimeError('Fixture API unavailable')
  r=collect(self.db,'fixture/repo',fail);self.assertFalse(r['history_complete']);self.assertEqual(self.db.execute('select count(*) from platform_workflow_runs').fetchone()[0],1)
  r=evaluation(self.db,'2026-10-01T00:00:00-04:00',datetime(2026,10,2,12,tzinfo=timezone.utc));self.assertFalse(r['completed_days'][0]['evidence_complete'])
 def test_collect_captures_failed_run_without_monitoring_row(self):
  row=self.row(1,conclusion='failure');row['jobs']=[]
  def get(path):
   if path.endswith('/scout.yml'):return {'state':'active'}
   if path.endswith('/fixture/repo'):return {'default_branch':'main'}
   if '/jobs?' in path:return {'jobs':[]}
   return {'workflow_runs':[row]}
  self.assertTrue(collect(self.db,'fixture/repo',get)['history_complete']);self.assertEqual(self.db.execute('select count(*) from monitoring_runs').fetchone()[0],0);self.assertEqual(self.db.execute('select count(*) from platform_workflow_runs').fetchone()[0],1)
 def test_next_schedule_keeps_four_existing_utc_opportunities(self):
  r=evaluation(self.db,None,datetime(2026,11,5,1,18,tzinfo=timezone.utc));self.assertEqual(r['next_schedule_utc'],'2026-11-05T07:17:00+00:00')

 def test_full_window_requires_seven_elapsed_days_and_all_publications(self):
  for day in range(1,8):
   for slot in range(4):
    row=self.row(day*10+slot);row['created_at']=f'2026-10-{day:02d}T{6+slot*4:02d}:00:00Z';self.insert(row)
  r=evaluation(self.db,'2026-10-01T00:00:00-04:00',datetime(2026,10,7,23,tzinfo=timezone.utc));self.assertFalse(r['completed']);self.assertEqual(len(r['completed_days']),6)
  r=evaluation(self.db,'2026-10-01T00:00:00-04:00',datetime(2026,10,8,12,tzinfo=timezone.utc));self.assertTrue(r['completed'])
  row=self.row(70);row['created_at']='2026-10-07T06:00:00Z';row['jobs'][-1]['conclusion']='failure'
  with self.db:self.db.execute('update platform_workflow_runs set payload=? where run_id=?',(json.dumps(row),'70'))
  self.assertFalse(evaluation(self.db,'2026-10-01T00:00:00-04:00',datetime(2026,10,8,12,tzinfo=timezone.utc))['completed'])
