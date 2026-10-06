# Changelog

## Unreleased - 2026-10-06: template library expansion (private harness sync)

- templates.py: new deterministic screening patterns ported from production
  use: zip-code, prev-employed, work-eligible, workauth-authorized
  ("legally authorized to work" -> Yes, excluding "without sponsorship"),
  currently-live-us, privacy-ack select/checkbox (refuses legal-weight
  language: arbitration, background checks, drug tests, assessments),
  facts-driven education dates (start/end month/year) and discipline,
  in-office willingness, commute willingness, and facts-driven essays
  (llm/devtools/why-company; skipped when not provided, never invented).
- templates.py: guard exemptions for workauth-authorized and privacy-ack
  so deterministic answers apply without human review; legal-weight
  language stays guarded.
- hard_patterns.py: type-to-filter for long react-select option lists
  (trusted CDP input so React sees the change).
- map.py: apply_work_auth_select_overrides (work-auth answers on
  select/custom-select dropdowns) and apply_acknowledge_override
  (narrow "Acknowledge/Confirm" privacy checkbox).
- cdp_direct.py: JOBPILOT_TAB_ID prefers a specific debug tab.
- facts.example.json: education entries document start/end month/year;
  new essays section for user-provided essay text.
- 34 offline tests (10 new).

## Unreleased - 2026-10-06: harness hardening ports

- Idempotent yes/no toggle handling in fill.py: Ashby yes/no buttons are
  independent toggles, so a blind click can unset an already-selected
  answer. New react_toggle_resolver_js()/click_react_toggle_js() ensure the
  desired button is pressed without toggling it off; wired into the
  singleton click path.
- Sponsorship hard-pattern in hard_patterns.py: sponsorship questions always
  resolve to Yes / will-require-sponsorship and never to a country-specific
  visa option; pick_sponsorship_option() fails loudly (None, None) when no
  safe option exists, and fill_react_select() raises if a country-specific
  visa ends up selected on a sponsorship field.
- common.py host_test_file(): with JOBPILOT_CDP_URL set (DIRECT_CDP), the
  resume path is checked locally instead of over SSH.

## Unreleased - 2026-10-06: round 2 feature audit

- Resolve all 39 MINOR findings, including bounded watchdog waits, observation
  evidence, explicit location configuration, unique default queue owners,
  role review artifacts, atomic setup/scoreboard writes and telemetry rotation.
- Preserve consent, deterministic mapping, human code verification and round 1
  Applied-history safeguards. See [round 2 details](docs/CHANGELOG.md).

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


