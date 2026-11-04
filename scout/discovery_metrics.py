"""Immutable observations from accepted complete inventories; no inferred posting clock."""
import json,hashlib
from dataclasses import asdict

def start_census(db,source,jobs,stamp,previous):
 keys=sorted(j.external_job_id or j.canonical_apply_url for j in jobs)
 return db.execute('insert into source_censuses(source_id,observed_at,previous_success_at,inventory_count,identity_sha256) values(?,?,?,?,?)',(source['id'],stamp,previous['finished_at'] if previous else None,len(jobs),hashlib.sha256(json.dumps(keys).encode()).hexdigest())).lastrowid

def observe(db,census,source,job,stamp,previous,prior,kind,decision,jid,known_job):
 if prior and prior['present'] and json.loads(prior['normalized_payload'])==asdict(job):return
 if prior:kind='REAPPEARED' if not prior['present'] else 'UPDATED_OR_REPOSTED'
 from .ingest import identity
 decision=dict(decision,observation=dict(title=job.title,locations=job.locations,countries=job.countries,provider=source['ats_type'],description_sha256=hashlib.sha256(job.description.encode()).hexdigest()))
 proof=getattr(job,'_publication_evidence',dict(raw=None,precision='UNVERIFIED_NORMALIZED',timezone_verified=False));proof=dict(proof,normalized=job.posted_at,posted_at_confidence=job.posted_at_confidence)
 db.execute('insert into discovery_events(census_id,source_id,identity,requisition,canonical_url,job_id,kind,observed_at,previous_success_at,classification,publication,known_job_before) values(?,?,?,?,?,?,?,?,?,?,?,?)',(census,source['id'],identity(job),job.external_job_id,job.canonical_apply_url,jid,kind,stamp,previous['finished_at'] if previous else None,json.dumps(decision),json.dumps(proof),int(known_job)))
