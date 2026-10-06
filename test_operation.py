#!/usr/bin/env python3
"""Offline tests for the operation layer: queue, tracker, sweep.

No browser, no model, no network. Covers:
  - queue.claim / mark / release / next_job (file backend, tmp dir)
  - tracker.build_record + JsonlTracker round-trip
  - sweep.title_ok / location_ok / sponsorship_ok / salary_ok /
    filter_postings / load_applied_urls

Run: python3 -m unittest test_operation -v
"""
import json
import os
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from job_queue import claim, next_job, release, mark, stats, norm_url, requeue
from tracker import build_record, JsonlTracker
from sweep import (title_ok, location_ok, sponsorship_ok, salary_ok,
                   filter_postings, load_applied_urls)

HERE = os.path.dirname(os.path.abspath(__file__))


def job(url, **kw):
    d = {"company": "Acme", "title": "Senior Backend Engineer",
         "location": "Austin, TX", "salary": "salary undisclosed",
         "url": url, "ats": "greenhouse", "notes": "",
         "queued_at": "2026-01-15T00:00:00Z", "source": "test",
         "status": "pending", "skip_reason": ""}
    d.update(kw)
    return d


class QueueTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.q = os.path.join(self.tmp.name, "queue.json")
        self.c = os.path.join(self.tmp.name, "claims.jsonl")
        jobs = [job("https://example.com/jobs/1"),
                job("https://example.com/jobs/2")]
        with open(self.q, "w") as f:
            json.dump(jobs, f)

    def tearDown(self):
        self.tmp.cleanup()

    def test_next_skips_claimed(self):
        j1 = next_job(self.q, self.c, "lane-a")
        self.assertEqual(j1["url"], "https://example.com/jobs/1")
        claimed = claim(self.q, self.c, j1["url"], "lane-a")
        self.assertIsNotNone(claimed)
        j2 = next_job(self.q, self.c, "lane-b")
        self.assertEqual(j2["url"], "https://example.com/jobs/2")
        # double claim returns None
        self.assertIsNone(claim(self.q, self.c, j1["url"], "lane-b"))

    def test_mark_terminal(self):
        claim(self.q, self.c, "https://example.com/jobs/1", "lane-a")
        self.assertTrue(mark(self.q, self.c, "https://example.com/jobs/1",
                             "applied", lane="lane-a"))
        with open(self.q) as f:
            jobs = json.load(f)
        self.assertEqual(jobs[0]["status"], "applied")
        nxt = next_job(self.q, self.c, "lane-b")
        self.assertEqual(nxt["url"], "https://example.com/jobs/2")

    def test_release_requeues(self):
        claim(self.q, self.c, "https://example.com/jobs/1", "lane-a")
        release(self.c, "https://example.com/jobs/1", "lane-a")
        nxt = next_job(self.q, self.c, "lane-b")
        self.assertEqual(nxt["url"], "https://example.com/jobs/1")

    def test_stats(self):
        s = stats(self.q, self.c)
        self.assertEqual(s["total"], 2)
        self.assertEqual(s["pending"], 2)
        self.assertEqual(s["live_claims"], 0)

    def test_norm_url(self):
        self.assertEqual(norm_url("HTTPS://Example.com/Jobs/1/?x=2#y"),
                         "example.com/Jobs/1?x=2")
        # tracking params are stripped, job-identifying params kept
        self.assertEqual(
            norm_url("https://example.com/careers?utm_source=x&job_id=42"),
            "example.com/careers?job_id=42")
        self.assertEqual(norm_url(""), "")


