# Contributing to jobpilot

## Ground rules

1. **Never invent applicant facts.** The harness answers only from
   `facts.json`. A new pattern that guesses, defaults, or infers a
   personal detail will be rejected.
2. **Never commit `facts.json`.** It holds real personal data. Use
   `facts.example.json` for tests and examples. CI fails the build if
   `facts.json` is tracked.
3. **Keep the driver stdlib-only.** `cdp_driver.py` runs on the browser
   host with no venv. No third-party imports there, ever.
4. **Deterministic first.** New screening answers go in
   `hard_patterns.py` / `templates.py` as pattern matchers, not as
   model prompts. The model gets one strict-JSON call per form in
   `map.py`; that budget does not grow.

## Workflow

- Fork, branch, pull request against `main`.
- `python3 -m py_compile *.py` must pass.
- `python3 -m unittest test_offline -v` must pass (11 tests, no
  browser/model/network needed).
- Live-browser tests (`test_*.py` except `test_offline.py`) need a
  debug Chrome and real posting URLs; run the ones your change
  touches with `--no-submit` (they never submit).
- Document ATS quirks you discover in `docs/ats-notes.md`.

## What makes a good pattern

- Anchored to the field label with a case-insensitive regex.
- Returns one of the documented action shapes: `("do", action, value,
  note)` or `("opt", candidates, note)`.
- Unknown fields fall through to `("do", "skip", "", "template-no-pattern")`.
- Add a case to `test_offline.py` covering the new pattern with a
  synthetic field.
