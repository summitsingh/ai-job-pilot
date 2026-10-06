#!/usr/bin/env python3
"""Offline tests for Batch C human review and setup helpers.

Run: python3 -m unittest test_human
No browser, model server, stdin, or network is required.
"""
import contextlib
import datetime
import io
import json
import os
import tempfile
import unittest
from unittest import mock

import job_queue


class HeartbeatTest(unittest.TestCase):
    def test_expiry_and_refresh(self):
        with tempfile.TemporaryDirectory() as d:
            q, c = os.path.join(d, "queue.json"), os.path.join(d, "claims.jsonl")
            url = "https://example.com/jobs/1"
            with open(q, "w") as f:
                json.dump([{"url": url}], f)
            job_queue.claim(q, c, url, "lane-a")
            old = (datetime.datetime.now(datetime.timezone.utc)
                   - datetime.timedelta(seconds=10)).isoformat()
            with open(c, "w") as f:
                f.write(json.dumps({"url": url, "lane": "lane-a",
                                    "claimed_at": old,
                                    "status": "in-progress"}) + "\n")
            self.assertIsNotNone(job_queue.next_job(q, c, "lane-b",
                                                    max_age_hours=0.001))
            self.assertTrue(job_queue.heartbeat(c, url, "lane-a"))
            self.assertIsNone(job_queue.next_job(q, c, "lane-b",
                                                 max_age_hours=0.001))
            self.assertEqual(len(job_queue.read_claims(c)[0]), 2)
            self.assertFalse(job_queue.heartbeat(c, url, "lane-b"))

    def test_requires_live_claim(self):
        with tempfile.TemporaryDirectory() as d:
            c = os.path.join(d, "claims.jsonl")
            self.assertFalse(job_queue.heartbeat(c, "u", "a"))
            job_queue.append_claim(c, {"url": "u", "lane": "a",
                                       "claimed_at": "2000-01-01T00:00:00Z",
                                       "status": "in-progress"})
            self.assertFalse(job_queue.heartbeat(c, "u", "a"))

    def test_cli_not_owner_exit(self):
        with tempfile.TemporaryDirectory() as d:
            with mock.patch("sys.argv", ["job_queue.py", "--claims",
                            os.path.join(d, "claims.jsonl"), "heartbeat",
                            "https://example.com/j", "--lane", "a"]):
                with contextlib.redirect_stdout(io.StringIO()) as out:
                    with self.assertRaises(SystemExit) as exc:
                        job_queue.main()
                self.assertEqual(exc.exception.code, 1)
                self.assertEqual(out.getvalue().strip(), "not-owner")


class FakeNotifier:
    def __init__(self, code):
        self.code = code
        self.messages = []
        self.waits = []

    def notify(self, message):
        self.messages.append(message)

    def wait_for_code(self, timeout_s):
        self.waits.append(timeout_s)
        return self.code