class TrackerTest(unittest.TestCase):
    def test_build_record_defaults(self):
        r = build_record(company="Acme", title="Senior SWE",
                         url="https://example.com/jobs/1")
        self.assertEqual(r["company"], "Acme")
        self.assertEqual(r["status"], "Applied")
        self.assertTrue(r["date"])
        with self.assertRaises(ValueError):
            build_record(company="Acme")  # url required
        with self.assertRaises(ValueError):
            build_record(company="Acme", url="https://example.com/1",
                         status="Bogus")

    def test_jsonl_roundtrip(self):
        tmp = tempfile.TemporaryDirectory()
        path = os.path.join(tmp.name, "apps.jsonl")
        t = JsonlTracker(path)
        rec = build_record(company="Acme", title="Senior SWE",
                           url="https://example.com/jobs/1")
        res = t.append(rec)
        self.assertEqual(res["backend"], "jsonl")
        with open(path) as f:
            back = json.loads(f.readline())
        self.assertEqual(back["company"], "Acme")
        tmp.cleanup()

    def test_sheets_missing_secret(self):
        from tracker import SheetsTracker
        env = dict(os.environ)
        os.environ.pop("TRACKER_SHEET_ID", None)
        try:
            with self.assertRaises(ValueError):
                SheetsTracker()
        finally:
            os.environ.clear()
            os.environ.update(env)

    def test_notion_missing_secret(self):
        from tracker import NotionTracker
        env = dict(os.environ)
        os.environ.pop("TRACKER_NOTION_TOKEN", None)
        try:
            with self.assertRaises(ValueError):
                NotionTracker()
        finally:
            os.environ.clear()
            os.environ.update(env)


class SweepTest(unittest.TestCase):
    def test_title_ok(self):
        self.assertTrue(title_ok("Senior Backend Engineer"))
        self.assertTrue(title_ok("Staff Platform Engineer"))
        self.assertTrue(title_ok("Principal AI Engineer"))
        self.assertFalse(title_ok("Senior Frontend Engineer"))
        self.assertFalse(title_ok("Junior Backend Engineer"))
        self.assertFalse(title_ok("Engineering Manager"))
        self.assertFalse(title_ok("Senior Backend Engineer Intern"))

    def test_location_ok(self):
        ok, loc = location_ok("Austin, TX")
        self.assertTrue(ok)
        ok, loc = location_ok("Remote")
        self.assertTrue(ok)
        self.assertEqual(loc, "Remote")
        ok, loc = location_ok("Remote, US")
        self.assertTrue(ok)
        self.assertEqual(loc, "Remote (US)")
        ok, _ = location_ok("London, UK")
        self.assertFalse(ok)
        ok, _ = location_ok("Remote - South Africa")
        self.assertFalse(ok)

    def test_sponsorship_ok(self):
        self.assertTrue(sponsorship_ok("We welcome all applicants."))
        self.assertFalse(sponsorship_ok("No sponsorship available."))
        self.assertFalse(
            sponsorship_ok("We cannot provide visa sponsorship."))
        self.assertFalse(
            sponsorship_ok("Sponsorship is not available for this role."))

    def test_salary_ok(self):
        self.assertTrue(salary_ok(None, None))
        self.assertTrue(salary_ok(150000, 200000))
        self.assertFalse(salary_ok(80000, 90000, floor=100000))
        self.assertFalse(salary_ok("$80,000", "$90,000", floor=100000))
        self.assertFalse(salary_ok("80k", "90k", floor=100000))
        self.assertTrue(salary_ok("$180,000", "$220,000", floor=100000))

    def test_filter_postings(self):
        raw = [
            {"company": "Acme", "title": "Senior Backend Engineer",
             "location": "Austin, TX", "url": "https://a.example.com/1",
             "description": "Great role.", "salary_min": 180000,
             "salary_max": 220000},
            {"company": "Beta", "title": "Senior Frontend Engineer",
             "location": "Austin, TX", "url": "https://b.example.com/2",
             "description": "Great role."},
            {"company": "Gamma", "title": "Staff Platform Engineer",
             "location": "London, UK", "url": "https://c.example.com/3",
             "description": "Great role."},
            {"company": "Delta", "title": "Lead SRE",
             "location": "Remote", "url": "https://d.example.com/4",
             "description": "No sponsorship available."},
        ]
        cands, stats = filter_postings(raw)
        self.assertEqual(len(cands), 1)
        self.assertEqual(cands[0]["company"], "Acme")
        self.assertEqual(cands[0]["ats"], "direct")
        self.assertEqual(stats["title_fail"], 1)
        self.assertEqual(stats["loc_fail"], 1)
        self.assertEqual(stats["sponsor_fail"], 1)

    def test_dedup(self):
        raw = [{"company": "Acme", "title": "Senior Backend Engineer",
                "location": "Austin, TX",
                "url": "https://a.example.com/1?utm_source=x",
                "description": "Great role."}]
        applied = {"a.example.com/1"}
        cands, stats = filter_postings(raw, applied)
        self.assertEqual(len(cands), 0)
        self.assertEqual(stats["dup"], 1)

    def test_in_batch_dedup(self):
        posting = {"company": "Acme", "title": "Senior Backend Engineer",
                   "location": "Austin, TX",
                   "url": "https://a.example.com/1",
                   "description": "Great role."}
        cands, stats = filter_postings([posting, dict(posting)])
        self.assertEqual(len(cands), 1)
        self.assertEqual(stats["dup"], 1)

    def test_ats_of(self):
        from sweep import ats_of
        self.assertEqual(
            ats_of("https://job-boards.greenhouse.io/acme/jobs/123"),
            "greenhouse")
        self.assertEqual(
            ats_of("https://boards.ashbyhq.com/acme/123"), "ashby")
        self.assertEqual(ats_of("https://evil.com/?x=greenhouse.io"),
                         "direct")

    def test_load_applied_urls_jsonl(self):
        tmp = tempfile.TemporaryDirectory()
        path = os.path.join(tmp.name, "apps.jsonl")
        with open(path, "w") as f:
            f.write(json.dumps({"url": "https://a.example.com/1"}) + "\n")
            f.write(json.dumps({"job_url": "https://b.example.com/2/"}) + "\n")
        urls = load_applied_urls(path)
        self.assertIn("a.example.com/1", urls)
        self.assertIn("b.example.com/2", urls)
        tmp.cleanup()


