from copy import deepcopy
from datetime import datetime, timedelta, timezone
import json
from pathlib import Path
import unittest
from scout.db import connect, company, source
from scout.model import Job
from scout.sources.workday import fetch, configuration, normalize_posting
from scout.sources.base import SourceError, Snapshot
from scout.ingest import run_source, upsert, consolidate_workday_duplicates
from scout.queries import jobs
from scout.coverage import company_state, source_state, summary, provider_hint, write_report
from scout.coverage import run_audit, probe
from unittest.mock import patch
from contextlib import redirect_stdout
import io
from scout.reconcile import scoped_legacy
import tempfile

FIXTURES = Path(__file__).parent/'fixtures/workday'
T0='2026-10-01T12:00:00+00:00'
T1='2026-10-02T12:00:00+00:00'


class WorkdayTests(unittest.TestCase):
    def setUp(self):
        self.pages=json.loads((FIXTURES/'pages.json').read_text())
        self.details=json.loads((FIXTURES/'details.json').read_text())
        self.source=dict(ats_type='workday',ats_identifier='example/External',careers_url='https://example.wd5.myworkdayjobs.com/en-us/External/job/NY/old_R9/apply')
        self.calls=[]

    def get(self,url,payload=None):
        self.calls.append((url,payload))
        if payload is not None:
            return deepcopy(self.pages[0 if payload['offset']==0 else 1])
        return deepcopy(self.details[url.rsplit('_',1)[-1]])

    def test_pagination_and_full_unfiltered_inventory(self):
        snapshot=fetch(self.source,self.get)
        self.assertEqual(len(snapshot.jobs),3)
        self.assertTrue(snapshot.authoritative)
        listing_calls=[p for _,p in self.calls if p is not None]
        self.assertEqual([p['offset'] for p in listing_calls],[0,2,0])
        self.assertTrue(all(p['searchText']=='' and p['appliedFacets']=={} for p in listing_calls))
        self.assertEqual(len(self.calls),5)

    def test_later_pages_zero_total_sentinel(self):
        self.pages[1]['total']=0
        self.assertEqual(len(fetch(self.source,self.get).jobs),3)

    def test_workday_two_thousand_result_cap_is_not_authoritative(self):
        self.pages[0]['total']=2000
        self.pages[0]['facets']=[{'facetParameter':'timeType','values':[{'count':3400},{'count':81}]}]
        with self.assertRaisesRegex(SourceError,'cap suspected'):
            fetch(self.source,self.get)
        self.assertEqual(len(self.calls),1)

    def test_uncapped_declared_total_above_two_thousand_can_complete(self):
        inventory=[dict(title='Senior Analyst',externalPath=f'/job/NY/Analyst_R{i}',bulletFields=[f'R{i}']) for i in range(2101)]
        def get(url,payload=None):
            offset=payload['offset']
            return dict(total=2101 if offset==0 else 0,jobPostings=inventory[offset:offset+20])
        self.assertEqual(len(fetch(self.source,get).jobs),2101)

    def test_normalization_and_missing_posted_date(self):
        jobs=fetch(self.source,self.get).jobs
        self.assertEqual(jobs[0].external_job_id,'R100')
        self.assertEqual(jobs[0].locations,['Boston, MA','New York, NY'])
        self.assertIn('Undergraduate',jobs[0].description)
        self.assertEqual(jobs[0].employment_type,'Full time')
        self.assertEqual(jobs[0].posted_at,'2026-10-01T00:00:00+00:00')
        self.assertTrue(jobs[0].canonical_apply_url.endswith('/apply'))
        self.assertIsNone(jobs[1].posted_at)
        self.assertEqual(jobs[1].posted_at_confidence,'UNKNOWN')

    def test_root_locale_deep_and_api_url_variants(self):
        for path in ['/External','/en-US/External','/en-us/External/job/Boston/Test_R1/apply','/wday/cxs/example/External/jobs']:
            with self.subTest(path=path):
                source=dict(self.source,careers_url='https://example.wd103.myworkdayjobs.com'+path)
                self.assertEqual(configuration(source),('https://example.wd103.myworkdayjobs.com','example','External'))
        for url in ['https://example.com/External','https://example.wd5.myworkdayjobs.com/','http://example.wd5.myworkdayjobs.com/External','https://example.wd5.myworkdayjobs.com/wday/cxs/other/External/jobs']:
            with self.assertRaises(SourceError):
                configuration(dict(self.source,careers_url=url))

    def test_page_total_change_early_end_and_repeated_path_fail(self):
        for mutation in ['changed','empty','duplicate','missing_total']:
            with self.subTest(mutation=mutation):
                self.setUp()
                if mutation=='changed': self.pages[1]['total']=4
                if mutation=='empty': self.pages[1]['jobPostings']=[]
                if mutation=='duplicate': self.pages[1]['jobPostings']=[self.pages[0]['jobPostings'][0]]
                if mutation=='missing_total': self.pages[0].pop('total')
                with self.assertRaises(SourceError): fetch(self.source,self.get)

    def test_bad_detail_and_cross_tenant_urls_fail(self):
        for mutation in ['missing','unavailable','wrong_board','no_description','wrong_requisition']:
            with self.subTest(mutation=mutation):
                self.setUp()
                info=self.details['R100']['jobPostingInfo']
                if mutation=='missing': self.details['R100']={}
                if mutation=='unavailable': info['canApply']=False
                if mutation=='wrong_board': info['externalUrl']='https://other.wd5.myworkdayjobs.com/External/job/a_R100'
                if mutation=='no_description': info.pop('jobDescription')
                if mutation=='wrong_requisition': info['jobReqId']='OTHER'
                with self.assertRaises(SourceError): fetch(self.source,self.get)

    def test_duplicate_requisition_ads_fail_instead_of_unstable_merge(self):
        self.pages[0]['jobPostings'][1]['bulletFields']=['R100']
        self.pages[0]['jobPostings'][1]['title']='Senior Analyst'
        self.pages[0]['jobPostings'][1]['externalPath']='/job/NY/Senior-Analyst_R100'
        with self.assertRaises(SourceError):
            fetch(self.source,self.get)

    def test_legacy_fulltime_identity_gets_details(self):
        self.source['_legacy_ids']=['r300']
        self.details['R300']={'jobPostingInfo':{'title':'Senior Finance Manager','jobReqId':'R300','jobDescription':'<p>Existing tracked position</p>'}}
        self.assertEqual(fetch(self.source,self.get).jobs[2].description,'Existing tracked position')

    def test_adapter_ingestion_idempotence_and_failed_empty_safety(self):
        db=connect(':memory:')
        cid=company(db,'Workday Fixture Employer')
        sid=source(db,cid,'workday','example/External',self.source['careers_url'])
        db.commit()
        s=dict(db.execute('SELECT * FROM sources WHERE id=?',(sid,)).fetchone())
        fetcher=lambda prepared:fetch(prepared,self.get)
        run_source(db,s,fetcher,T0)
        run_source(db,s,fetcher,T1)
        self.assertEqual(db.execute('SELECT COUNT(*) FROM jobs').fetchone()[0],2)
        self.assertEqual({r[0] for r in db.execute('SELECT first_seen_at FROM jobs')},{T0})
        self.pages[0]['total']=2000
        self.assertEqual(run_source(db,s,fetcher,T1)['status'],'PARSER_ERROR')
        self.assertEqual(db.execute('SELECT COUNT(*) FROM jobs WHERE active=1').fetchone()[0],2)
        self.pages[0]['total']=3
        self.pages[1]['jobPostings']=[]
        self.assertEqual(run_source(db,s,fetcher,T1)['status'],'PARSER_ERROR')
        self.pages[0]={'total':0,'jobPostings':[]}
        self.assertEqual(run_source(db,s,fetcher,T1)['status'],'PARSER_ERROR')
        self.assertEqual(db.execute('SELECT COUNT(*) FROM jobs WHERE active=1').fetchone()[0],2)
        db.close()


