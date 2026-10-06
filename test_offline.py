#!/usr/bin/env python3
"""Offline unit tests for jobpilot's deterministic core.

No browser, no model, no network. Covers:
  - templates.map_template on synthetic fields (pure function)
  - hard_patterns.matches (fuzzy label matcher)

Run: python3 -m unittest test_offline -v
"""
import json
import os
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from templates import map_template
from hard_patterns import (matches, pick_sponsorship_option,
                            is_country_visa_option, is_sponsorship_field)
from batch_apply import load_dedup_set, match_resume

HERE = os.path.dirname(os.path.abspath(__file__))


def load_example_facts():
    with open(os.path.join(HERE, "facts.example.json")) as fh:
        return json.load(fh)


def mapped(out):
    return {e["field"]: e for e in out["map"]}


class TestMapTemplate(unittest.TestCase):
    def setUp(self):
        self.facts = load_example_facts()

    def test_identity_fill(self):
        schema = {"fields": [
            {"key": "#first", "label": "First Name", "type": "text"},
            {"key": "#last", "label": "Last Name", "type": "text"},
            {"key": "#email", "label": "Email", "type": "email"},
        ]}
        m = mapped(map_template(schema, self.facts, "greenhouse"))
        self.assertEqual(m["#first"]["action"], "fill")
        self.assertEqual(m["#first"]["value"], "Alex")
        self.assertEqual(m["#last"]["value"], "Carter")
        self.assertEqual(m["#email"]["value"], "alex.carter@example.com")

    def test_unknown_field_skipped_never_guessed(self):
        schema = {"fields": [
            {"key": "#x", "label": "What is your favorite dinosaur?",
             "type": "text"},
        ]}
        m = mapped(map_template(schema, self.facts, "greenhouse"))
        self.assertEqual(m["#x"]["action"], "skip")
        self.assertIn("template-no-pattern", m["#x"].get("note", ""))

    def test_veteran_radio_never_invented(self):
        # No matching option in the form and no veteran fact: skip,
        # never pick an option at random.
        schema = {"fields": [
            {"key": "#v", "label": "Are you a veteran?", "type": "radio",
             "options": ["Maybe", "Prefer not to answer"]},
        ]}
        m = mapped(map_template(schema, self.facts, "greenhouse"))
        self.assertEqual(m["#v"]["action"], "skip")

    def test_sponsorship_picks_truthful_option(self):
        facts = dict(self.facts)
        facts["work_authorization"] = "H1B visa, eligible for H1B transfer"
        schema = {"fields": [
            {"key": "#s",
             "label": "Do you need sponsorship now or will you require "
                      "sponsorship in the future?",
             "type": "select",
             "options": ["Yes", "No", "Yes, I will require sponsorship"]},
        ]}
        m = mapped(map_template(schema, facts, "greenhouse"))
        # Truthful sponsorship option, never plain "Yes".
        self.assertEqual(m["#s"]["value"], "Yes, I will require sponsorship")

    def test_school_select_driven_by_facts(self):
        schema = {"fields": [
            {"key": "#school", "label": "School", "type": "select",
             "options": ["State University", "Tech Institute", "Other"]},
        ]}
        m = mapped(map_template(schema, self.facts, "greenhouse"))
        # Most-recent school from facts.json education, not a hardcoded one.
        self.assertEqual(m["#school"]["action"], "select")
        self.assertEqual(m["#school"]["value"], "Tech Institute")

    def test_phone_digits_only(self):
        schema = {"fields": [
            {"key": "#p", "label": "Phone", "type": "tel"},
        ]}
        m = mapped(map_template(schema, self.facts, "greenhouse"))
        self.assertEqual(m["#p"]["value"], "5551234567")

    def test_zip_code_from_facts(self):
        schema = {"fields": [
            {"key": "#z", "label": "ZIP Code", "type": "text"},
        ]}
        m = mapped(map_template(schema, self.facts, "greenhouse"))
        self.assertEqual(m["#z"]["action"], "fill")
        self.assertEqual(m["#z"]["value"],
                         self.facts.get("zip_code") or
                         self.facts.get("postal_code") or "")

    def test_prev_employed_defaults_no(self):
        schema = {"fields": [
            {"key": "#pe", "label": "Have you previously worked here?",
             "type": "select", "options": ["Yes", "No"]},
        ]}
        m = mapped(map_template(schema, self.facts, "greenhouse"))
        self.assertEqual(m["#pe"]["action"], "select")
        self.assertIn("No", m["#pe"]["value"])

    def test_work_eligible_yes(self):
        schema = {"fields": [
            {"key": "#we", "label": "Are you legally eligible to work?",
             "type": "select", "options": ["Yes", "No"]},
        ]}
        m = mapped(map_template(schema, self.facts, "greenhouse"))
        self.assertEqual(m["#we"]["action"], "select")


