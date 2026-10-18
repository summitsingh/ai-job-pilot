import unittest

from schema_dump import (
    _redact_greenhouse_validity_token,
    _sentinelone_job_id,
    _valid_sentinelone_embed_src,
)


class SentinelOneGreenhouseEmbedTests(unittest.TestCase):
    def test_recognizes_only_supported_sentinelone_job_parent(self):
        self.assertEqual(
            _sentinelone_job_id("https://www.sentinelone.com/jobs/?gh_jid=7704693003"),
            "7704693003",
        )
        self.assertIsNone(
            _sentinelone_job_id("https://evil.example/jobs/?gh_jid=7704693003")
        )
        self.assertIsNone(
            _sentinelone_job_id("https://www.sentinelone.com/jobs/?gh_jid=not-a-number")
        )

    def test_accepts_only_fresh_greenhouse_embed_for_same_job(self):
        parent = "https://www.sentinelone.com/jobs/?gh_jid=7704693003"
        good = (
            "https://job-boards.greenhouse.io/embed/job_app"
            "?for=sentinellabs&validityToken=ephemeral&token=7704693003"
        )
        self.assertTrue(_valid_sentinelone_embed_src(parent, good))
        self.assertFalse(_valid_sentinelone_embed_src(
            parent, good.replace("token=7704693003", "token=7704693004")
        ))
        self.assertFalse(_valid_sentinelone_embed_src(
            parent, good.replace("validityToken=ephemeral", "")
        ))
        self.assertFalse(_valid_sentinelone_embed_src(
            parent, good.replace("job-boards.greenhouse.io", "evil.example")
        ))

    def test_redacts_ephemeral_token_without_damaging_other_query_values(self):
        url = (
            "https://job-boards.greenhouse.io/embed/job_app?for=sentinellabs"
            "&validityToken=secret-value&token=7704693003"
        )
        redacted = _redact_greenhouse_validity_token(url)
        self.assertNotIn("secret-value", redacted)
        self.assertIn("validityToken=%5BREDACTED%5D", redacted)
        self.assertIn("token=7704693003", redacted)


if __name__ == "__main__":
    unittest.main()
