# Codex audit — 2026-10-07

Auditor: Codex CLI (gpt-6.1-sol, medium reasoning), read-only sandbox.
Scope: the 11 micro-commits ported from the private harness (4263c54 through d0a4da5, on top of 95ba59f), inspected against CHANGELOG claims in the working tree at `/home/hatch/workspace/jobpilot-staging` (no `.git` metadata available).

# Summary

**Verdict: changes need correction before use.** The current code violates the stated facts, consent, and sponsorship invariants. A likely real phone number also remains in an older public audit document.

The checkout contains **no `.git` metadata**. Consequently, I could inspect the current implementation against the CHANGELOG claims, but could not verify the individual messages or attribute defects conclusively to commits `4263c54` through `d0a4da5` versus base `95ba59f`.

Verification used source inspection, synthetic mapping cases, mocked integration calls, and an independent code review. No live applications, model calls, or browser interactions were performed.

- All ten identified added tests pass.
- Full `test_offline`: **34 tests, 32 passed, 2 environment errors** because temporary files cannot be created.
- A separate run of the 31 tests outside `TestDedup` passed.
- Education dates and willingness behavior do not consistently implement the documented facts-driven behavior.
- `JOBPILOT_TAB_ID` correctly prefers a matching page, but silently falls back when it is absent.

# Findings

### 1. HIGH — A public audit document preserves a likely real phone number

**Location:** `docs/audits/REPORT-claude-2026-10-05.md:11`, also lines 12, 31, and 72.

The older report reproduces the phone number it identifies as personal data, including a searchable digits-only representation. Updating operational examples did not remove this disclosure from the public snapshot.

**Recommended fix:** Redact the number everywhere, including audit documents. Check public Git history and published artifacts separately. Do not reproduce the full number in remediation reports.

### 2. HIGH — The acknowledgement override auto-answers certifications and attestations

**Location:** `map.py:603`, particularly lines 618–622.

`apply_acknowledge_override` requires only the option text `Acknowledge/Confirm`. It does not require privacy wording, and its denylist omits `certif`, `attest`, and `true and correct`. It removes an existing guard.

An integrated `map_fields` reproduction used:

```text
Label: I certify that all information provided is true and correct
Option: Acknowledge/Confirm
```

With an unrelated unmapped field activating backup, a mocked backup returning only skips still resulted in `action: click` on the certification.

The override also ignores `checked`, so it can restore a click on an already checked checkbox after earlier normalization.

**Recommended fix:** Require an explicitly recognized privacy acknowledgement, reject all consent/attestation language across the complete field context, and preserve checked-state handling. Explicit human approval must remain mandatory for legal consent.

### 3. HIGH — Privacy acknowledgement exemptions allow attestations

**Location:** `templates.py:464`, with guard exemption at line 874.

The template's `ACK_BAD_RE` includes certification but omits attestation. Consequently:

```text
I acknowledge the privacy policy and attest that my application
information is accurate
```

maps to `click` even with empty facts. `GUARD_RE` recognizes `attest`, but the privacy exemption bypasses that protection.

**Recommended fix:** Share one complete consent denylist between templates and overrides. Legal language must take precedence over privacy exceptions.

### 4. HIGH — Work-authorization exceptions bypass unrelated legal consent

**Location:** `templates.py:872`; `map.py:565`.

The authorization exemption skips the entire guard without checking whether the question also includes background-check, arbitration, or other consent language. The new select override likewise removes guards based solely on a work-authorization match.

Both these synthetic questions mapped to `Yes`:

```text
Are you legally authorized to work and do you consent to a background check?
Are you legally authorized to work and agree to arbitration?
```

**Recommended fix:** Apply legal-consent checks before authorization exceptions and again before overrides remove guards. Combined questions must stop for human approval.

### 5. HIGH — New affirmative answers ignore applicant facts

**Location:** `templates.py:250`, lines 454–457, 674, and 713–715; `map.py:554`.

`r_work_eligible` and `r_authorized_yes` unconditionally return `Yes`. The select override accepts no facts argument and reuses hardcoded affirmative answers.

Reproduced results include:

| Input | Actual answer |
|---|---|
| No work-authorization fact | Legally eligible/authorized → Yes |
| Explicitly not authorized | Legally authorized → Yes |
| Country Canada, location Toronto | Currently live in US → Yes |
| No commute willingness fact | Willing to commute → Yes |
| `willing_in_office: No` | Willing to work in-office → Yes |

