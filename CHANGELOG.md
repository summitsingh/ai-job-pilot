# Changelog

All notable changes to jobpilot are documented here. The format follows
[Keep a Changelog](https://keepachangelog.com/en/1.0.0/).

## [Unreleased]

### Added
- `batch_apply.py --dedup`: skip posting URLs already present in the
  applications log, so a queue can be re-run safely.
- `batch_apply.py --resume-map`: JSON file mapping URL substrings to resume
  paths; the matched resume overrides `facts.json` `resume_path` for that
  application only (never modifies your facts file).
- `JOBPILOT_RESUME` environment override, honored by `ats_fill.py`.
- Packaging (`pyproject.toml`): `pip install .` provides the
  `jobpilot`, `jobpilot-batch`, and `jobpilot-code-gate` console scripts.
- GitHub issue templates: bug report (with ATS type, schema snippet, logs)
  and new-ATS request.
- `examples/queue.txt` and `examples/resume-map.json`.
- Demo recording (`demo/demo.cast`, playable with `asciinema play`).

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
