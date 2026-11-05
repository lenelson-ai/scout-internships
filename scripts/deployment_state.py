"""Fail-closed Git state restore and lease-protected rolling checkpoint publication."""
import argparse,json,subprocess,gzip,hashlib,tempfile,shutil,os,sqlite3,uuid
from pathlib import Path
from scripts.checkpoint_shared_state import checkpoint
from scout.db import connect
from scout.model import now

def git(*args,check=True):return subprocess.run(['git',*args],check=check,capture_output=True)
def fingerprint(db):
 def digest(query):return hashlib.sha256(json.dumps([list(r) for r in db.execute(query)],separators=(',',':')).encode()).hexdigest()
 return dict(job_ids_first_seen=digest('select id,first_seen_at from jobs order by id'),memberships_first_seen=digest('select source_id,identity,first_seen_at from source_inventory order by source_id,identity'),job_count=db.execute('select count(*) from jobs').fetchone()[0],membership_count=db.execute('select count(*) from source_inventory').fetchone()[0])
def validate(path):
 if not Path(path).is_file():raise ValueError('Missing database')
 c=sqlite3.connect('file:'+str(Path(path).resolve())+'?mode=ro',uri=True)
 try:
  if c.execute('pragma integrity_check').fetchone()[0]!='ok' or c.execute('pragma foreign_key_check').fetchall():raise ValueError('Invalid durable state')
  if c.execute('select count(*) from legacy_records').fetchone()[0]:raise ValueError('Personalized tracker records forbidden in production state')
  if not c.execute('select count(*) from sources').fetchone()[0]:raise ValueError('Empty source registry')
 finally:c.close()

def restore(directory):
 d=Path(directory);d.mkdir(parents=True,exist_ok=True)
 # Authentication/network failures raise. Empty successful output alone means
 # absent branch. Never use seed to recover from a fetch/read/validation failure.
 remote=git('ls-remote','--heads','origin','refs/heads/scout-state').stdout.decode().strip();commit=remote.split()[0] if remote else None;manifest={}
 if commit:
  git('fetch','--no-tags','origin',commit)
  blob=git('show',commit+':state.sqlite3.gz').stdout
  m=git('show',commit+':state-manifest.json',check=False)
  if m.returncode==0:
   manifest=json.loads(m.stdout);assert hashlib.sha256(blob).hexdigest()==manifest['compressed_sha256'],'Checkpoint hash mismatch'
  for name,target in [('feed.json','previous-feed.json'),('inventory.json','previous-inventory.json')]:
   r=git('show',commit+':'+name,check=False)
   if r.returncode and name=='inventory.json':
    # Older production branches did not persist full inventory; do not substitute
    # a fresh seed inventory. Existing recent feed is retained, full view empty.
    raw=json.dumps(dict(schema_version=1,generated_at=now(),jobs=[],definition='Prior full inventory unavailable; awaiting successful export')).encode()
   elif r.returncode:raise ValueError('Production state lacks last valid feed')
   else:raw=r.stdout
   json.loads(raw);(d/target).write_bytes(raw)
 else:
  blob=Path('deploy/scanner-seed.sqlite3.gz').read_bytes()
  for name in ('feed','inventory'):shutil.copy2('site/data/'+name+'.json',d/('previous-'+name+'.json'))
 db=d/'scout.sqlite3'
 if db.exists():raise ValueError('Refusing to overwrite an existing working database')
 db.write_bytes(gzip.decompress(blob));validate(db);c=connect(db);actual=fingerprint(c);c.close()
 if manifest and actual!=manifest['history']:raise ValueError('Restored job/membership identity or discovery history differs from prior checkpoint')
 record=dict(restored_at=now(),origin='EXISTING_STATE' if commit else 'FIRST_DEPLOYMENT_SEED',state_commit=commit,compressed_sha256=hashlib.sha256(blob).hexdigest(),previous_manifest=manifest,history=actual,history_verified=bool(manifest),legacy_branch_without_manifest=bool(commit and not manifest));(d/'restore.json').write_text(json.dumps(record,indent=2))
 for name in ('feed','inventory'):shutil.copy2(d/('previous-'+name+'.json'),'site/data/'+name+'.json')
 print(json.dumps({k:v for k,v in record.items() if k not in ('previous_manifest','history')}))

