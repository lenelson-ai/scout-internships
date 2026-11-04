from urllib.parse import urlsplit
from .base import get_json, token, rows, required, validate
from ..model import Job


def fetch(source, get=get_json):
    board = token(source["ats_identifier"])
    items = rows(get(f"https://api.ashbyhq.com/posting-api/job-board/{board}"), "jobs")
    jobs = []
    for j in items:
        if j.get("isListed") is False:
            continue
        locations = [j.get("location", "")] + [x.get("location", "") for x in j.get("secondaryLocations", [])]
        countries = [j.get("address", {}).get("postalAddress", {}).get("addressCountry", "")]
        countries += [x.get("address", {}).get("addressCountry", "") for x in j.get("secondaryLocations", [])]
        url = required(j, "jobUrl")
        # Ashby public payload documents jobUrl rather than an explicit posting ID.
        external_id = j.get("id") or urlsplit(url).path.rstrip("/").split("/")[-1]
        jobs.append(Job(title=required(j, "title"), external_job_id=external_id,
                        description=j.get("descriptionPlain") or j.get("descriptionHtml", ""),
                        locations=locations, countries=countries,
                        department=" / ".join(filter(None, [j.get("department"), j.get("team")])),
                        remote_status=j.get("workplaceType") or ("REMOTE" if j.get("isRemote") else "UNKNOWN"),
                        employment_type=j.get("employmentType", ""), source_url=url,
                        canonical_apply_url=required(j, "applyUrl"), posted_at=j.get("publishedAt"),
                        posted_at_confidence="EMPLOYER_EXPLICIT"))
    return validate(jobs)
