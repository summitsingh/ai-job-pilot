# Audit Report: ai-job-pilot Micro-Commits 4263c54..d0a4da5

**Auditor:** Antigravity (Google DeepMind)  
**Date:** 2026-10-07  
**Repository:** `https://github.com/summitsingh/ai-job-pilot`  
**Working Directory:** `/home/summit/ai-job-pilot-audit`  
**Commit Range Audited:** `4263c54` through `d0a4da5` (11 commits on top of `95ba59f`)

---

## 1. Summary

This audit evaluates 11 micro-commits recently pushed to the public `ai-job-pilot` repository, which ported improvements from a private harness to the open-source codebase. The changes encompass template library expansions in `templates.py`, input typing mechanics in `hard_patterns.py`, override handlers in `map.py`, tab selection in `cdp_direct.py`, configuration additions in `facts.example.json`, documentation updates in `README.md` and `CHANGELOG.md`, and 10 new offline unit tests in `test_offline.py`.

### Overall Assessment
- **PII Leak Check:** **CLEAN.** No personal names, real email addresses, private phone numbers, physical addresses, actual employers, or private educational histories from the private harness leaked in the 11 commits. Placeholders in `facts.example.json` and tests remain generalized (`Alex Carter`, `Acme Corp`, `State University`).
- **Safety Invariants & Logic Correctness:** **CRITICAL RISKS IDENTIFIED.** Several ported patterns and guard bypasses violate core safety invariants:
  1. An attestation bypass in `templates.py` permits auto-acknowledging legally binding attestations because `attest` was omitted from `ACK_BAD_RE`.
  2. The guard exemption `AUTHORIZED_YES_GUARD_EXC` and pattern `workauth-authorized` bypass the guard for "without sponsorship" phrasings, causing candidates requiring visa sponsorship to falsely answer "Yes".
  3. `prev-employed` regex contains an overbroad `(at|for|with)` clause that matches standard technical skill questions (e.g., *"Have you worked with Python?"*), erroneously answering "No".
  4. Pattern shadowing causes `discipline` to be shadowed by a hardcoded `major` rule, and `inoffice-willingness` to be shadowed by `willing-onsite`, corrupting outputs and skipping free-text fields.
  5. The new `map.py` overrides (`apply_work_auth_select_overrides`, `apply_acknowledge_override`) are dead code on forms where templates match all candidate fields.
- **Test Suite:** **PARTIALLY TAUTOLOGICAL.** Multiple newly added tests assert unverified behaviors (e.g., verifying `action == "fill"` with empty string value, or matching a hardcoded shadowed pattern rather than the rule under test).

---

## 2. Findings

### Finding 1 [CRITICAL]: Legal-Weight Attestation Guard Bypass in Privacy-Ack Pattern
- **File & Line:** `templates.py:463-467, 862-864, 874-875`; `map.py:603-605`
- **Commit:** `431f171`
- **Description:**
  Invariant 8 and repository rules dictate: *"Consent fields are never auto-answered: arbitration agreements, certifications/attestations, background-check and drug-test authorizations, and assessments always stop the run for explicit human approval per application."*
  
  In `templates.py:866-876`, fields matching `GUARD_RE` are evaluated against `PRIVACY_ACK_GUARD_EXC` and `ACK_BAD_RE`:
  ```python
  if PRIVACY_ACK_GUARD_EXC.search(blob) and not ACK_BAD_RE.search(blob):
      continue  # handled by the privacy-ack-check pattern
  add(f["key"], "skip", "", guard=True)
  ```
  While `GUARD_RE` (`map.py:100`) strictly checks for `attest`, `ACK_BAD_RE` in `templates.py` omits `attest`:
  ```python
  ACK_BAD_RE = re.compile(r"arbitrat|background.?check|drug.?test|assessment|"
                          r"criminal|credit.?check|security.?clearance|"
                          r"true and correct|certif",
                          re.IGNORECASE)
  ```
  Consequently, a checkbox with text such as *"I attest that I have read the privacy notice"* triggers `GUARD_RE`, matches `PRIVACY_ACK_GUARD_EXC`, but because `attest` is missing from `ACK_BAD_RE`, `not ACK_BAD_RE.search(blob)` evaluates to `True`. The attestation is exempted from the guard and auto-clicked by `r_privacy_ack_check`.

  Furthermore, in `map.py:603`, `ACK_BAD_RE` is even weaker, omitting `attest`, `certif`, and `true and correct`:
  ```python
  ACK_BAD_RE = re.compile(r"arbitrat|background.?check|drug.?test|assessment|"
                          r"criminal|credit.?check|security.?clearance",
                          re.IGNORECASE)
  ```
