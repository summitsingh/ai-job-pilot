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
        a = FakeAdapter(flip_on=2)
        r = check_submit(a, a.url, CFG)
        self.assertEqual(r["outcome"], "confirmed")
        self.assertEqual(a.clicks, ["normal", "js"])
        self.assertEqual(r["ladder"],
                         ["wait+recheck", "click-normal", "click-js"])

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


if __name__ == "__main__":
    unittest.main()