class ReconciliationTests(unittest.TestCase):
    def setUp(self):
        self.db=connect(':memory:')
        self.cid=company(self.db,'Known Employer')
        self.sid=source(self.db,self.cid,'workday','example/External','https://example.wd5.myworkdayjobs.com/External')
        self.source=dict(self.db.execute('SELECT * FROM sources WHERE id=?',(self.sid,)).fetchone())
        self.url='https://example.wd5.myworkdayjobs.com/en-US/External/job/Boston/Finance-Intern_R100'
        self.legacy_job=Job(title='Finance Intern',external_job_id='R100',source_url=self.url,locations=['Boston, MA'])
        self.jid,_=upsert(self.db,self.cid,None,self.legacy_job,T0)
        self.db.execute('INSERT INTO legacy_records VALUES(?,?,?)',('old',self.jid,json.dumps(dict(requisitionId='R100',applicationLink=self.url))))
        self.db.commit()

    def tearDown(self): self.db.close()

    def row(self): return self.db.execute('SELECT * FROM jobs WHERE id=?',(self.jid,)).fetchone()

    def ingest(self,listings,stamp=T1):
        return run_source(self.db,self.source,lambda s:Snapshot(listings),stamp)

    def other(self):
        return Job(title='Senior Analyst',external_job_id='R900',source_url='https://example.wd5.myworkdayjobs.com/External/job/NY/Senior-Analyst_R900')

    def test_legacy_failed_and_suspicious_empty_stay_active(self):
        def fail(s): raise SourceError('BLOCKED','fixture')
        run_source(self.db,self.source,fail,T1)
        self.assertTrue(self.row()['active'])
        self.assertEqual(self.ingest([])['status'],'PARSER_ERROR')
        self.assertTrue(self.row()['active'])
        self.assertEqual(self.db.execute('SELECT COUNT(*) FROM job_verifications').fetchone()[0],0)

    def test_legacy_missing_closes_only_on_own_authoritative_board(self):
        result=self.ingest([self.other()])
        self.assertTrue(result['status'].startswith('SUCCESS'))
        self.assertFalse(self.row()['active'])
        self.assertEqual(self.row()['last_seen_at'],T0)
        self.assertEqual(self.db.execute('SELECT outcome FROM job_verifications').fetchone()[0],'MISSING')

    def test_wrong_board_does_not_close_migrated_role(self):
        self.source['careers_url']='https://example.wd5.myworkdayjobs.com/Other'
        self.source['ats_identifier']='example/Other'
        self.ingest([self.other()])
        self.assertTrue(self.row()['active'])

    def test_found_and_duplicate_workday_preserve_first_seen(self):
        self.ingest([self.legacy_job,self.other()])
        self.ingest([self.legacy_job,self.other()],'2026-10-03T12:00:00+00:00')
        self.assertEqual(self.db.execute('SELECT COUNT(*) FROM jobs').fetchone()[0],1)
        self.assertEqual(self.row()['first_seen_at'],T0)
        self.assertEqual(self.row()['last_seen_at'],'2026-10-03T12:00:00+00:00')
        self.assertEqual(self.db.execute('SELECT COUNT(*) FROM source_jobs').fetchone()[0],1)

    def test_unsupported_and_company_program_urls_do_not_prove_absence(self):
        self.db.execute('UPDATE legacy_records SET payload=?',(json.dumps(dict(requisitionId='R100',applicationLink='https://employer.example/students')),))
        self.db.commit()
        self.ingest([self.other()])
        self.assertTrue(self.row()['active'])

    def test_historical_metrics_use_original_active_job_denominator(self):
        audit=self.db.execute('INSERT INTO coverage_audits(started_at,baseline_active_legacy_jobs) VALUES(?,?)',(T0,json.dumps([self.jid]))).lastrowid
        self.db.commit()
        self.ingest([self.other()])
        report=summary(self.db,at=datetime(2026,10,2,15,tzinfo=timezone.utc),audit_id=audit)
        self.assertEqual(report['metrics']['historical_active_safely_closed'],1)
        self.assertEqual(report['metrics']['historical_active_unresolved'],0)

    def test_same_workday_tenant_cross_board_deduplication(self):
        self.ingest([self.legacy_job])
        sid=source(self.db,self.cid,'workday','example/Campus','https://example.wd5.myworkdayjobs.com/Campus')
        self.db.commit()
        s=dict(self.db.execute('SELECT * FROM sources WHERE id=?',(sid,)).fetchone())
        listing=Job(title='Finance Intern',external_job_id='R100',source_url=self.url.replace('/External/','/Campus/'))
        run_source(self.db,s,lambda s:Snapshot([listing]),T1)
        self.assertEqual(self.db.execute('SELECT COUNT(*) FROM jobs').fetchone()[0],1)
        self.assertEqual(self.db.execute('SELECT COUNT(*) FROM source_jobs').fetchone()[0],2)
        sid=source(self.db,self.cid,'workday','other/External','https://other.wd5.myworkdayjobs.com/External')
        self.db.commit()
        s=dict(self.db.execute('SELECT * FROM sources WHERE id=?',(sid,)).fetchone())
        listing.source_url='https://other.wd5.myworkdayjobs.com/External/job/Finance_R100'
        listing.canonical_apply_url=listing.source_url
        run_source(self.db,s,lambda s:Snapshot([listing]),T1)
        self.assertEqual(self.db.execute('SELECT COUNT(*) FROM jobs').fetchone()[0],2)

    def test_consolidation_keeps_history_and_memberships(self):
        self.ingest([self.legacy_job])
        columns=[r[1] for r in self.db.execute('PRAGMA table_info(jobs)') if r[1]!='id']
        self.db.execute('INSERT INTO jobs('+','.join(columns)+') SELECT '+','.join(columns)+' FROM jobs WHERE id=?',(self.jid,))
        duplicate=self.db.execute('SELECT MAX(id) FROM jobs').fetchone()[0]
        sid=source(self.db,self.cid,'workday','example/Campus','https://example.wd5.myworkdayjobs.com/Campus')
        self.db.execute('INSERT INTO source_jobs(source_id,identity,job_id,external_job_id,first_seen_at,last_seen_at) VALUES(?,?,?,?,?,?)',(sid,'id:R100',duplicate,'R100',T1,T1))
        self.db.commit()
        merged=consolidate_workday_duplicates(self.db)
        self.assertEqual(len(merged),1)
        self.assertEqual(self.db.execute('SELECT COUNT(*) FROM jobs').fetchone()[0],2)
        self.assertEqual(self.db.execute('SELECT COUNT(*) FROM source_jobs WHERE job_id=?',(self.jid,)).fetchone()[0],2)
        self.assertEqual(jobs(self.db,window='all',active=False)['total'],1)
        self.assertEqual(consolidate_workday_duplicates(self.db),[])


