"""Deterministic recruiting-employer identity, with explicit uncertain matches."""
import json
import re
from urllib.parse import urlsplit
from .db import canonical_id, company
from .model import normalize, now, canonical_url

VENDORS=('myworkdayjobs.com','myworkdaysite.com','icims.com','greenhouse.io','lever.co','ashbyhq.com',
         'smartrecruiters.com','oraclecloud.com','adp.com','csod.com','recsolu.com','yello.co','ultipro.com')


def name_key(value):
    value=re.sub(r'\b([A-Za-z])\.\s*([A-Za-z])\.',r'\1\2',value)
    value=re.sub(r'\s*\([^)]*\)\s*$',' ',value)
    value=normalize(value.replace('&',' and '))
    value=re.sub(r'\b(?:national association|n a|na)\s*$','',value).strip()
    while re.search(r'\b(?:inc|incorporated|corporation|corp|company|co|llc|ltd|limited|plc|lp|group|holdings|holding)$',value.strip()):
        value=re.sub(r'\s*\b(?:inc|incorporated|corporation|corp|company|co|llc|ltd|limited|plc|lp|group|holdings|holding)$','',value.strip())
    return re.sub(r'\s+',' ',value).strip().removesuffix(' and').strip()


def domain_key(value):
    if not value:return ''
    host=(urlsplit(value if '://' in value else 'https://'+value).hostname or '').lower().rstrip('.')
    if any(host==v or host.endswith('.'+v) for v in VENDORS):return ''
    for prefix in ('www.','careers.','jobs.','apply.'):
        if host.startswith(prefix):host=host[len(prefix):]
    return host


def alias(db,cid,name,reason,evidence_url=None):
    cid=canonical_id(db,cid)
    db.execute('INSERT OR IGNORE INTO company_aliases VALUES(?,?,?,?,?)',(cid,name,name_key(name),reason,evidence_url))


def resolve(db,name,domain='',parent=''):
    key=name_key(name);domain=domain_key(domain)
    names={canonical_id(db,r['id']) for r in db.execute('SELECT id,name FROM companies WHERE active=1') if name_key(r['name'])==key}
    names|={canonical_id(db,r[0]) for r in db.execute('SELECT a.company_id FROM company_aliases a JOIN companies c ON c.id=a.company_id WHERE a.normalized_alias=? AND c.active=1',(key,))}
    if len(names)>1:return None,sorted(names),'Ambiguous normalized name; no automatic merge'
    if names:
        cid=next(iter(names))
        domains={r[0] for r in db.execute('SELECT domain FROM company_domains WHERE company_id=?',(cid,))}
        if domain and not domains and key in {'first bank','firstbank','central bank','citizens bank','security bank','american bank','community bank'}:
            return None,[cid],'Common bank name has no corroborating domain; identity requires review'
        if domain and domains and domain not in domains:
            return None,[cid],'Name matches but verified non-ATS domains conflict'
        return cid,[],'Exact normalized name / recorded alias'
    domains={canonical_id(db,r[0]) for r in db.execute('SELECT company_id FROM company_domains WHERE domain=?',(domain,))} if domain else set()
    if len(domains)==1:
        cid=next(iter(domains));canonical=db.execute('SELECT name FROM companies WHERE id=?',(cid,)).fetchone()[0]
        overlap={w for w in key.split() if len(w)>2} & set(name_key(canonical).split())
        known_parent=db.execute('SELECT 1 FROM company_entities WHERE company_id=? AND parent_organization=?',(cid,parent)).fetchone() if parent else None
        if overlap or known_parent or (parent and name_key(parent)==name_key(canonical)):
            return cid,[],'Shared verified non-ATS domain plus name/parent corroboration'
    if domains:return None,sorted(domains),'Shared domain alone is insufficient recruiting-organization evidence'
    parents={canonical_id(db,r['id']) for r in db.execute('SELECT id,name FROM companies WHERE active=1') if parent and name_key(r['name'])==name_key(parent)}
    if parents:return None,sorted(parents),'Known parent, but shared recruiting organization not established'
    related={canonical_id(db,r['id']) for r in db.execute('SELECT id,name FROM companies WHERE canonical_company_id IS NULL AND active=1')
             if len(name_key(r['name']))>=5 and (key.startswith(name_key(r['name'])+' ') or name_key(r['name']).startswith(key+' '))}
    if related:return None,sorted(related),'Possible subsidiary, regional brand or legal-entity variant; shared recruiting requires review'
    # Detect close legal/brand variants without treating similarity as merge authority.
    from difflib import SequenceMatcher
    near={canonical_id(db,r['id']) for r in db.execute('SELECT id,name FROM companies WHERE canonical_company_id IS NULL AND active=1')
          if len(key)>7 and SequenceMatcher(None,key,name_key(r['name'])).ratio()>=.91}
    if near:return None,sorted(near),'Similar names require review; fuzzy matching is never auto-merge evidence'
    return None,[],'No conflicting identity evidence; new recruiting-employer candidate'


