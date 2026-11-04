"""Public Workday CXS boards. No keyword/country facets restrict the inventory."""
import re
from urllib.parse import urlsplit, unquote
from .base import get_json, rows, required, SourceError, Snapshot
from ..model import Job, date, canonical_url

LOCALE = re.compile(r"[a-z]{2}[-_][a-z]{2}", re.I)
HOST = re.compile(r"([a-z0-9-]+)\.wd[0-9]+\.myworkdayjobs\.com", re.I)


def configuration(source):
    url = urlsplit(source.get("careers_url") or "")
    if url.scheme!='https':raise SourceError('ATS_CHANGED','Workday requires HTTPS')
    host = url.hostname or ""
    match = HOST.fullmatch(host)
    if not match and re.fullmatch(r'wd[0-9]+\.myworkdaysite\.com',host,re.I):
        siteparts=[unquote(p) for p in url.path.split('/') if p]
        if 'recruiting' not in siteparts:raise SourceError('ATS_CHANGED','Workdaysite URL lacks recruiting tenant and board')
        pos=siteparts.index('recruiting')
        if len(siteparts)<pos+3:raise SourceError('ATS_CHANGED','Incomplete Workdaysite board')
        tenant,board=siteparts[pos+1:pos+3]
        if not re.fullmatch(r'[A-Za-z0-9_-]+',tenant+board):raise SourceError('ATS_CHANGED','Invalid Workdaysite identity')
        return f'https://{tenant}.{host.split(".")[0]}.myworkdayjobs.com',tenant,board
    if url.scheme != "https" or not match:
        raise SourceError("ATS_CHANGED", "Workday requires a verified tenant.wdN.myworkdayjobs.com HTTPS URL")
    tenant = match.group(1)
    parts = [unquote(p) for p in url.path.split("/") if p]
    if parts[:2] == ["wday", "cxs"] and len(parts) >= 4:
        if parts[2].lower() != tenant.lower():
            raise SourceError("ATS_CHANGED", "Workday URL tenant mismatch")
        board = parts[3]
    else:
        if parts and LOCALE.fullmatch(parts[0]):
            parts.pop(0)
        board = parts[0] if parts else ""
    if not re.fullmatch(r"[A-Za-z0-9_-]+", board) or board in {"job", "apply"}:
        raise SourceError("ATS_CHANGED", "Workday careers URL has no unambiguous board name")
    return f"https://{host}", tenant, board


def board_key(url):
    try:
        root, tenant, board = configuration({"careers_url": url})
        return root.lower(), tenant.lower(), board.lower()
    except SourceError:
        return None


def posting_path(value):
    if not isinstance(value, str) or not value.startswith("/job/") or ".." in unquote(value) or "?" in value or "#" in value:
        raise SourceError("PARSER_ERROR", "Invalid Workday externalPath")
    return value.rstrip("/")


def req_id(item):
    value = item.get("jobReqId")
    if value:
        return str(value)
    bullets = item.get("bulletFields") or []
    if len(bullets) == 1 and isinstance(bullets[0], str):
        return bullets[0]
    # A path identity remains unique even when a tenant omits requisition metadata.
    return posting_path(required(item, "externalPath"))


