# Code and documentation review: ai-job-pilot feature batch

**Repo:** `/home/hatch/workspace/jobpilot-staging` (not a git checkout, so I reviewed the files directly)
**Date:** 2026-10-06
**Scope:** the six new modules, the modified code, the docs and the consent invariants, as listed in the request.

**Verification limits:** permission to run commands was denied, so I did not run the offline test suite or any probe scripts. Everything below comes from reading the code and tracing it by hand. The "125 tests" count in the README and CHANGELOG is unverified.

Em-dash scan: no U+2014 in any of the in-scope files. The only hits in the repo are in `docs/audits/REPORT-*.md`, which is out of scope.

---

## Area 1: New modules

### submit_watchdog.py
The escalation order is correct: wait and recheck, then normal click, then JS click, then give up. It is bounded (at most two clicks) and cannot double-recover, except for the unbounded wait in MINOR 2.

- **MAJOR `:153-160`:** if the normal click finds nothing, the ladder returns `error` instead of trying the JS click.
  - *Scenario:* the lane's first click used the JS fallback (`lane_greenhouse.py:371-373`), because the normal selector missed. The watchdog's normal re-click misses too, so a possibly-submitted application is marked `blocked`.
  - *Fix:* record the miss and continue to the next rung. Only report "control not found" after both rungs fail.
- **MAJOR `:148-161`:** an unchanged URL with no text cannot tell a swallowed click from a slow in-flight submit. After the default 10s wait, the watchdog can click twice more.
  - *Fix:* add adapter signals for a disabled button, spinner or pending network. Wait longer before the first re-click, allow at most one re-click, and prefer `blocked` when the state is ambiguous.
