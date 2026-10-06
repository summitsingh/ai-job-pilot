# jobpilot skill

Deterministic, low-token autofill for ATS job application forms
(Greenhouse, Ashby, Lever, Workable). Give this skill a posting URL and
applicant facts; it fills the form truthfully, verifies every field by
readback, and submits. One model call per form; everything else is
deterministic.

## When to use this skill

- The user asks to apply to a job, fill an application form, or automate
  ATS applications.
- The user points you at this repo and says "use jobpilot" or "apply with
  jobpilot".
- You need to fill a Greenhouse, Ashby, Lever, or Workable application form.

## When NOT to use it

- The form is on iCIMS, Taleo, Workday, or SuccessFactors (account
  creation required; out of scope by design).
- The form shows a CAPTCHA or bot challenge (stop and report; never solve).
- LinkedIn Easy Apply (excluded deliberately).

## Prerequisites (do once)

1. Clone the repo and install (stdlib only, no dependencies):
   ```bash
   git clone https://github.com/summitsingh/ai-job-pilot.git
   cd jobpilot
   pip install .
   ```
2. Copy `facts.example.json` to `facts.json` and fill in the applicant's
   REAL details. Every answer the harness gives comes from this file.
   Never commit it. If it is missing, stop and ask the user to create it.
   See `docs/facts-schema.md` for every key.
3. Start Chrome with remote debugging:
   ```bash
   google-chrome --remote-debugging-port=9226 \
     --remote-allow-origins='*' \
     --no-first-run --no-default-browser-check
   ```
   Then set `JOBPILOT_CDP_URL="127.0.0.1:9226"` (same-machine browser).
   For a remote browser host, set `JOBPILOT_SSH_HOST="user@host"` instead.
4. Model: any OpenAI-compatible endpoint with strict JSON schema support.
   Set `JOBPILOT_MODEL_URL` (default `http://127.0.0.1:1234/v1`, LM Studio).
   If the model cannot do constrained JSON, the map step produces garbage;
   switch models, do not loosen the schema.
5. The `resume_path` in `facts.json` must exist ON THE BROWSER HOST.

## The workflow

### Single application

```bash
# 1. Always dry-run first
python3 ats_fill.py --url <posting URL> --port 9226 \
  --no-submit --workdir /tmp/jobpilot/run1

# 2. Inspect result.json: fields_filled vs fields_skipped.
#    Skips are normal for questions with no truthful answer.

# 3. Look at the screenshot in the workdir before submitting.

# 4. Submit
python3 ats_fill.py --url <posting URL> --port 9226 \
  --workdir /tmp/jobpilot/run1
```

Result JSON: `{status, confirmation_evidence, fields_filled,
fields_skipped, notes}`. `status: confirmed` with `confirmation_evidence`
(URL containing `/confirmation` and/or "thank you for applying" text)
means the application landed.

### Queue of applications

```bash
python3 batch_apply.py --queue urls.txt --port 9226
```

Fills each posting with `--no-submit`, asks for confirmation per submit,
handles the Greenhouse code gate per application, appends one JSON record
per application to `applications.jsonl`. Supports `--dedup` (skip URLs
already in the log) and `--resume-map` (per-posting resume selection).

### Greenhouse code gate (human-in-the-loop)

After submit, Greenhouse shows an 8-character email code. `code_gate.py`
handles the mechanics:

```bash
python3 code_gate.py --port 9226 --workdir /tmp/jobpilot/run1
```

It waits for the gate, prompts the operator for the code on stdin, enters
it, and verifies the confirmation page. Rules:

- STOP when the gate appears. Ask the user for the code from their email.
- Never guess, brute-force, or work around the gate.
- Codes are single-use and expire quickly. A failed entry means asking
  for a fresh code, not retrying the stale one.

## Safety invariants (never break)

1. NEVER invent applicant facts. No answer in `facts.json` means the
   field maps to `skip`. No exceptions, not even plausible ones.
2. NEVER commit `facts.json` or paste its contents into logs, issues,
   or transcripts beyond what the task needs.
3. Demographics: never infer race or gender. Decline when that option
   exists, otherwise skip.
4. Work authorization answers come from `facts.json` only. The model
   never decides these.
5. `status: blocked` means stop and report. Do not solve CAPTCHAs or
   evade bot checks.
6. One application per run. Do not parallelize submissions to the same
   employer.

## Failure modes

| Symptom | Likely cause |
|---|---|
| `fields_skipped` high | Form asks things outside `facts.json`; report the skipped labels |
| `status: failed` | Read `notes` in `result.json`; it names the step |
| Model returns invalid JSON | Model lacks strict schema support; switch models |
| Upload fails | Resume path missing on the browser host |
| Zero fields on Ashby | Board config breaks naive parsing; file an issue with the URL |

## Reference

- `AGENTS.md` - full agent runbook (setup, code gate protocol, layout)
- `docs/ats-notes.md` - per-ATS quirks from real runs
- `docs/facts-schema.md` - every `facts.json` key
- `docs/troubleshooting.md` - common failures
- `docs/agent-setup.md` - platform-specific setup (ChatGPT, Codex,
  Claude Code, Muse, Hermes)
