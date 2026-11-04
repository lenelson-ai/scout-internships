import unittest
from scout.model import Job
from scout.classify import classify
from scout.eligibility import eligibility
class ProgramTests(unittest.TestCase):
 def test_undergrad_summer_associate_can_be_confident_without_mba_boilerplate_penalty(self):
  j=Job(title='Summer Associate',description='Currently enrolled in a bachelor degree program. Join our 10 week summer program. MBA reimbursement is available to full time employees.')
  self.assertEqual(eligibility(j)['status'],'VERIFIED_UNDERGRADUATE');self.assertGreaterEqual(classify(j)['internship_confidence'],.8)
 def test_completed_undergraduate_degree_plus_mba_requirement_is_graduate(self):
  j=Job(title='Summer Associate',description='Basic qualifications: An undergraduate degree from an accredited institution. An MBA degree with a graduation date in 2028. Our summer associate program lasts 10 weeks.')
  self.assertEqual(eligibility(j)['status'],'VERIFIED_GRADUATE_ONLY');self.assertGreaterEqual(classify(j)['internship_confidence'],.8)
 def test_unknown_summer_associate_preserves_uncertainty(self):
  j=Job(title='Summer Associate',description='Join our team.');self.assertEqual(eligibility(j)['status'],'UNCERTAIN');self.assertLess(classify(j)['internship_confidence'],.8);self.assertTrue(classify(j)['retain'])
 def test_student_associate_requires_program_and_enrollment(self):
  for title in ('Undergraduate Associate','Student Associate'):
   j=Job(title=title,description='Currently pursuing a bachelors degree; paid temporary summer program.');self.assertGreaterEqual(classify(j)['internship_confidence'],.8)
  self.assertLess(classify(Job(title='Student Associate',description='Permanent full time staff position.'))['internship_confidence'],.8)
 def test_optional_masters_does_not_overrule_undergraduate_admission(self):
  j=Job(title='Finance Rotational Intern',description='Currently pursuing a bachelor or masters degree in finance.');self.assertEqual(eligibility(j)['status'],'VERIFIED_UNDERGRADUATE')
 def test_mba_requirement_not_bachelors_work_experience(self):
  j=Job(title='Investment Analyst Intern',description='Must be currently enrolled in an MBA program. A bachelor degree and two years of experience are required.');self.assertEqual(eligibility(j)['status'],'VERIFIED_GRADUATE_ONLY')
