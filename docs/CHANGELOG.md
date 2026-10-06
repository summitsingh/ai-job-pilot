# Changelog

## Unreleased - 2026-10-06: round 2 feature audit

- Resolve remaining MINOR findings: bounded watchdog timing, retained observation
  evidence, pre-click error comparison and shared detection patterns.
- Applicant location checks exclude employer questions, reject ambiguous English
  state tokens, and require explicit operator metro configuration.
- Queue timestamps and TTLs are bounded; default owners include hostname/process;
  CLI distinguishes missing live claims and wrong owners.
- Sweep keeps seniority and language symbols, normalizes Unicode and state names,
  buckets comparisons and saves withheld candidates for human review.
- Analytics deduplicates normalized URLs and reports missing input clearly.
  Scoreboard preserves modes, cleans failed writes and tolerates small clock skew.
- Setup dry-run reports overwrites and config persistence is atomic. Model telemetry
  rotates, wall budgets reject non-finite values and remote model names are quoted.
- Tracker identifiers and terminal output are validated; confirmation records use
  a matched phrase and URL. Docs clarify environment-only settings and gate roles.

## Unreleased - 2026-10-06: round 1 feature audit

### Fixed: round 1 feature audit
- Greenhouse human code coordinator is wired into the lane. Verified confirmation
  marks Applied and records Applied; failed/expired attempts alone can requeue for
  later runs, with 3 persisted attempts maximum. Missing stdin and ambiguous code
  confirmation stay blocked. Historical Applied prevents every submit/re-submit.
- Dry-run releases claims for all outcomes, with no tracker or terminal queue writes.
- Setup defaults to cwd, prints credential placeholders and alternate config export.
  Ignore rules cover local env, PII/state, locks, fingerprints and service-account keys.
- Submit watchdog reaches JS after a normal miss only when safe, allows one successful
  recovery click, waits 30 seconds by default, and blocks ambiguous/in-flight states.
- Sweep queue updates hold Store locks; persistent role fingerprints catch later reposts.
- Map, extract and verify use budgeted_chat with capped request timeouts and cooperative
  cancellation between retries/models. The lane calls heartbeat at claim/submit boundaries.
- Tracker supports response statuses, local update by URL/id, response_date and preserved
  furthest_stage. Analytics separates excluded rows. Published/short Sheets IDs fail closed.
- Location configuration validates before claims; city/state matching requires adjacency
  in one non-negated segment. SKILL safety rules include consent and employer blocklist.

See [the full project changelog](../CHANGELOG.md) for earlier releases.