def merge(db,duplicate,canonical,reason,evidence):
    duplicate=canonical_id(db,duplicate);canonical=canonical_id(db,canonical)
    if duplicate==canonical:return canonical
    if not reason or not evidence:raise ValueError('Company merges require reason and evidence')
    old=db.execute('SELECT * FROM companies WHERE id=?',(duplicate,)).fetchone()
    db.execute('INSERT INTO company_merges VALUES(?,?,?,?,?)',(duplicate,canonical,reason,json.dumps(evidence),now()))
    db.execute('UPDATE companies SET canonical_company_id=? WHERE id=? OR canonical_company_id=?',(canonical,duplicate,duplicate))
    for table in ('jobs','raw_organizations','company_entities'):
        db.execute(f'UPDATE {table} SET company_id=? WHERE company_id=?',(canonical,duplicate))
    db.execute('UPDATE source_inventory SET company_id=? WHERE company_id=?',(canonical,duplicate))
    for row in db.execute('SELECT * FROM company_sources WHERE company_id=?',(duplicate,)).fetchall():
        db.execute('INSERT OR IGNORE INTO company_sources VALUES(?,?,?,?)',(canonical,row['raw_organization_id'],row['discovered_at'],row['source_metadata']))
    db.execute('DELETE FROM company_sources WHERE company_id=?',(duplicate,))
    for row in db.execute('SELECT * FROM recruiting_evidence WHERE company_id=?',(duplicate,)).fetchall():
        db.execute('INSERT OR IGNORE INTO recruiting_evidence(company_id,evidence_type,evidence_key,source_url,observed_at,metadata) VALUES(?,?,?,?,?,?)',
                   (canonical,row['evidence_type'],row['evidence_key'],row['source_url'],row['observed_at'],row['metadata']))
    for row in db.execute('SELECT * FROM company_domains WHERE company_id=?',(duplicate,)).fetchall():
        db.execute('INSERT OR IGNORE INTO company_domains VALUES(?,?,?)',(canonical,row['domain'],row['evidence']))
    alias(db,canonical,old['name'],reason,str(evidence))
    for row in db.execute('SELECT * FROM company_aliases WHERE company_id=?',(duplicate,)).fetchall():
        alias(db,canonical,row['alias'],row['reason'],row['evidence_url'])
    # Keep original company/source rows as provenance. They remain routable through
    # canonical_company_id; only exact equivalent source configurations are retired.
    for key in ('domain','careers_url'):
        db.execute(f'UPDATE companies SET {key}=COALESCE({key},?) WHERE id=?',(old[key],canonical))
    rows=db.execute('SELECT s.* FROM sources s JOIN companies c ON c.id=s.company_id WHERE COALESCE(c.canonical_company_id,c.id)=? AND s.active=1 ORDER BY s.id',(canonical,)).fetchall()
    seen={};configured=[s for s in rows if s['careers_url'] or s['ats_identifier']]
    for s in rows:
        key=(s['ats_type'],s['ats_identifier'],canonical_url(s['careers_url']) if s['careers_url'] else '')
        target=seen.get(key)
        if not s['careers_url'] and not s['ats_identifier'] and configured:target=configured[0]['id']
        if target and target!=s['id']:
            db.execute('UPDATE sources SET active=0 WHERE id=?',(s['id'],))
            db.execute('INSERT OR IGNORE INTO source_equivalences VALUES(?,?,?,?)',(s['id'],target,'Exact source duplicate or empty placeholder after evidenced employer merge',now()))
        else:seen[key]=s['id']
    return canonical


def consolidate_baseline(db):
    groups={}
    for row in db.execute('SELECT * FROM companies WHERE canonical_company_id IS NULL ORDER BY id'):
        groups.setdefault(name_key(row['name']),[]).append(dict(row))
    merged=[]
    for key,rows in groups.items():
        if len(rows)<2:continue
        domains={domain_key(r['domain'] or r['careers_url']) for r in rows}
        if len(key)<5 and (len(domains)!=1 or not next(iter(domains))):continue
        canonical=rows[0]
        for duplicate in rows[1:]:
            d1=domain_key(canonical['domain'] or canonical['careers_url']);d2=domain_key(duplicate['domain'] or duplicate['careers_url'])
            if d1 and d2 and d1!=d2:
                db.execute('INSERT OR IGNORE INTO entity_resolution_cases(case_key,candidate_company_ids,reason,created_at) VALUES(?,?,?,?)',
                           ('baseline:'+str(duplicate['id']),json.dumps([canonical['id'],duplicate['id']]),'Equivalent legal name but conflicting domains',now()))
                continue
            merge(db,duplicate['id'],canonical['id'],'Equivalent normalized legal/brand name with no conflicting domain',dict(name_key=key,domains=[d1,d2]))
            merged.append([duplicate['id'],canonical['id']])
    return merged


