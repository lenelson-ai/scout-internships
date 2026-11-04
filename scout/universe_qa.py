"""Tier-specific health, immutable manifests, and missing-employer alarms."""
from collections import defaultdict,Counter
from datetime import datetime,timedelta,timezone
import csv
import hashlib
import json
from pathlib import Path
from .db import canonical_id
from .source_roles import evaluate
from .model import now
from .sources import ADAPTERS
from .coverage import provider_hint,configured
from .universe_sources import ROOT


def health_report(db,root=ROOT,at=None):
    at=at or datetime.now(timezone.utc);root=Path(root)
    policy=json.loads((root/'config/universe_policy.json').read_text())
    probes={r['source_id']:dict(r) for r in db.execute('SELECT p.* FROM source_probes p JOIN (SELECT source_id,MAX(id) id FROM source_probes GROUP BY source_id) latest ON latest.id=p.id')}
    sources=defaultdict(list)
    mappings={}
    for r in db.execute('SELECT m.* FROM career_mapping_attempts m JOIN (SELECT company_id,MAX(id) id FROM career_mapping_attempts GROUP BY company_id) latest ON latest.id=m.id ORDER BY m.id'):
        owner=canonical_id(db,r['company_id']);mappings[owner]=dict(r,recorded_company_id=r['company_id'],company_id=owner)
    for row in db.execute('SELECT * FROM sources WHERE active=1'):
        s=dict(row);cid=canonical_id(db,s['company_id']);s['company_id']=cid
        p=probes.get(s['id'],{});s['provider']=s['ats_type'] if s['ats_type']!='custom' else p.get('provider_hint') or provider_hint(s['careers_url'] or '')[0]
        if not configured(s):s['provider']='unconfigured'
        sources[cid].append(s)
    employers=[];tier_metrics={str(t):dict(total=0,monitored_successfully=0,unsupported=0,failing=0,stale=0,healthy_percentage=0) for t in (1,2,3)}
    distribution=defaultdict(lambda:defaultdict(set));impact=defaultdict(lambda:defaultdict(set));critical=[]
    checked24=checked7=0
    for c in db.execute('SELECT * FROM companies WHERE active=1 AND canonical_company_id IS NULL ORDER BY coverage_tier,name'):
        cid=c['id'];tier=c['coverage_tier'];ss=sources[cid];ss=[s for s in ss if s['source_role'] not in {'LEGACY','DUPLICATE','DISABLED','SUPPLEMENTAL'}];threshold=at-timedelta(hours=policy['stale_hours'][str(tier)])
        statuses=[];bad=[]
        for s in ss:
            last=datetime.fromisoformat(s['last_successful_check_at']) if s['last_successful_check_at'] else None
            is_supported=s['ats_type'] in ADAPTERS
            fresh=bool(last and last>=threshold)
            if not configured(s):status='UNKNOWN'
            elif not is_supported:
                if s['health_status']=='BLOCKED' or probes.get(s['id'],{}).get('status')=='BLOCKED':status='BLOCKED'
                elif s['provider'] in ADAPTERS:status='SUPPORTED_NOT_CONFIGURED'
                elif s['provider'] in {'custom','custom_unidentified'}:status='CUSTOM_PORTAL_REQUIRED'
                else:status='UNSUPPORTED_ATS'
            elif s['health_status']=='BLOCKED':status='BLOCKED'
            elif s['last_response_status'] in (404,410):status='BROKEN_URL'
            elif s['health_status'].startswith('SUCCESS') and fresh:status='MONITORED'
            elif last and not fresh:status='STALE'
            elif s['last_checked_at']:status='FAILING'
            else:status='UNKNOWN'
            statuses.append(status);s['monitoring_status']=status
            if status!='MONITORED':bad.append(s)
            distribution[s['provider']][tier].add(cid)
        scope=db.execute('SELECT * FROM recruiting_scope_reviews WHERE company_id=?',(cid,)).fetchone()
        scope_valid=bool(scope and scope['state']=='VALIDATED' and set(json.loads(scope['required_source_ids']))=={s['id'] for s in ss if s['source_role']=='AUTHORITATIVE'})
        authority=evaluate([dict(s,status=s['monitoring_status']) for s in ss]);healthy=authority['healthy'] and scope_valid
        status='MONITORED_SUCCESSFULLY' if healthy else 'PARTIALLY_MONITORED' if 'MONITORED' in statuses else next((s for s in ('BLOCKED','BROKEN_URL','FAILING','STALE','UNSUPPORTED_ATS','SUPPORTED_NOT_CONFIGURED','CUSTOM_PORTAL_REQUIRED','UNKNOWN') if s in statuses),'UNKNOWN')
        if not scope_valid or not any(s['source_role']=='AUTHORITATIVE' for s in ss):status='UNRESOLVED_SOURCE_SCOPE'
        if not ss:distribution['unconfigured'][tier].add(cid)
        if not ss and cid in mappings:
            error=mappings[cid].get('error') or ''
            if any(str(code) in error for code in (401,403,429)):status='BLOCKED'
            elif any(str(code) in error for code in (404,410)):status='BROKEN_URL'
        all_recent=lambda days:bool(ss) and all(s['last_successful_check_at'] and datetime.fromisoformat(s['last_successful_check_at'])>=at-timedelta(days=days) for s in ss)
        checked24+=int(all_recent(1));checked7+=int(all_recent(7))
        current_sources=[dict(r) for r in db.execute('''SELECT d.id,d.source_name,d.source_type,d.source_url,r.external_id,r.resolution_status
                    FROM company_sources s JOIN raw_organizations r ON r.id=s.raw_organization_id JOIN employer_datasets d ON r.dataset_id=d.id
                    WHERE s.company_id=? AND r.active=1 AND r.resolution_status='RESOLVED' ORDER BY d.id,r.external_id''',(cid,))]
        evidence_count=db.execute('SELECT COUNT(*) FROM recruiting_evidence WHERE company_id=?',(cid,)).fetchone()[0]
        item=dict(company_id=cid,company=c['name'],tier=tier,score=c['coverage_score'],must_cover=bool(c['must_cover']),category=c['employer_category'],
                  coverage_reason=json.loads(c['coverage_reason']),target_functions=json.loads(c['target_functions']),domain=c['domain'],careers_url=c['careers_url'],
                  monitoring_status=status,healthy=healthy,scope_validated=scope_valid,scope_evidence=dict(scope) if scope else None,sources=[dict(source_id=s['id'],ats=s['ats_type'],provider=s['provider'],identifier=s['ats_identifier'],
                    source_role=s['source_role'],role_evidence=s['role_evidence'],careers_url=s['careers_url'],status=s['monitoring_status'],last_checked_at=s['last_checked_at'],last_successful_check_at=s['last_successful_check_at'],consecutive_failures=s['consecutive_failures'],error=s['last_error']) for s in ss],
                  inclusion_sources=current_sources,recruiting_evidence_count=evidence_count,last_mapping_attempt=mappings.get(cid))
        cert=db.execute('SELECT scope_state,details,reviewed_at FROM tier1_scope_certifications WHERE company_id=?',(cid,)).fetchone()
        item['scope_certification']=dict(cert) if cert else None
        item['priority_evidence_status']='PROVISIONAL_NEEDS_REVIEW' if tier==3 and any(s['source_type']=='REGULATORY' for s in current_sources) else 'SCORED'
        employers.append(item);m=tier_metrics[str(tier)];m['total']+=1;m['monitored_successfully']+=int(healthy)
        m['partially_monitored']=m.get('partially_monitored',0)+int(not healthy and any(s['source_role']=='AUTHORITATIVE' and s['monitoring_status']=='MONITORED' for s in ss))
        m['unresolved']=m.get('unresolved',0)+int(any(s['source_role']=='UNKNOWN' for s in ss) or not scope_valid or not any(s['source_role']=='AUTHORITATIVE' for s in ss))
        m['unsupported']+=int(any(s['ats_type'] not in ADAPTERS and configured(s) for s in ss))
        m['failing']+=int(any(s['health_status'] not in {'NEVER_CHECKED'} and not s['health_status'].startswith('SUCCESS') for s in ss))
        m['stale']+=int(not ss or any(not s['last_successful_check_at'] or datetime.fromisoformat(s['last_successful_check_at'])<threshold for s in ss))
        if c['must_cover'] and (not healthy or not current_sources):
            severity='CRITICAL' if tier==1 else 'HIGH'
            critical.append(dict(company_id=cid,company=c['name'],tier=tier,severity=severity,status=status,
                                 reasons=['Required authoritative inventory or recruiting scope is unresolved, stale, or failing']+(['No active inclusion provenance'] if not current_sources else []),
                                 providers=sorted({s['provider'] for s in bad}) or ['unconfigured']))
        if tier in (1,2):
            for s in bad or ([dict(provider='unconfigured')] if not ss else []):impact[s['provider']][tier].add(cid)
    for m in tier_metrics.values():m['healthy_percentage']=round(100*m['monitored_successfully']/m['total'],4) if m['total'] else None
    ranking=[]
    for provider,tiers in impact.items():
        ids=tiers[1]|tiers[2]
        potential=sum(1 for e in employers if e['company_id'] in ids and ((not e['sources'] and provider=='unconfigured') or {s['provider'] for s in e['sources'] if s['status']!='MONITORED'}=={provider}))
        kind='SOURCE_CONFIGURATION' if provider=='unconfigured' or provider in ADAPTERS else 'HETEROGENEOUS_CUSTOM_BACKLOG' if provider in {'custom','custom_unidentified'} else 'SHARED_PROVIDER_ADAPTER'
        ranking.append(dict(provider=provider,tier_1_affected=len(tiers[1]),tier_2_affected=len(tiers[2]),tier_3_affected=sum(1 for e in employers if e['tier']==3 and any(s['provider']==provider and s['status']!='MONITORED' for s in e['sources'])),total_affected=len(ids),potential_coverage_unlocked=potential,
                            weighted_impact=10*len(tiers[1])+3*len(tiers[2]),work_type=kind))
    ranking.sort(key=lambda r:(-r['tier_1_affected'],-r['tier_2_affected'],r['provider']))
    for i,row in enumerate(ranking,1):row['recommended_priority']=i
    count=lambda sql:db.execute(sql).fetchone()[0]
    must_ids={e['company_id'] for e in employers if e['must_cover']}
    missing_anchors=[]
    from .entities import resolve
    for a in json.loads((root/'config/must_cover_anchors.json').read_text()):
        cid,_,_=resolve(db,a['name'],a.get('domain',''))
        if not cid or cid not in must_ids:missing_anchors.append(a['name'])
    raw=count('SELECT COUNT(*) FROM raw_organizations')
    resolved=count("SELECT COUNT(*) FROM raw_organizations WHERE company_id IS NOT NULL")
    resolved_employers=count('SELECT COUNT(DISTINCT company_id) FROM raw_organizations WHERE company_id IS NOT NULL')
    reviews=[dict(r) for r in db.execute("SELECT e.*,r.name,r.dataset_id,r.metadata FROM entity_resolution_cases e LEFT JOIN raw_organizations r ON e.raw_organization_id=r.id WHERE e.status='OPEN' ORDER BY e.id")]
    report=dict(generated_at=at.isoformat(),scope='Initial dataset union, not a complete registry of every major recruiting employer. No global internship recall claim.',
                tier_metrics=tier_metrics,total_canonical_employers=len(employers),total_raw_source_entities=raw,
                active_raw_source_entities=count('SELECT COUNT(*) FROM raw_organizations WHERE active=1'),
                source_observations_resolved=resolved,source_observations_collapsed=resolved-resolved_employers,
                existing_company_rows_collapsed=count('SELECT COUNT(*) FROM company_merges'),
                aliases=count('SELECT COUNT(*) FROM company_aliases'),legal_entities=count("SELECT COUNT(*) FROM company_entities WHERE entity_type='LEGAL_ENTITY'"),
                unresolved_entity_cases=count("SELECT COUNT(*) FROM entity_resolution_cases WHERE status='OPEN'"),
                provisional_priority_cases=sum(e['priority_evidence_status']=='PROVISIONAL_NEEDS_REVIEW' for e in employers),
                recruiting_evidence_records=count('SELECT COUNT(*) FROM recruiting_evidence'),
                employers_with_recruiting_evidence=count('SELECT COUNT(DISTINCT company_id) FROM recruiting_evidence e JOIN companies c ON c.id=e.company_id WHERE c.canonical_company_id IS NULL'),
                employers_added_only_from_recruiting_evidence=count("SELECT COUNT(*) FROM companies c WHERE c.canonical_company_id IS NULL AND EXISTS(SELECT 1 FROM company_sources s JOIN raw_organizations r ON r.id=s.raw_organization_id WHERE s.company_id=c.id AND r.dataset_id IN ('scout_history','scout_live_observations')) AND NOT EXISTS(SELECT 1 FROM company_sources s JOIN raw_organizations r ON r.id=s.raw_organization_id WHERE s.company_id=c.id AND r.dataset_id NOT IN ('scout_history','scout_live_observations'))"),
                companies_checked_successfully_last_24h=checked24,companies_checked_successfully_last_7d=checked7,
                provider_distribution=[dict(provider=p,tier_1=len(ts[1]),tier_2=len(ts[2]),tier_3=len(ts[3])) for p,ts in sorted(distribution.items())],
                unsupported_provider_distribution=[r for r in ranking if r['work_type']=='SHARED_PROVIDER_ADAPTER'],
                provider_impact=ranking,critical_failures=critical,missing_required_anchors=missing_anchors,
                datasets=[dict(r) for r in db.execute('SELECT id,source_name,source_type,source_url,status FROM employer_datasets ORDER BY id')],entity_review_cases=reviews,employers=employers)
    regression_file=root/'config/universe_regressions.json'
    regressions=[]
    if regression_file.exists():
        for check in json.loads(regression_file.read_text()):
            cid,_,_=resolve(db,check['employer']);employer=next((e for e in employers if e['company_id']==cid),None)
            regressions.append(dict(employer=check['employer'],present=bool(employer),company_id=cid,tier=employer['tier'] if employer else None,
                 sources=[s['id'] for s in employer['inclusion_sources']] if employer else [],manual_override=bool(db.execute('SELECT 1 FROM coverage_overrides WHERE company_id=?',(cid,)).fetchone()) if cid else False,
                 monitoring_status=employer['monitoring_status'] if employer else 'MISSING',note='Regression observation only; no scoring or inclusion rule references this name'))
    report['regression_checks']=regressions
    named={resolve(db,a['name'],a.get('domain',''))[0] for a in json.loads((root/'config/must_cover_anchors.json').read_text())}
    named|={r['company_id'] for r in regressions}
    peers=[]
    for e in employers:
        if e['company_id'] in named:continue
        assets=[json.loads(r[0]).get('assets_usd',0) for r in db.execute("SELECT r.metadata FROM company_sources s JOIN raw_organizations r ON s.raw_organization_id=r.id WHERE s.company_id=? AND r.dataset_id='fdic_trust_banks' AND r.active=1 AND r.resolution_status='RESOLVED'",(e['company_id'],))]
        if assets and e['tier'] in (1,2):peers.append(dict(company=e['company'],company_id=e['company_id'],tier=e['tier'],score=e['score'],max_reported_balance_sheet_assets_usd=max(assets),monitoring_status=e['monitoring_status']))
    report['unnamed_general_trust_peers']=peers
    report['unnamed_mid_sized_trust_peers']=sum(1 for e in peers if e['max_reported_balance_sheet_assets_usd']<10_000_000_000)
    return report


