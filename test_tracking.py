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
import tempfile
import unittest

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import analytics
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

    def test_constructor_uses_parser(self):
        t = SheetsTracker(
            spreadsheet_id="https://docs.google.com/spreadsheets/d/%s/edit"
            % SHEET, credentials_path="/nonexistent.json")
        self.assertEqual(t.spreadsheet_id, SHEET)
        with self.assertRaises(ValueError):
            SheetsTracker(spreadsheet_id="https://example.com/x",
                          credentials_path="/nonexistent.json")


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
        self.assertEqual(o["stages"], {"applied": 5, "responded": 3,
                                       "screening": 2, "interview": 2,
                                       "offer": 1})
        self.assertAlmostEqual(o["conversion"]["applied->responded"], 0.6)
        self.assertAlmostEqual(o["conversion"]["responded->screening"],
                               2 / 3, places=3)
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
        self.assertEqual(st["overall"]["other"], 2)
        self.assertEqual(st["unknown_statuses"], {"Skipped": 1, "(empty)": 1})
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
        b = {"company": "Acme", "title": "Staff Backend Engineer",
             "location": "austin, tx",
             "url": "https://jobs.lever.co/acme/abc"}
        self.assertTrue(sweep.near_duplicate(a, b))

    def test_company_suffix_variants(self):
        a = {"company": "Acme Inc.", "title": "Senior Platform Engineer",
             "location": "Remote"}
        b = {"company": "acme", "title": "Sr. Platform Engineer",
             "location": "Remote"}
        c = {"company": "ACME, LLC", "title": "Platform Engineer",
             "location": "Remote"}
        self.assertTrue(sweep.near_duplicate(a, b))
        self.assertTrue(sweep.near_duplicate(a, c))

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
             {"company": "acme inc", "title": "Backend Engineer",
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


if __name__ == "__main__":
    unittest.main()
