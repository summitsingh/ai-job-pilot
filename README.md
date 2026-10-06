# ai-job-pilot

Deterministic, low-token autofill for ATS job application forms (Greenhouse,
Ashby, Lever, Workable). Fill a form with ~50x fewer LLM calls and ~99%
fewer tokens than a naive agent loop, by doing everything deterministic
except the one step that actually needs a model.

## Why this exists

A naive AI agent filling out a job application re-reads the DOM and
re-reasons about every field, burning thousands of tokens per form.
ai-job-pilot flips the approach: extract the form as structured JSON,
make exactly one model call to map fields to your details, then fill
everything deterministically with readback verification.

The result is faster, cheaper, and more reliable than an agent loop,
because the model only does the one thing it is actually needed for.

## Proven on

Real verification numbers from production use:

- **Greenhouse:** 42/42 fields verified on live forms, zero mismatches
- **Ashby + Lever:** 82/82 fields verified across 8 real forms, zero model calls
- **Workable:** 7/9 fill actions verified on a live form (2026-10-04); the 2
  failures were address-autocomplete sub-fields, no Workable-specific code needed
- **Offline tests:** 125 tests (18 fill-core + 33 queue/tracker/
  sweep/launcher/config + 24 reliability + 28 tracking + 22 human/setup), no browser, model,
  or network required. Run with
  `python3 -m unittest test_offline test_operation test_reliability test_tracking test_human`
- **Live submits:** confirmed via `/confirmation` URL plus "Thank you for applying" page text

## Install

```bash
git clone https://github.com/summitsingh/ai-job-pilot.git
cd ai-job-pilot
pip install .
```

This provides eleven console scripts:

| Command | Purpose |
|---|---|
| `ai-job-pilot` | Fill a single application (dry-run or submit) |
| `ai-job-pilot-batch` | Run a queue of posting URLs with dedup and per-application confirmation |
| `ai-job-pilot-code-gate` | Handle Greenhouse's email verification code gate |
| `ai-job-pilot-setup` | Guided setup with non-secret config and credential exports |
| `ai-job-pilot-launch` | Open N headful Chrome browsers, one per lane |
| `ai-job-pilot-lane` | Work a shared queue with cross-lane claims (Greenhouse) |
| `ai-job-pilot-queue` | Inspect and manage the shared queue (`next`/`claim`/`heartbeat`/`mark`/`requeue`) |
| `ai-job-pilot-sweep` | Filter raw postings into vetted queue candidates |
| `ai-job-pilot-track` | Record applications (JSONL, Google Sheets, Notion) |
| `ai-job-pilot-scoreboard` | Per-lane day/week totals of applied/skipped/blocked/dead |
| `ai-job-pilot-analytics` | Funnel conversion and time-to-response over the tracker JSONL |

Or skip the install and run the scripts directly with Python 3.9+.
The core is standard-library only, no virtualenv needed. The optional
Google Sheets tracker backend needs `google-api-python-client` and
`google-auth` (`pip install google-api-python-client google-auth`).

For guided setup, run `ai-job-pilot-setup` after creating your real
`facts.json`. See [the setup wizard guide](docs/setup.md) for the flow,
credential exports, and `--yes --dry-run`.

## How it works

```
posting URL -> schema_dump -> map -> fill -> verify -> submit
```

### 1. Schema dump (no model)

`schema_dump.py` loads the posting in a debug-Chrome browser over CDP
and extracts every form field as JSON: label text, input type, options
for selects and radios, required flags, and autocomplete attributes.
This is pure DOM introspection, zero tokens spent.

### 2. Map (one model call)

`map.py` sends the field schema and your `facts.json` to a local model
with a strict JSON schema constraint. The model returns a field-to-answer
mapping. Fields with no safe answer map to `skip`, never to a guess.
This is the only LLM call in the entire pipeline.

Supported model servers: any OpenAI-compatible endpoint (LM Studio
default at `http://127.0.0.1:1234`) or Ollama native API. Configure
with `JOBPILOT_MODEL_URL` and `JOBPILOT_API_FLAVOR`.

