# Audit round 2 implementation plan

Goal: resolve MINOR 1 through 39 from the feature audit in place.
Spec: docs/audits/REPORT-claude-2026-10-06-features.md and the user's explicit corrections.
Constraints: Python 3.12, stdlib, offline tests, no commits, no U+2014, preserve round 1 consent and submission gates.

- [x] Watchdog, location and lane: reproduce observation failures, unbounded timing, static errors, ambiguous state tokens, employer location false blocks and confirmation PII; add test_reliability regressions; fix with bounded settings, pre-click baseline and narrow field detection.
- [x] Queue and sweep: add test_operation regressions for bounded claims, owner CLI outcomes, unique default lanes and normalized bucketed role review; implement and update queue/sweep docs.
- [x] Wizard and model: add test_human regressions for dry-run overwrite, atomic persistence, telemetry rotation and finite budgets; implement shell quoting and accurate setup/config documentation.
- [x] Analytics, scoreboard and tracker: add test_tracking regressions for atomic cleanup/modes, timestamp tolerance, URL deduplication, parser validation and terminal sanitization; document Notion limits.
- [x] Documentation and integration: verify existing MINOR 13/36/39 fixes, correct skill/README/layout, package terminal sanitizer, add changelog and a per-finding summary.
- [x] Verify the prescribed five-module offline suite and the exact repo-wide punctuation scan; report final count and any deferred findings.

Review focus: errors after a click must block without retry; malformed claims must expire safely; seniority and distinct places must remain distinct; failed setup must preserve existing config; tracker confirmation must exclude raw page text.
