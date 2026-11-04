"""Evidence labels distinguish current undergraduate eligibility from prior qualifications."""
import re
from .model import normalize

def eligibility(job):
 title=normalize(job.title);desc=normalize(job.description)
 if re.search(r'\b(skillbridge|military fellowship|veteran fellowship)\b',title):return dict(status='RESTRICTED',reason='Explicit military transition program title')
 # A completed bachelors degree preceding an MBA requirement is not current
 # undergraduate admission. Optional/boilerplate MBA mentions are not restrictions.
 clean=re.sub(r'\b(?:not (?:an? |for )?|no )(?:mba|phd|master s|masters)(?: degree| students?| program| required)?\b','',desc)
 current_ug=bool(re.search(r'(?:pursuing|enrolled|working toward|seeking|completing|studying).{0,110}(?:bachelor|undergraduate|baccalaureate)|(?:bachelor|undergraduate).{0,100}(?:expected|student|enroll|pursu)|rising (?:junior|senior)',clean))
 alternatives=bool(re.search(r'(?:bachelor s?|undergraduate).{0,25}(?:or|and).{0,25}(?:master s?|masters|mba)|(?:master s?|masters|mba).{0,25}(?:or|and).{0,25}bachelor',clean))
 graduate_title=bool(re.search(r'\b(mba|phd|doctoral|masters|master s)\b',title))
 graduate_requirement=bool(re.search(r'(?:graduate|master s|masters|mba|phd) (?:students|degree) (?:only|required)|(?:enrolled in|pursuing|working toward|candidate for|enrollment in) (?:an? |the |full time |an? full time )?(?:mba|master s|masters|doctoral|phd)|must (?:be|have|hold) (?:an? )?(?:mba|master s|masters|phd)|(?:an? )mba degree.{0,110}graduat|(?:mba|master s|phd) degree (?:is )?required',clean))
 if graduate_title or graduate_requirement and not alternatives:return dict(status='VERIFIED_GRADUATE_ONLY',reason='Explicit graduate title/current degree requirement; a prior undergraduate qualification is insufficient')
 if current_ug:return dict(status='VERIFIED_UNDERGRADUATE',reason='Employer explicitly accepts current undergraduate students/degree candidates')
 if re.search(r'\b(intern|internship|summer analyst)\b',title):return dict(status='LIKELY_UNDERGRADUATE',reason='Internship title; degree requirement not verified')
 return dict(status='UNCERTAIN',reason='No conclusive current undergraduate or restricted eligibility evidence')
