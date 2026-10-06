#!/usr/bin/env python3
"""Offline tests for Batch B: tracker URL parsing, scoreboard, analytics,
and fuzzy dedup in sweep.

No browser, no model server, no network. Run:
  python3 -m unittest test_tracking -v
"""
import datetime
import json
import os
import sys
import subprocess
import tempfile
import unittest

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import analytics
import tracker
import scoreboard
import sweep
from tracker import (NotionTracker, SheetsTracker, parse_notion_db_id,
                     parse_sheets_id)

SHEET = "1AbC_dEf-GhIjKlMnOpQrStUvWxYz0123456789"
HEX = "0123456789abcdef0123456789abcdef"
HYPH = "01234567-89ab-cdef-0123-456789abcdef"


class SheetsIdTest(unittest.TestCase):
    def test_full_url(self):
        url = "https://docs.google.com/spreadsheets/d/%s/edit#gid=0" % SHEET
        self.assertEqual(parse_sheets_id(url), SHEET)

    def test_url_variants(self):
        self.assertEqual(parse_sheets_id(
            "https://docs.google.com/spreadsheets/d/%s" % SHEET), SHEET)
        self.assertEqual(parse_sheets_id(
            "docs.google.com/spreadsheets/d/%s/edit?usp=sharing" % SHEET),
            SHEET)
        self.assertEqual(parse_sheets_id(
            "https://docs.google.com/spreadsheets/u/0/d/%s/edit" % SHEET),
            SHEET)

    def test_bare_id(self):
        self.assertEqual(parse_sheets_id(SHEET), SHEET)
        self.assertEqual(parse_sheets_id("  %s  " % SHEET), SHEET)

    def test_invalid(self):
        for bad in ("", "   ", None, "bad id!", "has.dot",
                    "https://example.com/spreadsheets/d/%s/edit" % SHEET,
                    "https://docs.google.com/document/d/%s/edit" % SHEET,
                    "https://docs.google.com/spreadsheets/"):
            with self.assertRaises(ValueError, msg=repr(bad)):
                parse_sheets_id(bad)

    def test_published_and_short_sheet_ids_rejected(self):
        for bad in ("https://docs.google.com/spreadsheets/d/e/2PACX-published/pubhtml",
                    "https://docs.google.com/spreadsheets/d/e/published/spreadsheets/d/" + SHEET,
                    "https://docs.google.com/spreadsheets/d/short/edit",
                    "short"):
            with self.assertRaises(ValueError, msg=bad):
                parse_sheets_id(bad)

    def test_constructor_uses_parser(self):
        t = SheetsTracker(
            spreadsheet_id="https://docs.google.com/spreadsheets/d/%s/edit"
            % SHEET, credentials_path="/nonexistent.json")
        self.assertEqual(t.spreadsheet_id, SHEET)
        with self.assertRaises(ValueError):
            SheetsTracker(spreadsheet_id="https://example.com/x",
                          credentials_path="/nonexistent.json")



