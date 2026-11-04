from .base import get_json, token, required, validate, SourceError
from ..model import Job


def fetch(source, get=get_json):
    board = token(source["ats_identifier"])
    jobs = []
    for offset in range(0, 100000, 100):
        data = get(f"https://api.lever.co/v0/postings/{board}?mode=json&limit=100&skip={offset}")
        if not isinstance(data, list) or any(not isinstance(j, dict) for j in data):
            raise SourceError("PARSER_ERROR", "Lever response is not a posting array")
        for j in data:
            cat = j.get("categories", {})
            desc = j.get("descriptionPlain", "") + " " + j.get("additionalPlain", "")
            desc += " ".join(x.get("text", "") + " " + x.get("content", "") for x in j.get("lists", []))
            jobs.append(Job(title=required(j, "text"), external_job_id=str(required(j, "id")),
                            description=desc, locations=cat.get("allLocations") or [cat.get("location", "")],
                            department=" / ".join(filter(None, [cat.get("department"), cat.get("team")])),
                            employment_type=cat.get("commitment", ""), remote_status=j.get("workplaceType", "UNKNOWN"),
                            source_url=required(j, "hostedUrl"), canonical_apply_url=required(j, "applyUrl")))
        if len(data) < 100:
            return validate(jobs)
    raise SourceError("PARSER_ERROR", "Lever pagination limit exceeded")
