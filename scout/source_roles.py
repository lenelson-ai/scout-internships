"""Auditable source scope. A successful crawl alone never establishes authority."""
import json
from .model import now
ROLES={'AUTHORITATIVE','SUPPLEMENTAL','LEGACY','DUPLICATE','DISABLED','UNKNOWN'}

def set_role(db,sid,role,evidence,canonical=None):
    if role not in ROLES or not evidence or not evidence.get('reason'):
        raise ValueError('Role and documentary reason required')
    row=db.execute('SELECT * FROM sources WHERE id=?',(sid,)).fetchone()
    if not row:raise ValueError('Unknown source')
    if role=='AUTHORITATIVE' and not evidence.get('official_url'):
        raise ValueError('Official recruiting evidence required')
    if role=='DUPLICATE':
        target=db.execute('SELECT * FROM sources WHERE id=?',(canonical,)).fetchone()
        from .db import canonical_id
        if not target or canonical==sid or canonical_id(db,target['company_id'])!=canonical_id(db,row['company_id']) or target['source_role']=='DUPLICATE':
            raise ValueError('Duplicate requires a direct same-employer canonical source')
    payload=json.dumps(evidence,sort_keys=True)
    db.execute('UPDATE sources SET source_role=?,role_evidence=?,canonical_source_id=? WHERE id=?',(role,payload,canonical,sid))
    db.execute('INSERT INTO source_role_history(source_id,role,evidence,changed_at) VALUES(?,?,?,?)',(sid,role,payload,now()))

def evaluate(sources):
    required=[s for s in sources if s['source_role']=='AUTHORITATIVE']
    unknown=[s for s in sources if s['source_role']=='UNKNOWN']
    successes=[s for s in required if s['status']=='MONITORED']
    healthy=bool(required) and len(successes)==len(required) and not unknown
    return dict(healthy=healthy,required_sources=len(required),unknown_sources=len(unknown),
                monitoring_state='MONITORED_SUCCESSFULLY' if healthy else 'PARTIALLY_MONITORED' if successes else 'UNRESOLVED' if unknown or not required else 'NOT_MONITORED')

def review_scope(db,cid,required,evidence):
    if not required or not evidence.get('official_url') or not evidence.get('scope_reason'):
        raise ValueError('Explicit required boards and official scope evidence required')
    from .db import canonical_id
    if canonical_id(db,cid)!=cid:raise ValueError('Scope must belong to canonical employer')
    for sid in required:
        row=db.execute('SELECT * FROM sources WHERE id=?',(sid,)).fetchone()
        if not row or not row['active'] or row['source_role']!='AUTHORITATIVE' or canonical_id(db,row['company_id'])!=cid:
            raise ValueError('Required boards must be active same-employer authoritative sources')
    ids=json.dumps(sorted(set(required)));payload=json.dumps(evidence,sort_keys=True);stamp=now()
    db.execute("INSERT INTO recruiting_scope_reviews VALUES(?,'VALIDATED',?,?,?) ON CONFLICT(company_id) DO UPDATE SET state=excluded.state,required_source_ids=excluded.required_source_ids,evidence=excluded.evidence,reviewed_at=excluded.reviewed_at",(cid,ids,payload,stamp))
    db.execute("INSERT INTO recruiting_scope_history(company_id,state,required_source_ids,evidence,reviewed_at) VALUES(?,'VALIDATED',?,?,?)",(cid,ids,payload,stamp))
