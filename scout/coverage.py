"""Evidence-based coverage: page reachability is never authoritative inventory."""
from collections import Counter, defaultdict
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timedelta, timezone
import json
import csv
from pathlib import Path
import re
import ssl
import os
import urllib.request
import urllib.error
from urllib.parse import urlsplit
from .model import now
from .sources import ADAPTERS, fetch
from .sources.base import SourceError, request_budget
from .ingest import run_source, consolidate_workday_duplicates
from .reconcile import prepare_source

PROVIDERS = {
    "talnet": r"\.tal\.net(?:[/:]|$)",
    "icims": r"icims\.com", "successfactors": r"successfactors\.(?:com|eu)|sfcareer",
    "cornerstone": r"csod\.com", "oracle": r"oraclecloud\.com|hcmUI/CandidateExperience|/sites/CX_",
    "taleo": r"taleo\.net", "phenom": r"phenompeople\.com|phenom\.com|phApp",
    "talentbrew": r"talentbrew\.com", "eightfold": r"eightfold\.ai",
    "yello": r"recsolu\.com|yello\.co", "ukg": r"ultipro\.com|ukg\.com",
    "dayforce": r"dayforcehcm\.com|dayforce\.com", "paylocity": r"paylocity\.com",
    "breezy": r"breezy\.hr", "avature": r"avature\.net", "brassring": r"brassring\.com",
    "greenhouse": r"(?:boards|job-boards)\.greenhouse\.io", "lever": r"jobs\.lever\.co",
    "ashby": r"jobs\.ashbyhq\.com", "smartrecruiters": r"(?:jobs|careers)\.smartrecruiters\.com",
    "workday": r"myworkdayjobs\.com|myworkdaysite\.com",
}


def provider_hint(url, html=""):
    for provider, pattern in PROVIDERS.items():
        m = re.search(pattern, url + "\n" + html, re.I)
        if m:
            return provider, m.group(0)
    return "custom_unidentified", "No supported provider fingerprint in inspected public page"


def configured(source):
    return bool(source["careers_url"] or (source["ats_type"] in ADAPTERS and source["ats_identifier"]))


def probe(source):
    stamp = now()
    url = source["careers_url"] or ""
    result = dict(source_id=source["id"], checked_at=stamp, status="UNKNOWN", response_status=None,
                  final_url=url, provider_hint=source["ats_type"], evidence="", error=None)
    if not configured(source):
        result.update(status="NO_SOURCE_CONFIGURED", provider_hint="unconfigured")
        return result
    try:
        p = urlsplit(url)
    except ValueError as exc:
        result.update(status="BROKEN_CAREERS_URL", error=str(exc))
        return result
    if p.scheme not in {"https", "http"} or not p.hostname or re.search(r"\s|[<>]", url):
        result.update(status="BROKEN_CAREERS_URL", error="Missing or malformed HTTP(S) careers URL")
        return result
    try:
        context = None
        if not os.environ.get("SSL_CERT_FILE") and not ssl.get_default_verify_paths().cafile and Path('/etc/ssl/cert.pem').is_file():
            context = ssl.create_default_context(cafile='/etc/ssl/cert.pem')
        req = urllib.request.Request(url, headers={"User-Agent": "InternshipScout/1.0 (coverage audit)", "Accept": "text/html"})
        with urllib.request.urlopen(req, context=context, timeout=15) as response:
            html = response.read(512 * 1024).decode('utf-8', errors='replace')
            result.update(response_status=response.status, final_url=response.url, status="REACHABLE_PORTAL_ONLY")
        provider, evidence = provider_hint(result["final_url"], html)
        result.update(provider_hint=provider if source["ats_type"] == "custom" else source["ats_type"], evidence=evidence)
        if re.search(r"verify you are human|access denied|captcha|just a moment\.\.\.", html, re.I):
            result.update(status="BLOCKED", error="Public page returned a challenge/access-denied marker")
    except urllib.error.HTTPError as exc:
        result.update(status="BLOCKED" if exc.code in (401, 403, 429) else "BROKEN_CAREERS_URL" if exc.code in (404, 410) else "HTTP_ERROR",
                      response_status=exc.code, error=str(exc))
        result["provider_hint"], result["evidence"] = provider_hint(url)
        if source["ats_type"] != "custom":
            result["provider_hint"] = source["ats_type"]
    except (urllib.error.URLError, TimeoutError, OSError) as exc:
        # DNS/TLS/timeout does not prove that the employer URL is invalid.
        result.update(status="UNKNOWN", error=str(exc))
        result["provider_hint"], result["evidence"] = provider_hint(url)
        if source["ats_type"] != "custom":
            result["provider_hint"] = source["ats_type"]
    return result


