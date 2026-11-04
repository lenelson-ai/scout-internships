from urllib.parse import urlsplit, parse_qs
from . import greenhouse, lever, ashby, smartrecruiters, workday, phenom, talentbrew, oracle, eightfold, icims
from .base import SourceError

ADAPTERS = {"greenhouse": greenhouse.fetch, "lever": lever.fetch,
            "ashby": ashby.fetch, "smartrecruiters": smartrecruiters.fetch, "workday": workday.fetch, "phenom":phenom.fetch, "talentbrew":talentbrew.fetch, "oracle":oracle.fetch, "eightfold":eightfold.fetch,"icims":icims.fetch}


def fetch(source):
    adapter = ADAPTERS.get(source["ats_type"])
    if not adapter:
        raise SourceError("ATS_CHANGED", f"No adapter for {source['ats_type']}; source requires configuration/implementation")
    return adapter(source)


def detect(url):
    """Conservative detection from actual URLs, never guessed company-name slugs."""
    p = urlsplit(url or "")
    host = (p.hostname or "").lower()
    parts = [x for x in p.path.split("/") if x]
    if '\\' in (url or '') or (parts and parts[0].casefold() in {'cdn-cgi','external-referrals','assets','static'}):return 'custom',''
    if host in {"boards.greenhouse.io", "job-boards.greenhouse.io"} and parts:
        if parts[0]=='embed':
            board=parse_qs(p.query).get('for',[''])[0]
            return ('greenhouse',board) if board else ('custom','')
        return "greenhouse", parts[0]
    if host == "boards-api.greenhouse.io" and "boards" in parts:
        return "greenhouse", parts[parts.index("boards") + 1]
    if host == "jobs.lever.co" and parts:
        return "lever", parts[0]
    if host == "jobs.ashbyhq.com" and parts:
        return "ashby", parts[0]
    if host in {"careers.smartrecruiters.com", "jobs.smartrecruiters.com"} and parts:
        return "smartrecruiters", parts[0]
    if host.endswith(".myworkdayjobs.com"):
        # Tenant and board both matter; locale is optional in URLs.
        board = [x for x in parts if not workday.LOCALE.fullmatch(x)]
        return "workday", host.split(".")[0] + "/" + (board[0] if board else "")
    if host.endswith('.myworkdaysite.com'):
        try:
            _,tenant,board=workday.configuration(dict(careers_url=url))
            return 'workday',tenant+'/'+board
        except SourceError:return 'custom',''
    if host.endswith('.oraclecloud.com') and 'sites' in parts:
        try:
            root,site=oracle.configuration(dict(careers_url=url))
            return 'oracle',root+'/hcmUI/CandidateExperience/en/sites/'+site
        except SourceError:return 'custom',''
    if host.endswith('.eightfold.ai') and parts and parts[0]=='careers':
        import re
        domain=parse_qs(p.query).get('domain',[''])[0]
        if re.fullmatch(r'[a-zA-Z0-9.-]+',domain) and '.' in domain:return 'eightfold',host+'|'+domain
    if host.endswith(".icims.com"):
        try:return 'icims',icims.configuration(dict(careers_url=url))[0]
        except SourceError:return 'custom',''
    if host=='myjobs.adp.com' and parts:
        return 'adp',parts[0]
    if "successfactors" in host:
        return "successfactors", parse_qs(p.query).get("company", [""])[0]
    return "custom", ""