def write_manifest(db,directory,root=ROOT):
    root=Path(root);directory=Path(directory);directory.mkdir(parents=True,exist_ok=True)
    report=health_report(db,root);policy_hash=hashlib.sha256((root/'config/universe_policy.json').read_bytes()).hexdigest()
    stable=dict(policy_sha256=policy_hash,employers=report['employers'],dataset_snapshots=[dict(r) for r in db.execute('SELECT dataset_id,sha256,parser_version FROM employer_dataset_snapshots ORDER BY dataset_id,sha256')])
    payload=json.dumps(stable,sort_keys=True);sha=hashlib.sha256(payload.encode()).hexdigest()
    previous=db.execute('SELECT * FROM universe_manifests ORDER BY id DESC LIMIT 1').fetchone()
    db.execute('INSERT OR IGNORE INTO universe_manifests(generated_at,policy_sha256,manifest_sha256,payload) VALUES(?,?,?,?)',(now(),policy_hash,sha,payload))
    manifest_id=db.execute('SELECT id FROM universe_manifests WHERE manifest_sha256=?',(sha,)).fetchone()[0]
    if previous and previous['manifest_sha256']!=sha:
        current={e['company_id'] for e in report['employers'] if e['must_cover']}
        for employer in json.loads(previous['payload'])['employers']:
            if employer['must_cover'] and canonical_id(db,employer['company_id']) not in current:
                db.execute('INSERT INTO universe_manifest_alerts(manifest_id,company_id,reason,created_at) VALUES(?,?,?,?)',(manifest_id,employer['company_id'],'Previously must-cover employer disappeared or lost priority; explicit review required',now()))
    report['manifest_id']=manifest_id;report['manifest_sha256']=sha
    report['manifest_alerts']=[dict(r) for r in db.execute('SELECT * FROM universe_manifest_alerts ORDER BY id')]
    (directory/'universe.json').write_text(json.dumps(report,indent=2))
    (directory/'manifest.json').write_text(json.dumps(stable,indent=2,sort_keys=True))
    (directory/f'manifest-{sha[:12]}.json').write_text(json.dumps(stable,indent=2,sort_keys=True))
    with (directory/'employers.csv').open('w',newline='') as handle:
        writer=csv.writer(handle);writer.writerow(['company_id','company','tier','score','must_cover','category','domain','careers_url','providers','monitoring_status','source_datasets','target_functions'])
        for e in report['employers']:writer.writerow([e['company_id'],e['company'],e['tier'],e['score'],e['must_cover'],e['category'],e['domain'],e['careers_url'],';'.join(sorted({s['provider'] for s in e['sources']})),e['monitoring_status'],';'.join(sorted({s['id'] for s in e['inclusion_sources']})),';'.join(e['target_functions'])])
    with (directory/'entity-review.csv').open('w',newline='') as handle:
        writer=csv.writer(handle);writer.writerow(['case_id','source_name','dataset','candidate_company_ids','reason'])
        for r in report['entity_review_cases']:writer.writerow([r['id'],r['name'],r['dataset_id'],r['candidate_company_ids'],r['reason']])
    with (directory/'unnamed-trust-peers.csv').open('w',newline='') as handle:
        fields=['company','company_id','tier','score','max_reported_balance_sheet_assets_usd','monitoring_status']
        writer=csv.DictWriter(handle,fieldnames=fields);writer.writeheader();writer.writerows(report['unnamed_general_trust_peers'])
    baseline=root/'data/universe/baseline-companies.json'
    if baseline.exists():
        current={e['company_id']:e for e in report['employers']}
        with (directory/'baseline-review.csv').open('w',newline='') as handle:
            writer=csv.writer(handle);writer.writerow(['original_company_id','original_name','canonical_company_id','canonical_name','baseline_review_state','current_monitoring_status','tier','relevance_evidence'])
            for old in json.loads(baseline.read_text()):
                cid=canonical_id(db,old['id']);e=current.get(cid)
                if not e:continue
                category='DUPLICATE_ENTITY' if cid!=old['id'] else e['monitoring_status']
                writer.writerow([old['id'],old['name'],cid,e['company'],category,e['monitoring_status'],e['tier'],'STRUCTURED_SOURCE_OR_RECRUITING_EVIDENCE' if e['score'] else 'UNKNOWN_NOT_AUTOMATICALLY_EXCLUDED'])
    lines=['# Scout employer universe','',report['scope'],'',f"Canonical employers: {report['total_canonical_employers']}; raw source entities: {report['total_raw_source_entities']}; unresolved entity cases: {report['unresolved_entity_cases']}.",'',
           '| Tier | Employers | Healthy | Healthy % | Unsupported | Failing | Stale/unverified |','|---|---:|---:|---:|---:|---:|---:|']
    for tier,m in report['tier_metrics'].items():lines.append(f"| {tier} | {m['total']} | {m['monitored_successfully']} | {m['healthy_percentage']} | {m['unsupported']} | {m['failing']} | {m['stale']} |")
    lines+=['','Unsupported/failing/stale flags can overlap; healthy requires all required authoritative sources to enumerate successfully and no unresolved source scope.','','## Priority by measured impact','','| Provider | Tier 1 | Tier 2 | Total | Potential unlocks | Work type |','|---|---:|---:|---:|---:|---|']
    for r in report['provider_impact']:lines.append(f"| {r['provider']} | {r['tier_1_affected']} | {r['tier_2_affected']} | {r['total_affected']} | {r['potential_coverage_unlocked']} | {r['work_type']} |")
    lines+=['','Impact ordering prioritizes Tier 1 affected, then Tier 2 affected. Potential unlocks are estimates, not achieved coverage.','','## Must-cover failures','','| Company | Tier | Severity | Status | Providers |','|---|---:|---|---|---|']
    for r in report['critical_failures']:lines.append(f"| {r['company']} | {r['tier']} | {r['severity']} | {r['status']} | {', '.join(r['providers'])} |")
    lines+=['','## Name-blind trust-cohort validation','',f"The general trust cohort contributes {len(report['unnamed_general_trust_peers'])} Tier 1/Tier 2 employers outside the explicit anchor/regression names, including {report['unnamed_mid_sized_trust_peers']} with reported balance-sheet assets below $10B. These are regulatory-screen peers, not proof that all primarily operate as wealth managers or recruit undergraduate interns.",'','Example checks do not change inclusion or score:']
    lines+=['','| Check | Present | Tier | Manual override | Status |','|---|---|---:|---|---|']
    for r in report['regression_checks']:lines.append(f"| {r['employer']} | {r['present']} | {r['tier']} | {r['manual_override']} | {r['monitoring_status']} |")
    (directory/'universe.md').write_text('\n'.join(lines)+'\n')
    return report