def recent(value, cutoff):
    return bool(value and datetime.fromisoformat(value) >= cutoff)


def source_state(source, probe_row=None, at=None):
    at = at or datetime.now(timezone.utc)
    if not configured(source):
        return "NO_SOURCE_CONFIGURED"
    if source["ats_type"] in ADAPTERS:
        if source["health_status"].startswith("SUCCESS") and recent(source["last_successful_check_at"], at - timedelta(hours=24)):
            return "NO_PUBLIC_JOBS" if source["jobs_returned"] == 0 else "MONITORED_SUCCESSFULLY"
        status = source["health_status"]
        if status == "BLOCKED":
            return "BLOCKED"
        if status == "PARSER_ERROR":
            return "PARSER_FAILURE"
        if source["last_response_status"] in (404, 410):
            return "BROKEN_CAREERS_URL"
        return "UNKNOWN"
    if probe_row:
        if probe_row["status"] in {"BROKEN_CAREERS_URL", "BLOCKED", "NO_SOURCE_CONFIGURED"}:
            return probe_row["status"]
    return "CUSTOM_PORTAL_REQUIRED" if source["ats_type"] == "custom" else "UNSUPPORTED_ATS"


def company_state(states):
    if not states or set(states) == {"NO_SOURCE_CONFIGURED"}:
        return "NO_SOURCE_CONFIGURED"
    successes = {"MONITORED_SUCCESSFULLY", "NO_PUBLIC_JOBS"}
    if all(s in successes for s in states):
        return "NO_PUBLIC_JOBS" if set(states) == {"NO_PUBLIC_JOBS"} else "MONITORED_SUCCESSFULLY"
    if any(s in successes for s in states):
        return "PARTIALLY_MONITORED"
    for state in ("PARSER_FAILURE", "BLOCKED", "BROKEN_CAREERS_URL", "UNSUPPORTED_ATS", "CUSTOM_PORTAL_REQUIRED", "UNKNOWN"):
        if state in states:
            return state
    return "UNKNOWN"