All notable changes to jobpilot are documented here. The format follows
[Keep a Changelog](https://keepachangelog.com/en/1.0.0/).

## [Unreleased]

### Added
- Human review batch C: owner-only queue `heartbeat`, the offline-testable
  `codegate.py` coordinator with CLI notifier and Telegram extension stub,
  and Greenhouse lane `--dry-run` with an answer table and `dryrun.png`.
- `setup_wizard.py` (`ai-job-pilot-setup`): optional browser launch, safe
  queue initialization, tracker credential exports, and facts validation.
  Non-secret settings only go to config.json; env vars win.
- `docs/setup.md` and `test_human.py`: 22 offline tests for Batch C.
- Tracking batch B.
  - `tracker.py`: `parse_sheets_id` / `parse_notion_db_id` let
    `TRACKER_SHEET_ID` and `TRACKER_NOTION_DB` take a full URL or a bare
    ID. Unparseable values raise `ValueError` (fail closed).
  - `scoreboard.py` (`ai-job-pilot-scoreboard`): `record` / `show`
    per-lane day and week totals over a locked JSON file
    (`JOBPILOT_SCOREBOARD`).
  - `analytics.py` (`ai-job-pilot-analytics`): funnel conversion rates,
    per-company table, median time-to-response, `--json` output; unknown
    statuses are reported as `other`.
  - `sweep.py`: `near_duplicate` / `find_duplicates` and a fuzzy dedup
    step in `filter_postings`. Reposts of the same role on another board
    are withheld from the queue and listed in `stats["fuzzy_dup_review"]`
    for human review; they are never auto-merged into the applied log.
  - `test_tracking.py`: 28 offline tests for the above.
- Reliability batch A.
  - `submit_watchdog.py`: detects a swallowed submit click (URL unchanged,
    no confirmation, no error) and walks a recovery ladder: wait+recheck,
    normal re-click, JS click, then give up as blocked with evidence.
    Recovery requires explicit safe-retry evidence, with one recovery click
    maximum; ambiguous states block; the Greenhouse code gate stays human-in-the-loop.
    Tunable with `JOBPILOT_SUBMIT_WAIT_S`, `JOBPILOT_SUBMIT_RECHECKS`,
    `JOBPILOT_SUBMIT_POLL_S`. Wired into `lane_greenhouse.py`.
  - `location_check.py`: post-fill location verifier. Requires the city
    as a whole word AND the state token, so "Austintown, Ohio" no longer
    passes for "Austin, TX". Fails closed (empty value, no metros). The
    lane returns `blocked` for review instead of submitting. Allowlist via
    `JOBPILOT_TARGET_METROS`.
  - `model.budgeted_chat`: `chat()` with a `max_tokens` ceiling
    (`JOBPILOT_MODEL_MAX_TOKENS`), a hard wall-clock budget
    (`JOBPILOT_MODEL_WALL_TIMEOUT_S`), and per-call JSONL telemetry
    (`JOBPILOT_MODEL_TELEMETRY`). real calls use the wrapper, with cooperative cancellation in `chat()`.
  - `test_reliability.py`: 24 offline tests for the above.
- Operation layer: run the whole pipeline, not just single fills.
  - `launch_browsers.py` (`ai-job-pilot-launch`): interactive headful
    Chrome launcher. Asks how many browsers to open (recommended 2),
    validates the count, assigns each its own port (checked free) and
    profile dir, applies the macOS / Windows (schtasks /IT, visible on
    the main screen) / Linux launch recipes, polls
    `/json/version` until each instance answers, and prints a lane
    summary table. Fails loudly if any instance does not come up.
    Lane 1 defaults to Greenhouse, lane 2 to other boards; lane 3+
    takes a purpose interactively or via `--purpose` flags.
  - `lane_greenhouse.py` (`ai-job-pilot-lane`): one lane drives one
    browser against the shared queue: claim, fresh tab, dead-posting
    check, schema dump, deterministic-first map, fill, `checkValidity`
    gate, submit, outcome detection (code gate / submitted /
    unconfirmed), then tracker record + queue mark. The Greenhouse
    code gate stays human-in-the-loop: the lane parks the job blocked
    and uses the human coordinator to enter the code and verify confirmation.
  - `job_queue.py`: file-backed queue with cross-lane claims (append-only
    JSONL claims log, stale-claim expiry, flock-guarded writes).
    Documented in `docs/queue.md`.
  - `tracker.py` (`ai-job-pilot-track`): pluggable application
    tracking. JSONL backend (default, durable local record), Google
    Sheets backend (service account, documented setup), Notion
    backend (stdlib-only API client, documented setup). Secrets from
    env vars only. Documented in `docs/tracking.md`.
  - `sweep.py` (`ai-job-pilot-sweep`): vetting funnel for raw
    postings: title level/role filters, location allowlist, visa
    sponsorship-language filter, salary floor, dedup against the
    applied log. Writes queue-shaped candidates. Documented in
    `docs/sweep.md`.
  - `docs/machine-setup.md`: visible-browser topology, per-platform
    launch recipes, CDP driver notes.
  - `config.example.json`: every env var in one place (copy to
    `config.json`, gitignored).
- `test_operation.py`: 33 offline tests covering queue claims/marks,
  tracker record + JSONL round-trip, sweep filters, launcher
  allocation/validation, and config precedence (no browser, model, or
  network needed).

### Fixed (pre-publish audit hardening)
- `job_queue.py` (renamed from `queue.py`, which shadowed the stdlib
  module): mutations now run inside a sidecar-lock transaction with
  unique temp files and fsync; claims file is created without
  truncation; release/mark verify claim ownership; malformed claim
  lines are counted, not silently dropped; URL normalization preserves
  path case and job-identifying query params (strips only known
  tracking params); naive timestamps handled; new `requeue`
  subcommand returns a terminal job to pending; `next`/`claim` accept
  an `--ats` filter.
- `tracker.py`: Sheets backend uses `RAW` input mode (posting text can
  never become a formula); `TRACKER_SHEET_TAB` env fallback fixed and
  tab names A1-quoted; fan-out validates all backends first and
  reports per-backend errors; JSONL appends are locked and fsynced;
  Notion backend persists all record fields with 2000-char clipping;
  records require a URL and a valid status.
- `sweep.py`: remote locations no longer invent US eligibility
  ("Remote - South Africa" is rejected); more sponsorship-exclusion
  phrases; string salaries ("$90,000", "90k") are parsed against the
  floor; in-batch dedup; ATS detection parses the URL host;
  `filter_postings` takes an injectable timestamp.
- `lane_greenhouse.py`: independent consent pre-scan blocks
  arbitration/certification/background-check fields before filling;
  validity gate fails closed; required fields must all be filled
  before submit; navigation host is validated; submit is one
  form-scoped find+click with verification; code-gate marks blocked
  (never releases while waiting); confirmation marks Applied,
  and only failed/expired attempts below the cap can requeue; `--no-submit` releases the claim; lane only takes Greenhouse
  jobs; non-local CDP URLs fail closed; all outcomes are tracked.
- `launch_browsers.py`: `--remote-allow-origins` restricted to
  loopback; Windows paths quoted and schtasks run without `shell=True`
  with cleanup in `finally`; purpose-assignment indexing fixed; port
  range validated; instance polling requires real Chrome fields.

### Fixed
- `fill.py` `do_select`: the react-select detector compared the whole
  CDP `Runtime.evaluate` response dict to the string `"rs"`, so
  `fill_react_select` was silently bypassed and execution fell through
  to the generic click+type+Enter path, which often failed to select
  the intended option. Added a `_cdp_value()` helper that normalizes
  both bare values and raw CDP RemoteObject dicts
  (`{"type": ..., "value": ...}`) before comparing, and applied it to
  `do_select`, the location-settle poll, and the three submit-button
  detectors that had the same latent dict-vs-string comparison. This
  was the root cause of the "dropdown won't fill" failures seen on
  Greenhouse react-selects (including school dropdowns).

### Changed
- Fill priority flipped to deterministic-first (was local-model-first):
  `hard_patterns.py` / `templates.py` answer every recognized field,
  backed by 500+ submitted applications of proven field data; the local
  model is now the backup for unmapped or low-confidence fields only.
  Model inference was the slowest step per application. Implemented in
  code: `map.py` `map_fields` now runs `templates.map_template` first
  and calls the model only for fields left unmapped or skipped without
  a guard marker (new `_map_with_model` helper; guard-skips are never
  sent to the model). The model server is probed only when the backup
  path is actually needed. Updated in `AGENTS.md` and
  `docs/multi-machine.md`.
- README: install via pip, batch mode, code-gate flow, and a "proven on"
  section with real verification numbers.

### Added
- `docs/greenhouse-quirks.md`: production Greenhouse form quirks -
  JS-enforced cover letter despite optional schema, location
  autocomplete picking the wrong city (always verify visually), React
  textarea `execCommand('insertText')`, react-select handling,
  confirmation detection requiring "successfully been received" or
  `/confirmation` URL, privacy/GDPR acknowledgement posture,
  sponsorship phrasing variants, unfixable employer form bugs, and the
  per-application code gate.
- `docs/multi-machine.md`: browser topology (one browser per worker,
  two app browsers per machine, third only for active fixes; all
  visible, never headless; CDP `setWindowBounds` tiling) and the
  Windows playbook (schtasks `/IT` visible launch, 8.3 short paths,
  `powershell -File`, Chrome 154 `lockfile` check).
- `docs/troubleshooting.md`: unfixable form bugs (mutually exclusive
  required checkboxes), the dropdown router bug note, and
  silent-submit causes.
- `docs/multi-machine.md`: multi-machine CDP operation guide. Visible
  debug Chromes with per-lane ports, default Chrome profile (temp profiles
  lose sessions), detached launches (schtasks on Windows, nohup on
  macOS/Linux), tab hygiene, the never-mix-headless-and-headful profile
  rule, one shared model server, and single-owner dedup across machines.
- Greenhouse code-gate handling pattern documented in `docs/ats-notes.md`:
  park at the code screen, accept a user-supplied code once, type it,
  submit, verify the confirmation page, never store or reuse codes. Codes
  expire in under 40 minutes; one code per application.
- ~~Local-models-first operation documented~~ (superseded by the
  deterministic-first change above; docs now describe templates-first
  with the model as backup).
- Troubleshooting: "profile in use" launch conflict and one-model-at-a-time
  server guidance.
- `batch_apply.py --dedup`: skip posting URLs already present in the
  applications log, so a queue can be re-run safely.
- `batch_apply.py --resume-map`: JSON file mapping URL substrings to resume
  paths; the matched resume overrides `facts.json` `resume_path` for that
  application only (never modifies your facts file).
- `JOBPILOT_RESUME` environment override, honored by `ats_fill.py`.
- Packaging (`pyproject.toml`): `pip install .` provides the
  `ai-job-pilot`, `ai-job-pilot-batch`, and `ai-job-pilot-code-gate` console scripts.
- GitHub issue templates: bug report (with ATS type, schema snippet, logs)
  and new-ATS request.
- `examples/queue.txt` and `examples/resume-map.json`.
- Demo recording (`demo/demo.cast`, playable with `asciinema play`).
- `ashby_graphql.py`: Ashby GraphQL form-template capture technique. Hooks
  both `fetch` and `XMLHttpRequest` to watch outbound GraphQL requests and
  stash the form-value mutation template (flexible operation-name match, not
  hardcoded), plus a parameterized trigger snippet that forces Ashby to emit
  a mutation. Direct-mutation filling is more reliable than DOM events, which
  Ashby can silently discard server-side.

## [0.1.0] - 2026-10-04

### Added
- Initial public release: deterministic ATS form filler for Greenhouse,
  Ashby, Lever, and an Indeed lane. Stdlib-only CDP browser driver.
- `code_gate.py`: detects the Greenhouse 8-character email verification
  gate, prompts for the code on stdin, enters one character per box,
  submits, and verifies the confirmation page. Codes stay
  human-in-the-loop; never guessed or bypassed.
- `batch_apply.py`: queue file of posting URLs, sequential fill with
  `--no-submit`, per-submit confirmation, code-gate handling, JSONL log.
- `test_offline.py`: 11 unit tests over the deterministic core (identity
  fill, never-guess skips, truthful sponsorship answers, facts-driven
  schools, fuzzy matcher). No browser, model, or network required.
- GitHub Actions CI: syntax check, offline tests, and a guard that fails
  the build if `facts.json` is ever committed.
- `docs/ats-notes.md`: Greenhouse per-application codes, Ashby config
  parsing quirks, Lever CAPTCHA behavior, phone formatting, upload paths,
  readback checks, model constraints.
- `CONTRIBUTING.md`: never invent facts, never commit `facts.json`,
  stdlib-only driver, deterministic-first, offline tests for new patterns.
- `AGENTS.md`: agent runbook (setup, code-gate protocol, safety invariants).
- `facts.example.json`: placeholder applicant profile; real `facts.json`
  is gitignored.
