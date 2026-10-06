#!/usr/bin/env python3
"""Offline tests for Batch A reliability features.

No browser, no model server, no network. Covers:
  - location_check.verify_location / parse_metros
  - submit_watchdog.check_submit recovery ladder (fake adapter)
  - model.budgeted_chat clamping, wall-clock timeout, telemetry
  - lane_greenhouse.location_values

Run: python3 -m unittest test_reliability -v
"""
import json
import os
import sys
import tempfile
import time
import unittest
from unittest import mock

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import model
from location_check import verify_location, parse_metros
from submit_watchdog import check_submit
from lane_greenhouse import location_values

METROS = ["Austin, TX"]


class LocationTest(unittest.TestCase):
    def test_austintown_fails(self):
        r = verify_location("Austintown, Ohio", METROS)
        self.assertFalse(r["ok"])
        self.assertIn("Austintown, Ohio", r["reason"])
        self.assertIn("Austin, TX", r["reason"])

    def test_austin_texas_passes(self):
        self.assertTrue(verify_location("Austin, Texas", METROS)["ok"])
        self.assertTrue(verify_location("Austin, TX, USA", METROS)["ok"])

    def test_wrong_state_fails(self):
        self.assertFalse(verify_location("Austin, Minnesota", METROS)["ok"])

    def test_city_substring_alone_fails(self):
        self.assertFalse(verify_location("Austin", METROS)["ok"])

    def test_remote(self):
        self.assertTrue(verify_location("Remote (US)", ["Remote (US)"])["ok"])
        self.assertTrue(verify_location("Remote - United States",
                                        ["Remote (US)"])["ok"])
        self.assertFalse(verify_location("Remote (EU)", ["Remote (US)"])["ok"])
        self.assertFalse(verify_location("Austin, TX", ["Remote (US)"])["ok"])

    def test_empty_fails_closed(self):
        self.assertFalse(verify_location("", METROS)["ok"])
        self.assertFalse(verify_location(None, METROS)["ok"])
        self.assertFalse(verify_location("Austin, TX", [])["ok"])

    def test_multiple_metros(self):
        m = ["Austin, TX", "New York, NY"]
        self.assertTrue(verify_location("New York, New York", m)["ok"])

    def test_parse_metros(self):
        self.assertEqual(parse_metros("Austin, TX; Remote (US)"),
                         ["Austin, TX", "Remote (US)"])
        self.assertEqual(parse_metros("Austin, TX, Remote (US)"),
                         ["Austin, TX", "Remote (US)"])
        self.assertEqual(parse_metros('["Austin, TX"]'), ["Austin, TX"])

    def test_location_values_from_fill(self):
        schema = {"fields": [{"key": "q_1", "label": "Current location"},
                             {"key": "first_name", "label": "First"}]}
        per = [{"field": "first_name", "action": "fill", "actual": "A"},
               {"field": "q_1", "action": "fill",
                "actual": "Austintown, Ohio"}]
        self.assertEqual(location_values(per, schema),
                         [("q_1", "Austintown, Ohio")])
        per2 = [{"field": "x", "action": "location", "actual": "<not-found>"}]
        self.assertEqual(location_values(per2, schema), [("x", "")])


class FakeAdapter:
    """Scripted page: the page flips to `after` once `flip_on` clicks land."""

    def __init__(self, start="https://x.test/apply", flip_on=None,
                 after=("https://x.test/apply", "Thank you for applying"),
                 text="Apply form", click_ok=True, raise_on=None):
        self.url, self.text = start, text
        self.flip_on, self.after = flip_on, after
        self.click_ok, self.raise_on = click_ok, raise_on
        self.clicks = []

    def current_url(self):
        return self.url

    def page_text(self, limit):
        return self.text[:limit]

    def submission_state(self):
        return {"retry_safe": True, "in_flight": False}

    def click_submit(self, mode):
        if mode == self.raise_on:
            raise RuntimeError("boom")
        self.clicks.append(mode)
        if self.flip_on and len(self.clicks) >= self.flip_on:
            self.url, self.text = self.after
        return {"clicked": self.click_ok}


