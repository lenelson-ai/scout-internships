"""Detail requests are local screening only; listing inventories remain unfiltered."""
from ..classify import classify

def needs_details(job,source):
 if source.get('detail_mode')=='all':return True
 known={str(i).casefold() for i in source.get('_legacy_ids',[])}
 if job.external_job_id.casefold() in known:return True
 decision=classify(job)
 return decision['retain'] and decision['internship_confidence']>=.4
