"""Offline interview reminder and storage checks."""
import contextlib
import io
import json
from pathlib import Path
import tempfile
import unittest

import interview_tracker as tracker


class InterviewTests(unittest.TestCase):
    def setUp(self):
        self.store = {'events': []}

    def interview(self, date='2026-10-20', company='Northwind Systems'):
        return tracker.add_interview(self.store, company, 'Platform Engineer',
                                     'Technical', date, 'alex.rivera@example.com')

    def test_thank_you(self):
        event = self.interview()
        self.assertEqual(tracker.due_reminders(self.store, '2026-10-22')[0]['kind'], 'thank_you')
        tracker.set_outcome(self.store, event['id'], 'thanked')
        self.assertEqual(tracker.due_reminders(self.store, '2026-10-22'), [])

    def test_feedback(self):
        event = self.interview()
        tracker.set_outcome(self.store, event['id'], 'rejected')
        self.assertEqual(tracker.due_reminders(self.store, '2026-10-22')[0]['kind'], 'feedback')
        tracker.set_outcome(self.store, event['id'], 'feedback_requested')
        self.assertEqual(tracker.due_reminders(self.store, '2026-10-22'), [])

    def test_application_rejection(self):
        tracker.add_application_rejection(self.store, 'Bluebird Travel', 'Platform Engineer', '2026-10-20')
        self.assertEqual(tracker.due_reminders(self.store, '2026-10-22'), [])

    def test_future(self):
        self.interview('2026-11-01')
        self.assertEqual(tracker.due_reminders(self.store, '2026-10-22'), [])

    def test_latest_per_process(self):
        self.interview()
        latest = self.interview('2026-10-21')
        tracker.set_outcome(self.store, latest['id'], 'thanked')
        self.assertEqual(tracker.due_reminders(self.store, '2026-10-22'), [])
        self.interview(company='Helios Health')
        self.assertEqual(len(tracker.due_reminders(self.store, '2026-10-22')), 1)

    def test_store_and_cli(self):
        with tempfile.TemporaryDirectory() as temp:
            path = Path(temp) / 'interviews.json'
            self.assertEqual(tracker.load_store(path), {'events': []})
            self.interview()
            tracker.save_store(path, self.store)
            self.assertEqual(tracker.load_store(path), self.store)
            with contextlib.redirect_stdout(io.StringIO()) as output:
                self.assertEqual(tracker.main(['--store', str(path), 'due', '--today', '2026-10-22']), 0)
            self.assertEqual(len(json.loads(output.getvalue())), 1)
            self.assertEqual(list(Path(temp).iterdir()), [path])

    def test_invalid_values(self):
        event = self.interview()
        with self.assertRaises(ValueError):
            tracker.set_outcome(self.store, event['id'], 'unknown')
        with self.assertRaises(ValueError):
            self.interview('not-a-date')


if __name__ == '__main__':
    unittest.main()
