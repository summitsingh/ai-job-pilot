import unittest

from ats_fill import validate_mapping
from map import resolve_key


class ResolveTruncatedAshbyKeyTests(unittest.TestCase):
    def setUp(self):
        self.schema = {"fields": [
            {"key": '[data-field-path="_systemfield_name"] input[type="text"]',
             "type": "text", "label": "Name"},
            {"key": '[data-field-path="auth-group"] [data-option="yes"]',
             "type": "yesno-button", "label": "Work authorization",
             "option_label": "Yes"},
            {"key": '[data-field-path="auth-group"] [data-option="no"]',
             "type": "yesno-button", "label": "Work authorization",
             "option_label": "No"},
        ]}

    def test_resolves_truncated_system_field_selector(self):
        key = '[data-field-path="_systemfield_name"]'
        self.assertEqual(resolve_key(key, self.schema), self.schema["fields"][0]["key"])

    def test_disambiguates_truncated_yesno_selector_by_answer(self):
        key = '[data-field-path="auth-group"]'
        self.assertEqual(resolve_key(key, self.schema, "Yes"), self.schema["fields"][1]["key"])
        self.assertEqual(resolve_key(key, self.schema, "No"), self.schema["fields"][2]["key"])


class ValidateMappingTests(unittest.TestCase):
    def test_refuses_empty_schema(self):
        with self.assertRaisesRegex(RuntimeError, "schema extraction returned zero fields"):
            validate_mapping({"fields": []})

    def test_refuses_empty_mapping_for_nonempty_schema(self):
        schema = {"fields": [{"key": "field-a"}]}
        with self.assertRaisesRegex(RuntimeError, "mapping returned zero fields"):
            validate_mapping(schema, {"map": [], "unmapped": ["field-a"]})

    def test_accepts_nonempty_mapping(self):
        schema = {"fields": [{"key": "field-a"}]}
        mapping = {"map": [{"field": "field-a", "action": "fill"}]}
        self.assertIsNone(validate_mapping(schema, mapping))


if __name__ == "__main__":
    unittest.main()
