"""Durable bounded runner with run, census, discovery and export measurements."""
import argparse,json,time,sys
from pathlib import Path
from scout.db import connect
from scout.scan_queue import scan,apply_cadences
from scout.public_site import build
from scout.model import now
from scout import operations

def main():
 p=argparse.ArgumentParser();p.add_argument('--db',default='data/scout.sqlite3');p.add_argument('--limit',type=int);p.add_argument('--max-seconds',type=int);p.add_argument('--output',default='site/data/feed.json');p.add_argument('--metrics-output',default='shared-state/run-metrics.json');a=p.parse_args();db=connect(a.db);context=operations.begin(db);old=operations.history(db);health={r['id']:r['health_status'] for r in db.execute('select id,health_status from sources')};started=time.monotonic();r=dict(operation_id=context['id'],context=context,export_success=False)
 try:
  import os
  if os.environ.get('GITHUB_ACTIONS')=='true':
   from scout.platform_health import collect
   r['platform_evidence_collection']=collect(db,os.environ['GITHUB_REPOSITORY'])
  from scout.deployment_config import adopt,activate
  cfg=Path('deploy/scanner-config.json')
  if cfg.exists():r['configuration_adoption']=adopt(db,json.loads(cfg.read_text()))
  policy=json.loads(Path('config/scan_policy.json').read_text());plan=apply_cadences(db,policy)
  result=scan(db,limit=a.limit if a.limit is not None else policy['max_sources_per_run'],workers=policy['workers'],provider_limit=policy['per_provider_concurrency'],budget=policy['per_source_budget_seconds'],max_seconds=a.max_seconds if a.max_seconds is not None else policy['max_run_seconds']);r.update(result,capacity_plan=plan)
  from scout.ingest import consolidate_native_apply_duplicates
  r['cross_provider_merges']=consolidate_native_apply_duplicates(db);r['activated_scope_plans']=activate(db)
  from scout.provenance import audit
  provenance=audit(db);r['inventory_provenance_audit']={k:v for k,v in provenance.items() if k!='rows'}
  r['integrity']=db.execute('pragma integrity_check').fetchone()[0];r['foreign_key_violations']=len(db.execute('pragma foreign_key_check').fetchall())
  current=operations.history(db);r['missing_historical_job_ids']=sorted(set(old)-set(current));r['first_seen_moved_later']=[i for i,t in old.items() if i in current and current[i]>t]
  if r['integrity']!='ok' or r['foreign_key_violations'] or r['missing_historical_job_ids'] or r['first_seen_moved_later']:raise RuntimeError('State invariants failed; publication withheld')
  t=time.monotonic()
  try:export=build(db,a.output);r.update(export_success=True,exported_jobs=len(export['jobs']))
  except Exception as e:r['export_error']=str(e)
  r['public_export_seconds']=time.monotonic()-t;r['discovery_delivery']=operations.deliveries(db,context['id'],r['export_success']);r['source_health_changes']=[dict(source_id=x['id'],before=health.get(x['id']),after=x['health_status']) for x in db.execute('select id,health_status from sources') if health.get(x['id'])!=x['health_status']]
 except Exception as e:r['operation_error']=str(e)
 finally:
  r['total_operation_seconds']=time.monotonic()-started;r['state_allocated_bytes']=db.execute('pragma page_count').fetchone()[0]*db.execute('pragma page_size').fetchone()[0];status='COMPLETED' if r.get('export_success') and not r.get('operation_error') else 'ERROR'
  with db:
   db.execute('update monitoring_runs set finished_at=?,status=?,metrics=? where id=?',(now(),status,json.dumps(r),context['id']))
   if r.get('batch_id'):db.execute('update scan_batches set metrics=? where id=?',(json.dumps(r),r['batch_id']))
  metrics=Path(a.metrics_output);metrics.parent.mkdir(parents=True,exist_ok=True);metrics.write_text(json.dumps(r,indent=2));report=operations.report(db);r['monitoring_snapshot']={k:report[k] for k in ('generated_at','monitoring_health','queue_backlog','oldest_overdue_seconds','missing_requested_cadence','missing_effective_cadence','backlog_reasons')};r['monitoring_snapshot']['monitoring_health']={k:v for k,v in report['monitoring_health'].items() if k!='sources'}
  with db:db.execute('update monitoring_runs set metrics=? where id=?',(json.dumps(r),context['id']))
  metrics.write_text(json.dumps(r,indent=2));metrics.with_name('operations.json').write_text(json.dumps(report,indent=2));metrics.with_name('production-health.md').write_text(__import__('scout.platform_health',fromlist=['human_report']).human_report(report));print(json.dumps(r),flush=True);db.execute('pragma wal_checkpoint(TRUNCATE)');db.close()
 return 0 if status=='COMPLETED' else 2
if __name__=='__main__':sys.exit(main())
