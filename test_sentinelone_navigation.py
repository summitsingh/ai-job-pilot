import unittest
from unittest.mock import patch

import schema_dump


class SentinelOneNavigationTests(unittest.TestCase):
    def test_refreshes_embed_url_from_parent_page_each_run(self):
        parent = "https://www.sentinelone.com/jobs/?gh_jid=7704693003"
        iframe = (
            "https://job-boards.greenhouse.io/embed/job_app?token=7704693003"
            "&validityToken=short-lived-secret")
        calls = []

        def fake_cdp(port, command, *args, **kwargs):
            calls.append((command, args[0] if args else None))
            if command == "evalb64":
                return {"result": iframe}
            return {}

        with (
            patch.object(schema_dump, "cdp_ok", side_effect=fake_cdp),
            patch.object(schema_dump.time, "monotonic", side_effect=[0, 1]),
            patch.object(schema_dump.time, "sleep"),
        ):
            schema_dump._navigate_to_application(parent, 9226)

        self.assertEqual([c[0] for c in calls], ["goto", "evalb64", "goto"])
        import base64
        navigated = base64.b64decode(calls[-1][1]).decode()
        self.assertIn("short-lived-secret", navigated)


if __name__ == "__main__":
    unittest.main()
