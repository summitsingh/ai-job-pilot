# Documentation audit: ai-job-pilot

I read all seven files. I did not have the repo's code, so I could not check commands or flags against `*.py`. Each flag, path or script behavior below is checked against the docs only.

## Cross-cutting findings

**1. The consent guardrails are missing from every file.** `grep` for consent, arbitration, certif and background finds zero hits in all seven files. The invariants you asked me to protect are not stated anywhere in the audited docs.
- `AGENTS.md` "Safety invariants" lists 7 rules (no invented facts, demographics, work authorization, CAPTCHA, blocklist, and others). None covers arbitration agreements, certifications/attestations, or background-check authorizations needing explicit human approval.
- `README.md` "Safety" and "The two human steps" don't cover them either.
- `README.md` step 3 says the deterministic layer answers "work authorization, demographics, salary expectations, ... yes/no willingness questions". A reader will assume it also auto-answers "I agree to arbitration" and "I certify the above is true".
- Fix: add an invariant to `AGENTS.md`, a bullet to the `README.md` Safety section, and a line to the `sweep.md` and `queue.md` lane flow. Suggested wording: "Arbitration agreements, certifications/attestations, and background-check authorizations are never auto-answered. The run stops and asks the human for explicit approval per application. `--no-submit` review does not substitute for this."
- If the code already enforces this, the docs must say so. If it doesn't, that is a bug rather than a doc gap.

**2. Em dashes are present, which violates your rule.**
- `AGENTS.md` line 1 and about 19 Layout lines (127-153).
- `CHANGELOG.md` line 76.
- Fix: replace `-` with `:` or `-` throughout.

**3. Unattended lanes conflict with the human-approval claims.** This is the most serious inconsistency.
- `README.md` Safety says "Human approval before every submit" and "The two human steps (all platforms)" says the agent dry-runs first and you approve.
- But `lane_greenhouse.py` (`README.md` "Running the full operation", `CHANGELOG.md`, `queue.md`) goes claim → fill → `checkValidity` → submit → track. No dry-run or approval step is documented.
- `README.md` step 3 runs the lane with no `--no-submit` or approval flag.
- Either the lane has an approval gate that is undocumented, or "approval before every submit" is false for lane mode. This overpromises on safety.
- Fix: document the lane's actual gate. If there isn't one, say so, and add a `--no-submit` or approval flag to the `README.md` and `machine-setup.md` examples.

**4. Env var naming and the `config.json` claim differ between docs.**
- `README.md` lists `JOBPILOT_*` and `TRACKER_*` variables and says "Legacy `ATS_*` names ... are also honored". The CHANGELOG title and `AGENTS.md` use "jobpilot" while the repo is "ai-job-pilot". The `/tmp/jobpilot/` and `$HOME/jobpilot/` paths are fine, but the naming is inconsistent.
- `README.md` and `AGENTS.md` say `config.example.json` is copied to `config.json` (gitignored). No doc says which scripts read `config.json`, or how it interacts with env vars (precedence).
- Fix: state the precedence rule, e.g. "env var overrides config.json overrides default".

**5. Test counts conflict.**
- `README.md` says "18/18 offline tests" in two places (`test_offline.py`).
- `CHANGELOG.md` says `test_offline.py` has 11 tests (0.1.0) and `test_operation.py` has 22.
- `README.md` "Layout" omits `test_operation.py` entirely, while `AGENTS.md` lists it.
- Fix: reconcile the counts, say how to run the tests (no command is given anywhere), and add `test_operation.py` to the `README.md` Layout.

## tracking.md

1. **Doesn't work as written:**
   - The CLI example uses `--backend jsonl`. The `README.md` example uses `--backend jsonl,sheets`. The text says `--backend` is a single choice or comma-separated. Say clearly that comma-separated is accepted.
   - Sheets setup never says to enable the Google Sheets API on the Cloud project. A service account without it returns a 403 that confuses people.
   - `pip install google-api-python-client google-auth` is step 4, after the env vars. Move it before first use.
   - Step 4 also contradicts `README.md` "The only dependency is the standard library".