def summary(db, at=None, audit_id=None):
    at = at or datetime.now(timezone.utc)
    cutoff = at - timedelta(hours=24)
    week = at - timedelta(days=7)
    companies = [dict(r) for r in db.execute('SELECT * FROM companies WHERE active=1 AND canonical_company_id IS NULL ORDER BY name')]
    sources = [dict(r,company_id=r['owner_id']) for r in db.execute('SELECT s.*,COALESCE(c.canonical_company_id,c.id) owner_id FROM sources s JOIN companies c ON c.id=s.company_id JOIN companies owner ON owner.id=COALESCE(c.canonical_company_id,c.id) WHERE s.active=1 AND owner.active=1')]
    probes = {r['source_id']: dict(r) for r in db.execute('SELECT p.* FROM source_probes p JOIN (SELECT source_id,MAX(id) id FROM source_probes GROUP BY source_id) latest ON p.id=latest.id')}
    by_company, providers = defaultdict(list), defaultdict(set)
    failures, issues, ranked = Counter(), [], defaultdict(set)
    healthy, failing, unsupported = 0, 0, 0
    for s in sources:
        p = probes.get(s['id'])
        state = source_state(s, p, at)
        provider = s['ats_type'] if s['ats_type'] != 'custom' else (p or {}).get('provider_hint') or provider_hint(s['careers_url'] or '')[0]
        if not configured(s):
            provider = 'unconfigured'
        item = dict(s, monitoring_state=state, provider=provider, probe=p)
        by_company[s['company_id']].append(item)
        providers[provider].add(s['company_id'])
        if state in {'MONITORED_SUCCESSFULLY', 'NO_PUBLIC_JOBS'}:
            healthy += 1
        elif s['ats_type'] in ADAPTERS and s['last_checked_at']:
            failing += 1
        if s['ats_type'] not in ADAPTERS and configured(s):
            unsupported += 1
        if state not in {'MONITORED_SUCCESSFULLY', 'NO_PUBLIC_JOBS'}:
            failures[state] += 1
            issues.append(dict(company_id=s['company_id'],source_id=s['id'],state=state,provider=provider,
                               url=s['careers_url'],error=s['last_error'] or (p or {}).get('error')))
    rows, states, successful, partial, last24, last7 = [], Counter(), 0, 0, 0, 0
    for c in companies:
        ss = by_company[c['id']]
        state = company_state([s['monitoring_state'] for s in ss])
        states[state] += 1
        fully = state in {'MONITORED_SUCCESSFULLY', 'NO_PUBLIC_JOBS'}
        successful += int(fully)
        partial += int(state == 'PARTIALLY_MONITORED')
        for days, boundary in ((1, cutoff), (7, week)):
            all_recent = bool(ss) and all(recent(s['last_successful_check_at'], boundary) for s in ss)
            if days == 1:
                last24 += int(all_recent)
            else:
                last7 += int(all_recent)
        if not fully:
            for s in ss:
                if s['ats_type'] not in ADAPTERS and configured(s):
                    ranked[s['provider']].add(c['id'])
        rows.append(dict(company_id=c['id'],company=c['name'],monitoring_state=state,
                         sources=[dict(source_id=s['id'],ats=s['ats_type'],provider=s['provider'],state=s['monitoring_state'],url=s['careers_url']) for s in ss]))
    def count(query, parameters=()):
        return db.execute(query, parameters).fetchone()[0]
    metrics = dict(known_companies=len(companies), configured_companies=sum(any(configured(s) for s in by_company[c['id']]) for c in companies),
                   successfully_monitored_companies=successful, monitoring_coverage_percent=round(100*successful/len(companies),4) if companies else 0,
                   partially_monitored_companies=partial, companies_with_any_successful_source=successful+partial,
                   healthy_sources=healthy, failing_sources=failing, unsupported_sources=unsupported,
                   companies_successfully_checked_last_24h=last24, companies_successfully_checked_last_7d=last7,
                   jobs_discovered_last_24h=count('SELECT COUNT(*) FROM jobs WHERE first_seen_at>=? AND first_seen_at<=? AND id NOT IN (SELECT duplicate_job_id FROM job_merges)',(cutoff.isoformat(),at.isoformat())),
                   jobs_discovered_last_7d=count('SELECT COUNT(*) FROM jobs WHERE first_seen_at>=? AND first_seen_at<=? AND id NOT IN (SELECT duplicate_job_id FROM job_merges)',(week.isoformat(),at.isoformat())),
                   active_jobs=count('SELECT COUNT(*) FROM jobs WHERE active=1'),
                   jobs_requiring_classification_review=count('SELECT COUNT(*) FROM jobs WHERE needs_review=1 AND id NOT IN (SELECT duplicate_job_id FROM job_merges)'),
                   duplicate_rows_consolidated=count('SELECT COUNT(*) FROM job_merges'))
    audit = db.execute('SELECT * FROM coverage_audits WHERE id=?',(audit_id,)).fetchone() if audit_id else db.execute('SELECT * FROM coverage_audits ORDER BY id DESC LIMIT 1').fetchone()
    if audit:
        audit_id = audit['id']
        baseline = set(json.loads(audit['baseline_active_legacy_jobs']))
        found = {r[0] for r in db.execute("SELECT DISTINCT job_id FROM job_verifications WHERE outcome='FOUND' AND checked_at>=?",(audit['started_at'],))}
        missing = {r[0] for r in db.execute("SELECT DISTINCT job_id FROM job_verifications WHERE outcome='MISSING' AND checked_at>=?",(audit['started_at'],))}
        inactive = {r[0] for r in db.execute('SELECT id FROM jobs WHERE active=0')}
        metrics.update(historical_active_baseline=len(baseline), historical_active_verified=len((baseline & found)-inactive),
                       historical_active_safely_closed=len(baseline & missing & inactive),
                       historical_active_unresolved=len(baseline - (found-inactive) - (missing & inactive)))
    def unlockable(provider, ids):
        successes={'MONITORED_SUCCESSFULLY','NO_PUBLIC_JOBS'}
        return sum(all(s['provider']==provider and s['ats_type'] not in ADAPTERS and configured(s)
                       for s in by_company[cid] if s['monitoring_state'] not in successes) for cid in ids)
    rank = [dict(provider=p,companies_blocked=len(ids),potential_full_monitoring_unlocks=unlockable(p,ids),company_ids=sorted(ids),
                 estimated_value='Configuration/routing to existing adapter' if p in ADAPTERS else 'Employer-specific work; not one universal adapter' if p=='custom_unidentified' else 'Shared adapter candidate') for p,ids in ranked.items()]
    rank.sort(key=lambda r:(-r['companies_blocked'],r['provider']))
    repeated = [dict(s,company=next(c['name'] for c in companies if c['id']==s['company_id'])) for s in sources if s['consecutive_failures']>=2]
    configured_ats=defaultdict(set)
    for s in sources:
        configured_ats[s['ats_type']].add(s['company_id'])
    return dict(generated_at=at.isoformat(),audit_id=audit_id,
                definition='Full monitoring requires every active source configuration to pass an authoritative inventory check in the last 24h. Missing sources and reachable HTML pages do not count. This measures known configured sources, not undiscovered employer sites.',
                metrics=metrics,company_states=dict(states),source_failure_states=dict(failures),
                crawl_status_distribution=dict(Counter(s['health_status'] for s in sources if s['ats_type'] in ADAPTERS)),
                probe_status_distribution=dict(Counter(p['status'] for p in probes.values())),
                configured_ats_distribution=[dict(provider=p,companies=len(ids),sources=sum(s['ats_type']==p for s in sources),
                     healthy_sources=sum(s['ats_type']==p and s['monitoring_state'] in {'MONITORED_SUCCESSFULLY','NO_PUBLIC_JOBS'} for ss in by_company.values() for s in ss),
                     companies_with_working_source=len({s['company_id'] for ss in by_company.values() for s in ss if s['ats_type']==p and s['monitoring_state'] in {'MONITORED_SUCCESSFULLY','NO_PUBLIC_JOBS'}})) for p,ids in sorted(configured_ats.items())],
                provider_distribution=[dict(provider=p,companies=len(ids),sources=sum(1 for ss in by_company.values() for s in ss if s['provider']==p)) for p,ids in sorted(providers.items())],
                unsupported_ranking=rank,repeated_failures=repeated,
                invalid_careers_urls=[r for r in issues if r['state']=='BROKEN_CAREERS_URL'],source_issues=issues,companies=rows)


