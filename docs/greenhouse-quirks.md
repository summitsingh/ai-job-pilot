# Greenhouse form quirks

Greenhouse application forms look straightforward but misbehave in
several consistent ways. Every one of these has been hit on real
production forms; guard against all of them.

## The schema lies about the cover letter

The form schema can mark the cover letter optional while the page's
JavaScript refuses to submit without one. If submit silently does
nothing and every field validates, check for a JS-enforced cover
letter. Attach a truthful one built only from the applicant's verified
facts; never invent experience to fill it.

## Location autocomplete picks the wrong city

The location combobox suggests cities as you type and can select the
wrong one (it has picked "Austintown, Ohio" for "Austin, Texas").
Always verify the location field visually (screenshot or readback)
before submitting. Never trust the autocomplete blindly.

## React textareas need special handling

React-controlled textareas do not register plain `.value = ...` sets.
The harness uses the native value setter plus `input`/`change` events
(see `batch_fill_js` in `fill.py`). If a textarea still does not stick,
focus the element and use `document.execCommand('insertText', false,
text)` instead, which goes through the browser's editing pipeline that
React tracks. Verify with a readback either way.

## React selects need the dedicated helper

Greenhouse dropdowns are react-select widgets, not native `<select>`s.
Detect them by the `.select__control` container and fill via
`fill_react_select` (container click, menu pick, self-verify). A plain
click-and-type will not stick. Note the historical bug this doc guards
against: the `do_select` router once compared the whole CDP response
dict to the string `"rs"`, so the react-select path was silently
bypassed (execution fell through to the generic click+type path, which
often failed to pick the option). Always normalize
`Runtime.evaluate` results to their `.value` before comparing (see
`_cdp_value` in `fill.py`).

## Confirmation detection: avoid false positives

A submit click is not a submission. Only treat a role as applied when
the confirmation page shows text like "successfully been received" /
"thank you for applying", or the URL contains `/confirmation`. Earlier
driver versions accepted weaker signals and logged phantom
submissions; require the strong signal.

## Privacy and GDPR acknowledgements

Required privacy-policy / GDPR disclosure checkboxes are consented as
part of applying (they are notices, not contracts). Keep them distinct
from guarded items: never consent to background checks, drug tests,
skills assessments, arbitration clauses, or "certify true and correct"
legal attestations without explicit applicant approval.

## Sponsorship phrasing variants

The sponsorship question appears in many wordings. Match broadly,
including: "Do you now or will you in the future require immigration
sponsorship", "will you require sponsorship", and "at any point in the
future require ... sponsor". The truthful answer for an H1B holder
needing a transfer is Yes; never describe the applicant as needing
initial sponsorship.

## Unfixable employer form bugs

Some forms are broken on the employer's side and cannot be completed
truthfully, no matter the tooling. Seen in the wild: sets of mutually
exclusive checkboxes each individually marked required (e.g. visa-status
or location checkbox groups where selecting one invalidates another, so
the form can never validate). Detect this pattern, log the role as
skipped with the reason, and move on. Do not fight the form.

## The per-application code gate

Greenhouse shows an 8-character email verification code after submit is
clicked. One code per application; codes expire quickly (under 40
minutes) and cannot be reused. Park at the code screen, accept a
user-supplied code once, type it, submit, verify the confirmation page.
Never store or reuse codes. See `docs/ats-notes.md` and AGENTS.md for
the full gate procedure.
