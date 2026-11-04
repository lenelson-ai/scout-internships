"""A finite, evidenced employer registry. Policy inputs and outputs are versionable."""
from collections import defaultdict,Counter
from datetime import datetime,timedelta,timezone
from dataclasses import asdict
import csv
from datetime import datetime,timedelta,timezone
import hashlib
import json
from pathlib import Path
from .db import company,canonical_id,source
from .entities import alias,domain_key,name_key,resolve,consolidate_baseline,flag_related_baseline_names
from .model import Job,now,normalize
from .classify import classify
from .universe_sources import ROOT,fdic_rows,vault_rows,occ_trust_rows,digest


def catalog(db,root=ROOT):
    for row in json.loads((Path(root)/'config/universe_datasets.json').read_text()):
        db.execute('''INSERT INTO employer_datasets VALUES(?,?,?,?,?,?,?,?) ON CONFLICT(id) DO UPDATE SET
                      source_name=excluded.source_name,source_url=excluded.source_url,metadata=excluded.metadata''',
                   (row['id'],row['source_name'],row['source_type'],row.get('source_url'),row['source_category'],row['independent_family'],row['status'],json.dumps(row)))


def snapshot(db,dataset_id,path,files=None,archive_root=None):
    files=files or [Path(path)]
    sha=hashlib.sha256(('universe-v2\n'+''.join(str(Path(p).name)+':'+digest(p) for p in sorted(files))).encode()).hexdigest()
    archive=Path(archive_root or ROOT/'data/universe/archive')/dataset_id/sha
    archive.mkdir(parents=True,exist_ok=True)
    import shutil
    archive_manifest=[]
    for file in files:
        target=archive/Path(file).name
        if target.exists() and digest(target)!=digest(file):raise ValueError('Immutable dataset archive collision')
        if not target.exists():shutil.copyfile(file,target)
        archive_manifest.append(dict(file=target.name,sha256=digest(target)))
    manifest=archive/'snapshot.json'
    if not manifest.exists():manifest.write_text(json.dumps(dict(dataset_id=dataset_id,sha256=sha,files=archive_manifest),indent=2))
    db.execute('INSERT OR IGNORE INTO employer_dataset_snapshots(dataset_id,sha256,path,fetched_at,parser_version) VALUES(?,?,?,?,?)',
               (dataset_id,sha,str(manifest),now(),'universe-v2'))
    db.execute('UPDATE employer_dataset_snapshots SET path=? WHERE dataset_id=? AND sha256=?',(str(manifest),dataset_id,sha))
    return db.execute('SELECT id FROM employer_dataset_snapshots WHERE dataset_id=? AND sha256=?',(dataset_id,sha)).fetchone()[0]


def attach(db,raw_id,cid,reason,row):
    cid=canonical_id(db,cid)
    db.execute("UPDATE raw_organizations SET company_id=?,resolution_status='RESOLVED' WHERE id=?",(cid,raw_id))
    db.execute('DELETE FROM company_sources WHERE raw_organization_id=? AND company_id<>?',(raw_id,cid))
    kind=db.execute('SELECT d.source_type FROM raw_organizations r JOIN employer_datasets d ON r.dataset_id=d.id WHERE r.id=?',(raw_id,)).fetchone()[0]
    db.execute('INSERT OR REPLACE INTO company_entities VALUES(?,?,?,?,?,?)',
               (raw_id,cid,'LEGAL_ENTITY' if kind=='REGULATORY' or row.get('parent_name') else 'RECRUITING_BRAND',row.get('parent_name'),db.execute('SELECT name FROM companies WHERE id=?',(cid,)).fetchone()[0],reason))
    db.execute('''INSERT INTO company_sources VALUES(?,?,?,?) ON CONFLICT(company_id,raw_organization_id)
                  DO UPDATE SET source_metadata=excluded.source_metadata''',(cid,raw_id,now(),json.dumps(row.get('metadata',{}))))
    alias(db,cid,row['name'],reason,row.get('source_url'))
    domain=domain_key(row.get('domain',''))
    if domain:
        db.execute('INSERT OR IGNORE INTO company_domains VALUES(?,?,?)',(cid,domain,'Raw organization '+str(raw_id)))
        db.execute('UPDATE companies SET domain=COALESCE(domain,?) WHERE id=?',(domain,cid))
    db.execute("UPDATE entity_resolution_cases SET status='RESOLVED',resolution=? WHERE raw_organization_id=? AND status='OPEN'",(reason,raw_id))
    return cid


