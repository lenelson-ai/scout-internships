import argparse
from contextlib import contextmanager
from datetime import datetime, timezone, timedelta
import fcntl
import json
import logging
from pathlib import Path
import time
from .db import connect, DEFAULT_DB
from .importers import import_companies, migrate_legacy
from .ingest import run_source
from .queries import jobs, health
from .web import serve

ROOT = Path(__file__).resolve().parents[1]


@contextmanager
def locked(path):
    lock_path = Path(str(Path(path).resolve()) + ".lock")
    with lock_path.open("a") as handle:
        try:
            fcntl.flock(handle, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError as exc:
            raise RuntimeError("Another importer/crawler holds the database run lock") from exc
        try:
            yield
        finally:
            fcntl.flock(handle, fcntl.LOCK_UN)


def run(db, args, due_only=False):
    where, params = ["s.active=1", "owner.active=1"], []
    if args.company:
        from .entities import resolve
        from .db import canonical_id
        cid=canonical_id(db,int(args.company)) if args.company.isdigit() else resolve(db,args.company)[0]
        if not cid:raise ValueError('Company name/alias did not resolve uniquely')
        where.append('owner.id=?');params.append(cid)
    if args.source:
        where.append("s.ats_type=?")
        params.append(args.source.lower())
    if args.source_id:
        where.append("s.id=?")
        params.append(args.source_id)
    sources = db.execute("SELECT s.* FROM sources s JOIN companies c ON s.company_id=c.id JOIN companies owner ON owner.id=COALESCE(c.canonical_company_id,c.id) WHERE " + " AND ".join(where) + " ORDER BY owner.coverage_tier,owner.must_cover DESC,s.id", params).fetchall()
    if not sources and (args.company or args.source or args.source_id):
        raise ValueError("No active source matched the requested company/source")
    failed = False
    for source in sources:
        if due_only and source["last_checked_at"]:
            next_check = datetime.fromisoformat(source["last_checked_at"]) + timedelta(seconds=source["interval_seconds"])
            if next_check > datetime.now(timezone.utc):
                continue
        result = run_source(db, source)
        print(json.dumps(result), flush=True)
        failed = failed or not result["status"].startswith("SUCCESS")
    return int(failed)


def main():
    parser = argparse.ArgumentParser(description="Neutral internship ingestion and internal inspection")
    parser.add_argument("--db", default=str(DEFAULT_DB))
    parser.add_argument("--config", default=str(ROOT / "config/ingestion.json"))
    sub = parser.add_subparsers(dest="command", required=True)
    sub.add_parser("init")
    sub.add_parser("migrate-legacy")
    imp = sub.add_parser("import-companies")
    imp.add_argument("path")
    for command in ("crawl", "schedule"):
        crawl = sub.add_parser(command)
        crawl.add_argument("--company", help="Exact company name or numeric ID")
        crawl.add_argument("--source", help="ATS type, e.g. greenhouse")
        crawl.add_argument("--source-id", type=int)
        if command == "crawl":
            crawl.add_argument("--due", action="store_true")
        else:
            crawl.add_argument("--once", action="store_true", help="Run due sources once and exit")
    view = sub.add_parser("serve")
    view.add_argument("--port", type=int, default=8000)
    query = sub.add_parser("jobs")
    query.add_argument("--category")
    query.add_argument("--window", default="7d", choices=["24h", "3d", "7d", "today", "older", "all"])
    query.add_argument("--basis", default="effective", choices=["effective", "posted", "discovered"])
    query.add_argument("--include-inactive", action="store_true")
    feed_cmd=sub.add_parser("feed-export");feed_cmd.add_argument("--window",choices=["today","7d"],default="today");feed_cmd.add_argument("--output",required=True)
    sub.add_parser("health")
    audit = sub.add_parser("audit-coverage")
    audit.add_argument("--output", default=str(ROOT / "reports/coverage"))
    audit.add_argument("--workers", type=int, default=6)
    audit.add_argument("--budget-seconds", type=int, default=180)
    audit.add_argument("--audit-id", type=int, help="Continue an existing audit baseline")
    sub.add_parser("coverage")
    sub.add_parser('universe-fetch')
    sub.add_parser('universe-build')
    mapping=sub.add_parser('universe-map');mapping.add_argument('--workers',type=int,default=6);mapping.add_argument('--pending',action='store_true')
    universe=sub.add_parser('universe-report');universe.add_argument('--output',default=str(ROOT/'reports/universe'))
    sub.add_parser('must-cover-qa')
    external=sub.add_parser('universe-import');external.add_argument('dataset_id');external.add_argument('path')
    manual=sub.add_parser('priority-override');manual.add_argument('--company',required=True);manual.add_argument('--tier',type=int,required=True,choices=[1,2,3]);manual.add_argument('--reason',required=True);manual.add_argument('--best-effort',action='store_true')
    bench=sub.add_parser('benchmark-import');bench.add_argument('path')
    bench=sub.add_parser('benchmark-evaluate');bench.add_argument('--window-days',type=int,default=7)
    review=sub.add_parser('entity-resolve');review.add_argument('--raw-id',type=int,required=True);review.add_argument('--company',required=True);review.add_argument('--reason',required=True);review.add_argument('--evidence-url',required=True)
    args = parser.parse_args()
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    settings = json.loads(Path(args.config).read_text())
    db = connect(args.db)
    try:
        if args.command == "serve":
            db.close()
            serve(args.db, args.port)
        elif args.command == "jobs":
            print(json.dumps(jobs(db, category=args.category, window=args.window, active=not args.include_inactive, basis=args.basis), indent=2))
        elif args.command == "feed-export":
            from .feed import export
            result=export(db,args.output,args.window);print(json.dumps(dict(total=result["total"],output=args.output)))
        elif args.command == "health":
            print(json.dumps(health(db), indent=2))
        elif args.command == "coverage":
            from .coverage import summary
            from .universe_qa import health_report
            print(json.dumps(health_report(db), indent=2))
        elif args.command == 'must-cover-qa':
            from .universe_qa import health_report
            report=health_report(db)
            print(json.dumps(dict(tier_metrics=report['tier_metrics'],critical_failures=report['critical_failures'],missing_required_anchors=report['missing_required_anchors']),indent=2))
            return int(bool(report['critical_failures'] or report['missing_required_anchors']))
        else:
            with locked(args.db):
                if args.command == "init":
                    print(f"Initialized {args.db}")
                elif args.command == 'universe-fetch':
                    from .universe_sources import fetch_datasets
                    fetch_datasets();print('Public snapshots fetched; no employers added until universe-build')
                elif args.command == 'universe-build':
                    from .universe import build
                    print(json.dumps(build(db),indent=2))
                elif args.command == 'universe-map':
                    from .career_mapping import run_mapping
                    print(json.dumps(run_mapping(db,workers=args.workers,pending_only=args.pending),indent=2))
                elif args.command == 'universe-report':
                    from .universe_qa import write_manifest
                    with db:report=write_manifest(db,args.output)
                    print(json.dumps({k:report[k] for k in ['total_canonical_employers','total_raw_source_entities','tier_metrics','unresolved_entity_cases','manifest_sha256']},indent=2))
                elif args.command == 'universe-import':
                    from .universe import catalog,import_rows,snapshot,score_all
                    path=Path(args.path)
                    if path.suffix.lower()=='.csv':
                        import csv
                        with path.open() as handle:rows=list(csv.DictReader(handle))
                        for row in rows:row['metadata']=json.loads(row.get('metadata') or '{}')
                    else:
                        data=json.loads(path.read_text());rows=data.get('rows',[]) if isinstance(data,dict) else data
                    with db:
                        catalog(db);result=import_rows(db,args.dataset_id,rows,snapshot(db,args.dataset_id,path));score_all(db)
                    print(json.dumps(result,indent=2))
                elif args.command == 'priority-override':
                    from .entities import resolve
                    from .universe import override,score_all
                    cid,_,_=resolve(db,args.company)
                    if not cid:raise ValueError('Employer not found uniquely')
                    with db:override(db,cid,args.tier,not args.best_effort,args.reason);score_all(db)
                elif args.command=='benchmark-import':
                    from .benchmark import import_benchmarks
                    with db:count=import_benchmarks(db,json.loads(Path(args.path).read_text()))
                    print(json.dumps(dict(benchmarks_imported=count)))
                elif args.command=='benchmark-evaluate':
                    from .benchmark import evaluate
                    with db:report=evaluate(db,args.window_days)
                    print(json.dumps(report,indent=2))
                elif args.command=='entity-resolve':
                    from .entities import resolve
                    from .universe import attach,score_all
                    from .model import now,canonical_url
                    cid,_,_=resolve(db,args.company)
                    raw=db.execute('SELECT * FROM raw_organizations WHERE id=?',(args.raw_id,)).fetchone()
                    if not cid or not raw or not args.reason.strip():raise ValueError('Unique canonical employer, raw entity and nonempty reason required')
                    url=canonical_url(args.evidence_url)
                    with db:
                        row=dict(name=raw['name'],domain=raw['domain'],parent_name=raw['parent_name'],metadata=json.loads(raw['metadata']),source_url=url)
                        attach(db,args.raw_id,cid,args.reason,row)
                        import uuid
                        db.execute('INSERT INTO entity_resolution_cases(raw_organization_id,case_key,candidate_company_ids,reason,status,created_at,resolution) VALUES(?,?,?,?,?,?,?)',
                                   (args.raw_id,'manual:'+uuid.uuid4().hex,json.dumps([cid]),args.reason,'RESOLVED',now(),json.dumps(dict(evidence_url=url,previous_company_id=raw['company_id'],company_id=cid))))
                        score_all(db)
                elif args.command == "migrate-legacy":
                    print(json.dumps(migrate_legacy(db, ROOT, settings["interval_seconds"]), indent=2))
                elif args.command == "import-companies":
                    print(json.dumps({"rows_imported": import_companies(db, args.path, settings["interval_seconds"])}))
                elif args.command == "crawl":
                    return run(db, args, args.due)
                elif args.command == "audit-coverage":
                    from .coverage import run_audit
                    report = run_audit(db, args.output, args.workers, args.budget_seconds, args.audit_id)
                    print(json.dumps(report['metrics'], indent=2))
                elif args.command == "schedule":
                    while True:
                        result = run(db, args, True)
                        if args.once:
                            return result
                        time.sleep(min(60, max(1, settings.get("scheduler_poll_seconds", 60))))
    except (ValueError, RuntimeError) as exc:
        parser.exit(2, str(exc) + "\n")
    except KeyboardInterrupt:
        pass
    finally:
        db.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