CFG = {"wait_s": 2, "rechecks": 2, "poll_s": 1, "sleep": lambda s: None}


class WatchdogTest(unittest.TestCase):
    def test_confirmed_without_clicks(self):
        a = FakeAdapter(text="Thank you for applying")
        r = check_submit(a, a.url, CFG)
        self.assertEqual(r["outcome"], "confirmed")
        self.assertEqual(a.clicks, [])
        self.assertEqual(r["ladder"], ["wait+recheck"])

    def test_confirmation_url(self):
        a = FakeAdapter(start="https://x.test/jobs/1/confirmation")
        r = check_submit(a, "https://x.test/jobs/1", CFG)
        self.assertEqual(r["outcome"], "confirmed")

    def test_code_gate(self):
        a = FakeAdapter(text="A verification code was sent to you")
        self.assertEqual(check_submit(a, a.url, CFG)["outcome"], "code-gate")

    def test_normal_reclick_recovers(self):
        a = FakeAdapter(flip_on=1)
        r = check_submit(a, a.url, CFG)
        self.assertEqual(r["outcome"], "confirmed")
        self.assertEqual(a.clicks, ["normal"])
        self.assertEqual(r["ladder"], ["wait+recheck", "click-normal"])

    def test_js_click_recovers(self):
        a = FakeAdapter()
        def click(mode):
            a.clicks.append(mode)
            if mode == "normal":
                return {"clicked": False}
            a.text = "Thank you for applying"
            return {"clicked": True}
        a.click_submit = click
        r = check_submit(a, a.url, CFG)
        self.assertEqual(r["outcome"], "confirmed")
        self.assertEqual(a.clicks, ["normal", "js"])
        self.assertEqual(r["ladder"], ["wait+recheck", "click-normal", "click-js"])

    def test_ambiguous_state_never_reclicks(self):
        a = FakeAdapter()
        a.submission_state = lambda: {"retry_safe": False, "in_flight": False}
        self.assertEqual(check_submit(a, a.url, CFG)["outcome"], "blocked")
        self.assertEqual(a.clicks, [])

    def test_only_one_successful_reclick(self):
        a = FakeAdapter(flip_on=2)
        self.assertEqual(check_submit(a, a.url, CFG)["outcome"], "blocked")
        self.assertEqual(a.clicks, ["normal"])


    def test_gives_up_blocked_with_evidence(self):
        a = FakeAdapter()
        r = check_submit(a, a.url, CFG)
        self.assertEqual(r["outcome"], "blocked")
        self.assertEqual(r["ladder"][-1], "give-up")
        self.assertEqual(r["evidence"]["started_url"], a.url)
        self.assertEqual(r["evidence"]["final_url"], a.url)
        self.assertIn("Apply form", r["evidence"]["page_snippet"])

    def test_no_reclick_when_url_moved(self):
        a = FakeAdapter()
        a.url = "https://x.test/other"
        r = check_submit(a, "https://x.test/apply", CFG)
        self.assertEqual(r["outcome"], "blocked")
        self.assertEqual(a.clicks, [])

    def test_error_shown_stops_ladder(self):
        a = FakeAdapter(text="This field is required")
        r = check_submit(a, a.url, CFG)
        self.assertEqual(r["outcome"], "error")
        self.assertEqual(a.clicks, [])

    def test_click_exception_is_error(self):
        a = FakeAdapter(raise_on="normal")
        self.assertEqual(check_submit(a, a.url, CFG)["outcome"], "error")

    def test_unclickable_is_error(self):
        a = FakeAdapter(click_ok=False)
        self.assertEqual(check_submit(a, a.url, CFG)["outcome"], "error")

    def test_env_config(self):
        with mock.patch.dict(os.environ, {"JOBPILOT_SUBMIT_WAIT_S": "7",
                                          "JOBPILOT_SUBMIT_RECHECKS": "x"}):
            from submit_watchdog import load_watchdog_cfg
            c = load_watchdog_cfg()
        self.assertEqual(c["wait_s"], 7.0)
        self.assertEqual(c["rechecks"], 2)  # bad value falls back


class BudgetedChatTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.tel = os.path.join(self.tmp.name, "tel.jsonl")
        p = mock.patch.object(model, "probe", lambda timeout=5: (False, []))
        p.start()
        self.addCleanup(p.stop)

    def tearDown(self):
        self.tmp.cleanup()

    def lines(self):
        with open(self.tel) as f:
            return [json.loads(x) for x in f if x.strip()]

    def test_ok_passthrough_and_telemetry(self):
        seen = {}

        def fast(m, s, u, name, schema, max_tokens, temp, timeout):
            seen["max_tokens"] = max_tokens
            return {"answer": 1}
        with mock.patch.object(model, "_chat_once", fast):
            r = model.budgeted_chat("s", "u", "demo", {}, max_tokens=500,
                                    telemetry_path=self.tel)
        self.assertEqual(r, {"answer": 1})
        self.assertEqual(seen["max_tokens"], 500)
        rec, = self.lines()
        self.assertEqual(rec["status"], "ok")
        self.assertEqual(rec["schema_name"], "demo")
        self.assertIsNone(rec["tokens"])
        self.assertIsNone(rec["error"])
        self.assertGreaterEqual(rec["latency_s"], 0)
        self.assertIn("ts", rec)
        self.assertEqual(rec["max_tokens_requested"], 500)

    def test_max_tokens_clamped(self):
        seen = {}

        def stub(m, s, u, name, schema, max_tokens, temp, timeout):
            seen["mt"] = max_tokens
            return {}
        with mock.patch.object(model, "_chat_once", stub), \
                mock.patch.dict(os.environ,
                                {"JOBPILOT_MODEL_MAX_TOKENS": "100"}):
            model.budgeted_chat("s", "u", "demo", {}, max_tokens=9000,
                                telemetry_path=self.tel)
        self.assertEqual(seen["mt"], 100)
        rec, = self.lines()
        self.assertEqual(rec["max_tokens_requested"], 9000)
        self.assertEqual(rec["max_tokens_effective"], 100)

    def test_wall_timeout_cuts_runaway(self):
        def slow(*a, **k):
            time.sleep(3)
            return {}
        t0 = time.monotonic()
        with mock.patch.object(model, "_chat_once", slow), \
                mock.patch.dict(os.environ,
                                {"JOBPILOT_MODEL_WALL_TIMEOUT_S": "0.3"}):
            with self.assertRaises(TimeoutError) as cm:
                model.budgeted_chat("s", "u", "demo", {},
                                    telemetry_path=self.tel)
        self.assertLess(time.monotonic() - t0, 1.5)
        self.assertIn("wall-clock budget exceeded", str(cm.exception))
        rec, = self.lines()
        self.assertEqual(rec["status"], "timeout")

    def test_error_is_reraised_and_logged(self):
        def bad(*a, **k):
            raise ValueError("bad json")
        with mock.patch.object(model, "_chat_once", bad), \
                mock.patch.object(model.time, "sleep", lambda s: None):
            with self.assertRaises(RuntimeError):
                model.budgeted_chat("s", "u", "demo", {}, retries=1,
                                    telemetry_path=self.tel)
        self.assertEqual(self.lines()[0]["status"], "error")


