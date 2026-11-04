"""Deterministic, explainable screening. No user preferences or LLM calls."""
import re
from .model import normalize
from .eligibility import eligibility

# Ordered specific-to-broad. A bare 'business' is never positive evidence.
RULES = [
    ("FINANCE", "Quantitative Finance", r"quantitative trad(?:ing|er)|trading intern|trader intern"),
    ("FINANCE", "Accounting & Assurance", r"tax|audit|auditing|financial assurance|assurance intern|assurance internship"),
    ("FINANCE", "Wealth Management", r"client advisory"),
    ("CONSULTING", "Transaction Advisory", r"transaction advisory|deal advisory|transaction services|valuation"),
    ("CONSULTING", "Strategy Consulting", r"strategy consult(?:ing|ant)?"),
    ("CONSULTING", "Operations Consulting", r"operations consult(?:ing|ant)?"),
    ("CONSULTING", "Management Consulting", r"management consult(?:ing|ant)?|associate consultant|consulting"),
    ("CONSULTING", "Advisory", r"advisory"),
    ("STRATEGY", "Corporate Development", r"corporate development|mergers (?:and )?acquisitions|m a"),
    ("STRATEGY", "Strategy & Operations", r"strategy and operations|strategy operations|strategic operations"),
    ("STRATEGY", "Business Transformation", r"business transformation|enterprise transformation"),
    ("STRATEGY", "Strategic Initiatives", r"strategic initiatives"),
    ("STRATEGY", "Corporate Strategy", r"corporate strategy|strategy and management|strategic planning|business planning|strategy"),
    ("FINANCE", "Strategic Finance", r"strategic finance"),
    ("FINANCE", "FP&A", r"fp a|financial planning (?:and )?analysis"),
    ("FINANCE", "Treasury", r"treasury|liquidity management"),
    ("FINANCE", "Investment Banking", r"investment banking"),
    ("FINANCE", "Commercial Banking", r"commercial banking|corporate banking|credit analyst"),
    ("FINANCE", "Asset Management", r"asset management|investment management|equity research|portfolio management"),
    ("FINANCE", "Wealth Management", r"wealth management|private bank(?:ing)?|financial advisor|client advisory"),
    ("FINANCE", "Capital Markets", r"capital markets|global markets|sales and trading"),
    ("FINANCE", "Finance Analytics", r"finance analytics|financial analytics|financial analysis|financial analyst"),
    ("FINANCE", "Risk", r"risk management|credit risk|market risk|enterprise risk|risk"),
    ("FINANCE", "Corporate Finance", r"office of the cfo|corporate finance|finance management|finance|financial"),
]


def match(pattern, value):
    return re.search(r"\b(?:" + pattern + r")\b", value) is not None