class TrackerStatusTest(unittest.TestCase):
    def test_response_fields_and_case_tolerant_statuses(self):
        for status in ("applied", "RESPONDED", "screening", "Interview",
                       "offer", "rejected", "withdrawn", "blocked"):
            rec = tracker.build_record(url="https://example.com/1", status=status,
                                       response_date="2026-01-05")
            self.assertEqual(rec["status"], status.title())
            self.assertEqual(rec["response_date"], "2026-01-05")

    def test_update_cli_preserves_stage_and_first_response_by_url_or_id(self):
        with tempfile.TemporaryDirectory() as d:
            path = os.path.join(d, "applications.jsonl")
            rec = tracker.build_record(url="https://example.com/1", date="2026-01-01",
                                       company="Acme", status="Applied")
            tracker.JsonlTracker(path).append(rec)
            for selector, status, date in ((["--url", rec["url"]], "interview", "2026-01-04"),
                                           (["--id", rec["id"]], "rejected", "2026-01-08")):
                result = subprocess.run([sys.executable, tracker.__file__, "update",
                    "--jsonl-path", path, *selector, "--status", status,
                    "--response-date", date], capture_output=True, text=True)
                self.assertEqual(result.returncode, 0, result.stderr)
            with open(path) as f:
                rows = [json.loads(line) for line in f]
            self.assertEqual(len(rows), 1)
            self.assertEqual(rows[0]["status"], "Rejected")
            self.assertEqual(rows[0]["furthest_stage"], "Interview")
            self.assertEqual(rows[0]["response_date"], "2026-01-04")
            stages = analytics.funnel_stats(rows)["overall"]["stages"]
            self.assertEqual(stages, {"applied": 1, "responded": 1, "screening": 1,
                                      "interview": 1, "offer": 0})

    def test_update_unknown_record_or_bad_date_does_not_mutate_log(self):
        with tempfile.TemporaryDirectory() as d:
            path = os.path.join(d, "applications.jsonl")
            rec = tracker.build_record(url="https://example.com/1", date="2026-01-01")
            local = tracker.JsonlTracker(path)
            local.append(rec)
            with open(path) as f:
                before = f.read()
            for kwargs in ({"url": "https://example.com/missing"},
                           {"url": rec["url"], "response_date": "bad"},
                           {"url": rec["url"], "response_date": "2025-12-31"}):
                with self.assertRaises(ValueError):
                    local.update("interview", **kwargs)
                with open(path) as f:
                    self.assertEqual(f.read(), before)

    def test_update_response_defaults_to_today(self):
        with tempfile.TemporaryDirectory() as d:
            path = os.path.join(d, "applications.jsonl")
            local = tracker.JsonlTracker(path)
            local.append(tracker.build_record(url="https://example.com/1"))
            rec = local.update("screening", url="https://example.com/1?utm_source=test")
            self.assertEqual(rec["response_date"], datetime.date.today().isoformat())
            self.assertEqual(rec["furthest_stage"], "Screening")

    def test_terminal_stage_and_excluded_records(self):
        stats = analytics.funnel_stats([
            {"status": "Withdrawn", "furthest_stage": "Offer"},
            {"status": "Rejected", "furthest_stage": "Interview"},
            *({"status": s} for s in ("Ready", "Dead", "Skipped", "Blocked"))])
        self.assertEqual(stats["overall"]["excluded"], 4)
        self.assertEqual(stats["unknown_statuses"], {})
        self.assertEqual(stats["overall"]["stages"], {
            "applied": 2, "responded": 2, "screening": 2, "interview": 2, "offer": 1})

class NotionIdTest(unittest.TestCase):
    def test_bare_ids(self):
        self.assertEqual(parse_notion_db_id(HEX), HYPH)
        self.assertEqual(parse_notion_db_id(HYPH), HYPH)
        self.assertEqual(parse_notion_db_id(HEX.upper()), HYPH)

    def test_url_unhyphenated_after_title(self):
        url = "https://www.notion.so/workspace/My-Tracker-%s?v=%s" % (
            HEX, "f" * 32)
        self.assertEqual(parse_notion_db_id(url), HYPH)

    def test_url_id_only_segment(self):
        self.assertEqual(parse_notion_db_id(
            "https://notion.so/%s" % HEX), HYPH)
        self.assertEqual(parse_notion_db_id(
            "https://notion.so/ws/%s?pvs=4" % HYPH), HYPH)

    def test_view_id_is_never_used(self):
        # The only 32-hex value is in the query (a view id): reject.
        with self.assertRaises(ValueError):
            parse_notion_db_id("https://notion.so/ws/Tracker?v=%s" % HEX)

    def test_invalid(self):
        for bad in ("", None, "abc", HEX[:-1], HEX + "0", "g" * 32,
                    "https://example.com/Tracker-%s" % HEX,
                    "https://notion.so/ws/Tracker"):
            with self.assertRaises(ValueError, msg=repr(bad)):
                parse_notion_db_id(bad)

    def test_constructor_uses_parser(self):
        t = NotionTracker(token="t",
                          database_id="https://notion.so/Db-%s" % HEX)
        self.assertEqual(t.database_id, HYPH)
        with self.assertRaises(ValueError):
            NotionTracker(token="t", database_id="not-an-id")


class ScoreboardTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.db = os.path.join(self.tmp.name, "sb.json")
        self.now = datetime.datetime(2026, 1, 15, 12, 0,
                                     tzinfo=datetime.timezone.utc)

    def tearDown(self):
        self.tmp.cleanup()

    def _rec(self, lane, kind, days_ago=0, hours_ago=0):
        ts = (self.now - datetime.timedelta(days=days_ago,
                                            hours=hours_ago)).isoformat()
        scoreboard.record(self.db, lane, kind, company="Acme",
                          url="https://example.com/j", ts=ts)

    def test_record_persists(self):
        ev = scoreboard.record(self.db, "lane-a:1", "applied", "Acme", "u")
        self.assertEqual(ev["kind"], "applied")
        self.assertEqual(len(scoreboard.load_events(self.db)), 1)
        scoreboard.record(self.db, "lane-a:1", "dead")
        self.assertEqual(len(scoreboard.load_events(self.db)), 2)

    def test_record_validation(self):
        with self.assertRaises(ValueError):
            scoreboard.record(self.db, "lane-a", "oops")
        with self.assertRaises(ValueError):
            scoreboard.record(self.db, "", "applied")
        self.assertFalse(os.path.exists(self.db))

    def test_day_and_week_totals(self):
        self._rec("m:1", "applied")
        self._rec("m:1", "skipped", hours_ago=1)
        self._rec("m:1", "applied", days_ago=3)
        self._rec("m:1", "blocked", days_ago=10)
        ev = scoreboard.load_events(self.db)
        day = scoreboard.totals(ev, "day", now=self.now)
        self.assertEqual(day["overall"]["applied"], 1)
        self.assertEqual(day["overall"]["skipped"], 1)
        self.assertEqual(day["overall"]["total"], 2)
        week = scoreboard.totals(ev, "week", now=self.now)
        self.assertEqual(week["overall"]["applied"], 2)
        self.assertEqual(week["overall"]["blocked"], 0)
        self.assertEqual(week["overall"]["total"], 3)

    def test_lane_filter_and_per_lane(self):
        self._rec("a:1", "applied")
        self._rec("b:2", "applied")
        self._rec("b:2", "dead")
        ev = scoreboard.load_events(self.db)
        both = scoreboard.totals(ev, "day", now=self.now)
        self.assertEqual(sorted(both["lanes"]), ["a:1", "b:2"])
        self.assertEqual(both["lanes"]["b:2"]["total"], 2)
        only = scoreboard.totals(ev, "day", lane="b:2", now=self.now)
        self.assertEqual(list(only["lanes"]), ["b:2"])
        self.assertEqual(only["overall"]["total"], 2)

    def test_bad_period_and_table(self):
        with self.assertRaises(ValueError):
            scoreboard.totals([], "month")
        self._rec("a:1", "applied")
        res = scoreboard.totals(scoreboard.load_events(self.db), "day",
                                now=self.now)
        text = scoreboard.format_table(res, "day")
        self.assertIn("a:1", text)
        self.assertIn("OVERALL", text)
        self.assertNotIn(chr(0x2014), text)


def _jsonl(*recs):
    return [json.dumps(r) for r in recs]


class AnalyticsTest(unittest.TestCase):
    def test_parse_records_counts_malformed(self):
        recs, bad = analytics.parse_records(
            ['{"company": "A"}', "", "not json", "[1]", '{"company": "B"}'])
        self.assertEqual(len(recs), 2)
        self.assertEqual(bad, 2)

    def test_funnel_and_conversion(self):
        recs, _ = analytics.parse_records(_jsonl(
            {"company": "Acme", "status": "Applied", "date": "2026-01-01"},
            {"company": "Acme", "status": "RESPONDED", "date": "2026-01-01",
             "response_date": "2026-01-05"},
            {"company": "Acme", "status": "interview", "date": "2026-01-01",
             "response_date": "2026-01-03"},
            {"company": "Beta", "status": "rejected", "date": "2026-01-02",
             "response_date": "2026-01-12"},
            {"company": "Beta", "status": "offer", "date": "2026-01-02"},
        ))
        st = analytics.funnel_stats(recs)
        o = st["overall"]
        self.assertEqual(st["total"], 5)
        self.assertEqual(o["stages"], {"applied": 5, "responded": 4,
                                       "screening": 2, "interview": 2,
                                       "offer": 1})
        self.assertAlmostEqual(o["conversion"]["applied->responded"], 0.8)
        self.assertAlmostEqual(o["conversion"]["responded->screening"],
                               0.5, places=3)
        self.assertEqual(o["counts"]["rejected"], 1)
        # days: 4, 2, 10 -> median 4
        self.assertEqual(o["median_days_to_response"], 4)
        self.assertEqual(o["response_samples"], 3)
        self.assertEqual(sorted(st["companies"]), ["acme", "beta"])
        self.assertEqual(st["companies"]["acme"]["stages"]["applied"], 3)
        self.assertEqual(
            st["companies"]["beta"]["median_days_to_response"], 10)

    def test_unknown_status_reported_not_dropped(self):
        st = analytics.funnel_stats([
            {"company": "A", "status": "Skipped"},
            {"company": "A", "status": ""},
            {"company": "A", "status": "applied"}])
        self.assertEqual(st["overall"]["other"], 1)
        self.assertEqual(st["overall"]["excluded"], 1)
        self.assertEqual(st["unknown_statuses"], {"(empty)": 1})
        self.assertEqual(st["overall"]["stages"]["applied"], 1)
        self.assertIn("unknown statuses", analytics.format_report(st))

    def test_empty_input_no_division_errors(self):
        st = analytics.funnel_stats([])
        self.assertIsNone(st["overall"]["conversion"]["applied->responded"])
        self.assertIsNone(st["overall"]["median_days_to_response"])
        self.assertIn("n/a", analytics.format_report(st))

    def test_file_roundtrip(self):
        with tempfile.TemporaryDirectory() as d:
            p = os.path.join(d, "applications.jsonl")
            with open(p, "w") as f:
                f.write("\n".join(_jsonl(
                    {"company": "A", "status": "Applied"})) + "\n")
            with open(p) as f:
                recs, bad = analytics.parse_records(f)
            self.assertEqual((len(recs), bad), (1, 0))
            json.dumps(analytics.funnel_stats(recs))


