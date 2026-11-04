import argparse,json,csv
from pathlib import Path
from scout.db import connect
from scout.provenance import audit
p=argparse.ArgumentParser();p.add_argument('--at');p.add_argument('--db',default='data/scout.sqlite3');p.add_argument('--output',default='reports/workday-reliability');a=p.parse_args();c=connect(a.db);
from datetime import datetime
r=audit(c,datetime.fromisoformat(a.at) if a.at else None);out=Path(a.output);out.mkdir(parents=True,exist_ok=True);(out/'inventory-provenance.json').write_text(json.dumps(r,indent=2))
with (out/'inventory-provenance.csv').open('w',newline='') as f:
 w=csv.DictWriter(f,fieldnames=list(r['rows'][0]) if r['rows'] else []);w.writeheader();w.writerows(dict(x,memberships=json.dumps(x['memberships'])) for x in r['rows'])
print({k:v for k,v in r.items() if k!='rows'});c.close()
