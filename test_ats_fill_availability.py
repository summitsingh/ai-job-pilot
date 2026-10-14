import json
import tempfile
import unittest
from contextlib import redirect_stdout
from io import StringIO
from unittest.mock import patch

import ats_fill
from ats_fill import PostingUnavailableError, unavailable_posting_marker


class PostingAvailabilityTests(unittest.TestCase):
    def test_job_not_found_page_is_identified_as_unavailable(self):
        page_text = "Job not found. The job you requested was not found."
        self.assertEqual(unavailable_posting_marker(page_text), "job not found")

    def test_live_posting_text_is_not_marked_unavailable(self):
        page_text = "Senior Platform Engineer. Applications are open."
        self.assertIsNone(unavailable_posting_marker(page_text))

    def test_unavailable_marker_matching_ignores_case_and_spacing(self):
        self.assertEqual(
            unavailable_posting_marker("This position\nHAS BEEN FILLED"),
            "position has been filled",
        )

    def test_empty_page_text_has_no_marker(self):
        self.assertIsNone(unavailable_posting_marker(None))

    def test_unavailable_error_is_a_runtime_error(self):
        self.assertIsInstance(PostingUnavailableError("job not found"), RuntimeError)

    def test_dead_posting_is_blocked_before_mapping_or_submit(self):
        with tempfile.TemporaryDirectory() as workdir:
            stdout = StringIO()
            with patch.object(ats_fill, "dump_schema", return_value={"fields": []}), \
                 patch.object(ats_fill, "cdp_ok", return_value={"text": "Job not found"}), \
                 patch.object(ats_fill, "acquire_lock"), \
                 patch.object(ats_fill, "release_lock"), \
                 patch.object(ats_fill, "map_fields") as map_fields, \
                 patch.object(ats_fill.sys, "argv", [
                     "ats_fill.py", "--url", "https://example.test/job",
                     "--port", "9226", "--workdir", workdir,
                 ]), redirect_stdout(stdout):
                ats_fill.main()

            result = json.loads(stdout.getvalue())
            self.assertEqual(result["status"], "blocked")
            self.assertIn("posting unavailable: job not found", result["notes"])
            map_fields.assert_not_called()

    def test_empty_schema_without_dead_posting_marker_stays_fail_closed(self):
        with tempfile.TemporaryDirectory() as workdir:
            stdout = StringIO()
            with patch.object(ats_fill, "dump_schema", return_value={"fields": []}), \
                 patch.object(ats_fill, "cdp_ok", return_value={"text": "Application form"}), \
                 patch.object(ats_fill, "acquire_lock"), \
                 patch.object(ats_fill, "release_lock"), \
                 patch.object(ats_fill.sys, "argv", [
                     "ats_fill.py", "--url", "https://example.test/job",
                     "--port", "9226", "--workdir", workdir,
                 ]), redirect_stdout(stdout):
                ats_fill.main()

            result = json.loads(stdout.getvalue())
            self.assertEqual(result["status"], "failed")
            self.assertTrue(any("schema extraction returned zero fields" in note
                                for note in result["notes"]))


if __name__ == "__main__":
    unittest.main()
