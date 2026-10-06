#!/usr/bin/env python3
"""File-backed job queue with cross-lane claims.

Multiple lanes (browsers on different machines/ports) pull from the same
queue file without double-applying: a lane claims a job before working it,
and marks it applied/skipped/blocked/dead when done. Claims are advisory
and recorded in a JSONL claims file with timestamps, so a stale claim from
a dead lane can be spotted and reclaimed.

Queue file: JSON list of job dicts. Each job:
  {
    "company": "...", "title": "...", "location": "...",
    "salary": "...", "url": "https://...", "ats": "greenhouse",
    "notes": "...", "queued_at": "2026-01-01T00:00:00Z",
    "source": "sweep 2026-01-01",
    "status": "pending",            # pending|applied|skipped|blocked|dead
    "skip_reason": ""               # set when status is skipped/blocked/dead
  }

Claims file: JSONL, one claim per line:
  {"url": "...", "lane": "machine-a:9445", "claimed_at": "...Z",
   "status": "in-progress"}

Usage:
  python3 job_queue.py --queue queue.json --claims claims.jsonl next --lane NAME
  python3 job_queue.py --queue queue.json --claims claims.jsonl claim URL --lane NAME
  python3 job_queue.py --queue queue.json --claims claims.jsonl mark URL applied
  python3 job_queue.py heartbeat URL --lane NAME
  python3 job_queue.py --queue queue.json --claims claims.jsonl requeue URL
  python3 job_queue.py --queue queue.json stats

  claim prints the job JSON and exits 0, or prints null and exits 1 when
  the job is unavailable.

Concurrency model: every mutation runs inside a transaction guarded by a
stable sidecar lock file (<path>.lock), so read-modify-write is atomic
even though the queue file itself is replaced on save. This coordinates
lanes on ONE POSIX host. It does NOT coordinate across machines over a
network share (flock is unreliable there): point all lanes at files on a
single host, or run one queue writer per machine. On Windows the lock is
a best-effort no-op; same single-host rule applies.

Stale claims: a claim counts as live only while in-progress and younger
than CLAIM_MAX_AGE_HOURS (env, default 6). A lane that died mid-job stops
blocking the queue after that window. Heartbeat every N minutes while
working a job (for example, every 5 minutes). The latest entry per URL is
authoritative, so a fresh in-progress heartbeat extends the live claim.
Claims with no heartbeat within CLAIM_MAX_AGE_HOURS become claimable.
"""
import argparse
import datetime
import json
import os
import sys
import tempfile
import urllib.parse

try:
    import fcntl

    def _lock(fh):
        fcntl.flock(fh, fcntl.LOCK_EX)

    def _unlock(fh):
        fcntl.flock(fh, fcntl.LOCK_UN)
except ImportError:  # Windows: no flock; best effort only
    def _lock(fh):
        pass

    def _unlock(fh):
        pass

HERE = os.path.dirname(os.path.abspath(__file__))

TERMINAL = {"applied", "skipped", "blocked", "dead"}
CLAIM_MAX_AGE_HOURS = float(os.environ.get("CLAIM_MAX_AGE_HOURS", "6"))

# Query params that never identify a job; stripped during normalization.
TRACKING_PARAMS = {"utm_source", "utm_medium", "utm_campaign", "utm_term",
                   "utm_content", "fbclid", "gclid", "mc_cid", "mc_eid",
                   "_ga", "ref"}


def utcnow():
    return datetime.datetime.now(datetime.timezone.utc).isoformat()


def norm_url(u):
    """Normalize a job URL for identity comparison.

    Lowercases the host, keeps path case, drops the fragment, and strips
    known tracking query params while preserving job-identifying ones
    (e.g. ?job_id=). Two postings are the same job only if this matches.
    """
    u = (u or "").strip()
    if not u:
        return ""
    if "://" not in u:
        u = "https://" + u
    try:
        p = urllib.parse.urlsplit(u)
    except ValueError:
        return u.lower()
    host = p.hostname.lower() if p.hostname else ""
    if p.port and p.port not in (80, 443):
        host = "%s:%d" % (host, p.port)
    q = urllib.parse.parse_qsl(p.query, keep_blank_values=True)
    q = sorted((k, v) for k, v in q if k.lower() not in TRACKING_PARAMS)
    query = urllib.parse.urlencode(q)
    path = p.path.rstrip("/") or "/"
    out = host + path
    if query:
        out += "?" + query
    return out


class Store:
    """A locked file store. Use as a context manager around mutations."""

    def __init__(self, path):
        self.path = os.path.abspath(path)
        self.lock_path = self.path + ".lock"
        self._fh = None

    def __enter__(self):
        d = os.path.dirname(self.path)
        os.makedirs(d, exist_ok=True)
        # O_CREAT without truncation: never erase another writer's file.
        fd = os.open(self.lock_path, os.O_CREAT | os.O_RDWR, 0o644)
        self._fh = os.fdopen(fd, "w")
        _lock(self._fh)
        return self

    def __exit__(self, *exc):
        try:
            _unlock(self._fh)
        finally:
            self._fh.close()
            self._fh = None


