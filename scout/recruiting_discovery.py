"""Reproducible candidate discovery; directory ages are never job timestamps."""
import re,hashlib
from urllib.parse import urlsplit,unquote
from .entities import resolve,name_key
from .sources import detect
BUSINESS=re.compile(r'financ|account|audit|bank|invest|wealth|asset manag|consult|strateg|business|operations|supply chain|procure|merchandis|marketing|human resources|sales|product manag|offering manag',re.I)
def directory_rows(markdown):
 out=[]
 for line in markdown.splitlines():
  cells=[c.strip() for c in re.split(r'(?<!\\)\|',line.strip())[1:-1]]
  if len(cells)<5:continue
  cm=re.fullmatch(r'\[([^]]+)\]\((https?://[^)]+)\)',cells[0]);apply=re.search(r'\[Apply\]\((https://[^)]+)\)',cells[-1])
  if not cm or not apply:continue
  title=re.sub(r'\\([()])',r'\1',cells[1]).replace('🛂','').strip();url=apply[1]
  if not BUSINESS.search(title) or not re.search(r'intern|co.op|summer (?:analyst|associate)',title,re.I):continue
  if re.search(r'\bMBA\b|\bPhD\b|master|graduate leadership',title,re.I):continue
  provider,identifier=detect(url)
  out.append(dict(key=hashlib.sha256(url.encode()).hexdigest()[:20],employer=cm[1],title=title,external_location=cells[2],external_age=cells[3],url=url,provider=provider,identifier=identifier,function='FINANCE' if re.search(r'financ|account|audit|bank|invest|wealth',title,re.I) else 'CONSULTING' if 'consult' in title.lower() else 'STRATEGY' if 'strateg' in title.lower() else 'BUSINESS_OPERATIONS'))
 unique={r['key']:r for r in out};return list(unique.values())
def board_identity(url):
 provider,identifier=detect(url)
 if provider=='workday':
  from .sources.workday import configuration
  root,tenant,board=configuration(dict(careers_url=url));return provider,tenant+'/'+board,root+'/en-US/'+board
 if provider=='icims':
  from .sources.icims import configuration
  host,root=configuration(dict(careers_url=url));return 'icims',host,root
 if provider=='eightfold':
  from urllib.parse import urlsplit
  return provider,identifier,'https://'+urlsplit(url).netloc+'/careers'
 if provider in ('greenhouse','lever','ashby','smartrecruiters'):
  roots={'greenhouse':'https://job-boards.greenhouse.io/','lever':'https://jobs.lever.co/','ashby':'https://jobs.ashbyhq.com/','smartrecruiters':'https://careers.smartrecruiters.com/'}
  return provider,identifier,roots[provider]+identifier
 return provider,identifier,url

def mfa_members(html):
    """Primary directory articles; service providers are never treated as fund managers."""
    from .model import text
    rows=[]
    for attrs,body in re.findall(r'<article\b([^>]*)>(.*?)</article>',html,re.S):
        if not re.search(r'members_level-(?:founders-council|fund-manager-members)\b',attrs):continue
        ident=re.search(r'id="post-(\d+)"',attrs);link=re.search(r'<a[^>]+href="(https?://[^"]+)"[^>]*>(.*?)</a>',body,re.S)
        heading=re.search(r'<div class="elementor-heading-title[^"]*">(.*?)</div>',body,re.S)
        if not ident or not heading:raise ValueError('MFA fund-member article missing identity/name')
        rows.append(dict(external_id=ident[1],name=text(link[2] if link else heading[1]),domain=link[1] if link else '',metadata=dict(category='ALTERNATIVES',membership_source='MFA public primary directory')))
    if len(rows)<50 or len({r['external_id'] for r in rows})!=len(rows):raise ValueError('MFA roster unexpectedly short or duplicate')
    return rows
