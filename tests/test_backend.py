from dataclasses import replace
from datetime import datetime, timezone
import json
from pathlib import Path
import tempfile
import unittest
from scout.db import connect, company, source
from scout.model import Job, normalize, canonical_url, date, text
from scout.classify import classify
from scout.ingest import upsert, run_source
from scout.importers import import_companies, migrate_legacy
from scout.queries import jobs
from scout.sources import greenhouse, lever, ashby, smartrecruiters, detect
from scout.sources.base import Snapshot, SourceError
from scout.__main__ import locked
from scout.web import table

T0 = '2026-10-01T12:00:00+00:00'
T1 = '2026-10-02T12:00:00+00:00'
T2 = '2026-10-03T12:00:00+00:00'


def job(jid='1', **kwargs):
    values = dict(title='Corporate Finance Intern', external_job_id=jid,
                  source_url='https://employer.example/jobs/' + jid, locations=['New York, NY'])
    values.update(kwargs)
    return Job(**values)


class RulesTests(unittest.TestCase):
    def test_normalization(self):
        self.assertEqual(normalize('<p>Corporate STRATEGY — Intern</p>'), 'corporate strategy intern')
        self.assertEqual(text('Finance Intern (FP&A)'), 'Finance Intern (FP&A)')
        self.assertEqual(canonical_url('https://EXAMPLE.com/job/?jobId=2&utm_source=x#top'), 'https://example.com/job?jobId=2')
        self.assertNotEqual(canonical_url('https://example.com/?jobId=2'), canonical_url('https://example.com/?jobId=3'))
        self.assertIsNone(date('Posted 3 Days Ago'))
        self.assertIsNone(date('2026-02-30'))
        self.assertEqual(job(posted_at='unavailable').clean().posted_at, None)

    def test_categories(self):
        cases = [('Corporate Strategy Intern', 'STRATEGY', 'Corporate Strategy'),
                 ('Strategy & Operations Intern', 'STRATEGY', 'Strategy & Operations'),
                 ('Business Transformation Internship', 'STRATEGY', 'Business Transformation'),
                 ('Corporate Development Intern', 'STRATEGY', 'Corporate Development'),
                 ('Strategic Initiatives Intern', 'STRATEGY', 'Strategic Initiatives'),
                 ('FP&A Intern', 'FINANCE', 'FP&A'),
                 ('Financial Planning & Analysis Intern', 'FINANCE', 'FP&A'),
                 ('Treasury Summer Analyst', 'FINANCE', 'Treasury'),
                 ('Strategic Finance Intern', 'FINANCE', 'Strategic Finance'),
                 ('Investment Banking Summer Analyst', 'FINANCE', 'Investment Banking'),
                 ('Commercial Banking Intern', 'FINANCE', 'Commercial Banking'),
                 ('Asset Management Intern', 'FINANCE', 'Asset Management'),
                 ('Wealth Management Intern', 'FINANCE', 'Wealth Management'),
                 ('Capital Markets Intern', 'FINANCE', 'Capital Markets'),
                 ('Finance Analytics Intern', 'FINANCE', 'Finance Analytics'),
                 ('Risk Intern', 'FINANCE', 'Risk'),
                 ('Management Consulting Intern', 'CONSULTING', 'Management Consulting'),
                 ('Strategy Consulting Intern', 'CONSULTING', 'Strategy Consulting'),
                 ('Transaction Advisory Intern', 'CONSULTING', 'Transaction Advisory'),
                 ('Operations Consulting Intern', 'CONSULTING', 'Operations Consulting'),
                 ('Advisory Intern', 'CONSULTING', 'Advisory')]
        for title, cat, sub in cases:
            with self.subTest(title=title):
                result = classify(job(title=title))
                self.assertEqual((result['function'], result['sub_function']), (cat, sub))
                self.assertTrue(result['retain'])

    def test_screening(self):
        for title in ['Senior Finance Manager', 'Quality Assurance Intern', 'Software Engineering Intern']:
            with self.subTest(title=title):
                self.assertFalse(classify(job(title=title))['retain'])
        ambiguous = classify(job(title='Business Intern', description='Work with our finance team.'))
        self.assertTrue(ambiguous['retain'])
        self.assertEqual(ambiguous['function'], 'OTHER')
        self.assertTrue(ambiguous['needs_review'])
        self.assertFalse(classify(job(countries=['GB'], locations=['London']))['retain'])
        self.assertTrue(classify(job(locations=['Remote']))['needs_review'])
        self.assertTrue(classify(job(title='Summer Associate'))['needs_review'])
        self.assertTrue(classify(job(title='MBA Strategic Finance Intern'))['needs_review'])
        self.assertTrue(classify(job(title='Corporate Finance Co-Op'))['retain'])
        self.assertTrue(classify(job(title='Campus Finance Program'))['retain'])