class ModelDeadlineRegressionTest(unittest.TestCase):
    def test_request_timeout_capped_to_remaining_budget(self):
        timeouts = []
        with tempfile.TemporaryDirectory() as td:
            def once(*args):
                timeouts.append(args[-1])
                return {}
            with mock.patch.object(model, "probe", return_value=(False, [])), \
                    mock.patch.object(model, "_chat_once", side_effect=once):
                model.budgeted_chat("s", "u", "demo", {}, timeout=180,
                                    wall_timeout=0.2,
                                    telemetry_path=os.path.join(td, "t.jsonl"))
        self.assertGreater(timeouts[0], 0)
        self.assertLessEqual(timeouts[0], 0.2)

    def test_expired_worker_never_retries_or_changes_models(self):
        import threading
        finished = threading.Event()
        requests = []
        def once(*args):
            requests.append(args[0])
            threading.Event().wait(0.08)
            finished.set()
            raise TimeoutError("server timeout")
        with tempfile.TemporaryDirectory() as td, \
                mock.patch.object(model, "probe", return_value=(False, [])), \
                mock.patch.object(model, "_chat_once", side_effect=once), \
                mock.patch.object(model.time, "sleep", return_value=None):
            with self.assertRaises(model.ModelBudgetExceeded):
                model.budgeted_chat("s", "u", "demo", {}, retries=3,
                                    wall_timeout=0.02,
                                    telemetry_path=os.path.join(td, "t.jsonl"))
            self.assertTrue(finished.wait(0.5))
            threading.Event().wait(0.3)
            self.assertEqual(len(requests), 1)

    def test_real_consumers_apply_ceiling_and_write_telemetry(self):
        import importlib
        extract_mod = importlib.import_module("extract")
        verify_mod = importlib.import_module("verify")
        map_mod = importlib.import_module("map")
        requests = []
        def once(m, s, u, name, schema, max_tokens, temp, timeout):
            requests.append((name, max_tokens))
            if name == "field_map":
                return [{"field": "q_unknown", "action": "skip", "value": ""}]
            if name == "verify":
                return {"confirmed": False, "evidence": "form visible"}
            return {"title": None}
        def cdp(port, action, *args):
            return {"url": "https://example.test/apply"} if action == "url" else {"text": "Apply"}
        with tempfile.TemporaryDirectory() as td:
            tel = os.path.join(td, "t.jsonl")
            with mock.patch.dict(os.environ, {"JOBPILOT_MODEL_MAX_TOKENS": "17",
                                              "JOBPILOT_MODEL_TELEMETRY": tel}), \
                    mock.patch.object(model, "probe", return_value=(True, [])), \
                    mock.patch.object(model, "_chat_once", side_effect=once), \
                    mock.patch.object(verify_mod, "cdp_ok", side_effect=cdp):
                extract_mod.extract("Posting", "https://example.test/job")
                verify_mod.verify(9226)
                mapped, guarded, seen = map_mod._map_with_model(
                    {"fields": [{"key": "q_unknown", "type": "text", "label": "Unknown"}]},
                    {}, ["q_unknown"])
            self.assertEqual(mapped[0]["action"], "skip")
            self.assertEqual(requests, [("extract", 17), ("verify", 17), ("field_map", 17)])
            with open(tel) as f:
                self.assertEqual(len(f.readlines()), 3)


if __name__ == "__main__":
    unittest.main()

class AuditLocationTest(unittest.TestCase):
    def test_city_state_name_and_bad_descriptor(self):
        from location_check import validate_metros
        self.assertEqual(parse_metros("New York, NY, Washington, DC"), ["New York, NY", "Washington, DC"])
        with mock.patch("location_check.sys.stderr") as err:
            with self.assertRaisesRegex(ValueError, "unusable"):
                validate_metros("Austin")
            self.assertTrue(err.write.called)
        with self.assertRaisesRegex(ValueError, "JSON"):
            validate_metros('["Austin, TX"')

    def test_segment_adjacency_and_negation(self):
        for value in ("Austin, MN; Dallas, TX", "Austin, MN or Dallas, TX", "Austin via Dallas, TX", "Austin, TX except locals", "Remote (not US)"):
            self.assertFalse(verify_location(value, ["Austin, TX", "Remote (US)"])["ok"], value)