def import_rows(db,dataset_id,rows,snapshot_id=None):
    dataset=db.execute('SELECT * FROM employer_datasets WHERE id=?',(dataset_id,)).fetchone()
    if not dataset:raise ValueError('Unknown source dataset')
    counts=Counter();seen=set()
    for row in rows:
        if not row.get('external_id') or not row.get('name'):raise ValueError('Organizations require stable external_id and name')
        external=str(row['external_id'])
        if external in seen:raise ValueError('Duplicate source entity ID in input')
        seen.add(external);stamp=now();metadata=row.get('metadata',{})
        old=db.execute('SELECT * FROM raw_organizations WHERE dataset_id=? AND external_id=?',(dataset_id,external)).fetchone()
        db.execute('''INSERT INTO raw_organizations(dataset_id,external_id,name,normalized_name,domain,parent_name,metadata,resolution_status,first_seen_at,last_seen_at,last_snapshot_id)
                      VALUES(?,?,?,?,?,?,?,'PENDING',?,?,?) ON CONFLICT(dataset_id,external_id) DO UPDATE SET
                      name=excluded.name,normalized_name=excluded.normalized_name,domain=excluded.domain,parent_name=excluded.parent_name,metadata=excluded.metadata,
                      last_seen_at=excluded.last_seen_at,last_snapshot_id=excluded.last_snapshot_id,active=1''',
                   (dataset_id,external,row['name'],name_key(row['name']),domain_key(row.get('domain','')),row.get('parent_name'),json.dumps(metadata),stamp,stamp,snapshot_id))
        raw_id=db.execute('SELECT id FROM raw_organizations WHERE dataset_id=? AND external_id=?',(dataset_id,external)).fetchone()[0]
        if old and old['company_id']:
            identity_changed=(name_key(old['name'])!=name_key(row['name']) or (old['domain'] and domain_key(row.get('domain','')) and old['domain']!=domain_key(row['domain']))
                              or (old['parent_name'] and row.get('parent_name') and old['parent_name']!=row['parent_name']))
            if identity_changed:
                db.execute("UPDATE raw_organizations SET resolution_status='REVIEW' WHERE id=?",(raw_id,))
                db.execute('INSERT OR IGNORE INTO entity_resolution_cases(raw_organization_id,case_key,candidate_company_ids,reason,created_at) VALUES(?,?,?,?,?)',
                           (raw_id,'changed:'+str(raw_id),json.dumps([old['company_id']]),'Source entity name/domain/parent changed; recruiting identity must be revalidated',stamp));counts['review']+=1;continue
            cid=attach(db,raw_id,old['company_id'],'Preserved source-specific entity mapping',row);counts['resolved']+=1;continue
        if metadata.get('existing_company_id'):
            cid=attach(db,raw_id,int(metadata['existing_company_id']),'Preserved Scout employer identity',row);counts['resolved']+=1;continue
        cid,candidates,reason=resolve(db,row['name'],row.get('domain',''),row.get('parent_name',''))
        # Regulatory advisers/funds are not promoted merely because they exist.
        fund_category=metadata.get('category') or dataset['source_category']
        if dataset_id=='sec_adv' or fund_category in {'ASSET_MANAGEMENT','PRIVATE_EQUITY','PRIVATE_CREDIT','ALTERNATIVES','VENTURE_CAPITAL'}:
            if float(metadata.get('aum_usd',0))<1_000_000_000 or not metadata.get('recruiting_url'):
                reason='Scale plus recruiting evidence required before adviser/fund promotion';candidates=candidates or [];cid=None
                db.execute("UPDATE raw_organizations SET resolution_status='REVIEW' WHERE id=?",(raw_id,))
                db.execute('INSERT OR IGNORE INTO entity_resolution_cases(raw_organization_id,case_key,candidate_company_ids,reason,created_at) VALUES(?,?,?,?,?)',
                           (raw_id,'entity:'+str(raw_id),json.dumps(candidates),reason,stamp));counts['review']+=1;continue
        if candidates and cid is None:
            db.execute("UPDATE raw_organizations SET resolution_status='REVIEW' WHERE id=?",(raw_id,))
            db.execute('INSERT OR IGNORE INTO entity_resolution_cases(raw_organization_id,case_key,candidate_company_ids,reason,created_at) VALUES(?,?,?,?,?)',
                       (raw_id,'entity:'+str(raw_id),json.dumps(candidates),reason,stamp));counts['review']+=1;continue
        if cid is None and dataset['source_type']=='REGULATORY' and not domain_key(row.get('domain','')) and not metadata.get('recruiting_url'):
            db.execute("UPDATE raw_organizations SET resolution_status='REVIEW' WHERE id=?",(raw_id,))
            db.execute('INSERT OR IGNORE INTO entity_resolution_cases(raw_organization_id,case_key,candidate_company_ids,reason,created_at) VALUES(?,?,?,?,?)',
                       (raw_id,'entity:'+str(raw_id),'[]','Regulatory legal entity has no verified recruiting domain or recruiting-brand evidence; retained raw, not counted as an employer',stamp));counts['review']+=1;continue
        if cid is None:
            cid=company(db,row['name'],domain=domain_key(row.get('domain','')));counts['new_employers']+=1
            provisional=db.execute("SELECT 1 FROM company_entities WHERE company_id=? AND entity_type='PROVISIONAL_LEGAL_ENTITY'",(cid,)).fetchone()
            if provisional and (row.get('domain') or metadata.get('recruiting_url')):db.execute('UPDATE companies SET active=1 WHERE id=?',(cid,))
        attach(db,raw_id,cid,reason,row);counts['resolved']+=1
    if snapshot_id:
        # Missing rows are retained, but loss of a source membership is visible on refresh.
        for old in db.execute('SELECT id,external_id FROM raw_organizations WHERE dataset_id=?',(dataset_id,)).fetchall():
            if old['external_id'] not in seen:db.execute('UPDATE raw_organizations SET active=0 WHERE id=?',(old['id'],))
    db.execute("UPDATE employer_datasets SET status='IMPORTED' WHERE id=?",(dataset_id,))
    return dict(counts)