### 3. Deterministic screening answers (no model)

`hard_patterns.py` and `templates.py` answer the long tail of screening
questions with pure pattern matching: work authorization, demographics,
salary expectations, education, yes/no willingness questions. First
matching pattern wins. Unknown fields are skipped, never guessed.
The Ashby and Lever templates cover 82/82 fields across 8 real forms
with zero model calls.

### 4. Fill (no model)

`fill.py` applies the field map through CDP: batched JavaScript for text
inputs, real mouse clicks for selects, radios, checkboxes, and file
uploads. After filling, it reads every field back from the DOM and diffs
against the expected values. Any mismatch is reported, not silently
accepted.

### 5. Verify and submit (no model)

`verify.py` confirms the fill state. `submit_only.py` clicks submit.
For Greenhouse, `code_gate.py` handles the email verification gate
(see below). Confirmation is verified by URL (`/confirmation`) and
page text ("Thank you for applying") before the run is logged as
successful.

### The one hard rule

The harness never invents facts. Anything not in `facts.json` is
skipped. This is enforced at three levels: the map prompt instructs
`skip` for unknown fields, the templates only fire on known patterns,
and the fill step refuses to write values with no source.

## Quick start

```bash
cp facts.example.json facts.json   # then fill in YOUR details
# serve a local model (LM Studio default http://127.0.0.1:1234),
# or point JOBPILOT_MODEL_URL at any OpenAI-compatible endpoint
export JOBPILOT_SSH_HOST="user@browser-host"   # host running debug Chrome
ai-job-pilot --url <greenhouse|ashby|lever|workable posting URL> \
    --port 9226 --no-submit
```

`--no-submit` fills and verifies without submitting. Drop it to actually
submit. Every run emits `result.json`:

```json
{
  "status": "filled|confirmed|failed|blocked",
  "confirmation_evidence": "URL and/or confirmation text",
  "fields_filled": 42,
  "fields_skipped": 3,
  "notes": "human-readable summary"
}
```

## Using with an AI agent

ai-job-pilot is built to be driven by an agent. Point your agent at this
repo and tell it to read `SKILL.md`:

> Clone https://github.com/summitsingh/ai-job-pilot and read SKILL.md.
> Apply to <posting URL> with my facts. Dry-run first, show me the result
> before submitting.

`SKILL.md` is the full playbook: prerequisites, the workflow, the
Greenhouse code-gate protocol, and the safety invariants. `AGENTS.md` is
the longer runbook for agents that want the full context.

### Per-platform setup

**Claude Code** reads `SKILL.md` natively. For a persistent install:

```bash
mkdir -p ~/.claude/skills/ai-job-pilot
cp /path/to/ai-job-pilot/SKILL.md ~/.claude/skills/ai-job-pilot/
```

Then: "Read the ai-job-pilot skill and apply to <posting URL> using my
facts.json. Dry-run first."

**Codex** works from the repo directory:

```bash
git clone https://github.com/summitsingh/ai-job-pilot.git
cd ai-job-pilot && cp facts.example.json facts.json  # fill in your details
codex
```

Then: "Read SKILL.md and follow it to apply to <posting URL>. Use
--no-submit first and show me the result before submitting."

**ChatGPT** via a Custom GPT: upload `SKILL.md`, `AGENTS.md`, and
`docs/facts-schema.md` as knowledge files, with instructions to follow
the SKILL.md workflow. Note: ChatGPT cannot run the browser or scripts
itself; use it for planning and review, running the commands it gives
you and pasting results back.

**Muse** runs the full loop itself (shell, browser, filesystem):

> Clone https://github.com/summitsingh/ai-job-pilot and read SKILL.md.
> Apply to <posting URL> with my facts at <path to facts.json>.

Submit approvals and Greenhouse email codes come to you as approval
prompts.

