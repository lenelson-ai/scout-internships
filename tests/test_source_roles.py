import unittest
from scout.source_roles import evaluate
class SourceRoleTests(unittest.TestCase):
 def s(self,role,status):return dict(source_role=role,status=status)
 def test_legacy_failure_does_not_block(self):
  self.assertTrue(evaluate([self.s('AUTHORITATIVE','MONITORED'),self.s('LEGACY','FAILING')])['healthy'])
 def test_required_campus_failure_blocks(self):
  self.assertEqual(evaluate([self.s('AUTHORITATIVE','MONITORED'),self.s('AUTHORITATIVE','FAILING')])['monitoring_state'],'PARTIALLY_MONITORED')
 def test_duplicate_not_required(self):
  self.assertTrue(evaluate([self.s('AUTHORITATIVE','MONITORED'),self.s('DUPLICATE','FAILING')])['healthy'])
 def test_unknown_scope_blocks(self):
  self.assertFalse(evaluate([self.s('AUTHORITATIVE','MONITORED'),self.s('UNKNOWN','MONITORED')])['healthy'])
 def test_html_not_inventory(self):
  self.assertFalse(evaluate([self.s('AUTHORITATIVE','REACHABLE_PORTAL_ONLY')])['healthy'])
 def test_no_authority_blocks(self):
  self.assertFalse(evaluate([self.s('SUPPLEMENTAL','MONITORED')])['healthy'])

class ScopeValidationTests(unittest.TestCase):
 def test_scope_requires_documented_same_employer_boards(self):
  from scout.db import connect,company,source
  from scout.source_roles import set_role,review_scope
  db=connect(':memory:')
  try:
   cid=company(db,'Example');sid=source(db,cid,'greenhouse','example')
   with self.assertRaises(ValueError):review_scope(db,cid,[sid],dict(official_url='https://example.com/careers',scope_reason='Full board'))
   set_role(db,sid,'AUTHORITATIVE',dict(official_url='https://example.com/careers',reason='Official linked board'))
   review_scope(db,cid,[sid],dict(official_url='https://example.com/careers',scope_reason='Full board'))
   self.assertEqual(db.execute('select state from recruiting_scope_reviews').fetchone()[0],'VALIDATED')
  finally:db.close()
