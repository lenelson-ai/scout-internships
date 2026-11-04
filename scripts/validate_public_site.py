import json
from urllib.parse import urlsplit
from pathlib import Path
r=json.loads(Path('site/data/feed.json').read_text());assert r['schema_version']==1
assert len({j['scout_job_id'] for j in r['jobs']})==len(r['jobs'])
allowed={'scout_job_id','company','title','location','category','internship_type','posted_at','first_seen_at','last_seen_at','canonical_apply_url','source','source_id','provider','external_id','active','discovery_kind','new_to_scout','new_since_previous_successful_inventory','employer_posted_recently','needs_review','season','source_current'}
for j in r['jobs']:
 assert set(j)<=allowed and j['first_seen_at'] and j['last_seen_at']
 assert urlsplit(j['canonical_apply_url']).scheme=='https'
 assert j['discovery_kind']!='INITIAL_DISCOVERY' or not j['new_since_previous_successful_inventory']
assert 0<=r['priority_monitored']<=r['priority_total']
print('Public export validated:',len(r['jobs']),'unique jobs; no unapproved fields')