**Recommended fix:** Require explicit relevant facts. Return skip for missing, ambiguous, or country-inapplicable authorization information. Derive sponsorship and willingness independently rather than defaulting to affirmative answers.

### 6. HIGH — "Without requiring sponsorship" escapes the exclusion

**Location:** `templates.py:709`, also line 857.

The new pattern and guard exemption exclude only literal `without sponsorship`. This question maps to `Yes`:

```text
Are you legally authorized to work without requiring sponsorship?
```

Existing `map.work_auth_override` explicitly refuses that wording at lines 130–131, so the new template weakens an existing safeguard.

**Recommended fix:** Centralize sponsorship exclusion logic and cover equivalent formulations. Skip unless the complete proposition is supported by facts.

### 7. HIGH — Country-specific visa options can still be selected

**Location:** `map.py:577`; `hard_patterns.py:62`, lines 85–108 and 317–323.

The new select override accepts any option starting with `Yes`. It therefore selects `Yes, Germany visa` without consulting the visa filter.

Additional weaknesses:

- `pick_sponsorship_option` has no production callers; its tests do not establish live-path protection.
- Its denylist misses options such as `Yes, I will require sponsorship: Brazil visa`, `Yes, Germany EU Blue Card`, and `Yes, I will require sponsorship: H-1B`. All were accepted in reproductions.
- The fill guard infers question meaning from the selector and requested value, not the actual question label. An opaque selector plus requested value `Yes` may not activate it.
- The fill guard runs after selection.

**Recommended fix:** Carry explicit sponsorship-question metadata into filling. Use one conservative selector across native selects, custom selects, and option groups. Reject visa-specific answers before interacting with the form; skip when only such options exist.

### 8. HIGH — Education dates are shadowed, inconsistent, and applied to employment fields

**Location:** `templates.py:485`, lines 646 and 650–660.

The new date implementation does not consistently use the intended education entry:

- `End Year` matches the earlier `gradyear` pattern and fills hardcoded `2021`, despite example facts containing `2019`.
- `_edu_entry` returns the first entry, while the existing generic school resolver prefers the last entry. The example can therefore pair Tech Institute with State University's dates.
- `Employment start month` matches the education pattern and receives `September` from education facts.
- Year patterns exclude select/custom-select controls.

**Recommended fix:** Bind school, degree, discipline, and dates to the same identified education entry and form section. Require education context for generic date labels. Remove the hardcoded graduation-year path and support year dropdowns.

### 9. MEDIUM — Existing patterns prevent new discipline and in-office handlers from working

**Location:** `templates.py:644`, lines 648 and 671–677.

`major` precedes `discipline`; `willing-onsite` precedes `inoffice-willingness`.

Observed consequences:

- The new discipline test exercises the existing major handler.
- Custom-select discipline receives `Other` for degree `BS Computer Science`, despite an available `Computer Science` option.
- In-office text/textarea questions match the earlier option handler and skip rather than reaching the new facts-driven text handler.
- In-office dropdowns receive hardcoded `Yes` even when facts say `No`.

**Recommended fix:** Consolidate overlapping handlers, make type/context restrictions explicit, and use structured discipline facts rather than whole degree strings.

### 10. MEDIUM — Privacy Yes/No buttons receive two clicks

**Location:** `templates.py:477`, pattern types at line 723.

`r_privacy_ack_check` returns unconditional `do/click` for checkboxes, radios, and yesno-buttons. A Yes/No pair sharing the question `I acknowledge the privacy notice` receives clicks on **both Yes and No**.

Radio deduplication does not cover yesno-buttons. The final response can therefore become No or remain inconsistent.

**Recommended fix:** Resolve radio and yesno-button acknowledgements as a group with exactly one affirmative selection. Restrict unconditional checking to genuine singleton checkboxes.

### 11. MEDIUM — Type-to-filter cannot reach search-only option lists

**Location:** `hard_patterns.py:265`, with `_open_menu` at line 204.

The code calls `_open_menu` before typing. `_open_menu` requires a visible option to declare success. Search-only lists that initially show no options therefore time out before the new `ftype` call runs.

The implementation also discards the typing response and suppresses exceptions. `Input.insertText` does not itself guarantee replacement of an existing search query.

