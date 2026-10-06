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
                   - datetime.timedelta(minutes=10)).isoformat()
            with open(c, "w") as f:
                f.write(json.dumps({"url": url, "lane": "lane-a",
                                    "claimed_at": old,
                                    "status": "in-progress"}) + "\n")
            self.assertIsNotNone(job_queue.next_job(q, c, "lane-b",
                                                    max_age_hours=5 / 60))
            self.assertTrue(job_queue.heartbeat(c, url, "lane-a"))
            self.assertIsNone(job_queue.next_job(q, c, "lane-b",
                                                 max_age_hours=5 / 60))
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

    def test_cli_no_live_claim_exit(self):
        with tempfile.TemporaryDirectory() as d:
            with mock.patch("sys.argv", ["job_queue.py", "--claims",
                            os.path.join(d, "claims.jsonl"), "heartbeat",
                            "https://example.com/j", "--lane", "a"]):
                with contextlib.redirect_stdout(io.StringIO()) as out:
                    with self.assertRaises(SystemExit) as exc:
                        job_queue.main()
                self.assertEqual(exc.exception.code, 1)
                self.assertEqual(out.getvalue().strip(), "no-live-claim")


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
            from codegate import NO_INPUT
            self.assertIs(CliNotifier().wait_for_code(4), NO_INPUT)

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
        self.assertNotIn("JOBPILOT_LANE", cfg)
        self.assertEqual(cfg["TRACKER_BACKENDS"], "jsonl")
        self.assertTrue(cfg["JOBPILOT_QUEUE"].endswith("queue.json"))
        self.assertTrue(cfg["JOBPILOT_CLAIMS"].endswith("claims.jsonl"))

    def test_explicit_lane_is_preserved(self):
        from setup_wizard import plan_setup
        self.assertEqual(plan_setup({"lane": "operator-lane"})["JOBPILOT_LANE"],
                         "operator-lane")

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

    def test_default_setup_keeps_process_lane_identity_unconfigured(self):
        with tempfile.TemporaryDirectory() as d:
            with open(os.path.join(d, "facts.json"), "w") as f:
                f.write('{}')
            self.assertEqual(self._run(d), 0)
            with open(os.path.join(d, "config.json")) as f:
                self.assertNotIn("JOBPILOT_LANE", json.load(f))

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

class AuditGateTest(unittest.TestCase):
    def test_no_input_does_not_requeue(self):
        from codegate import coordinate, NO_INPUT
        retry = mock.Mock()
        self.assertEqual(coordinate(FakeNotifier(NO_INPUT), 1, mock.Mock(), retry)["status"], "no_input")
        retry.assert_not_called()

    def test_callback_failure_is_surfaced_and_cap_blocks(self):
        from codegate import coordinate
        self.assertEqual(coordinate(FakeNotifier(None), 1, mock.Mock(), lambda: False)["status"], "blocked")
        retry = mock.Mock()
        self.assertEqual(coordinate(FakeNotifier(None), 1, mock.Mock(), retry, attempts=3)["status"], "blocked")
        retry.assert_not_called()

    def test_eof_and_decode_failure_are_no_input(self):
        from codegate import CliNotifier, NO_INPUT
        stream = mock.Mock()
        stream.fileno.return_value = 0
        for data in (b"", b"\xff\n"):
            with mock.patch("codegate.sys.stdin", stream), mock.patch("codegate.select.select", return_value=([stream], [], [])), mock.patch("codegate.os.read", return_value=data):
                self.assertIs(CliNotifier().wait_for_code(1), NO_INPUT)

    def test_gate_success_applied_failure_requeued_cap_blocked(self):
        import lane_greenhouse as lane
        with tempfile.TemporaryDirectory() as d:
            q, c = os.path.join(d, "q.json"), os.path.join(d, "c.jsonl")
            job = {"url": "https://example.com/job", "ats": "greenhouse"}
            with open(q, "w") as f:
                json.dump([job], f)
            cfg = {"queue": q, "claims": c, "lane": "a", "tracker_backends": [], "cdp_url": "127.0.0.1:1", "notifier": FakeNotifier("ABCD1234")}
            for attempt in range(1, 4):
                job_queue.claim(q, c, job["url"], "a")
                with mock.patch.object(lane, "enter_gate_code", return_value={"confirmed": False, "note": "expired"}), mock.patch.object(lane, "track", return_value=[]):
                    terminal = lane.finish_job(job, {"status": "code-gate"}, cfg)
                self.assertEqual(terminal, "code-requeued" if attempt < 3 else "blocked")
                self.assertEqual(job_queue.load_queue(q)[0]["code_gate_attempts"], attempt)
            self.assertFalse(job_queue.requeue(q, c, job["url"]))
            job["url"] += "2"
            with open(q, "w") as f:
                json.dump([job], f)
            job_queue.claim(q, c, job["url"], "a")
            with mock.patch.object(lane, "enter_gate_code", return_value={"confirmed": True, "url": job["url"] + "/confirmation"}), mock.patch.object(lane, "track", return_value=[]) as tracked:
                self.assertEqual(lane.finish_job(job, {"status": "code-gate"}, cfg), "applied")
                self.assertEqual(tracked.call_args.args[0]["status"], "Applied")
            self.assertFalse(job_queue.requeue(q, c, job["url"]))
            self.assertTrue(lane.already_applied(job["url"], cfg))

    def test_dry_run_all_outcomes_release_without_writes(self):
        import lane_greenhouse as lane
        cfg = {"queue": "q", "claims": "c", "lane": "a"}
        for status in ("blocked", "dead", "ready", "code-gate", "submitted"):
            with mock.patch.object(lane, "release") as release, mock.patch.object(lane, "track") as track, mock.patch.object(lane, "mark") as mark:
                lane.finish_job({"url": "u"}, {"status": status, "dry_run": True}, cfg)
                release.assert_called_once_with("c", "u", "a")
                track.assert_not_called()
                mark.assert_not_called()