def flag_related_baseline_names(db,company_ids):
    """Possible brand/subsidiary variants are review cases, never fuzzy merges."""
    roots=sorted({canonical_id(db,cid) for cid in company_ids})
    rows=[dict(db.execute('SELECT * FROM companies WHERE id=?',(cid,)).fetchone()) for cid in roots]
    for i,left in enumerate(rows):
        a=name_key(left['name'])
        for right in rows[i+1:]:
            b=name_key(right['name'])
            short,long=sorted((a,b),key=len)
            if short!=long and len(short)>=9 and long.startswith(short+' '):
                db.execute('INSERT OR IGNORE INTO entity_resolution_cases(case_key,candidate_company_ids,reason,created_at) VALUES(?,?,?,?)',
                           ('baseline-related:'+str(left['id'])+':'+str(right['id']),json.dumps([left['id'],right['id']]),'Related employer/brand names in legacy universe; recruiting identity needs evidence before merging',now()))


def consolidate_recruiting_groups(db):
    """A common regulatory parent AND identical successful board inventories prove grouping."""
    from .sources.workday import board_key
    grouped={}
    rows=db.execute("""SELECT s.*,COALESCE(c.canonical_company_id,c.id) owner_id FROM sources s JOIN companies c ON c.id=s.company_id
                        WHERE s.active=1 AND s.ats_type='workday' AND s.health_status LIKE 'SUCCESS%'""").fetchall()
    for s in rows:
        board=board_key(s['careers_url'])
        if not board:continue
        cid=s['owner_id']
        parents={r[0] for r in db.execute('SELECT DISTINCT parent_organization FROM company_entities WHERE company_id=? AND parent_organization IS NOT NULL',(cid,))}
        ids=tuple(r[0] for r in db.execute('SELECT external_job_id FROM source_inventory WHERE source_id=? AND present=1 ORDER BY external_job_id',(s['id'],)))
        if not ids or len(parents)!=1:continue
        parent=next(iter(parents))
        grouped.setdefault((name_key(parent),board,ids),dict(parent=parent,companies=set(),source_ids=[]))['companies'].add(cid)
        grouped[(name_key(parent),board,ids)]['source_ids'].append(s['id'])
    changes=[]
    for (_,board,ids),group in grouped.items():
        owners={canonical_id(db,cid) for cid in group['companies']}
        if len(owners)<2:continue
        matches={canonical_id(db,r['id']) for r in db.execute('SELECT id,name FROM companies') if name_key(r['name'])==name_key(group['parent'])}
        if len(matches)>1:continue
        target=next(iter(matches)) if matches else company(db,group['parent'].title())
        proof=dict(regulatory_parent=group['parent'],board=board,source_ids=group['source_ids'],identical_public_inventory_count=len(ids))
        for cid in sorted(owners):
            if cid!=target:
                merge(db,cid,target,'Common regulatory parent and identical successfully enumerated recruiting board',proof)
                changes.append(dict(from_company=cid,to_company=target,evidence=proof))
    return changes


def retire_malformed_mapping_duplicates(db):
    """Correct URL-list captures only when the valid equivalent board is already recorded."""
    retired=[]
    for s in db.execute("SELECT * FROM sources WHERE active=1 AND ats_type='workday' AND careers_url LIKE '%,%'").fetchall():
        valid=s['careers_url'].split(',',1)[0]
        owner=canonical_id(db,s['company_id'])
        target=next((r['id'] for r in db.execute("SELECT s.* FROM sources s JOIN companies c ON c.id=s.company_id WHERE COALESCE(c.canonical_company_id,c.id)=? AND s.active=1 AND s.id<>?",(owner,s['id'])) if r['careers_url']==valid),None)
        if target:
            db.execute('UPDATE sources SET active=0 WHERE id=?',(s['id'],))
            db.execute('INSERT OR REPLACE INTO source_equivalences VALUES(?,?,?,?)',(s['id'],target,'Corrected comma-separated URL-list capture; exact valid board already configured',now()))
            retired.append(dict(source_id=s['id'],canonical_source_id=target))
    return retired

def resolve_recruiting_unit(db,name,careers_url,evidence_url,record_alias=False):
    """A verified shared publication board can disambiguate a recruiting brand.

    This never merges employers. Multiple board owners remain manual-review cases.
    Callers must supply an employer-controlled link, not a guessed slug.
    """
    from .recruiting_discovery import board_identity
    if not evidence_url:raise ValueError('Official recruiting relationship evidence required')
    try:provider,identifier,_=board_identity(careers_url)
    except Exception:return None,[],'Invalid publication board identity'
    if provider=='custom' or not identifier:return resolve(db,name)
    owners=set()
    for s in db.execute('SELECT company_id,careers_url FROM sources WHERE active=1 AND careers_url IS NOT NULL'):
        try:p,i,_=board_identity(s['careers_url'])
        except Exception:continue
        if (p,i.casefold())==(provider,identifier.casefold()):owners.add(canonical_id(db,s['company_id']))
    if len(owners)!=1:return (None,sorted(owners),'Shared board has multiple owners; no automatic identity merge') if owners else resolve(db,name)
    cid=next(iter(owners));direct,candidates,_=resolve(db,name)
    if direct and direct!=cid:return None,sorted({direct,cid}),'Name and verified recruiting board disagree'
    if record_alias:alias(db,cid,name,'Employer-controlled link to same verified recruiting board',evidence_url)
    return cid,[],'Exact verified recruiting board identity; registry rows not merged'