def evidence(db,cid,kind,key,url,metadata,observed=None):
    db.execute('''INSERT INTO recruiting_evidence(company_id,evidence_type,evidence_key,source_url,observed_at,metadata)
                  VALUES(?,?,?,?,?,?) ON CONFLICT(company_id,evidence_type,evidence_key) DO UPDATE SET metadata=excluded.metadata''',
               (canonical_id(db,cid),kind,key,url,observed or now(),json.dumps(metadata)))


def override(db,cid,tier,must_cover,reason,evidence_url=None):
    if not reason.strip() or tier not in (1,2,3):raise ValueError('Override requires tier 1/2/3 and nonempty reason')
    cid=canonical_id(db,cid)
    values=(cid,tier,int(must_cover),reason,evidence_url,now())
    old=db.execute('SELECT * FROM coverage_overrides WHERE company_id=?',(cid,)).fetchone()
    if old and (old['coverage_tier'],old['must_cover'],old['reason'],old['evidence_url'])==values[1:5]:return
    db.execute('INSERT OR REPLACE INTO coverage_overrides VALUES(?,?,?,?,?,?)',values)
    db.execute('INSERT INTO coverage_override_history(company_id,coverage_tier,must_cover,reason,evidence_url,changed_at) VALUES(?,?,?,?,?,?)',values)


