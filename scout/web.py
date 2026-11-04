"""Read-only, loopback-only internal inspector. No frontend framework."""
from html import escape
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
from urllib.parse import parse_qs, urlencode, urlsplit
from .db import connect
from .queries import jobs, health
from .coverage import summary
from .universe_qa import health_report


def serve(path, port=8000):
    class Handler(BaseHTTPRequestHandler):
        def do_GET(self):
            parsed = urlsplit(self.path)
            params = {k: v[-1] for k, v in parse_qs(parsed.query).items()}
            db = connect(path)
            try:
                if parsed.path in ("/", "/jobs", "/api/jobs"):
                    options = dict(category=params.get("category") or None, window=params.get("window", "7d"),
                                   active=params.get("active", "1") != "0", basis=params.get("basis", "effective"),
                                   limit=int(params.get("limit", "200")), offset=int(params.get("offset", "0")))
                    data = jobs(db, **options)
                    if parsed.path == "/api/jobs":
                        return self.reply(200, json.dumps(data), "application/json")
                    columns = [("company", "Company"), ("title", "Title"), ("function", "Category"),
                               ("sub_function", "Subcategory"), ("locations", "Location"), ("posted_at", "Posted At"),
                               ("first_seen_at", "First Seen At"), ("last_seen_at", "Last Seen At"),
                               ("active", "Active"), ("source_type", "Source"), ("canonical_apply_url", "Apply URL"),
                               ("freshness_basis", "Freshness basis"), ("needs_review", "Needs review")]
                    controls = '<form><label>Category <select name="category">'
                    for value in ("", "STRATEGY", "FINANCE", "CONSULTING", "OTHER"):
                        controls += f'<option value="{value}" {"selected" if value == (options["category"] or "") else ""}>{value or "All"}</option>'
                    controls += '</select></label> <label>Window <select name="window">'
                    for value in ("24h", "3d", "7d", "today", "older", "all"):
                        controls += f'<option {"selected" if value == options["window"] else ""}>{value}</option>'
                    controls += '</select></label> <label>Date basis <select name="basis">'
                    for value in ("effective", "posted", "discovered"):
                        controls += f'<option {"selected" if value == options["basis"] else ""}>{value}</option>'
                    controls += '</select></label> <label>Status <select name="active">'
                    for value, label in (("1", "Active only"), ("0", "All historical jobs")):
                        controls += f'<option value="{value}" {"selected" if options["active"] == (value == "1") else ""}>{label}</option>'
                    controls += '</select></label> <button>Filter</button></form>'
                    body = controls + f'<p>{data["total"]} jobs; showing up to 200. Effective windows include recent employer posting dates or recent Scout discovery dates; both dates are shown separately. Historical imports are unverified until recrawled.</p>'
                    body += table(data["jobs"], columns)
                    if options["offset"] + len(data["jobs"]) < data["total"]:
                        nxt = dict(params, offset=str(options["offset"] + len(data["jobs"])))
                        body += f'<p><a href="/jobs?{escape(urlencode(nxt), quote=True)}">Next page</a></p>'
                elif parsed.path == "/api/feed":
                    from .feed import feed
                    return self.reply(200,json.dumps(feed(db,params.get("window","today"))),"application/json")
                elif parsed.path == "/api/coverage":
                    return self.reply(200, json.dumps(health_report(db)), "application/json")
                elif parsed.path=='/api/universe':
                    return self.reply(200,json.dumps(health_report(db)),'application/json')
                elif parsed.path=='/api/inventory':
                    where=['i.present=1','s.active=1'];values=[]
                    for param,column in [('company_id','i.company_id'),('source_id','i.source_id')]:
                        if params.get(param):where.append(column+'=?');values.append(int(params[param]))
                    clause=' AND '.join(where);limit=max(1,min(1000,int(params.get('limit',200))));offset=max(0,int(params.get('offset',0)))
                    total=db.execute('SELECT COUNT(*) FROM source_inventory i JOIN sources s ON s.id=i.source_id WHERE '+clause,values).fetchone()[0]
                    rows=[dict(r) for r in db.execute('SELECT i.* FROM source_inventory i JOIN sources s ON s.id=i.source_id WHERE '+clause+' ORDER BY i.first_seen_at DESC LIMIT ? OFFSET ?',values+[limit,offset])]
                    for row in rows:
                        row['normalized_payload']=json.loads(row['normalized_payload']);row['classification']=json.loads(row['classification'])
                    return self.reply(200,json.dumps(dict(total_source_postings=total,postings=rows)),'application/json')
                elif parsed.path in ("/health", "/sources", "/api/health"):
                    data = health(db)
                    if parsed.path == "/api/health":
                        return self.reply(200, json.dumps(data), "application/json")
                    coverage = summary(db)
                    registry=health_report(db)
                    body='<p>Must-cover employer health by tier. Unsupported, failing and stale counts can overlap.</p>'
                    body+=table([dict(tier=t,**m) for t,m in registry['tier_metrics'].items()],[('tier','Tier'),('total','Employers'),('monitored_successfully','Healthy'),('healthy_percentage','Healthy %'),('unsupported','Unsupported'),('failing','Failing'),('stale','Stale/unverified')])
                    body+=f"<p>{len(registry['critical_failures'])} must-cover coverage issues. <a href='/api/universe'>Employer registry and QA JSON</a> | <a href='/api/inventory'>Full source inventory JSON</a></p>"
                    body+=table(registry['critical_failures'],[('company','Company'),('tier','Tier'),('severity','Urgency'),('status','Status'),('providers','Providers')])
                    body += '<details><summary>Overall inventory diagnostics</summary><p>' + escape(coverage['definition']) + '</p>'
                    body += table([dict(metric=k,value=v) for k,v in coverage['metrics'].items()], [('metric','Metric'),('value','Value')])+'</details>'
                    body += '<p><a href="/api/coverage">Complete coverage JSON</a></p>'
                    body += table(registry['employers'], [('company','Company'),('monitoring_status','Monitoring state')])
                    body += '<p>NEVER_CHECKED and ATS_CHANGED are coverage gaps. Imported job status is historical, not live verification.</p>'
                    body += table(data, [("company", "Company"), ("id", "Source ID"), ("ats_type", "ATS"),
                               ("ats_identifier", "Board"), ("last_checked_at", "Last checked"),
                               ("last_successful_check_at", "Last success"), ("health_status", "Status"),
                               ("jobs_returned", "Jobs returned"), ("consecutive_failures", "Failures"),
                               ("last_error", "Error")])
                else:
                    return self.reply(404, "Not found", "text/plain")
                page = '<!doctype html><meta charset="utf-8"><title>Scout internal inspector</title><style>body{font:14px system-ui;margin:20px}table{border-collapse:collapse}td,th{border:1px solid #aaa;padding:6px;text-align:left}nav{margin-bottom:16px}</style><nav><a href="/jobs">Jobs</a> | <a href="/health">Source health</a></nav>' + body
                self.reply(200, page, "text/html")
            except (ValueError, KeyError) as exc:
                self.reply(400, json.dumps({"error": str(exc)}), "application/json")
            finally:
                db.close()

        def reply(self, status, body, content_type):
            raw = body.encode()
            self.send_response(status)
            self.send_header("Content-Type", content_type + "; charset=utf-8")
            self.send_header("Content-Length", str(len(raw)))
            self.send_header("X-Content-Type-Options", "nosniff")
            self.send_header("Content-Security-Policy", "default-src 'none'; style-src 'unsafe-inline'; form-action 'self'; base-uri 'none'")
            self.end_headers()
            self.wfile.write(raw)
    server = ThreadingHTTPServer(("127.0.0.1", port), Handler)
    print(f"Internal inspector: http://127.0.0.1:{server.server_port}/jobs", flush=True)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()


def table(rows, columns):
    result = '<table><thead><tr>' + ''.join('<th>' + escape(label) + '</th>' for _, label in columns) + '</tr></thead><tbody>'
    for row in rows:
        result += '<tr>'
        for key, _ in columns:
            value = row.get(key)
            if isinstance(value, list):
                value = "; ".join(value)
            cell = escape(str(value)) if value is not None else "—"
            if key == "canonical_apply_url" and value:
                cell = f'<a href="{escape(value, quote=True)}" rel="noreferrer">Apply</a>'
            result += '<td>' + cell + '</td>'
        result += '</tr>'
    return result + '</tbody></table>'