class CoordinatorTest(unittest.TestCase):
    def test_code(self):
        from codegate import coordinate
        n, entered, timed = FakeNotifier(" ABCD1234 \n"), [], []
        self.assertEqual(coordinate(n, 30, entered.append,
                                    lambda: timed.append(True)),
                         {"status": "code-entered"})
        self.assertEqual(entered, ["ABCD1234"])
        self.assertEqual(timed, [])
        self.assertEqual((len(n.messages), n.waits), (1, [30]))

    def test_invalid(self):
        from codegate import coordinate
        for code in ("", "short", "123456789", "1234 678", 12345678):
            n, entered, timed = FakeNotifier(code), [], []
            self.assertEqual(coordinate(n, 10, entered.append,
                                        lambda: timed.append(True)),
                             {"status": "invalid-code"})
            self.assertEqual((entered, timed, len(n.waits)), ([], [], 1))

    def test_timeout(self):
        from codegate import coordinate
        timed = []
        entered = mock.Mock()
        self.assertEqual(coordinate(FakeNotifier(None), 5, entered,
                                    lambda: timed.append(True)),
                         {"status": "timeout"})
        self.assertEqual(timed, [True])
        entered.assert_not_called()

    def test_timeout_requeues(self):
        from codegate import requeue_on_timeout
        with mock.patch("job_queue.requeue", return_value=True) as rq:
            self.assertTrue(requeue_on_timeout("queue", "claims", "url"))
            rq.assert_called_once_with("queue", "claims", "url",
                                       reason="code-gate timeout")

    def test_partial_input_respects_timeout(self):
        from codegate import CliNotifier
        stream = mock.Mock()
        stream.readline.side_effect = AssertionError("blocking readline")
        stream.fileno.return_value = 0
        with mock.patch("codegate.sys.stdin", stream), \
                mock.patch("codegate.select.select", side_effect=[([stream], [], []),
                                                                  ([], [], [])]), \
                mock.patch("os.read", return_value=b"ABCD"):
            self.assertIsNone(CliNotifier().wait_for_code(1))

    def test_cli_wait_and_timeout(self):
        from codegate import CliNotifier
        stream = mock.Mock()
        stream.fileno.return_value = 0
        with mock.patch("codegate.sys.stdin", stream), \
                mock.patch("codegate.select.select", return_value=([stream], [], [])) as wait, \
                mock.patch("codegate.os.read", return_value=b"ABCD1234\n"):
            self.assertEqual(CliNotifier().wait_for_code(4), "ABCD1234")
            self.assertEqual(wait.call_args.args[:3], ([stream], [], []))
            self.assertLessEqual(wait.call_args.args[3], 4)
        with mock.patch("codegate.select.select", return_value=([], [], [])):
            self.assertIsNone(CliNotifier().wait_for_code(0))
        with mock.patch("codegate.select.select", side_effect=OSError):
            self.assertIsNone(CliNotifier().wait_for_code(4))

    def test_telegram_stub(self):
        from codegate import TelegramNotifier
        for call in (lambda: TelegramNotifier().notify("prompt"),
                     lambda: TelegramNotifier().wait_for_code(10)):
            with self.assertRaises(NotImplementedError):
                call()


class FieldMapTest(unittest.TestCase):
    def test_table(self):
        from lane_greenhouse import format_field_map
        table = format_field_map({"map": [
            {"field": "name", "label": "Full name", "action": "fill",
             "value": "Example Applicant"},
            {"field": "extra", "action": "skip", "note": "no fact"},
            {"field": "agree", "action": "click", "value": False}],
            "unmapped": ["other"]})
        for text in ("Full name", "name", "Example Applicant", "SKIP",
                     "no fact", "False", "other"):
            self.assertIn(text, table)
        self.assertNotIn(chr(0x2014), table)


