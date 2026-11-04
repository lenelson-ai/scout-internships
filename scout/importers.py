import csv
import json
from pathlib import Path
from urllib.parse import urlsplit
from . import db as store
from .ingest import upsert
from .model import Job, date, now, normalize
from .sources import detect


def read_rows(path):
    path = Path(path)
    if path.suffix.lower() == ".json":
        data = json.loads(path.read_text())
        if not isinstance(data, list) or any(not isinstance(x, dict) for x in data):
            raise ValueError("Company JSON must contain an array of objects")
        return data
    with path.open(newline="", encoding="utf-8-sig") as f:
        return list(csv.DictReader(f))


def import_companies(db, path, intervals=None):
    intervals = intervals or {}
    rows = read_rows(path)
    with db:
        for row in rows:
            name = row.get("company_name", "").strip()
            if not name:
                raise ValueError("Every company row requires company_name")
            url = row.get("careers_url") or None
            if url and (urlsplit(url).scheme not in {"https", "http"} or not urlsplit(url).hostname):
                raise ValueError(f"Invalid careers_url for {name}")
            cid = store.company(db, name, **{k: row.get(k) for k in ("domain", "careers_url", "industry", "company_type", "company_size")})
            detected_type, detected_id = detect(url)
            ats = (row.get("ats_type") or detected_type).lower()
            identifier = row.get("ats_identifier") or detected_id
            store.source(db, cid, ats, identifier, url, intervals.get(ats, intervals.get("default", 21600)))
            db.execute("INSERT OR REPLACE INTO company_imports VALUES(?,?,?)", (cid, str(Path(path).resolve()), json.dumps(row)))
    return len(rows)


def migrate_legacy(db, root, intervals=None):
    root = Path(root)
    intervals = intervals or {}
    imported, source_ids = 0, set()
    def add_source(cid, url):
        if not url or not url.startswith(("http://", "https://")) or any(x in url for x in (" ", ";", "\n")):
            return
        ats, identifier = detect(url)
        sid = store.source(db, cid, ats, identifier, url, intervals.get(ats, intervals.get("default", 21600)))
        source_ids.add(sid)

    with db:
        for filename in ("fortune500_2026.csv", "watchlist.csv"):
            path = root / "data" / filename
            if not path.exists():
                continue
            for row in read_rows(path):
                cid = store.company(db, row["Company"], industry=row.get("Industry"), fortune_rank=row.get("Rank"))
                db.execute("INSERT OR REPLACE INTO company_imports VALUES(?,?,?)", (cid, filename, json.dumps(row)))
        audit = root / "data/organization_audit.csv"
        if audit.exists():
            # Retain actual ATS tenant evidence; historical audit claims do not become current health.
            for row in read_rows(audit):
                cid = store.company(db, row["Company"])
                url = row.get("Official Portal URL", "")
                add_source(cid, url)
                if url.startswith("https://") and " " not in url:
                    store.company(db, row["Company"], careers_url=url, domain=urlsplit(url).hostname)
        records = json.loads((root / "data/opportunities.json").read_text())
        for row in records:
            if db.execute("SELECT 1 FROM legacy_records WHERE record_id=?", (row["recordId"],)).fetchone():
                continue
            cid = store.company(db, row["company"])
            add_source(cid, row.get("applicationLink"))
            label = row.get("postingDateLabel", "").lower()
            # The old tracker sometimes used verification/update dates in its posting column.
            posting = row.get("postingDate")
            if any(x in label for x in ("earliest verified", "update date", "unavailable", "not stated", "not inferred", "days ago", "yesterday")):
                posting = None
            job = Job(title=row["internship"], external_job_id=row.get("requisitionId", ""),
                      locations=[row.get("location", "")], season=row.get("period", ""),
                      source_url=row.get("applicationLink", ""), posted_at=posting,
                      posted_at_confidence="LEGACY_EMPLOYER_DATE" if posting else "UNKNOWN", deadline=row.get("deadline"))
            first = date(row.get("firstSeen"))
            last = date(row.get("lastVerified")) or first
            if not first or not last:
                raise ValueError(f"Legacy record {row['recordId']} lacks valid discovery/verification dates")
            jid, _ = upsert(db, cid, None, job, last, first_seen=first,
                            active=row.get("listingStatus") == "Open", preserve_locations=True)
            db.execute("INSERT INTO legacy_records VALUES(?,?,?)", (row["recordId"], jid, json.dumps(row)))
            imported += 1
        for row in db.execute("SELECT id FROM companies WHERE id NOT IN (SELECT company_id FROM sources)").fetchall():
            store.source(db, row[0], "custom", "", interval_seconds=intervals.get("default", 21600))
    return {"legacy_records_imported": imported,
            "companies": db.execute("SELECT COUNT(*) FROM companies").fetchone()[0],
            "jobs": db.execute("SELECT COUNT(*) FROM jobs").fetchone()[0],
            "sources": db.execute("SELECT COUNT(*) FROM sources").fetchone()[0]}