def write_report(report, directory):
    directory = Path(directory)
    directory.mkdir(parents=True,exist_ok=True)
    (directory/'coverage.json').write_text(json.dumps(report,indent=2))
    with (directory/'companies.csv').open('w',newline='') as handle:
        writer=csv.writer(handle)
        writer.writerow(['company_id','company','monitoring_state','source_ids','providers','source_states'])
        for row in report['companies']:
            writer.writerow([row['company_id'],row['company'],row['monitoring_state'],
                             ';'.join(str(s['source_id']) for s in row['sources']),
                             ';'.join(sorted({s['provider'] for s in row['sources']})),
                             ';'.join(s['state'] for s in row['sources'])])
    lines=['# Employer coverage audit','',report['definition'],'',f"Generated: {report['generated_at']}",'','| Metric | Value |','|---|---:|']
    lines += [f'| {k} | {v} |' for k,v in report['metrics'].items()]
    for key,title in [('company_states','Company monitoring states'),('source_failure_states','Source gap states'),
                      ('crawl_status_distribution','Latest supported-source crawl statuses'),('probe_status_distribution','Unsupported-source URL probes')]:
        lines += ['',f'## {title}','','| State | Count |','|---|---:|']+[f'| {k} | {v} |' for k,v in sorted(report[key].items())]
    lines += ['','## Configured ATS distribution (company counts overlap)','','| Provider | Companies | Sources |','|---|---:|---:|']
    lines += [f"| {r['provider']} | {r['companies']} | {r['sources']} |" for r in report['configured_ats_distribution']]
    lines += ['','## Provider fingerprints and configured ATS (company counts overlap)','','| Provider | Companies | Sources |','|---|---:|---:|']
    lines += [f"| {r['provider']} | {r['companies']} | {r['sources']} |" for r in report['provider_distribution']]
    lines += ['','## Unsupported provider priority','','Counts overlap. Potential unlocks assume every affected source is implemented successfully; they are estimates, not verified coverage.','','| Provider | Companies affected | Potential full monitoring unlocks | Estimated value |','|---|---:|---:|---|']
    lines += [f"| {r['provider']} | {r['companies_blocked']} | {r['potential_full_monitoring_unlocks']} | {r['estimated_value']} |" for r in report['unsupported_ranking']]
    lines += ['','## Repeated source failures','','| Company | Source | Failures | Last error |','|---|---:|---:|---|']
    lines += [f"| {r['company']} | {r['id']} | {r['consecutive_failures']} | {str(r['last_error']).replace('|','/')} |" for r in report['repeated_failures']]
    lines += ['','## Invalid or unavailable careers URLs','','| Company ID | Source | URL | Error |','|---|---:|---|---|']
    lines += [f"| {r['company_id']} | {r['source_id']} | {r['url']} | {str(r['error']).replace('|','/')} |" for r in report['invalid_careers_urls']]
    lines += ['','Complete per-company states, unsupported company IDs and source errors are in `coverage.json`.']
    (directory/'coverage.md').write_text('\n'.join(lines)+'\n')


