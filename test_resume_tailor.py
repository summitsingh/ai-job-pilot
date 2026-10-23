"""Resume layout, truthfulness, annotations, QA and overflow checks."""
import contextlib
import copy
import io
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

import resume_tailor as resume

SAMPLE = Path(__file__).parent / 'examples/sample_resume.json'


class ResumeTests(unittest.TestCase):
    def setUp(self):
        self.base = resume.load_data(SAMPLE)
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.pdf = Path(self.temp.name) / 'resume.pdf'

    def build(self, profile='platform', base=None):
        return resume.build_resume(base or self.base, profile, self.pdf)

    def test_pdf_and_links(self):
        report = self.build()
        self.assertTrue(self.pdf.exists())
        self.assertEqual(report['pages'], 1)
        self.assertEqual(resume.pdf_pages(self.pdf), 1)
        self.assertLessEqual(report['max_y'], 279.4 - 13.5)
        self.assertEqual(resume.qa_pdf(self.pdf, self.base, report['links'], []), [])
        self.assertIn('https://beacondns.example.com', resume.pdf_links(self.pdf))
        self.assertIn(self.base['publications'][0]['url'], resume.pdf_links(self.pdf))
        self.assertIn('mailto:' + self.base['email'], resume.pdf_links(self.pdf))

    def test_profile_order(self):
        first, second = [b['text'] for b in self.base['experience'][0]['bullets'][:2]]
        self.build('platform')
        platform = resume.normalized(resume.extract_text(self.pdf))
        self.build('observability')
        observability = resume.normalized(resume.extract_text(self.pdf))
        self.assertLess(platform.index(first), platform.index(second))
        self.assertLess(observability.index(second), observability.index(first))

    def test_pii(self):
        base = copy.deepcopy(self.base)
        base['summaries']['platform'] += ' Confidential fictional phrase.'
        report = self.build(base=base)
        failures = resume.qa_pdf(self.pdf, base, report['links'], ['CONFIDENTIAL FICTIONAL PHRASE'])
        self.assertTrue(any(f.startswith('pii:') for f in failures))

    def test_cover_letter(self):
        note = 'Thank you for the fictional platform discussion at Quartz Analytics.'
        report = resume.build_cover_letter(self.base, 'Quartz Analytics', note, self.pdf, 'platform')
        self.assertIn(note, resume.normalized(resume.extract_text(self.pdf)))
        self.assertEqual(resume.qa_pdf(self.pdf, None, report['links'], []), [])

    def test_overflow(self):
        base = copy.deepcopy(self.base)
        base['experience'][0]['bullets'] *= 50
        with self.assertRaisesRegex(ValueError, 'cannot fit'):
            self.build(base=base)
        self.assertFalse(self.pdf.exists())

    def test_auto_fit_uses_tighter_parameters(self):
        base = copy.deepcopy(self.base)
        base['experience'][0]['bullets'] *= 6
        report = self.build(base=base)
        self.assertLess(report['parameters']['body_size'], 9.0)
        self.assertGreaterEqual(report['parameters']['body_size'], 8.0)
        self.assertEqual(resume.qa_pdf(self.pdf, base, report['links'], []), [])

    def test_yaml_base(self):
        try:
            import yaml
        except ImportError:
            self.skipTest('PyYAML not installed')
        path = Path(self.temp.name) / 'base.yaml'
        path.write_text(yaml.safe_dump(self.base), encoding='utf-8')
        self.assertEqual(resume.load_data(path), self.base)

    def test_stdlib_extraction(self):
        report = self.build()
        with patch('resume_tailor.shutil.which', return_value=None):
            text = resume.extract_text(self.pdf)
            self.assertIn(self.base['name'], text)
            self.assertEqual(resume.qa_pdf(self.pdf, self.base, report['links'], []), [])

    def test_truthfulness_and_missing_link(self):
        report = self.build()
        changed = copy.deepcopy(self.base)
        changed['name'] = 'Fictional Applicant'
        failures = resume.qa_pdf(self.pdf, changed, ['https://missing.example.com'], [])
        self.assertTrue(any(f.startswith('text:') for f in failures))
        self.assertTrue(any(f.startswith('truthfulness:') for f in failures))
        self.assertTrue(any(f.startswith('links:') for f in failures))

    def test_jd_and_stable_order(self):
        name, profile = resume.profile_for_jd(self.base, 'observability telemetry cascade')
        self.assertEqual(name, 'observability')
        self.assertEqual(profile['emphasis_tags'][0], 'observability')
        custom = dict(summary='default', skill_order=[], project_order=[], emphasis_tags=[])
        tailored = resume.tailor_resume(self.base, custom)
        self.assertEqual(tailored['skills'], self.base['skills'])
        self.assertEqual(tailored['experience'], self.base['experience'])
        with self.assertRaises(KeyError):
            resume.tailor_resume(self.base, dict(custom, summary='missing'))

    def test_cli_qa_failure(self):
        pii = Path(self.temp.name) / 'pii.json'
        pii.write_text(json.dumps(['Alex Rivera']))
        with contextlib.redirect_stdout(io.StringIO()) as output:
            status = resume.main(['build', '--base', str(SAMPLE), '--profile', 'platform',
                                  '--out', str(self.pdf), '--pii-file', str(pii)])
        self.assertEqual(status, 1)
        self.assertIn('FAIL pii:', output.getvalue())


if __name__ == '__main__':
    unittest.main()