class AuditLaneLifecycleTest(unittest.TestCase):
    def test_tracker_applied_wins_over_pending_queue_and_submit_is_skipped(self):
        import lane_greenhouse as lane
        with tempfile.TemporaryDirectory() as d:
            q, c, t = [os.path.join(d, n) for n in ("q.json", "c.jsonl", "t.jsonl")]
            url = "https://example.com/job"
            with open(q, "w") as f:
                json.dump([{"url": url, "status": "pending"}], f)
            with open(t, "w") as f:
                f.write(json.dumps({"url": url, "status": "Applied"}) + "\n")
            cfg = {"queue": q, "claims": c, "tracker_jsonl": t}
            self.assertTrue(lane.already_applied(url, cfg))
            adapter = lane.CdpSubmitAdapter("unused", lambda: lane.already_applied(url, cfg))
            with mock.patch.object(lane, "cdp") as cdp:
                self.assertFalse(adapter.click_submit("normal")["clicked"])
                self.assertFalse(adapter.click_submit("js")["clicked"])
                cdp.assert_not_called()

    def test_dry_run_exception_releases_real_claim(self):
        import lane_greenhouse as lane
        with tempfile.TemporaryDirectory() as d:
            q, c, facts = [os.path.join(d, n) for n in ("q.json", "c.jsonl", "facts.json")]
            with open(q, "w") as f:
                json.dump([{"url": "https://example.com/job", "ats": "greenhouse"}], f)
            with open(facts, "w") as f:
                json.dump({}, f)
            env = {"JOBPILOT_QUEUE": q, "JOBPILOT_CLAIMS": c, "JOBPILOT_FACTS": facts, "JOBPILOT_CDP_URL": "127.0.0.1:9226", "JOBPILOT_TARGET_METROS": "Remote (US)"}
            with mock.patch.dict(os.environ, env), mock.patch("sys.argv", ["lane", "--workdir", d, "--dry-run"]), mock.patch.object(lane, "load_config", return_value={}), mock.patch.object(lane, "run_job", side_effect=RuntimeError("offline failure")), mock.patch.object(lane, "track") as track, mock.patch.object(lane, "mark") as mark, contextlib.redirect_stdout(io.StringIO()):
                lane.main()
                track.assert_not_called()
                mark.assert_not_called()
            self.assertEqual(job_queue.load_queue(q)[0].get("status", "pending"), "pending")
            self.assertEqual(job_queue.read_claims(c)[0][-1]["status"], "released")

    def test_bad_location_config_fails_before_claim(self):
        import lane_greenhouse as lane
        with mock.patch("sys.argv", ["lane", "--workdir", "/tmp/unused"]), mock.patch.object(lane, "load_config", return_value={}), mock.patch.dict(os.environ, {"JOBPILOT_TARGET_METROS": '["broken"', "JOBPILOT_CDP_URL": "127.0.0.1:9226"}), mock.patch.object(lane, "claim") as claim, contextlib.redirect_stdout(io.StringIO()):
            with self.assertRaises(SystemExit) as exc:
                lane.main()
            self.assertEqual(exc.exception.code, 2)
            claim.assert_not_called()

    def test_no_input_and_ambiguous_code_confirmation_stay_blocked(self):
        import lane_greenhouse as lane
        from codegate import NO_INPUT
        for supplied, response in ((NO_INPUT, {}), ("ABCD1234", {"confirmed": False, "ambiguous": True})):
            with tempfile.TemporaryDirectory() as d:
                q, c = os.path.join(d, "q.json"), os.path.join(d, "c.jsonl")
                job = {"url": "https://example.com/job"}
                with open(q, "w") as f:
                    json.dump([job], f)
                job_queue.claim(q, c, job["url"], "a")
                cfg = {"queue": q, "claims": c, "lane": "a", "cdp_url": "unused", "tracker_backends": [], "notifier": FakeNotifier(supplied)}
                with mock.patch.object(lane, "track", return_value=[]), mock.patch.object(lane, "enter_gate_code", return_value=response), mock.patch.object(lane, "requeue") as retry:
                    self.assertEqual(lane.finish_job(job, {"status": "code-gate"}, cfg), "blocked")
                    retry.assert_not_called()
                self.assertEqual(job_queue.load_queue(q)[0]["status"], "blocked")