def run_audit(db, directory, workers=6, budget_seconds=180, audit_id=None):
    resume_since=None
    if audit_id:
        audit=db.execute('SELECT started_at FROM coverage_audits WHERE id=?',(audit_id,)).fetchone()
        if not audit:
            raise ValueError('Unknown coverage audit ID')
        resume_since=audit[0]
    if not audit_id:
        baseline=[r[0] for r in db.execute('SELECT DISTINCT j.id FROM jobs j JOIN legacy_records l ON j.id=l.job_id WHERE j.active=1')]
        with db:
            audit_id=db.execute('INSERT INTO coverage_audits(started_at,baseline_active_legacy_jobs) VALUES(?,?)',(now(),json.dumps(baseline))).lastrowid
    sources=[prepare_source(db,r) for r in db.execute('SELECT s.* FROM sources s JOIN companies c ON c.id=s.company_id JOIN companies owner ON owner.id=COALESCE(c.canonical_company_id,c.id) WHERE s.active=1 AND owner.active=1')]
    def check(s):
        if s['ats_type'] not in ADAPTERS:
            return 'probe',probe(s)
        try:
            with request_budget(budget_seconds):
                return 'snapshot',fetch(s)
        except Exception as exc:
            return 'error',exc
    completed=0
    pending=sources
    if resume_since:
        already_probed={r[0] for r in db.execute('SELECT DISTINCT source_id FROM source_probes WHERE checked_at>=?',(resume_since,))}
        pending=[s for s in sources if not ((s['last_checked_at'] and s['last_checked_at']>=resume_since) or s['id'] in already_probed)]
        completed=len(sources)-len(pending)
        print(json.dumps(dict(audit_id=audit_id,already_checked_sources=completed,remaining_sources=len(pending))),flush=True)
    with ThreadPoolExecutor(max_workers=max(1,min(12,workers))) as pool:
        futures={pool.submit(check,s):s for s in pending}
        for future in as_completed(futures):
            s=futures[future]
            kind,value=future.result()
            if kind=='probe':
                with db:
                    db.execute('INSERT INTO source_probes('+','.join(value)+') VALUES('+','.join('?' for _ in value)+')',list(value.values()))
            else:
                def fetched(_source):
                    if kind=='error':
                        raise value
                    return value
                run_source(db,s,fetcher=fetched)
            completed+=1
            if completed%10==0 or completed==len(sources):
                print(json.dumps(dict(audit_id=audit_id,completed_sources=completed,total_sources=len(sources))),flush=True)
    merged=consolidate_workday_duplicates(db)
    if merged:
        Path(directory).mkdir(parents=True,exist_ok=True)
        (Path(directory)/'duplicate-consolidation.json').write_text(json.dumps(merged,indent=2))
    report=summary(db,audit_id=audit_id)
    with db:
        db.execute('UPDATE coverage_audits SET completed_at=?,report=? WHERE id=?',(now(),json.dumps(report),audit_id))
    write_report(report,directory)
    return report
