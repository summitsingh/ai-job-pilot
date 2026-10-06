I've finished the sweep and am writing up the report now. Git history couldn't be inspected because the harness denied `git` commands.

# jobpilot pre-push audit

## 1) SECRETS SWEEP

**Findings in the already-pushed tree (`/home/summit/jobpilot`):**

| # | Location | Snippet | Status |
|---|---|---|---|
| 1 | `test_offline.py:114` | `matches("***REMOVED***", "***REMOVED***")` | Looks like a real phone number. The changed version replaces it with `5551234567` / `(555) 123-4567`. |
| 2 | `docs/ats-notes.md:94-95` | ``***REMOVED***` vs `***REMOVED***` `` | Same number. The changed version (lines 99-100) is clean. |
| 3 | `pyproject.toml:12` | `authors = [{ name = "Summit Singh Thakur" }]` | Real personal name. |
| 4 | `LICENSE:3` | `Copyright (c) 2026 Summit Singh Thakur` | Same name. This is normal for an MIT licence, but your rules say to flag it, so please confirm it's intended. |
| 5 | `test_indeed.py:10-11, 22-23` | `mac-mini Chrome (port 19446)`, `ATS_HOST_SSH=~/workspace/bin/macmini-ssh` | Machine-specific hostname and script path. `SYNC.md` says to scrub these. |
| 6 | `test_templates.py:7` | `indeed x2 via mac-mini lane` | Machine-specific reference. |
| 7 | `fill.py:630` | `saved on the browser host in the amd lane` | Reference to the private `amd_cdp` machine. |
| 8 | `model.py:137, 169, 171` | `~/.lmstudio/bin/lms ...` | Generic LM Studio default path. Fine, no action. |

**Findings in the changed files:**
- `docs/ats-notes.md:73`: "live NALA Senior Platform Engineer posting". This names an employer the owner tested against. It's low risk, but it shows which company they were looking at, so decide whether to keep it.
- No other findings in the changed files. Every IP is `127.0.0.1`. The phones are `555…`. There are no emails other than `alex.carter@example.com`, and no tokens, passwords, Bitwarden references, `/home/` paths or 100.x addresses.

**Not findings:**
- `examples/resume-map.json` uses `/home/user/...` placeholders.
- `facts.example.json` is fully placeholder.
- The demo cast (`demo/demo.cast`) is clean.
- The public job URLs in the test files and the `summitsingh` repo URLs are fine.
- No API keys or secrets turned up anywhere. The grep hits for "token" and "password" were all unrelated: LLM `max_tokens` and "never invent passwords" comments.

**Git history.** The tree is "last pushed", so the real phone number (findings 1-2) may already be in public history. Replacing the files in this update doesn't remove it from earlier commits. I'd check with `git log -p -S***REMOVED***` before pushing, and treat the number as exposed if it appears.

## 2) CODE REVIEW

**`test_offline.py`:** no issues.
- The change is correct and complete. Line 97 expects `5551234567`, which matches `facts.example.json`.
- Line 114 now tests `5551234567` against `(555) 123-4567`, which still exercises the digit-stripped comparison.
- No other real personal data in the file: every URL is `*.example`, every path is under `/tmp`, and the name and email are `Alex Carter` / `alex.carter@example.com`.

**`docs/multi-machine.md`:**
- Accuracy: the "macOS / Linux" code block uses the macOS `/Applications/...` binary path only. Add a Linux variant such as `google-chrome`, or relabel the block "macOS".
- Accuracy: "Launch Chrome WITHOUT `--user-data-dir`" will not work on current Chrome (136 and later). Those versions ignore `--remote-debugging-port` on the default profile, so the advice needs a caveat or an alternative. I'm going from memory on this version cutoff, not from a check.
- Safety: `--remote-allow-origins='*'` combined with the real default profile (Gmail and ATS logins) deserves a one-line warning to keep the debug port on localhost or an SSH tunnel.
- The `schtasks` command, the `curl .../json/version` check and the `nohup` syntax look right.
- No personal data.

**`docs/troubleshooting.md`:**
- "see `demo/` for the format" of a hand-written map is inaccurate. `demo/` only contains `demo.cast`, so point to `docs/` or an example map instead.
- `git rm --cached facts.json` is correct.
- No personal data.

**`AGENTS.md`:**
- The Chrome command in step 2 puts a `\` line continuation inside inline backticks. It's cosmetic, but it renders badly, so use a fenced block.
- The new cross-reference to `docs/multi-machine.md` is valid.
- No personal data.

**`CHANGELOG.md`:** no issues.
- It's consistent with the new docs.
- The `0.1.0` entry says "11 unit tests" and the suite has since grown, but that entry is historical.
- No personal data.

**`docs/ats-notes.md`:**
- The phone example is now properly placeholder.
- See the NALA note in section 1.

## 3) EXCLUSIONS CHECK

**PASS.** A glob and find across `/home/summit/jobpilot` and `/home/summit/jobpilot-audit/changed` found none of `facts.json`, `linkedin_dump.py`, `linkedin_fill.py`, `log_application.py`, `BUILD_LOG.md`, `amd_cdp.py` or any `*.pre-wire-backup`.
- The only `*.pre-wire-backup` files on the machine are in `/home/summit/job-harness/harness/` and `/home/summit/harness-run/harness/`, which are outside the audited trees.
- `SYNC.md` and `sync-from-private.sh` mention the excluded filenames as text only.

VERDICT: BLOCKED - the old real phone number `***REMOVED***` is in the pushed tree's `test_offline.py` and `docs/ats-notes.md` and may be in git history, the real name and `mac-mini` / `macmini-ssh` references are still in `LICENSE`, `pyproject.toml`, `test_indeed.py`, `test_templates.py` and `fill.py`, and git history was not inspected. The new and changed files themselves are clean. Once those items are resolved, or the name and the other references are confirmed as intended, it's safe to push.
