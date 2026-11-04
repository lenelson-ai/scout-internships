from .base import get_json, token, rows, required, validate, SourceError
from ..model import Job
from ..classify import classify


def fetch(source, get=get_json):
    board = token(source["ats_identifier"])
    jobs, expected = [], None
    root = f"https://api.smartrecruiters.com/v1/companies/{board}/postings"
    for offset in range(0, 100000, 100):
        data = get(f"{root}?limit=100&offset={offset}")
        items = rows(data, "content")
        total = data.get("totalFound")
        if not isinstance(total, int) or total < 0 or (expected is not None and total != expected):
            raise SourceError("PARSER_ERROR", "Missing or changing SmartRecruiters total")
        expected = total
        for j in items:
            jid = str(required(j, "id"))
            loc = j.get("location", {})
            job = Job(title=required(j, "name"), external_job_id=jid,
                      locations=[", ".join(filter(None, [loc.get("city"), loc.get("region"), loc.get("country")]))],
                      countries=[loc.get("country", "")], department=j.get("department", {}).get("label", ""),
                      employment_type=j.get("typeOfEmployment", {}).get("label", ""),
                      remote_status="REMOTE" if loc.get("remote") else "UNKNOWN",
                      posted_at=j.get("releasedDate"), posted_at_confidence="EMPLOYER_EXPLICIT",
                      source_url=f"https://jobs.smartrecruiters.com/{board}/{jid}")
            # Descriptions are only exposed in detail responses; avoid fetching experienced roles.
            if classify(job)["internship_confidence"] >= .4:
                detail = get(f"{root}/{token(jid)}")
                if str(required(detail, "id")) != jid:
                    raise SourceError("PARSER_ERROR", "SmartRecruiters detail identity mismatch")
                sections = detail.get("jobAd", {}).get("sections", {})
                job.description = " ".join(s.get("text", "") for s in sections.values() if isinstance(s, dict))
                job.canonical_apply_url = detail.get("applyUrl") or job.source_url
            jobs.append(job)
        if len(jobs) == total:
            return validate(jobs)
        if not items or len(jobs) > total:
            raise SourceError("PARSER_ERROR", "Incomplete SmartRecruiters pagination")
    raise SourceError("PARSER_ERROR", "SmartRecruiters pagination limit exceeded")
