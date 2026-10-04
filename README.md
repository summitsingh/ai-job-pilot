# ai-job-pilot

Deterministic, low-token autofill for ATS job application forms (Greenhouse,
Ashby, Lever, Workable). Fill a form with ~50x fewer LLM calls and ~99%
fewer tokens than a naive agent loop, by doing everything deterministic
except the one step that actually needs a model.

## Proven on

Real verification numbers from production use:

- **Greenhouse:** 42/42 fields verified on live forms, zero mismatches
- **Ashby + Lever:** 82/82 fields verified across 8 real forms, zero model calls
- **Workable:** 7/9 fill actions verified on a live form (2026-10-04), zero
  Workable-specific code; 2 failures were address-autocomplete sub-fields
- **Offline tests:** 18/18 passing, no browser, model, or network required
- **Live submits:** confirmed via `/confirmation` URL + "Thank you for applying" page text

## Install

```bash
git clone https://github.com/summitsingh/ai-job-pilot.git
cd jobpilot
pip install .
```

This provides three console scripts: `ai-job-pilot` (single application),
`ai-job-pilot-batch` (queue runner), and `ai-job-pilot-code-gate` (email code gate
handler). Or skip the install and run the scripts directly with Python 3.9+;
the only dependency is the standard library.

## How it works

```
posting URL -> schema_dump -> map -> fill -> verify -> submit
```

1. **schema_dump.py** - loads the posting in a debug-Chrome browser and dumps
   every form field (label, type, options, required) as JSON.
2. **map.py** - a local model maps each field to an answer from your
   `facts.json`, using strict JSON schema output. One model call per form.
   Fields with no safe answer map to `skip`.
3. **hard_patterns.py / templates.py** - deterministic pattern matchers that
   answer the long tail of screening questions (work auth, demographics,
   salary, education, yes/no willingness) with zero model calls. First
   matching pattern wins; unknown fields are skipped, never guessed.
4. **fill.py** - applies the field map through CDP: batched JS for text
   fields, real clicks for selects/radios/uploads. Reads every field back
   and diffs against expected values.
5. **verify.py / submit_only.py** - confirm the fill, then submit.

The one hard rule: the harness never invents facts. Anything not in
`facts.json` is skipped.

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
submit. Every run emits `result.json` with `{status, confirmation_evidence,
fields_filled, fields_skipped, notes}`.

## Using with an AI agent

jobpilot is built to be driven by an agent. Point your agent at this repo
and tell it to read `SKILL.md`:

> Clone https://github.com/summitsingh/ai-job-pilot and read SKILL.md.
> Apply to <posting URL> with my facts. Dry-run first, show me the result
> before submitting.

`SKILL.md` is the full playbook: prerequisites, the workflow, the
Greenhouse code-gate protocol, and the safety invariants. `AGENTS.md` is
the longer runbook for agents that want the full context.

### Per-platform setup

**Claude Code** reads `SKILL.md` natively. For a persistent install:

```bash
mkdir -p ~/.claude/skills/jobpilot
cp /path/to/jobpilot/SKILL.md ~/.claude/skills/jobpilot/
```

Then: "Read the jobpilot skill and apply to <posting URL> using my
facts.json. Dry-run first."

**Codex** works from the repo directory:

```bash
git clone https://github.com/summitsingh/ai-job-pilot.git
cd jobpilot && cp facts.example.json facts.json  # fill in your details
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
runbook. "Read AGENTS.md in ./jobpilot and apply to <URL>."

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

- `--dedup`: skip URLs already in your applications log (safe re-runs).
- `--resume-map`: JSON mapping URL substrings to resume paths, so each
  posting gets its tailored resume without editing `facts.json`.
- Each application fills with `--no-submit` first and asks for your
  confirmation before submitting; every result is appended to
  `applications.jsonl`.

## Greenhouse code gate

Greenhouse shows an 8-character email verification code on submit
(per-application; codes expire quickly). `ai-job-pilot-code-gate` detects the
gate, prompts for the code, enters one character per box, submits, and
verifies the confirmation page. Codes stay human-in-the-loop; they are
never guessed, stored, or bypassed. See `AGENTS.md` for the full protocol.

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
| `JOBPILOT_CDP_URL` | (unset) | `host:port` of a directly reachable debug Chrome; when set, the driver runs locally |
| `JOBPILOT_HOST_SSH` | (unset) | ssh helper for browser-host file checks in direct mode |
| `JOBPILOT_MODEL_URL` | `http://127.0.0.1:1234` | model server base URL |
| `JOBPILOT_API_FLAVOR` | `openai` | `openai` (LM Studio) or `ollama` (native API) |
| `JOBPILOT_MODEL_NAME` | `qwen/qwen3-coder-next` | primary model id |
| `JOBPILOT_BACKUP_MODELS` | (see model.py) | comma-separated fallback model ids |

Legacy `ATS_*` names for these variables are also honored.

## Browser setup

The harness drives Chrome over the DevTools protocol. Start Chrome with
remote debugging on your browser host:

```bash
google-chrome --remote-debugging-port=9226 --remote-allow-origins='*' \
  --no-first-run --no-default-browser-check
```

`ensure_driver()` copies the stdlib-only `cdp_driver.py` to the browser
host automatically; no venv needed there.

## facts.json

All answers come from `facts.json` (gitignored; start from
`facts.example.json`). It holds identity, contact, work history,
education, work authorization, salary expectations, and screening
posture (willingness defaults, disclosure answers, demographics).
`templates.py` documents every supported key. Keep this file private.

## Layout

- `ats_fill.py` - orchestrator: dump -> map -> fill -> verify -> submit
- `batch_apply.py` - run a queue of posting URLs one at a time, with
  `--dedup`, `--resume-map`, a per-application confirm prompt, and a JSONL log
- `code_gate.py` - handle the Greenhouse email verification gate
  (prompts for the code, enters it, verifies confirmation)
- `examples/` - sample `queue.txt` and `resume-map.json` for batch mode
- `demo/demo.cast` - recorded `--no-submit` run (play with asciinema)
- `extract.py`, `schema_dump.py` - form introspection
- `map.py` - model-based field-to-fact mapping (strict JSON, one call)
- `hard_patterns.py`, `templates.py` - deterministic screening answers
- `fill.py`, `set_select.py`, `set_select2.py`, `modal_common.py` - fill primitives
- `verify.py`, `submit_only.py` - verification and submission
- `cdp_driver.py`, `cdp_direct.py`, `common.py` - CDP transport
- `model.py` - local model client with fallback chain
- `indeed_dump.py`, `indeed_fill.py` - Indeed application lane
- `test_offline.py` - offline unit tests (no browser/model needed);
  `test_*.py` are live-browser integration tests (`--no-submit` only)
- `docs/ats-notes.md` - Greenhouse/Ashby/Lever quirks learned in production
- `docs/platforms.md` - 50+ job discovery platforms (boards, APIs, VC portfolios)

## Notes

- If you are an AI agent, read `AGENTS.md` first. It documents the full
  run procedure, the Greenhouse code-gate protocol, and the safety
  invariants.
- Greenhouse shows an email verification code on submit; `code_gate.py`
  walks the operator through it (or see `AGENTS.md` for the protocol).
- CAPTCHA/reCAPTCHA detection stops the run instead of attempting a solve.
- Some employers are blocklisted from automation in the default config;
  see `modal_common.py`.
- CI runs the offline test suite on every push. See `CONTRIBUTING.md`
  before submitting a pull request.

## License

MIT. See LICENSE.
