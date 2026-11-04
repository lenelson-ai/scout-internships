import argparse
from scout.db import connect
from scout.public_site import build
p=argparse.ArgumentParser();p.add_argument('--db',default='data/scout.sqlite3');p.add_argument('--output',default='site/data/feed.json');a=p.parse_args()
r=build(connect(a.db),a.output);print(f"Public feed: {len(r['jobs'])} jobs; {r['priority_monitored']}/{r['priority_total']} priority employers")