2. **Inconsistency:** Sheets "header row is the field list above, in order". The Notion property list has no Sponsorship, EEO, Resume or Lane. It lists Title, Company, Location, Salary, Source, ATS, URL, Status, Date, Confirmation and Notes. That is 11 properties against 15 record fields, so 4 fields are dropped. Either state that or add them.
3. **Missing prerequisite:** the sheet needs the header row to exist before the first append. This is easy to miss. Say "create the header row manually".
4. **Overpromise:** "Tracking failures never lose an application: `lane_greenhouse.py` logs tracker errors" only covers the lane. `batch_apply.py` writes `applications.jsonl` via a different path. Also, `batch_apply.py --dedup` reads `applications.jsonl`, while `sweep.py` and `tracker.py` use `--jsonl-path`. These are three different dedup sources. Say they are the same file and the default path.
5. **Unclear:** `--from-json result.json`. A lane's `result.json` has `{status, confirmation_evidence, fields_filled, fields_skipped, notes}`, which has no company, title or URL. Say which fields must be added, otherwise the example fails.
6. **Notion:** Confirmation and Notes are given with no type. State rich_text. `TRACKER_NOTION_TOKEN="ntn_..."` is fine as a placeholder.

## queue.md

1. **Doesn't work as written:**
   - `queue.py` is described as a CLI (`next`, `claim`, `mark`, `release`), but `README.md` Install lists seven console scripts and none is a queue command. Say it is run as `python3 queue.py ...` and only from the repo directory.
   - A top-level `queue.py` shadows the stdlib `queue` module for any script in that directory. If stdlib `queue` is imported anywhere (e.g. `concurrent.futures`, `logging.handlers`), it breaks. Verify this, and if confirmed, rename it or warn.
   - `queue.py claim` has "gets nothing back" with no exit code or output stated. Define it so lanes can script against it.
2. **Inconsistency:**
   - The claims log field is `status: in-progress`, but `released` is also used. `live_claims` says "in-progress and younger than 6 hours". "Tunable in code" gives no variable name or env var. Name the constant.
   - The doc doesn't mention `JOBPILOT_QUEUE`, `JOBPILOT_CLAIMS` or `JOBPILOT_LANE`, which `machine-setup.md` and the README config table use. Add a "Configuration" line.
   - The claim `{"url","lane","claimed_at","status"}` has no `reason`, but `mark --reason` is documented. Say where the reason is stored (`skip_reason` in `queue.json`?).
3. **Overpromise / safety:** "lets multiple lanes share one job queue without double-applying" is too strong. The flock covers one POSIX host. The doc already hedges for Windows, but a shared-filesystem (NFS/SMB) topology across `<MACHINE_A>` and `<MACHINE_B>` is exactly what `machine-setup.md` shows, and `flock` is unreliable there. State that cross-machine lanes need a single host owning the files, or that the file lock isn't safe over a network share. The CHANGELOG mentions "single-owner dedup across machines", which is the real rule.
4. **Unclear:** step 4 says release keeps the job `pending`. But the Greenhouse code-gate path (CHANGELOG) "releases its claim and exits". Say that explicitly, since the next lane will pick the job up again and could trigger a second submit and a second code email. Add a rule: a submitted-but-unconfirmed job must be marked, not released.
5. The `docs/machine-setup.md` reference is correct.

## sweep.md

1. **Doesn't work as written:**
   - `--applied applications.jsonl` and the `tracker.py` default path may differ. The `tracking.md` example uses `./applications.jsonl`, so they probably match. Say so.
   - The default `--queue` isn't stated. Dry-run is documented as `--in raw.json --dry-run` with no `--queue`, so the default exists but is not named.
   - The Greenhouse API URL is right (`boards-api.greenhouse.io/v1/boards/<board>/jobs`), but it returns no description unless you add `?content=true`. Without content, filter step 3 (sponsorship language) sees nothing and silently passes everything. This is a real safety-adjacent trap. Document `?content=true`.
2. **Overpromise / hard-coded policy:**
   - Filters are not configurable via flags other than `--min-salary`. The title filter requires senior/staff/lead/principal AND backend/platform/infra/AI/ML, and the location allowlist is "Seattle, Bay Area, LA, NYC, Austin" (a personal preference). A new user will find their jobs all rejected. Say these are defaults hard-coded in `sweep.py`, name the constants, and point to the pure functions as the tuning surface. The doc says tuning is safe but doesn't say where to edit.
   - "Rejects ... manager-track titles" but accepts "lead". These overlap ("Engineering Lead"). Clarify.
   - Sponsorship filter: "rejects postings whose text rules out visa sponsorship". This only matters if the user needs sponsorship. Say it is a filter for sponsorship-needing users and can be disabled otherwise. Right now it reads as universal policy.
   - Undisclosed salary passes. That is fine, but the doc should say so next to the floor's purpose.