class DatabaseTests(unittest.TestCase):
    def setUp(self):
        self.db = connect(':memory:')
        self.cid = company(self.db, 'Private Boutique')
        self.sid = source(self.db, self.cid, 'greenhouse', 'boutique')
        self.db.commit()

    def tearDown(self):
        self.db.close()

    def src(self, sid=None):
        return self.db.execute('SELECT * FROM sources WHERE id=?', (sid or self.sid,)).fetchone()

    def ingest(self, listings, stamp=T0, **kwargs):
        return run_source(self.db, self.src(), lambda s: Snapshot(listings, **kwargs), stamp)

    def count(self):
        return self.db.execute('SELECT COUNT(*) FROM jobs').fetchone()[0]

    def test_dedupe_and_dates(self):
        self.ingest([job()])
        self.ingest([job(description='Updated description')], T1)
        row = self.db.execute('SELECT * FROM jobs').fetchone()
        self.assertEqual(self.count(), 1)
        self.assertEqual(row['first_seen_at'], T0)
        self.assertEqual(row['last_seen_at'], T1)
        self.assertIsNone(row['posted_at'])
        self.assertEqual(row['posted_at_confidence'], 'UNKNOWN')
        self.assertEqual(row['description'], 'Updated description')

    def test_explicit_date_preserved_when_absent_later(self):
        self.ingest([job(posted_at='2026-09-30', posted_at_confidence='EMPLOYER_EXPLICIT')])
        self.ingest([job()], T1)
        self.assertEqual(self.db.execute('SELECT posted_at FROM jobs').fetchone()[0], '2026-09-30T00:00:00+00:00')

    def test_repost_distinct_locations_and_shared_url(self):
        self.ingest([job()])
        result = self.ingest([job(), job('2'), job('3', locations=['Boston, MA'])], T1)
        self.assertEqual(self.count(), 3)
        self.assertEqual(result['events']['POSSIBLE_REPOST'], 1)
        self.assertIsNotNone(self.db.execute("SELECT repost_of FROM jobs WHERE external_job_id='2'").fetchone()[0])
        self.assertIsNone(self.db.execute("SELECT repost_of FROM jobs WHERE external_job_id='3'").fetchone()[0])
        with self.db:
            upsert(self.db, self.cid, None, job('4', source_url='https://example.com/program'), T0)
            upsert(self.db, self.cid, None, job('5', source_url='https://example.com/program'), T0)
        self.assertEqual(self.count(), 5)

    def test_fallback_and_url_identity(self):
        for _ in range(2):
            with self.db:
                upsert(self.db, self.cid, None, job('', source_url=''), T0)
                upsert(self.db, self.cid, None, job('', source_url='https://example.com/a?utm_source=x'), T0)
        self.assertEqual(self.count(), 2)
        with self.db:
            upsert(self.db, self.cid, None, job('', source_url='', locations=['Boston, MA']), T0)
        self.assertEqual(self.count(), 3)

    def test_atomic_failure_and_health_recovery(self):
        self.ingest([job()])
        bad = self.ingest([job('2'), job('3', title='')], T1)
        self.assertEqual(bad['status'], 'PARSER_ERROR')
        self.assertEqual(self.count(), 1)
        self.assertEqual(self.db.execute('SELECT active FROM jobs').fetchone()[0], 1)
        self.assertEqual(self.src()['consecutive_failures'], 1)
        self.assertEqual(self.src()['last_successful_check_at'], T0)
        self.ingest([job()], T2)
        self.assertEqual(self.src()['consecutive_failures'], 0)
        self.assertEqual(self.src()['last_successful_check_at'], T2)

    def test_failed_crawl_never_closes(self):
        self.ingest([job()])
        for status in ['TIMEOUT', 'HTTP_ERROR', 'BLOCKED', 'PARSER_ERROR', 'ATS_CHANGED', 'UNKNOWN_ERROR']:
            def fail(s):
                raise SourceError(status, 'fixture failure', 403)
            result = run_source(self.db, self.src(), fail, T1)
            self.assertEqual(result['status'], status)
            self.assertTrue(self.db.execute('SELECT active FROM jobs').fetchone()[0])
        self.assertEqual(self.src()['consecutive_failures'], 6)
        self.assertEqual(self.src()['last_successful_check_at'], T0)

    def test_suspicious_zero_and_non_authoritative(self):
        self.ingest([job()])
        self.assertEqual(self.ingest([], T1)['status'], 'PARSER_ERROR')
        self.assertEqual(self.ingest([], T2)['status'], 'PARSER_ERROR')
        self.assertEqual(self.ingest([job('2')], T2, authoritative=False)['status'], 'PARSER_ERROR')
        self.assertEqual(self.count(), 1)
        self.assertTrue(self.db.execute('SELECT active FROM jobs').fetchone()[0])

    def test_authoritative_missing_and_reopen(self):
        self.ingest([job(), job('2')])
        self.ingest([job('2')], T1)
        row = self.db.execute("SELECT * FROM jobs WHERE external_job_id='1'").fetchone()
        self.assertFalse(row['active'])
        self.assertEqual(row['closed_at'], T1)
        self.ingest([job(), job('2')], T2)
        row = self.db.execute("SELECT * FROM jobs WHERE external_job_id='1'").fetchone()
        self.assertTrue(row['active'])
        self.assertIsNone(row['closed_at'])
        self.assertEqual(row['first_seen_at'], T0)

    def test_all_returned_ids_used_even_when_classification_changes(self):
        self.ingest([job()])
        self.ingest([job(title='Software Engineer')], T1)
        self.assertTrue(self.db.execute('SELECT active FROM jobs').fetchone()[0])

    def test_multi_source_provenance_prevents_premature_closure(self):
        self.ingest([job(), job('2')])
        other = source(self.db, self.cid, 'lever', 'other')
        self.db.commit()
        run_source(self.db, self.src(other), lambda s: Snapshot([job()]), T0)
        self.assertEqual(self.count(), 2)
        self.assertEqual(self.db.execute('SELECT COUNT(*) FROM source_jobs WHERE job_id=1').fetchone()[0], 2)
        self.ingest([job('2')], T1)
        self.assertTrue(self.db.execute('SELECT active FROM jobs WHERE id=1').fetchone()[0])

    def test_id_collisions_across_ats_not_merged(self):
        self.ingest([job()])
        other = source(self.db, self.cid, 'lever', 'other')
        self.db.commit()
        run_source(self.db, self.src(other), lambda s: Snapshot([job(source_url='https://other.example/jobs/1')]), T0)
        self.assertEqual(self.count(), 2)

    def test_legacy_requisition_id_can_differ_from_ats_posting_id(self):
        with self.db:
            upsert(self.db, self.cid, None,
                   job('REQ-123', source_url='https://jobs.lever.co/boutique/uuid'), T0)
        self.ingest([job('uuid', source_url='https://jobs.lever.co/boutique/uuid',
                         canonical_apply_url='https://jobs.lever.co/boutique/uuid/apply')], T1)
        self.assertEqual(self.count(), 1)
        row = self.db.execute('SELECT * FROM jobs').fetchone()
        self.assertEqual(row['first_seen_at'], T0)
        self.assertEqual(row['external_job_id'], 'uuid')
        payload = json.loads(self.db.execute('SELECT normalized_payload FROM source_jobs').fetchone()[0])
        self.assertEqual(payload['external_job_id'], 'uuid')

    def test_rollback_after_partial_persistence(self):
        self.ingest([job()])
        # Fail the second database insert, after the first would succeed.
        self.db.execute("""CREATE TRIGGER reject_bad BEFORE INSERT ON jobs
                         WHEN NEW.external_job_id='bad' BEGIN SELECT RAISE(ABORT,'fixture failure'); END""")
        with self.assertLogs('scout.ingest', level='ERROR'):
            result = self.ingest([job('good'), job('bad')], T1)
        self.assertEqual(result['status'], 'UNKNOWN_ERROR')
        self.assertEqual(self.count(), 1)
        self.assertEqual(self.db.execute('SELECT COUNT(*) FROM source_jobs').fetchone()[0], 1)
        self.assertTrue(self.db.execute('SELECT active FROM jobs').fetchone()[0])

    def test_suspicious_partial_drop(self):
        self.ingest([job(str(i)) for i in range(10)])
        self.assertEqual(self.ingest([job('1')], T1)['status'], 'PARSER_ERROR')
        self.assertEqual(self.db.execute('SELECT COUNT(*) FROM jobs WHERE active=1').fetchone()[0], 10)

    def test_success_no_matches(self):
        result = self.ingest([job(title='Software Engineering Intern')])
        self.assertEqual(result['status'], 'SUCCESS_NO_MATCHING_JOBS')
        self.assertEqual(self.count(), 0)
        self.assertEqual(self.src()['jobs_returned'], 1)

    def test_freshness(self):
        self.ingest([job('old', posted_at='2026-09-01', posted_at_confidence='EMPLOYER_EXPLICIT'), job('unknown')])
        at = datetime(2026, 10, 2, 12, tzinfo=timezone.utc)
        effective = jobs(self.db, at=at)
        # An older employer posting discovered recently must still surface.
        self.assertEqual(effective['total'], 2)
        self.assertEqual(effective['jobs'][0]['freshness_basis'], 'DISCOVERED')
        self.assertEqual(jobs(self.db, at=at, basis='discovered')['total'], 2)
        self.assertEqual(jobs(self.db, at=at, basis='posted')['total'], 0)
        self.assertEqual(jobs(self.db, at=at, window='older')['total'], 0)
        self.assertEqual(jobs(self.db, at=at, window='24h')['total'], 2)
        self.assertEqual(jobs(self.db, at=at, window='3d')['total'], 2)
        self.assertEqual(jobs(self.db, at=at, window='today')['total'], 0)
        with self.assertRaises(ValueError):
            jobs(self.db, category="'; DROP TABLE jobs;")

    def test_import_atomic_idempotent_and_neutral(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / 'companies.json'
            path.write_text(json.dumps([dict(company_name='Regional Firm', careers_url='https://jobs.lever.co/regional')]))
            import_companies(self.db, path)
            import_companies(self.db, path)
            self.assertEqual(self.db.execute('SELECT COUNT(*) FROM companies').fetchone()[0], 2)
            self.assertEqual(self.db.execute("SELECT ats_identifier FROM sources WHERE ats_type='lever'").fetchone()[0], 'regional')
            path.write_text(json.dumps([dict(company_name='Rollback Me'), {}]))
            with self.assertRaises(ValueError):
                import_companies(self.db, path)
            self.assertFalse(self.db.execute("SELECT 1 FROM companies WHERE name='Rollback Me'").fetchone())

    def test_legacy_migration_preserves_raw_and_is_idempotent(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / 'data').mkdir()
            rows = [dict(recordId='a', company='Historical Firm', internship='Corporate Finance Intern', requisitionId='r1',
                         applicationLink='https://example.com/r1', location='Boston, MA', firstSeen='2026-08-09',
                         lastVerified='2026-09-01', postingDate='2026-08-09', postingDateLabel='Earliest verified date',
                         listingStatus='Open', fitScore=1, applicationStatus='Applied'),
                    dict(recordId='b', company='Historical Firm', internship='Corporate Finance Intern', requisitionId='r1',
                         applicationLink='https://example.com/r1', location='New York, NY', firstSeen='2026-08-10',
                         lastVerified='2026-09-01', listingStatus='Open')]
            (root / 'data/opportunities.json').write_text(json.dumps(rows))
            migrate_legacy(self.db, root)
            self.assertEqual(self.count(), 1)
            row = self.db.execute('SELECT * FROM jobs').fetchone()
            self.assertEqual(len(json.loads(row['locations'])), 2)
            self.assertIsNone(row['posted_at'])
            self.assertEqual(row['first_seen_at'], '2026-08-09T00:00:00+00:00')
            self.assertEqual(self.db.execute('SELECT COUNT(*) FROM legacy_records').fetchone()[0], 2)
            payload = json.loads(self.db.execute("SELECT payload FROM legacy_records WHERE record_id='a'").fetchone()[0])
            self.assertEqual(payload['applicationStatus'], 'Applied')
            self.assertEqual(migrate_legacy(self.db, root)['legacy_records_imported'], 0)


class AdapterTests(unittest.TestCase):
    def test_greenhouse_does_not_invent_date(self):
        response = {'jobs': [{'id': 1, 'title': 'Finance Intern', 'absolute_url': 'https://example.com/1',
                              'updated_at': T0, 'location': {'name': 'Boston, MA'}, 'content': '<p>Plan budgets</p>'}], 'meta': {'total': 1}}
        listing = greenhouse.fetch({'ats_identifier': 'example'}, lambda url: response).jobs[0]
        self.assertIsNone(listing.posted_at)
        self.assertEqual(listing.description, 'Plan budgets')
        response['meta']['total'] = 2
        with self.assertRaises(SourceError):
            greenhouse.fetch({'ats_identifier': 'example'}, lambda url: response)
        with self.assertRaises(SourceError):
            greenhouse.fetch({'ats_identifier': 'example'}, lambda url: {})

    def test_lever_pagination(self):
        calls = []
        def get(url):
            calls.append(url)
            if 'skip=0' in url:
                return [dict(id=str(n), text='Finance Intern', hostedUrl=f'https://example.com/{n}', applyUrl=f'https://example.com/{n}/apply') for n in range(100)]
            return []
        snapshot = lever.fetch({'ats_identifier': 'example'}, get)
        self.assertEqual(len(snapshot.jobs), 100)
        self.assertEqual(len(calls), 2)
        self.assertIsNone(snapshot.jobs[0].posted_at)
        with self.assertRaises(SourceError):
            lever.fetch({'ats_identifier': 'example'}, lambda url: {})

    def test_ashby_country_locations_and_published(self):
        response = dict(jobs=[dict(title='Finance Intern', jobUrl='https://jobs.ashbyhq.com/firm/uuid',
                                   applyUrl='https://jobs.ashbyhq.com/firm/uuid/application', publishedAt=T0,
                                   location='Boston', address={'postalAddress': {'addressCountry': 'USA'}},
                                   secondaryLocations=[{'location': 'New York'}])])
        listing = ashby.fetch({'ats_identifier': 'firm'}, lambda url: response).jobs[0]
        self.assertEqual(listing.external_job_id, 'uuid')
        self.assertEqual(listing.posted_at, T0)
        self.assertEqual(len(listing.locations), 2)
        self.assertEqual(classify(listing)['geography_status'], 'US')

    def test_smartrecruiters_detail_and_pagination(self):
        calls = []
        def get(url):
            calls.append(url)
            if '?' in url:
                return {'totalFound': 1, 'content': [dict(id='1', name='Finance Intern', location={'country': 'US'})]}
            return dict(id='1', jobAd={'sections': {'qualifications': {'text': '<p>Undergraduate students</p>'}}})
        listing = smartrecruiters.fetch({'ats_identifier': 'firm'}, get).jobs[0]
        self.assertEqual(listing.description, 'Undergraduate students')
        self.assertEqual(len(calls), 2)
        with self.assertRaises(SourceError):
            smartrecruiters.fetch({'ats_identifier': 'firm'}, lambda url: {'totalFound': 2, 'content': []})

    def test_detection_and_changed_payload(self):
        self.assertEqual(detect('https://job-boards.greenhouse.io/firm/jobs/1'), ('greenhouse', 'firm'))
        self.assertEqual(detect('https://jobs.lever.co/firm/uuid'), ('lever', 'firm'))
        self.assertEqual(detect('https://firm.wd5.myworkdayjobs.com/en-US/Careers/job/1'), ('workday', 'firm/Careers'))
        self.assertEqual(detect('https://fakegreenhouse.io/firm'), ('custom', ''))
        with self.assertRaises(SourceError):
            greenhouse.fetch({'ats_identifier': '../../localhost'}, lambda url: {})


class OperationsTests(unittest.TestCase):
    def test_exclusive_lock(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / 'db'
            with locked(path):
                with self.assertRaises(RuntimeError):
                    with locked(path):
                        pass
            with locked(path):
                pass

    def test_internal_table_escapes_employer_content(self):
        html = table([dict(title='<script>alert(1)</script>')], [('title', 'Title')])
        self.assertNotIn('<script>', html)
        self.assertIn('&lt;script&gt;', html)


if __name__ == '__main__':
    unittest.main()
