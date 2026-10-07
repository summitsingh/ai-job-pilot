import unittest
from form_audit import audit_form


class PreSubmitAuditTests(unittest.TestCase):
    def test_conditional_required_field_blocks(self):
        initial = {'fields': [{'key': '#ethnicity', 'required': True}]}
        live = {'fields': initial['fields'] + [{'key': '#race', 'required': True, 'value': ''}]}
        self.assertIn('new-required:#race', audit_form(initial, live, {'map': [{'field': '#ethnicity', 'action': 'select', 'value': 'Decline'}]}, []))

    def test_skipped_required_guard_blocks(self):
        schema = {'fields': [{'key': '#attestation', 'required': True, 'value': ''}]}
        mapping = {'map': [{'field': '#attestation', 'action': 'skip', 'guard': 'human-review'}]}
        self.assertIn('unanswered-required:#attestation', audit_form(schema, schema, mapping, []))

    def test_unchecked_required_checkbox_blocks_even_if_value_is_on(self):
        schema = {'fields': [{'key': '#consent', 'type': 'checkbox', 'required': True, 'value': 'on', 'checked': False}]}
        self.assertIn('unanswered-required:#consent', audit_form(schema, schema, {'map': []}, []))

    def test_invalid_and_mismatched_fields_block(self):
        schema = {'fields': [{'key': '#city', 'required': True}]}
        blockers = audit_form(schema, schema, {'map': [{'field': '#city', 'action': 'location', 'value': 'Example City'}]}, ['#city'], ['#city'])
        self.assertIn('mismatch:#city', blockers)
        self.assertIn('invalid:#city', blockers)

    def test_optional_skip_and_filled_required_pass(self):
        schema = {'fields': [{'key': '#cover', 'required': False}, {'key': '#city', 'required': True, 'value': 'Example City'}]}
        mapping = {'map': [{'field': '#cover', 'action': 'skip'}, {'field': '#city', 'action': 'location', 'value': 'Example City'}]}
        self.assertEqual(audit_form(schema, schema, mapping, []), [])


if __name__ == '__main__':
    unittest.main()
