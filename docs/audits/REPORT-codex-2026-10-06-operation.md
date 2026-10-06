**queue.py**

1. **P1: Queue writes are not safely serialized.** [Line 198](/tmp/jp-review-code/queue.py:198) locks the queue file, but [line 97](/tmp/jp-review-code/queue.py:97) replaces that file with a different inode. A waiting writer can hold the old inode’s lock while another writer locks the replacement. `save_queue()` also uses one shared `.tmp` filename and does not acquire any lock itself. Concurrent marks or sweeps can lose updates, race on the temporary file, or restore an applied job to pending. **Fix:** use a stable sidecar lock around the entire read-modify-write transaction, unique temporary files, and durable writes. Require every writer to use that transaction.

2. **P1: Claims can be erased during initialization.** [Lines 75-77](/tmp/jp-review-code/queue.py:75) check existence and then open with `"w"` before locking. Two initial claimers can both observe a missing file; a delayed opener can truncate the first claimer’s appended record. **Fix:** create without truncation, using `O_CREAT` or append mode, then initialize under a stable lock.

3. **P1: Expiry has no heartbeat or ownership fencing.** [Line 139](/tmp/jp-review-code/queue.py:139) expires claims after six hours, while [release()](/tmp/jp-review-code/queue.py:181) and [mark()](/tmp/jp-review-code/queue.py:194) never verify ownership. If lane A expires and lane B reclaims the job, A can still submit, release B’s claim, or overwrite B’s terminal status. **Fix:** assign claim tokens, renew leases, and require the current token for renewal, release, marking, and authorization to submit.

4. **P1: The cross-machine guarantee is unsupported for some storage arrangements.** [Lines 47-52](/tmp/jp-review-code/queue.py:47) make Windows locking a no-op. POSIX `flock` also cannot coordinate separately synchronized copies, and shared-filesystem lock behavior must be established. “One writer per machine” does not serialize multiple machines. **Fix:** use a centralized transactional queue, or explicitly require and verify a shared filesystem with working cross-host locks. Fail closed when locking is unavailable.

5. **P1: Terminal claim appends bypass locking; damaged records silently disappear.** [Line 215](/tmp/jp-review-code/queue.py:215) calls the unlocked `append_claim()`. [Lines 107-110](/tmp/jp-review-code/queue.py:107) silently discard malformed log lines. A truncated final record followed by another append can produce one invalid line containing both records, losing a live claim. **Fix:** serialize every append, validate the trailing record before appending, detect corruption explicitly, and flush/fsync important transitions.

6. **P2: URL normalization merges different jobs.** [Lines 63-68](/tmp/jp-review-code/queue.py:63) remove every query parameter and lowercase the path. Verified: `/careers?job_id=1` and `/careers?job_id=2` become identical. Case-sensitive paths can also collide. This affects claims, marking, and deduplication. **Fix:** preserve job-identifying parameters and path case; remove only known tracking parameters. Prefer ATS job IDs.

7. **P2: Valid JSON can crash claim processing.** [Line 129](/tmp/jp-review-code/queue.py:129) assumes every record is a dictionary; [lines 135-139](/tmp/jp-review-code/queue.py:135) assume a string, timezone-aware timestamp. A timezone-free timestamp produces a verified `TypeError`. Scalars, null timestamps, or malformed queue entries also crash consumers. **Fix:** validate record schemas and timestamp timezone/range, and report invalid records explicitly.

8. **P2: The module name conflicts with Python’s standard library.** [queue.py](/tmp/jp-review-code/queue.py:1), imported by the lane, sweep, and tests, occupies the name `queue`. Verified: importing standard-library `queue` before `lane_greenhouse` causes `ImportError`. The reverse import order can break dependencies expecting `queue.Queue`. **Fix:** rename it to `job_queue.py` and use package-relative imports.

**tracker.py**

1. **P1, security: Untrusted posting text becomes spreadsheet formulas.** [Line 107](/tmp/jp-review-code/tracker.py:107) uses `USER_ENTERED`. A company, title, or notes value beginning with `=` can be evaluated as a formula. **Fix:** use `RAW` for application records.

2. **P2: `TRACKER_SHEET_TAB` is ignored by default.** [Lines 79-83](/tmp/jp-review-code/tracker.py:79) default `tab` to `"Applications"`, preventing the environment fallback from running. Verified with an environment value of `"Custom"`. [Line 106](/tmp/jp-review-code/tracker.py:106) also interpolates tab names without A1 quoting. **Fix:** default `tab=None`, resolve the environment value, and quote/escape the sheet name.

