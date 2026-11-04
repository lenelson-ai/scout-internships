"""Bounded mapping from recorded official domains and publisher website links."""
from concurrent.futures import ThreadPoolExecutor,as_completed
from html.parser import HTMLParser
import hashlib
import json
from pathlib import Path
import re
import ssl
import urllib.request
from urllib.parse import urljoin,urlsplit
from .db import source
from .entities import domain_key
from .sources import detect,ADAPTERS
from .sources.workday import configuration
from .sources.base import SourceError
from .coverage import provider_hint
from .model import now,canonical_url
from .universe_sources import ROOT

def published_redirect(body):
    """Recognize a script-only literal handoff, never execute arbitrary JavaScript."""
    if len(body)>4096 or re.search(r'\b(?:if|function)\s*\(',body):return None
    matches=re.findall(r"window(?:\.top)?\.location(?:\.href)?\s*=\s*(['\"])(.*?)\1",body,re.S)
    if len(matches)!=1:return None
    url=matches[0][1].replace('\\/','/')
    p=urlsplit(url)
    return url if p.scheme=='https' and p.hostname and not p.username else None

class Links(HTMLParser):
    def __init__(self,base_url=''):
        super().__init__();self._links=[];self.current=None;self.words=[];self.script=False;self.scripts=[];self.base_url=base_url
    def handle_starttag(self,tag,attrs):
        a=dict(attrs)
        if tag=='a':self.current=a;self.words=[]
        if tag=='script':self.script=True
        if tag in ('iframe','form','button'):
            u=a.get('src') or a.get('action') or a.get('data-href') or a.get('data-url')
            search_form=tag=='form' and a.get('data-selector-name') in ('searchform','advancedsearch') and bool(re.search(r'jobs?|careers?',urlsplit(u or '').path,re.I))
            if search_form:self._links.append((u,'Published full job search',dict(a,recruiting_inventory_root=True)))
            if u and (detect(u)[0]!='custom' or provider_hint(u)[0]!='custom_unidentified' or re.search('career|job|recruit',a.get('title','')+' '+a.get('aria-label',''),re.I)):
                self._links.append((u,'Embedded recruiting jobs publication',dict(a,embedded=True)))
    def handle_data(self,data):
        if self.current is not None:self.words.append(data)
        if self.script:self.scripts.append(data)
    def handle_endtag(self,tag):
        if tag=='a' and self.current is not None:
            self._links.append((self.current.get('href',''),' '.join(self.words),self.current));self.current=None
        if tag=='script':self.script=False
    @property
    def links(self):
        result=list(self._links);seen={x[0] for x in result}
        for script in self.scripts:
            import html as html_module
            body=html_module.unescape(script).replace('\\/','/')
            # JSON nested inside script strings can contain multiple escaping levels.
            body=re.sub(r'\\+(?=[/\"\'])','',body)
            for m in re.finditer(r'["\']((?:https?://[^"\'<>\s]+)?/(?:[A-Za-z0-9_-]+/){0,4}(?:careers?|jobs|students-and-graduates|early-careers|recruiting)(?:/[^"\'<>\s]*)?)["\']',body,re.I):
                u=m[1]
                if re.search(r'\.(?:js|mjs|css|png|svg|woff2?)(?:\?|$)',u,re.I):continue
                absolute=urljoin(self.base_url,u);same=not urlsplit(u).netloc or domain_key(absolute)==domain_key(self.base_url)
                explicit_label=bool(re.search(r'(?:label|title|name|text)["\']?\s*:\s*["\'][^"\']*(?:Careers|Jobs|Join Our Team)',body[max(0,m.start()-160):m.end()+160],re.I))
                if not same and detect(u)[0]=='custom' and provider_hint(u)[0]=='custom_unidentified' and not explicit_label:continue
                if u not in seen:seen.add(u);result.append((u,'Published careers navigation',dict(published_navigation=True)))
        return result


