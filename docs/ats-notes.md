# ATS quirks

Field notes from running jobpilot against real postings. Read this before
debugging a "the form looks fine but the run failed" situation.

## Greenhouse

- **Email verification code on every submit.** After clicking submit,
  Greenhouse shows an 8-character code gate; a code is emailed to the
  applicant address. Each application needs a FRESH code: one verification
  does not carry over to the next application, and codes expire quickly
  (under 40 minutes in practice). There is no resend UI; submitting again
  triggers a new code email. `code_gate.py` handles the whole flow: it waits
  for the gate, prompts the operator for the code, enters it, and verifies.
- **Code-gate handling pattern.** Park the browser at the code screen and
  stop. Accept a user-supplied code exactly once, type it into the boxes
  (one character per box), click submit, and verify the confirmation page.
  Never guess, brute-force, or work around the gate. Never store or reuse
  codes: a code that fails once is stale, ask for a fresh one.
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
- **DOM fills can be silently discarded.** Ashby forms submit field values
  through a GraphQL mutation (operation names like `ApiSetFormValue`,
  though the exact name drifts). Driving the form through DOM input
  events alone is unreliable: the client shows no error but the server
  drops the values. The robust path is `ashby_graphql.py`: install the
  fetch/XHR hook, trigger one real mutation to capture the template
  (query text plus organization/render/definition identifiers), then
  replay the mutation directly for every field. Always verify by
  reading the values back server-side; a 200 response alone does not
  prove the values stuck. The hook must cover both `fetch` and
  `XMLHttpRequest`: Ashby has been observed switching transports, and a
  fetch-only hook silently misses everything in that case.

## Lever

- **CAPTCHA on submit.** Lever presents an interactive CAPTCHA on
  essentially every submit. jobpilot detects challenge pages and
  stops with `status: blocked` instead of attempting a solve.
  Do not add CAPTCHA solving; that is an explicit non-goal.
- Otherwise Lever forms are the simplest of the three: standard
  inputs, predictable submit buttons.

## Workable

- **URL pattern.** Postings live at `apply.workable.com/<company>/j/<shortcode>`;
  the application form is at `apply.workable.com/<company>/j/<shortcode>/apply`.
  Note: shortcodes go stale fast when postings close; always pull current
  URLs from the company's live Workable board, not from search results.
- **No account needed.** Workable application forms are plain web forms
  like Greenhouse/Ashby/Lever. The standard pipeline
  (`schema_dump` -> `map` -> `fill` -> `verify`) works without changes.
- **Verified 2026-10-04.** Ran against a live NALA Senior Platform Engineer
  posting: 13 fields extracted, 9 fill actions attempted, 7 verified with
  zero mismatches on name, email, phone, address, salary range, and notice
  period. The 2 failures were city/country sub-fields of an address
  autocomplete widget that clears on blur (same pattern as Greenhouse
  location fields), not a Workable-specific blocker. No Workable-specific
  code was needed. Tested with `--no-submit`; no application was filed.
- Pass `--ats workable` to `ats_fill.py` (currently a hint; the fill
  logic is ATS-agnostic).

## Explicitly out of scope

- **iCIMS, Taleo, Workday, SuccessFactors.** These require creating an
  account with a password before you can apply, and use multi-page
  flows (iCIMS uses nested iframes). Automated account creation is an
  explicit non-goal: it creates credentials the user must manage and
  violates the "no invented facts" rule at the identity layer. If you
  need these, apply manually.
- **LinkedIn Easy Apply.** Excluded deliberately (account automation
  risk). See `CONTRIBUTING.md`.

## General

- **Readback diff.** After every fill, the harness reads each field
  back and diffs against the expected value. Trust a `filled-no-submit`
  result with zero mismatches; distrust anything else.
- **Phone fields.** Widgets reformat digits (`(555) 123-4567` vs
  `5551234567`). `hard_patterns.matches` compares digit-stripped, so
  readback verification tolerates reformatting.
- **Uploads.** The resume path in `facts.json` must exist ON THE
  BROWSER HOST (that is where CDP `setFileInputFiles` reads from),
  not just on the machine running the harness.
- **One model call per form.** Only `map.py` uses the model, with a
  strict JSON schema. If the model cannot do constrained JSON output,
  the map step produces garbage: switch models, do not loosen the
  schema. Qwen3-Coder via LM Studio is the validated default.