def normalize_posting(item, source, detail=None):
    root, tenant, board = configuration(source)
    path = posting_path(required(item, "externalPath"))
    detail = detail or {}
    info = detail.get("jobPostingInfo", {})
    if not isinstance(info, dict):
        raise SourceError("PARSER_ERROR", "Invalid Workday jobPostingInfo")
    original=urlsplit(source.get("careers_url") or "")
    url = (f"https://{original.hostname}/en-US/recruiting/{tenant}/{board}{path}" if (original.hostname or "").endswith(".myworkdaysite.com") else f"{root}/en-US/{board}{path}")
    external = info.get("externalUrl")
    if external:
        # A detail response for another tenant/board cannot prove current membership.
        if board_key(external) != board_key(url):
            raise SourceError("PARSER_ERROR", "Workday detail changed tenant or board")
        url = canonical_url(external)
    locations = [info.get("location")] + (info.get("additionalLocations") or [])
    locations = [v.get("descriptor", "") if isinstance(v, dict) else v for v in locations if v]
    if not locations:
        locations = [item.get("locationsText", "")]
    country = info.get("country") or {}
    requisition_location = info.get("jobRequisitionLocation") or {}
    countries = [requisition_location.get("country", {}).get("alpha2Code") or country.get("descriptor", "")]
    # startDate is CXS's published-on date on tested external boards. Never use
    # postedOn ('Posted Today', etc.), endDate, or requisition's employment start.
    published = info.get("datePosted") or info.get("postedDate") or info.get("startDate")
    explicit = date(published)
    job = Job(title=info.get("title") or required(item, "title"),
               external_job_id=str(info.get("jobReqId") or req_id(item)),
               description=info.get("jobDescription", ""), locations=locations, countries=countries,
               department=info.get("jobFamilyGroup", "") if isinstance(info.get("jobFamilyGroup", ""), str) else "",
               employment_type=info.get("timeType") or info.get("workerSubType", ""),
               remote_status=info.get("remoteType", "UNKNOWN"),
               source_url=url, canonical_apply_url=url + ("" if url.endswith("/apply") else "/apply"),
               posted_at=explicit, posted_at_confidence="EMPLOYER_EXPLICIT" if explicit else "UNKNOWN").clean()
    from ..model import publication_evidence
    job._publication_evidence=publication_evidence(published)
    return job