def load_queue(queue_path):
    if not os.path.exists(queue_path):
        return []
    with open(queue_path) as f:
        data = json.load(f)
    if isinstance(data, dict):
        data = data.get("jobs", data.get("queue", []))
    return [j for j in data if isinstance(j, dict) and j.get("url")]


def save_queue(queue_path, jobs):
    fd, tmp = tempfile.mkstemp(prefix=".queue-",
                               dir=os.path.dirname(
                                   os.path.abspath(queue_path)))
    with os.fdopen(fd, "w") as f:
        json.dump(jobs, f, indent=1)
        f.flush()
        os.fsync(f.fileno())
    os.replace(tmp, queue_path)


def read_claims(claims_path):
    """Read the claims log. Returns (claims, malformed_count)."""
    claims, bad = [], 0
    if os.path.exists(claims_path):
        with open(claims_path) as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    c = json.loads(line)
                except ValueError:
                    bad += 1
                    continue
                if isinstance(c, dict) and c.get("url"):
                    claims.append(c)
                else:
                    bad += 1
    return claims, bad


def append_claim(claims_path, claim):
    d = os.path.dirname(os.path.abspath(claims_path))
    os.makedirs(d, exist_ok=True)
    # O_APPEND + O_CREAT: never truncate; each record is one write().
    fd = os.open(claims_path, os.O_CREAT | os.O_WRONLY | os.O_APPEND, 0o644)
    with os.fdopen(fd, "a") as f:
        f.write(json.dumps(claim) + "\n")
        f.flush()
        os.fsync(f.fileno())


def _parse_ts(s):
    try:
        dt = datetime.datetime.fromisoformat(
            str(s).replace("Z", "+00:00"))
    except ValueError:
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=datetime.timezone.utc)
    return dt


def live_claims(claims_path, max_age_hours=CLAIM_MAX_AGE_HOURS):
    """Live claims: latest entry per URL is in-progress and young.

    The log is append-only, so a later released/applied entry supersedes
    an earlier in-progress claim from a lane that crashed or gave up.
    """
    now = datetime.datetime.now(datetime.timezone.utc)
    latest = {}
    claims, _ = read_claims(claims_path)
    for c in claims:
        latest[norm_url(c.get("url", ""))] = c
    live = {}
    for url, c in latest.items():
        if not url or c.get("status") != "in-progress":
            continue
        ts = _parse_ts(c.get("claimed_at", ""))
        if ts is None:
            continue
        if (now - ts).total_seconds() < max_age_hours * 3600:
            live[url] = c
    return live


def next_job(queue_path, claims_path, lane, ats=None,
             max_age_hours=CLAIM_MAX_AGE_HOURS):
    """Next unclaimed pending job (optionally filtered by ATS)."""
    jobs = load_queue(queue_path)
    claimed = live_claims(claims_path, max_age_hours)
    for job in jobs:
        if job.get("status", "pending") != "pending":
            continue
        if ats and job.get("ats", "").lower() != ats.lower():
            continue
        if norm_url(job.get("url", "")) in claimed:
            continue
        return job
    return None


def claim(queue_path, claims_path, url, lane, ats=None):
    """Claim a job for a lane. Returns the job dict, or None."""
    with Store(claims_path):
        claimed = live_claims(claims_path)
        if norm_url(url) in claimed:
            return None
        jobs = load_queue(queue_path)
        job = next((j for j in jobs
                    if norm_url(j.get("url", "")) == norm_url(url)
                    and j.get("status", "pending") == "pending"
                    and (not ats or j.get("ats", "").lower() == ats.lower())),
                   None)
        if not job:
            return None
        append_claim(claims_path, {
            "url": job["url"], "company": job.get("company", ""),
            "title": job.get("title", ""), "lane": lane,
            "claimed_at": utcnow(), "status": "in-progress",
        })
        return job


def _check_owner(claims_path, url, lane):
    """True if lane holds the live claim (or no live claim exists)."""
    live = live_claims(claims_path)
    c = live.get(norm_url(url))
    return c is None or c.get("lane") == lane


def heartbeat(claims_path, url, lane):
    """Refresh a live claim held by this lane, never acquire a new claim."""
    with Store(claims_path):
        if not lane or not _check_owner(claims_path, url, lane):
            return False
        current = live_claims(claims_path).get(norm_url(url))
        # _check_owner allows an absent claim for release/mark; heartbeats
        # require a live owner so a stale lane cannot revive lost work.
        if current is None:
            return False
        fresh = dict(current, claimed_at=utcnow(), status="in-progress")
        append_claim(claims_path, fresh)
        return True