- **MINOR 1 `:89-91`:** exceptions from `current_url` or `page_text` (for example the lane's `subprocess.TimeoutExpired`) escape after the click and lose the ladder evidence. Catch them and return `blocked` with evidence.
- **MINOR 2 `:62-66,98-103`:** `JOBPILOT_SUBMIT_WAIT_S=inf` is accepted and polls forever. Clamp the value to a sane range.
- **MINOR 3 `:54-56`:** `ERROR_RE` ("is required") can match static page copy and produce a false `error`. Compare against pre-click text or scope it to invalid or error elements.
- **MINOR 4 `:48-53`:** `CODE_GATE_RE` and `CONFIRM_RE` are duplicated in `lane_greenhouse.py:70-74`, and the lane's copies are now unused. Import from the watchdog and delete the lane's copies.

### location_check.py
It is fail-closed for an empty value, an empty target list, and descriptors with no state. A bare "austin" no longer matches "Austintown, Ohio". Three gaps remain.

- **MAJOR `:112-118`:** a comma-only list breaks for any city that is also a state name.
  - By my trace, `"New York, NY"` becomes `["New York", "NY"]`, and both are unusable. `"Washington, DC"` fails the same way.
  - *Fix:* require `;` or JSON for lists, or glue only when the previous token is not already a state. Warn when a descriptor is unusable.
- **MAJOR `:101-109,144-146`:** unusable descriptors are skipped silently, and a malformed JSON array raises inside `run_job` (`lane_greenhouse.py:323`).
  - A config typo therefore marks every queued job `blocked`, one at a time.
  - *Fix:* validate once at lane startup and fail fast with a clear message.
- **MAJOR `:154-155`:** the city and state tokens are not required to be adjacent, so composite or negated values pass.
  - `"Austin, MN; Dallas, TX"` passes for "Austin, TX", and `"Remote (not US)"` passes for "Remote (US)".
  - *Fix:* split the value on `;`, `|` and "or", and require city and state within one segment. Reject negations such as "not" and "except".
- **MINOR 5 `:154-155`:** abbreviations that are English words (`or`, `in`, `me`, `ok`, `hi`) match as bare tokens.
- **MINOR 6 `:37`:** the open-source default is the maintainer's own metro ("Austin, TX; Remote (US)"). It also conflicts with `sweep.py:54-59`, which queues Seattle, SF and NYC jobs that the lane then always blocks. Default to empty, or align the two lists.
- **MINOR 7 `lane_greenhouse.py:129-147`:** any field whose label contains "location" is checked. A question like "open to our Austin location?" produces `Yes` and a false block.

### scoreboard.py
Concurrent access is safe on one POSIX host. Writers use the `Store` sidecar flock, saves use `os.replace`, and readers are unlocked but see atomic files.

- **MINOR 8 `:8-11`:** the lock is a no-op on Windows (`job_queue.py:66-71`), which can lose events. The docstring claims otherwise without that caveat.
- **MINOR 9 `:61-68`:** the temp file leaks on error. `mkstemp` also resets the file mode to 0600. Use try/finally with `unlink`.
- **MINOR 10 `:98-109`:** events with `ts > now` are dropped, so lanes on hosts with fast clocks lose events. "Day" is UTC. Add a small tolerance and document the UTC behaviour.

### analytics.py
Stages nest correctly. The rank comparison makes applied ≥ responded ≥ screening ≥ interview ≥ offer, division by zero is guarded, and the median is formatted correctly. The problems are in how it fits with the rest of the repo.

- **MAJOR `:32-37` vs `tracker.py:59,69`:** the status vocabularies do not match.
  - The tracker writes only `Applied/Skipped/Blocked/Dead/Ready` and rejects everything else. Analytics understands `applied … withdrawn`.
  - So Skipped, Blocked, Dead and Ready all land in "other / unknown", and the later funnel stages can never be reached through the tooling.
  - *Fix:* add the response statuses and an update path to the tracker, and put non-applications in an "excluded" bucket rather than "unknown".
- **MAJOR `:36-37,118`:** `rejected` and `withdrawn` rank 0, so a rejection after an interview erases the interview. A rejection also counts toward the median (`:118`) but not toward the "responded" stage. Track the furthest stage reached.
- **MAJOR `:119-121`:** `response_date` is not in `tracker.FIELDS`, and `build_record` silently drops unknown keys (`tracker.py:66-68`). The median is `n/a` unless someone hand-edits the JSONL. Add the field or remove the feature and its docs.
- **MINOR 11 `:112-125`:** duplicate records for one URL each count as an application. Dedupe by `norm_url`, keeping the latest.
- **MINOR 12 `:172-177`:** the default path is relative to the working directory, and a missing file gives a raw traceback.

### codegate.py
- **MAJOR `:73-75,86-90`:** on timeout it requeues, with no attempt cap, and the callback's result is ignored.
  - `coordinate` returns `timeout` even if `requeue` returned False.
  - The requeued job becomes immediately claimable (the latest claim entry is `blocked`, not in-progress), so the lane re-fills and re-submits and sends another code email, indefinitely.
  - *Fix:* add an attempts counter with a cap of 1 or 2, then leave the job blocked for a human. Surface the requeue result.
- **MAJOR `:36-56`:** stdin EOF (`</dev/null`, detached runs), Windows (`select` on stdin raises `OSError`) and `UnicodeDecodeError` all return `None`. `coordinate` treats `None` as a timeout and requeues immediately with no human wait. Return a distinct "no-input" result.
- **MAJOR `:1-90`:** `coordinate` and `Notifier` are called only from `test_human.py`. No lane, CLI or console script uses them.
  - The only real notifier is `CliNotifier`. `TelegramNotifier` is a stub that raises `NotImplementedError`.
  - `docs/queue.md:84-90` says it "owns the waiting loop", and `CHANGELOG.md:9-11` lists it as shipped. Wire it in, or label it library-only.
- **MINOR 13 `:78-81`:** validation is length 8 after `strip()` plus no internal whitespace. There is no charset check, so `!!!!!!!!` and non-ASCII or zero-width characters pass. Use `re.fullmatch(r"[A-Za-z0-9]{8}", code)`.
- **MINOR 14 `:9,86`:** `codegate.py` and `code_gate.py` are easy to confuse; rename one. `requeue_on_timeout` passes `claims_path`, which `job_queue.requeue` ignores along with `lane`.

It never reads email or the inbox, and it uses a monotonic deadline. Both are good.

### setup_wizard.py
`config.json` contains only the keys from `plan_setup` (`:37-63`), so no secrets are written to disk. `--yes` skips stdin and launches. `--dry-run` returns before any write (`:205-207`).

- **MAJOR `:129-131,196-199`:** the raw `TRACKER_NOTION_TOKEN` (and the other credential values) are printed to stdout as `export` lines.
  - That defeats the `getpass` prompt, and in `--yes` mode the value from the environment lands in CI logs and scrollback.
  - *Fix:* print placeholders such as `export TRACKER_NOTION_TOKEN=<your token>`, or write a 0600 file behind an opt-in flag.
- **MAJOR `:43,160,163,200`:** the default directory is the script directory, which is `site-packages` after the `pip install .` the README recommends.
  - `config.json`, `queue.json`, `claims.jsonl` and the facts path default there.
  - `facts.example.json` is not packaged, so the "looks like the template" check silently never runs (`:144`).
  - `--directory` elsewhere writes a config that `config.py:23` does not read unless `JOBPILOT_CONFIG` is exported.
  - *Fix:* default to the working directory or `~/.config`, print the `JOBPILOT_CONFIG` export, and ship the example files as package data.
- **MINOR 15 `:165-167`:** `--dry-run` against an existing config exits 2 until you add `--force`.
- **MINOR 16 `:208-228`:** the config is written before queue initialisation and browser launch, so a launch failure leaves it written. The `--force` path truncates then writes; use a temp file and replace.

---

## Area 2: Modified code

### model.py `budgeted_chat`
It is fail-closed at the caller. On timeout it raises `ModelBudgetExceeded` and the result is never returned. Only the main thread writes telemetry, so there is no in-process race.

- **MAJOR `:285-299`:** the timed-out daemon thread cannot be cancelled and keeps running `chat()`.
  - That is up to retries × models × 180s plus backoff, which can run for tens of minutes against the single-model server.
  - The next call then queues behind it.
  - *Fix:* pass a deadline or `Event` into `chat()` and check it between attempts and models. Cap the per-request timeout to the remaining budget.
- **MAJOR (feature not wired):** `map.py:477`, `extract.py:64` and `verify.py:42` still call `model.chat`. The token ceiling, wall budget and telemetry therefore never apply outside tests. Route those call sites through `budgeted_chat`.
- **MINOR 17 `:244-250`:** telemetry has no rotation and no cross-process lock. It logs no prompt text, which is good.
- **MINOR 18 `:236-241,278-280`:** `wall_timeout=0` or negative times out instantly, while the same value in the environment falls back to the default. `inf` makes `join` raise `OverflowError`.

### job_queue.py heartbeat
Heartbeat semantics are correct. Only the live owner can refresh a claim. When the TTL lapses, nobody owns the job, any lane can claim it, and the old lane must re-claim before heartbeating. This matches `docs/queue.md:44-53`. Lock ordering is deadlock-free, and "latest entry wins" is by file order.

- **MINOR 19 `:274-286,410-413`:** no lane calls `heartbeat`, and the TTL is 6 hours, so it adds nothing today. The CLI also prints `not-owner` for an expired claim. Add a separate short TTL, call it from the lane, and print "no-live-claim".
- **MINOR 20 `:76,216-224`:** a claim with an unparseable `claimed_at` is treated as not live (fail-open). A future-dated claim stays live indefinitely. `CLAIM_MAX_AGE_HOURS=0` disables claims, and `nan` or `inf` is accepted.
- **MINOR 21 `:267-271`:** ownership is only the lane string, and the default is `lane-1` on every host (`lane_greenhouse.py:491`). Two hosts can pass each other's owner checks. Require `JOBPILOT_LANE` or add a unique token.
- **MINOR 22 `:386-395,419-420`:** `mark` and `release` default to `--lane lane-1`. That contradicts `docs/queue.md:31-32` ("an operator with no `--lane`"), and `mark` prints `not-found` for a not-owner result.
- **MINOR 23 `:105`:** `p.port` can raise `ValueError` outside the `try`, for example on `https://h:abc/x`.

### sweep.py fuzzy dedup
- **MAJOR `:327-331,164-193,359-367`:** fuzzy matching runs only against candidates in the current batch. `load_applied_urls` returns URLs only, and the queue merge compares by URL only. The headline case, a role already applied to being reposted under a new URL in a later sweep, is not caught.
- **MAJOR `:359-368` (older code, but in a modified file):** the sweep writes the queue with no `Store` lock, so it can overwrite a lane's `mark()` and flip an applied job back to pending, leading to a duplicate application. Wrap the read-modify-write in `with Store(a.queue)`.
- **MINOR 24 `:212-214`:**
  - The 0.8 Jaccard threshold is reasonable.
  - But seniority words are stripped, so Senior, Staff and Lead variants compare as equal.
  - Identical titles for separate openings at one company and location are withheld, and the review list is only printed, not saved.
- **MINOR 25 `:219-239`:** normalisation is ASCII-only `[a-z0-9]+` with no NFKD or casefold. "C++" and "C#" both become `c`, so "Senior C++ Engineer" equals "Senior C# Engineer". Accented words split, and CJK produces no tokens.
- **MINOR 26 `:259-261`:** location is compared by strict equality ("austin tx" ≠ "austin texas"). That is the safe direction but misses many reposts.
- **MINOR 27 `:327`:** comparison is O(n²) and re-normalises every pair. Precompute keys and bucket by company and location.

### tracker.py URL parsing
Trailing slashes, query strings, fragments and surrounding whitespace are handled. The Notion `?v=` view ID is correctly ignored, and hosts are matched strictly.

- **MAJOR `:115-119`:** for a published-sheet URL like `/spreadsheets/d/e/2PACX-…/pubhtml`, the regex captures `e` as the ID (traced by hand). That breaks the "never guess an ID" promise. Reject `/d/e/` and require a realistic ID length.
- **MINOR 28 `:107-110`:** bare IDs accept any `[A-Za-z0-9_-]+`, so `hello` passes and only fails later with a 404. Any scheme is accepted if the host matches.
- **MINOR 29 `:133-146`:** Notion accepts any hyphen placement, cannot tell a page URL from a database URL, and rejects custom domains.

---

## Area 3: Security

I found no confirmed exploitable injection. I found no `eval`, `exec`, `os.system` or `shell=True`, and no untrusted text reaching a command line. Specifics:

- **Formula injection:** Sheets uses `valueInputOption="RAW"` (`tracker.py:214-216`), so posting text is stored as text. Notion and JSONL are safe.
  - **MINOR 30:** a later CSV export opened in Excel could still evaluate `=…` cells. Company names also print raw to the terminal in `analytics.py:164-166` and `lane_greenhouse.py:226-227`, which only escape `\n\r\t`, so ANSI escape sequences pass through. Strip control characters.
- **MINOR 31 `lane_greenhouse.py:384-390,443`:** the confirmation-page snippet is saved to the tracker (and so to Sheets or Notion). It may contain the applicant's name or email. Store the matched phrase and URL instead.
- **MINOR 32 `model.py:175-178`:** `_lms_ensure` puts `current` (from remote `lms ps` output) and `target` into shell strings. Use `shlex.quote`.
- **Secrets:** the tracker reads credentials from the environment only (`tracker.py:181-185,225-227`), and telemetry logs no prompt text. The one leak is the wizard echo (MAJOR in `setup_wizard.py`, above).
  - **MAJOR `.gitignore`:** it has no `.env`, although `docs/tracking.md:118-119` says the local `.env` is gitignored.
  - It also omits `applications.jsonl`, `queue.json`, `claims.jsonl`, `scoreboard.json`, `*.lock` and service-account keys. These are pipeline and PII data that are easy to commit by accident.
- **Path traversal:** none found. The workdir name goes through `slug()` (`lane_greenhouse.py:197-200`) and subprocess calls use list arguments. Queue, claims, scoreboard, telemetry and config paths come from the operator, not from posting data. The wizard creates files with `open(path, "x")`.

---

## Area 4: Docs accuracy

- **README vs pyproject:** the 11 console scripts in `README.md:41-55` match `[project.scripts]` exactly, and `code_gate:main` exists. All new modules are in `py-modules`.
- **docs/setup.md vs `setup_wizard.py`:** the flags (`--yes`, `--force`, `--dry-run`, `--directory`, `JOBPILOT_CONFIG`) and the written keys match.
  - **MINOR 37:** it does not say that credential values are echoed in plaintext, that the default directory is the install directory, or that the config is not found when `--directory` is not the script directory (`:26-27`, `:36-41`).
- **docs/queue.md vs `job_queue.py`:** the heartbeat and claim semantics match. Two doc errors:
  - **MINOR 38:** the heartbeat command omits `--claims`, and the doc implies lanes already heartbeat.
  - The code-gate flow and the `codegate` description are wrong (see S1 below and MAJOR on `codegate.py`).
- **CHANGELOG:** most entries are accurate and the watchdog and location wiring are verified in the lane.
  - **MINOR 36 `:64-65`:** it says the lane "releases its claim" on a code gate, but the code marks the job blocked (`lane_greenhouse.py:416-428`).
  - The `codegate` and `budgeted_chat` entries read as shipped features but are not wired (see MAJORs above). The analytics median claim is unreachable through the tracker.
- **MINOR 33 `config.example.json:7-10`:** it lists `JOBPILOT_MODEL_*` and `TRACKER_*` as settable in `config.json`, but `model.py` and `tracker.py` read `os.environ` only, so config-file values are ignored. The `config.py:6-8` docstring makes the same claim.
- **MINOR 34 `SKILL.md`:** `cd jobpilot` (`:30`) but the repo clones to `ai-job-pilot`. `JOBPILOT_MODEL_URL` default is shown with `/v1` (`:45`), but `model.py:31-33` appends `/v1/...` itself, so that value would break.
- **MAJOR `SKILL.md:102-115`:** it lists 6 safety invariants and omits the consent rule (arbitration, certifications, background-check, drug-test and assessment fields are never auto-answered) and the `modal_common` employer blocklist. `AGENTS.md` has both. An agent following only the skill never sees the consent invariant.
- **MINOR 35 `README.md:380-415` and `AGENTS.md` Layout:** both omit `setup_wizard.py`, `codegate.py` and `docs/setup.md`, and `AGENTS.md` also omits the other new modules. `README.md:84` says map is "the only LLM call", but `extract.py` and `verify.py` also call `model.chat`.
- **MINOR 39:** `docs/audits/*.md` contain em dashes. They are out of scope, but the convention applies repo-wide.

---

## Area 5: Consent and fail-closed invariants

- **Consent is intact.** No new module touches form checkboxes. The independent `consent_blockers` scan and the `GUARD_RE` guard are unchanged and run before the fill (`lane_greenhouse.py:181-194,281-305`). `--dry-run` does not relax them. The code gate stays human-in-the-loop: the watchdog never re-clicks on a gate, and `codegate` never reads email.
- **Deterministic-first is intact.** `map_fields` runs templates and `hard_patterns` first and calls the model only for unmapped or low-confidence fields (`map.py:427-460`). The fill path is unchanged.
- **New gates are fail-closed:** location, validity, navigation, and the watchdog's "moved with no confirmation means blocked". The softer spots are a missing location field, which is not checked (acceptable), and the unparseable-claim case in MINOR 20.
- **MAJOR `lane_greenhouse.py:408-452,531-541`:** `--dry-run` is documented as stopping before submit (`:44-45`), but any non-`ready` outcome still marks the job terminal in the queue and writes a tracker row (Blocked or Dead). That includes the exception path.
  - A transient navigation failure on a dry run permanently removes the job, and the row can reach Sheets or Notion.
  - *Fix:* when `dry_run` is set, always release the claim and skip tracking and marking.

### SEVERE S1: the code-gate flow contradicts itself and can duplicate applications
`lane_greenhouse.py:386-388` tells the operator to enter the code and then "mark applied". But `:412-419`, `:553-555` and `docs/queue.md:33-35,76-83` all say to `requeue` after the code is entered.

Once `code_gate.py` has confirmed the application, a requeue returns it to `pending` and makes it claimable (the latest claim entry is `blocked`, not in-progress). The next lane then re-fills and re-submits an application that already went through. The tracker row also stays `Blocked`.

*Fix:*
- A confirmed code entry should call `mark applied` and record a tracker `Applied` row.
- `requeue` should be reserved for a failed or expired code, with an attempt cap (see the `codegate.py` timeout finding).
- Make the lane message, `docs/queue.md` and `AGENTS.md` all say the same thing.

---

## Summary

| Severity | Count |
|---|---|
| SEVERE | 1 |
| MAJOR | 21 |
| MINOR | 39 |
| **Total** | **61** |

Security-relevant items are MAJOR and MINOR only: the credential echo, `.gitignore`, shell interpolation, terminal escapes and PII snippets. Nothing confirmed as exploitable.

**VERDICT: BLOCKED**

Minimum to unblock:
1. Fix the code-gate flow (S1).
2. Make `--dry-run` side-effect free.
3. Stop the wizard echoing secrets.
4. Fix the watchdog's rung-2 escalation and double-click risk.
5. Fix `.gitignore` and the sweep queue lock.
6. Wire in or relabel `codegate`, `heartbeat` and `budgeted_chat`.

The rest can follow as APPROVED-WITH-FIXES work.
