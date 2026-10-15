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
    def test_apply_fill_retries_dom_value_mismatch_and_verifies(self, batch_mock,
                                                                readback_mock,
                                                                cdp_ok_mock,
                                                                cdp_mock,
                                                                driver_mock,
                                                                audit_mock):
        readback_calls = 0

        def answer_cdp(port, command, payload, **kwargs):
            nonlocal readback_calls
            if payload == b64("batch"):
                return {"result": [{"key": "#profile-summary", "ok": True}]}
            if payload == b64("readback"):
                readback_calls += 1
                value = "" if readback_calls == 1 else "Example text"
                return {"result": [{
                    "key": "#profile-summary", "found": True, "value": value,
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


if __name__ == "__main__":
    unittest.main()
