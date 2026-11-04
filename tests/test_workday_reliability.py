import unittest,json
from scout.sources.workday import fetch
from scout.sources.base import SourceError
class HistoricalWorkdayTests(unittest.TestCase):
 def source(self):return dict(careers_url='https://firm.wd1.myworkdayjobs.com/External',_validated_posting_urls={'r1':['https://firm.wd1.myworkdayjobs.com/en-US/External/job/Boston/Finance-Intern_R1/apply']})
 def getter(self,change=None):
  def get(url,payload=None):
   if payload is not None:return dict(total=1,jobPostings=[dict(bulletFields=['R1'])])
   info=dict(jobReqId='R1',title='Finance Intern',posted=True,canApply=True,jobDescription='Pursuing a bachelor degree.',externalUrl='https://firm.wd1.myworkdayjobs.com/en-US/External/job/Boston/Finance-Intern_R1',country=dict(descriptor='United States'),location='Boston, MA')
   if change:info.update(change)
   return dict(jobPostingInfo=info)
  return get
 def test_history_requires_live_positive_exact_detail(self):
  s=fetch(self.source(),get=self.getter());self.assertEqual(len(s.jobs),1);self.assertEqual(s.jobs[0].external_job_id,'R1');self.assertEqual(len(s.inventory_evidence['historical_identity_recoveries']),1)
 def test_unavailable_or_identity_mismatch_cannot_become_complete(self):
  for change in (dict(posted=False),dict(canApply=False),dict(posted=None),dict(jobReqId='R2'),dict(jobDescription='')):
   with self.subTest(change=change),self.assertRaises(SourceError):fetch(self.source(),get=self.getter(change))
 def test_different_board_cannot_supply_identity(self):
  s=self.source();s['_validated_posting_urls']['r1']=['https://firm.wd1.myworkdayjobs.com/Students/job/Boston/Finance-Intern_R1']
  with self.assertRaises(SourceError):fetch(s,get=self.getter())
 def test_changed_detail_publication_is_rejected(self):
  with self.assertRaises(SourceError):fetch(self.source(),get=self.getter(dict(externalUrl='https://other.wd1.myworkdayjobs.com/External/job/Boston/Finance-Intern_R1')))
 def test_no_history_means_no_guessed_path(self):
  s=self.source();s.pop('_validated_posting_urls')
  with self.assertRaises(SourceError):fetch(s,get=self.getter())

class ProvenanceAuditTests(unittest.TestCase):
 def test_historical_capture_gains_inventory_without_resetting_discovery(self):
  from scout.db import connect,company,source
  from scout.ingest import upsert,run_source
  from scout.model import Job,now
  from scout.sources.base import Snapshot
  from scout.provenance import audit
  from scout.source_roles import set_role
  from scout.feed import feed
  c=connect(':memory:');self.addCleanup(c.close);cid=company(c,'Fixture');sid=source(c,cid,'greenhouse','fixture','https://boards.greenhouse.io/fixture')
  job=Job(title='Finance Intern',external_job_id='R1',description='Currently pursuing a bachelor degree.',locations=['Boston, MA'],countries=['US'],canonical_apply_url='https://boards.greenhouse.io/fixture/jobs/1')
  with c:jid,_=upsert(c,cid,None,job,now(),first_seen='2020-01-01T00:00:00+00:00')
  self.assertEqual(audit(c)['reasons'].get('NO_INVENTORY_MEMBERSHIP'),1)
  run_source(c,dict(c.execute('select * from sources where id=?',(sid,)).fetchone()),fetcher=lambda _:Snapshot([job]))
  self.assertEqual(audit(c)['reasons'].get('SOURCE_ROLE_UNVALIDATED'),1)
  with c:set_role(c,sid,'AUTHORITATIVE',dict(official_url='https://fixture.test/careers',reason='Fixture official handoff'))
  self.assertEqual(audit(c)['trusted_current'],1);self.assertEqual(c.execute('select first_seen_at from jobs where id=?',(jid,)).fetchone()[0],'2020-01-01T00:00:00+00:00');self.assertEqual(feed(c,'7d',include_other=True)['total'],0);self.assertEqual(feed(c,'inventory',include_other=True)['total'],1)