class TestMatches(unittest.TestCase):
    def test_case_insensitive(self):
        self.assertTrue(matches("I am not a veteran", "I AM NOT A VETERAN"))

    def test_substring(self):
        self.assertTrue(matches("Yes", "yes, definitely"))

    def test_mismatch(self):
        self.assertFalse(matches("No", "Yes"))

    def test_empty_expected_matches_anything(self):
        self.assertTrue(matches("", "anything"))

    def test_digit_tolerant(self):
        self.assertTrue(matches("5551234567", "(555) 123-4567"))


class TestSponsorship(unittest.TestCase):
    def test_picks_yes_over_country_visa(self):
        idx, text = pick_sponsorship_option(
            ["Netherlands Highly Skilled Migrant Visa", "Yes"])
        self.assertEqual((idx, text), (1, "Yes"))

    def test_rejects_all_country_visa_options(self):
        idx, text = pick_sponsorship_option(
            ["Netherlands Highly Skilled Migrant Visa",
             "Germany EU Blue Card visa"])
        self.assertIsNone(idx)
        self.assertIsNone(text)

    def test_fails_loudly_when_no_safe_option(self):
        idx, text = pick_sponsorship_option(["Maybe", "Prefer not to say"])
        self.assertIsNone(idx)
        self.assertIsNone(text)

    def test_substring_fallback_never_picks_country_visa(self):
        # A visa option whose text mentions sponsorship must still be
        # rejected by the country-visa filter before the substring fallback.
        idx, text = pick_sponsorship_option(
            ["I will require sponsorship: Netherlands visa"])
        self.assertIsNone(idx)

    def test_country_visa_detection(self):
        self.assertTrue(is_country_visa_option(
            "Netherlands Highly Skilled Migrant Visa"))
        self.assertTrue(is_country_visa_option("Germany EU Blue Card visa"))
        self.assertTrue(is_country_visa_option("UK Skilled Worker visa"))
        self.assertFalse(is_country_visa_option(
            "Yes, I will require sponsorship"))
        self.assertFalse(is_country_visa_option("No"))

    def test_sponsorship_field_detection(self):
        self.assertTrue(is_sponsorship_field("sponsorship_question", "Yes"))
        self.assertTrue(is_sponsorship_field("visa_type", ""))
        self.assertTrue(is_sponsorship_field("q1", "work authorization"))
        self.assertFalse(is_sponsorship_field("first_name", "John"))


class TestDedup(unittest.TestCase):
    def _write_log(self, records):
        fd, path = tempfile.mkstemp(suffix=".jsonl")
        with os.fdopen(fd, "w") as fh:
            for rec in records:
                fh.write(json.dumps(rec) + "\n")
        self.addCleanup(os.unlink, path)
        return path

    def test_loads_urls(self):
        p = self._write_log([
            {"url": "https://a.example/1", "status": "ok"},
            {"url": "https://b.example/2", "status": "ok"},
        ])
        seen = load_dedup_set(p)
        self.assertEqual(seen, {"https://a.example/1", "https://b.example/2"})

    def test_skips_bad_lines(self):
        fd, path = tempfile.mkstemp(suffix=".jsonl")
        with os.fdopen(fd, "w") as fh:
            fh.write('{"url": "https://a.example/1"}\n')
            fh.write("not json at all\n")
            fh.write('{"nourl": true}\n')
        self.addCleanup(os.unlink, path)
        self.assertEqual(load_dedup_set(path), {"https://a.example/1"})

    def test_missing_file_is_empty(self):
        self.assertEqual(load_dedup_set("/tmp/definitely-not-here-123.jsonl"),
                         set())


class TestResumeMap(unittest.TestCase):
    MAP = {
        "_comment": "ignored",
        "greenhouse.io/exampleco": "/tmp/res-a.pdf",
        "ashbyhq.com": "/tmp/res-b.pdf",
    }

    def test_match(self):
        self.assertEqual(
            match_resume("https://job-boards.greenhouse.io/exampleco/jobs/1",
                         self.MAP),
            "/tmp/res-a.pdf")

    def test_no_match(self):
        self.assertIsNone(
            match_resume("https://jobs.lever.co/other/abc", self.MAP))

    def test_comment_keys_ignored(self):
        self.assertIsNone(match_resume("https://_comment.example/", self.MAP))

    def test_first_match_wins(self):
        m = {"example": "/tmp/first.pdf", "exampleco": "/tmp/second.pdf"}
        self.assertEqual(match_resume("https://x.exampleco/y", m),
                         "/tmp/first.pdf")


if __name__ == "__main__":
    unittest.main()
