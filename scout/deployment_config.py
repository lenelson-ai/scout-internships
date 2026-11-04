"""Add verified source configuration to existing state; never replace history/health."""
import json,hashlib
from .db import canonical_id,source
from .model import now

def manifest(db):
 from .scan_queue import eligible_sources
 ids=eligible_sources(db);ss=[]
 for sid in sorted(ids):
  s=dict(db.execute('select * from sources where id=?',(sid,)).fetchone());c=db.execute('select * from companies where id=?',(canonical_id(db,s['company_id']),)).fetchone()
  ss.append(dict(seed_source_id=sid,employer=c['normalized_name'],provider=s['ats_type'],identifier=s['ats_identifier'],url=s['careers_url'],role_evidence=s['role_evidence'] if 'role_evidence' in s else 'Previously reviewed public recruiting publication'))
 plans=[]
 for r in db.execute("select r.*,c.normalized_name from recruiting_scope_reviews r join companies c on c.id=r.company_id where r.state='VALIDATED'"):
  req=json.loads(r['required_source_ids'])
  if req and set(req)<=ids:plans.append(dict(employer=r['normalized_name'],required_seed_ids=req,evidence=r['evidence']))
 result=dict(format=1,sources=ss,scope_plans=plans);result['revision']=hashlib.sha256(json.dumps(result,sort_keys=True).encode()).hexdigest();return result

def adopt(db,cfg):
 expected=dict(cfg);revision=expected.pop('revision');assert hashlib.sha256(json.dumps(expected,sort_keys=True).encode()).hexdigest()==revision,'Configuration hash mismatch'
 old=db.execute('select result from deployment_configuration where revision=?',(revision,)).fetchone()
 if old:return dict(json.loads(old[0]),already_applied=True)
 companies={r['normalized_name']:canonical_id(db,r['id']) for r in db.execute('select id,normalized_name from companies')};mapping={};added=[];conflicts=[]
 with db:
  for s in cfg['sources']:
   cid=companies.get(s['employer'])
   if not cid:conflicts.append(dict(employer=s['employer'],reason='Employer absent from existing registry; manual identity review required'));continue
   row=db.execute('select * from sources where company_id=? and ats_type=? and ats_identifier=?',(cid,s['provider'],s['identifier'])).fetchone()
   if row:
    sid=row['id']
    if row['source_role'] in ('DISABLED','LEGACY','DUPLICATE'):conflicts.append(dict(source_id=sid,reason='Existing retired/disabled source retained; no automatic reactivation'));continue
    if row['careers_url']!=s['url']:
     from .recruiting_discovery import board_identity
     old_board=board_identity(row['careers_url'] or '')[:2];new_board=board_identity(s['url'])[:2]
     if old_board!=new_board or new_board[0]!=s['provider']:
      conflicts.append(dict(source_id=sid,reason='Same configuration key changes recruiting publication; existing URL/history retained for manual review'));continue
     db.execute('update sources set careers_url=? where id=?',(s['url'],sid))
    if row['source_role']=='UNKNOWN':
     from .source_roles import set_role
     set_role(db,sid,'AUTHORITATIVE',dict(official_url=s['url'],reason='Verified deployment configuration ownership evidence; live full inventory still required',configuration_revision=revision,evidence=s['role_evidence']))
   else:
    sid=source(db,cid,s['provider'],s['identifier'],s['url']);added.append(sid)
    from .source_roles import set_role
    set_role(db,sid,'AUTHORITATIVE',dict(official_url=s['url'],reason='Verified deployment configuration; inventory not yet checked',configuration_revision=revision,evidence=s['role_evidence']))
   mapping[s['seed_source_id']]=sid
   census=db.execute('select 1 from crawl_runs where source_id=? and authoritative=1',(sid,)).fetchone()
   if not census:db.execute('insert or replace into source_onboarding values(?,?,?)',(sid,revision,json.dumps(s)))
  for plan in cfg['scope_plans']:
   cid=companies.get(plan['employer'])
   if not cid or any(i not in mapping for i in plan['required_seed_ids']):continue
   req={mapping[i] for i in plan['required_seed_ids']};old=db.execute('select required_source_ids from recruiting_scope_reviews where company_id=?',(cid,)).fetchone()
   if old:req.update(json.loads(old[0]))
   req.update(r[0] for r in db.execute("select id from sources where company_id=? and active=1 and source_role='AUTHORITATIVE'",(cid,)))
   db.execute('insert or replace into deployment_scope_plans values(?,?,?,?)',(cid,json.dumps(sorted(req)),plan['evidence'],revision))
   if old and set(json.loads(old[0]))!=req:
    evidence=json.dumps(dict(reason='Verified configuration adds required publications; live full inventories pending',configuration_revision=revision,prior_scope_preserved=True))
    db.execute("update recruiting_scope_reviews set state='UNRESOLVED',required_source_ids=?,evidence=?,reviewed_at=? where company_id=?",(json.dumps(sorted(req)),evidence,now(),cid))
    db.execute("insert into recruiting_scope_history(company_id,state,required_source_ids,evidence,reviewed_at) values(?,'UNRESOLVED',?,?,?)",(cid,json.dumps(sorted(req)),evidence,now()))
  result=dict(revision=revision,added_source_ids=added,conflicts=conflicts,source_mapping=mapping,history_copied=False)
  db.execute('insert into deployment_configuration values(?,?,?)',(revision,now(),json.dumps(result)))
 return result

def activate(db):
 from datetime import datetime,timezone,timedelta
 from .source_roles import review_scope
 cutoff=(datetime.now(timezone.utc)-timedelta(hours=24)).isoformat();activated=[]
 with db:
  for p in db.execute('select * from deployment_scope_plans').fetchall():
   req=json.loads(p['required_source_ids']);complete=bool(req)
   if db.execute("select 1 from sources where company_id=? and active=1 and source_role='UNKNOWN' and (careers_url is not null or ats_identifier!='')",(p['company_id'],)).fetchone():continue
   for sid in req:
    s=db.execute('select * from sources where id=?',(sid,)).fetchone();census=db.execute('select jobs_returned from crawl_runs where source_id=? and authoritative=1 order by id desc limit 1',(sid,)).fetchone();count=db.execute('select count(*) from source_inventory where source_id=? and present=1',(sid,)).fetchone()[0]
    if not s or not s['active'] or s['source_role']!='AUTHORITATIVE' or not s['health_status'].startswith('SUCCESS') or not s['last_successful_check_at'] or s['last_successful_check_at']<cutoff or not census or census[0]!=count:complete=False;break
   if complete:
    old=db.execute('select state,required_source_ids from recruiting_scope_reviews where company_id=?',(p['company_id'],)).fetchone()
    if not old or old['state']!='VALIDATED' or set(json.loads(old['required_source_ids']))!=set(req):
     review_scope(db,p['company_id'],req,dict(official_url=json.loads(p['evidence']).get('official_url'),scope_reason='All known required publications in verified configuration and existing state now have complete fresh inventories',configuration_revision=p['revision'],prior_scope_evidence=p['evidence']));activated.append(p['company_id'])
  db.execute("delete from source_onboarding where source_id in (select s.id from sources s where s.health_status like 'SUCCESS%' and exists(select 1 from crawl_runs r where r.source_id=s.id and r.authoritative=1))")
 return activated