class LauncherTest(unittest.TestCase):
    def test_validate_count(self):
        from launch_browsers import validate_count
        self.assertEqual(validate_count("2"), 2)
        self.assertEqual(validate_count(" 3 "), 3)
        with self.assertRaises(ValueError):
            validate_count("0")
        with self.assertRaises(ValueError):
            validate_count("-1")
        with self.assertRaises(ValueError):
            validate_count("abc")
        with self.assertRaises(ValueError):
            validate_count("")

    def test_warn_if_many(self):
        from launch_browsers import warn_if_many
        self.assertEqual(warn_if_many(2), "")
        self.assertEqual(warn_if_many(8), "")
        self.assertIn("warning", warn_if_many(9).lower())

    def test_allocate_lanes(self):
        from unittest import mock
        from launch_browsers import allocate_lanes
        with mock.patch("launch_browsers.port_free", return_value=True):
            lanes = allocate_lanes(3, base_port=59991)
        self.assertEqual([l["port"] for l in lanes], [59991, 59992, 59993])
        self.assertEqual(lanes[0]["purpose"], "greenhouse")
        self.assertEqual(lanes[1]["purpose"], "other-boards")
        self.assertEqual(lanes[2]["purpose"], "lane-3")
        for l in lanes:
            self.assertTrue(l["profile_dir"].endswith(
                ".chrome-jobpilot-%d" % l["port"]))
        # explicit purposes win
        from unittest import mock
        with mock.patch("launch_browsers.port_free", return_value=True):
            lanes = allocate_lanes(2, base_port=59981,
                                   purposes=["research", "code-gate"])
        self.assertEqual(lanes[0]["purpose"], "research")
        # port range validation
        with mock.patch("launch_browsers.port_free", return_value=True):
            with self.assertRaises(ValueError):
                allocate_lanes(2, base_port=65535)

    def test_allocate_lanes_busy_port(self):
        import socket
        from launch_browsers import allocate_lanes
        s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        s.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        s.bind(("127.0.0.1", 59971))
        s.listen(1)
        try:
            with self.assertRaises(ValueError):
                allocate_lanes(1, base_port=59971)
        finally:
            s.close()

    def test_build_launch(self):
        from launch_browsers import build_launch
        lane = {"lane": 1, "port": 59991,
                "profile_dir": "/tmp/prof-59991", "purpose": "greenhouse"}
        mac = build_launch("Darwin", lane)
        self.assertEqual(mac[:4], ["open", "-n", "-a", "Google Chrome"])
        self.assertIn("--remote-debugging-port=59991", mac)
        self.assertTrue(all("--headless" not in a for a in mac))
        lin = build_launch("Linux", lane)
        self.assertFalse(any("headless" in a for a in lin))
        win = build_launch("Windows", lane)
        self.assertIn("cmd_path", win)
        self.assertIn("/it", win["schtasks_create"])
        # profile path with a space must be quoted in the .cmd body
        lane2 = dict(lane, profile_dir="C:\\Users\\Some User\\prof")
        win2 = build_launch("Windows", lane2)
        self.assertIn('"--user-data-dir=C:\\Users\\Some User\\prof"',
                        win2["cmd_body"])

    def test_summary_table(self):
        from unittest import mock
        from launch_browsers import summary_table, allocate_lanes
        with mock.patch("launch_browsers.port_free", return_value=True):
            lanes = allocate_lanes(2, base_port=59961)
        t = summary_table(lanes)
        self.assertIn("greenhouse", t)
        self.assertIn("59961", t)

    def test_wait_for_instance_requires_chrome(self):
        from unittest import mock
        from launch_browsers import wait_for_instance
        good = {"Browser": "Chrome/154", "webSocketDebuggerUrl": "ws://x"}
        with mock.patch("urllib.request.urlopen") as uo:
            uo.return_value.__enter__.return_value.read.return_value = ""
            with mock.patch("json.load", return_value={}):
                self.assertIsNone(wait_for_instance(59999, timeout=1))
        with mock.patch("urllib.request.urlopen") as uo:
            with mock.patch("json.load", return_value=good):
                self.assertEqual(
                    wait_for_instance(59999, timeout=1), "Chrome/154")


