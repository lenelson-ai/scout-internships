from .eligibility import eligibility
from dataclasses import asdict
import json
import logging
from .classify import classify
from .model import now, normalize
from .sources import fetch, detect, ADAPTERS
from .sources.base import SourceError
from .reconcile import scoped_legacy, matches_legacy, prepare_source, url_key
from .sources.workday import board_key

log = logging.getLogger(__name__)


def identity(job):
    if job.external_job_id:
        return "id:" + job.external_job_id
    if job.canonical_apply_url:
        return "url:" + job.canonical_apply_url
    return "fallback:" + normalize(job.title) + "|" + json.dumps(sorted(normalize(x) for x in job.locations))


def upsert(db, cid, sid, job, stamp, *, first_seen=None, active=True, preserve_locations=False):
    job.clean()
    decision = classify(job)
    key = identity(job)
    existing = None
    if sid is not None:
        existing = db.execute("""SELECT j.* FROM jobs j JOIN source_jobs sj ON sj.job_id=j.id
                                 WHERE sj.source_id=? AND sj.identity=?""", (sid, key)).fetchone()
    # Company + external ID is primary. Across distinct ATS boards, only a matching URL
    # or legacy record can prove identity (numeric IDs can collide across providers).
    if not existing and job.external_job_id:
        candidates = db.execute("SELECT * FROM jobs WHERE company_id=? AND external_job_id=? AND id NOT IN (SELECT duplicate_job_id FROM job_merges)", (cid, job.external_job_id)).fetchall()
        for candidate in candidates:
            bound = db.execute("SELECT s.* FROM sources s JOIN source_jobs sj ON s.id=sj.source_id WHERE sj.job_id=?", (candidate["id"],)).fetchall()
            current = db.execute("SELECT * FROM sources WHERE id=?", (sid,)).fetchone() if sid else None
            same_scope = any(current and (s["ats_type"], s["ats_identifier"]) == (current["ats_type"], current["ats_identifier"]) for s in bound)
            # Workday requisitions belong to the tenant, and may be published on
            # both a campus board and the main external board. Keep both memberships.
            if current and current['ats_type']=='workday':
                board_identity=board_key(current['careers_url'])
                same_scope = same_scope or any(s['ats_type']=='workday' and board_identity and board_key(s['careers_url'])
                                               and board_key(s['careers_url'])[:2]==board_identity[:2] for s in bound)
            if not bound or same_scope or candidate["canonical_apply_url"] == job.canonical_apply_url:
                existing = candidate
                break
    if not existing and job.canonical_apply_url:
        # A shared program URL is insufficient when both postings expose DIFFERENT IDs.
        existing = db.execute("""SELECT * FROM jobs WHERE company_id=? AND canonical_apply_url=?
                                 AND (external_job_id IS NULL OR external_job_id='' OR ?='' OR external_job_id=?)
                                 ORDER BY id LIMIT 1""",
                              (cid, job.canonical_apply_url, job.external_job_id, job.external_job_id)).fetchone()
    if not existing and job.source_url:
        # ATS application and hosted URLs can differ. A legacy employer requisition
        # number can also differ from the ATS public posting ID for the same dedicated URL.
        dedicated_ats_url = detect(job.source_url)[0] in ADAPTERS
        existing = db.execute("""SELECT * FROM jobs WHERE company_id=?
                                 AND (source_url IN (?,?) OR canonical_apply_url IN (?,?))
                                 AND (COALESCE(external_job_id,'')='' OR ?='' OR external_job_id=?
                                      OR (source_type='legacy' AND ?)) ORDER BY id LIMIT 1""",
                              (cid, job.source_url, job.canonical_apply_url, job.source_url, job.canonical_apply_url,
                               job.external_job_id, job.external_job_id, dedicated_ats_url)).fetchone()
    loc_key = json.dumps(sorted(set(normalize(x) for x in job.locations)))
    if not existing and not job.external_job_id and not job.canonical_apply_url:
        existing = db.execute("""SELECT * FROM jobs WHERE company_id=? AND normalized_title=? AND normalized_locations=?
                                 AND COALESCE(external_job_id,'')='' AND COALESCE(canonical_apply_url,'')='' LIMIT 1""",
                              (cid, normalize(job.title), loc_key)).fetchone()
    values = asdict(job)
    values["countries"] = json.dumps(job.countries)
    values.update({k: v for k, v in decision.items() if k != "retain"})
    values["classification_reasons"] = json.dumps(values["classification_reasons"])
    values["company_id"] = cid
    values["normalized_title"] = normalize(job.title)
    values["normalized_locations"] = loc_key
    values["locations"] = json.dumps(job.locations)
    values["updated_at"] = stamp
    values["last_seen_at"] = max(stamp, existing["last_seen_at"]) if existing else stamp
    values["active"] = int(active)
    values["closed_at"] = None if active else stamp
    values["source_type"] = db.execute("SELECT ats_type FROM sources WHERE id=?", (sid,)).fetchone()[0] if sid else "legacy"
    if existing:
        from urllib.parse import urlsplit,unquote
        native_primary=(existing['source_type']=='workday' and board_key(existing['canonical_apply_url']) and existing['external_job_id'] and unquote(urlsplit(existing['canonical_apply_url']).path).removesuffix('/apply').casefold().endswith('_'+existing['external_job_id'].casefold()) and values['source_type']!='workday' and url_key(existing['canonical_apply_url'])==url_key(job.canonical_apply_url))
        if existing['external_job_id']!=job.external_job_id and (native_primary or db.execute('SELECT 1 FROM job_merges WHERE canonical_job_id=?',(existing['id'],)).fetchone()):
            values['external_job_id']=existing['external_job_id'];values['source_type']=existing['source_type'];values['canonical_apply_url']=existing['canonical_apply_url']
        # Imported multi-location rows sharing an actual requisition are one posting.
        if preserve_locations:
            merged = sorted(set(json.loads(existing["locations"]) + job.locations))
            values["locations"] = json.dumps(merged)
            values["normalized_locations"] = json.dumps(sorted(set(normalize(x) for x in merged)))
        values["first_seen_at"] = min(existing["first_seen_at"], first_seen or stamp)
        if not values["posted_at"]:
            values["posted_at"] = existing["posted_at"]
            values["posted_at_confidence"] = existing["posted_at_confidence"]
        if not values["description"]:
            values["description"] = existing["description"]
        if not values["external_job_id"]:
            values["external_job_id"] = existing["external_job_id"]
        jid = existing["id"]
        db.execute("UPDATE jobs SET " + ",".join(k + "=?" for k in values) + " WHERE id=?", [*values.values(), jid])
        event = "REOPENED" if not existing["active"] and active else "DUPLICATE"
    else:
        previous = db.execute("""SELECT id FROM jobs WHERE company_id=? AND normalized_title=? AND normalized_locations=?
                                 AND COALESCE(external_job_id,'')<>? ORDER BY first_seen_at DESC,id DESC LIMIT 1""",
                              (cid, normalize(job.title), loc_key, job.external_job_id)).fetchone() if job.external_job_id else None
        values.update(first_seen_at=first_seen or stamp, created_at=stamp, repost_of=previous[0] if previous else None)
        cursor = db.execute("INSERT INTO jobs(" + ",".join(values) + ") VALUES(" + ",".join("?" for _ in values) + ")", list(values.values()))
        jid = cursor.lastrowid
        event = "POSSIBLE_REPOST" if previous else "NEW"
    if sid is not None:
        db.execute("""INSERT INTO source_jobs(source_id,identity,job_id,external_job_id,source_url,canonical_apply_url,first_seen_at,last_seen_at,present,normalized_payload)
                      VALUES(?,?,?,?,?,?,?,?,1,?) ON CONFLICT(source_id,identity) DO UPDATE SET
                      job_id=excluded.job_id,source_url=excluded.source_url,canonical_apply_url=excluded.canonical_apply_url,
                      last_seen_at=excluded.last_seen_at,present=1,normalized_payload=excluded.normalized_payload""",
                   (sid, key, jid, job.external_job_id, job.source_url, job.canonical_apply_url, first_seen or stamp, stamp, json.dumps(asdict(job))))
    return jid, event