class PublicTransientRetryTests(unittest.TestCase):
 def test_gateway_error_retried_once_and_metered(self):
  from unittest.mock import patch,MagicMock
  from urllib.error import HTTPError
  from scout.sources.base import get_json,request_metrics
  response=MagicMock();response.__enter__.return_value.read.return_value=b'{"jobs":[]}'
  with patch('scout.sources.transport.open_url',side_effect=[HTTPError('https://firm.test',502,'gateway',{},None),response]) as opened,patch('scout.sources.base.time.sleep'):
   with request_metrics() as cost:self.assertEqual(get_json('https://firm.test'),{'jobs':[]})
   self.assertEqual(opened.call_count,2);self.assertEqual(cost['transient_http_retries'],1)
 def test_denials_and_long_retry_after_are_not_retried(self):
  from unittest.mock import patch
  from urllib.error import HTTPError
  from scout.sources.base import get_json
  for code,headers in ((403,{}),(429,{'Retry-After':'60'}),(503,{'Retry-After':'60'})):
   with patch('scout.sources.transport.open_url',side_effect=HTTPError('https://firm.test',code,'denied',headers,None)) as opened:
    with self.assertRaises(SourceError):get_json('https://firm.test')
    self.assertEqual(opened.call_count,1)
 def test_repeated_gateway_errors_stop_after_second_attempt(self):
  from unittest.mock import patch
  from urllib.error import HTTPError
  from scout.sources.base import get_json
  with patch('scout.sources.transport.open_url',side_effect=HTTPError('https://firm.test',502,'gateway',{},None)) as opened,patch('scout.sources.base.time.sleep'):
   with self.assertRaises(SourceError):get_json('https://firm.test')
   self.assertEqual(opened.call_count,2)

class RecoveryCooldownTests(unittest.TestCase):
 def test_manual_access_failure_cannot_be_immediately_recrawled(self):
  from scout.db import connect,company,source
  from scout.scan_queue import initialize,sync,claim
  from scout.source_roles import set_role,review_scope
  c=connect(':memory:');self.addCleanup(c.close);cid=company(c,'Fixture');sid=source(c,cid,'lever','f','https://jobs.lever.co/f')
  with c:set_role(c,sid,'AUTHORITATIVE',dict(official_url='https://fixture.test',reason='Fixture'));review_scope(c,cid,[sid],dict(official_url='https://fixture.test',scope_reason='Fixture'))
  initialize(c)
  with c:
   c.execute("update sources set health_status='BLOCKED',last_response_status=403,last_checked_at='2026-11-04T12:00:00+00:00' where id=?",(sid,))
   c.execute("insert into scan_queue(source_id,due_at) values(?,'2026-11-04T11:00:00+00:00')",(sid,))
  self.assertEqual(claim(c,at='2026-11-04T12:01:00+00:00')[1],[]);self.assertEqual(c.execute('select due_at from scan_queue').fetchone()[0],'2026-11-05T12:00:00+00:00')
 def test_manual_timeout_gets_backoff_without_inflating_attempts(self):
  from scout.db import connect,company,source
  from scout.scan_queue import initialize,sync
  from scout.source_roles import set_role,review_scope
  c=connect(':memory:');self.addCleanup(c.close);cid=company(c,'Fixture');sid=source(c,cid,'lever','f','https://jobs.lever.co/f')
  with c:set_role(c,sid,'AUTHORITATIVE',dict(official_url='https://fixture.test',reason='Fixture'));review_scope(c,cid,[sid],dict(official_url='https://fixture.test',scope_reason='Fixture'))
  initialize(c)
  with c:c.execute("update sources set health_status='TIMEOUT',last_checked_at='2026-11-04T12:00:00+00:00' where id=?",(sid,));c.execute("insert into scan_queue(source_id,due_at) values(?,'2026-11-04T11:00:00+00:00')",(sid,))
  sync(c);sync(c);self.assertEqual(tuple(c.execute('select due_at,attempts from scan_queue').fetchone()),('2026-11-04T12:15:00+00:00',1))