def release(claims_path, url, lane=""):
    """Release a claim; the job stays pending for another pass."""
    with Store(claims_path):
        if lane and not _check_owner(claims_path, url, lane):
            return False
        append_claim(claims_path, {
            "url": url, "lane": lane, "claimed_at": utcnow(),
            "status": "released",
        })
        return True


def mark(queue_path, claims_path, url, status, reason="", lane=""):
    """Set a job's terminal status and close its claim. Returns True."""
    if status not in TERMINAL:
        raise ValueError("status must be one of %s" % sorted(TERMINAL))
    with Store(queue_path):
        with Store(claims_path):
            if lane and not _check_owner(claims_path, url, lane):
                return False
            jobs = load_queue(queue_path)
            found = False
            for job in jobs:
                if norm_url(job.get("url", "")) == norm_url(url):
                    job["status"] = status
                    if reason:
                        job["skip_reason"] = reason
                    job["resolved_at"] = utcnow()
                    job["resolved_by"] = lane
                    found = True
            if not found:
                return False
            save_queue(queue_path, jobs)
            append_claim(claims_path, {
                "url": url, "lane": lane, "claimed_at": utcnow(),
                "status": status, "reason": reason,
            })
            return True


def requeue(queue_path, claims_path, url, reason="", lane=""):
    """Return a terminal job to pending (e.g. after a code-gate resume)."""
    with Store(queue_path):
        jobs = load_queue(queue_path)
        found = False
        for job in jobs:
            if norm_url(job.get("url", "")) == norm_url(url):
                if job.get("status", "pending") not in TERMINAL:
                    return False
                job["status"] = "pending"
                job["skip_reason"] = ""
                job["requeued_at"] = utcnow()
                if reason:
                    job["notes"] = (job.get("notes", "") + " [requeue: " +
                                    reason + "]").strip()
                found = True
        if not found:
            return False
        save_queue(queue_path, jobs)
        return True


def stats(queue_path, claims_path):
    jobs = load_queue(queue_path)
    claims, bad = read_claims(claims_path)
    out = {"total": len(jobs), "malformed_claim_lines": bad}
    for job in jobs:
        out[job.get("status", "pending")] = out.get(
            job.get("status", "pending"), 0) + 1
    out["live_claims"] = len(live_claims(claims_path))
    return out


def main():
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--queue", default="queue.json")
    ap.add_argument("--claims", default="claims.jsonl")
    sub = ap.add_subparsers(dest="cmd", required=True)

    p = sub.add_parser("next", help="print next unclaimed pending job")
    p.add_argument("--lane", default="lane-1")
    p.add_argument("--ats", default="",
                   help="only jobs for this ATS (e.g. greenhouse)")

    p = sub.add_parser("claim", help="claim a job URL for a lane")
    p.add_argument("url")
    p.add_argument("--lane", default="lane-1")
    p.add_argument("--ats", default="")

    p = sub.add_parser("heartbeat", help="refresh a live owned claim")
    p.add_argument("url")
    p.add_argument("--lane", required=True)

    p = sub.add_parser("release", help="release a claim")
    p.add_argument("url")
    p.add_argument("--lane", default="lane-1")

    p = sub.add_parser("mark", help="set terminal status for a job")
    p.add_argument("url")
    p.add_argument("status", choices=sorted(TERMINAL))
    p.add_argument("--reason", default="")
    p.add_argument("--lane", default="lane-1")

    p = sub.add_parser("requeue", help="return a terminal job to pending")
    p.add_argument("url")
    p.add_argument("--reason", default="")
    p.add_argument("--lane", default="lane-1")

    sub.add_parser("stats", help="queue/claims summary")

    a = ap.parse_args()
    if a.cmd == "next":
        job = next_job(a.queue, a.claims, a.lane, a.ats or None)
        print(json.dumps(job, indent=1) if job else "null")
    elif a.cmd == "claim":
        job = claim(a.queue, a.claims, a.url, a.lane, a.ats or None)
        if job:
            print(json.dumps(job, indent=1))
        else:
            print("null")
            sys.exit(1)
    elif a.cmd == "heartbeat":
        if not heartbeat(a.claims, a.url, a.lane):
            print("not-owner")
            sys.exit(1)
        print("heartbeat")
    elif a.cmd == "release":
        print("released" if release(a.claims, a.url, a.lane)
              else "not-owner")
    elif a.cmd == "mark":
        print("marked" if mark(a.queue, a.claims, a.url, a.status,
                              a.reason, a.lane) else "not-found")
    elif a.cmd == "requeue":
        print("requeued" if requeue(a.queue, a.claims, a.url,
                                   a.reason, a.lane) else "not-found")
    elif a.cmd == "stats":
        print(json.dumps(stats(a.queue, a.claims), indent=1))


if __name__ == "__main__":
    main()