def consolidate_workday_duplicates(db):
    """Repair same-tenant cross-board duplicates while retaining original job rows."""
    groups={}
    for row in db.execute("""SELECT DISTINCT j.*,s.careers_url board_url FROM jobs j
                              JOIN source_jobs sj ON sj.job_id=j.id JOIN sources s ON sj.source_id=s.id
                              WHERE s.ats_type='workday' AND j.id NOT IN (SELECT duplicate_job_id FROM job_merges)"""):
        key=board_key(row['board_url'])
        if key and row['external_job_id']:
            groups.setdefault((row['company_id'],row['external_job_id'],key[:2]),{})[row['id']]=dict(row)
    merged=[]
    with db:
        for group in groups.values():
            if len(group)<2:
                continue
            records=sorted(group.values(),key=lambda r:(r['first_seen_at'],r['id']))
            canonical=records[0]
            locations=sorted({loc for r in records for loc in json.loads(r['locations'])})
            db.execute('UPDATE jobs SET first_seen_at=?,last_seen_at=?,locations=?,normalized_locations=? WHERE id=?',
                       (min(r['first_seen_at'] for r in records),max(r['last_seen_at'] for r in records),json.dumps(locations),
                        json.dumps(sorted(set(normalize(x) for x in locations))),canonical['id']))
            for duplicate in records[1:]:
                db.execute('UPDATE source_jobs SET job_id=? WHERE job_id=?',(canonical['id'],duplicate['id']))
                db.execute('UPDATE legacy_records SET job_id=? WHERE job_id=?',(canonical['id'],duplicate['id']))
                db.execute('UPDATE jobs SET repost_of=? WHERE repost_of=? AND id<>?',(canonical['id'],duplicate['id'],canonical['id']))
                db.execute('UPDATE jobs SET repost_of=NULL WHERE id=? AND repost_of=?',(canonical['id'],duplicate['id']))
                db.execute("INSERT INTO job_merges VALUES(?,?,?,?)",(duplicate['id'],canonical['id'],now(),'Same company, Workday tenant and requisition; multiple public boards'))
                db.execute('UPDATE jobs SET active=0,closed_at=NULL WHERE id=?',(duplicate['id'],))
                merged.append(dict(duplicate_job_id=duplicate['id'],canonical_job_id=canonical['id']))
            present=db.execute('SELECT 1 FROM source_jobs WHERE job_id=? AND present=1',(canonical['id'],)).fetchone()
            if present:
                db.execute('UPDATE jobs SET active=1,closed_at=NULL WHERE id=?',(canonical['id'],))
    return merged


