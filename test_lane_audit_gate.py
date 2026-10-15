import json
import os
import tempfile
import unittest
from types import SimpleNamespace
from unittest import mock

import lane_greenhouse


class LaneAuditGateTests(unittest.TestCase):
    def test_blocks_mismatched_fill_before_submit(self):
        job_url = "https://job-boards.greenhouse.io/acme/jobs/123"
        schema = {
            "fields": [{
                "key": "#email",
                "label": "Email address",
                "type": "email",
                "required": True,
            }]
        }
        mapping = {
            "map": [{"field": "#email", "action": "fill", "value": "candidate@example.com"}],
            "skipped_by_guard": [],
            "unmapped": [],
        }
        fill_result = {
            "per_field": [{
                "field": "#email",
                "action": "fill",
                "expected": "candidate@example.com",
                "actual": "wrong@example.com",
                "applied": True,
                "verified": False,
            }],
            "audit_blockers": ["mismatch:#email"],
        }

        with tempfile.TemporaryDirectory() as td:
            facts = os.path.join(td, "facts.json")
            with open(facts, "w", encoding="utf-8") as f:
                json.dump({}, f)

            def schema_dump(argv, **kwargs):
                out_path = argv[argv.index("--out") + 1]
                with open(out_path, "w", encoding="utf-8") as f:
                    json.dump(schema, f)
                return SimpleNamespace(returncode=0, stdout="", stderr="")

            def cdp(_url, command, *args, **kwargs):
                if command == "url":
                    return {"url": job_url}
                if command == "text":
                    return {"text": "Application form"}
                if command == "evalb64":
                    return {"result": []}
                return {"ok": True}

            cfg = {
                "cdp_url": "127.0.0.1:9226",
                "port": 9226,
                "workdir": td,
                "lane": "test-lane",
                "facts": facts,
                "claims": os.path.join(td, "claims.jsonl"),
                "queue": os.path.join(td, "queue.json"),
                "target_metros": [],
            }

            with (
                mock.patch("lane_greenhouse.cdp", side_effect=cdp),
                mock.patch("lane_greenhouse.subprocess.run", side_effect=schema_dump),
                mock.patch("lane_greenhouse.time.sleep"),
                mock.patch("lane_greenhouse.guard_perplexity"),
                mock.patch("map.map_fields", return_value=mapping),
                mock.patch("fill.apply_fill", return_value=fill_result),
                mock.patch("lane_greenhouse.already_applied", return_value=False),
                mock.patch("lane_greenhouse.heartbeat", return_value=True),
                mock.patch("lane_greenhouse.initial_submit", return_value={"clicked": True}) as submit,
                mock.patch("lane_greenhouse.check_submit", return_value={
                    "outcome": "confirmed",
                    "evidence": {"final_url": job_url, "confirmation_phrase": "Thank you for applying"},
                    "ladder": [],
                }) as watchdog,
            ):
                result = lane_greenhouse.run_job({"url": job_url}, cfg, no_submit=False)

        self.assertEqual(result["status"], "blocked")
        self.assertIn("mismatch:#email", result["reason"])
        submit.assert_not_called()
        watchdog.assert_not_called()


if __name__ == "__main__":
    unittest.main()
