# Lessons from sequential application automation

This anonymized engineering note describes failure modes, corrective patterns, and remaining limitations observed in an operational deployment. It deliberately omits candidate identities, employers applied to, posting IDs, application outcomes, immigration details, personal answers, consent decisions, machine addresses, screenshots, and raw execution logs.

**Scope:** this change publishes documentation, not a synchronization of the private deployment's code or data. A fix described below must not be assumed present in this repository until its implementation and tests are checked. The operational policy changes described here are instructions, not independently enforced scheduler guarantees.

## 1. Read committed dropdown selections, not search text

**Finding:** a required React-select field may have an empty input value even after a valid answer is selected. Treating that search input as the answer produces false `unanswered-required` blockers.

**Correction observed:** the deployment's schema extractor reads `.select__single-value` within the input's nearest `.select__control`. This scopes the answer to the correct control rather than collecting unrelated page text. A reviewed live form subsequently reported no blockers or mismatches before a confirmed submission.

**Limits:** a generic fallback to combobox `innerText` is not sufficient proof of selection. It may include a placeholder or an expanded options list. Require provider-specific evidence of a committed answer, verify that it matches the intended option, and keep unknown controls blocked. A proposed fallback was not validated broadly enough to claim support for every provider.

Regression coverage should include a selected answer with an empty search input, an unanswered placeholder, an expanded menu with no selection, two adjacent controls, a cleared selection, and a mismatch between intended and committed options.

## 2. Full narrative verification must be independent of compact schemas

**Finding:** a compact schema truncated field values for readability. An additional narrative equality check consequently failed, and a shell command sequence continued toward submission despite that failure.

**Required corrective pattern:** keep presentation truncation separate from validation. Read the full textarea value directly from the live DOM and compare it with the exact approved payload. An assertion failure must prevent any submit command from executing.

Use checked subprocess calls or explicit exit-status branching. Do not rely on an unguarded sequence of shell commands. `set -e` is not a substitute for understanding shell conditional behavior. Persist a distinct full-text validation result and include it in the submission gate.

**Status:** the incident was recorded; later operational reports included full narrative verification. This documentation does not certify that every execution path has a tested hard gate. Add a regression that forces the full-text check to fail and asserts that the submit action is never invoked.

## 3. Separate application state from submission evidence

Use distinct states for discovered, eligible, filled, pending-input, pending-technical, blocked, and confirmed-submitted. Clicking a button is an attempt, not evidence that an application was received.

**Practices exercised in the deployment:** canonical job identities, pre-fill history checks, fail-closed form audit, precise confirmation text or URL capture, and record readback. Resume an existing pending record without appending a second record; never resubmit an already-confirmed application.

Canonicalization must handle tracking parameters and application-path suffixes without collapsing different jobs. A generic job-board URL is not a unique job identifier. Preserve historical evidence when conflicting or duplicate records need reconciliation rather than deleting rows blindly.

## 4. Explicit authorization must reach every layer

**Finding:** approvals recorded in the answer store were contradicted by older scheduler instructions that prohibited those same answers. Repeated runs kept reporting already-resolved consent blockers.

**Correction observed:** the persisted job instructions were revised to recognize narrowly scoped documented approvals and were read back after editing.

A narrative drafting permission is not blanket legal consent. Store approval scope and provenance privately; distinguish the same agreement from materially different terms. Unsupported or unanswered guarded fields remain blockers. Do not weaken the audit by treating a guarded field as satisfied just because filling it was intentionally skipped.

Disclose actual AI assistance truthfully where asked. A stale generic answer must not override what the workflow actually did.

## 5. Missing personal information should defer one posting, not stop the pipeline

Log the exact missing questions and verified form state privately, leave the application unsubmitted, and continue with other eligible postings. Consolidate unresolved questions for a later review rather than repeatedly interrupting the operator.

Resume a pending posting only when its specific missing facts or approvals are documented. Employer-specific preferences and factual claims cannot be extrapolated to unrelated questions. Draft ordinary motivation answers from documented experience, but never invent metrics, employers, technical specialization, or interview history.

## 6. Diversify employers before filling forms

**Finding:** resuming one employer's pending queue before discovering new roles caused application activity to concentrate on that employer.

**Policy correction recorded:** remove employer-specific priority, rotate employers, avoid consecutive submissions to the same employer, and impose a configurable rolling-window cap. Count confirmed submissions using reliable timestamps and conservatively normalize aliases. If timestamp precision is incomplete, do not report an exact rolling count.

An over-cap posting should be deferred, not falsely marked permanently ineligible. Diversification must not relax role fit, compensation criteria, or verified sponsorship requirements. These policies should eventually be enforced in queue code with deterministic tests; prompt-level instructions alone are not proof of enforcement.

## 7. Fail before execution when a model is unavailable

**Finding:** a delegated debugging run failed immediately because its configured local model identifier did not match a loaded model. An application run also reported an unsupported-model response; later successful runs do not establish the cause of that earlier failure.

Query the configured provider's model inventory, verify the exact identifier and endpoint, then execute a minimal completion or tool-call probe. Local inventory availability does not prove tool compatibility. Keep global defaults, per-job overrides, and delegation overrides distinguishable.

Report model failures as execution failures, not successful empty results. Preserve the requested task and retry with a verified supported configuration; do not claim debugging occurred when the model failed before work started.

## 8. Scratch files and concurrency are correctness concerns

A reused filename caused a mutation safeguard to refuse an overwrite. Use fresh run- and candidate-specific scratch directories, read existing files before overwriting, and use targeted patches for small changes. A refused write is a failure to investigate, not a guard to disable.

Sequential scheduling should honor the active-run lock. Do not start a parallel browser run when a scheduled application is already executing. An interval is not a per-run timeout; a request to change one should not silently change the other.

## Verification boundaries

An independently rerun local regression subset passed eleven tests covering form audit, job identity, queue filtering, and logger deduplication. That evidence supports those tested paths only: it does not establish broad provider compatibility, test the later combobox fallback, or validate all command-sequence failure paths.

A successful scheduler wrapper, a saved file, or a passing unit suite is not an end-to-end application success. Report separately:

- configuration persisted and read back;
- regression tests executed and their actual results;
- live values and attachments reviewed;
- submission attempted;
- confirmation captured;
- exact records updated and read back.

## Privacy-safe publication checklist

- Write a clean allowlisted document; do not bulk-copy a working directory.
- Exclude answer stores, resumes, private consent records, application logs, browser profiles, cookies, raw HTML, screenshots, terminal transcripts, and credential files.
- Scan new content and the staged diff for known personal tokens, contact details, home paths, private IPs, and secrets.
- Check commit author metadata: prefer the repository account's noreply address over a private email.
- Use a dedicated documentation branch and reviewable pull request; do not automatically merge unrelated changes.
- Read the published files and pull request back from the remote before reporting success.
- Synthetic examples must be clearly labeled synthetic; never present them as captured operational evidence.