- **Recommended Fix:**
  Add `attest` (and ensure `certif`, `true and correct`, `perjury`) is included in `ACK_BAD_RE` across both `templates.py` and `map.py`:
  ```python
  ACK_BAD_RE = re.compile(r"arbitrat|background.?check|drug.?test|assessment|"
                          r"criminal|credit.?check|security.?clearance|"
                          r"true and correct|certif|attest",
                          re.IGNORECASE)
  ```

---

### Finding 2 [CRITICAL]: "Without Sponsorship" Phrasings Bypass Guard and Auto-Answer "Yes"
- **File & Line:** `templates.py:582-586, 856-858, 872-873`
- **Commit:** `260cf4c`
- **Description:**
  The commit introduces `AUTHORIZED_YES_GUARD_EXC` and `workauth-authorized` to answer *"legally authorized to work"* questions with "Yes", with the stated goal: *"Keep guarding the 'without sponsorship' variant."*

  However:
  1. The regex has two branches:
     ```python
     AUTHORIZED_YES_GUARD_EXC = re.compile(
         r"legally authorized to work(?!.*without sponsorship)|have authorization to work",
         re.IGNORECASE)
     ```
     The second branch `have authorization to work` contains **no negative lookahead**. Any question phrased *"Do you have authorization to work without sponsorship?"* matches the second branch, bypasses the guard, matches `workauth-authorized`, and answers "Yes".
  2. The negative lookahead on the first branch is rigid: `(?!.*without sponsorship)`. Phrasings such as *"authorized to work without requiring sponsorship"*, *"authorized to work without company sponsorship"*, or *"authorized to work without employer assistance"* fail to match `without sponsorship` and therefore bypass the negative lookahead, incorrectly answering "Yes".
- **Recommended Fix:**
  Refactor `AUTHORIZED_YES_GUARD_EXC` and `workauth-authorized` to guard against any "without ... sponsor" clause:
  ```python
  AUTHORIZED_YES_GUARD_EXC = re.compile(
      r"(?:legally authorized to work|have authorization to work)(?!.*without\s+(?:requiring\s+|employer\s+|visa\s+)?sponsor)",
      re.IGNORECASE)
  ```
  Additionally, verify that if `"without"` and `"sponsor"` appear anywhere in the label, the question remains guarded.

---

### Finding 3 [HIGH]: Overbroad `prev-employed` Regex Falsely Matches Technical Experience Questions
- **File & Line:** `templates.py:491-494`
- **Commit:** `4263c54`
- **Description:**
  The `prev-employed` pattern uses the regex:
  ```python
  re.compile(r"have you (ever |previously )?(worked|been employed) (at|for|with)|"
             r"previously (worked|been employed)|worked here before", re.I)
  ```
  Because `(ever |previously )?` is optional and the preposition group includes `with`, any question asking about technical experience using the phrase "worked with" matches this pattern:
  - *"Have you worked with Python?"* -> **Matches**
  - *"Have you worked with AWS?"* -> **Matches**
  - *"Have you ever worked with Kubernetes?"* -> **Matches**

  Because `r_prev_employed` defaults to `facts.get("previously_employed_here") or "No"`, the harness answers **"No"** to these technical screening questions, falsely telling employers that the applicant has no experience with required technical skills.
- **Recommended Fix:**
  Restrict `with` to company/employer contexts:
  ```python
  re.compile(r"have you (ever |previously )?(worked|been employed) (at|for)\b|"
             r"have you worked with (us|this company)\b|"
             r"previously (worked|been employed)|worked here before", re.I)
  ```

---

