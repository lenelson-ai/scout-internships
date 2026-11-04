from dataclasses import dataclass
import json
import re
import socket
import ssl
import os
from pathlib import Path
from contextlib import contextmanager
from contextvars import ContextVar
import time
import urllib.error
import urllib.request
from ..model import Job

_deadline = ContextVar("source_deadline", default=None)


@contextmanager
def request_budget(seconds):
    marker = _deadline.set(time.monotonic() + seconds)
    from .transport import SESSION,Session
    pool=Session(_deadline.get());session_token=SESSION.set(pool)
    try:
        yield
    finally:
        pool.close();SESSION.reset(session_token);_deadline.reset(marker)


class SourceError(Exception):
    def __init__(self, status, message, response_status=None, retry_after_seconds=None):
        super().__init__(message)
        self.status = status
        self.response_status = response_status
        self.retry_after_seconds = retry_after_seconds


@dataclass
class Snapshot:
    jobs: list[Job]
    authoritative: bool = True
    response_status: int = 200


def parse_retry_after(value):
    try:return max(0,int(value))
    except (ValueError,TypeError):
        try:
            from email.utils import parsedate_to_datetime
            from datetime import datetime,timezone
            return max(0,int((parsedate_to_datetime(value)-datetime.now(timezone.utc)).total_seconds()))
        except (ValueError,TypeError,OverflowError):return None


_request_cost=ContextVar('source_request_cost',default=None)
@contextmanager
def request_metrics():
    metrics=dict(requests=0,response_bytes=0);token=_request_cost.set(metrics);started=time.monotonic()
    try:yield metrics
    finally:metrics['runtime_seconds']=round(time.monotonic()-started,3);_request_cost.reset(token)
def record_request(response_bytes=None):
    metrics=_request_cost.get()
    if metrics is not None:
        if response_bytes is None:metrics['requests']+=1
        else:metrics['response_bytes']+=response_bytes


def get_json(url, payload=None):
    return _get_json(url,payload,True)

def _get_json(url,payload,dns_retry):
    remaining = _deadline.get() - time.monotonic() if _deadline.get() else 30
    if remaining <= 0:
        raise SourceError("TIMEOUT", "Source audit time budget exhausted; inventory not authoritative")
    headers = {"User-Agent": "InternshipScout/1.0 (public jobs research)", "Accept": "application/json"}
    if payload is not None:
        headers["Content-Type"] = "application/json"
    request = urllib.request.Request(url, data=json.dumps(payload).encode() if payload is not None else None, headers=headers)
    try:
        # Some macOS Python installers ship without a default CA bundle. Use the
        # installed system trust bundle when available; never disable TLS verification.
        from .transport import open_url,tls_context
        context=tls_context()
        record_request()
        with open_url(request, timeout=min(30, remaining), context=context) as response:
            raw = response.read(32 * 1024 * 1024 + 1)
            record_request(len(raw))
            if len(raw) > 32 * 1024 * 1024:
                raise SourceError("PARSER_ERROR", "Response exceeds 32 MiB limit; incomplete snapshot")
            return json.loads(raw)
    except urllib.error.HTTPError as exc:
        status = "BLOCKED" if exc.code in (401, 403, 429) else "ATS_CHANGED" if exc.code in (404, 410) else "HTTP_ERROR"
        retry=parse_retry_after(exc.headers.get('Retry-After','')) if exc.code in (429,502,503,504) and exc.headers else None
        from .transport import SESSION
        pool=SESSION.get()
        if pool:pool.close()
        exc.close()
        delay=retry if retry is not None else .25
        if dns_retry and exc.code in (502,503,504) and delay<=5 and (not _deadline.get() or _deadline.get()-time.monotonic()>delay+.1):
            metrics=_request_cost.get()
            if metrics is not None:metrics['transient_http_retries']=metrics.get('transient_http_retries',0)+1
            time.sleep(delay)
            return _get_json(url,payload,False)
        raise SourceError(status, str(exc), exc.code,retry_after_seconds=retry) from exc
    except (TimeoutError, socket.timeout) as exc:
        raise SourceError("TIMEOUT", str(exc)) from exc
    except urllib.error.URLError as exc:
        if dns_retry and isinstance(exc.reason,socket.gaierror) and (not _deadline.get() or _deadline.get()-time.monotonic()>.2):
            time.sleep(.15)
            return _get_json(url,payload,False)
        status = "TIMEOUT" if isinstance(exc.reason, (TimeoutError, socket.timeout)) else "NETWORK_ERROR"
        raise SourceError(status, str(exc)) from exc
    except OSError as exc:
        raise SourceError("NETWORK_ERROR",str(exc)) from exc
    except (json.JSONDecodeError, UnicodeDecodeError) as exc:
        raise SourceError("PARSER_ERROR", "Response was not valid JSON") from exc


def token(value):
    if not re.fullmatch(r"[A-Za-z0-9_.-]+", value or ""):
        raise SourceError("ATS_CHANGED", "Missing or invalid verified ATS board identifier")
    return value


def rows(payload, key):
    result = payload.get(key) if isinstance(payload, dict) else None
    if not isinstance(result, list) or any(not isinstance(x, dict) for x in result):
        raise SourceError("PARSER_ERROR", f"Expected array of objects: {key}")
    return result


def required(item, key):
    value = item.get(key)
    if value is None or str(value).strip() == "":
        raise SourceError("PARSER_ERROR", f"Missing required posting field: {key}")
    return value


def validate(jobs):
    keys = []
    for job in jobs:
        job.clean()
        if not job.canonical_apply_url or not job.external_job_id:
            raise SourceError("PARSER_ERROR", "Posting missing application URL or stable identity")
        keys.append(job.external_job_id)
    if len(keys) != len(set(keys)):
        raise SourceError("PARSER_ERROR", "Repeated job IDs; pagination may be broken")
    return Snapshot(jobs)


def html_page(url):
    """Budgeted public HTML with the same explicit health errors as JSON calls."""
    remaining = _deadline.get() - time.monotonic() if _deadline.get() else 15
    if remaining <= 0:
        raise SourceError('TIMEOUT','Source audit time budget exhausted')
    from ..career_mapping import get_page
    try:
        return get_page(url,timeout=min(15,remaining))
    except urllib.error.HTTPError as exc:
        raise SourceError('BLOCKED' if exc.code in (401,403,429) else 'ATS_CHANGED' if exc.code in (404,410) else 'HTTP_ERROR',str(exc),exc.code,retry_after_seconds=parse_retry_after(exc.headers.get('Retry-After','')) if exc.code==429 and exc.headers else None) from exc
    except (TimeoutError,socket.timeout) as exc:
        raise SourceError('TIMEOUT',str(exc)) from exc
    except urllib.error.URLError as exc:
        raise SourceError('NETWORK_ERROR',str(exc)) from exc
    except OSError as exc:
        raise SourceError('NETWORK_ERROR',str(exc)) from exc
    except ValueError as exc:
        raise SourceError('PARSER_ERROR',str(exc)) from exc