class Round2WatchdogTest(unittest.TestCase):
    def test_observation_exception_retains_ladder(self):
        a = FakeAdapter()
        a.current_url = mock.Mock(side_effect=TimeoutError("CDP unavailable"))
        r = check_submit(a, a.url, CFG)
        self.assertEqual(r["outcome"], "blocked")
        self.assertEqual(r["ladder"], ["wait+recheck"])
        self.assertIn("observation", r["evidence"]["note"])
        self.assertEqual(a.clicks, [])

    def test_observation_exception_after_recovery_retains_click(self):
        a = FakeAdapter()
        def click(mode):
            a.page_text = mock.Mock(side_effect=TimeoutError("lost page"))
            return {"clicked": True}
        a.click_submit = click
        r = check_submit(a, a.url, CFG)
        self.assertEqual(r["outcome"], "blocked")
        self.assertEqual(len(r["evidence"]["clicks"]), 1)

    def test_wait_settings_are_finite_and_bounded(self):
        from submit_watchdog import load_watchdog_cfg
        for value in ("inf", "nan", "0", "-1", "121"):
            with self.subTest(value=value), mock.patch.dict(os.environ, {"JOBPILOT_SUBMIT_WAIT_S": value}):
                self.assertEqual(load_watchdog_cfg()["wait_s"], 30)

    def test_static_required_copy_is_not_new_error(self):
        a = FakeAdapter(text="A resume is required for this position")
        r = check_submit(a, a.url, dict(CFG, pre_click_text=a.text))
        self.assertEqual(r["outcome"], "blocked")

    def test_new_error_after_click_is_error(self):
        a = FakeAdapter(text="Email is required")
        r = check_submit(a, a.url, dict(CFG, pre_click_text="Application form"))
        self.assertEqual(r["outcome"], "error")

class Round2LocationTest(unittest.TestCase):
    def test_english_words_are_not_states(self):
        for value, metro in (("Portland or nearby", "Portland, OR"),
                             ("Indianapolis in town", "Indianapolis, IN"),
                             ("Portland me please", "Portland, ME"),
                             ("Tulsa ok thanks", "Tulsa, OK"), ("Honolulu hi there", "Honolulu, HI")):
            self.assertFalse(verify_location(value, [metro])["ok"], value)
        for value in ("Portland, OR", "Portland OR", "Portland, or", "Portland Oregon"):
            self.assertTrue(verify_location(value, ["Portland, OR"])["ok"], value)

    def test_no_default_metros(self):
        from location_check import DEFAULT_METROS
        self.assertEqual(parse_metros(DEFAULT_METROS), [])

    def test_employer_location_question_is_ignored(self):
        schema = {"fields": [{"key": "office_location", "label": "Open to our Austin location?"}]}
        self.assertEqual(location_values([{"field": "office_location", "action": "select", "actual": "Yes"}], schema), [])

    def test_applicant_location_labels(self):
        for label in ("Current location", "Where are you located?", "City", "State", "Country"):
            schema = {"fields": [{"key": "q1", "label": label}]}
            self.assertEqual(location_values([{"field": "q1", "actual": "Austin, TX"}], schema), [("q1", "Austin, TX")])

class Round2LaneEvidenceTest(unittest.TestCase):
    def test_confirmation_summary_excludes_applicant_copy(self):
        from lane_greenhouse import confirmation_summary
        self.assertEqual(confirmation_summary("https://x.test/confirmation", "Jane Doe, jane@example.test: Thank you for applying"),
                         "Thank you for applying; https://x.test/confirmation")

    def test_field_table_strips_terminal_escapes(self):
        from lane_greenhouse import format_field_map
        result = format_field_map({"map": [{"field": "name", "value": "\x1b[31mAcme\x1b[0m\x07"}]})
        self.assertNotIn("\x1b", result)
        self.assertNotIn("\x07", result)
        self.assertIn("Acme", result)

    def test_codegate_rejects_non_alphanumeric(self):
        from codegate import coordinate
        for code in ("!!!!!!!!", "é2345678", "ABC\u200b1234", "ABCD 123"):
            notifier = mock.Mock()
            notifier.wait_for_code.return_value = code
            on_code = mock.Mock()
            self.assertEqual(coordinate(notifier, 1, on_code, lambda: False)["status"], "invalid-code")
            on_code.assert_not_called()