### Finding 4 [HIGH]: `discipline` Pattern Shadowed by Pre-Existing `major` Pattern with Hardcoded Fallback
- **File & Line:** `templates.py:644-649, 324-329`
- **Commit:** `8ab2b1b`
- **Description:**
  Commit `8ab2b1b` added `r_discipline` to dynamically parse education degrees from `facts.json` and registered:
  ```python
  ("discipline", re.compile(r"\bdiscipline\b|field of study", re.I),
   r_discipline, {"select", "custom-select"})
  ```
  However, it was placed after `major`:
  ```python
  ("major", re.compile(r"\bmajor\b|field of study|discipline", re.I),
   r_major, {"text", "select"})
  ```
  Because `major` already matches `field of study|discipline` and accepts `select`, any dropdown question for "Field of study" or "Discipline" is intercepted by `major`.
  
  `r_major` has a hardcoded option list:
  ```python
  def r_major(f, facts, ats):
      if f.get("type") == "select":
          return ("opt", ["Computer Science", "Computer science", "CS", "Other"],
                  "education")
      return ("do", "fill", "Computer Science", "education")
  ```
  If an applicant's degree in `facts.json` is "BS Mathematics" or "BS Mechanical Engineering", the harness intercepts the field via `major` and selects **"Computer Science"**, directly violating Safety Invariant 1 (never invent applicant facts).
- **Recommended Fix:**
  Remove `field of study|discipline` from `major`, place `discipline` above `major`, and update `r_major` to defer to `r_discipline` logic rather than using hardcoded string literals.

---

### Finding 5 [HIGH]: `inoffice-willingness` Shadowed by `willing-onsite`, Skipping Text/Textarea Questions
- **File & Line:** `templates.py:673-677`
- **Commit:** `3885cbe`
- **Description:**
  `inoffice-willingness` was introduced to handle text, textarea, and select fields for in-office preferences:
  ```python
  ("willing-onsite", re.compile(r"on-?site|in.?office|in.?person|hybrid",
                                re.I), r_yes, None),
  ...
  ("inoffice-willingness", re.compile(r"in-office|in office|on-site|onsite|hybrid.*prefer|"
                                      r"work.*five days.*office|office.*five days", re.I),
   r_inoffice, {"text", "textarea", "select", "custom-select"}),
  ```
  `willing-onsite` has field type filter `None` (matches any element type) and precedes `inoffice-willingness`.
  When a form asks a free-text/textarea question such as *"Please describe your willingness to work in-office"*, `willing-onsite` matches first and returns directive `("opt", ["Yes"], "willingness-yes")`.
  
  In `opt_singleton`:
  ```python
  if ftype in ("select", "custom-select"): ...
  if ftype in ("checkbox", "radio"): ...
  return ("skip", "", "template-no-pattern")
  ```
  Directive `("opt", ...)` cannot resolve on a `text` or `textarea` element. As a result, the field is skipped as `template-no-pattern` instead of filled with `facts.get("willing_in_office")`.
- **Recommended Fix:**
  Constrain `willing-onsite` to `{"select", "custom-select", "radio", "yesno-button", "checkbox"}` or place `inoffice-willingness` ahead of `willing-onsite`.

---

### Finding 6 [HIGH]: `apply_work_auth_select_overrides` and `apply_acknowledge_override` Are Dead Code on Full-Template Forms
- **File & Line:** `map.py:445-446, 459-460`
- **Commit:** `bf05483`
- **Description:**
  In `map_fields` (`map.py:427-464`):
  ```python
  tmpl = map_template(schema, facts, ats)
  candidates = list(tmpl["unmapped"]) + [
      e["field"] for e in tmpl["map"]
      if e["action"] == "skip" and not e.get("guard")
      and e["field"] not in tmpl["unmapped"]]
  if not candidates:
      return tmpl
  ...
  mapped = apply_work_auth_select_overrides(mapped, schema)
  mapped = apply_acknowledge_override(mapped, schema)
  ```
  When deterministic templates match all fields on a form (or mark them with `guard="muse-review"`), `candidates` is empty. Line 446 returns `tmpl` immediately.
  
  Neither `apply_work_auth_select_overrides` nor `apply_acknowledge_override` is called in `templates.py:map_template`. Therefore, on forms that do not require model backup, these overrides never execute.
- **Recommended Fix:**
  Move the invocations of `apply_work_auth_select_overrides` and `apply_acknowledge_override` into `templates.py:map_template` (step 3), or execute them on `tmpl["map"]` before checking `if not candidates: return tmpl`.

---