**Hermes / OpenClaw / local agents:** `AGENTS.md` is the self-contained
runbook. "Read AGENTS.md in ./ai-job-pilot and apply to <URL>."

### The two human steps (all platforms)

1. **Submit approval.** The agent dry-runs with `--no-submit` and shows
   you the fill. You approve before anything is submitted.
2. **Greenhouse email codes.** After submit, Greenhouse emails an
   8-character code. You read it from your email and give it to the agent.

Full details: [`docs/agent-setup.md`](docs/agent-setup.md).

## Batch mode

`ai-job-pilot-batch` runs a queue of posting URLs one at a time:

```bash
ai-job-pilot-batch --queue examples/queue.txt --port 9226 --dedup \
    --resume-map examples/resume-map.json
```

- `--dedup`: skip URLs already in your applications log. Safe to re-run
  the same queue; completed applications are not repeated.
- `--resume-map`: JSON mapping URL substrings to resume file paths, so
  each posting gets its tailored resume without editing `facts.json`.
  Example: `{"acme": "/path/to/acme-tailored.pdf"}`.
- Each application fills with `--no-submit` first and asks for your
  confirmation before submitting. Every result is appended to
  `applications.jsonl` as one JSON object per line.

## Greenhouse code gate

Greenhouse shows an 8-character email verification code after you click
submit. Key facts:

- The gate is **per-application**: verifying once does not carry over
  to the next application.
- Codes expire quickly (under 40 minutes in testing).
- There is no resend button; only a fresh Submit click triggers a new code.

`ai-job-pilot-code-gate` detects the gate, prompts you for the code on
stdin, enters one character per input box, clicks submit, and verifies
the confirmation page. Codes stay human-in-the-loop: they are never
guessed, stored, or bypassed. See `AGENTS.md` for the full protocol.

## Finding jobs to apply to

`docs/platforms.md` lists 50+ job discovery platforms: API-first sources
(AI Dev Jobs has a free REST API built for agents), high-volume boards
(EchoJobs, Remotive), niche boards (Kube Careers for infra, LeadJobs.dev
for Staff+), AI-specific boards, remote-first boards, startup and VC
portfolio boards, and direct ATS board URL patterns.

## Running the full operation

Beyond single fills, the repo ships the whole pipeline:

```bash
# 1. open one headful Chrome per lane (interactive; validates ports)
python3 launch_browsers.py

# 2. vet raw postings into the shared queue
python3 sweep.py --in raw.json --applied applications.jsonl \
    --queue queue.json --min-salary 100000

# 3. run a lane per browser (each claims jobs so lanes never collide).
# Dry-run first: review result.json, then drop --no-submit.
JOBPILOT_CDP_URL=127.0.0.1:9445 JOBPILOT_LANE="machine-a:9445" \
    python3 lane_greenhouse.py --workdir /tmp/jobpilot/lane-9445 --no-submit
```

- `docs/queue.md`: the queue + claims protocol (`job_queue.py`)
- `docs/sweep.md`: the vetting funnel (`sweep.py`)
- `docs/tracking.md`: application tracking (`tracker.py`: JSONL,
  Google Sheets, Notion backends)
- `docs/machine-setup.md`: visible-browser topology and launch recipes
- `config.example.json`: every env var in one place (copy to
  `config.json`, gitignored)

Applications are tracked automatically by the lane; `tracker.py` also
works standalone:

```bash
export TRACKER_BACKENDS="jsonl,sheets"
python3 tracker.py --company Acme --title "Senior Backend Engineer" \
  --url https://job-boards.greenhouse.io/acme/jobs/123 --backend jsonl,sheets
```

## Demo

`demo/demo.cast` is a recorded `--no-submit` run. Play it with:

```bash
asciinema play demo/demo.cast
```

## Configuration

