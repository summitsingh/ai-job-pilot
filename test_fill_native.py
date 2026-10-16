import unittest
from unittest.mock import call, patch

import fill
from common import b64


class NativeTextFillTests(unittest.TestCase):
    def test_native_replace_script_selects_existing_text_without_setting_value(self):
        script = fill.native_replace_js('#profile-summary')

        self.assertIn('document.querySelector("#profile-summary")', script)
        self.assertIn('el.focus()', script)
        self.assertIn('el.select()', script)
        self.assertNotIn('el.value=', script)

    @patch("fill.cdp")
    @patch("fill.cdp_ok")
    def test_native_fallback_types_and_blurs_in_order(self, cdp_ok_mock, cdp_mock):
        cdp_ok_mock.return_value = {"result": {"ok": True}}
        cdp_mock.return_value = {"ok": True}

        ok, error = fill.fill_text_native(9450, '#profile-summary', 'Example text')

        self.assertTrue(ok)
        self.assertEqual(error, "")
        self.assertEqual(cdp_ok_mock.call_args, call(
            9450, "evalb64", b64(fill.native_replace_js('#profile-summary')),
            timeout=60))
        self.assertEqual(cdp_mock.call_args_list, [
            call(9450, "ftype", b64('#profile-summary'), b64('Example text')),
            call(9450, "fkey", "Tab"),
        ])

    @patch("fill.cdp")
    @patch("fill.cdp_ok")
    def test_native_fallback_contains_driver_errors(self, cdp_ok_mock, cdp_mock):
        cdp_ok_mock.return_value = {"result": {"ok": True}}
        cdp_mock.side_effect = RuntimeError("driver unavailable")

        ok, error = fill.fill_text_native(9450, '#profile-summary', 'Example text')

        self.assertFalse(ok)
        self.assertEqual(error, "native-type:RuntimeError")
        cdp_mock.assert_called_once_with(
            9450, "ftype", b64('#profile-summary'), b64('Example text'))

    @patch("fill.cdp")
    @patch("fill.cdp_ok")
    def test_native_fallback_stops_when_field_cannot_be_selected(self, cdp_ok_mock, cdp_mock):
        cdp_ok_mock.return_value = {"result": {"ok": False}}

        ok, error = fill.fill_text_native(9450, '#missing', 'Example text')

        self.assertFalse(ok)
        self.assertEqual(error, "native-select-failed")
        cdp_mock.assert_not_called()


    @patch("fill.audit_form", return_value=[])
    @patch("fill.ensure_driver")
    @patch("fill.cdp")
    @patch("fill.cdp_ok")
    @patch("fill.readback_js", return_value="readback")
    @patch("fill.batch_fill_js", return_value="batch")
    def test_apply_fill_uses_trusted_typing_and_verifies(self, batch_mock,
                                                         readback_mock,
                                                         cdp_ok_mock,
                                                         cdp_mock,
                                                         driver_mock,
                                                         audit_mock):
        def answer_cdp(port, command, payload, **kwargs):
            if payload == b64("batch"):
                return {"result": [{"key": "#profile-summary", "ok": True}]}
            if payload == b64("readback"):
                return {"result": [{
                    "key": "#profile-summary", "found": True, "value": "Example text",
                }]}
            if payload == b64(fill.native_replace_js("#profile-summary")):
                return {"result": {"ok": True}}
            return {"result": []}

        cdp_ok_mock.side_effect = answer_cdp
        cdp_mock.return_value = {"ok": True}
        schema = {"fields": [{
            "key": "#profile-summary", "label": "Summary", "type": "textarea",
        }]}
        fmap = {"map": [{
            "field": "#profile-summary", "action": "fill", "value": "Example text",
        }]}

        with patch.object(fill, "DUMP_JS", "live"):
            result = fill.apply_fill(schema, fmap, 9450, True, "")

        self.assertEqual(result["fields_filled"], ["#profile-summary"])
        self.assertEqual(result["fields_mismatched"], [])
        self.assertFalse(result["submitted"])
        self.assertEqual(cdp_mock.call_args_list, [
            call(9450, "ftype", b64("#profile-summary"), b64("Example text")),
            call(9450, "fkey", "Tab"),
        ])

    @patch("fill.fill_text_native")
    @patch("fill.cdp_ok")
    def test_mismatched_control_retries_with_trusted_input(self, cdp_ok_mock,
                                                           native_mock):
        cdp_ok_mock.return_value = {"result": [{
            "key": "#profile-summary", "found": True, "value": "",
        }]}
        native_mock.return_value = (True, "")
        items = [{"key": "#profile-summary", "value": "Example text"}]
        by_key = {"#profile-summary": {"type": "textarea"}}

        result = fill.retry_mismatched_text(9450, items, by_key)

        self.assertEqual(result, {
            "#profile-summary": {"ok": True, "note": "trusted-input-fallback"},
        })
        native_mock.assert_called_once_with(9450, "#profile-summary", "Example text")

    @patch("fill.fill_text_native")
    @patch("fill.cdp_ok")
    def test_matching_control_does_not_retry(self, cdp_ok_mock, native_mock):
        cdp_ok_mock.return_value = {"result": [{
            "key": "#profile-summary", "found": True, "value": "Example text",
        }]}
        items = [{"key": "#profile-summary", "value": "Example text"}]
        by_key = {"#profile-summary": {"type": "textarea"}}

        result = fill.retry_mismatched_text(9450, items, by_key)

        self.assertEqual(result, {})
        native_mock.assert_not_called()

    @patch("fill.fill_text_native")
    @patch("fill.cdp_ok")
    def test_force_trusted_typing_when_dom_value_matches(self, cdp_ok_mock,
                                                         native_mock):
        cdp_ok_mock.side_effect = AssertionError("forced typing must not depend on readback")
        native_mock.return_value = (True, "")
        items = [{"key": "#name", "value": "Summit Singh Thakur"}]
        by_key = {"#name": {"type": "text"}}

        result = fill.retry_mismatched_text(9450, items, by_key,
                                            always_type=True)

        self.assertEqual(result, {
            "#name": {"ok": True, "note": "trusted-input-fallback"},
        })
        native_mock.assert_called_once_with(9450, "#name", "Summit Singh Thakur")

    @patch("fill.fill_text_native")
    @patch("fill.cdp_ok")
    def test_number_field_mismatch_remains_fail_closed(self, cdp_ok_mock,
                                                        native_mock):
        cdp_ok_mock.return_value = {"result": [{
            "key": "#salary", "found": True, "value": "",
        }]}
        items = [{"key": "#salary", "value": "120000"}]
        by_key = {"#salary": {"type": "number"}}

        result = fill.retry_mismatched_text(9450, items, by_key)

        self.assertEqual(result, {})
        native_mock.assert_not_called()

    def test_unverified_fill_blocks_submission_even_when_audit_has_no_blockers(self):
        self.assertEqual(
            fill.pre_submit_blockers([], ["#required-radio"]),
            ["unverified-fill:#required-radio"],
        )

    def test_failed_trusted_text_input_blocks_even_if_dom_matches(self):
        self.assertEqual(
            fill.pre_submit_blockers([], [], ["#required-name"]),
            ["trusted-input-failed:#required-name"],
        )

    def test_grouped_radio_click_targets_associated_label_geometry(self):
        script = fill.group_option_click_js(
            '[data-field-path="experience"]', 'input[type="radio"]', '6-10 years'
        )

        self.assertIn("const target = (el.labels && el.labels[0]) || el.closest('label')", script)
        self.assertIn("const r = target.getBoundingClientRect()", script)
        self.assertNotIn("const r = el.getBoundingClientRect()", script)

    def test_grouped_radio_uses_trusted_click_on_label_target(self):
        schema = {"fields": [{
            "key": "#radio-option", "type": "radio", "grouped": True,
            "dfp": "experience", "option_label": "6-10 years",
        }]}
        fmap = {"map": [{
            "field": "#radio-option", "action": "click", "value": "6-10 years",
        }]}
        status_js = fill.group_option_status_js("scope", "inputs", "6-10 years")
        status_calls = 0

        def answer_cdp_ok(port, command, payload, **kwargs):
            nonlocal status_calls
            if payload == b64("readback"):
                return {"result": [{
                    "key": "#radio-option", "found": True, "value": "checked",
                }]}
            if payload == b64("live"):
                return {"result": {"fields": []}}
            if payload == b64(status_js):
                status_calls += 1
                return {"result": {"found": True, "checked": status_calls >= 2}}
            return {"result": {"ok": True}}

        with (
            patch.object(fill, "ensure_driver"),
            patch.object(fill, "group_scope_inputs", return_value=("scope", "inputs")),
            patch.object(fill, "cdp_ok", side_effect=answer_cdp_ok),
            patch.object(fill, "cdp", return_value={"ok": True}) as cdp_mock,
            patch.object(fill, "readback_js", return_value="readback"),
            patch.object(fill, "audit_form", return_value=[]),
            patch.object(fill, "DUMP_JS", "live"),
            patch.object(fill.time, "sleep", return_value=None),
        ):
            result = fill.apply_fill(schema, fmap, 9450, True, "")

        cdp_mock.assert_called_once_with(
            9450, "fclickel", b64(fill.group_option_click_js("scope", "inputs", "6-10 years"))
        )
        self.assertGreaterEqual(status_calls, 2)
        self.assertEqual(result["fields_mismatched"], [])
        self.assertFalse(result["submitted"])

    def test_grouped_radio_skips_click_when_option_is_already_selected(self):
        schema = {"fields": [{
            "key": "#radio-option", "type": "radio", "grouped": True,
            "dfp": "experience", "option_label": "6-10 years",
        }]}
        fmap = {"map": [{
            "field": "#radio-option", "action": "click", "value": "6-10 years",
        }]}
        status_js = fill.group_option_status_js("scope", "inputs", "6-10 years")

        def answer_cdp_ok(port, command, payload, **kwargs):
            if payload == b64("readback"):
                return {"result": [{
                    "key": "#radio-option", "found": True, "value": "checked",
                }]}
            if payload == b64("live"):
                return {"result": {"fields": []}}
            if payload == b64(status_js):
                return {"result": {"found": True, "checked": True}}
            return {"result": {"ok": True}}

        with (
            patch.object(fill, "ensure_driver"),
            patch.object(fill, "group_scope_inputs", return_value=("scope", "inputs")),
            patch.object(fill, "cdp_ok", side_effect=answer_cdp_ok),
            patch.object(fill, "cdp") as cdp_mock,
            patch.object(fill, "readback_js", return_value="readback"),
            patch.object(fill, "audit_form", return_value=[]),
            patch.object(fill, "DUMP_JS", "live"),
        ):
            result = fill.apply_fill(schema, fmap, 9450, True, "")

        cdp_mock.assert_not_called()
        self.assertTrue(result["per_field"][0]["applied"])
        self.assertEqual(result["fields_mismatched"], [])

    def test_grouped_radio_retries_pointer_click_until_selection_persists(self):
        schema = {"fields": [{
            "key": "#radio-option", "type": "radio", "grouped": True,
            "dfp": "experience", "option_label": "6-10 years",
        }]}
        fmap = {"map": [{
            "field": "#radio-option", "action": "click", "value": "6-10 years",
        }]}
        status_js = fill.group_option_status_js("scope", "inputs", "6-10 years")
        click_count = 0

        def answer_cdp_ok(port, command, payload, **kwargs):
            if payload == b64("readback"):
                return {"result": [{
                    "key": "#radio-option", "found": True, "value": "checked",
                }]}
            if payload == b64("live"):
                return {"result": {"fields": []}}
            if payload == b64(status_js):
                return {"result": {"found": True, "checked": click_count >= 2}}
            return {"result": {"ok": True}}

        def trusted_click(*args, **kwargs):
            nonlocal click_count
            click_count += 1
            return {"ok": True}

        with (
            patch.object(fill, "ensure_driver"),
            patch.object(fill, "group_scope_inputs", return_value=("scope", "inputs")),
            patch.object(fill, "cdp_ok", side_effect=answer_cdp_ok),
            patch.object(fill, "cdp", side_effect=trusted_click) as cdp_mock,
            patch.object(fill, "readback_js", return_value="readback"),
            patch.object(fill, "audit_form", return_value=[]),
            patch.object(fill, "DUMP_JS", "live"),
            patch.object(fill.time, "sleep", return_value=None),
        ):
            result = fill.apply_fill(schema, fmap, 9450, True, "")

        self.assertEqual(click_count, 2)
        self.assertEqual(cdp_mock.call_count, 2)
        self.assertEqual(result["fields_mismatched"], [])
        self.assertFalse(result["submitted"])

    def test_unverified_radio_prevents_submit_when_required_audit_is_empty(self):
        schema = {"fields": [{
            "key": "#radio-option", "type": "radio", "grouped": True,
            "dfp": "experience", "option_label": "6-10 years",
        }]}
        fmap = {"map": [{
            "field": "#radio-option", "action": "click", "value": "6-10 years",
        }]}

        def answer_cdp_ok(port, command, payload, **kwargs):
            if payload == b64("readback"):
                return {"result": [{
                    "key": "#radio-option", "found": True, "value": "unchecked",
                }]}
            if payload == b64("live"):
                return {"result": {"fields": []}}
            return {"result": []}

        with (
            patch.object(fill, "ensure_driver"),
            patch.object(fill, "group_scope_inputs", return_value=("scope", "inputs")),
            patch.object(fill, "cdp_ok", side_effect=answer_cdp_ok),
            patch.object(fill, "cdp", return_value={"ok": True}),
            patch.object(fill, "readback_js", return_value="readback"),
            patch.object(fill, "audit_form", return_value=[]),
            patch.object(fill, "DUMP_JS", "live"),
            patch.object(fill, "resolve_submit_key") as resolve_submit_mock,
        ):
            result = fill.apply_fill(schema, fmap, 9450, False, "")

        self.assertEqual(result["fields_mismatched"], ["#radio-option"])
        self.assertIn("unverified-fill:#radio-option", result["audit_blockers"])
        self.assertFalse(result["submitted"])
        resolve_submit_mock.assert_not_called()


if __name__ == "__main__":
    unittest.main()