def apply_snapshot(db, source, snapshot, stamp):
    """Atomic validation, ingestion, and lifecycle updates for a complete source inventory."""
    for job in snapshot.jobs:
        job.clean()
    keys = [identity(j) for j in snapshot.jobs]
    if len(keys) != len(set(keys)):
        raise SourceError("PARSER_ERROR", "Duplicate identities in snapshot")
    if not snapshot.authoritative:
        raise SourceError("PARSER_ERROR", "Incomplete/non-authoritative snapshot; no lifecycle changes applied")
    previous = db.execute("""SELECT jobs_returned,finished_at FROM crawl_runs WHERE source_id=? AND authoritative=1
                             ORDER BY id DESC LIMIT 1""", (source["id"],)).fetchone()
    # A suddenly empty or sharply truncated board needs investigation. Repeated suspicious
    # results do not establish a new baseline; only successful authoritative checks do.
    legacy = scoped_legacy(db, source)
    tracked_present = db.execute("SELECT COUNT(*) FROM source_jobs WHERE source_id=? AND present=1", (source["id"],)).fetchone()[0]
    if not snapshot.jobs and (tracked_present or any(j["active"] for j in legacy)):
        raise SourceError("PARSER_ERROR", "Empty inventory conflicts with tracked/migrated active jobs; closure withheld")
    if previous and previous[0] and (not snapshot.jobs or len(snapshot.jobs) < previous[0] * .2):
        raise SourceError("PARSER_ERROR", f"Suspicious inventory drop: {previous[0]} -> {len(snapshot.jobs)}; closure withheld")
    seen, matching, events = set(keys), 0, {}
    found_legacy = set()
    inventory_bootstrap=not previous or (previous[0]>0 and not db.execute('SELECT 1 FROM source_inventory WHERE source_id=? LIMIT 1',(source['id'],)).fetchone())
    path_id_history={};proper_id_urls=set()
    if source['ats_type']=='workday':
        for row in db.execute('SELECT * FROM source_inventory WHERE source_id=?',(source['id'],)):
            if (row['external_job_id'] or '').startswith('/job/'):
                path_id_history.setdefault(url_key(row['canonical_url']),[]).append(dict(row))
            else:proper_id_urls.add(url_key(row['canonical_url']))
    from .discovery_metrics import start_census,observe
    census=start_census(db,source,snapshot.jobs,stamp,previous)
    for job in snapshot.jobs:
        metrics_prior=db.execute("select * from source_inventory where source_id=? and identity=?",(source["id"],identity(job))).fetchone()
        observed_jid=None;known_before=False
        # Persist the entire unfiltered authoritative inventory before screening.
        prior_inventory=db.execute('SELECT first_seen_at FROM source_inventory WHERE source_id=? AND identity=?',(source['id'],identity(job))).fetchone()
        enriched_origin=None
        if not prior_inventory and source['ats_type']=='workday' and not (job.external_job_id or '').startswith('/job/') and url_key(job.canonical_apply_url) not in proper_id_urls:
            candidates=path_id_history.get(url_key(job.canonical_apply_url),[])
            if len(candidates)>1:raise SourceError('PARSER_ERROR','Ambiguous Workday fallback identity enrichment')
            if candidates:enriched_origin=candidates[0];prior_inventory=enriched_origin
        prior_membership=db.execute('SELECT first_seen_at FROM source_jobs WHERE source_id=? AND identity=?',(source['id'],identity(job))).fetchone()
        legacy_matches = [row for row in legacy if matches_legacy(job, row)]
        known_first=min([stamp]+([prior_inventory['first_seen_at']] if prior_inventory else [])+([prior_membership['first_seen_at']] if prior_membership else [])+[r['first_seen_at'] for r in legacy_matches])
        db.execute('''INSERT INTO source_inventory(source_id,identity,company_id,external_job_id,canonical_url,normalized_payload,first_seen_at,last_seen_at,classification,discovery_kind)
                      VALUES(?,?,?,?,?,?,?,?, '{}',?) ON CONFLICT(source_id,identity) DO UPDATE SET
                      company_id=excluded.company_id,canonical_url=excluded.canonical_url,normalized_payload=excluded.normalized_payload,last_seen_at=excluded.last_seen_at,present=1''',
                   (source['id'],identity(job),source['company_id'],job.external_job_id,job.canonical_apply_url,json.dumps(asdict(job)),known_first,stamp,enriched_origin['discovery_kind'] if enriched_origin else 'INITIAL_DISCOVERY' if inventory_bootstrap else 'NEW_SINCE_PREVIOUS_SUCCESSFUL_INVENTORY'))
        decision = classify(job)
        db.execute('UPDATE source_inventory SET classification=? WHERE source_id=? AND identity=?',(json.dumps(dict(decision,eligibility=eligibility(job))),source['id'],identity(job)))
        if not prior_inventory and not prior_membership and not legacy_matches:
            key='INVENTORY_BASELINE' if inventory_bootstrap else 'INVENTORY_NEW'
            events[key]=events.get(key,0)+1
        old = db.execute("SELECT job_id FROM source_jobs WHERE source_id=? AND identity=?", (source["id"], identity(job))).fetchone()
        if decision["retain"] or old or legacy_matches:
            jid, event = upsert(db, source["company_id"], source["id"], job, stamp,first_seen=prior_inventory['first_seen_at'] if prior_inventory else known_first)
            observed_jid=jid;known_before=event in ('DUPLICATE','REOPENED')
            db.execute('UPDATE source_inventory SET job_id=? WHERE source_id=? AND identity=?',(jid,source['id'],identity(job)))
            if decision['internship_confidence']>=.8 and decision['classification_confidence']>=.7 and decision['function']!='OTHER' and decision['geography_status']!='NON_US' and not any(s in job.title.lower() for s in ('mba','phd')):
                from .universe import observed_recruiter
                observed_recruiter(db,source['company_id'],job,decision,jid,source['id'])
            matching += int(decision["retain"])
            events[event] = events.get(event, 0) + 1
            for row in legacy_matches:
                found_legacy.add(row["id"])
                db.execute("INSERT OR IGNORE INTO job_verifications(job_id,source_id,checked_at,outcome,evidence) VALUES(?,?,?,?,?)",
                           (row["id"], source["id"], stamp, "FOUND", "Authoritative full inventory; matching requisition or dedicated URL"))
                # Usually upsert matched this historical row. Never lose evidence if
                # a source-specific identity selected a different existing duplicate.
                db.execute("UPDATE jobs SET last_seen_at=?,active=1,closed_at=NULL,updated_at=? WHERE id=?", (stamp, stamp, row["id"]))
        observed_kind='HISTORICAL_REVALIDATION' if enriched_origin or legacy_matches or prior_membership or known_before else 'INITIAL_DISCOVERY' if inventory_bootstrap else 'NEW_SINCE_PREVIOUS_SUCCESSFUL_INVENTORY'
        observe(db,census,source,job,stamp,previous,metrics_prior,observed_kind,dict(decision,eligibility=eligibility(job)),observed_jid,known_before)
    for row in legacy:
        if row["id"] not in found_legacy and row["active"]:
            remains = db.execute("SELECT 1 FROM source_jobs WHERE job_id=? AND present=1 AND source_id<>?", (row["id"], source["id"])).fetchone()
            db.execute("INSERT OR IGNORE INTO job_verifications(job_id,source_id,checked_at,outcome,evidence) VALUES(?,?,?,?,?)",
                       (row["id"], source["id"], stamp, "MISSING", "Absent from authoritative complete inventory of its original ATS board"))
            if not remains:
                db.execute("UPDATE jobs SET active=0,closed_at=?,updated_at=? WHERE id=?", (stamp, stamp, row["id"]))
                events["LEGACY_CLOSED"] = events.get("LEGACY_CLOSED", 0) + 1
    # Reconciliation uses ALL returned postings, not only matching internships.
    for row in db.execute("SELECT identity,job_id FROM source_jobs WHERE source_id=? AND present=1", (source["id"],)).fetchall():
        if row["identity"] not in seen:
            db.execute("UPDATE source_jobs SET present=0 WHERE source_id=? AND identity=?", (source["id"], row["identity"]))
            remains = db.execute("SELECT 1 FROM source_jobs WHERE job_id=? AND present=1 LIMIT 1", (row["job_id"],)).fetchone()
            if not remains:
                db.execute("UPDATE jobs SET active=0,closed_at=?,updated_at=? WHERE id=? AND active=1", (stamp, stamp, row["job_id"]))
    for row in db.execute('SELECT identity FROM source_inventory WHERE source_id=? AND present=1',(source['id'],)).fetchall():
        if row['identity'] not in seen:db.execute('UPDATE source_inventory SET present=0 WHERE source_id=? AND identity=?',(source['id'],row['identity']))
    return matching, events