### Finding 7 [MEDIUM]: `r_zip_code` Fills Empty String When Fact Is Absent; `facts.example.json` Missing Field
- **File & Line:** `templates.py:238-242`; `facts.example.json`; `test_offline.py:100-108`
- **Commit:** `4263c54`
- **Description:**
  `r_zip_code` is defined as:
  ```python
  def r_zip_code(f, facts, ats):
      return ("do", "fill",
              facts.get("zip_code") or facts.get("postal_code") or "",
              "identity")
  ```
  1. When neither `zip_code` nor `postal_code` is present in `facts`, it returns `("do", "fill", "", "identity")`. Filling an empty string into an ATS input triggers validation errors or submits empty data, violating Invariant 1 (*"If a field has no answer in facts.json, it maps to skip"*). Other resolvers (e.g. `_essay`, `r_edu_month`) return `None` so unprovided facts cleanly skip.
  2. `facts.example.json` was not updated to include `zip_code` or `postal_code`.
  3. `test_zip_code_from_facts` in `test_offline.py` passes only because it asserts `m["#z"]["value"] == ""` against the unpopulated `self.facts`, codifying an empty fill instead of testing truthful population.
- **Recommended Fix:**
  In `r_zip_code`:
  ```python
  def r_zip_code(f, facts, ats):
      val = facts.get("zip_code") or facts.get("postal_code")
      if not val:
          return None
      return ("do", "fill", str(val), "identity")
  ```
  Add `"postal_code": "78701"` to `facts.example.json`, and update the unit test to verify non-empty extraction.

---

### Finding 8 [MEDIUM]: Education Date Patterns Match Non-Education Date Fields
- **File & Line:** `templates.py:650-661`
- **Commit:** `8ab2b1b`
- **Description:**
  The education date regexes are:
  - `start.*month|month.*start`
  - `end.*month|month.*end`
  - `start.*year|year.*start`
  - `end.*year|year.*end`

  These patterns contain no education-specific scoping. When an ATS application includes employment history or availability fields (e.g., *"Current job start month"*, *"Start Year"*, *"Availability start month"*), these patterns intercept the fields and populate the applicant's college education dates.
- **Recommended Fix:**
  Require education keywords in the label blob or check field grouping:
  ```python
  re.compile(r"(?:edu|school|degree|college|university).*(?:start|end).*(?:month|year)|"
             r"(?:start|end).*(?:month|year).*(?:edu|school|degree)", re.I)
  ```

---

### Finding 9 [MEDIUM]: `apply_work_auth_select_overrides` Lacks Country-Specific Visa Safeguards
- **File & Line:** `map.py:570-580`
- **Commit:** `bf05483`
- **Description:**
  In `apply_work_auth_select_overrides`:
  ```python
  pick = None
  for o in opts:
      if o.strip().lower() == want_opt.lower():
          pick = o
          break
  if not pick:
      for o in opts:
          if o.strip().lower().startswith(want_opt.lower()):
              pick = o
              break
  ```
  If a dropdown presents options like `["Yes - UK Skilled Worker Visa", "No"]`, `o.strip().lower().startswith("yes")` is satisfied. Unlike `hard_patterns.py:pick_sponsorship_option`, this function does not check `is_country_visa_option(o)`, risking selecting country-specific visas.
- **Recommended Fix:**
  Import and verify `not is_country_visa_option(o)` prior to selecting a matching option.

---

### Finding 10 [LOW]: `JOBPILOT_TAB_ID` Implemented in `cdp_direct.py` but Omitted from `cdp_driver.py`
- **File & Line:** `cdp_direct.py:139-142`; `cdp_driver.py:126-132`
- **Commit:** `be5e401`
- **Description:**
  `be5e401` added support for preferring a specific browser tab via `JOBPILOT_TAB_ID` in `cdp_direct.py:page_ws()`. However, `cdp_driver.py` (used for remote browser sessions over SSH) was not updated. Multi-tab debugging via `cdp_driver.py` ignores `JOBPILOT_TAB_ID`.
- **Recommended Fix:**
  Mirror the `JOBPILOT_TAB_ID` check from `cdp_direct.py:page_ws()` into `cdp_driver.py:page_ws()`.

---