def classify(job):
    title, dept, desc = map(normalize, (job.title, job.department, job.description))
    reasons = []
    explicit = match(r"intern|interns|internship|internships|co op|coop|summer analyst", title)
    associate = match(r"summer associate", title)
    state=eligibility(job)['status']
    graduate=state=='VERIFIED_GRADUATE_ONLY'
    undergrad=state=='VERIFIED_UNDERGRADUATE'
    program=match(r'internship|summer program|summer associate program|student program|temporary|[0-9]+ week|[0-9]+ weeks',desc)
    student_associate=match(r'undergraduate associate|student associate',title)
    if explicit:
        internship = .98
    elif associate:
        internship = .9 if undergrad or graduate and program else .55
    elif student_associate:
        internship=.9 if undergrad and program else .55
    elif match(r"university|campus|undergraduate|internship|co op", title + " " + job.employment_type.lower()):
        internship = .55
    elif match(r"internship program|summer internship|undergraduate students", desc):
        internship = .45
    else:
        internship = .05
    experienced = match(r"senior|sr|director|vice president|principal|manager", title) and not explicit
    if experienced:
        internship = .05
    if graduate:
        reasons.append("graduate eligibility needs review")

    category, sub, confidence = "OTHER", None, .2
    # Obvious unrelated titles are not rescued by generic company boilerplate.
    unrelated = match(r"software|mechanical|electrical|civil|chemical|field engineer|nursing|graphic design|quality assurance|test engineer", title)
    # Clinical training roles are clearly outside target functions. Keep explicit
    # finance/strategy/business roles in healthcare eligible for normal screening.
    clinical=match(r"pharmacy|pharmacist|clinical|patient care|dental",title)
    explicit_target=any(match(pattern,title) for _,_,pattern in RULES)
    unrelated=unrelated or (clinical and not explicit_target)
    if not unrelated:
        for cat, label, pattern in RULES:
            if match(pattern, title):
                category, sub, confidence = cat, label, .92
                break
        if category == "OTHER":
            for cat, label, pattern in RULES:
                if match(pattern, dept):
                    category, sub, confidence = cat, label, .72
                    break
        if category == "OTHER":
            hits = [(c, s) for c, s, p in RULES[:-1] if match(p, desc)]
            if len(hits) >= 2:
                category, sub, confidence = *hits[0], .5
    # Generic marketing/product strategy and physical safety/security risk are ambiguous.
    if match(r"marketing|product|cyber|security|safety|insurance underwriting", title):
        confidence = min(confidence, .5)
        reasons.append("function context needs review")
    countries = set(job.countries)
    us = bool(countries & {"US", "USA", "UNITED STATES", "UNITED STATES OF AMERICA"})
    loc = " ".join(job.locations)
    states = r"AL|AK|AZ|AR|CA|CO|CT|DE|DC|FL|GA|HI|ID|IL|IN|IA|KS|KY|LA|ME|MD|MA|MI|MN|MS|MO|MT|NE|NV|NH|NJ|NM|NY|NC|ND|OH|OK|OR|PA|RI|SC|SD|TN|TX|UT|VT|VA|WA|WV|WI|WY"
    named_us=bool(re.search(r"\b(?:United States|USA|U\.S\.)\b",loc,re.I))
    postal_us=not countries and bool(re.search(r",\s*(?:"+states+r")\b",loc,re.I))
    # Written-out state names after a city comma are location evidence too.
    # Explicit foreign country metadata still takes precedence over state inference.
    full_states=r"Alabama|Alaska|Arizona|Arkansas|California|Colorado|Connecticut|Delaware|District of Columbia|Florida|Georgia|Hawaii|Idaho|Illinois|Indiana|Iowa|Kansas|Kentucky|Louisiana|Maine|Maryland|Massachusetts|Michigan|Minnesota|Mississippi|Missouri|Montana|Nebraska|Nevada|New Hampshire|New Jersey|New Mexico|New York|North Carolina|North Dakota|Ohio|Oklahoma|Oregon|Pennsylvania|Rhode Island|South Carolina|South Dakota|Tennessee|Texas|Utah|Vermont|Virginia|Washington|West Virginia|Wisconsin|Wyoming"
    # Georgia is also a country; its bare full name is not US proof.
    full_states=full_states.replace("|Georgia","")
    full_state_us=not countries and any(re.search(r",\s*(?:"+full_states+r")\s*$",v,re.I) for v in job.locations)
    us=us or named_us or postal_us or full_state_us
    geography = "US" if us else "NON_US" if countries else "UNKNOWN"
    if geography == "UNKNOWN":
        reasons.append("US eligibility unknown")
    if category == "OTHER" or confidence < .7:
        reasons.append("function needs classification")
    if internship < .8:
        reasons.append("internship eligibility uncertain")
    # Retain ambiguous internships, but reject clear unrelated/experienced/non-US roles.
    retain = internship >= .4 and geography != "NON_US" and not unrelated
    return dict(function=category, sub_function=sub, classification_confidence=confidence,
                internship_confidence=internship, geography_status=geography,
                needs_review=bool(reasons), classification_reasons=reasons, retain=retain)