if __name__ == "__main__":
    unittest.main()


class ConfigTest(unittest.TestCase):
    def test_load_missing(self):
        from config import load_config
        self.assertEqual(load_config("/nonexistent/config.json"), {})

    def test_precedence(self):
        import json
        import tempfile
        from config import load_config, get
        tmp = tempfile.NamedTemporaryFile("w", suffix=".json", delete=False)
        json.dump({"JOBPILOT_LANE": "from-file",
                   "_comment": "ignored"}, tmp)
        tmp.close()
        try:
            cfg = load_config(tmp.name)
            self.assertEqual(cfg["JOBPILOT_LANE"], "from-file")
            self.assertNotIn("_comment", cfg)
            os.environ["JOBPILOT_LANE"] = "from-env"
            try:
                self.assertEqual(get("JOBPILOT_LANE", cfg=cfg), "from-env")
            finally:
                del os.environ["JOBPILOT_LANE"]
            self.assertEqual(get("JOBPILOT_LANE", cfg=cfg), "from-file")
            self.assertEqual(get("MISSING_KEY", "dflt", cfg=cfg), "dflt")
        finally:
            os.unlink(tmp.name)

    def test_malformed(self):
        import tempfile
        from config import load_config
        tmp = tempfile.NamedTemporaryFile("w", suffix=".json", delete=False)
        tmp.write("{not json")
        tmp.close()
        try:
            with self.assertRaises(ValueError):
                load_config(tmp.name)
        finally:
            os.unlink(tmp.name)