def get_page(url,root=ROOT,timeout=15):
    if url.startswith('http://'):url='https://'+url[len('http://'):]
    p=urlsplit(url)
    if p.scheme!='https' or not p.hostname:raise ValueError('No valid HTTPS official source URL')
    from .sources.transport import open_url,tls_context
    ctx=tls_context()
    request=urllib.request.Request(url,headers={'User-Agent':'InternshipScout/1.0 (official careers source mapping)'})
    from .sources.base import record_request
    record_request()
    with open_url(request,context=ctx,timeout=timeout) as response:
        body=response.read(4*1024*1024+1);final=response.url
        record_request(len(body))
        if len(body)>4*1024*1024:raise ValueError("Careers page exceeds bounded capture; no complete page inspection")
    sha=hashlib.sha256(body).hexdigest();directory=Path(root)/'data/universe/career_mapping_snapshots';directory.mkdir(parents=True,exist_ok=True)
    (directory/(sha+'.html')).write_bytes(body)
    with (directory/'index.jsonl').open('a') as f:f.write(json.dumps(dict(url=url,final_url=final,sha256=sha,at=now()))+'\n')
    html=body.decode(errors='replace');parser=Links(final);parser.feed(html);parser.close()
    return final,html,parser.links,sha


def useful_url(url):
    host=(urlsplit(url).hostname or '').lower()
    return bool(host and not any(host==s or host.endswith('.'+s) for s in ('linkedin.com','indeed.com','glassdoor.com','facebook.com','twitter.com','youtube.com','instagram.com','vault.com')))


def find_careers(url,links):
    result=[]
    for href,label,attrs in links:
        if href.startswith(('#','mailto:','tel:','javascript:')):continue
        absolute=urljoin(url,href)
        if re.search(r'privacy|cookie|/blog/|student.?loan|\.(?:pdf|css|js)$',urlsplit(absolute).path,re.I):continue
        if useful_url(absolute) and re.search(r'career|\bjobs?\b|join (?:our )?team|employment opportunities|work with us',urlsplit(href).path+' '+label,re.I):
            if absolute not in result:result.append(absolute)
    return result


