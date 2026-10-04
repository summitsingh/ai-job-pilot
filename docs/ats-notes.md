# ATS quirks

Field notes from running jobpilot against real postings. Read this before
debugging a "the form looks fine but the run failed" situation.

## Greenhouse

- **Email verification code on every submit.** After clicking submit,
  Greenhouse shows an 8-character code gate; a code is emailed to the
  applicant address. Each application needs a FRESH code: one verification
  does not carry over to the next application, and codes expire quickly
  (tens of minutes). There is no resend UI; submitting again triggers a
  new code email. `code_gate.py` handles the whole flow: it waits for the
  gate, prompts the operator for the code, enters it, and verifies.
- **Confirmation.** A submitted application lands on a URL ending in
  `/confirmation` with text containing "thank you for applying". Check
  both; the URL alone is the stronger signal.
- **Form variance.** Fields differ per posting: some have GitHub,
  demographics, self-assessment, or signature fields, others do not.
  The harness fills what exists and skips the rest. Nothing is invented
  to fill a missing slot.
- **React selects.** Many dropdowns are react-select controls whose
  visible input is 2-4px wide. Clicking the input center is unreliable;
  click the control container's center instead
  (`common.container_click_js`).

## Ashby

- **Board API.** Ashby exposes a posting API that is useful for
  verifying a board is live and enumerating postings before ever
  opening a browser.
- **Config variance.** Some Ashby board configurations break naive
  schema parsing (custom field renderers, non-standard submit flows).
  When `schema_dump` returns zero fields on an Ashby form, the board
  config is the first suspect, not the harness. File the board URL
  and the raw HTML snippet in an issue.

## Lever

- **CAPTCHA on submit.** Lever presents an interactive CAPTCHA on
  essentially every submit. jobpilot detects challenge pages and
  stops with `status: blocked` instead of attempting a solve.
  Do not add CAPTCHA solving; that is an explicit non-goal.
- Otherwise Lever forms are the simplest of the three: standard
  inputs, predictable submit buttons.

## General

- **Readback diff.** After every fill, the harness reads each field
  back and diffs against the expected value. Trust a `filled-no-submit`
  result with zero mismatches; distrust anything else.
- **Phone fields.** Widgets reformat digits (`[PHONE-REDACTED]` vs
  `[PHONE-REDACTED]`). `hard_patterns.matches` compares digit-stripped, so
  readback verification tolerates reformatting.
- **Uploads.** The resume path in `facts.json` must exist ON THE
  BROWSER HOST (that is where CDP `setFileInputFiles` reads from),
  not just on the machine running the harness.
- **One model call per form.** Only `map.py` uses the model, with a
  strict JSON schema. If the model cannot do constrained JSON output,
  the map step produces garbage: switch models, do not loosen the
  schema. Qwen3-Coder via LM Studio is the validated default.