def priority(db,cid,policy):
    rows=db.execute('''SELECT r.metadata,d.source_type,d.source_category,d.independent_family,d.id dataset_id FROM company_sources s
                       JOIN raw_organizations r ON s.raw_organization_id=r.id JOIN employer_datasets d ON r.dataset_id=d.id
                       WHERE s.company_id=? AND r.active=1 AND r.resolution_status='RESOLVED' ''',(cid,)).fetchall()
    ev=db.execute('SELECT * FROM recruiting_evidence WHERE company_id=?',(cid,)).fetchall()
    points=policy['points'];reasons=[];metadata=[(r,json.loads(r['metadata'])) for r in rows]
    def add(key,why):reasons.append(dict(factor=key,points=points[key],evidence=why))
    corp=[m['rank'] for r,m in metadata if r['source_type']=='CORPORATE_RANKING' and m.get('rank')]
    ranked=[m['rank'] for r,m in metadata if r['source_type']=='RELEVANT_RANKING' and m.get('rank')]
    if corp:add('corporate_rank_top_100' if min(corp)<=100 else 'corporate_rank_other',dict(best_rank=min(corp)))
    if ranked:add('relevant_ranking_top_10' if min(ranked)<=10 else 'relevant_ranking_other',dict(best_rank=min(ranked)))
    if any(r['source_type']=='REGULATORY' for r in rows):add('regulatory_presence',[r['dataset_id'] for r in rows if r['source_type']=='REGULATORY'])
    assets=max([float(m.get('assets_usd',0)) for _,m in metadata] or [0])
    if assets>=100_000_000_000:add('bank_assets_100b_usd',assets)
    elif assets>=10_000_000_000:add('bank_assets_10b_usd',assets)
    aum=max([float(m.get('aum_usd',0)) for _,m in metadata] or [0])
    if aum>=100_000_000_000:add('adviser_aum_100b_usd',aum)
    elif aum>=1_000_000_000:add('adviser_aum_1b_usd',aum)
    established=[]
    priority_date=datetime.fromisoformat(policy.get('as_of_date') or datetime.now(timezone.utc).date().isoformat()).replace(tzinfo=timezone.utc)
    for r,m in metadata:
        if r['source_type']=='REGULATORY' and m.get('established_date') and float(m.get('assets_usd',0))>=1_000_000_000:
            try:
                stamp=datetime.strptime(m['established_date'],'%m/%d/%Y').replace(tzinfo=timezone.utc)
                if stamp<=priority_date-timedelta(days=365.25*10):established.append(m['established_date'])
            except ValueError:pass
    if established:add('regulated_1b_assets_10_years',dict(established_dates=established))
    categories=[m.get('category') or r['source_category'] for r,m in metadata if (m.get('category') or r['source_category']) not in {'UNKNOWN','MIXED'}]
    category=next((c for c in categories if c!='CORPORATE'),categories[0] if categories else 'UNKNOWN')
    history=[e for e in ev if e['evidence_type'] in {'HISTORICAL_TARGET_INTERNSHIP','OBSERVED_TARGET_INTERNSHIP'}]
    if category!='UNKNOWN' or history:add('target_relevance',category)
    if history:add('historical_relevant_internship',dict(distinct_evidence_keys=len({e['evidence_key'] for e in history})))
    if len({e['evidence_key'] for e in history})>=3:add('repeat_recruiting_3_requisitions',len(history))
    if any(e['evidence_type']=='OFFICIAL_UNDERGRAD_PROGRAM' for e in ev):add('dedicated_undergraduate_program',[e['source_url'] for e in ev if e['evidence_type']=='OFFICIAL_UNDERGRAD_PROGRAM'])
    families={r['independent_family'] for r in rows if r['independent_family'] not in {'legacy','scout_history','user'}}
    if len(families)>=2:add('independent_external_families_2',sorted(families))
    score=sum(r['points'] for r in reasons)
    gate=policy.get('tier_1_eligibility',{})
    major=(bool(corp and min(corp)<=gate.get('corporate_rank_max',500)) or bool(ranked and min(ranked)<=gate.get('relevant_rank_max',10)) or
           assets>=gate.get('bank_assets_min_usd',100_000_000_000) or max([float(m.get('aum_usd',0)) for _,m in metadata] or [0])>=gate.get('aum_min_usd',100_000_000_000))
    tier=1 if score>=policy['tier_1_min'] and major else 2 if score>=policy['tier_2_min'] else 3
    if score>=policy['tier_1_min'] and not major:reasons.append(dict(factor='tier_1_scale_gate',points=0,evidence='High recruiting score without a major scale/ranking input remains Tier 2'))
    manual=db.execute('SELECT * FROM coverage_overrides WHERE company_id=?',(cid,)).fetchone()
    if manual:
        tier=manual['coverage_tier'];reasons.append(dict(factor='manual_override',points=0,evidence=dict(reason=manual['reason'],evidence_url=manual['evidence_url'])))
    must=manual['must_cover'] if manual else tier in (1,2)
    functions=['STRATEGY','FINANCE'] if category=='CORPORATE' else ['STRATEGY','FINANCE','CONSULTING'] if category=='CONSULTING' else ['FINANCE'] if category!='UNKNOWN' else sorted({json.loads(e['metadata']).get('function') for e in history if json.loads(e['metadata']).get('function')})
    db.execute('UPDATE companies SET coverage_tier=?,coverage_score=?,coverage_reason=?,must_cover=?,target_functions=?,employer_category=?,last_priority_review_at=? WHERE id=?',
               (tier,score,json.dumps(reasons),must,json.dumps(functions),category,now(),cid))
    return dict(tier=tier,score=score,reasons=reasons)


