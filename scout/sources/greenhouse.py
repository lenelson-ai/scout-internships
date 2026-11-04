from .base import get_json, token, rows, required, validate, SourceError
from ..model import Job


def fetch(source, get=get_json):
    board = token(source["ats_identifier"])
    payload = get(f"https://boards-api.greenhouse.io/v1/boards/{board}/jobs?content=true")
    items = rows(payload, "jobs")
    total = payload.get("meta", {}).get("total")
    if total is not None and total != len(items):
        raise SourceError("PARSER_ERROR", "Greenhouse total does not match inventory")
    jobs = []
    for j in items:
        job=Job(title=required(j, "title"), external_job_id=str(required(j, "id")),
                        description=j.get("content", ""), locations=[j.get("location", {}).get("name", "")],
                        department=" / ".join(d["name"] for d in j.get("departments", [])),
                        source_url=required(j, "absolute_url"),
                        # updated_at is NOT a posting date. Public list commonly has no published date.
                        posted_at=j.get("first_published"), posted_at_confidence="EMPLOYER_EXPLICIT")
        from ..model import publication_evidence
        job._publication_evidence=publication_evidence(j.get("first_published"));jobs.append(job)
    return validate(jobs)
