import unittest
from scout.external_benchmark import normalize,match_score
from scout.sources.oracle import discover_configuration
from scout.sources.base import SourceError
class ExternalBenchmarkTests(unittest.TestCase):
 def test_preserve_dates_and_explicit_duplicates(self):
  rows=normalize('1. Firm — Finance Intern 2027 — January 1, 2026\n2. Firm — Finance Intern 2027 — January 1, 2026 — Duplicate tracker entry\n100. Firm — Other Intern — 23 hours ago')
  self.assertEqual(rows[0]['opportunity_key'],rows[1]['opportunity_key']);self.assertEqual(rows[0]['listed_opening_date'],'2026-01-01');self.assertIsNone(rows[2]['listed_opening_date']);self.assertNotIn('first_seen_at',rows[0])
 def test_unconfirmed_repeated_title_not_silently_merged(self):
  r=normalize('107. Firm — IT Intern — 4 days ago\n111. Firm — IT Intern — 1 month ago')
  self.assertNotEqual(r[0]['opportunity_key'],r[1]['opportunity_key']);self.assertIn('possible_duplicate_group',r[1])
 def test_no_year_mismatch_and_no_generic_program_exact_match(self):
  b=dict(program='Summer Analyst 2027',recruiting_years=['2027'],location=None)
  self.assertEqual(match_score(b,dict(title='Summer Analyst 2028')),0);self.assertLess(match_score(b,dict(title='Summer Analyst 2027')),0.79)
 def test_vanity_metadata_validation(self):
  html='<base data-apibaseurl="https://tenant.oraclecloud.com:443" data-vanitybaseurl="https://careers.example.com/" data-sitenumber="CX_1">'
  self.assertIn('/sites/CX_1',discover_configuration(html,'https://careers.example.com/jobs'))
  with self.assertRaises(SourceError):discover_configuration(html.replace('tenant.oraclecloud.com','evil.example'),'https://careers.example.com')
  with self.assertRaises(SourceError):discover_configuration(html,'https://other.example')
 def test_distinct_programs_never_merged_by_employer(self):
  r=normalize('1. D.E. Shaw — Fundamental Research Analyst Intern, Summer 2027 — January 9, 2026\n2. D.E. Shaw — Investment Management Intern, Summer 2027 — January 9, 2026\n3. DRW — Venture Capital Analyst Intern — July 13, 2026\n4. DRW — Quantitative Trading Analyst Intern — July 13, 2026')
  self.assertEqual(len({x['opportunity_key'] for x in r}),4)
 def test_future_graduation_year_not_program_year(self):
  r=normalize('1. NERA — Summer Internship, Summer 2028 Graduates — September 1, 2026')[0]
  self.assertEqual(r['graduation_years'],['2028']);self.assertEqual(r['recruiting_years'],[])
 def test_oracle_detail_identity_mismatch_fails_inventory(self):
  from scout.sources.oracle import fetch
  from urllib.parse import parse_qs,urlsplit
  def get(u):
   if '/recruitingCEJobRequisitionDetails/' in u:return dict(Id='different',Title='Finance Intern',ExternalDescriptionStr='Undergraduate finance internship')
   offset=int(parse_qs(urlsplit(u).query)['finder'][0].split('offset=')[1]);return dict(items=[dict(SiteNumber='CX_1',Offset=offset,TotalJobsCount=1,requisitionList=[dict(Id='1',Title='Finance Intern')])])
  with self.assertRaisesRegex(SourceError,'identity/title'):fetch(dict(careers_url='https://example.oraclecloud.com/en/sites/CX_1'),get)
 def test_offering_management_is_business_feed_candidate(self):
  from scout.public_site import business_candidate
  self.assertTrue(business_candidate(dict(title='Offering Management – Summer 2027 Intern',category='OTHER')))
  self.assertTrue(business_candidate(dict(title='Future IT Leaders - Summer 2027 Intern',category='OTHER')))

 def test_old_verification_cannot_remain_current_recall_evidence(self):
  from scout.external_benchmark import compare
  from scout.db import connect
  db=connect(':memory:');self.addCleanup(db.close)
  b=normalize('1. Example — Finance Intern 2027 — January 1, 2026')[0]
  r=compare(db,[b],{b['opportunity_key']:dict(status='VERIFIED_OPEN',eligible=True,verified_at='2020-01-01T00:00:00+00:00',evidence=[])})[0]
  self.assertEqual(r['verification']['status'],'VERIFICATION_STALE');self.assertFalse(r['verified_open_eligible'])
