# AGENTS.md: instructions for AI agents running jobpilot

This repo is a deterministic autofill harness for ATS job application forms
(Greenhouse, Ashby, Lever). An agent's job: take a posting URL and applicant
facts, fill the form truthfully, verify the fill, and submit. Deterministic
patterns fill first; the model is the backup.

Deterministic first: the pipeline routes field mapping through
`hard_patterns.py` / `templates.py` first, backed by 500+ real
submitted applications of proven field data. The local model (LM Studio /
Ollama, OpenAI-compatible) is the backup for fields the deterministic
pass leaves unmapped or marks low-confidence, via at most one
strict-JSON call per form in `map.py` (only if any field needs it),
which raises if the server is unreachable, so keep the model server up
before a run. Only one model needs to be loaded at a
time; the harness probes the server and prefers whichever chain model is
actually loaded. See `docs/multi-machine.md` for multi-machine
operation.

## The one command

```bash
python3 ats_fill.py --url <posting URL> --port 9226 --no-submit \
    --workdir /tmp/jobpilot/run1
```

Pipeline: `schema_dump` (read the form) -> `map` (deterministic
`hard_patterns` / `templates` first; one strict-JSON model call only for
unmapped or low-confidence fields) -> `fill` (CDP autofill + readback
diff) -> `verify`. Result goes to stdout and `<workdir>/result.json`:
`{status, confirmation_evidence, fields_filled, fields_skipped, notes}`.

## Setup (do this once)

1. `cp facts.example.json facts.json` and fill in the applicant's REAL
   details. Every answer the harness gives comes from this file.
   Never commit it (it is gitignored). If it is missing, stop and ask
   the user to create it.
2. Browser: Chrome with remote debugging on the fill port:
   ```bash
   google-chrome --remote-debugging-port=9226 --remote-allow-origins='*' \
     --no-first-run --no-default-browser-check
   ```
3. Model (backup only): an OpenAI-compatible endpoint with strict JSON
   schema support (`response_format: json_schema, strict: true`), used
   only for fields the deterministic pass leaves unmapped or marks
   low-confidence. LM Studio default `http://127.0.0.1:1234` works;
   Qwen3-Coder is the validated model family. Override with
   `JOBPILOT_MODEL_URL`. If the model cannot do constrained JSON, the
   unmapped fields are skipped rather than guessed; the map step will
   not produce garbage, it just maps less.
4. Transport: if Chrome runs on this same machine, set
   `JOBPILOT_CDP_URL="127.0.0.1:9226"` and skip SSH entirely. For a
   remote browser host, set `JOBPILOT_SSH_HOST="user@host"` instead.
5. The resume path in `facts.json` (`resume_path`) must exist ON THE
   BROWSER HOST, that is where the upload reads from.

## Running an application

1. Always run with `--no-submit` first. Inspect `result.json`:
   `fields_filled` vs `fields_skipped`. Skips are normal for questions
   with no truthful answer in facts.json.
2. Screenshots land in the workdir. Look at one before submitting.
3. Re-run without `--no-submit` to fill and submit. `ats_fill.py`
   respects `browser-<port>.lock` files (a lock fresher than 15 min
   blocks the run unless `--force`); do not run two fills on one port.