| Variable | Default | Purpose |
|---|---|---|
| `JOBPILOT_SSH_HOST` | (required) | ssh destination of the browser host |
| `JOBPILOT_SSH_CMD` | `ssh` | ssh command |
| `JOBPILOT_SCP_CMD` | `scp` | scp command (copies the CDP driver) |
| `JOBPILOT_CDP_URL` | (unset) | `host:port` of a directly reachable debug Chrome; when set, the driver runs locally without ssh |
| `JOBPILOT_HOST_SSH` | (unset) | ssh helper for browser-host file checks in direct mode |
| `JOBPILOT_MODEL_URL` | `http://127.0.0.1:1234` | model server base URL |
| `JOBPILOT_API_FLAVOR` | `openai` | `openai` (LM Studio and compatibles) or `ollama` (native API) |
| `JOBPILOT_MODEL_NAME` | `qwen/qwen3-coder-next` | primary model id |
| `JOBPILOT_BACKUP_MODELS` | (see model.py) | comma-separated fallback model ids, tried in order |
| `JOBPILOT_FACTS` | `./facts.json` | path to your facts file |
| `JOBPILOT_RESUME` | (from facts.json) | override path to your resume PDF |
| `JOBPILOT_QUEUE` | `./queue.json` | shared job queue for lanes |
| `JOBPILOT_CLAIMS` | `./claims.jsonl` | cross-lane claims log |
| `JOBPILOT_LANE` | `lane-1` | this lane's name (e.g. machine-a:9445) |
| `TRACKER_BACKENDS` | `jsonl` | comma-separated: jsonl, sheets, notion |
| `TRACKER_SHEET_ID` | (unset) | Google Sheet id for the sheets backend |
| `TRACKER_SHEET_TAB` | `Applications` | sheet tab name |
| `GOOGLE_APPLICATION_CREDENTIALS` | (unset) | service-account JSON path |
| `TRACKER_NOTION_TOKEN` | (unset) | Notion integration token |
| `TRACKER_NOTION_DB` | (unset) | Notion database id |

Legacy `ATS_*` names for these variables are also honored.

## Browser setup

The harness drives Chrome over the DevTools protocol. Start Chrome with
remote debugging on your browser host:

```bash
google-chrome --remote-debugging-port=9226 --remote-allow-origins='*' \
  --no-first-run --no-default-browser-check
```

`ensure_driver()` copies the stdlib-only `cdp_driver.py` to the browser
host automatically over ssh/scp; no Python venv is needed on the browser
host. If Chrome runs on the same machine, set `JOBPILOT_CDP_URL` instead
and skip ssh entirely.

## facts.json

All answers come from `facts.json` (gitignored; start from
`facts.example.json`). It holds:

- Identity and contact (name, email, phone, location, links)
- Work history (titles, companies, dates, descriptions)
- Education (degrees, schools, dates)
- Work authorization (visa status, sponsorship needs)
- Salary expectations
- Screening posture (willingness defaults, disclosure answers, demographics)

`templates.py` documents every supported key, and
`docs/facts-schema.md` has the full schema reference. Keep this file
private; it is gitignored by default.

## Safety

- **No invented data.** Fields without answers are skipped, never guessed.
- **No CAPTCHA solving.** Detection stops the run; the operator is notified.
- **No account-gated ATS automation** without explicit setup (iCIMS, Workday,
  Taleo, and similar multi-page or account-walled flows are out of scope).
- **Human approval** before every submit in single-run mode
  (`--no-submit` dry-run first). Lane mode (`lane_greenhouse.py`)
  submits unattended by design: run it with `--no-submit` first and
  review `result.json` before letting it submit. Either way, the lane
  blocks on consent fields (see below) instead of answering them.
- **Consent fields are never auto-answered.** Arbitration agreements,
  certifications/attestations, background-check and drug-test
  authorizations, and assessments always stop the run for explicit
  human approval per application. `--no-submit` review does not
  substitute for this.
- **Human-in-the-loop** for Greenhouse email codes.
- Some employers are blocklisted from automation in the default config;
  see `modal_common.py`.
- `facts.json` is gitignored. Never commit it.

## Layout