3. **Inconsistency:** the funnel diagram says `lane_greenhouse.py` works each job, but the queue holds Ashby, Lever and other jobs too (`"ats"` field, `machine-setup.md` lane table). There is no documented lane for them. Say what happens to non-Greenhouse jobs, e.g. that they are worked via `ai-job-pilot` or `batch_apply.py`, and how to export from the queue to `queue.txt`.
4. **Unclear:** "Remote US, plus neighbors" in step 2 is undefined.
5. The Ashby URL `api.ashbyhq.com/posting-api/job-board/<board>` is correct. Add `?includeCompensation=true` if salary matters.

## machine-setup.md

1. **Doesn't work as written:**
   - Rule 3 says use the default Chrome profile. The manual recipe uses `--user-data-dir="$HOME/.chrome-jobpilot-9445"`, a fresh non-default profile. The rule also says temp/fresh profiles lose sessions. This is a direct contradiction. Also, Chrome 136+ ignores `--remote-debugging-port` when launched on the default user data directory, so rule 3 can't work on current Chrome anyway. Rewrite the rule as "persistent dedicated profile, sign in once".
   - Rule 1 says one profile per lane, which contradicts rule 3 as written ("the default profile"), since two lanes can't share it.
   - The section "macOS launch" is the only one with the launcher. `launch_browsers.py` is documented elsewhere as covering macOS, Windows and Linux. There is no Linux section, and the generic `google-chrome` recipe lives in the `README.md` and `AGENTS.md` (port 9226). Add one, or retitle the section "Launch (macOS/Linux)".
   - `cdp_direct.py ... goto <base64-url>`: the base64 requirement is not explained. Show the encoding command (`echo -n URL | base64`).
   - Windows `schtasks ... /st 23:59 /it` with `/sc once` fails if the time has already passed in some locales. Minor. The 8.3 path example `C:\Users\RUNNER~1\...` contains an account-like name. Change it to `<USER~1>`.
   - `--remote-allow-origins='*'` plus the debug port is a security exposure on a multi-machine setup. Add a warning to bind to 127.0.0.1 or use an SSH tunnel. Port 9445 reachable from `<MACHINE_B>` is not mentioned. The `JOBPILOT_CDP_URL="127.0.0.1:9445"` example only works on the same machine as the lane, so say how a remote browser is reached.
2. **Inconsistencies:**
   - Ports are 9445/9444/9337/9336 here and 9226 in the `README.md` and `AGENTS.md` quick starts. Either explain that 9226 is the single-run default, or align.
   - `launch_browsers.py --lanes 2 --base-port 9445`: the table gives lane ports 9445 and 9444 (descending), but a base port normally ascends. State whether lanes go up or down from base. The docs also say lane 1 = Greenhouse (9445), lane 2 = other (9444), which implies descending. Confirm.
   - `README.md` and the CHANGELOG say `launch_browsers.py` is also exposed as `ai-job-pilot-launch`. Use one form consistently.
   - The "Env wiring" block sets `JOBPILOT_LANE="<MACHINE_A>:9445"`, while `README.md` uses `"machine-a:9445"`. Fine, but the placeholder style should match.
   - `JOBPILOT_MODEL_URL="http://<MODEL_HOST>:1234"` and `<MODEL_HOST>` are not in the topology table, but the closing note correctly names them.
3. **Missing prerequisites:**
   - `$HOME/jobpilot/facts.json` is set but never created. Link back to `cp facts.example.json`.
   - The `--workdir /tmp/jobpilot/lane-9445` directory: say whether it is auto-created, and note that `/tmp` is cleared on reboot, which loses screenshots and `result.json`.
   - The Greenhouse code gate needs a human at the keyboard. `machine-setup.md` never mentions that headless operation of lanes can't clear it. The `README.md` mentions it. Add a cross-reference.
   - `docs/greenhouse-quirks.md` is cited but has no listed path in the README Layout. It exists in the CHANGELOG, so add it to the README Layout.
4. **Overpromise:** "proven shape" and "violating any of them caused real failures" are fine. "Headless ... bot detection flags it" is stated as fact. OK as experience, but soften it.
5. Visible and tiled: no command for tiling. The CHANGELOG mentions CDP `setWindowBounds`. Link it.

## README.md