def map_one(company,root=ROOT,get=get_page):
    stamp=now();result=dict(company_id=company['id'],attempted_at=stamp,input_url=None,status='UNKNOWN',careers_url=None,provider=None,evidence='',error=None)
    if company.get('configured_sources'):
        result.update(status='EXISTING_CONFIG',careers_url=company['configured_sources'][0]['careers_url'],
                      provider=company['configured_sources'][0]['ats_type'],evidence=json.dumps(dict(source_ids=[s['id'] for s in company['configured_sources']],note='Existing source inventory health, not URL presence, determines coverage')))
        return result
    domain=domain_key(company.get('domain',''))
    profile=company.get('profile_url')
    try:
        if not domain and profile:
            final,html,links,sha=get(profile,root)
            # Use the publisher's explicit website field, not arbitrary external links.
            websites=[urljoin(final,href) for href,label,attrs in links if useful_url(urljoin(final,href)) and re.search(r'visit (?:the )?website|company website|^website$|web.?site',label+' '+str(attrs.get('class','')),re.I)]
            if websites:domain=domain_key(websites[0])
        if not domain:
            result.update(status='NO_VERIFIED_DOMAIN',evidence='No official non-ATS domain or explicit publisher website field in source datasets')
            return result
        homepage='https://'+domain;result['input_url']=homepage
        final,html,links,sha=get(homepage,root);careers=find_careers(final,links)
        if not careers:
            result.update(status='UNKNOWN',evidence=json.dumps(dict(homepage_sha256=sha,note='No careers link in bounded public homepage inspection; not proof of absence')));return result
        primary=careers[0];provider,identifier=detect(primary)
        found=[];programs=[]
        if provider!='custom':found.append(dict(ats_type=provider,ats_identifier=identifier,url=primary))
        elif 'myjobs.adp.com' in primary:found.append(dict(ats_type='adp',ats_identifier=urlsplit(primary).path.strip('/').split('/')[0],url=primary))
        else:
            career_final,career_html,career_links,career_sha=get(primary,root)
            for href,label,_ in career_links:
                program_url=urljoin(career_final,href)
                authoritative_program=(domain_key(program_url)==domain_key(career_final) or detect(program_url)[0]!='custom')
                if authoritative_program and useful_url(program_url) and re.search(r'internship program|campus program|summer intern|students and graduates',label,re.I) and re.search(r'intern|student|campus',urlsplit(program_url).path,re.I):
                    try:
                        program_final,program_html,_,program_sha=get(program_url,root)
                        if re.search(r'undergraduate|rising seniors|bachelor',program_html,re.I) and re.search(r'internship',program_html,re.I):
                            programs.append(dict(url=program_final,sha256=program_sha,rule='Officially linked student-program page with undergraduate and internship evidence'))
                    except Exception:pass
                    if len(programs)>=2:break
            candidates=[urljoin(career_final,href) for href,_,_ in career_links]
            candidates+=re.findall(r'https://(?:job-boards\.greenhouse\.io|boards\.greenhouse\.io|jobs\.lever\.co|jobs\.ashbyhq\.com|careers\.smartrecruiters\.com|[A-Za-z0-9-]+\.wd\d+\.myworkdayjobs\.com|[A-Za-z0-9-]+\.icims\.com)/[^\s"\'<>,]+',career_html)
            for url in candidates:
                ats,ident=detect(url)
                if ats=='workday':
                    try:configuration(dict(careers_url=url))
                    except SourceError:continue
                if ats!='custom' and ident and not any((x['ats_type'],x['ats_identifier'])==(ats,ident) for x in found):found.append(dict(ats_type=ats,ats_identifier=ident,url=url))
            for d in discover_public_sources(career_final,career_html,career_links):
                if not any((x['ats_type'],x['ats_identifier'])==(d['provider'],d['identifier']) for x in found):found.append(dict(ats_type=d['provider'],ats_identifier=d['identifier'],url=d['url']))
            if not found:
                hint,_=provider_hint(career_final,career_html)
                found=[dict(ats_type='custom',ats_identifier='',url=career_final,provider_hint=hint)]
        result.update(status='MAPPED',careers_url=primary,provider=found[0].get('provider_hint') or found[0]['ats_type'],
                      evidence=json.dumps(dict(homepage_sha256=sha,official_linked_careers=careers,sources=found,domain=domain,programs=programs)))
    except Exception as exc:
        result.update(status='MAPPING_FAILED',error=str(exc))
    return result


def run_mapping(db,root=ROOT,workers=6,pending_only=False):
    from .db import canonical_id
    already={canonical_id(db,r[0]) for r in db.execute('SELECT DISTINCT company_id FROM career_mapping_attempts')} if pending_only else set()
    companies=[]
    for row in db.execute('SELECT * FROM companies WHERE canonical_company_id IS NULL AND active=1 AND must_cover=1 ORDER BY coverage_tier,id'):
        if row['id'] in already:continue
        item=dict(row)
        item['configured_sources']=[dict(s) for s in db.execute('SELECT s.* FROM sources s JOIN companies c ON c.id=s.company_id WHERE COALESCE(c.canonical_company_id,c.id)=? AND s.active=1 AND (s.careers_url IS NOT NULL OR s.ats_identifier<>\'\')',(row['id'],))]
        profile=db.execute("SELECT r.metadata FROM company_sources s JOIN raw_organizations r ON r.id=s.raw_organization_id WHERE s.company_id=? AND r.dataset_id IN ('vault_consulting','vault_banking') ORDER BY r.id LIMIT 1",(row['id'],)).fetchone()
        item['profile_url']=json.loads(profile[0]).get('profile_url') if profile else None
        companies.append(item)
    completed=0;counts={}
    with ThreadPoolExecutor(max_workers=max(1,min(workers,8))) as pool:
        futures={pool.submit(map_one,c,root):c for c in companies}
        for future in as_completed(futures):
            row=future.result();counts[row['status']]=counts.get(row['status'],0)+1
            with db:
                db.execute('INSERT INTO career_mapping_attempts('+','.join(row)+') VALUES('+','.join('?' for _ in row)+')',list(row.values()))
                if row['status']=='MAPPED':
                    meta=json.loads(row['evidence'])
                    db.execute('UPDATE companies SET domain=COALESCE(domain,?),careers_url=? WHERE id=?',(meta['domain'],row['careers_url'],row['company_id']))
                    for s in meta['sources']:
                        sid=source(db,row['company_id'],s['ats_type'],s['ats_identifier'],s['url'])
                        db.execute('UPDATE sources SET active=1 WHERE id=? AND NOT EXISTS(SELECT 1 FROM source_equivalences WHERE source_id=?)',(sid,sid))
                    from .universe import evidence
                    for program in meta.get('programs',[]):evidence(db,row['company_id'],'OFFICIAL_UNDERGRAD_PROGRAM',program['url'],program['url'],program)
            completed+=1
            if completed%25==0 or completed==len(companies):print(json.dumps(dict(mapping_completed=completed,total_must_cover=len(companies))),flush=True)
    return counts