class AuditAppliedHistoryTest(unittest.TestCase):
    def test_applied_history_cannot_be_requeued_or_downgraded(self):
        with tempfile.TemporaryDirectory() as d:
            q, c = os.path.join(d, "q.json"), os.path.join(d, "c.jsonl")
            job = {"url": "https://example.com/job"}
            with open(q, "w") as f:
                json.dump([job], f)
            self.assertTrue(job_queue.mark(q, c, job["url"], "applied"))
            self.assertFalse(job_queue.mark(q, c, job["url"], "blocked"))
            with open(q, "w") as f:
                json.dump([dict(job, status="blocked")], f)
            self.assertFalse(job_queue.requeue(q, c, job["url"]))

class AuditReviewRegressionTest(unittest.TestCase):
    def test_ambiguous_click_failure_is_not_a_selector_miss(self):
        import lane_greenhouse as lane
        with mock.patch.object(lane, "cdp", return_value={"ok": False, "err": "lost response"}):
            clicked = lane.CdpSubmitAdapter("unused").click_submit("normal")
        self.assertTrue(clicked["ambiguous"])
        self.assertFalse(clicked["miss"])
        with mock.patch.object(lane, "cdp", return_value={"ok": False, "error": "no such element: button"}):
            clicked = lane.CdpSubmitAdapter("unused").click_submit("normal")
        self.assertTrue(clicked["miss"])
        self.assertFalse(clicked["ambiguous"])

    def test_initial_lost_response_never_falls_back_to_js(self):
        import lane_greenhouse as lane
        adapter = mock.Mock()
        adapter.click_submit.return_value = {"clicked": False, "ambiguous": True, "miss": False}
        result = lane.initial_submit(adapter, "https://example.com/apply")
        self.assertTrue(result["ambiguous"])
        adapter.click_submit.assert_called_once_with("normal")

    def test_postclaim_heartbeat_exception_releases_dry_run(self):
        import lane_greenhouse as lane
        with tempfile.TemporaryDirectory() as d:
            q, c, facts = [os.path.join(d, n) for n in ("q.json", "c.jsonl", "facts.json")]
            with open(q, "w") as f:
                json.dump([{"url": "https://example.com/job", "ats": "greenhouse"}], f)
            with open(facts, "w") as f:
                json.dump({}, f)
            env = {"JOBPILOT_QUEUE": q, "JOBPILOT_CLAIMS": c, "JOBPILOT_FACTS": facts, "JOBPILOT_CDP_URL": "127.0.0.1:9226", "JOBPILOT_TARGET_METROS": "Remote (US)"}
            with mock.patch.dict(os.environ, env), mock.patch("sys.argv", ["lane", "--workdir", d, "--dry-run"]), mock.patch.object(lane, "load_config", return_value={}), mock.patch.object(lane, "heartbeat", side_effect=OSError("heartbeat unavailable")), mock.patch.object(lane, "run_job", return_value={"status": "ready", "dry_run": True}), contextlib.redirect_stdout(io.StringIO()):
                lane.main()
            self.assertEqual(job_queue.read_claims(c)[0][-1]["status"], "released")