1. **Doesn't work as written:**
   - Install says `pip install .` provides seven console scripts, but also says "skip the install and run the scripts directly". Then the "Running the full operation" section uses `python3 sweep.py` etc. The CHANGELOG 0.1.0 packaging entry lists only three scripts. Make sure `pyproject.toml` has all seven, otherwise `ai-job-pilot-lane` etc. fail.
   - `ai-job-pilot-batch --queue examples/queue.txt` only works from the repo directory after a pip install. State that.
   - Quick start sets `JOBPILOT_SSH_HOST="user@browser-host"`. The user is then told Chrome is "on the browser host". For a single-machine user, the direct-mode `JOBPILOT_CDP_URL` path is simpler and should be first. `AGENTS.md` says the same thing, so this is consistent but backwards for beginners.
   - Quick start uses `--port 9226` but never says to start Chrome first. "Browser setup" comes ~200 lines later. Add a forward reference or move it up.
   - The Claude Code install copies `SKILL.md`, but `SKILL.md` isn't in any Layout list or the audited files. Confirm that it exists.
   - Codex section "cd ai-job-pilot && cp facts.example.json facts.json" is fine.
   - `asciinema play` needs asciinema installed. List it as optional.
2. **Inconsistencies:**
   - Intro and `AGENTS.md` list Greenhouse, Ashby and Lever. The README also says Workable. `AGENTS.md` line 3 omits Workable. The README Safety section and `AGENTS.md` safety invariants differ too: the README adds "no account-gated ATS". Align the platform lists.
   - README's "Proven on" says "Workable: 7/9 fill actions verified ... 2 failures", while the top claim is "~50x fewer LLM calls and ~99% fewer tokens". The 50x/99% figures are unsourced in these docs.
   - The README says the model is "the only LLM call in the entire pipeline" ("Map (one model call)", "This is the only LLM call"). This contradicts the deterministic-first change (CHANGELOG, `AGENTS.md`), where the model is a backup that is skipped when templates cover everything. Step 2 is also ordered before step 3 ("Deterministic screening answers"), which inverts the actual order. Rewrite: step 2 = deterministic templates first, step 3 = model only for unmapped fields. The heading "The one hard rule" paragraph also says "the map prompt instructs skip" while the templates fire first. Reorder it.
   - "The Ashby and Lever templates cover 82/82 fields ... zero model calls" is consistent with deterministic-first, but it conflicts with step 2's wording.
   - The config table says `JOBPILOT_MODEL_NAME` default `qwen/qwen3-coder-next`, while `AGENTS.md` says "Qwen3-Coder is the validated model family". Fine.
   - `JOBPILOT_SSH_HOST` is "(required)" in the table but not required when `JOBPILOT_CDP_URL` is set. Fix: "(required unless JOBPILOT_CDP_URL is set)".
   - The README says `ai-job-pilot-batch` prompts for per-application confirmation. `AGENTS.md` agrees.
   - The README says Greenhouse codes are "8-character". Consistent with the other docs.
3. **Overpromise / safety:**
   - "The harness never invents facts ... the fill step refuses to write values with no source" is stated as a three-level enforcement. I can't verify it from the docs. The statement "Human approval before every submit" conflicts with lane mode (see cross-cutting #3).
   - "Live submits: confirmed via /confirmation URL" plus "500+ submitted applications" (CHANGELOG, `AGENTS.md`) is unverifiable marketing in a public repo and could embarrass if the figure is wrong. Consider removing or sourcing it.
   - Step 3 lists "work authorization" among the auto-answers. That is OK per invariant 4, but it should say "from facts.json only", plus the consent exclusions.
   - Batch mode: "completed applications are not repeated" is too strong. Dedup is by URL only, so a re-posted job or different URL for the same role will be re-applied.
4. **Missing prerequisites:** Python 3.9+ is stated. Chrome install, a model server, and a Google account are not listed as a prerequisites checklist. Pip-installed users of Google Sheets need `google-api-python-client`, which contradicts "only dependency is the standard library". Note "except optional Sheets backend".
5. **Placeholders:** `machine-a:9445` is lowercase with no angle brackets. Change it to `<MACHINE_A>:9445` for consistency. `user@browser-host` is acceptable.
6. "Muse", "Hermes / OpenClaw" platform instructions are unverifiable from here. Skip.

## CHANGELOG.md

