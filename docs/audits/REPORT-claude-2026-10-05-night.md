I've finished reading all six files and I'm writing up the findings now. `hard_patterns.py`, `templates.py`, `map.py`, `code_gate.py`, `common.py` and `ats-notes.md` aren't in `audit-night/`, so claims about them are unverified.

# Audit findings

**Scope limit:** the claims about `hard_patterns.fill_react_select`, `templates.py`, `map.py`, `code_gate.py` and `model.chat()` come only from how `fill.py` calls them. The claims about `test_templates.py` and the `docs/ats-notes.md` references can't be checked at all.

## Blocker

1. **`multi-machine.md:141`: real username leaked.** `C:\Users\SUMMIT~1\...` is the maintainer's Windows account in 8.3 form. That is a personal identifier.
   - **Fix:** use a placeholder such as `C:\Users\USERNA~1\AppData\Local\Google\Chrome\USERDA~1`, and say to run `dir /x` to find the real short name.
   - Everything else I grepped was clean: no emails, phone numbers, spreadsheet IDs or personal URLs. `JOBPILOT_SSH_HOST="user@host"` is a placeholder.

## Major

2. **`AGENTS-root.md:123` and `:124-125`: stale local-model-first language.**
   - Line 123 calls `map.py` "the single model call", which still describes the old policy.
   - Lines 124-125 say `hard_patterns`/`templates` are "zero model calls; unknown fields are skipped". That contradicts the intro (lines 8-14) and the pipeline (lines 26-28), where unmapped fields go to the model.
   - **Fix:**
     - `map.py` — "orchestrates mapping; deterministic pass first, one strict-JSON model call only for unmapped or low-confidence fields".
     - `hard_patterns.py`/`templates.py` — "deterministic answers; no model calls inside these modules".
     - Reconcile this with safety invariant 1 ("no answer → `skip`").

3. **`AGENTS-root.md:12-16` and `:43-47`: model dependency is unclear.**
   - The text says the model is only a backup, but it also says `map.py` raises if the server is down, so "keep it up before a run". Setup step 3 says "if the model cannot do constrained JSON, stop".
   - A new contributor can't tell whether a fully deterministic form needs the server. If `map.py` still raises unconditionally, the "backup" framing is misleading. If it only calls the model when needed, the "raises" sentence is wrong.
   - **Fix:** state which it is, and soften "stop" to "needed only for unmapped fields".
   - `troubleshooting.md:121-124` ("No model available") has the same gap. It should say that all-recognized forms can run with the model down, if that is true.

4. **`multi-machine.md:197-201` contradicts the new Browser topology section (lines 236-252).**
   - The old section says "There is no one-browser-per-machine rule… as many instances as you need… one per lane (Indeed, ATS, code gates)". The new one says one browser per worker, two app browsers per machine, and a third only while fixing something.
   - **Fix:** merge the two sections or rewrite the old one to match the new rule.

5. **`multi-machine.md:175-187` contradicts `:249-252`.**
   - "Use the default Chrome profile… do NOT launch a second instance on the same profile" conflicts with giving every worker its own profile directory.
   - The default-profile note (`:189-193`) says recent Chrome ignores the debug port on the default data directory, yet the Windows playbook (`:282-284`) says to check `lockfile` before launching against the default profile.
   - **Fix:** say the default profile suits at most one browser, and every additional worker needs its own `--user-data-dir` signed into the same account.

6. **`multi-machine.md:164` conflicts with the Windows playbook (`:267-272`).** The headline Windows example runs `schtasks /create … /sc once /st 23:59 /f` inline, without `/IT`. The playbook says `/IT` is required for a visible session, via a `.cmd` file, and that the task should be deleted afterward.
   - **Fix:** make the top example match the playbook (`/it`, a `.cmd` wrapper, delete the task) or point to it.

