"""Source-specific parsing and public snapshot fetching; no search/LLM selection."""
import csv
import hashlib
import json
from pathlib import Path
import re
import ssl
import shutil
import subprocess
import urllib.request
from urllib.parse import urlencode
from .model import text, now
from .sources.base import get_json

ROOT=Path(__file__).resolve().parents[1]


def digest(path):return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def fetch_datasets(root=ROOT):
    out=Path(root)/'data/universe/snapshots';out.mkdir(parents=True,exist_ok=True)
    for name,filters in [('fdic_established_banks','ACTIVE:1 AND ASSET:[10000000 TO *]'),('fdic_trust_banks','ACTIVE:1 AND NAME:*Trust* AND ASSET:[1000000 TO *]')]:
        url='https://api.fdic.gov/banks/institutions?'+urlencode(dict(filters=filters,fields='NAME,CERT,ASSET,WEBADDR,NAMEHCR,REGAGNT,CITY,STALP,TRUST,ESTYMD',limit=10000,format='json'))
        data=get_json(url)
        if len(data.get('data',[]))!=data.get('meta',{}).get('total'):raise ValueError('Incomplete FDIC snapshot')
        (out/(name+'.json')).write_text(json.dumps(dict(request_url=url,fetched_at=now(),response=data),indent=2))
    paths={
        'vault_consulting':'https://legacy.vault.com/best-companies-to-work-for/consulting/vault-consulting-rankings-top-50',
        'vault_banking':'https://legacy.vault.com/best-companies-to-work-for/banking/best-banks-to-work-for-top-25',
        'wellington_careers':'https://www.wellington.com/en/careers',
        'wellington_program':'https://www.wellington.com/en/about-us/campus-programs'}
    context=ssl.create_default_context(cafile='/etc/ssl/cert.pem') if Path('/etc/ssl/cert.pem').exists() else ssl.create_default_context()
    for name,url in paths.items():
        request=urllib.request.Request(url,headers={'User-Agent':'InternshipScout/1.0 (public employer datasets)'})
        with urllib.request.urlopen(request,context=context,timeout=30) as response:html=response.read(5*1024*1024).decode()
        (out/(name+'.html')).write_text(html)
        if name.startswith('vault_'):
            attrs=dict(re.findall(r'data-(rank|year|category|maxPages)="([^"]+)"',html))
            if not all(k in attrs for k in ('rank','year','category','maxPages')):raise ValueError('Ranking page metadata changed')
            for page in range(2,int(attrs['maxPages'])+1):
                endpoint='https://legacy.vault.com/vault/api/Rankings/LoadMoreCompanyRanksJSON?'+urlencode(dict(rank=attrs['rank'],year=attrs['year'],category=attrs['category'],pg=page))
                rows=get_json(endpoint)
                if not isinstance(rows,list) or not rows:raise ValueError('Public ranking pagination incomplete/unavailable')
                (out/f'{name}_page{page}.json').write_text(json.dumps(dict(request_url=endpoint,rows=rows),indent=2))
    occ_url='https://www.occ.gov/topics/charters-and-licensing/financial-institution-lists/trust-by-name.pdf'
    with urllib.request.urlopen(occ_url,context=context,timeout=30) as response:pdf=response.read(8*1024*1024)
    if not pdf.startswith(b'%PDF') or b'%%EOF' not in pdf[-2048:]:raise ValueError('OCC did not return a complete PDF roster')
    (out/'occ_trust.pdf').write_bytes(pdf)
    executable=shutil.which('pdftotext')
    if not executable:raise RuntimeError('OCC roster extraction requires pdftotext (Poppler), or use a normalized reviewed roster import')
    subprocess.run([executable,'-layout',str(out/'occ_trust.pdf'),str(out/'occ_trust.txt')],check=True)
    occ_trust_rows(out/'occ_trust.txt')


def fdic_rows(path):
    doc=json.loads(Path(path).read_text());data=doc['response']
    if len(data['data'])!=data['meta']['total']:raise ValueError('FDIC count mismatch')
    return [dict(external_id=str(row['data']['CERT']),name=row['data']['NAME'],domain=row['data'].get('WEBADDR',''),
                 parent_name=row['data'].get('NAMEHCR',''),metadata=dict(row['data'],assets_usd=int(row['data'].get('ASSET',0))*1000,
                 established_date=row['data'].get('ESTYMD'),category='BANKING_AND_TRUST' if 'fdic_trust_banks' in Path(path).name else 'BANKING',request_url=doc['request_url'])) for row in data['data']]


def occ_trust_rows(path):
    """Parse Poppler -layout output of the complete official roster, not a hand list."""
    content=Path(path).read_text();result=[]
    for line in content.splitlines():
        match=re.match(r'^\s*(\d+)\s+(.*?)\s{2,}',line)
        if not match:
            if re.match(r'^\s*\d+\s+',line):raise ValueError('Unparsed OCC data row')
            continue
        tail=re.search(r'\b([A-Z]{2})\s+(\d+)(?:\s+(\d+))?\s*$',line)
        if not tail:raise ValueError('OCC trust roster table format changed')
        result.append(dict(external_id=match[1],name=match[2],metadata=dict(category='WEALTH_MANAGEMENT',charter_id=match[1],certificate=tail[2],rssd=tail[3],regulatory_directory='OCC national trust banks',source_line=line.strip())))
    if len(result)<30 or len({r['external_id'] for r in result})!=len(result):raise ValueError('Incomplete or duplicate OCC trust roster')
    return result


def vault_rows(directory,name,expected):
    directory=Path(directory);html=(directory/(name+'.html')).read_text()
    headings=re.findall(r'<h3[^>]*>(.*?)</h3>',html,re.S)
    ranks=re.findall(r'class="rank RankNumber"[^>]*>\s*<span>#</span>\s*(\d+)',html)
    if len(headings)!=len(ranks) or not headings:raise ValueError('Ranking first-page markup changed')
    result=[]
    for rank,heading in zip(ranks,headings):
        link=re.search(r'href="([^"]+)"',heading)
        if not link:raise ValueError('Ranking item has no stable profile URL')
        result.append(dict(external_id=link[1],name=text(heading),metadata=dict(rank=int(rank),profile_url='https://legacy.vault.com'+link[1],category='CONSULTING' if name=='vault_consulting' else 'INVESTMENT_BANKING')))
    files=[directory/(name+'.html')]
    for path in sorted(directory.glob(name+'_page*.json')):
        files.append(path)
        for row in json.loads(path.read_text())['rows']:
            result.append(dict(external_id=row['URL'],name=text(row['Title']),metadata=dict(rank=int(row['Rank']),profile_url='https://legacy.vault.com'+row['URL'],category='CONSULTING' if name=='vault_consulting' else 'INVESTMENT_BANKING')))
    if sorted(r['metadata']['rank'] for r in result)!=list(range(1,expected+1)) or len({r['external_id'] for r in result})!=expected:
        raise ValueError('Ranking missing/duplicate entries; refusing incomplete source list')
    return result,files