class JobQueueExtraTest(unittest.TestCase):
    def setUp(self):
        import tempfile
        self.tmp = tempfile.TemporaryDirectory()
        self.q = os.path.join(self.tmp.name, "queue.json")
        self.c = os.path.join(self.tmp.name, "claims.jsonl")
        jobs = [job("https://example.com/jobs/1", ats="greenhouse"),
                job("https://example.com/jobs/2", ats="ashby")]
        with open(self.q, "w") as f:
            json.dump(jobs, f)

    def tearDown(self):
        self.tmp.cleanup()

    def test_ats_filter(self):
        # Greenhouse lane only sees greenhouse jobs
        j = next_job(self.q, self.c, "lane-a", ats="greenhouse")
        self.assertEqual(j["ats"], "greenhouse")
        # Ashby job is not claimable with the greenhouse filter
        self.assertIsNone(claim(self.q, self.c,
                                "https://example.com/jobs/2",
                                "lane-a", ats="greenhouse"))

    def test_requeue(self):
        claim(self.q, self.c, "https://example.com/jobs/1", "lane-a")
        self.assertTrue(mark(self.q, self.c, "https://example.com/jobs/1",
                             "blocked", "code-gate", lane="lane-a"))
        # job 1 is terminal now; only job 2 is pending
        j = next_job(self.q, self.c, "lane-b")
        self.assertEqual(j["url"], "https://example.com/jobs/2")
        self.assertTrue(requeue(self.q, self.c,
                                "https://example.com/jobs/1",
                                "failed code attempt"))
        j = next_job(self.q, self.c, "lane-c")
        self.assertEqual(j["url"], "https://example.com/jobs/1")

    def test_release_wrong_owner(self):
        claim(self.q, self.c, "https://example.com/jobs/1", "lane-a")
        # lane-b cannot release lane-a's claim
        self.assertFalse(release(self.c, "https://example.com/jobs/1",
                                 "lane-b"))
        # lane-a can
        self.assertTrue(release(self.c, "https://example.com/jobs/1",
                                "lane-a"))

    def test_malformed_claim_lines_counted(self):
        from job_queue import read_claims
        with open(self.c, "w") as f:
            f.write('{"url": "https://example.com/jobs/1"}\n')
            f.write("not json\n")
            f.write('{"nourl": true}\n')
        claims, bad = read_claims(self.c)
        self.assertEqual(len(claims), 1)
        self.assertEqual(bad, 2)

    def test_stale_claim_expires(self):
        import datetime
        from job_queue import live_claims
        old = (datetime.datetime.now(datetime.timezone.utc)
               - datetime.timedelta(hours=7)).isoformat()
        with open(self.c, "w") as f:
            f.write(json.dumps({"url": "https://example.com/jobs/1",
                                "lane": "lane-a", "claimed_at": old,
                                "status": "in-progress"}) + "\n")
        self.assertEqual(live_claims(self.c), {})
        # ...so the job is claimable again
        j = next_job(self.q, self.c, "lane-b")
        self.assertEqual(j["url"], "https://example.com/jobs/1")


class SetupAuditTest(unittest.TestCase):
    def test_yes_never_prints_environment_credentials(self):
        import contextlib
        import io
        from unittest import mock
        import setup_wizard
        with tempfile.TemporaryDirectory() as directory:
            with open(os.path.join(directory, 'facts.json'), 'w') as fh:
                json.dump({'first_name': 'Applicant'}, fh)
            output = io.StringIO()
            token = 'sk-raw-sensitive-token-9f2x'
            with mock.patch.dict(os.environ, {'TRACKER_BACKENDS': 'notion',
                    'TRACKER_NOTION_TOKEN': token,
                    'TRACKER_NOTION_DB': '0123456789abcdef0123456789abcdef'}, clear=True), \
                    mock.patch('setup_wizard.allocate_lanes', return_value=[]), \
                    contextlib.redirect_stdout(output):
                result = setup_wizard.main(['--yes', '--directory', directory])
            self.assertEqual(result, 0)
            self.assertNotIn(token, output.getvalue())
            self.assertNotIn('01234567-89ab-cdef-0123-456789abcdef', output.getvalue())
            self.assertIn('export JOBPILOT_CONFIG=', output.getvalue())
            self.assertIn(os.path.join(directory, 'config.json'), output.getvalue())

    def test_default_setup_directory_is_cwd(self):
        import contextlib
        import io
        from unittest import mock
        import setup_wizard
        with tempfile.TemporaryDirectory() as directory:
            with open(os.path.join(directory, 'facts.json'), 'w') as fh:
                json.dump({'first_name': 'Applicant'}, fh)
            with mock.patch.dict(os.environ, {}, clear=True), \
                    mock.patch('setup_wizard.os.getcwd', return_value=directory), \
                    mock.patch('setup_wizard.allocate_lanes', return_value=[]), \
                    contextlib.redirect_stdout(io.StringIO()):
                cfg = setup_wizard.plan_setup({})
                result = setup_wizard.main(['--yes'])
            self.assertEqual(cfg['JOBPILOT_QUEUE'], os.path.join(directory, 'queue.json'))
            self.assertEqual(result, 0)
            self.assertTrue(os.path.isfile(os.path.join(directory, 'config.json')))