class AuditGateBrowserMechanicsTest(unittest.TestCase):
    def test_human_code_confirmed_or_explicit_expiry(self):
        import lane_greenhouse as lane
        for text, confirmed in (("Your application has successfully been received", True), ("Verification code has expired", False)):
            responses = [
                {"result": {"gate": True, "boxes": 8, "keys": ["#code%d" % i for i in range(8)]}},
                {"result": [{"ok": True} for _ in range(8)]},
                {"result": "CLICKED"},
            ]
            adapter = mock.Mock()
            adapter.current_url.return_value = "https://example.com/code"
            adapter.page_text.return_value = text
            with mock.patch.object(lane, "cdp", side_effect=responses) as cdp, mock.patch.object(lane, "CdpSubmitAdapter", return_value=adapter), mock.patch.object(lane.time, "sleep"):
                result = lane.enter_gate_code("unused", "ABCD1234")
            self.assertEqual(result["confirmed"], confirmed)
            self.assertFalse(result.get("ambiguous", False))
            self.assertEqual(cdp.call_count, 3)

class AuditTimeoutCapTest(unittest.TestCase):
    def test_timeouts_persist_attempts_and_stop_after_three(self):
        import lane_greenhouse as lane
        with tempfile.TemporaryDirectory() as d:
            q, c = os.path.join(d, "q.json"), os.path.join(d, "c.jsonl")
            job = {"url": "https://example.com/job"}
            with open(q, "w") as f:
                json.dump([job], f)
            cfg = {"queue": q, "claims": c, "lane": "a", "cdp_url": "unused", "tracker_backends": [], "notifier": FakeNotifier(None)}
            for attempt in range(1, 4):
                job_queue.claim(q, c, job["url"], "a")
                with mock.patch.object(lane, "track", return_value=[]):
                    terminal = lane.finish_job(job, {"status": "code-gate"}, cfg)
                self.assertEqual(terminal, "code-requeued" if attempt < 3 else "blocked")
            state = job_queue.load_queue(q)[0]
            self.assertEqual((state["status"], state["code_gate_attempts"]), ("blocked", 3))
            self.assertIn("3/3", state["skip_reason"])
            self.assertIsNone(job_queue.next_job(q, c, "b"))
            self.assertFalse(job_queue.requeue(q, c, job["url"]))

class AuditCodeSubmitGuardTest(unittest.TestCase):
    def test_applied_check_is_repeated_immediately_before_code_submit(self):
        import lane_greenhouse as lane
        replies = [
            {"result": {"gate": True, "boxes": 8, "keys": ["#code%d" % i for i in range(8)]}},
            {"result": [{"ok": True} for _ in range(8)]},
        ]
        with mock.patch.object(lane, "cdp", side_effect=replies) as cdp:
            result = lane.enter_gate_code("unused", "ABCD1234", applied_check=lambda: True)
        self.assertTrue(result["confirmed"])
        self.assertEqual(cdp.call_count, 2)

class MinorWizardRegressionTest(unittest.TestCase):
    _run = WizardTest._run

    def test_existing_config_dry_run_previews_without_force(self):
        from setup_wizard import main
        with tempfile.TemporaryDirectory() as d:
            original = '{"keep": 1}\n'
            with open(os.path.join(d, "facts.json"), "w") as f:
                f.write('{}')
            path = os.path.join(d, "config.json")
            with open(path, "w") as f:
                f.write(original)
            with mock.patch.dict(os.environ, {}, clear=True), \
                    mock.patch("launch_browsers.port_free", return_value=True), \
                    contextlib.redirect_stdout(io.StringIO()) as out:
                self.assertEqual(main(["--yes", "--dry-run", "--directory", d]), 0)
            self.assertIn("would overwrite " + path, out.getvalue())
            with open(path) as f:
                self.assertEqual(f.read(), original)
            self.assertEqual(sorted(os.listdir(d)), ["config.json", "facts.json"])

    def test_browser_failure_preserves_previous_config(self):
        from setup_wizard import main
        with tempfile.TemporaryDirectory() as d:
            path = os.path.join(d, "config.json")
            with open(path, "w") as f:
                f.write('{"keep": 1}\n')
            with open(os.path.join(d, "facts.json"), "w") as f:
                f.write('{}')
            def answers(defaults):
                return dict(defaults, launch=True, skip_queue=True)
            with mock.patch.dict(os.environ, {}, clear=True), \
                    mock.patch("launch_browsers.port_free", return_value=True), \
                    mock.patch("setup_wizard.collect_answers", side_effect=answers), \
                    mock.patch("setup_wizard.launch_lane"), \
                    mock.patch("setup_wizard.wait_for_instance", return_value=False), \
                    contextlib.redirect_stdout(io.StringIO()):
                self.assertEqual(main(["--force", "--directory", d]), 1)
            with open(path) as f:
                self.assertEqual(f.read(), '{"keep": 1}\n')
            self.assertEqual(sorted(os.listdir(d)), ["config.json", "facts.json"])

    def test_config_replace_failure_keeps_old_file_and_removes_temp(self):
        with tempfile.TemporaryDirectory() as d:
            path = os.path.join(d, "config.json")
            with open(path, "w") as f:
                f.write('{"keep": 1}\n')
            with open(os.path.join(d, "facts.json"), "w") as f:
                f.write('{}')
            with mock.patch("os.replace", side_effect=OSError("replace denied")):
                self.assertEqual(self._run(d, "--force"), 2)
            with open(path) as f:
                self.assertEqual(f.read(), '{"keep": 1}\n')
            self.assertFalse(any(name.startswith(".config-") for name in os.listdir(d)))

    def test_queue_initialization_failure_preserves_config(self):
        with tempfile.TemporaryDirectory() as d:
            path = os.path.join(d, "config.json")
            with open(path, "w") as f:
                json.dump({"JOBPILOT_QUEUE": os.path.join(d, "missing", "q.json")}, f)
            with open(path) as f:
                original = f.read()
            with open(os.path.join(d, "facts.json"), "w") as f:
                f.write('{}')
            with mock.patch("os.makedirs", side_effect=OSError("directory denied")):
                self.assertEqual(self._run(d, "--force"), 2)
            with open(path) as f:
                self.assertEqual(f.read(), original)