def run_source(db, source, fetcher=fetch, stamp=None):
    source=prepare_source(db,source)
    started = stamp or now()
    count, matching, events, response_status = None, None, {}, None
    authoritative, error = False, None
    try:
        snapshot = fetcher(prepare_source(db, source))
        count, response_status = len(snapshot.jobs), snapshot.response_status
        with db:
            matching, events = apply_snapshot(db, source, snapshot, started)
        authoritative = True
        status = "SUCCESS" if matching else "SUCCESS_NO_MATCHING_JOBS"
    except SourceError as exc:
        status, error = exc.status, str(exc)
        response_status = exc.response_status or response_status
    except (ValueError, TypeError, KeyError, AttributeError) as exc:
        status, error = "PARSER_ERROR", str(exc)
    except Exception as exc:
        status, error = "UNKNOWN_ERROR", str(exc)
        log.exception("Unexpected source failure id=%s", source["id"])
    finished = stamp or now()
    with db:
        db.execute("""INSERT INTO crawl_runs(source_id,started_at,finished_at,status,authoritative,jobs_returned,matching_jobs,response_status,error)
                      VALUES(?,?,?,?,?,?,?,?,?)""", (source["id"], started, finished, status, authoritative, count, matching, response_status, error))
        db.execute("""UPDATE sources SET last_checked_at=?,health_status=?,jobs_returned=?,last_response_status=?,last_error=?,
                      consecutive_failures=CASE WHEN ? THEN 0 ELSE consecutive_failures+1 END,
                      last_successful_check_at=CASE WHEN ? THEN ? ELSE last_successful_check_at END,
                      last_job_seen_at=CASE WHEN ? THEN ? ELSE last_job_seen_at END WHERE id=?""",
                   (started, status, count, response_status, error, authoritative, authoritative, finished,
                    authoritative and bool(count), finished, source["id"]))
        states = db.execute("SELECT s.* FROM sources s JOIN companies c ON c.id=s.company_id WHERE COALESCE(c.canonical_company_id,c.id)=? AND s.active=1", (source["company_id"],)).fetchall()
        all_healthy = all(s["health_status"].startswith("SUCCESS") for s in states)
        db.execute("""UPDATE companies SET last_checked_at=?,health_status=?,updated_at=?,
                      consecutive_failures=CASE WHEN ? THEN 0 ELSE consecutive_failures+1 END,
                      last_successful_check_at=CASE WHEN ? THEN ? ELSE last_successful_check_at END,
                      last_job_seen_at=CASE WHEN ? THEN ? ELSE last_job_seen_at END WHERE id=?""",
                   (started, "SUCCESS" if all_healthy else "DEGRADED", finished, all_healthy, all_healthy, finished,
                    authoritative and bool(count), finished, source["company_id"]))
        if authoritative:
            from .universe import priority
            from .universe_sources import ROOT
            priority(db,source['company_id'],json.loads((ROOT/'config/universe_policy.json').read_text()))
    log.info("source=%s company=%s status=%s returned=%s matching=%s events=%s error=%s",
             source["id"], source["company_id"], status, count, matching, events, error)
    return {"source_id": source["id"], "status": status, "jobs_returned": count, "matching_jobs": matching, "events": events, "error": error}

