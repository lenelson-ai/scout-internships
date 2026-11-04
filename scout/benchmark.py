"""Verified external postings are recall QA evidence, never canonical job feeds."""
from datetime import datetime,timedelta,timezone
import json
import math
from pathlib import Path
import statistics
from .entities import resolve
from .model import Job,date,now
from .reconcile import url_key
from .classify import classify
from .universe_qa import health_report


def import_benchmarks(db,rows):
    for row in rows:
        required=('benchmark_key','employer','title','benchmark_source','verified_at','expected_category','verified_by')
        if any(not row.get(k) for k in required):raise ValueError('Verified benchmark requires '+', '.join(required))
        if row['expected_category'] not in {'STRATEGY','FINANCE','CONSULTING'}:raise ValueError('Benchmark category must be a target function')
        if not (row.get('canonical_url') or row.get('external_job_id')):raise ValueError('Benchmark requires stable posting URL or ID')
        verified=date(row['verified_at'])
        if not verified:raise ValueError('Benchmark verification date must be explicit ISO date/time')
        db.execute('''INSERT INTO benchmark_jobs(benchmark_key,employer,title,location,canonical_url,external_job_id,posted_at,benchmark_source,source_url,verified_at,expected_category,metadata)
                      VALUES(?,?,?,?,?,?,?,?,?,?,?,?) ON CONFLICT(benchmark_key) DO UPDATE SET
                      employer=excluded.employer,title=excluded.title,canonical_url=excluded.canonical_url,external_job_id=excluded.external_job_id,
                      verified_at=excluded.verified_at,expected_category=excluded.expected_category,metadata=excluded.metadata''',
                   (row['benchmark_key'],row['employer'],row['title'],row.get('location'),row.get('canonical_url'),row.get('external_job_id'),
                    date(row.get('posted_at')),row['benchmark_source'],row.get('source_url'),verified,row['expected_category'],json.dumps(row)))
    return len(rows)


def evaluate(db,window_days=7,at=None):
    at=at or datetime.now(timezone.utc);boundary=at-timedelta(days=window_days)
    employers={e['company_id']:e for e in health_report(db,at=at)['employers']}
    results=[];latencies=[]
    for b in db.execute('SELECT * FROM benchmark_jobs ORDER BY id').fetchall():
        cid,candidates,_=resolve(db,b['employer']);matched=None;inventory=None;decision=None;first=None
        desired_url=url_key(b['canonical_url']) if b['canonical_url'] else ''
        if cid:
            for row in db.execute('SELECT * FROM jobs WHERE company_id=? AND id NOT IN (SELECT duplicate_job_id FROM job_merges)',(cid,)):
                if ((b['external_job_id'] and str(row['external_job_id'])==b['external_job_id']) or
                    (desired_url and desired_url in {url_key(row['source_url']),url_key(row['canonical_apply_url'])})):
                    matched=dict(row);first=row['first_seen_at'];decision=dict(internship_confidence=row['internship_confidence'],function=row['function']);break
            if not matched:
                for row in db.execute('SELECT * FROM source_inventory WHERE company_id=?',(cid,)):
                    if ((b['external_job_id'] and row['external_job_id']==b['external_job_id']) or
                        (desired_url and url_key(row['canonical_url'])==desired_url)):
                        inventory=dict(row);first=row['first_seen_at'];decision=json.loads(row['classification']) or classify(Job(**json.loads(row['normalized_payload'])));break
        observed=bool(matched or inventory)
        internship=bool(decision and decision.get('internship_confidence',0)>=.4)
        classified=bool(decision and decision.get('function')==b['expected_category'])
        posted=matched.get('posted_at') if matched else json.loads(inventory['normalized_payload']).get('posted_at') if inventory else None
        in_window=any(value and boundary<=datetime.fromisoformat(value)<=at for value in (first,posted))
        surfaced=bool(matched and matched['active'] and internship and classified and in_window)
        if not cid or cid not in employers:reason='EMPLOYER_UNIVERSE_MISS'
        elif not employers[cid]['healthy']:reason='SOURCE_COVERAGE_MISS'
        elif not observed:reason='INGESTION_PARSER_MISS'
        elif not internship:reason='INTERNSHIP_DETECTION_MISS'
        elif not classified:reason='CLASSIFICATION_MISS'
        elif not surfaced:reason='TIMING_MISS' if not in_window else 'INGESTION_PARSER_MISS'
        else:reason=None
        observed_at=json.loads(b['metadata']).get('first_observed_at') or b['verified_at']
        latency=None
        if first and date(observed_at):
            latency=max(0,(datetime.fromisoformat(first)-datetime.fromisoformat(date(observed_at))).total_seconds());latencies.append(latency)
        item=dict(benchmark_id=b['id'],employer=b['employer'],company_id=cid,scout_job_id=matched['id'] if matched else None,
                  observed_by_scout=observed,scout_first_seen_at=first,internship_detected=internship,classified_correctly=classified,
                  surfaced_in_window=surfaced,miss_reason=reason,latency_from_external_observation_seconds=latency)
        results.append(item)
        db.execute('UPDATE benchmark_jobs SET company_id=?,scout_job_id=?,miss_reason=?,scout_first_seen_at=?,evaluated_at=? WHERE id=?',
                   (cid,item['scout_job_id'],reason,first,now(),b['id']))
    total=len(results);observed=sum(r['observed_by_scout'] for r in results)
    def recall(n,d):return n/d if d else None
    ordered=sorted(latencies)
    return dict(verified_benchmark_jobs=total,scope='Recall on this verified benchmark only; not all internships. Latency is relative to external observation, not inferred public posting time.',
                job_discovery_recall=recall(observed,total),internship_detection_recall_on_observed=recall(sum(r['internship_detected'] for r in results),observed),
                classification_recall_on_observed=recall(sum(r['classified_correctly'] for r in results),observed),surfacing_recall=recall(sum(r['surfaced_in_window'] for r in results),total),
                median_discovery_latency_seconds=statistics.median(latencies) if latencies else None,
                p95_discovery_latency_seconds=ordered[max(0,math.ceil(.95*len(ordered))-1)] if ordered else None,results=results)
