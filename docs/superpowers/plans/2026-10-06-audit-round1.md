# Round 1 audit implementation plan

Goal: fix S1 and the 21 major findings from docs/audits/REPORT-claude-2026-10-06-features.md in place.
Constraints: offline stdlib tests, Python 3.12, no commits, no em dash characters, preserve consent and unknown-fact skips.

1. Add regressions for code-gate confirmation, bounded failure retries, no-input and dry-run finalization. Persist attempt counts in the queue, coordinate human input in the lane, verify code confirmation before Applied, and guard every submit against applied history. Wire heartbeat at claim and submit boundaries.
2. Add watchdog regressions. Require explicit safe-retry adapter evidence, settle longer, reach JS only after a normal miss, and permit one successful recovery click.
3. Add location regressions. Parse city/state descriptors correctly, validate once at startup, and require adjacent city/state tokens in a non-negated segment.
4. In parallel, fix tracker/analytics statuses and update path, model budget call sites and cancellation, and wizard/gitignore/sweep persistence and locking. Each owner adds offline regressions before implementation.
5. Align queue, setup, tracking, README, AGENTS, SKILL and changelog documentation with final behavior. Leave unrelated minor findings deferred.
6. Review changed paths, run the full requested unittest suite, scan edited files for forbidden characters, and write prompts/round1-summary.txt with all dispositions and test count.

Review focus: confirmation lost after submit must block rather than retry; unavailable stdin must not requeue; retry counters must survive a fresh lane run; historical Applied must win over a pending queue entry; a later sweep must retain terminal queue statuses under its lock.