3. **P2: Fan-out has no partial-failure or retry contract.** [Lines 172-178](/tmp/jp-review-code/tracker.py:172) stop on the first exception. Earlier backends may already have written, later ones are skipped, and retrying duplicates earlier writes. **Fix:** validate all backend configurations first, return per-backend results, and use a stable application ID plus a durable retry outbox.

4. **P2: JSONL writes lack coordination and durability guarantees.** [Lines 71-72](/tmp/jp-review-code/tracker.py:71) append through a buffered text writer without locking or fsync. Concurrent large records or a crash can leave damaged or lost entries. **Fix:** serialize complete-record appends with a supported locking mechanism and define the durability policy.

5. **P2: Notion silently drops audit fields and does not bound text.** [Lines 137-149](/tmp/jp-review-code/tracker.py:137) omit sponsorship, EEO, resume, and lane, although they belong to `FIELDS`. Long notes or confirmation text are placed into a single text object and can fail backend validation. **Fix:** persist all supported fields, validate the database schema, and split long text into bounded objects.

6. **P2: The public record API accepts invalid or unsupported application claims.** [Lines 54-60](/tmp/jp-review-code/tracker.py:54) default status to `"Applied"` and accept arbitrary types and statuses. An incomplete CLI invocation can record an application without evidence of submission. The [constructors](/tmp/jp-review-code/tracker.py:79) also accept credentials/token arguments despite the stated env-only policy. **Fix:** require an explicit valid status, validate field types and required evidence, and resolve secrets exclusively from environment variables if that is the contract.

**sweep.py**

1. **P1: Remote eligibility is invented from insufficient location data.** [Lines 82-91](/tmp/jp-review-code/sweep.py:82) turn any accepted “remote” location into `"Remote (US)"`. Verified: `"Remote - South Africa"` and `"Remote - Worldwide"` both pass. `"Austin, Australia (USA team)"` also passes because mentioning USA disables the non-US rejection. **Fix:** require explicit US eligibility or structured country information; preserve ambiguous locations for review.

2. **P2: Common explicit sponsorship exclusions pass.** [Lines 93-99](/tmp/jp-review-code/sweep.py:93) miss verified examples including “We cannot provide visa sponsorship,” “Sponsorship is not available,” and “We do not offer sponsorship.” Conversely, “not open to C2C” does not necessarily prohibit visa sponsorship. **Fix:** separate sponsorship restrictions from engagement type, normalize whitespace, and cover common negative constructions.

3. **P2: Salary parse failures silently bypass the floor.** [Lines 105-110](/tmp/jp-review-code/sweep.py:105) treat an invalid ceiling as undisclosed. Values such as `"$90,000"` or `"90k"` pass despite being below the floor; currency and pay period are also unspecified. **Fix:** define a numeric annual-salary input contract, validate finite values and units, and flag malformed stated salaries rather than treating them as absent.

4. **P2: Deduplication is incomplete and can suppress retryable jobs.** [Line 179](/tmp/jp-review-code/sweep.py:179) checks only `applied_urls`, never candidates already accepted in the batch. Verified: two identical postings produce two candidates. [Lines 155-163](/tmp/jp-review-code/sweep.py:155) also exclude every tracker URL regardless of status. **Fix:** maintain a normalized `seen` set during filtering and define which tracker statuses prevent reconsideration. Apply the queue URL-identity fix here too.

5. **P2, security: ATS detection trusts substring matches.** [Lines 122-131](/tmp/jp-review-code/sweep.py:122) classify URLs containing `greenhouse.io` anywhere, including an attacker hostname or query string. No URL scheme validation occurs. **Fix:** parse URLs, require HTTPS and exact supported hostnames, and derive ATS identity from the parsed host.

6. **P2: The advertised pure funnel is nondeterministic and fragile on input.** [Line 173](/tmp/jp-review-code/sweep.py:173) reads the clock internally, so identical inputs produce different results. [Lines 174-175](/tmp/jp-review-code/sweep.py:174) assume dictionary entries, while string fields are assumed throughout. **Fix:** inject the timestamp and validate the posting schema with indexed rejection reasons. Keep filesystem loading separate from pure filtering.

The queue-write race at [lines 233-241](/tmp/jp-review-code/sweep.py:233) is covered by the first queue finding.

