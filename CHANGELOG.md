# Changelog

All notable changes to jobpilot are documented here. The format follows
[Keep a Changelog](https://keepachangelog.com/en/1.0.0/).

## [Unreleased]

### Added
- `docs/multi-machine.md`: multi-machine CDP operation guide. Visible
  debug Chromes with per-lane ports, default Chrome profile (temp profiles
  lose sessions), detached launches (schtasks on Windows, nohup on
  macOS/Linux), tab hygiene, the never-mix-headless-and-headful profile
  rule, one shared model server, and single-owner dedup across machines.
- Greenhouse code-gate handling pattern documented in `docs/ats-notes.md`:
  park at the code screen, accept a user-supplied code once, type it,
  submit, verify the confirmation page, never store or reuse codes. Codes
  expire in under 40 minutes; one code per application.
- Local-models-first operation documented: the pipeline makes one
  strict-JSON local-model call per form (`map.py`, which raises if the
  server is unreachable, so keep it up); deterministic
  `hard_patterns.py` / `templates.py` are the separate offline template
  path. Documented in `docs/multi-machine.md` and AGENTS.md.
- Troubleshooting: "profile in use" launch conflict and one-model-at-a-time
  server guidance.
- `batch_apply.py --dedup`: skip posting URLs already present in the
  applications log, so a queue can be re-run safely.
- `batch_apply.py --resume-map`: JSON file mapping URL substrings to resume
  paths; the matched resume overrides `facts.json` `resume_path` for that
  application only (never modifies your facts file).
- `JOBPILOT_RESUME` environment override, honored by `ats_fill.py`.
- Packaging (`pyproject.toml`): `pip install .` provides the
  `ai-job-pilot`, `ai-job-pilot-batch`, and `ai-job-pilot-code-gate` console scripts.
- GitHub issue templates: bug report (with ATS type, schema snippet, logs)
  and new-ATS request.
- `examples/queue.txt` and `examples/resume-map.json`.
- Demo recording (`demo/demo.cast`, playable with `asciinema play`).
- `ashby_graphql.py`: Ashby GraphQL form-template capture technique. Hooks
  both `fetch` and `XMLHttpRequest` to watch outbound GraphQL requests and
  stash the form-value mutation template (flexible operation-name match, not
  hardcoded), plus a parameterized trigger snippet that forces Ashby to emit
  a mutation. Direct-mutation filling is more reliable than DOM events, which
  Ashby can silently discard server-side.

### Changed
- README: install via pip, batch mode, code-gate flow, and a "proven on"
  section with real verification numbers.

## [0.1.0] - 2026-10-04

### Added
- Initial public release: deterministic ATS form filler for Greenhouse,
  Ashby, Lever, and an Indeed lane. Stdlib-only CDP browser driver.
- `code_gate.py`: detects the Greenhouse 8-character email verification
  gate, prompts for the code on stdin, enters one character per box,
  submits, and verifies the confirmation page. Codes stay
  human-in-the-loop; never guessed or bypassed.
- `batch_apply.py`: queue file of posting URLs, sequential fill with
  `--no-submit`, per-submit confirmation, code-gate handling, JSONL log.
- `test_offline.py`: 11 unit tests over the deterministic core (identity
  fill, never-guess skips, truthful sponsorship answers, facts-driven
  schools, fuzzy matcher). No browser, model, or network required.
- GitHub Actions CI: syntax check, offline tests, and a guard that fails
  the build if `facts.json` is ever committed.
- `docs/ats-notes.md`: Greenhouse per-application codes, Ashby config
  parsing quirks, Lever CAPTCHA behavior, phone formatting, upload paths,
  readback checks, model constraints.
- `CONTRIBUTING.md`: never invent facts, never commit `facts.json`,
  stdlib-only driver, deterministic-first, offline tests for new patterns.
- `AGENTS.md`: agent runbook (setup, code-gate protocol, safety invariants).
- `facts.example.json`: placeholder applicant profile; real `facts.json`
  is gitignored.