1. **Inconsistencies:**
   - There are two `### Added` sections under `[Unreleased]` (lines 8 and 75), split by `### Changed`. Merge them.
   - The title says "jobpilot" while the repo and package are `ai-job-pilot`.
   - The `[0.1.0]` section says "11 unit tests" and the README says 18. Possibly correct if tests were added later, but that is not recorded under Unreleased.
   - The packaging entry says `pip install .` provides three console scripts, while the README says seven. The new four are not noted under Unreleased "Packaging".
   - README says "Workable" is supported, but no CHANGELOG entry records Workable support.
   - The struck-through "~~Local-models-first operation documented~~" entry is clutter in a public changelog. Delete it.
   - `docs/multi-machine.md` and `docs/machine-setup.md` overlap heavily (browser topology, never headless, Windows schtasks). The README links only `machine-setup.md`, and `AGENTS.md` links only `multi-machine.md`. Two docs for one topic will drift. Merge them or cross-link.
2. **Em dash** on line 76 (fix as noted).
3. **Safety wording:** the "Changed" entry says the model "backs up unmapped or low-confidence fields only". That is accurate. Add a note that guard-skips (consent, unknown facts) never reach the model. The entry does say "guard-skips are never sent to the model", which is good. This is the one place the invariant is represented correctly. Keep it, and reference it from the docs.
4. "500+ submitted applications of proven field data" is repeated here and in `AGENTS.md`. See the overpromise note above.
5. The Fixed entry is clear and has no personal data. Clean.

## AGENTS.md

1. **Doesn't work as written:**
   - `batch_apply.py --queue urls.txt` is fine, but the lane workflow (`lane_greenhouse.py`, `queue.py`) is listed only in Layout with no run instructions. An agent told to "read AGENTS.md" can't run the operation layer. Add a short section or link to `docs/queue.md`.
   - The "one command" example uses `--workdir /tmp/jobpilot/run1`. The lock file `browser-<port>.lock` location is not stated (workdir or cwd).
   - `ats_fill.py ... --force` is mentioned only here. Say what `--force` does to safety (it bypasses the lock, which is how two fills on one port happen).
2. **Safety invariants are incomplete and one is inconsistent:**
   - Missing the consent invariant (cross-cutting #1). This is the most important fix.
   - Invariant 7, "One application per run. Do not parallelize submissions to the same employer", is in tension with multi-lane operation. Clarify: lanes may run in parallel across employers, never the same employer, and the queue does not enforce the same-employer rule (it dedups by URL only). Add enforcement or a warning.
   - Invariant 4 says work authorization comes from facts.json only. Good. Extend it to "same for sponsorship, salary, and consent items".
   - Add: never click submit without the human approval from the `--no-submit` review, including in lane mode.
3. **Inconsistency:**
   - The "Deterministic first" paragraph is accurate, but "which raises if the server is unreachable, so keep the model server up before a run" contradicts "(only if any field needs it)" and the changelog line "The model server is probed only when the backup path is actually needed". Fix: "raises only if a field needs the model and the server is unreachable". As written, an agent will block on a down server unnecessarily.
   - Setup step 3 says "If the model cannot do constrained JSON, the unmapped fields are skipped". The Failure modes section says "Model returns invalid JSON repeatedly ... Switch models". One says skip, one says fix it. Reconcile: skip and report.
   - Platforms: line 3 omits Workable (README includes it).
   - Setup step 5 says `resume_path` must exist on the browser host. With `JOBPILOT_RESUME` and `--resume-map` (CHANGELOG, README) it is the same requirement, but it is not mentioned there. Add it to the README batch-mode note.
4. **Em dashes:** about 20 (fix as noted).

## Summary

| File | Verdict |
|---|---|
| `tracking.md` | Needs fixes (Sheets API enablement, dependency contradiction, `--from-json` example, dropped Notion fields) |
| `queue.md` | Needs fixes (consent/code-gate release rule, `queue.py` stdlib shadowing, cross-machine `flock` claim) |
| `sweep.md` | Needs fixes (`?content=true` trap, hard-coded personal policy not flagged, non-Greenhouse jobs have no lane) |
| `machine-setup.md` | Needs fixes (default-profile rule contradicts the recipe, CDP exposure, port ordering) |
| `README.md` | Needs fixes (the "only one LLM call" contradiction, human-approval claim vs. lane mode, no consent guardrails) |
| `CHANGELOG.md` | Minor (duplicate `### Added`, em dash, stale struck-through entry, script count) |
| `AGENTS.md` | Needs fixes (no consent invariant, em dashes, model-server wording) |

No real emails, IPs, hostnames, Sheet IDs or resume paths appear in any file. The only IPs are `127.0.0.1`, which is fine. The `RUNNER~1` fragment in `machine-setup.md` looks like a real account name, so replace it with a placeholder.

The three items to fix before publishing are: the missing consent invariant, the lane-mode approval gap, and the README's "only LLM call" claim.