class FuzzyDedupTest(unittest.TestCase):
    def test_same_role_two_boards(self):
        a = {"company": "Acme", "title": "Senior Backend Engineer",
             "location": "Austin, TX",
             "url": "https://job-boards.greenhouse.io/acme/jobs/1"}
        b = {"company": "Acme", "title": "Sr Backend Engineer",
             "location": "austin, tx",
             "url": "https://jobs.lever.co/acme/abc"}
        self.assertTrue(sweep.near_duplicate(a, b))
        for title in ("Staff Backend Engineer", "Lead Backend Engineer"):
            self.assertFalse(sweep.near_duplicate(a, dict(b, title=title)))

    def test_company_suffix_variants(self):
        a = {"company": "Acme Inc.", "title": "Senior Platform Engineer",
             "location": "Remote"}
        b = {"company": "acme", "title": "Sr. Platform Engineer",
             "location": "Remote"}
        c = {"company": "ACME, LLC", "title": "Platform Engineer",
             "location": "Remote"}
        self.assertTrue(sweep.near_duplicate(a, b))
        self.assertFalse(sweep.near_duplicate(a, c))

    def test_different_titles_location_company(self):
        base = {"company": "Acme", "title": "Senior Backend Engineer",
                "location": "Austin, TX"}
        for other in (
                dict(base, title="Senior Platform Engineer"),
                dict(base, title="Senior Backend Engineer Payments"),
                dict(base, location="Seattle, WA"),
                dict(base, company="Acme Robotics")):
            self.assertFalse(sweep.near_duplicate(base, other), other)

    def test_empty_fields_never_match(self):
        e = {"company": "", "title": "", "location": ""}
        self.assertFalse(sweep.near_duplicate(e, dict(e)))

    def test_find_duplicates_pairs(self):
        c = [{"company": "Acme", "title": "Senior Backend Engineer",
              "location": "Austin"},
             {"company": "Other", "title": "Senior Backend Engineer",
              "location": "Austin"},
             {"company": "acme inc", "title": "Sr Backend Engineer",
              "location": "Austin"}]
        self.assertEqual(sweep.find_duplicates(c), [(0, 2)])

    def test_filter_postings_withholds_for_review(self):
        raw = [
            {"company": "Acme", "title": "Senior Backend Engineer",
             "location": "Austin, TX",
             "url": "https://job-boards.greenhouse.io/acme/jobs/1"},
            {"company": "Acme Inc", "title": "Sr Backend Engineer",
             "location": "Austin, TX",
             "url": "https://jobs.lever.co/acme/abc"},
            {"company": "Acme", "title": "Senior Platform Engineer",
             "location": "Austin, TX",
             "url": "https://jobs.lever.co/acme/def"},
        ]
        cands, stats = sweep.filter_postings(raw, now="2026-01-01T00:00:00Z")
        self.assertEqual([c["url"] for c in cands],
                         [raw[0]["url"], raw[2]["url"]])
        self.assertEqual(stats["fuzzy_dup"], 1)
        self.assertEqual(stats["fuzzy_dup_review"],
                         [(raw[0]["url"], raw[1]["url"])])
        self.assertEqual(stats["dup"], 0)

    def test_no_fuzzy_dup_keys_default(self):
        cands, stats = sweep.filter_postings([])
        self.assertEqual(cands, [])
        self.assertEqual(stats["fuzzy_dup"], 0)
        self.assertEqual(stats["fuzzy_dup_review"], [])


