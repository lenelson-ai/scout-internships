"""Only board-scoped employer evidence may verify or close migrated postings."""
import json
from urllib.parse import urlsplit
from .model import canonical_url
from .db import canonical_id
from .sources import detect
from .sources.workday import board_key


def scope(url):
    kind, identifier = detect(url)
    if kind=='icims':
        from .sources.icims import configuration,posting_key
        if not posting_key(url):return None
        try:return 'icims',configuration(dict(careers_url=url))[0]
        except Exception:return None
    if kind == "workday":
        return (kind, board_key(url)) if board_key(url) else None
    if kind in {"greenhouse", "lever", "ashby", "smartrecruiters"} and identifier:
        return kind, identifier.casefold()
    return None


def source_scope(source):
    if source['ats_type']=='icims':
        from .sources.icims import configuration
        try:return 'icims',configuration(source)[0]
        except Exception:return None
    if source["ats_type"] == "workday":
        return scope(source["careers_url"])
    if source["ats_type"] in {"greenhouse", "lever", "ashby", "smartrecruiters"}:
        return source["ats_type"], source["ats_identifier"].casefold()
    return None


def url_key(url):
    if not url:
        return ""
    url = canonical_url(url)
    p = urlsplit(url)
    from .sources.icims import posting_key
    key=posting_key(url)
    if key:return 'icims:'+key[0]+'/'+key[1]
    if scope(url) and detect(url)[0] == "workday" and "/job/" in p.path:
        return repr(board_key(url)) + "/job/" + p.path.split("/job/", 1)[1].removesuffix("/apply")
    return url.removesuffix("/apply").removesuffix("/application")


def scoped_legacy(db, source):
    board = source_scope(source)
    if not board:
        return []
    result = {}
    rows = db.execute("""SELECT j.*,l.payload FROM jobs j JOIN legacy_records l ON j.id=l.job_id
                         WHERE j.company_id=?""", (source["company_id"],)).fetchall()
    for row in rows:
        original = json.loads(row["payload"])
        if scope(original.get("applicationLink", "")) == board:
            item = result.setdefault(row["id"], dict(row, legacy_ids=set(), legacy_urls=set()))
            if original.get("requisitionId"):
                item["legacy_ids"].add(str(original["requisitionId"]).casefold())
            item["legacy_urls"].add(url_key(original.get("applicationLink", "")))
    return list(result.values())


def matches_legacy(job, legacy):
    jid = job.external_job_id.split("|", 1)[0].casefold()
    return (jid in legacy["legacy_ids"] or
            bool({url_key(job.source_url), url_key(job.canonical_apply_url)} & legacy["legacy_urls"]))


def prepare_source(db, source):
    prepared = dict(source)
    prepared['company_id']=canonical_id(db,source['company_id'])
    legacy = scoped_legacy(db, prepared)
    prepared["_legacy_ids"] = [value for row in legacy for value in row["legacy_ids"]]
    # Native Workday requisitions usually have capital letters; matching in the adapter is case insensitive.
    prepared["_legacy_urls"] = [value for row in legacy for value in row["legacy_urls"]]
    bound = db.execute("SELECT external_job_id FROM source_jobs WHERE source_id=?", (source["id"],)).fetchall()
    prepared["_legacy_ids"] += [str(r[0]).casefold() for r in bound if r[0]]
    if prepared['ats_type']=='workday' and db.execute('select 1 from crawl_runs where source_id=? and authoritative=1 limit 1',(source['id'],)).fetchone():
        known={};board=board_key(prepared['careers_url'])
        for r in db.execute('select external_job_id,canonical_url from source_inventory where source_id=?',(source['id'],)):
            if r['external_job_id'] and board and board_key(r['canonical_url'])==board and '/job/' in (r['canonical_url'] or ''):
                known.setdefault(str(r['external_job_id']).casefold(),[]).append(r['canonical_url'])
        prepared['_validated_posting_urls']=known
    return prepared