**lane_greenhouse.py**

1. **P1: Consent enforcement depends on optional mapper metadata.** [Lines 173-182](/tmp/jp-review-code/lane_greenhouse.py:173) block only when `skipped_by_guard` is populated. The assumed `map_fields()` interface does not itself establish that this metadata must exist. A mapped arbitration, certification, background-check, drug-test, or assessment field can reach filling without an independent policy check. **Fix:** inspect schema fields before filling and enforce a shared consent policy that forces human review, including optional consent fields.

   Deterministic-first behavior and factual provenance are also delegated entirely to the unreviewed mapper. These files alone cannot establish those guarantees. Require explicit policy/provenance results and validate them before submission.

2. **P1: Validity evaluation fails open.** [Lines 198-199](/tmp/jp-review-code/lane_greenhouse.py:198) convert every non-list result, including a CDP error, to `[]`. The lane then declares the form clean. An empty document also returns an empty list. **Fix:** require successful evaluation, a recognized application form, and a correctly typed result; block otherwise.

3. **P1: Failed or incomplete filling does not prevent submission.** [Lines 191-195](/tmp/jp-review-code/lane_greenhouse.py:191) merely record failures. Unmapped fields and overall fill failure are not submission gates. Native validity cannot establish factual accuracy, custom-widget completion, or consent compliance. **Fix:** enforce mapping and fill completeness for required fields, reject unsupported answers, and validate the target form’s custom controls.

4. **P1: Navigation and host checks do not establish a trusted target.** [Lines 142-148](/tmp/jp-review-code/lane_greenhouse.py:142) ignore reset/navigation failures and search serialized output for `"greenhouse"`. A Greenhouse-looking original URL can bypass the redirect check entirely. **Fix:** validate the input host, parse the returned current URL, verify successful navigation and posting identity, and block unexpected redirects before providing applicant facts.

5. **P1: Verification gates requeue an application awaiting human action.** [Lines 262-266](/tmp/jp-review-code/lane_greenhouse.py:262) release the claim while leaving the job pending. Another lane can immediately reset, refill, and submit it. Rerunning after manual verification starts the application workflow again instead of resuming outcome detection. **Fix:** persist a nonclaimable `awaiting-human` state with browser/session details and implement an explicit resume path.

6. **P1: Submit controls and confirmation evidence are inconsistent.** [Line 79](/tmp/jp-review-code/lane_greenhouse.py:79) finds a text-matched button that the click script never selects. [Line 215](/tmp/jp-review-code/lane_greenhouse.py:215) ignores whether clicking succeeded. [Line 227](/tmp/jp-review-code/lane_greenhouse.py:227) accepts page-wide text or a URL substring as confirmation, including pre-existing posting text. **Fix:** identify one form-scoped control, verify click success, and require a verified post-submit transition with confirmation evidence tied to that job.

   A mocked reproduction reached `"submitted"` despite failed navigation, failed validity evaluation, failed filling, and `{clicked: false}`.

7. **P2: `--no-submit` permanently blocks the job.** [Lines 258-260](/tmp/jp-review-code/lane_greenhouse.py:258) convert `"ready"` to terminal `"blocked"`. A subsequent normal run cannot pick it up. **Fix:** represent ready-for-review explicitly and provide resume behavior, or release a dry-run claim without terminal marking.

8. **P2: The Greenhouse lane claims other ATS jobs.** [Lines 310-314](/tmp/jp-review-code/lane_greenhouse.py:310) select any pending job. Since sweep produces multiple ATS types, this lane can claim and permanently block jobs intended for another lane. **Fix:** select and atomically claim by ATS/capability.

9. **P1: Browser configuration can address different browser instances.** [Line 280](/tmp/jp-review-code/lane_greenhouse.py:280) retains only the port for schema and fill, while CDP calls use the full host. Remote-host configurations can therefore inspect/fill a local browser with the same port. [Line 300](/tmp/jp-review-code/lane_greenhouse.py:300) also preserves an existing conflicting `ATS_CDP_URL`. **Fix:** use one validated endpoint throughout, or reject nonlocal/conflicting endpoints where sibling interfaces support only a port.

10. **P2: Tracking and finalization can lose the audit trail.** [Lines 248-252](/tmp/jp-review-code/lane_greenhouse.py:248) discard tracking failures and still mark applied. Other statuses never call the tracker. [Lines 327-329](/tmp/jp-review-code/lane_greenhouse.py:327) write results and finalize outside the exception handler, so filesystem failures can leave claims unresolved. Job directories based only on the URL’s final component can overwrite unrelated results. **Fix:** persist a durable result/outbox for every outcome, isolate finalization failures, and use a full job identity plus attempt ID for artifacts.