**Recommended fix:** Detect an open/focused searchable control independently of visible options, clear its query, check the trusted typing result, then wait for filtered options. Add coverage for initially empty and repeated searches.

### 12. MEDIUM — Missing ZIP facts produce a fill action instead of skip

**Location:** `templates.py:238`; `test_offline.py:100`.

The example facts contain neither `zip_code` nor `postal_code`. The handler returns `fill` with an empty value. Its new test explicitly accepts this behavior rather than verifying the missing-fact invariant.

**Recommended fix:** Return skip for missing ZIP/postal facts. Test a supplied value and an absent value separately.

### 13. MEDIUM — Essay patterns can miss intended questions or fill unrelated ones

**Location:** `templates.py:633`, lines 757–765.

The earlier generic `experience.*with` skip shadows common developer-tools essay wording. For example, a supplied developer-tools essay does not fill `Do you have experience with developer tools building SDKs?`.

Conversely, the LLM regex matches any occurrence of `llm`, including non-essay questions. `Which LLM model do you use?` receives the entire supplied experience essay.

**Recommended fix:** Require question intent as well as topic. Restrict the technical-experience skip to questions it actually handles, and add positive/negative tests for all three essay categories.

### 14. LOW — Missing preferred tab silently routes to another page

**Location:** `cdp_direct.py:139`.

The implementation correctly honors the documented *preference*. However, a stale `JOBPILOT_TAB_ID` silently routes commands to the first other page. A mocked target list confirmed this fallback.

`reset` also closes all page tabs and invalidates an existing preference.

**Recommended fix:** Document these semantics. If operators use the variable for isolation, fail when the requested tab is absent and explicitly define reset behavior. Test matching, absent, and non-page targets.

# PII scan result

**Result: FAIL for the current snapshot; commit-specific attribution unavailable.**

The scan covered source, documentation, examples, audit documents, prompt logs, the demo, and readable members of both distribution archives.

- A likely real phone number remains in `docs/audits/REPORT-claude-2026-10-05.md`, as identified above.
- Real author attribution appears in `README.md:258`, `LICENSE:3`, `pyproject.toml:12`, and distribution metadata. This appears intentional publication metadata, but is personal data under the requested broad scan.
- Example identities, email addresses, phones, employers, and schools are recognizable placeholders. However, `github.com/alexcarter`, `linkedin.com/in/alexcarter`, and `alexcarter.dev` are live-looking destinations rather than reserved example domains; they were not verified as fictional.
- Private-harness context remains in comments and prompts: an xAI-specific referral policy, "his Oct 1 posture," stated self-assessment assumptions, and machine-specific examples. These are generalization gaps, although they do not establish another private applicant identity.
- No `facts.json` was present. No additional private applicant email, employer, or school was confirmed.
- No matching private phone was found in the two distribution archives.

A clean public-history result cannot be asserted without Git metadata and the private reference dataset.

# Test assessment

The ten added tests exercise real functions, but several check only action types and pass despite the defects above.

| Added test | Assessment |
|---|---|
| ZIP from facts | Incorrect fixture: no ZIP fact; accepts empty fill. |
| Previous employment defaults No | Facts already specify No, so it does not test the claimed default or absent facts. |
| Work eligible Yes | Checks select action only; no contrary/missing authorization case. |
| Privacy acknowledgement select | Checks action only; no selected-value assertion. |
| Privacy acknowledgement with arbitration | Useful basic guard test, but label contains no privacy wording, so it does not challenge the new exemption. |
| Education dates | Checks actions only; passes while End Year is fabricated as 2021. |
| Discipline | Checks action only and exercises an existing handler. |
| In-office/commute | Checks actions only; misses contrary willingness and handler shadowing. |
| Missing essay | Meaningful template-level skip assertion. |
| Supplied essay | Meaningful exact-value assertion. |

**Missing safety-critical coverage:** certifications and attestations through `map_fields`; mixed authorization/consent questions; sponsorship exclusion variants; missing and contrary facts; visa-option rejection in production paths; privacy button groups; override behavior with and without backup; education-entry consistency; search-only React controls; and tab selection.

`CHANGELOG.md:24` correctly describes the current count of 34 offline tests. Passing these tests does **not** substantiate its legal-language refusal or facts-driven education claims. The README's broader 234-test claim was not independently verified in this read-only environment.