class AuditNotionProgressFieldsTest(unittest.TestCase):
    def test_response_date_and_stage_survive_backend_record(self):
        import io
        from unittest import mock
        rec = tracker.build_record(url="https://example.com/job", status="Rejected", response_date="2026-10-06", furthest_stage="Interview")
        backend = tracker.NotionTracker(token="offline", database_id="0123456789abcdef0123456789abcdef")
        with mock.patch("tracker.urllib.request.urlopen", return_value=io.BytesIO(b'{"id":"page"}')) as request:
            backend.append(rec)
        payload = json.loads(request.call_args.args[0].data)
        props = payload["properties"]
        self.assertEqual(props["Response Date"]["date"]["start"], rec["response_date"])
        self.assertEqual(props["Furthest Stage"]["rich_text"][0]["text"]["content"], "Interview")
        self.assertEqual(props["ID"]["rich_text"][0]["text"]["content"], rec["id"])


class RoundTwoTrackingTest(unittest.TestCase):
    def test_scoreboard_replacement_preserves_permissions(self):
        import stat
        with tempfile.TemporaryDirectory() as d:
            path = os.path.join(d, "scoreboard.json")
            scoreboard.record(path, "lane", "applied")
            os.chmod(path, 0o640)
            scoreboard.record(path, "lane", "skipped")
            self.assertEqual(stat.S_IMODE(os.stat(path).st_mode), 0o640)

    def test_scoreboard_failed_replace_cleans_temp_and_preserves_data(self):
        from unittest import mock
        with tempfile.TemporaryDirectory() as d:
            path = os.path.join(d, "scoreboard.json")
            scoreboard.record(path, "lane", "applied")
            with mock.patch("scoreboard.os.replace", side_effect=OSError("replace failed")):
                with self.assertRaises(OSError):
                    scoreboard.record(path, "lane", "dead")
            self.assertEqual(len(scoreboard.load_events(path)), 1)
            self.assertFalse(any(name.startswith(".scoreboard-") for name in os.listdir(d)))

    def test_scoreboard_clock_skew_boundary(self):
        now = datetime.datetime(2026, 1, 15, 12, tzinfo=datetime.timezone.utc)
        events = [{"lane": "a", "kind": "applied", "ts": value} for value in (
            "2026-01-15T12:05:00Z", "2026-01-15T12:05:01Z")]
        for period in ("day", "week"):
            self.assertEqual(scoreboard.totals(events, period, now=now)["overall"]["total"], 1)

    def test_scoreboard_day_uses_utc_with_offset_now(self):
        now = datetime.datetime.fromisoformat("2026-01-16T01:00:00+02:00")
        events = [{"lane": "a", "kind": "applied", "ts": "2026-01-15T12:00:00Z"},
                  {"lane": "a", "kind": "applied", "ts": "2026-01-16T00:00:00Z"}]
        self.assertEqual(scoreboard.totals(events, "day", now=now)["overall"]["total"], 1)

    def test_analytics_keeps_last_normalized_url_without_merging_url_less_rows(self):
        records = [
            {"url": "https://example.com/job/?utm_source=a", "company": "Old", "status": "Applied"},
            {"url": "https://example.com/job", "company": "New", "status": "Interview"},
            {"company": "Other", "status": "Applied"},
            {"url": "", "company": "Other", "status": "Applied"}]
        stats = analytics.funnel_stats(records)
        self.assertEqual(stats["total"], 3)
        self.assertNotIn("old", stats["companies"])
        self.assertEqual(stats["overall"]["stages"]["interview"], 1)

    def test_analytics_missing_input_is_friendly(self):
        with tempfile.TemporaryDirectory() as d:
            result = subprocess.run([sys.executable, analytics.__file__, "--in", os.path.join(d, "missing.jsonl")],
                                    capture_output=True, text=True)
        self.assertNotEqual(result.returncode, 0)
        self.assertNotIn("Traceback", result.stderr)
        self.assertIn("missing.jsonl", result.stderr)

    def test_analytics_default_input_independent_of_cwd(self):
        from unittest import mock
        import io
        original_cwd = os.getcwd()
        with tempfile.TemporaryDirectory() as d, mock.patch.dict(os.environ, {}, clear=True):
            os.chdir(d)
            try:
                with mock.patch("sys.argv", ["analytics.py", "--json"]), mock.patch("builtins.open", return_value=io.StringIO("")) as opened, mock.patch("sys.stdout", new_callable=io.StringIO):
                    analytics.main()
                self.assertEqual(os.path.abspath(opened.call_args.args[0]),
                                 os.path.join(os.path.dirname(analytics.__file__), "applications.jsonl"))
            finally:
                os.chdir(original_cwd)

    def test_tracker_url_parsers_reject_non_https_schemes(self):
        for scheme in ("http", "ftp", "file", "javascript"):
            for parser, url in ((parse_sheets_id, "docs.google.com/spreadsheets/d/" + SHEET),
                                (parse_notion_db_id, "notion.so/" + HEX)):
                with self.subTest(scheme=scheme, url=url), self.assertRaises(ValueError):
                    parser(scheme + "://" + url)

    def test_notion_rejects_noncanonical_hyphen_placement(self):
        for value in ("-" + HEX, HEX + "-", HEX[:4] + "-" + HEX[4:], HYPH.replace("-", "--")):
            with self.subTest(value=value), self.assertRaises(ValueError):
                parse_notion_db_id(value)

    def test_terminal_sanitizer_handles_osc_st_unfinished_and_all_controls(self):
        from terminal_output import sanitize_terminal
        self.assertEqual(sanitize_terminal("Café\x1b]8;;https://example.com\x1b\\Link\x1b]8;;\x1b\\"), "CaféLink")
        self.assertEqual(sanitize_terminal("ok\x1b]0;unfinished"), "ok")
        self.assertEqual(sanitize_terminal("ok\x1b]0;hidden\x1b[31mtext\x07end"), "okend")
        self.assertEqual(sanitize_terminal("ok\x1b[31"), "ok")
        self.assertEqual(sanitize_terminal("ok" + "".join(chr(i) for i in range(32)) + chr(127)), "ok")

    def test_scoreboard_clock_skew_never_crosses_utc_day(self):
        now = datetime.datetime(2026, 1, 15, 23, 59, tzinfo=datetime.timezone.utc)
        events = [{"lane": "a", "kind": "applied", "ts": "2026-01-16T00:01:00Z"}]
        self.assertEqual(scoreboard.totals(events, "day", now=now)["overall"]["total"], 0)
        self.assertEqual(scoreboard.totals(events, "week", now=now)["overall"]["total"], 1)

    def test_analytics_configured_default_input(self):
        with tempfile.TemporaryDirectory() as d:
            path = os.path.join(d, "chosen.jsonl")
            with open(path, "w") as f:
                f.write(json.dumps({"url": "https://example.com/job", "status": "Applied"}) + "\n")
            env = dict(os.environ, TRACKER_JSONL_PATH=path)
            result = subprocess.run([sys.executable, analytics.__file__, "--json"], cwd=d,
                                    env=env, capture_output=True, text=True)
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertEqual(json.loads(result.stdout)["total"], 1)

    def test_terminal_company_and_lane_output_strips_escape_sequences(self):
        hostile = "Acme\x1b[31mRed\x1b[0m\x1b]0;hijack\x07\n\r\t\x00\x7f"
        report = analytics.format_report(analytics.funnel_stats([{"company": hostile, "status": "Applied"}]))
        self.assertIn("AcmeRed", report)
        self.assertNotIn("hijack", report)
        self.assertNotIn("\x1b", report)
        totals = scoreboard.totals([{"lane": hostile, "kind": "applied", "ts": "2026-01-01T00:00:00Z"}],
                                  now=datetime.datetime(2026, 1, 1, tzinfo=datetime.timezone.utc))
        self.assertIn("AcmeRed", scoreboard.format_table(totals))
        self.assertNotIn("\x1b", scoreboard.format_table(totals))


if __name__ == "__main__":
    unittest.main()
