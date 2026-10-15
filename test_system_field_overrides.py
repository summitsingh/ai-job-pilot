import unittest
from unittest.mock import patch

from map import map_fields


class SystemFieldOverrideTests(unittest.TestCase):
    def test_required_ashby_system_fields_use_facts_when_model_omits_them(self):
        schema = {
            "fields": [
                {"key": '[data-field-path="_systemfield_name"] input[type="text"]',
                 "type": "text", "label": "Name", "required": True},
                {"key": '[data-field-path="_systemfield_email"] input[type="email"]',
                 "type": "email", "label": "Email", "required": True},
                {"key": '[data-field-path="_systemfield_resume"] input[type="file"]',
                 "type": "file", "label": "Resume", "required": True},
            ]
        }
        facts = {
            "full_name": "Test Candidate",
            "email": "candidate@example.test",
            "resume_path": "/tmp/test-resume.pdf",
        }
        with patch("map._map_with_model", return_value=([], [], set())) as model:
            result = map_fields(schema, facts, ats="ashby")

        mapped = {entry["field"]: entry for entry in result["map"]}
        expected = {
            schema["fields"][0]["key"]: ("fill", facts["full_name"]),
            schema["fields"][1]["key"]: ("fill", facts["email"]),
            schema["fields"][2]["key"]: ("upload", facts["resume_path"]),
        }
        for key, (action, value) in expected.items():
            with self.subTest(field=key):
                self.assertEqual((mapped[key]["action"], mapped[key]["value"]),
                                 (action, value))
                self.assertNotIn(key, result["unmapped"])
        model.assert_not_called()

    def test_missing_facts_keep_system_fields_skipped_and_unmapped(self):
        fields = [
            {"key": '[data-field-path="_systemfield_name"] input[type="text"]',
             "type": "text", "label": "Full name", "required": True},
            {"key": '[data-field-path="_systemfield_email"] input[type="email"]',
             "type": "email", "label": "Email", "required": True},
            {"key": '[data-field-path="_systemfield_resume"] input[type="file"]',
             "type": "file", "label": "Resume", "required": True},
        ]
        schema = {"fields": fields}
        with patch("map._map_with_model", return_value=([], [], set())) as model:
            result = map_fields(schema, {}, ats="ashby")

        mapped = {entry["field"]: entry for entry in result["map"]}
        expected_notes = {
            fields[0]["key"]: "missing-fact:full_name",
            fields[1]["key"]: "missing-fact:email",
            fields[2]["key"]: "missing-fact:resume_path",
        }
        for key, note in expected_notes.items():
            with self.subTest(field=key):
                self.assertEqual(mapped[key]["action"], "skip")
                self.assertEqual(mapped[key].get("note"), note)
                self.assertIn(key, result["unmapped"])
        model.assert_not_called()

    def test_blank_facts_do_not_fill_system_fields(self):
        fields = [
            {"key": '[data-field-path="_systemfield_name"] input[type="text"]',
             "type": "text", "label": "Full name", "required": True},
            {"key": '[data-field-path="_systemfield_email"] input[type="email"]',
             "type": "email", "label": "Email", "required": True},
            {"key": '[data-field-path="_systemfield_resume"] input[type="file"]',
             "type": "file", "label": "Resume", "required": True},
        ]
        facts = {"full_name": "   ", "email": chr(9), "resume_path": ""}
        with patch("map._map_with_model", return_value=([], [], set())) as model:
            result = map_fields({"fields": fields}, facts, ats="ashby")

        mapped = {entry["field"]: entry for entry in result["map"]}
        for field in fields:
            with self.subTest(field=field["key"]):
                self.assertEqual(mapped[field["key"]]["action"], "skip")
                self.assertIn(field["key"], result["unmapped"])
        model.assert_not_called()


if __name__ == "__main__":
    unittest.main()