### Finding 11 [LOW]: `_edu_entry` Parameter `current=True` Unused
- **File & Line:** `templates.py:485-492`
- **Commit:** `8ab2b1b`
- **Description:**
  `_edu_entry(facts, current=False)` supports returning an in-progress degree if `current=True`. However, all callers (`r_edu_month`, `r_edu_year`) invoke `_edu_entry(facts)` without arguments, always returning `edu[0]`. If `edu[0]` is a completed undergraduate degree and `edu[1]` is an active graduate program, dates will consistently be drawn from the undergraduate degree.
- **Recommended Fix:**
  Inspect the question label (e.g. "most recent" vs "current") to pass `current=True` where appropriate, or sort entries chronologically by `start_year`/`end_year`.

---

## 3. PII Scan Result

A comprehensive audit was performed across:
- The git commit diffs (`git diff 95ba59f..HEAD`)
- All 11 commit log messages and author metadata
- New additions in `facts.example.json`, `templates.py`, `map.py`, and `test_offline.py`
- Historical commits in the repository

### Scan Findings
- **Commit Range 4263c54..HEAD:** **NO PII LEAKS.**
  - Mock identity data in `facts.example.json` strictly uses synthetic placeholders:
    - Name: `Alex Carter`
    - Email: `alex.carter@example.com`
    - Phone: `5551234567`
    - Location: `Austin, Texas, United States`
    - Employers: `Acme Corp`, `Startup Inc`
    - Schools: `State University`, `Tech Institute`
    - URLs: `https://github.com/alexcarter`, `https://www.linkedin.com/in/alexcarter`, `https://alexcarter.dev`
  - In `test_offline.py`, test data uses generic snippets (`"I build ML systems."`).
  - No personal addresses, phone numbers, school names, or employers from the maintainer or private harness were introduced in these commits.
- **Historical Reference:**
  An earlier phone number (`[REDACTED]`) referenced in historical audit documentation (`docs/audits/REPORT-claude-2026-10-05.md`) was previously sanitized from code in commit `fefa7e3` and does not exist in active source code.

---

## 4. Test Assessment

The 10 unit tests added in `test_offline.py` (`test_zip_code_from_facts` through `test_essay_answered_from_facts`) were examined for rigor, coverage, and fault detection:

| Test Name | Assessment | Defect / Limitation |
| :--- | :--- | :--- |
| `test_zip_code_from_facts` | **Flawed / Tautological** | `self.facts` lacks `zip_code` or `postal_code`. Test asserts `m["#z"]["value"] == ""` (asserts empty fill instead of value or skip). |
| `test_prev_employed_defaults_no` | **Incomplete** | Tests happy path only. Misses regression where technical questions ("worked with Python") falsely match. |
| `test_work_eligible_yes` | **Weak** | Asserts `action == "select"`, but fails to assert `value == "Yes"`. |
| `test_privacy_ack_select_answered` | **Weak** | Asserts `action == "select"`, but does not assert that the value is `"Acknowledge"`. |
| `test_privacy_ack_with_arbitration_stays_guarded` | **Good** | Accurately verifies that arbitration clauses remain guarded. However, does not test attestations (`attest`), which fail. |
| `test_edu_dates_from_facts` | **Weak** | Asserts actions (`select`, `fill`), but does not assert actual extracted date values (`September`, `2019`). |
| `test_discipline_from_facts` | **Masked Failure** | False positive. Matched pre-existing `major` rule with hardcoded `"Computer Science"` rather than the tested `discipline` rule. |
| `test_inoffice_willingness` | **Masked Failure** | False positive. Matched `willing-onsite` rather than `inoffice-willingness`. Does not test `textarea` fields, masking shadowing bug. |
| `test_essay_never_invented` | **Good** | Validates that unpopulated essay prompts result in `action: "skip"`. |
| `test_essay_answered_from_facts` | **Good** | Verifies extraction and filling of facts-provided essay text. |

### Critical Untested Paths:
1. `apply_work_auth_select_overrides` and `apply_acknowledge_override` in `map.py` have **0 unit tests**.
2. React-select type-to-filter in `hard_patterns.py` has **0 unit tests**.
3. Negative lookahead paths for `workauth-authorized` (verifying that "without sponsorship" remains guarded) have **0 unit tests**.
4. Country-specific visa exclusions in select dropdowns have **0 unit tests**.