class SweepAuditTest(unittest.TestCase):
    def run_sweep(self, directory, posting, extra=()):
        import contextlib
        import io
        from unittest import mock
        import sweep
        raw = os.path.join(directory, 'raw.json')
        with open(raw, 'w') as fh:
            json.dump([posting], fh)
        args = ['sweep.py', '--in', raw, '--queue', os.path.join(directory, 'queue.json')]
        with mock.patch.object(sys, 'argv', args + list(extra)), \
                contextlib.redirect_stdout(io.StringIO()):
            sweep.main()

    def test_reposted_role_with_new_url_in_later_sweep_is_withheld(self):
        with tempfile.TemporaryDirectory() as directory:
            self.run_sweep(directory, job('https://example.com/old'))
            # Historical fingerprints must survive a queue rotation.
            os.unlink(os.path.join(directory, 'queue.json'))
            self.run_sweep(directory, job('https://example.com/new'))
            with open(os.path.join(directory, 'queue.json')) as fh:
                self.assertEqual(json.load(fh), [])

    def test_new_url_matches_applied_history(self):
        with tempfile.TemporaryDirectory() as directory:
            applied = os.path.join(directory, 'applications.jsonl')
            with open(applied, 'w') as fh:
                fh.write(json.dumps(job('https://example.com/old', status='Applied')) + '\n')
            self.run_sweep(directory, job('https://example.com/new'), ['--applied', applied])
            with open(os.path.join(directory, 'queue.json')) as fh:
                self.assertEqual(json.load(fh), [])

    def test_queue_load_and_save_are_inside_store_transaction(self):
        from unittest import mock
        import job_queue
        with tempfile.TemporaryDirectory() as directory:
            active = []
            real_enter, real_exit = job_queue.Store.__enter__, job_queue.Store.__exit__
            real_load, real_save = job_queue.load_queue, job_queue.save_queue
            def enter(store):
                result = real_enter(store)
                active.append(store.path)
                return result
            def leave(store, *exc):
                active.remove(store.path)
                return real_exit(store, *exc)
            def load(path):
                self.assertIn(os.path.abspath(path), active)
                return real_load(path)
            def save(path, jobs):
                self.assertIn(os.path.abspath(path), active)
                return real_save(path, jobs)
            with mock.patch.object(job_queue.Store, '__enter__', enter), \
                    mock.patch.object(job_queue.Store, '__exit__', leave), \
                    mock.patch('job_queue.load_queue', side_effect=load), \
                    mock.patch('job_queue.save_queue', side_effect=save):
                self.run_sweep(directory, job('https://example.com/new'))

    def test_fingerprint_file_has_only_role_identity(self):
        with tempfile.TemporaryDirectory() as directory:
            posting = job('https://example.com/old', notes='private applicant note',
                          email='private@example.com')
            self.run_sweep(directory, posting)
            path = os.path.join(directory, 'queue-fingerprints.jsonl')
            with open(path) as fh:
                records = [json.loads(line) for line in fh]
            self.assertEqual(len(records), 1)
            self.assertEqual(set(records[0]), {'company', 'title', 'location', 'url'})
            self.assertEqual(records[0]['title'], 'backend engineer senior')

    def test_dry_run_does_not_write_queue_or_fingerprints(self):
        with tempfile.TemporaryDirectory() as directory:
            self.run_sweep(directory, job('https://example.com/new'), ['--dry-run'])
            self.assertEqual(os.listdir(directory), ['raw.json'])

class AuditLegacyFingerprintTest(unittest.TestCase):
    def test_legacy_job_url_history_withholds_repost(self):
        from sweep import filter_postings
        role = {"company": "Example", "title": "Senior Software Engineer", "location": "Austin, TX", "url": "https://example.com/new"}
        old = dict(role, job_url="https://example.com/old")
        del old["url"]
        candidates, stats = filter_postings([role], history=[old])
        self.assertEqual(candidates, [])
        self.assertEqual(stats["fuzzy_dup_review"], [(old["job_url"], role["url"])])