**launch_browsers.py**

1. **P1, security: Debugging access unnecessarily permits every origin.** [Line 115](/tmp/jp-review-code/launch_browsers.py:115) sets `--remote-allow-origins=*`, removing an origin restriction around a powerful browser-control endpoint. [Line 118](/tmp/jp-review-code/launch_browsers.py:118) also selects the basic password store for persistent applicant profiles. **Fix:** remove the wildcard, restrict necessary origins and debugging access, and retain the platform’s protected credential store.

2. **P1, security/correctness: Windows command construction is unsafe.** [Lines 125-127](/tmp/jp-review-code/launch_browsers.py:125) join arguments into a batch command without quoting the profile path. A profile under `C:\Users\Example User\...` splits at the space. Shell metacharacters in paths can become commands, and [line 150](/tmp/jp-review-code/launch_browsers.py:150) executes task commands through `shell=True`. **Fix:** use structured subprocess arguments for `schtasks` and a Windows-safe launch mechanism that avoids interpolated batch code.

3. **P2: Interactive purposes are assigned to the wrong lanes.** [Lines 233-241](/tmp/jp-review-code/launch_browsers.py:233) skip the two defaults but append lane 3’s answer at list index zero when no purposes were supplied. Allocation assigns that answer to lane 1. **Fix:** initialize a complete indexed purpose list, then replace the relevant entries.

4. **P2: Port allocation neither validates the range nor establishes ownership.** [Lines 64-85](/tmp/jp-review-code/launch_browsers.py:64) use a connection probe without reservation or launcher coordination. Concurrent launchers can choose identical ports/profiles. Invalid ranges can raise uncaught errors; port zero cannot be polled as assigned. **Fix:** validate `1..65535`, coordinate allocation with per-profile locks, and verify the launched instance’s identity.

5. **P2: Any JSON object can count as a healthy Chrome instance.** [Lines 170-172](/tmp/jp-review-code/launch_browsers.py:170) return `"chrome"` even for `{}`. An unrelated service winning the port race can be reported as successful. **Fix:** require valid browser/version and debugger endpoint fields and verify a CDP connection to the intended instance.

6. **P2: Windows task failures are suppressed.** [Lines 152-154](/tmp/jp-review-code/launch_browsers.py:152) ignore `/run` and `/delete` errors. Failed launches become generic polling timeouts; failed cleanup can leave a scheduled task behind. The generated `.cmd` parent directory is also assumed to exist. **Fix:** check each operation, preserve actionable diagnostics, create the directory, and clean up the task in `finally`.

**test_operation.py**

1. **P2: Direct execution skips launcher tests.** [Lines 205-206](/tmp/jp-review-code/test_operation.py:205) invoke `unittest.main()` before `LauncherTest` is defined at line 209. Module-based discovery finds 22 tests, including six launcher tests; direct execution misses those six. **Fix:** move the main guard to the end.

2. **P2: Port-dependent tests are not isolated.** [Lines 231, 240, 249, and 273](/tmp/jp-review-code/test_operation.py:231) use fixed ports, causing failures when services or parallel test runs occupy them. The allocation tests also depend on real socket probes. **Fix:** mock availability for unit tests and use OS-assigned ports for actual socket tests.

3. **P1: The suite omits the critical operational invariants.** [Queue tests](/tmp/jp-review-code/test_operation.py:38) are sequential and do not exercise writer races, initialization, expiry, stale-owner fencing, or damaged logs. There are no lane tests for consent, CDP failure, click failure, dry-run state, or verification resumption. [Line 145](/tmp/jp-review-code/test_operation.py:145) explicitly endorses converting unspecified remote work to US eligibility. **Fix:** add deterministic concurrency tests and mocked lane failure-path tests; correct the location expectation.

No file is clean across the requested categories. I found no em dash characters or evident real applicant emails, external personal IPs, Sheet IDs, or resume paths in these six files. Loopback addresses are local connection defaults.

Twelve selected existing tests passed, and read-only targeted probes confirmed the failures noted above. The full offline suite could not run: the execution approval request failed, and the read-only sandbox prevented temporary-file creation. That infrastructure failure is not counted as a code defect.
tokens used
