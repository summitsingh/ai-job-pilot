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
   another pass. Only the lane holding the live claim can release or mark it.
   A separate operator process must pass that owner's exact `--lane` token.
   Explicit `--lane ""` is an administrative override.
5. To retry a failed or expired code attempt (never a confirmed application):
   `python3 job_queue.py --claims claims.jsonl requeue <url> --reason "..."`.
   The claims path is required for Applied-history protection; requeue has
   no lane-owner argument and cannot reset an Applied job.

## Stale claims

A claim counts as live only while `in-progress` and younger than
the configured TTL (default 6 hours, preserving the bounded-run recovery
window). `JOBPILOT_CLAIM_TTL_SECONDS` selects a shorter heartbeat lease and
wins over the legacy `CLAIM_MAX_AGE_HOURS` override. Valid values are clamped
to 5 minutes through 24 hours; malformed, nonpositive, NaN and infinite values
fall back to 6 hours. A lane that dies stops blocking after that window.

Malformed or future-dated claim timestamps are repaired once to current UTC
under the claims lock, preserving the owner for one TTL. The repair is appended
to the log, so repeated reads cannot extend it forever. Read-only queue queries
can therefore append a timestamp repair when encountering corrupt claims.

## Heartbeats

The Greenhouse lane calls `heartbeat()` after claim, before submit, and after
submit. These checkpoints verify ownership and refresh the claim. The default
6-hour crash-recovery TTL exceeds a normal single-job run, so per-minute
heartbeats are unnecessary for the bounded pipeline and 5-minute code wait.
For longer manual work, use `python3 job_queue.py --claims claims.jsonl
heartbeat <url> --lane <name>`. Only the live owner can refresh a claim;
a different live owner yields `not-owner`, while an absent or expired claim
yields `no-live-claim`. Both failures exit 1. An expired lane must claim again. This uses the single-host locking scope below.

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
(default `./claims.jsonl`), and `JOBPILOT_LANE`, or the same keys in
`config.json` (see `config.py`; env vars win). The lane and queue claim CLI
use an explicit identity when configured; otherwise `default_lane()` produces
`lane-1@hostname#pid`. For separate CLI invocations that share ownership, set
`JOBPILOT_LANE` or pass the same `--lane` explicitly. Standalone `mark` and `release` use the same unique identity default, so
another process must supply the live owner's exact token. Explicit `--lane ""`
retains the library's administrative override. A rejected mark by a different
live owner prints `not-owner`, determined inside the locked transaction;
an attempt to downgrade an Applied job prints `already-applied`.

## Code-gate rule

The Greenhouse lane wires `codegate.coordinate(CliNotifier(), ...)` into its
code-gate path. It parks the job as `blocked` before waiting, persists
`code_gate_attempts` in the queue, and waits once (300 seconds by default)
for the human's 8-character alphanumeric code. It reuses `code_gate.py`'s
browser mechanics to enter that code once and submit once.

Only a verified confirmation page, a `/confirmation` URL or recognized
confirmation text, leads to `mark applied` and a tracker `Applied` row.
Never requeue after confirmation. The lane checks queue, claims history,
and the local tracker before any submission and skips historical Applied
jobs. Applied queue entries cannot be downgraded or requeued.

A failed or expired attempt can requeue for a later lane run, with at most
3 code-gate attempts per job. The third failure stays `blocked` with evidence,
and `requeue` refuses it. The lane stops after a requeue so it cannot send
another code email immediately. EOF, unsupported stdin, or decoding failure
returns `no_input` and leaves the job blocked for a human, without requeue.
An ambiguous submitted code with no verified confirmation also stays blocked.
Timeout callback failure is reported as blocked, not as a successful retry.

If you handle a parked gate manually with `code_gate.py`, verify confirmation,
then run `job_queue.py mark <url> applied --lane <name>` and record an Applied
tracker row. Requeue is only for a failed or expired attempt below the cap.
The coordinator never reads email or an inbox; Telegram remains an extension
stub. See [tracking](tracking.md) for local records.

`--dry-run` always releases its claim, including blocked, dead, and exception
outcomes. It writes review artifacts only, with no tracker writes or terminal
queue marks. It still fills the browser form for inspection.

## Consent rule

Jobs whose forms contain arbitration agreements, certifications/attestations,
background-check or drug-test authorizations, or assessments are marked `blocked`
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