class QueueMinorAuditTest(unittest.TestCase):
    def test_unique_default_lane_and_environment_override(self):
        from unittest import mock
        import job_queue
        self.assertTrue(callable(getattr(job_queue, 'default_lane', None)))
        with mock.patch.dict(os.environ, {}, clear=True), mock.patch('socket.gethostname', return_value='host-a'), mock.patch('os.getpid', return_value=42):
            self.assertEqual(job_queue.default_lane(), 'lane-1@host-a#42')
        with mock.patch.dict(os.environ, {'JOBPILOT_LANE': 'operator-lane'}):
            self.assertEqual(job_queue.default_lane(), 'operator-lane')

    def test_invalid_and_future_claims_block_then_expire_without_extending(self):
        import datetime
        from unittest import mock
        import job_queue
        start = datetime.datetime(2026, 1, 1, tzinfo=datetime.timezone.utc)
        for stamp in ['garbage', '2999-01-01T00:00:00Z']:
            with self.subTest(stamp=stamp), tempfile.TemporaryDirectory() as d:
                path = os.path.join(d, 'claims.jsonl')
                with open(path, 'w') as f:
                    f.write(json.dumps({'url': 'https://example.com/1', 'lane': 'owner', 'claimed_at': stamp, 'status': 'in-progress'}) + '\n')
                with mock.patch('job_queue.datetime.datetime', wraps=datetime.datetime) as clock:
                    clock.now.return_value = start
                    self.assertIn('example.com/1', job_queue.live_claims(path, 1))
                    clock.now.return_value = start + datetime.timedelta(hours=2)
                    self.assertEqual(job_queue.live_claims(path, 1), {})
                claims, _ = job_queue.read_claims(path)
                self.assertEqual(len(claims), 2)
                self.assertEqual(claims[-1]['lane'], 'owner')

    def test_nonpositive_and_nonfinite_ttl_fail_safe(self):
        import job_queue
        with tempfile.TemporaryDirectory() as d:
            path = os.path.join(d, 'claims.jsonl')
            job_queue.append_claim(path, {'url': 'https://example.com/1', 'lane': 'owner', 'claimed_at': job_queue.utcnow(), 'status': 'in-progress'})
            for ttl in [0, -1, float('nan'), float('inf'), 'invalid']:
                with self.subTest(ttl=ttl):
                    self.assertIn('example.com/1', job_queue.live_claims(path, ttl))

    def test_invalid_url_port_does_not_raise(self):
        self.assertEqual(norm_url('https://h:abc/x'), 'https://h:abc/x')

    def run_cli(self, args):
        import contextlib
        import io
        from unittest import mock
        import job_queue
        output = io.StringIO()
        with mock.patch.object(sys, 'argv', ['job_queue.py'] + args), contextlib.redirect_stdout(output):
            try:
                job_queue.main()
            except SystemExit as e:
                self.assertEqual(e.code, 1)
        return output.getvalue().strip()

    def test_heartbeat_cli_no_live_claim(self):
        with tempfile.TemporaryDirectory() as d:
            self.assertEqual(self.run_cli(['--claims', os.path.join(d, 'claims.jsonl'), 'heartbeat', 'https://example.com/1', '--lane', 'a']), 'no-live-claim')

    def test_unique_cli_owner_and_explicit_operator_override(self):
        with tempfile.TemporaryDirectory() as d:
            q, c = os.path.join(d, 'queue.json'), os.path.join(d, 'claims.jsonl')
            url = 'https://example.com/1'
            with open(q, 'w') as f:
                json.dump([job(url)], f)
            claim(q, c, url, 'owner')
            self.assertEqual(self.run_cli(['--queue', q, '--claims', c, 'mark', url, 'blocked', '--lane', 'other']), 'not-owner')
            self.assertEqual(self.run_cli(['--queue', q, '--claims', c, 'release', url]), 'not-owner')
            self.assertEqual(self.run_cli(['--queue', q, '--claims', c, 'release', url, '--lane', '']), 'released')
            claim(q, c, url, 'owner')
            self.assertEqual(self.run_cli(['--queue', q, '--claims', c, 'mark', url, 'applied']), 'not-owner')
            self.assertEqual(self.run_cli(['--queue', q, '--claims', c, 'mark', url, 'applied', '--lane', '']), 'marked')

