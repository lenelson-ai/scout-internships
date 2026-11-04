from datetime import datetime,timezone
import json
from pathlib import Path
import tempfile
import unittest
from scout.db import connect,company,source,canonical_id
from scout.entities import alias,resolve,merge,name_key,domain_key,consolidate_baseline,consolidate_recruiting_groups
from scout.universe import catalog,import_rows,evidence,priority,override,score_all,snapshot
from scout.universe_sources import ROOT,fdic_rows,occ_trust_rows,vault_rows
from scout.universe_qa import health_report,write_manifest
from scout.model import Job
from scout.ingest import run_source,upsert
from scout.sources.base import Snapshot,SourceError
from scout.benchmark import import_benchmarks,evaluate

NOW='2026-11-02T12:00:00+00:00'
AT=datetime.fromisoformat(NOW)


class RegistryTests(unittest.TestCase):
    def setUp(self):
        self.db=connect(':memory:');catalog(self.db)
        self.policy=json.loads((ROOT/'config/universe_policy.json').read_text())

    def tearDown(self):self.db.close()

    def bank(self,name='Example Trust Company',external='1',domain='exampletrust.example',assets=2_000_000_000):
        row=dict(external_id=external,name=name,domain=domain,metadata=dict(assets_usd=assets,established_date='01/01/2000',category='BANKING_AND_TRUST'))
        import_rows(self.db,'fdic_trust_banks',[row])
        return resolve(self.db,name,domain)[0]

    def test_legal_normalization_and_shared_ats_are_not_identity(self):
        self.assertEqual(name_key('Example Trust Company, National Association'),'example trust')
        self.assertEqual(name_key('Bain & Company'),'bain')
        self.assertEqual(domain_key('https://example.wd5.myworkdayjobs.com/External'),'')
        self.assertEqual(domain_key('https://careers.example.com/jobs'),'example.com')

    def test_multiple_regulatory_entities_preserve_provenance(self):
        cid=self.bank()
        self.bank('Example Trust Company, National Association','2')
        self.assertEqual(self.db.execute('SELECT COUNT(*) FROM companies').fetchone()[0],1)
        self.assertEqual(self.db.execute('SELECT COUNT(*) FROM company_entities').fetchone()[0],2)
        self.assertEqual(self.db.execute('SELECT COUNT(*) FROM company_sources WHERE company_id=?',(cid,)).fetchone()[0],2)

    def test_domain_conflict_and_parent_only_match_require_review(self):
        cid=self.bank()
        import_rows(self.db,'fdic_trust_banks',[dict(external_id='2',name='Example Trust',domain='different.example',metadata={})])
        self.assertEqual(self.db.execute("SELECT resolution_status FROM raw_organizations WHERE external_id='2'").fetchone()[0],'REVIEW')
        import_rows(self.db,'fdic_trust_banks',[dict(external_id='3',name='Distinct Subsidiary',domain='subsidiary.example',parent_name='Example Trust',metadata={})])
        self.assertEqual(self.db.execute('SELECT COUNT(*) FROM companies').fetchone()[0],1)
        self.assertEqual(self.db.execute("SELECT COUNT(*) FROM entity_resolution_cases WHERE status='OPEN'").fetchone()[0],2)

    def test_ambiguous_aliases_never_auto_merge(self):
        a=company(self.db,'Example One');b=company(self.db,'Example Two')
        alias(self.db,a,'Shared Brand','fixture');alias(self.db,b,'Shared Brand','fixture')
        cid,candidates,_=resolve(self.db,'Shared Brand')
        self.assertIsNone(cid);self.assertEqual(set(candidates),{a,b})

    def test_refresh_identity_change_is_reviewed(self):
        self.bank()
        self.bank('Changed Recruiting Brand','1','other.example')
        self.assertEqual(self.db.execute('SELECT resolution_status FROM raw_organizations').fetchone()[0],'REVIEW')
        self.assertEqual(self.db.execute('SELECT COUNT(*) FROM companies').fetchone()[0],1)

    def test_scoring_deduplicates_source_presence_and_requires_evidence(self):
        cid=self.bank()
        first=priority(self.db,cid,self.policy)
        self.bank('Example Trust Company, National Association','2')
        second=priority(self.db,cid,self.policy)
        self.assertEqual(first['score'],second['score'])
        self.assertEqual(first['tier'],2)
        self.assertNotIn('independent_external_families_2',[r['factor'] for r in second['reasons']])

    def test_example_name_does_not_change_identical_priority_inputs(self):
        a=self.bank('Bessemer Trust','A','first.example')
        b=self.bank('Unmentioned Harbor Trust','B','second.example')
        self.assertEqual(priority(self.db,a,self.policy)['score'],priority(self.db,b,self.policy)['score'])
        self.assertEqual(priority(self.db,a,self.policy)['tier'],priority(self.db,b,self.policy)['tier'])

    def test_history_can_raise_priority_without_subjective_employer_list(self):
        cid=company(self.db,'Previously Unknown Recruiting Firm')
        evidence(self.db,cid,'OBSERVED_TARGET_INTERNSHIP','R1','https://example.org/1',dict(function='FINANCE'))
        self.assertEqual(priority(self.db,cid,self.policy)['tier'],3)
        for key in ('R2','R3'):evidence(self.db,cid,'OBSERVED_TARGET_INTERNSHIP',key,'https://example.org/'+key,dict(function='FINANCE'))
        self.assertEqual(priority(self.db,cid,self.policy)['tier'],2)

    def test_overrides_require_reasons_and_keep_history(self):
        cid=self.bank()
        with self.assertRaises(ValueError):override(self.db,cid,1,True,'')
        override(self.db,cid,1,True,'Explicit fixture decision')
        override(self.db,cid,1,True,'Explicit fixture decision')
        self.assertEqual(self.db.execute('SELECT COUNT(*) FROM coverage_override_history').fetchone()[0],1)
        self.assertEqual(priority(self.db,cid,self.policy)['tier'],1)
        override(self.db,cid,3,False,'Manual best-effort exception')
        self.assertEqual(priority(self.db,cid,self.policy)['tier'],3)
        self.assertEqual(self.db.execute('SELECT COUNT(*) FROM coverage_override_history').fetchone()[0],2)

    def test_adviser_requires_scale_and_recruiting_evidence(self):
        result=import_rows(self.db,'sec_adv',[dict(external_id='CRD1',name='Tiny Fund',metadata=dict(aum_usd=1_000_000))])
        self.assertEqual(result['review'],1)
        self.assertEqual(self.db.execute('SELECT COUNT(*) FROM companies').fetchone()[0],0)

    def test_established_adviser_with_recruiting_evidence_gets_general_priority(self):
        import_rows(self.db,'sec_adv',[dict(external_id='CRD2',name='Unnamed Advisory Firm',domain='adviser.example',metadata=dict(aum_usd=2_000_000_000,recruiting_url='https://adviser.example/students',category='ASSET_MANAGEMENT'))])
        cid=resolve(self.db,'Unnamed Advisory Firm')[0]
        self.assertEqual(priority(self.db,cid,self.policy)['tier'],2)

    def test_fintech_ranking_does_not_require_an_adviser_aum_field(self):
        import_rows(self.db,'industry_employers',[dict(external_id='fintech-1',name='Unnamed Established Fintech',domain='fintech.example',metadata=dict(rank=20,category='FINTECH'))])
        cid=resolve(self.db,'Unnamed Established Fintech')[0]
        self.assertEqual(priority(self.db,cid,self.policy)['tier'],2)

    def test_legal_directory_without_recruiting_identity_is_held_raw(self):
        result=import_rows(self.db,'occ_trust',[dict(external_id='12345',name='Unnamed National Trust Company',metadata={})])
        self.assertEqual(result['review'],1)
        self.assertEqual(self.db.execute('SELECT COUNT(*) FROM raw_organizations').fetchone()[0],1)
        self.assertEqual(self.db.execute('SELECT COUNT(*) FROM companies').fetchone()[0],0)

    def test_shared_parent_and_identical_enumerated_board_resolve_legal_units(self):
        owners=[]
        for i,name in enumerate(['Coastal Trust','North District Bank']):
            row=dict(external_id=str(i),name=name,domain=f'bank{i}.example',parent_name='Common Financial Holdings',metadata=dict(assets_usd=2_000_000_000,category='BANKING_AND_TRUST'))
            import_rows(self.db,'fdic_trust_banks',[row]);cid=resolve(self.db,name)[0];owners.append(cid)
            sid=source(self.db,cid,'workday','common/External','https://common.wd1.myworkdayjobs.com/External');self.db.commit()
            s=self.db.execute('SELECT * FROM sources WHERE id=?',(sid,)).fetchone()
            listing=Job(title='Finance Intern',external_job_id='R1',source_url='https://common.wd1.myworkdayjobs.com/External/job/Finance_R1',locations=['New York, NY'])
            run_source(self.db,s,lambda _:Snapshot([listing]),NOW)
        changes=consolidate_recruiting_groups(self.db)
        self.assertEqual(len(changes),2)
        self.assertEqual(canonical_id(self.db,owners[0]),canonical_id(self.db,owners[1]))
        self.assertEqual(self.db.execute('SELECT COUNT(*) FROM raw_organizations WHERE dataset_id=\'fdic_trust_banks\'').fetchone()[0],2)

    def test_merge_keeps_jobs_and_sources_routable(self):
        a=company(self.db,'Recorded Recruiting Brand');b=company(self.db,'Recorded Recruiting Brand Company')
        sa=source(self.db,a,'greenhouse','firm','https://job-boards.greenhouse.io/firm')
        sb=source(self.db,b,'greenhouse','firm','https://job-boards.greenhouse.io/firm')
        job=Job(title='Finance Intern',external_job_id='1',source_url='https://example.org/1',locations=['Boston, MA'])
        jid,_=upsert(self.db,b,None,job,NOW)
        merge(self.db,b,a,'Same official recruiting brand',dict(domain='example.org'))
        self.assertEqual(canonical_id(self.db,b),a)
        self.assertEqual(self.db.execute('SELECT company_id FROM jobs WHERE id=?',(jid,)).fetchone()[0],a)
        self.assertEqual(self.db.execute('SELECT COUNT(*) FROM sources').fetchone()[0],2)
        self.assertEqual(self.db.execute('SELECT COUNT(*) FROM sources WHERE active=1').fetchone()[0],1)
        self.assertEqual(self.db.execute('SELECT COUNT(*) FROM source_equivalences').fetchone()[0],1)

    def test_snapshot_archive_is_immutable_and_import_idempotent(self):
        with tempfile.TemporaryDirectory() as directory:
            path=Path(directory)/'input.json';path.write_text('[1]')
            sid=snapshot(self.db,'fdic_trust_banks',path,archive_root=Path(directory)/'archive')
            manifest=Path(self.db.execute('SELECT path FROM employer_dataset_snapshots WHERE id=?',(sid,)).fetchone()[0])
            path.write_text('[2]');snapshot(self.db,'fdic_trust_banks',path,archive_root=Path(directory)/'archive')
            self.assertEqual((manifest.parent/'input.json').read_text(),'[1]')
        self.bank();self.bank()
        self.assertEqual(self.db.execute('SELECT COUNT(*) FROM raw_organizations').fetchone()[0],1)

    def test_manifest_stable_and_missing_must_cover_is_loud(self):
        cid=self.bank();priority(self.db,cid,self.policy);self.db.commit()
        with tempfile.TemporaryDirectory() as directory:
            first=write_manifest(self.db,directory);second=write_manifest(self.db,directory)
            self.assertEqual(first['manifest_sha256'],second['manifest_sha256'])
            self.db.execute('UPDATE raw_organizations SET active=0');priority(self.db,cid,self.policy)
            changed=write_manifest(self.db,directory)
            self.assertTrue(changed['manifest_alerts'])
            self.assertEqual(self.db.execute('SELECT COUNT(*) FROM companies').fetchone()[0],1)

    def test_healthy_long_tail_does_not_hide_tier_one_failure(self):
        critical=self.bank();override(self.db,critical,1,True,'Fixture critical employer');priority(self.db,critical,self.policy)
        tail=company(self.db,'Long Tail')
        sid=source(self.db,tail,'greenhouse','tail','https://job-boards.greenhouse.io/tail')
        self.db.execute("UPDATE sources SET source_role='AUTHORITATIVE',health_status='SUCCESS',last_successful_check_at=?,jobs_returned=1 WHERE id=?",(NOW,sid))
        self.db.execute("INSERT INTO recruiting_scope_reviews VALUES(?,'VALIDATED',?,'{}',?)",(tail,json.dumps([sid]),NOW))
        report=health_report(self.db,at=AT)
        self.assertEqual(report['tier_metrics']['1']['monitored_successfully'],0)
        self.assertEqual(report['tier_metrics']['3']['monitored_successfully'],1)
        self.assertTrue(any(r['company_id']==critical and r['severity']=='CRITICAL' for r in report['critical_failures']))

    def test_name_blind_general_trust_cohort_captures_many_peers(self):
        rows=fdic_rows(ROOT/'data/universe/snapshots/fdic_trust_banks.json')
        import_rows(self.db,'fdic_trust_banks',rows);score_all(self.db)
        # The full source cohort is imported into a clean DB, with no example overrides.
        cid,_,_=resolve(self.db,'Bessemer Trust')
        self.assertIsNotNone(cid)
        self.assertEqual(self.db.execute('SELECT coverage_tier FROM companies WHERE id=?',(cid,)).fetchone()[0],2)
        self.assertFalse(self.db.execute('SELECT 1 FROM coverage_overrides').fetchone())
        peers=self.db.execute('SELECT COUNT(*) FROM companies WHERE coverage_tier=2 AND id<>?',(cid,)).fetchone()[0]
        self.assertGreaterEqual(peers,50)