class WizardTest(unittest.TestCase):
    def test_plan_defaults(self):
        from setup_wizard import plan_setup
        with mock.patch("setup_wizard.allocate_lanes", side_effect=AssertionError("port probe")):
            cfg = plan_setup({"directory": "."})
        self.assertEqual(cfg["JOBPILOT_CDP_URL"], "127.0.0.1:9445")
        self.assertEqual(cfg["JOBPILOT_LANE"], "lane-1")
        self.assertEqual(cfg["TRACKER_BACKENDS"], "jsonl")
        self.assertTrue(cfg["JOBPILOT_QUEUE"].endswith("queue.json"))
        self.assertTrue(cfg["JOBPILOT_CLAIMS"].endswith("claims.jsonl"))

    def test_plan_no_secrets(self):
        from setup_wizard import plan_setup
        answers = {"backends": "jsonl,sheets,notion", "base_port": 9555,
                   "token": "secret", "sheet_id": "private",
                   "credentials": "private.json", "notion_db": "private",
                   "target_metros": "Remote (US)"}
        with mock.patch("setup_wizard.allocate_lanes", side_effect=AssertionError("port probe")):
            cfg = plan_setup(answers)
        self.assertEqual(cfg["JOBPILOT_CDP_URL"], "127.0.0.1:9555")
        self.assertEqual(cfg["JOBPILOT_TARGET_METROS"], "Remote (US)")
        self.assertEqual(cfg["TRACKER_BACKENDS"], "jsonl,sheets,notion")
        self.assertNotIn("private", json.dumps(cfg))
        self.assertNotIn("secret", json.dumps(cfg))

    def test_credential_exports_env_wins(self):
        import shlex
        from setup_wizard import credential_exports
        db = "0123456789abcdef0123456789abcdef"
        with mock.patch.dict(os.environ, {"TRACKER_NOTION_TOKEN": "env token'quote",
                                          "TRACKER_NOTION_DB": db}, clear=True):
            exports, problems = credential_exports(
                {"TRACKER_NOTION_TOKEN": "answer-token"}, ["notion"])
        self.assertEqual(problems, [])
        self.assertEqual(shlex.split(exports[0]),
                         ["export", "TRACKER_NOTION_TOKEN=env token'quote"])
        self.assertIn("01234567-89ab-cdef-0123-456789abcdef", exports[1])

    def test_credential_problems(self):
        from setup_wizard import credential_exports
        with mock.patch.dict(os.environ, {}, clear=True):
            _, problems = credential_exports(
                {"GOOGLE_APPLICATION_CREDENTIALS": "missing-example-file",
                 "TRACKER_SHEET_ID": "bad id"}, ["sheets", "notion"])
        self.assertEqual(len(problems), 4)

    def test_interactive_skip_queue(self):
        from setup_wizard import collect_answers
        defaults = {"queue": "queue.json", "claims": "claims.jsonl",
                    "backends": "jsonl", "target_metros": ""}
        with mock.patch("builtins.input", side_effect=["", "", "", "skip", "", ""]):
            answers = collect_answers(defaults)
        self.assertTrue(answers["skip_queue"])
        self.assertFalse(answers["launch"])
        self.assertEqual(answers["count"], "2")

    def test_invalid_backends(self):
        from setup_wizard import plan_setup
        with self.assertRaises(ValueError):
            plan_setup({"backends": "unknown"})

    def _run(self, d, *flags):
        from setup_wizard import main
        with mock.patch.dict(os.environ, {}, clear=True), \
                mock.patch("launch_browsers.port_free", return_value=True), \
                mock.patch("builtins.input", side_effect=AssertionError("stdin")), \
                contextlib.redirect_stdout(io.StringIO()):
            return main(["--yes", "--directory", d, *flags])

    def test_missing_facts_fail_closed(self):
        with tempfile.TemporaryDirectory() as d:
            self.assertEqual(self._run(d), 2)
            self.assertFalse(os.path.exists(os.path.join(d, "config.json")))

    def test_dry_run_no_writes(self):
        with tempfile.TemporaryDirectory() as d:
            with open(os.path.join(d, "facts.json"), "w") as f:
                json.dump({"first_name": "Example"}, f)
            self.assertEqual(self._run(d, "--dry-run"), 0)
            self.assertEqual(os.listdir(d), ["facts.json"])

    def test_existing_config_and_queue_preserved(self):
        with tempfile.TemporaryDirectory() as d:
            for name, data in (("facts.json", {}), ("config.json", {"keep": 1}),
                               ("queue.json", [{"url": "https://example.com/j"}])):
                with open(os.path.join(d, name), "w") as f:
                    json.dump(data, f)
            self.assertEqual(self._run(d), 2)
            self.assertEqual(self._run(d, "--force"), 0)
            with open(os.path.join(d, "queue.json")) as f:
                self.assertEqual(json.load(f), [{"url": "https://example.com/j"}])

    def test_environment_overrides_config(self):
        from setup_wizard import main
        with tempfile.TemporaryDirectory() as d:
            with open(os.path.join(d, "facts.json"), "w") as f:
                json.dump({}, f)
            with mock.patch.dict(os.environ, {"JOBPILOT_CDP_URL": "127.0.0.1:9555",
                                              "JOBPILOT_LANE": "example-lane",
                                              "JOBPILOT_TARGET_METROS": "Remote (US)"}, clear=True), \
                    mock.patch("launch_browsers.port_free", return_value=True), \
                    mock.patch("setup_wizard.launch_lane") as launch, \
                    contextlib.redirect_stdout(io.StringIO()):
                self.assertEqual(main(["--yes", "--directory", d]), 0)
            launch.assert_not_called()
            with open(os.path.join(d, "config.json")) as f:
                cfg = json.load(f)
            self.assertEqual(cfg["JOBPILOT_CDP_URL"], "127.0.0.1:9555")
            self.assertEqual(cfg["JOBPILOT_LANE"], "example-lane")
            self.assertEqual(cfg["JOBPILOT_TARGET_METROS"], "Remote (US)")
            with open(os.path.join(d, "queue.json")) as f:
                self.assertEqual(json.load(f), [])
            self.assertTrue(os.path.isfile(os.path.join(d, "claims.jsonl")))

    def test_bad_facts(self):
        with tempfile.TemporaryDirectory() as d:
            with open(os.path.join(d, "facts.json"), "w") as f:
                json.dump([], f)
            self.assertEqual(self._run(d), 2)


if __name__ == "__main__":
    unittest.main()