class SweepMinorAuditTest(unittest.TestCase):
    def test_seniority_differences_survive_long_titles(self):
        import sweep
        a = job('https://example.com/a', title='Senior Backend Software Platform Infrastructure Cloud Engineer')
        for level in ['Staff', 'Lead', 'Principal']:
            self.assertFalse(sweep.near_duplicate(a, dict(a, title=a['title'].replace('Senior', level))))

    def test_unicode_and_programming_languages_preserved(self):
        import sweep
        self.assertEqual(sweep.normalize_company('Caf\u00e9 Inc.'), sweep.normalize_company('CAFE\u0301 LLC'))
        self.assertEqual(sweep.normalize_company('Stra\u00dfe'), sweep.normalize_company('STRASSE'))
        self.assertTrue(sweep.normalize_company('\u6771\u4eac'))
        a = job('https://example.com/a', title='Senior Backend C++ Software Engineer')
        self.assertFalse(sweep.near_duplicate(a, dict(a, title='Senior Backend C# Software Engineer')))
        self.assertIn('c++', sweep.title_tokens(a['title']))
        self.assertIn('c#', sweep.title_tokens('Senior C# Engineer'))

    def test_us_state_names_and_abbreviations_match(self):
        import sweep
        for left, right in [('Austin, TX', 'Austin, Texas'), ('New York, NY', 'New York, New York'), ('Washington, DC', 'Washington, District of Columbia'), ('Portland, OR', 'Portland, Oregon')]:
            self.assertEqual(sweep.normalize_location(left), sweep.normalize_location(right))
        self.assertNotEqual(sweep.normalize_location('Austin, TX'), sweep.normalize_location('Austin, MN'))

    def test_filter_normalizes_each_role_once_and_buckets(self):
        from unittest import mock
        import sweep
        roles = [job('https://example.com/%s' % n, company='Company%s' % n) for n in range(20)]
        with mock.patch.object(sweep, 'normalize_company', wraps=sweep.normalize_company) as normalize:
            candidates, _ = sweep.filter_postings(roles)
        self.assertEqual(len(candidates), 20)
        self.assertLessEqual(normalize.call_count, 20)

    def test_duplicate_review_saved_in_workdir(self):
        import contextlib
        import io
        from unittest import mock
        import sweep
        with tempfile.TemporaryDirectory() as d:
            raw, q, workdir = os.path.join(d, 'raw.json'), os.path.join(d, 'queue.json'), os.path.join(d, 'workdir')
            with open(raw, 'w') as f:
                json.dump([job('https://example.com/a'), job('https://example.com/b')], f)
            with mock.patch.object(sys, 'argv', ['sweep.py', '--in', raw, '--queue', q, '--workdir', workdir]), contextlib.redirect_stdout(io.StringIO()):
                sweep.main()
            paths = [os.path.join(workdir, p) for p in os.listdir(workdir) if p.endswith('.json')]
            self.assertEqual(len(paths), 1)
            with open(paths[0]) as f:
                self.assertEqual(json.load(f)['fuzzy_dup_review'], [['https://example.com/a', 'https://example.com/b']])

class SweepLanguageIdentityTest(unittest.TestCase):
    def test_language_distinction_survives_long_title(self):
        import sweep
        title = 'Senior Backend Platform Infrastructure Cloud Systems Software Engineer Distributed Services C++'
        a = job('https://example.com/a', title=title)
        self.assertFalse(sweep.near_duplicate(a, dict(a, title=title.replace('C++', 'C#'))))

class SweepDryReviewTest(unittest.TestCase):
    def test_dry_run_saves_review_without_queue_or_fingerprints(self):
        import contextlib
        import io
        from unittest import mock
        import sweep
        with tempfile.TemporaryDirectory() as d:
            raw, q, workdir = os.path.join(d, 'raw.json'), os.path.join(d, 'queue.json'), os.path.join(d, 'workdir')
            with open(raw, 'w') as f:
                json.dump([job('https://example.com/a'), job('https://example.com/b')], f)
            with mock.patch.object(sys, 'argv', ['sweep.py', '--in', raw, '--queue', q, '--workdir', workdir, '--dry-run']), contextlib.redirect_stdout(io.StringIO()) as out:
                sweep.main()
            self.assertFalse(os.path.exists(q))
            self.assertFalse(os.path.exists(os.path.splitext(q)[0] + '-fingerprints.jsonl'))
            review = json.loads(out.getvalue())['review_file']
            with open(review) as f:
                self.assertEqual(json.load(f)['fuzzy_dup_review'], [['https://example.com/a', 'https://example.com/b']])