def discover_public_sources(url,html,links):
    """Extract known public providers from linked/embedded recruiting configuration."""
    import html as html_module
    from .recruiting_discovery import board_identity
    candidates=[url]+[urljoin(url,h) for h,_,_ in links]
    candidates+=re.findall(r'https://(?:job-boards\.greenhouse\.io|boards\.greenhouse\.io|boards-api\.greenhouse\.io|jobs\.lever\.co|jobs\.ashbyhq\.com|careers\.smartrecruiters\.com|[A-Za-z0-9-]+\.wd\d+\.myworkdayjobs\.com|[A-Za-z0-9-]+\.icims\.com)/[^\s"\'<>,]+',html_module.unescape(html))
    result={}
    for u in candidates:
        try:p,i,board=board_identity(u)
        except Exception:continue
        if p in ADAPTERS and i:result[(p,i.casefold())]=dict(provider=p,identifier=i,url=board,evidence_url=url,method='Explicit official link or embedded public board URL')
    if 'data-apibaseurl' in html:
        try:
            from .sources.oracle import discover_configuration
            board=discover_configuration(html,url);result[('oracle',board)]=dict(provider='oracle',identifier=board,url=board,evidence_url=url,method='Verified Oracle vanity metadata')
        except SourceError:pass
    if 'widgetApiEndpoint' in html and ('phApp' in html):
        try:
            from .sources.phenom import configuration as phenom_configuration
            cfg=phenom_configuration(html);base=canonical_url(cfg['baseUrl'])
            if urlsplit(base).hostname!=urlsplit(url).hostname:raise ValueError('Phenom base host differs from inspected recruiting site')
            result[('phenom',base.casefold())]=dict(provider='phenom',identifier=base,url=base,evidence_url=url,method='Public Phenom configured base URL, locale and widget endpoint; full census still required',contract=dict(endpoint=cfg['widgetApiEndpoint'],locale=cfg['locale'],country=cfg['country']))
        except (SourceError,KeyError,ValueError):pass
    config=re.search(r'<code[^>]+id=["\']pcsx-data["\'][^>]*>(.*?)</code>',html,re.S|re.I)
    if config:
        try:
            data=json.loads(html_module.unescape(config[1]));domain=data['domain']
            if not isinstance(domain,str) or not re.fullmatch(r'[a-zA-Z0-9.-]+',domain) or '.' not in domain:raise ValueError('Invalid public Eightfold domain')
            base='https://'+urlsplit(url).netloc+'/careers'
            identity=urlsplit(base).hostname.lower()+'|'+domain
            result[('eightfold',identity.casefold())]=dict(provider='eightfold',identifier=identity,url=base,evidence_url=url,method='Explicit pcsx-data JSON domain on inspected official recruiting publication')
        except (ValueError,KeyError,TypeError):pass
    if re.search(r'id=["\']search-results["\']',html) and re.search(r'talentbrew|tbcdn',html,re.I):result[('talentbrew',url)]=dict(provider='talentbrew',identifier=url,url=url,evidence_url=url,method='Public TalentBrew search configuration; full census still required')
    return list(result.values())
