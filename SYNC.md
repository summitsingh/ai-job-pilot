# Syncing the private harness to public jobpilot

The public jobpilot repo is a scrubbed snapshot of a private ATS harness
used for real job applications. The private harness keeps evolving. This
document describes how to sync improvements back without leaking personal
data or regressing the public generalizations.

## When to sync

After any meaningful change to the private harness: new field patterns,
bug fixes, new ATS quirks documented, improved fill primitives. There is
no automation; sync is a deliberate, review-driven act.

## The process

1. Run `./sync-from-private.sh /path/to/private/harness`. It copies shared
   files to a temp dir, flags env-var transformations, enforces the
   exclusion list, runs the offline tests, and prints a diff summary.
2. Review each diff. The public files have diverged (generalized env vars,
   removed machine-specific paths, rewritten docstrings). Preserve the
   public generalizations; port only the logic changes.
3. Apply the env-var transformation to any new `ATS_*` references:
   `os.environ.get("ATS_X"` becomes
   `os.environ.get("JOBPILOT_X", os.environ.get("ATS_X"`.
   `JOBPILOT_*` is primary; `ATS_*` is honored as a legacy alias.
4. Copy the merged files into this repo, run `python3 -m unittest
   test_offline`, and verify the diff contains no personal data
   (see checklist below).
5. Commit and push.

## Exclusion list

These private-harness files must NEVER go public:

| File | Reason |
|---|---|
| `facts.json` | Personal applicant data (name, email, phone, work history) |
| `linkedin_dump.py`, `linkedin_fill.py` | LinkedIn automation modules (account-flag risk, ToS optics) |
| `log_application.py` | Personal Google Sheet logger (hardcoded Sheet ID) |
| `BUILD_LOG.md` | Personal development log |
| `amd_cdp.py` | Machine-specific CDP driver (replaced by generic `cdp_driver.py`) |
| `*.pre-wire-backup`, `__pycache__` | Cruft |

## Pre-push checklist

- [ ] No personal names, emails, phone numbers, or addresses in any file
- [ ] No hardcoded SSH hosts, IPs, or machine-specific paths
- [ ] `facts.json` is not tracked (`git check-ignore facts.json` should succeed)
- [ ] All `ATS_*` env references have `JOBPILOT_*` primary names
- [ ] `python3 -m unittest test_offline` passes
- [ ] `CHANGELOG.md` has an entry under `[Unreleased]`

## Public-only files

These exist only in the public repo and are never synced from private:

- `batch_apply.py`, `code_gate.py` (built for the public release)
- `AGENTS.md`, `CONTRIBUTING.md`, `docs/`, `examples/`, `demo/`
- `pyproject.toml`, `facts.example.json`, `LICENSE`
- `test_offline.py` (public test suite; private has its own test files)