def persist(directory):
 d=Path(directory);restored=json.loads((d/'restore.json').read_text());metrics=json.loads((d/'run-metrics.json').read_text()) if (d/'run-metrics.json').exists() else {};good=bool(metrics.get('export_success')) and not metrics.get('operation_error')
 if not good:
  for name in ('feed','inventory'):shutil.copy2(d/('previous-'+name+'.json'),'site/data/'+name+'.json')
 subprocess.run([os.sys.executable,'-m','scripts.validate_public_site'],check=True)
 validate(d/'scout.sqlite3');checkpoint(d/'scout.sqlite3',d/'state.sqlite3.gz');c=connect(d/'scout.sqlite3');history=fingerprint(c)
 from scout.operations import report
 operations=report(c);events=[dict(r) for r in c.execute("select e.*,d.result delivery from discovery_events e left join discovery_deliveries d on d.event_id=e.id and d.run_id=? where e.kind='NEW_SINCE_PREVIOUS_SUCCESSFUL_INVENTORY'",(metrics.get('operation_id'),))];c.close()
 manifest=dict(format=1,checkpointed_at=now(),operation_id=metrics.get('operation_id'),workflow_run_id=os.environ.get('GITHUB_RUN_ID'),workflow_run_attempt=os.environ.get('GITHUB_RUN_ATTEMPT'),workflow_event=os.environ.get('GITHUB_EVENT_NAME','local'),restored_state_commit=restored['state_commit'],restored_operation_id=restored['previous_manifest'].get('operation_id'),restore_history_verified=restored['history_verified'],checkpoint_success=True,public_export_success=good,compressed_sha256=hashlib.sha256((d/'state.sqlite3.gz').read_bytes()).hexdigest(),history=history,operations=operations)
 # Keep the existing rolling-checkpoint architecture while preventing stale or
 # concurrent writers from replacing a newer committed state. The DATABASE keeps
 # history; no fresh seed ever replaces an existing production branch.
 work=Path(tempfile.mkdtemp(prefix='scout-state-push-'));git('worktree','add','--detach',str(work),'HEAD')
 try:
  git('-C',str(work),'checkout','--orphan','scout-checkpoint-'+uuid.uuid4().hex);git('-C',str(work),'rm','-rf','.')
  shutil.copy2(d/'state.sqlite3.gz',work/'state.sqlite3.gz')
  for name in ('feed','inventory'):shutil.copy2('site/data/'+name+'.json',work/(name+'.json'))
  from scout.platform_health import human_report
  (work/'production-health.md').write_text(human_report(operations))
  (work/'state-manifest.json').write_text(json.dumps(manifest,indent=2));(work/'operations.json').write_text(json.dumps(operations,indent=2));(work/'discovery-events.json').write_text(json.dumps(events,indent=2));git('-C',str(work),'add','state.sqlite3.gz','state-manifest.json','feed.json','inventory.json','operations.json','discovery-events.json','production-health.md');git('-C',str(work),'-c','user.name=Scout scanner','-c','user.email=scout@users.noreply.github.com','commit','-m','Checkpoint verified scanner history')
  expected=restored['state_commit'] or '';git('-C',str(work),'push','origin','HEAD:refs/heads/scout-state','--force-with-lease=refs/heads/scout-state:'+expected)
 finally:git('worktree','remove','--force',str(work),check=False);shutil.rmtree(work,ignore_errors=True)
 (d/'checkpoint-result.json').write_text(json.dumps(dict(checkpoint_success=True,pushed=True,public_export_success=good,compressed_sha256=manifest['compressed_sha256']),indent=2));print('Validated durable checkpoint persisted; publication eligibility:',good)
if __name__=='__main__':
 p=argparse.ArgumentParser();p.add_argument('mode',choices=['restore','persist']);p.add_argument('--directory',default='shared-state');a=p.parse_args();(restore if a.mode=='restore' else persist)(a.directory)