- `ats_fill.py` - orchestrator: dump -> map -> fill -> verify -> submit
- `batch_apply.py` - queue runner with `--dedup`, `--resume-map`,
  per-application confirm prompt, and JSONL logging
- `code_gate.py` - Greenhouse email verification gate handler
- `examples/` - sample `queue.txt` and `resume-map.json` for batch mode
- `demo/demo.cast` - recorded `--no-submit` run (play with asciinema)
- `extract.py`, `schema_dump.py` - form introspection to JSON
- `map.py` - the single model call (strict JSON field-to-fact mapping)
- `hard_patterns.py`, `templates.py` - deterministic screening answers,
  zero model calls
- `fill.py`, `set_select.py`, `set_select2.py`, `modal_common.py` - fill
  primitives over CDP
- `verify.py`, `submit_only.py` - fill verification and submission
- `cdp_driver.py`, `cdp_direct.py`, `common.py` - transport (stdlib-only
  driver, no venv needed on the browser host)
- `model.py` - model client with fallback chain
- `indeed_dump.py`, `indeed_fill.py` - Indeed application lane
- `launch_browsers.py` - headful Chrome launcher, one browser per lane
  (`ai-job-pilot-launch`)
- `lane_greenhouse.py` - queue-driven Greenhouse lane: claim, fill,
  submit, verify, track (`ai-job-pilot-lane`)
- `job_queue.py` - shared queue with cross-lane claims
  (`ai-job-pilot-queue`); protocol in `docs/queue.md`
- `tracker.py` - application tracking: JSONL, Google Sheets, Notion
  (`ai-job-pilot-track`); setup in `docs/tracking.md`
- `sweep.py` - posting vetting funnel: title/location/salary/
  sponsorship filters, dedup, queue writing (`ai-job-pilot-sweep`);
  protocol in `docs/sweep.md`
- `config.py`, `config.example.json` - config file loading (env vars
  override `config.json`, which overrides built-in defaults)
- `test_offline.py` - 18 offline unit tests for the fill core
- `test_operation.py` - 33 offline unit tests for queue, tracker,
  sweep, launcher, and config (no browser/model needed)
- `test_reliability.py` - 24 offline unit tests for the submit watchdog,
  location verifier, and budgeted model calls
- `submit_watchdog.py`, `location_check.py` - lane reliability modules
  (swallowed-submit recovery, location autocomplete check)
- `test_tracking.py` - 28 offline unit tests for tracker URL parsing,
  scoreboard, funnel analytics, and fuzzy dedup
- `scoreboard.py`, `analytics.py` - lane scoreboard and funnel analytics
  (`ai-job-pilot-scoreboard`, `ai-job-pilot-analytics`)
- `test_human.py` - 22 offline tests for heartbeats, code coordination,
  field-map review, and setup
- `test_*.py` - live-browser integration tests (`--no-submit` only)
- `docs/ats-notes.md` - Greenhouse, Ashby, Lever, and Workable quirks
  learned in production
- `docs/greenhouse-quirks.md` - Greenhouse form quirks: react-selects,
  location autocomplete, cover-letter requirements, code gate
- `docs/machine-setup.md` - visible-browser topology and per-platform
  launch recipes
- `docs/platforms.md` - 50+ job discovery platforms (boards, APIs, VC portfolios)
- `docs/agent-setup.md` - per-platform agent setup (Claude Code, Codex,
  ChatGPT, Muse, Hermes, OpenClaw)
- `docs/facts-schema.md` - full `facts.json` schema reference
- `docs/troubleshooting.md` - common failures and fixes

## Notes

- If you are an AI agent, read `AGENTS.md` first. It documents the full
  run procedure, the Greenhouse code-gate protocol, and the safety
  invariants.
- CAPTCHA and reCAPTCHA detection stops the run instead of attempting
  a solve.
- CI runs the offline test suite on every push. See `CONTRIBUTING.md`
  before submitting a pull request.

## License

MIT. See LICENSE.
