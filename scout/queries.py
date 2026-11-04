from datetime import datetime, timedelta, timezone
import json
from zoneinfo import ZoneInfo

WINDOWS = {"24h": 1, "3d": 3, "7d": 7}


def jobs(db, *, category=None, window="7d", active=True, basis="effective", timezone_name="America/New_York", limit=200, offset=0, at=None):
    if category and category not in {"STRATEGY", "FINANCE", "CONSULTING", "OTHER"}:
        raise ValueError("Invalid category")
    if window not in {*WINDOWS, "today", "older", "all"}:
        raise ValueError("Invalid window")
    if basis not in {"effective", "posted", "discovered"}:
        raise ValueError("Invalid date basis")
    at = at or datetime.now(timezone.utc)
    field = {"effective": "CASE WHEN j.posted_at>j.first_seen_at THEN j.posted_at ELSE j.first_seen_at END", "posted": "j.posted_at", "discovered": "j.first_seen_at"}[basis]
    where, args = ["j.id NOT IN (SELECT duplicate_job_id FROM job_merges)"], []
    if category:
        where.append("j.function=?")
        args.append(category)
    if active:
        where.append("j.active=1")
    if window != "all":
        if window == "today":
            boundary = at.astimezone(ZoneInfo(timezone_name)).replace(hour=0, minute=0, second=0, microsecond=0).astimezone(timezone.utc)
        else:
            boundary = at - timedelta(days=WINDOWS.get(window, 7))
        cutoff=boundary.isoformat(timespec='seconds');current=at.isoformat(timespec='seconds')
        if basis=='effective' and window!='older':
            where.append('((j.posted_at>=? AND j.posted_at<=?) OR (j.first_seen_at>=? AND j.first_seen_at<=?))')
            args.extend([cutoff,current,cutoff,current])
        else:
            where.append(field + (" < ?" if window == "older" else " >= ?"));args.append(cutoff)
            if window!='older':where.append(field+' <= ?');args.append(current)
    clause = " AND ".join(where)
    total = db.execute("SELECT COUNT(*) FROM jobs j WHERE " + clause, args).fetchone()[0]
    result = db.execute("""SELECT j.*,c.name company,
                           CASE WHEN j.posted_at IS NULL OR j.first_seen_at>j.posted_at THEN 'DISCOVERED' ELSE 'POSTED' END freshness_basis
                           FROM jobs j JOIN companies c ON j.company_id=c.id WHERE """ + clause +
                        f" ORDER BY {field} DESC,j.id DESC LIMIT ? OFFSET ?", [*args, max(1, min(1000, int(limit))), max(0, int(offset))]).fetchall()
    items = []
    for row in result:
        item = dict(row)
        for key in ("locations", "countries", "classification_reasons"):
            item[key] = json.loads(item[key])
        posted=datetime.fromisoformat(item['posted_at']) if item['posted_at'] else None
        item['employer_posted_recently']=bool(posted and at-timedelta(days=7)<=posted<=at)
        item['freshness_labels']=['EMPLOYER_POSTED_RECENTLY'] if item['employer_posted_recently'] else []
        items.append(item)
    return {"total": total, "jobs": items}


def health(db):
    return [dict(r) for r in db.execute("""SELECT s.*,c.name company,
               (SELECT COUNT(*) FROM jobs j WHERE j.company_id=c.id AND j.source_type='legacy') legacy_jobs_unverified
               FROM sources s JOIN companies c ON s.company_id=c.id ORDER BY c.name,s.id""")]
