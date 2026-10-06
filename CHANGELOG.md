# Changelog

All notable changes to jobpilot are documented here. The format follows
[Keep a Changelog](https://keepachangelog.com/en/1.0.0/).

## [Unreleased]

### Fixed
- `fill.py` `do_select`: the react-select detector compared the whole
  CDP `Runtime.evaluate` response dict to the string `"rs"`, so
  `fill_react_select` was silently bypassed and execution fell through
  to the generic click+type+Enter path, which often failed to select
  the intended option. Added a `_cdp_value()` helper that normalizes
  both bare values and raw CDP RemoteObject dicts
  (`{"type": ..., "value": ...}`) before comparing, and applied it to
  `do_select`, the location-settle poll, and the three submit-button
  detectors that had the same latent dict-vs-string comparison. This
  was the root cause of the "dropdown won't fill" failures seen on
  Greenhouse react-selects (including school dropdowns).

### Changed
- Fill priority flipped to deterministic-first (was local-model-first):
  `hard_patterns.py` / `templates.py` answer every recognized field,
  backed by 500+ submitted applications of proven field data; the local
  model is now the backup for unmapped or low-confidence fields only.
  Model inference was the slowest step per application. Implemented in
  code: `map.py` `map_fields` now runs `templates.map_template` first
  and calls the model only for fields left unmapped or skipped without
  a guard marker (new `_map_with_model` helper; guard-skips are never
  sent to the model). The model server is probed only when the backup
  path is actually needed. Updated in `AGENTS.md` and
  `docs/multi-machine.md`.
- README: install via pip, batch mode, code-gate flow, and a "proven on"
  section with real verification numbers.

### Added
- `docs/greenhouse-quirks.md`: production Greenhouse form quirks —
  JS-enforced cover letter despite optional schema, location
  autocomplete picking the wrong city (always verify visually), React
  textarea `execCommand('insertText')`, react-select handling,
  confirmation detection requiring "successfully been received" or
  `/confirmation` URL, privacy/GDPR acknowledgement posture,
  sponsorship phrasing variants, unfixable employer form bugs, and the
  per-application code gate.
- `docs/multi-machine.md`: browser topology (one browser per worker,
  two app browsers per machine, third only for active fixes; all
  visible, never headless; CDP `setWindowBounds` tiling) and the
  Windows playbook (schtasks `/IT` visible launch, 8.3 short paths,
  `powershell -File`, Chrome 154 `lockfile` check).
- `docs/troubleshooting.md`: unfixable form bugs (mutually exclusive
  required checkboxes), the dropdown router bug note, and
  silent-submit causes.
- `docs/multi-machine.md`: multi-machine CDP operation guide. Visible
  debug Chromes with per-lane ports, default Chrome profile (temp profiles
  lose sessions), detached launches (schtasks on Windows, nohup on
  macOS/Linux), tab hygiene, the never-mix-headless-and-headful profile
  rule, one shared model server, and single-owner dedup across machines.
- Greenhouse code-gate handling pattern documented in `docs/ats-notes.md`:
  park at the code screen, accept a user-supplied code once, type it,
  submit, verify the confirmation page, never store or reuse codes. Codes
  expire in under 40 minutes; one code per application.
- ~~Local-models-first operation documented~~ (superseded by the
  deterministic-first change above; docs now describe templates-first
  with the model as backup).
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