class Round2InitialSubmitTest(unittest.TestCase):
    def test_static_error_copy_does_not_block_explicit_selector_fallback(self):
        from lane_greenhouse import initial_submit
        a = FakeAdapter(text="A resume is required")
        def click(mode):
            a.clicks.append(mode)
            return {"clicked": mode == "js", "miss": mode == "normal"}
        a.click_submit = click
        result = initial_submit(a, a.url, pre_click_text=a.text)
        self.assertTrue(result["clicked"])
        self.assertEqual(a.clicks, ["normal", "js"])

class Round2StateContextTest(unittest.TestCase):
    def test_state_context_preserves_case_and_comma_spacing(self):
        for value in ("portland OR", "Portland,  or nearby"):
            self.assertTrue(verify_location(value, ["Portland, OR"])["ok"], value)

class Round2SplitAddressTest(unittest.TestCase):
    def test_split_components_match_metro_without_full_address(self):
        from lane_greenhouse import verify_applicant_location
        for label, value in (("City", "Austin"), ("State", "TX"), ("Country", "United States")):
            schema = {"fields": [{"key": "q1", "label": label}]}
            self.assertTrue(verify_applicant_location("q1", value, schema, METROS)["ok"], label)
        schema = {"fields": [{"key": "q1", "label": "State"}]}
        self.assertFalse(verify_applicant_location("q1", "MN", schema, METROS)["ok"])
        schema = {"fields": [{"key": "q1", "label": "Country"}]}
        self.assertFalse(verify_applicant_location("q1", "Canada", schema, METROS)["ok"])

    def test_postal_code_is_not_a_metro_value(self):
        schema = {"fields": [{"key": "postal_code", "label": "Postal code"}]}
        self.assertEqual(location_values([{"field": "postal_code", "actual": "78701"}], schema), [])

    def test_blocklisted_employer_stops_before_browser(self):
        from lane_greenhouse import run_job
        with tempfile.TemporaryDirectory() as td, mock.patch("lane_greenhouse.cdp") as browser:
            result = run_job({"url": "https://job-boards.greenhouse.io/perplexity/jobs/1"},
                             {"cdp_url": "127.0.0.1:9226", "port": 9226, "workdir": td, "lane": "test"}, True)
        self.assertEqual(result["status"], "blocked")
        browser.assert_not_called()

class Round2AddressPairTest(unittest.TestCase):
    def test_split_city_state_cannot_match_different_metros(self):
        from lane_greenhouse import verify_applicant_location
        schema = {"fields": [{"key": "city", "label": "City"}, {"key": "state", "label": "State"}]}
        per = [{"field": "city", "actual": "Austin"}, {"field": "state", "actual": "MN"}]
        checks = [verify_applicant_location(k, v, schema, ["Austin, TX", "Minneapolis, MN"])
                  for k, v in location_values(per, schema)]
        self.assertTrue(any(not check["ok"] for check in checks))

class Round2AddressPrefixTest(unittest.TestCase):
    def test_residential_and_applicant_components(self):
        from lane_greenhouse import verify_applicant_location
        schema = {"fields": [{"key": "#city", "label": "Residential City"},
                             {"key": "applicant_state", "label": "Residential State"},
                             {"key": "#country", "label": "Residential Country"},
                             {"key": "q_zip", "label": "Applicant Postal Code"}]}
        per = [{"field": "#city", "actual": "Austin"}, {"field": "applicant_state", "actual": "TX"},
               {"field": "#country", "actual": "United States"}, {"field": "q_zip", "actual": "78701"}]
        values = location_values(per, schema)
        self.assertEqual(len(values), 2)
        self.assertTrue(all(verify_applicant_location(k, v, schema, METROS)["ok"] for k, v in values))