def score_all(db,root=ROOT):
    policy=json.loads((Path(root)/'config/universe_policy.json').read_text())
    for row in db.execute('SELECT id FROM companies WHERE canonical_company_id IS NULL AND active=1').fetchall():priority(db,row['id'],policy)


def observed_recruiter(db,cid,job,decision,jid,sid):
    cid=canonical_id(db,cid)
    db.execute('''INSERT OR IGNORE INTO employer_datasets VALUES('scout_live_observations','Authoritative ATS internship observations',
                  'RECRUITING_HISTORY',NULL,'MIXED','scout_history','IMPORTED','{}')''')
    name=db.execute('SELECT name FROM companies WHERE id=?',(cid,)).fetchone()[0]
    import_rows(db,'scout_live_observations',[dict(external_id='company:'+str(cid),name=name,metadata=dict(existing_company_id=cid,first_source_id=sid))])
    evidence(db,cid,'OBSERVED_TARGET_INTERNSHIP',job.external_job_id or job.canonical_apply_url,job.source_url,dict(function=decision['function'],job_id=jid,source_id=sid))


def build(db,root=ROOT):
    root=Path(root);out=root/'data/universe';out.mkdir(exist_ok=True)
    baseline=out/'baseline-companies.json'
    if not baseline.exists():baseline.write_text(json.dumps([dict(r) for r in db.execute('SELECT * FROM companies ORDER BY id')],indent=2))
    results={}
    snap=lambda name,path,files=None:snapshot(db,name,path,files,archive_root=out/'archive')
    with db:
        catalog(db,root)
        for row in json.loads(baseline.read_text()):
            domain=domain_key(row.get('domain') or row.get('careers_url'))
            if domain:db.execute('INSERT OR IGNORE INTO company_domains VALUES(?,?,?)',(row['id'],domain,'Preserved recorded employer/career domain'))
            alias(db,row['id'],row['name'],'Preserved employer name')
        sid=snap('legacy_baseline',baseline)
        import_rows(db,'legacy_baseline',[dict(external_id=str(r['id']),name=r['name'],domain=domain_key(r.get('domain') or r.get('careers_url')),metadata=dict(existing_company_id=r['id'])) for r in json.loads(baseline.read_text())],sid)
        results['baseline_merges']=consolidate_baseline(db)
        flag_related_baseline_names(db,[r['id'] for r in json.loads(baseline.read_text())])
        fortune=root/'data/fortune500_2026.csv'
        with fortune.open() as f:rows=list(csv.DictReader(f))
        results['fortune']=import_rows(db,'legacy_fortune_2026',[dict(external_id=r['Rank'],name=r['Company'],metadata=dict(rank=int(r['Rank']),industry=r['Industry'],category='INSURANCE' if 'insurance' in r['Industry'].lower() else 'BANKING' if 'bank' in r['Industry'].lower() else 'CORPORATE')) for r in rows],snap('legacy_fortune_2026',fortune))
        history=root/'data/opportunities.json';records=json.loads(history.read_text());by_name=defaultdict(list)
        for r in records:by_name[r['company']].append(r)
        history_rows=[]
        for name,records in by_name.items():
            existing=db.execute('SELECT id FROM companies WHERE normalized_name=?',(normalize(name),)).fetchone()
            history_rows.append(dict(external_id=normalize(name),name=name,metadata=dict(existing_company_id=existing[0] if existing else None,record_ids=[r['recordId'] for r in records])))
        import_rows(db,'scout_history',history_rows,snap('scout_history',history))
        for original in json.loads(history.read_text()):
            link=db.execute('SELECT j.company_id FROM legacy_records l JOIN jobs j ON j.id=l.job_id WHERE l.record_id=?',(original['recordId'],)).fetchone()
            if not link:continue
            job=Job(title=original['internship'],locations=[original.get('location','')]).clean();decision=classify(job)
            if decision['internship_confidence']>=.8 and decision['function']!='OTHER' and decision['classification_confidence']>=.7 and decision['geography_status']!='NON_US' and not any(x in job.title.lower() for x in ('mba','phd')):
                evidence(db,link[0],'HISTORICAL_TARGET_INTERNSHIP',str(original.get('requisitionId') or original['recordId']),original.get('applicationLink'),dict(function=decision['function'],record_id=original['recordId'],verification='historical imported observation; not current open-status proof'),original.get('firstSeen'))
        anchors=json.loads((root/'config/must_cover_anchors.json').read_text())
        for item in anchors:
            cid,candidates,_=resolve(db,item['name'],item.get('domain',''))
            if cid is None:
                if candidates:raise ValueError('Required anchor needs identity review: '+item['name'])
                cid=company(db,item['name'],domain=item.get('domain'))
            alias(db,cid,item['name'],'Explicit user-required minimum anchor')
            override(db,cid,item['tier'],True,item['reason'],item.get('evidence_url'))
            import_rows(db,'user_required_anchors',[dict(external_id=item['name'],name=item['name'],domain=item.get('domain',''),metadata=dict(existing_company_id=cid))])
        for name in ('fdic_established_banks','fdic_trust_banks'):
            path=out/'snapshots'/f'{name}.json'
            results[name]=import_rows(db,name,fdic_rows(path),snap(name,path))
        for name,count in [('vault_consulting',50),('vault_banking',25)]:
            rows,files=vault_rows(out/'snapshots',name,count)
            results[name]=import_rows(db,name,rows,snap(name,files[0],files))
        occ=out/'snapshots/occ_trust.txt'
        if occ.exists():results['occ_trust']=import_rows(db,'occ_trust',occ_trust_rows(occ),snap('occ_trust',occ,[occ,occ.with_suffix('.pdf')]))
        mappings=root/'config/verified_career_mappings.json'
        if mappings.exists():
            from .sources import detect
            for mapping in json.loads(mappings.read_text()):
                cid=resolve(db,mapping['company'],mapping['domain'])[0]
                if not cid:raise ValueError('Verified source mapping requires a uniquely resolved employer')
                db.execute('UPDATE companies SET domain=?,careers_url=? WHERE id=?',(mapping['domain'],mapping['careers_url'],cid))
                evidence(db,cid,'OFFICIAL_UNDERGRAD_PROGRAM',mapping['program_url'],mapping['program_url'],dict(function='FINANCE',snapshot_sha256=digest(out/'snapshots'/mapping['program_snapshot'])))
                raw=db.execute("SELECT id,metadata FROM raw_organizations WHERE dataset_id='user_required_anchors' AND company_id=?",(cid,)).fetchone()
                if raw:
                    meta=json.loads(raw['metadata']);meta['category']=mapping['employer_category'];db.execute('UPDATE raw_organizations SET metadata=? WHERE id=?',(json.dumps(meta),raw['id']))
                for url in mapping['ats_urls']:
                    ats,identifier=detect(url);source(db,cid,ats,identifier,url)
        score_all(db,root)
    return results