4. After submit, check `result.json`: `status` should be `confirmed`
   with `confirmation_evidence` (confirmation URL and/or "thank you for
   applying" text).

For first-run review with the Greenhouse lane, use
`python3 lane_greenhouse.py --workdir /tmp/jobpilot/lane1 --dry-run`.
It fills and checks one job, saves `dryrun.png` in the job workdir, prints
an answer table, and stops before submit with `dry_run: true`. Location,
validity, and consent blockers still apply; review the exact answers
before a real run.

For a queue of postings, use `batch_apply.py --queue urls.txt --port 9226`:
it fills each posting with `--no-submit`, asks you to confirm each
submit, handles the code gate per application, and appends one JSON
record per application to `applications.jsonl`.

## The Greenhouse code gate (human-in-the-loop, do not bypass)

Greenhouse shows an 8-character email verification code after submit is
clicked. `code_gate.py` automates the mechanics: it waits for the gate,
prompts the operator for the code on stdin, enters one character per
box, clicks submit, and verifies the confirmation page
(`code_gate.py --port 9226 --workdir /tmp/jobpilot/run1`).

When you see text like "verification code was sent to" with
empty code boxes:

1. STOP. Do not guess, brute-force, or work around the gate.
2. Ask the user for the code from their email (or run `code_gate.py`
   and let it prompt).
3. Enter it into the boxes, click submit, and verify the confirmation
   page: URL contains `/confirmation`, or text contains
   "successfully been received" / "thank you for applying".
4. Codes are single-use and expire quickly (under 40 minutes). If entry
   fails, ask for a fresh code rather than retrying a stale one. There
   is no resend control on the gate page; submitting the form again
   triggers a fresh code email.

## Safety invariants (never break these)

1. NEVER invent applicant facts. If a field has no answer in facts.json,
   it maps to `skip`. No exceptions, not even plausible ones.
2. NEVER commit `facts.json` or paste its contents into logs, issues,
   or chat transcripts beyond what the task needs.
3. Demographics: never infer race or gender from name, photo, or
   anything else. Decline when that option exists, otherwise skip.
4. Work authorization answers come from facts.json only. The model
   never decides these; `templates.py`/`hard_patterns.py` own them.
5. If `status` is `blocked` (CAPTCHA, blocklist, challenge page), stop
   and report. Do not attempt to solve or evade.
6. `modal_common.py` blocklists certain employers from automation.
   Respect it.
7. One application per run. Do not parallelize submissions to the same
   employer.
8. Consent fields are never auto-answered: arbitration agreements,
   certifications/attestations, background-check and drug-test
   authorizations, and assessments always stop the run for explicit
   human approval per application. The lane enforces this with an
   independent pre-fill scan (`consent_blockers` in
   `lane_greenhouse.py`), separate from whatever the mapper decided.

## Failure modes

- `fields_skipped` high: usually fine, the form asks things outside
  facts.json. Report the skipped labels so the user can extend facts.json.
- `status: failed`: read `notes` in result.json, it names the step.
- Model returns invalid JSON repeatedly: the model likely does not
  support strict schema mode. Switch models, do not loosen the schema.
- Upload fails: the resume path does not exist on the browser host.
  Copy it there first and confirm with a file-exists check.

## Layout

- `ats_fill.py`: orchestrator (dump -> map -> fill -> verify -> submit)
- `extract.py`, `schema_dump.py`: form introspection to JSON
- `map.py`: mapping orchestrator: deterministic `hard_patterns` /
  `templates` first, at most one strict-JSON model call, only for fields
  left unmapped or low-confidence
- `hard_patterns.py`, `templates.py`: deterministic screening answers;
  no model calls inside these modules; fields with no truthful answer
  map to `skip` (safety invariant 1)
- `fill.py`, `set_select.py`, `set_select2.py`, `modal_common.py`: fill
  primitives over CDP
- `ashby_graphql.py`: Ashby GraphQL mutation-template capture (fetch/XHR
  hooks + trigger snippet); direct-mutation filling for Ashby forms
- `verify.py`, `submit_only.py`: fill verification and submission
- `cdp_driver.py`, `cdp_direct.py`, `common.py`: transport (stdlib-only
  driver, no venv needed on the browser host)
- `model.py`: model client with fallback chain
- `indeed_dump.py`, `indeed_fill.py`: Indeed lane
- `launch_browsers.py`: headful Chrome launcher, one browser per lane
- `lane_greenhouse.py`: queue-driven Greenhouse lane (claim -> fill ->
  submit -> verify -> track)
- `job_queue.py`: shared queue with cross-lane claims
- `config.py`: config.json loading (env vars override file)
- `tracker.py`: application tracking (JSONL, Google Sheets, Notion)
- `sweep.py`: posting vetting funnel (title/location/salary/sponsorship
  filters, dedup, queue writing)
- `config.example.json`: env var template (copy to config.json)
- `test_operation.py`: offline tests for queue/tracker/sweep/launcher
- `facts.example.json`: template for the applicant's private facts
