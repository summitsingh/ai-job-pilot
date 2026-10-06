# ai-job-pilot

[![Tests](https://github.com/summitsingh/ai-job-pilot/actions/workflows/tests.yml/badge.svg)](https://github.com/summitsingh/ai-job-pilot/actions/workflows/tests.yml)
[![License: MIT](https://img.shields.io/badge/License-MIT-green.svg)](LICENSE)
[![Python 3.9+](https://img.shields.io/badge/python-3.9+-blue.svg)](pyproject.toml)

Deterministic autofill for Greenhouse, Ashby, Lever, and Workable.
Uses ~99% fewer tokens than a naive agent loop: patterns and templates
first, at most one strict-JSON backup model call per form. Answers come
from your facts; unknowns are skipped and every fill is verified.

## Why this exists

A naive AI agent filling out a job application re-reads the DOM and
re-reasons about every field, burning thousands of tokens per form.
ai-job-pilot flips the approach: extract the form as structured JSON,
map fields with deterministic patterns and templates, and use at most
one strict-JSON backup model call for whatever the deterministic pass
leaves unmapped. Then fill everything deterministically and verify each
field by reading it back from the DOM.

The model only does the one thing it is actually needed for. The result
is faster, cheaper, and more reliable than an agent loop, and it never
invents your data: anything not in `facts.json` maps to `skip`.

## Verified on real forms

Production verification numbers, not benchmarks:

- **Greenhouse:** 42/42 fields verified on live forms, zero mismatches
- **Ashby + Lever:** 82/82 fields verified across 8 real forms, zero model calls
- **Workable:** 7/9 fill actions verified on a live form (2026-10-04); the 2
  failures were address-autocomplete sub-fields, no Workable-specific code needed
- **Offline tests:** 224 passing (24 fill-core, 54 operation, 50 reliability,
  48 tracking, 48 human/setup). Run `python3 -m unittest discover`.
  No browser, model server, or network required.
- **Live submits:** confirmed via `/confirmation` URL plus "Thank you for applying" page text

## Quick start

Prerequisites: Python 3.9+, Chrome with remote debugging, and (optionally)
any OpenAI-compatible model server for the backup call.

```bash
git clone https://github.com/summitsingh/ai-job-pilot.git
cd ai-job-pilot
pip install .
cp facts.example.json facts.json   # then fill in YOUR details
```

Start Chrome with remote debugging:

```bash
google-chrome --remote-debugging-port=9226 --remote-allow-origins='*' \
  --no-first-run --no-default-browser-check
```

Dry-run an application (fills and verifies, never submits):

```bash
export JOBPILOT_CDP_URL="127.0.0.1:9226"   # same-machine browser; skip ssh entirely
ai-job-pilot --url <greenhouse|ashby|lever|workable posting URL> \
    --port 9226 --no-submit
```

Every run emits `result.json`:

```json
{
  "status": "filled|confirmed|failed|blocked",
  "confirmation_evidence": "URL and/or confirmation text",
  "fields_filled": 42,
  "fields_skipped": 3,
  "notes": "human-readable summary"
}
```

Drop `--no-submit` to actually submit (after you approve the dry-run).
For a remote browser host, set `JOBPILOT_SSH_HOST="user@browser-host"`
instead of `JOBPILOT_CDP_URL`. For guided setup, run `ai-job-pilot-setup`
and see [the setup wizard guide](docs/setup.md).

## Safety

- **No invented data.** Fields without answers are skipped, never guessed.
  Enforced at three levels: the map prompt, the templates, and the fill step.
- **No CAPTCHA solving.** Detection stops the run; the operator is notified.
- **Consent fields are never auto-answered.** Arbitration agreements,
  certifications, background-check and drug-test authorizations, and
  assessments always stop for explicit human approval per application.
- **Human approval before every submit** in single-run mode (`--no-submit`
  dry-run first). Lane mode submits unattended by design: dry-run first,
  review `result.json`, then let it run.
- **Human-in-the-loop for Greenhouse email codes.** Greenhouse emails an
  8-character code per application; codes are never guessed, stored, or bypassed.
- `facts.json` is gitignored. Never commit it.

## How it works

```
posting URL -> schema_dump -> map -> fill -> verify -> submit
```

1. **Schema dump (no model).** `schema_dump.py` loads the posting in a
   debug-Chrome browser over CDP and extracts every form field as JSON:
   label text, input type, options, required flags, autocomplete
   attributes. Pure DOM introspection, zero tokens.
2. **Map (deterministic first, at most one backup model call).**
   `hard_patterns.py` and `templates.py` answer screening questions with
   pure pattern matching: work authorization, demographics, salary,
   education, yes/no willingness. Only unmapped or low-confidence fields
   reach one strict-JSON backup call. Fields with no truthful answer map
   to `skip`.
3. **Fill (no model).** `fill.py` applies the field map through CDP:
   batched JavaScript for text inputs, real mouse clicks for selects,
   radios, checkboxes, and file uploads. Idempotent yes/no toggles
   (Ashby buttons are independent toggles; blind clicks unset answers).
   After filling, every field is read back from the DOM and diffed
   against expected values. Mismatches are reported, not silently accepted.
4. **Verify and submit.** `verify.py` combines confirmation rules with
   bounded model classification. `submit_only.py` clicks submit; the
   Greenhouse lane uses rule-based outcome checks plus a submit watchdog
   that detects swallowed submits. Confirmation is verified by URL
   (`/confirmation`) and page text before a run is logged as successful.

Model servers: any OpenAI-compatible endpoint (LM Studio default
`http://127.0.0.1:1234`) or Ollama native API. Configure with
`JOBPILOT_MODEL_URL` and `JOBPILOT_API_FLAVOR`. The model is probed only
when the backup path is actually needed.

## Naive agent loop vs ai-job-pilot

| | Naive agent loop | ai-job-pilot |
|---|---|---|
| Model calls per form | dozens (re-reads DOM, re-reasons per field) | at most one, only for unmapped fields |
| Token cost | thousands per form | ~99% fewer |
| Field mapping | model guesses every field | deterministic patterns first, backed by 500+ real applications |
| Unknown fields | model may hallucinate | map to `skip`, never guessed |
| Fill verification | none | every field read back from the DOM and diffed |
| Consent fields | model may agree | always stop for human approval |
| CAPTCHA | may attempt solve | detection stops the run |

## Using with an AI agent

Point your agent at this repo and tell it to read `SKILL.md`:

> Clone https://github.com/summitsingh/ai-job-pilot and read SKILL.md.
> Apply to <posting URL> with my facts. Dry-run first, show me the result
> before submitting.

`SKILL.md` is the full playbook: prerequisites, the workflow, the
Greenhouse code-gate protocol, and the safety invariants. `AGENTS.md` is
the longer runbook for agents that want the full context.

Per-platform notes:

- **Claude Code** reads `SKILL.md` natively. For a persistent install,
  copy it to `~/.claude/skills/ai-job-pilot/`.
- **Codex** works from the repo directory: clone, copy
  `facts.example.json` to `facts.json`, then "Read SKILL.md and follow
  it to apply to <posting URL>. Use --no-submit first."
- **ChatGPT** via a Custom GPT: upload `SKILL.md`, `AGENTS.md`, and
  `docs/facts-schema.md` as knowledge files. Running the workflow needs
  shell access and debug Chrome; use it for planning and review, running
  the commands it gives you and pasting results back.
- **Muse** runs the full loop itself. Submit approvals and Greenhouse
  email codes come to you as approval prompts.

The human steps: approve the dry-run before submit, and hand over
Greenhouse email codes when the gate appears. Full details:
[`docs/agent-setup.md`](docs/agent-setup.md).

## Batch mode and operations

```bash
# Queue of postings: fills each with --no-submit, asks per-submit confirmation
ai-job-pilot-batch --queue examples/queue.txt --port 9226 --dedup \
    --resume-map examples/resume-map.json

# Greenhouse code gate: prompts for the 8-char email code, enters it, verifies
ai-job-pilot-code-gate

# Full lane operation: vet postings, run a queue-driven lane, track results
python3 sweep.py --in raw.json --applied applications.jsonl \
    --queue queue.json --min-salary 100000
JOBPILOT_CDP_URL=127.0.0.1:9445 \
    python3 lane_greenhouse.py --workdir /tmp/jobpilot/lane-9445 --no-submit
```

Eleven console scripts ship with `pip install .`: `ai-job-pilot`,
`ai-job-pilot-batch`, `ai-job-pilot-code-gate`, `ai-job-pilot-lane`,
`ai-job-pilot-sweep`, `ai-job-pilot-track`, `ai-job-pilot-launch`,
`ai-job-pilot-setup`, `ai-job-pilot-queue`, `ai-job-pilot-scoreboard`,
`ai-job-pilot-analytics`.

Docs: [setup](docs/setup.md) · [queue protocol](docs/queue.md) ·
[sweep funnel](docs/sweep.md) · [tracking](docs/tracking.md) ·
[machine setup](docs/machine-setup.md) · [ATS quirks](docs/ats-notes.md) ·
[Greenhouse quirks](docs/greenhouse-quirks.md) ·
[50+ job discovery platforms](docs/platforms.md) ·
[troubleshooting](docs/troubleshooting.md)

## Configuration

Key variables (full list in `config.example.json`; legacy `ATS_*` names honored):

| Variable | Default | Purpose |
|---|---|---|
| `JOBPILOT_CDP_URL` | (unset) | `host:port` of a directly reachable debug Chrome; skips ssh |
| `JOBPILOT_SSH_HOST` | (unset) | ssh destination for remote browser mode |
| `JOBPILOT_MODEL_URL` | `http://127.0.0.1:1234` | model server base URL (backup calls only) |
| `JOBPILOT_API_FLAVOR` | `openai` | `openai` or `ollama` |
| `JOBPILOT_FACTS` | `./facts.json` | path to your facts file |
| `JOBPILOT_LANE` | `lane-1@hostname#pid` | this lane's name for queue claims |
| `TRACKER_BACKENDS` | `jsonl` | comma-separated: jsonl, sheets, notion |

## FAQ

**Do I need a model server?** No. Deterministic patterns handle the
long tail; the model is a backup for unfamiliar fields. If no model is
reachable, unmapped fields are skipped, not guessed.

**Does it work with my local Chrome?** Yes. Start Chrome with
`--remote-debugging-port` and set `JOBPILOT_CDP_URL`. Everything stays
on your machine.

**What happens with consent fields?** The run stops and asks you.
Arbitration, background checks, drug tests, and assessments are never
auto-answered.

**What if a required field has no truthful answer?** It maps to `skip`
and the run reports it. The harness never invents data.

**How do Greenhouse email codes work?** After submit, Greenhouse emails
an 8-character code (per application, expires in under 40 minutes).
`ai-job-pilot-code-gate` prompts you, enters it, and verifies the
confirmation page.

**Which ATS platforms are supported?** Greenhouse, Ashby, Lever,
Workable, plus an Indeed lane. iCIMS, Workday, Taleo, and
SuccessFactors are out of scope (account-walled flows).

**Can I run many applications in a row?** Yes: batch mode with `--dedup`
for a queue, or the lane operation (`lane_greenhouse.py` + `sweep.py` +
`tracker.py`) for continuous volume with cross-lane claim dedup.

## Contributing

See [CONTRIBUTING.md](CONTRIBUTING.md). The rules that matter: never
invent facts, never commit `facts.json`, keep the CDP driver
stdlib-only, deterministic-first for new patterns, and add offline tests
for every new pattern. CI runs the offline suite on every push.

## License

MIT. See [LICENSE](LICENSE).

Built by [Summit Singh Thakur](https://github.com/summitsingh).