class MinorModelRegressionTest(unittest.TestCase):
    def test_invalid_wall_budgets_use_same_default_for_env_and_argument(self):
        import model
        for value in (0, -1, float("inf"), float("nan"), "bad"):
            for explicit in (False, True):
                with self.subTest(value=value, explicit=explicit), tempfile.TemporaryDirectory() as d:
                    path = os.path.join(d, "telemetry.jsonl")
                    env = {} if explicit else {"JOBPILOT_MODEL_WALL_TIMEOUT_S": str(value)}
                    kwargs = {"wall_timeout": value} if explicit else {}
                    with mock.patch.dict(os.environ, env, clear=True), \
                            mock.patch.object(model, "chat", return_value={"ok": True}):
                        self.assertEqual(model.budgeted_chat("private", "private", "test", {},
                                                            telemetry_path=path, **kwargs), {"ok": True})
                    with open(path) as f:
                        self.assertEqual(json.loads(f.read())["wall_timeout_s"], 300)

    def test_telemetry_rotates_at_five_megabytes_and_keeps_two_generations(self):
        import model
        with tempfile.TemporaryDirectory() as d:
            path = os.path.join(d, "telemetry.jsonl")
            old = b'x' * (5 * 1024 * 1024)
            with open(path, "wb") as f:
                f.write(old)
            for suffix, text in ((".1", "first"), (".2", "second")):
                with open(path + suffix, "w") as f:
                    f.write(text)
            model._write_telemetry(path, {"status": "ok"})
            with open(path) as f:
                self.assertEqual(json.loads(f.read()), {"status": "ok"})
            self.assertEqual(os.path.getsize(path + ".1"), 5 * 1024 * 1024)
            with open(path + ".2") as f:
                self.assertEqual(f.read(), "first")
            self.assertEqual(len(os.listdir(d)), 3)

    def test_telemetry_does_not_capture_exception_prompt_text(self):
        import model
        with tempfile.TemporaryDirectory() as d:
            path = os.path.join(d, "telemetry.jsonl")
            with mock.patch.object(model, "chat", side_effect=RuntimeError("private applicant prompt")):
                with self.assertRaises(RuntimeError):
                    model.budgeted_chat("private applicant prompt", "private", "test", {}, telemetry_path=path)
            with open(path) as f:
                self.assertNotIn("private", f.read())

    def test_remote_model_names_are_single_shell_arguments(self):
        import model
        import shlex
        current, target = "loaded;touch /tmp/unwanted", "next'$(whoami)"
        commands = []
        with mock.patch.object(model, "API_FLAVOR", "openai"), \
                mock.patch.object(model, "_lms_loaded", return_value=current), \
                mock.patch("common.host_ssh", side_effect=lambda cmd, **kw: commands.append(cmd)):
            model._lms_ensure(target)
        self.assertEqual(len(commands), 2)
        for command, action, name in zip(commands, ("unload", "load"), (current, target)):
            self.assertEqual(shlex.split(command)[:3], ["~/.lmstudio/bin/lms", action, name])
