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
import unittest

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from templates import map_template
from hard_patterns import matches

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
        self.assertTrue(matches("[PHONE-REDACTED]", "[PHONE-REDACTED]"))


if __name__ == "__main__":
    unittest.main()