7. **Overstated bug impact: `CHANGELOG.md:189-195` and `troubleshooting.md:170-175`.**
   - Both say the bug left "every react-select dropdown unfilled" and "silently skipped every fill". In `fill.py:320-360` the mismatch only skipped `fill_react_select`. Execution then fell through to `fill_generic_combobox` and the click+type+Enter path (`:353-364`), which returns `True, ""`. A fill was attempted, just not by the intended helper.
   - The "root cause of … failures previously misattributed to missing school lists" claim is also unverifiable from the repo.
   - **Fix:** say "bypassed `fill_react_select` and fell back to the generic click+type path, which often failed to select the option". Drop or qualify the root-cause sentence.

8. **`fill.py:395-403` breaks the rule the docs now state.** `greenhouse-quirks.md:37-38` says "always normalize `Runtime.evaluate` results to `.value`". The location-settle poll uses `cdp_ok(...)["result"]` un-normalized and tests `if v:`. If the dict claim in the `fill.py:336` comment is true, `v` is always truthy and the 12-second poll exits immediately.
   - Other call sites (`:287`, `:312`) treat `["result"]` as already parsed, so the comment's claim is itself uncertain. Check `common.cdp_ok`'s contract.
   - **Fix:** resolve the contract, then either normalize at line 399 or soften the docs' blanket rule.

9. **`greenhouse-quirks.md:22-27`: the `execCommand` claim isn't supported by `fill.py`.**
   - `fill.py` fills textareas with a native value-setter plus `input`/`change` events (`:45`), and uses `Input.insertText` elsewhere. There is no `execCommand`.
   - If `execCommand` lives in another module, name it. If not, reword it as guidance ("if a textarea doesn't stick, …"), not as a description of what the harness does.
   - The `fill.py` header docstring (`:10`) is also stale. It says select is "type exact option, Enter", but react-select now goes through `fill_react_select`.

## Minor

10. **Code-gate consistency.**
    - The core facts agree across `AGENTS-root.md`, `greenhouse-quirks.md` and `troubleshooting.md`: 8-character code, single-use, human-in-the-loop.
    - `greenhouse-quirks.md:80` and the changelog point to `docs/ats-notes.md`, which I couldn't check.
    - Details are spread out: "<40 minutes" appears only in `greenhouse-quirks.md`, and "no resend control; resubmit for a fresh code" only in `troubleshooting.md`. `AGENTS-root.md:89` just says "ask for a fresh code".
    - **Fix:** put the procedure in one place and link to it from the others. Add the resubmit tip and the 40-minute figure to `AGENTS-root.md`.

11. **Confirmation signals differ.** `AGENTS-root.md:63-66` accepts "URL and/or text". `greenhouse-quirks.md:40-46` requires the strong signal, "successfully been received" / "thank you for applying" or a `/confirmation` URL. AGENTS omits "successfully been received".
    - **Fix:** use the same phrase list in both.

12. **`multi-machine.md:223-234`: redundant and dated.**
    - The section repeats itself: it says to point controllers at `JOBPILOT_MODEL_URL` and that LM Studio/Ollama is the backup twice (lines 219-221 and 230-233).
    - The "(changed 2026-10-05; it was local-model-first before)" stamp belongs in the changelog, not the doc.
    - `:244-247` still lists model inference as a slow part. That is inconsistent with the new premise that the model is rarely called.
    - `test_templates.py` (`:234`) is mentioned nowhere else. The changelog's test file is `test_offline.py`. Verify it exists.

13. **CHANGELOG structure.**
    - There are two `### Added` and two `### Changed` headings under `[Unreleased]` (lines 17-47 and 49-end of the Unreleased block). Merge them.
    - The old entry at lines 53-57, "Local-models-first operation documented… one strict-JSON local-model call per form", now contradicts the new policy and sits in Unreleased.
    - **Fix:** rewrite it, or mark it superseded by the "Changed" entry.
    - The changelog also lists the troubleshooting additions as "the dropdown router bug note" and "silent-submit causes". That is fine, but mention "Browser topology" separately if you want it discoverable.

14. **Minor wording.**
    - `AGENTS-root.md:9` says "hundreds" while the other docs say "500+". Pick one.
    - `AGENTS-root.md:8-12`: "one strict-JSON call per form" reads as always happening. Say "at most one … and only if any field needs it".

**Verdict: CHANGES REQUESTED.** Fix item 1 first (the leaked username). Items 2-9 are accuracy and consistency problems a new contributor would trip over.
