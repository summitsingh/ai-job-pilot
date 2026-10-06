# Queue and claims protocol

`job_queue.py` (console script `ai-job-pilot-queue`) is the coordination
layer that lets multiple lanes (browsers on different ports) share one
job queue. Run it from the repo directory: `python3 job_queue.py ...`.

## The files

- `queue.json`: JSON list of job dicts. Each job carries a `status`:
  `pending` (default), `applied`, `skipped`, `blocked`, or `dead`.
- `claims.jsonl`: append-only log of claims and resolutions. One JSON
  object per line: `{"url", "lane", "claimed_at", "status"}` where
  status is `in-progress`, `released`, or a terminal status. Terminal
  marks also carry the `reason`.

## The protocol

1. A lane asks for the next job:
   `python3 job_queue.py next --lane <name> [--ats greenhouse]`.
   It gets the first `pending` job with no live claim.
2. The lane claims it:
   `python3 job_queue.py claim <url> --lane <name>`.
   This appends an `in-progress` claim. A second lane claiming the
   same URL gets `null` on stdout and exit code 1.
3. The lane works the job, then marks it terminal:
   `python3 job_queue.py mark <url> applied|skipped|blocked|dead --reason "..."`.
   Marking appends the terminal status to the claims log too, and the
   reason is stored as `skip_reason` on the job in `queue.json`.
4. If the lane crashes or gives up without a verdict, it releases:
   `python3 job_queue.py release <url>`. The job stays `pending` for
   another pass. Only the lane holding the claim (or an operator with
   no `--lane`) can release or mark it.
5. To return a terminal job to the queue (for example after a
   code-gate resume):
   `python3 job_queue.py requeue <url> --reason "..."`.

## Stale claims

A claim counts as live only while `in-progress` and younger than
`CLAIM_MAX_AGE_HOURS` (env var, default 6). A lane that died mid-job
stops blocking the queue after that window, and the claims log shows
exactly which lane held it and when.

## Heartbeats

While working a job, lanes should heartbeat every N minutes (for example,
every 5 minutes): `python3 job_queue.py heartbeat <url> --lane <name>`.
The command appends a fresh `in-progress` entry only when the same lane
holds the live claim; otherwise it prints `not-owner` and exits 1. The
latest entry per URL is authoritative. With no heartbeat within
`CLAIM_MAX_AGE_HOURS`, the claim expires and the job becomes claimable.
An expired lane must claim again before heartbeating. This uses the same
single-host locking scope described below.

## Concurrency limits (read this before going multi-machine)

- Writes are serialized by a sidecar lock file (`<path>.lock`) held
  for the whole read-modify-write transaction. This coordinates lanes
  on ONE POSIX host.
- It does NOT coordinate across machines over a network share: `flock`
  is unreliable on NFS/SMB, and on Windows the lock is a best-effort
  no-op. For multi-machine lanes, keep the queue and claims files on a
  single host that all lanes can reach (or run one queue writer per
  machine). When the lock cannot be trusted, the queue fails open, so
  do not rely on it alone across hosts.
- One lane per browser, one browser per port. The queue cannot protect
  you from two lanes driving the same Chrome; the topology in
  `docs/machine-setup.md` does that.

## Configuration

`JOBPILOT_QUEUE` (default `./queue.json`), `JOBPILOT_CLAIMS`
(default `./claims.jsonl`), `JOBPILOT_LANE` (default `lane-1`), or the
same keys in `config.json` (see `config.py`; env vars win).

## Code-gate rule

A job that hit the Greenhouse email code gate is marked `blocked`
with a `code-gate` reason, never released. Releasing it would let the
next lane re-submit and trigger a second code email. After the
operator enters the code (`code_gate.py`), return the job with
`requeue`.

`codegate.coordinate` owns the waiting loop: the lane marks code-gate jobs
blocked, the coordinator waits once for a human-supplied 8-character code,
and a caller callback uses the existing `code_gate.py` browser mechanics.
After human handling, the job is requeued. On timeout, callers can wire
`requeue_on_timeout(queue_path, claims_path, url)` to retry later. Invalid
codes fail closed without another prompt. The coordinator never reads
email or an inbox; the Telegram notifier is only an extension stub.

## Consent rule

Jobs whose forms contain arbitration agreements, certifications,
background-check authorizations, or assessments are marked `blocked`
by the lane with a consent reason. They are never auto-answered, and
`--no-submit` review does not substitute for explicit human approval.

## Job dict schema

```json
{
  "company": "Acme",
  "title": "Senior Backend Engineer",
  "location": "Austin, TX",
  "salary": "$180K-$220K",
  "url": "https://job-boards.greenhouse.io/acme/jobs/123",
  "ats": "greenhouse",
  "notes": "why this job fits",
  "queued_at": "2026-01-15T00:00:00Z",
  "source": "sweep 2026-01-15",
  "status": "pending",
  "skip_reason": ""
}
```

`sweep.py` writes exactly this shape, so sweep output feeds the queue
directly. Non-Greenhouse jobs in the queue are worked with
`ai-job-pilot` / `batch_apply.py` (there is no Ashby/Lever lane
runner yet); export their URLs with
`python3 -c "import json; print('\n'.join(j['url'] for j in
json.load(open('queue.json')) if j.get('ats') != 'greenhouse'))"`.