class InventoryTests(unittest.TestCase):
    def test_mapping_ignores_url_list_tail(self):
        from scout.career_mapping import map_one
        def get(url,root):
            if url=='https://firm.example':return url,'',[('/careers','Careers',{})],'homehash'
            return url,'https://common.wd1.myworkdayjobs.com/External,https://unrelated.example','', 'careerhash'
        result=map_one(dict(id=1,domain='firm.example'),get=get)
        sources=json.loads(result['evidence'])['sources']
        self.assertEqual(len(sources),1)
        self.assertEqual(sources[0]['url'],'https://common.wd1.myworkdayjobs.com/External')
    def test_all_listings_persist_before_screening_and_failures_preserve_presence(self):
        db=connect(':memory:');cid=company(db,'Fixture Employer');sid=source(db,cid,'greenhouse','firm');db.commit()
        s=db.execute('SELECT * FROM sources WHERE id=?',(sid,)).fetchone()
        listings=[Job(title='Finance Intern',external_job_id='1',source_url='https://example.org/1',locations=['Boston, MA']),
                  Job(title='Senior Software Engineer',external_job_id='2',source_url='https://example.org/2')]
        run_source(db,s,lambda s:Snapshot(listings),NOW)
        self.assertEqual(db.execute('SELECT COUNT(*) FROM source_inventory').fetchone()[0],2)
        self.assertEqual(db.execute('SELECT COUNT(*) FROM jobs').fetchone()[0],1)
        result=run_source(db,s,lambda s:Snapshot(listings),'2026-11-03T12:00:00+00:00')
        self.assertNotIn('INVENTORY_NEW',result['events'])
        self.assertEqual(db.execute('SELECT first_seen_at FROM source_inventory LIMIT 1').fetchone()[0],NOW)
        def fail(s):raise SourceError('BLOCKED','fixture')
        run_source(db,s,fail,NOW)
        self.assertEqual(db.execute('SELECT COUNT(*) FROM source_inventory WHERE present=1').fetchone()[0],2)
        run_source(db,s,lambda s:Snapshot(listings[:1]),'2026-11-04T12:00:00+00:00')
        self.assertEqual(db.execute("SELECT present FROM source_inventory WHERE external_job_id='2'").fetchone()[0],0)
        db.close()

    def test_general_title_variants_classify_locally(self):
        from scout.classify import classify
        for title in ['Office of the CFO Summer Analyst','Enterprise Transformation Intern','Business Planning Intern','Strategic Initiatives Intern','Corporate Development Summer Analyst','Client Advisory Summer Associate']:
            with self.subTest(title=title):
                result=classify(Job(title=title,description='Undergraduate student program',locations=['New York, NY']))
                self.assertTrue(result['retain']);self.assertNotEqual(result['function'],'OTHER')


