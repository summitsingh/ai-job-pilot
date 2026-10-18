import unittest
from unittest.mock import patch

import fill
from common import b64


class CaptchaGuardTests(unittest.TestCase):
    def test_captcha_presence_blocks_submit_before_submit_selector_resolution(self):
        def answer_cdp_ok(port, command, payload, **kwargs):
            if payload == b64("live"):
                return {"result": {"fields": []}}
            if payload == b64(fill.CAPTCHA_PRESENT_JS):
                return {"result": {"present": True, "evidence": ["recaptcha-iframe"]}}
            return {"result": []}

        with (
            patch.object(fill, "ensure_driver"),
            patch.object(fill, "cdp_ok", side_effect=answer_cdp_ok),
            patch.object(fill, "audit_form", return_value=[]),
            patch.object(fill, "DUMP_JS", "live"),
            patch.object(fill, "resolve_submit_key") as resolve_submit,
        ):
            result = fill.apply_fill({"fields": []}, {"map": []}, 9450, False, "")

        self.assertFalse(result["submitted"])
        self.assertIn("captcha-required:human-verification", result["audit_blockers"])
        self.assertTrue(any("captcha" in note.lower() for note in result["notes"]))
        resolve_submit.assert_not_called()

    def test_unavailable_captcha_check_fails_closed(self):
        def answer_cdp_ok(port, command, payload, **kwargs):
            if payload == b64("live"):
                return {"result": {"fields": []}}
            if payload == b64(fill.CAPTCHA_PRESENT_JS):
                return {"result": None}
            return {"result": []}

        with (
            patch.object(fill, "ensure_driver"),
            patch.object(fill, "cdp_ok", side_effect=answer_cdp_ok),
            patch.object(fill, "audit_form", return_value=[]),
            patch.object(fill, "DUMP_JS", "live"),
            patch.object(fill, "resolve_submit_key") as resolve_submit,
        ):
            result = fill.apply_fill({"fields": []}, {"map": []}, 9450, False, "")

        self.assertFalse(result["submitted"])
        self.assertIn("captcha-required:human-verification", result["audit_blockers"])
        self.assertIn("captcha-check-failed", " ".join(result["notes"]))
        resolve_submit.assert_not_called()


if __name__ == "__main__":
    unittest.main()
