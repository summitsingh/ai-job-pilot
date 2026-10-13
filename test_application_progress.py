import csv
import json
import tempfile
import unittest
from pathlib import Path
from application_progress import events_from, save, match_job, stage_for


class ProgressTests(unittest.TestCase):
    def setUp(self):
        self.apps = [
            {'Company': 'Acme', 'Title': 'Senior Engineer', 'Job URL': 'https://example.com/1', 'Status': 'Submitted'},
            {'Company': 'Acme', 'Title': 'Staff Engineer', 'Job URL': 'https://example.com/2', 'Status': 'Applied'},
            {'Company': 'Beta Labs', 'Title': 'Developer', 'Job URL': 'https://example.com/3', 'Status': 'Blocked'},
        ]

    def test_no_invented_match_for_same_company_roles(self):
        m = {'id': 'a', 'subject': 'Acme interview invitation', 'snippet': ''}
        self.assertIsNone(match_job(m, self.apps))
        m['subject'] = 'Acme Senior Engineer interview invitation'
        matched = match_job(m, self.apps)
        self.assertIsNotNone(matched)
        self.assertEqual(matched['Job URL'], 'https://example.com/1')

    def test_sender_email_domain_alone_does_not_match_company(self):
        msg = {'subject': 'Application update', 'from': 'recruiting@acme.example'}
        self.assertIsNone(match_job(msg, [self.apps[0]]))

    def test_sender_display_name_can_match_company(self):
        msg = {'subject': 'Application update',
               'from': 'Acme Recruiting <jobs@example.test>'}
        self.assertIs(match_job(msg, [self.apps[0]]), self.apps[0])

    def test_blocked_app_and_generic_emails_do_not_progress(self):
        msgs = [{'id': 'a', 'subject': 'Beta Labs interview invitation'},
                {'id': 'b', 'subject': 'Acme application update', 'snippet': ''}]
        events, unresolved = events_from(msgs, self.apps)
        self.assertEqual(events, [])
        self.assertEqual(len(unresolved), 1)

    def test_stage_priority_and_conservative_text(self):
        self.assertEqual(stage_for({'subject': 'Acme: we are not moving forward'}), 'rejected')
        self.assertEqual(stage_for({'subject': 'Acme interview invitation'}), 'interview')
        self.assertIsNone(stage_for({'subject': 'Acme application update'}))

    def test_save_creates_missing_parent_directory(self):
        with tempfile.TemporaryDirectory() as d:
            prefix = Path(d) / 'private' / 'nested' / 'progress'
            self.assertEqual(save(str(prefix), [], []), 0)
            self.assertTrue(Path(str(prefix) + '.json').is_file())

    def test_save_csv_json_idempotent(self):
        msg = {'id': 'm1', 'threadId': 't1', 'date': '2026-10-01',
               'subject': 'Acme Senior Engineer interview invitation',
               'from': 'recruiting@acme.example', 'snippet': 'Schedule interview'}
        events, _ = events_from([msg], self.apps)
        with tempfile.TemporaryDirectory() as d:
            prefix = str(Path(d) / 'progress')
            self.assertEqual(save(prefix, events, []), 1)
            self.assertEqual(save(prefix, events, []), 1)
            rows = json.loads(Path(prefix + '.json').read_text())
            with open(prefix + '.csv', newline='') as f:
                csv_rows = list(csv.DictReader(f))
            self.assertEqual(rows, csv_rows)
            self.assertEqual(rows[0]['job_url'], 'https://example.com/1')


if __name__ == '__main__':
    unittest.main()