class CoverageTests(unittest.TestCase):
    def test_reachable_page_and_malformed_url_are_not_monitoring_success(self):
        s=dict(id=1,ats_type='custom',ats_identifier='',careers_url='https://employer.example')
        self.assertEqual(source_state(s,dict(status='REACHABLE_PORTAL_ONLY')),'CUSTOM_PORTAL_REQUIRED')
        self.assertEqual(probe(dict(s,careers_url='https://[invalid'))['status'],'BROKEN_CAREERS_URL')

    def test_audit_accounts_for_every_active_company(self):
        db=connect(':memory:')
        a=company(db,'Working Board');b=company(db,'Blocked Board');c=company(db,'No Source')
        source(db,a,'greenhouse','working','https://job-boards.greenhouse.io/working')
        source(db,b,'workday','blocked/External','https://blocked.wd1.myworkdayjobs.com/External')
        source(db,c,'custom')
        db.commit()
        def fetch(source):
            if source['ats_type']=='workday': raise SourceError('BLOCKED','test challenge')
            return Snapshot([Job(title='Finance Intern',external_job_id='1',source_url='https://job-boards.greenhouse.io/working/jobs/1',locations=['Boston, MA'])])
        with tempfile.TemporaryDirectory() as directory, patch('scout.coverage.fetch',fetch), redirect_stdout(io.StringIO()):
            result=run_audit(db,directory,workers=2)
        self.assertEqual(len(result['companies']),3)
        self.assertEqual(result['metrics']['successfully_monitored_companies'],1)
        self.assertEqual(result['metrics']['known_companies'],3)
        self.assertEqual(result['company_states'],{'BLOCKED':1,'NO_SOURCE_CONFIGURED':1,'MONITORED_SUCCESSFULLY':1})
        self.assertIsNotNone(db.execute('SELECT completed_at FROM coverage_audits').fetchone()[0])
        db.close()

    def test_company_state_assignment(self):
        cases=[([], 'NO_SOURCE_CONFIGURED'),(['NO_SOURCE_CONFIGURED'],'NO_SOURCE_CONFIGURED'),
               (['MONITORED_SUCCESSFULLY'],'MONITORED_SUCCESSFULLY'),(['NO_PUBLIC_JOBS'],'NO_PUBLIC_JOBS'),
               (['MONITORED_SUCCESSFULLY','CUSTOM_PORTAL_REQUIRED'],'PARTIALLY_MONITORED'),
               (['BLOCKED'],'BLOCKED'),(['PARSER_FAILURE'],'PARSER_FAILURE'),
               (['BROKEN_CAREERS_URL'],'BROKEN_CAREERS_URL'),(['UNSUPPORTED_ATS'],'UNSUPPORTED_ATS'),
               (['CUSTOM_PORTAL_REQUIRED'],'CUSTOM_PORTAL_REQUIRED'),(['UNKNOWN'],'UNKNOWN')]
        for states,expected in cases:
            self.assertEqual(company_state(states),expected)

    def test_untested_and_stale_sources_are_not_monitored(self):
        source=dict(ats_type='workday',ats_identifier='firm/External',careers_url='https://firm.wd1.myworkdayjobs.com/External',health_status='NEVER_CHECKED',last_successful_check_at=None,last_response_status=None)
        at=datetime(2026,10,5,tzinfo=timezone.utc)
        self.assertEqual(source_state(source,at=at),'UNKNOWN')
        source.update(health_status='SUCCESS',last_successful_check_at=T0,jobs_returned=3)
        self.assertEqual(source_state(source,at=at),'UNKNOWN')

    def test_provider_fingerprints(self):
        self.assertEqual(provider_hint('https://company.csod.com/careers')[0],'cornerstone')
        self.assertEqual(provider_hint('https://employer.example', '<script src="https://tbcdn.talentbrew.com/x"></script>')[0],'talentbrew')
        self.assertEqual(provider_hint('https://employer.example')[0],'custom_unidentified')

    def test_metrics_do_not_double_count_multisource_companies(self):
        db=connect(':memory:')
        cid=company(db,'Partially Covered')
        source(db,cid,'greenhouse','a','https://job-boards.greenhouse.io/a')
        source(db,cid,'custom','','https://employer.example')
        with db:
            db.execute("UPDATE sources SET health_status='SUCCESS',last_successful_check_at=?,jobs_returned=5 WHERE ats_type='greenhouse'",(T1,))
        report=summary(db,at=datetime(2026,10,2,15,tzinfo=timezone.utc))
        self.assertEqual(report['metrics']['known_companies'],1)
        self.assertEqual(report['metrics']['successfully_monitored_companies'],0)
        self.assertEqual(report['metrics']['partially_monitored_companies'],1)
        self.assertEqual(report['metrics']['healthy_sources'],1)
        with tempfile.TemporaryDirectory() as directory:
            write_report(report,directory)
            self.assertTrue((Path(directory)/'coverage.json').exists())
        db.close()
