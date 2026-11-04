from dataclasses import dataclass, field
from datetime import datetime, timezone
from html import unescape
from html.parser import HTMLParser
import re
import unicodedata
from urllib.parse import urlsplit, urlunsplit, parse_qsl, urlencode


def now():
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


class _Text(HTMLParser):
    def __init__(self):
        super().__init__()
        self.parts = []

    def handle_data(self, data):
        self.parts.append(data)


def text(value):
    parser = _Text()
    parser.feed(unescape(str(value or "")))
    parser.close()
    return re.sub(r"\s+", " ", " ".join(parser.parts)).strip()


def normalize(value):
    return re.sub(r"[^\w]+", " ", unicodedata.normalize("NFKC", text(value)).casefold()).strip()


def date(value):
    """Only explicit ISO calendar dates/timestamps. Never convert relative dates."""
    if not value:
        return None
    try:
        result = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
        return result.replace(tzinfo=result.tzinfo or timezone.utc).astimezone(timezone.utc).isoformat(timespec="seconds")
    except (ValueError, TypeError):
        return None


def canonical_url(value):
    if not value:
        return ""
    p = urlsplit(value)
    if p.scheme not in ("https", "http") or not p.hostname or p.username:
        raise ValueError("Job URLs must be public HTTP(S) URLs")
    # Preserve identity-bearing query parameters (jobId, gh_jid, requisition, etc.).
    query = sorted((k, v) for k, v in parse_qsl(p.query, keep_blank_values=True)
                   if not k.lower().startswith("utm_") and k.lower() not in {"source", "ref", "referrer", "trackingid"})
    return urlunsplit((p.scheme.lower(), p.netloc.lower(), p.path.rstrip("/"), urlencode(query), ""))


@dataclass
class Job:
    title: str
    external_job_id: str = ""
    description: str = ""
    locations: list[str] = field(default_factory=list)
    countries: list[str] = field(default_factory=list)
    department: str = ""
    remote_status: str = "UNKNOWN"
    employment_type: str = ""
    season: str = ""
    graduation_year_eligibility: str | None = None
    source_url: str = ""
    canonical_apply_url: str = ""
    posted_at: str | None = None
    posted_at_confidence: str = "UNKNOWN"
    deadline: str | None = None

    def clean(self):
        self.title = text(self.title)
        if not self.title:
            raise ValueError("Missing job title")
        self.external_job_id = str(self.external_job_id or "")
        self.description = text(self.description)
        self.department = text(self.department)
        self.locations = sorted(set(text(x) for x in self.locations if text(x)))
        self.countries = sorted(set(str(x).upper() for x in self.countries if x))
        self.source_url = canonical_url(self.source_url)
        self.canonical_apply_url = canonical_url(self.canonical_apply_url or self.source_url)
        self.posted_at = date(self.posted_at)
        self.deadline = date(self.deadline)
        if not self.posted_at:
            self.posted_at_confidence = "UNKNOWN"
        if not self.season:
            m = re.search(r"\b(summer|spring|fall|winter)\s+20\d{2}\b", self.title, re.I)
            self.season = m.group(0) if m else ""
        return self


def publication_evidence(value):
    """Evidence precision before normalization; absent timezone is never assumed."""
    raw=str(value) if value is not None else None
    result=dict(raw=raw,precision='UNKNOWN',timezone_verified=False)
    if not raw:return result
    if re.fullmatch(r'\d{4}-\d{2}-\d{2}',raw):result['precision']='DATE';return result
    try:
        parsed=datetime.fromisoformat(raw.replace('Z','+00:00'))
        if 'T' in raw or ' ' in raw:
            result['precision']='TIMESTAMP' if parsed.tzinfo else 'TIMESTAMP_TIMEZONE_UNKNOWN';result['timezone_verified']=bool(parsed.tzinfo)
    except ValueError:pass
    return result