def consolidate_native_apply_duplicates(db):
    """Same employer and dedicated Workday apply URL + native req proof, across providers."""
    import re
    from urllib.parse import unquote,urlsplit
    groups={};merged=[]
    for row in db.execute('SELECT * FROM jobs WHERE id NOT IN(SELECT duplicate_job_id FROM job_merges)'):
        u=row['canonical_apply_url'] or ''
        if not board_key(u):continue
        m=re.search(r'_([^/]+)/apply$',unquote(urlsplit(u).path))
        if m:groups.setdefault((row['company_id'],url_key(u),row['normalized_title'],m[1]),[]).append(dict(row))
    with db:
        for (_,url,_,req),rows in groups.items():
            if len(rows)<2:continue
            targets=[r for r in rows if r['source_type']=='workday' and r['external_job_id'].casefold()==req.casefold() and db.execute("SELECT 1 FROM source_inventory i JOIN sources s ON s.id=i.source_id WHERE i.job_id=? AND i.present=1 AND s.ats_type='workday' AND s.source_role='AUTHORITATIVE' AND s.health_status LIKE 'SUCCESS%'",(r['id'],)).fetchone()]
            if len(targets)!=1:continue
            canonical=targets[0]
            for other in rows:
                if other['id']==canonical['id'] or other['source_type']=='workday':continue
                db.execute('UPDATE source_jobs SET job_id=? WHERE job_id=?',(canonical['id'],other['id']))
                db.execute('UPDATE source_inventory SET job_id=? WHERE job_id=?',(canonical['id'],other['id']))
                db.execute('UPDATE legacy_records SET job_id=? WHERE job_id=?',(canonical['id'],other['id']))
                db.execute('UPDATE jobs SET first_seen_at=min(first_seen_at,?),last_seen_at=max(last_seen_at,?) WHERE id=?',(other['first_seen_at'],other['last_seen_at'],canonical['id']))
                db.execute('INSERT INTO job_merges VALUES(?,?,?,?)',(other['id'],canonical['id'],now(),'Same employer, title and dedicated Workday apply URL whose requisition matches canonical native ID; cross-provider publication'))
                db.execute('UPDATE jobs SET active=0,closed_at=NULL WHERE id=?',(other['id'],));merged.append(dict(duplicate_job_id=other['id'],canonical_job_id=canonical['id'],apply_url=url))
    return merged