def fetch(source, get=get_json):
    from ..reconcile import url_key
    source = dict(source)
    root, tenant, board = configuration(source)
    original=urlsplit(source.get("careers_url") or "")
    endpoint_root=f"https://{original.hostname}" if (original.hostname or "").endswith(".myworkdaysite.com") else root
    endpoint = f"{endpoint_root}/wday/cxs/{tenant}/{board}"
    from .workday_inventory import enumerate_inventory
    # Opaque CXS rows sometimes contain only a requisition bullet. Resolve them
    # through the same board's published search, requiring one exact ID match;
    # never invent a posting slug or discard the row from the census.
    detail_cache={};recovered={};recovery_evidence=[]
    def detail_for(path):
        if path not in detail_cache:detail_cache[path]=get(endpoint+posting_path(path))
        return detail_cache[path]
    def historical_match(requisition):
        # History supplies candidate URLs, never current membership or availability.
        # The row itself is in today's exhaustive inventory; live detail supplies
        # its exact title/path, publication and positive application availability.
        candidates=set()
        for u in source.get('_validated_posting_urls',{}).get(requisition.casefold(),[]):
            if board_key(u)!=board_key(source['careers_url']) or '/job/' not in urlsplit(u).path:continue
            candidates.add(posting_path('/job/'+urlsplit(u).path.split('/job/',1)[1].removesuffix('/apply')))
        if not candidates or len(candidates)>5:return None
        matches=[]
        for path in sorted(candidates):
            try:d=detail_for(path)
            except SourceError as exc:
                if exc.status=='ATS_CHANGED':continue
                raise
            info=d.get('jobPostingInfo',{}) if isinstance(d,dict) else {}
            if str(info.get('jobReqId','')).casefold()!=requisition.casefold() or info.get('posted') is not True or info.get('canApply') is not True or not info.get('title') or not info.get('jobDescription'):continue
            published=info.get('externalUrl')
            if published and (board_key(published)!=board_key(source['careers_url']) or '/job/' not in urlsplit(published).path):raise SourceError('PARSER_ERROR','Historical Workday detail changed publication')
            actual=posting_path('/job/'+urlsplit(published).path.split('/job/',1)[1].removesuffix('/apply')) if published and '/job/' in urlsplit(published).path else path
            item=dict(externalPath=actual,jobReqId=info['jobReqId'],title=info['title'])
            normalize_posting(item,source,d)
            detail_cache[actual]=d;matches.append((actual,item,d))
        unique={x[0]:x for x in matches}
        if len(unique)!=1:return None
        path,item,d=next(iter(unique.values()))
        import hashlib,json
        recovery_evidence.append(dict(requisition=requisition,observed_candidate_paths=sorted(candidates),current_path=path,same_publication=True,posted=True,canApply=True,detail_sha256=hashlib.sha256(json.dumps(d,sort_keys=True).encode()).hexdigest()))
        return item
    def inventory_get(url, payload=None):
        page=get(url,payload) if payload is not None else get(url)
        if url.endswith('/jobs') and isinstance(page,dict):
            for item in page.get('jobPostings',[]):
                if not item.get('externalPath'):
                    opaque_id=req_id(item)
                    if opaque_id.startswith('/job/'):
                        raise SourceError('PARSER_ERROR','Opaque Workday row has no exact requisition identity')
                    if opaque_id.casefold() in recovered:
                        item.update(recovered[opaque_id.casefold()]);continue
                    lookup=get(endpoint+'/jobs',dict(appliedFacets={},limit=20,offset=0,searchText=opaque_id))
                    lookup_rows=rows(lookup,'jobPostings')
                    if type(lookup.get('total')) is not int or lookup['total']!=len(lookup_rows) or not 0<=lookup['total']<=20:
                        raise SourceError('PARSER_ERROR','Opaque Workday requisition search is not an exhaustive bounded result')
                    matches=[r for r in lookup_rows if r.get('externalPath') and req_id(r).casefold()==opaque_id.casefold()]
                    if len(matches)>1:raise SourceError('PARSER_ERROR','Ambiguous exact Workday requisition search')
                    match=matches[0] if matches else historical_match(opaque_id)
                    if not match:raise SourceError('PARSER_ERROR','Opaque Workday requisition search lacks one exact published match; no live-validated historical identity')
                    posting_path(match['externalPath']);item.update(match);
                    if match.get('title'):recovered[opaque_id.casefold()]=dict(match)
                if item.get('title'):continue
                path=posting_path(required(item,'externalPath'))
                detail=detail_for(path)
                info=detail.get('jobPostingInfo') if isinstance(detail,dict) else None
                if not isinstance(info,dict) or not info.get('title'):
                    raise SourceError('PARSER_ERROR','Opaque Workday listing has no validated detail title')
                expected=req_id(item)
                if info.get('jobReqId') and not expected.startswith('/job/') and str(info['jobReqId']).casefold()!=expected.casefold():
                    raise SourceError('PARSER_ERROR','Opaque Workday detail requisition mismatch')
                if info.get('posted') is False or info.get('canApply') is False:
                    raise SourceError('PARSER_ERROR','Opaque Workday listing is unavailable in detail')
                normalize_posting(item,source,detail) # includes board identity validation
                item['title']=info['title']
        return page
    items=enumerate_inventory(endpoint,inventory_get)
    jobs = []
    from .detail_policy import needs_details
    legacy_ids = set(source.get("_legacy_ids", []))
    legacy_urls = set(source.get("_legacy_urls", []))
    for item in items:
        listing = normalize_posting(item, source)
        # Inventory is complete and unfiltered. Hydrate locally eligible candidates
        # and tracked identities; detail_mode=all remains available for inspection.
        hydrate = (needs_details(listing,source)
                   or listing.external_job_id.casefold() in legacy_ids or url_key(listing.source_url) in legacy_urls)
        if hydrate:
            detail = detail_for(posting_path(item["externalPath"]))
            if not isinstance(detail, dict) or not isinstance(detail.get("jobPostingInfo"), dict):
                raise SourceError("PARSER_ERROR", "Workday detail response missing jobPostingInfo")
            info = detail["jobPostingInfo"]
            expected_id = req_id(item)
            if info.get('jobReqId') and not expected_id.startswith('/job/') and str(info['jobReqId']).casefold() != expected_id.casefold():
                raise SourceError("PARSER_ERROR", "Workday detail requisition does not match listing")
            if info.get("posted") is False or info.get("canApply") is False:
                raise SourceError("PARSER_ERROR", "Workday listing/detail disagree on availability; retry full inventory")
            listing = normalize_posting(item, source, detail)
            if not listing.description:
                raise SourceError("PARSER_ERROR", "Workday candidate detail has no description")
        jobs.append(listing)
    # Multiple advertisements sharing one requisition need a separate posting-ID
    # model. Fail visibly rather than merge distinct ads or create unstable identities
    # when one ad disappears on a later run.
    from collections import Counter
    counts = Counter(j.external_job_id for j in jobs)
    if any(count > 1 for count in counts.values()):
        raise SourceError("PARSER_ERROR", "Multiple Workday postings share one requisition; separate advertisement identities require review")
    snapshot=Snapshot(jobs)
    snapshot.inventory_evidence=dict(provider="workday",unique_requisitions=len(jobs),historical_identity_recoveries=recovery_evidence)
    return snapshot