class BenchmarkTests(unittest.TestCase):
    def test_empty_benchmark_never_claims_perfect_recall(self):
        db=connect(':memory:');report=evaluate(db,at=AT)
        self.assertEqual(report['verified_benchmark_jobs'],0);self.assertIsNone(report['job_discovery_recall']);db.close()

    def test_verified_benchmark_requires_provenance(self):
        db=connect(':memory:')
        with self.assertRaises(ValueError):import_benchmarks(db,[dict(employer='Example')])
        db.close()

    def test_all_miss_reasons_and_old_posted_new_discovery_success(self):
        db=connect(':memory:');rows=[]
        stages=['EMPLOYER_UNIVERSE_MISS','SOURCE_COVERAGE_MISS','INGESTION_PARSER_MISS','INTERNSHIP_DETECTION_MISS','CLASSIFICATION_MISS','TIMING_MISS',None]
        for i,stage in enumerate(stages):
            employer=f'Fixture Employer {i}';url=f'https://employer{i}.example/jobs/1'
            rows.append(dict(benchmark_key=str(i),employer=employer,title='Finance Intern',canonical_url=url,external_job_id='1',benchmark_source='Fixture verified source',verified_by='Unit test',verified_at=NOW,expected_category='FINANCE'))
            if i==0:continue
            cid=company(db,employer);sid=source(db,cid,'greenhouse','firm'+str(i),f'https://job-boards.greenhouse.io/firm{i}')
            db.execute("UPDATE sources SET source_role='AUTHORITATIVE',health_status=?,last_successful_check_at=?,jobs_returned=1 WHERE id=?",('BLOCKED' if i==1 else 'SUCCESS',NOW,sid))
            db.execute("INSERT INTO recruiting_scope_reviews VALUES(?,'VALIDATED',?,'{}',?)",(cid,json.dumps([sid]),NOW))
            if i<3:continue
            title='Senior Financial Analyst' if i==3 else 'Business Intern' if i==4 else 'Finance Intern'
            stamp='2026-08-01T12:00:00+00:00' if i==5 else NOW
            upsert(db,cid,sid,Job(title=title,external_job_id='1',source_url=url,locations=['New York, NY'],posted_at='2026-01-01'),stamp)
        import_benchmarks(db,rows);report=evaluate(db,at=AT)
        self.assertEqual([r['miss_reason'] for r in report['results']],stages)
        self.assertAlmostEqual(report['job_discovery_recall'],4/7)
        self.assertTrue(report['results'][-1]['surfaced_in_window'])
        db.close()


if __name__=='__main__':unittest.main()
