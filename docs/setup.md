# Setup wizard

Run `ai-job-pilot-setup` after installing, or `python3 setup_wizard.py`
from the repo. The wizard checks setup, writes a non-secret `config.json`,
and leaves applicant facts to you.

1. Choose the browser count and base port, defaulting to 2 lanes starting
   at 9445. Allocation reuses the launcher checks. Browsers launch only
   after an explicit `yes` in interactive mode, and startup is verified.
2. Choose queue and claims paths, defaulting next to the script. Missing
   files are initialized as an empty JSON queue and empty claims log.
   Existing files are kept, including non-empty queues, even with `--force`.
3. Choose `jsonl`, `sheets`, or `notion`, comma-separated for multiple
   backends. Sheets accepts a spreadsheet URL or ID plus a service-account
   file. Notion accepts a token plus a database URL or ID.
4. Credential checks report missing values, malformed IDs, or a missing
   service-account file without aborting the rest of setup.
5. The wizard checks `JOBPILOT_FACTS`, defaulting to `facts.json`. It must
   exist, parse as JSON, and contain an object. Example-looking facts
   produce a warning. Missing or invalid facts stop setup before writing:
   run `cp facts.example.json facts.json` and fill in real details.

Optional prompts accept `skip`. Queue initialization can be skipped;
browser launching defaults to no. Facts validation always runs and never
creates or invents facts. Existing config requires `--force` before it can
be overwritten. `--directory` selects a different setup directory;
`JOBPILOT_CONFIG` can select the config file.

## Settings and secrets

The wizard writes only `JOBPILOT_CDP_URL`, `JOBPILOT_QUEUE`,
`JOBPILOT_CLAIMS`, `JOBPILOT_LANE`, `TRACKER_BACKENDS`, and, when set,
`JOBPILOT_TARGET_METROS`. Environment variables override answers and
config defaults, matching `config.py`.

Credentials are never saved to config.json. The wizard prints shell-safe
`export` lines for `GOOGLE_APPLICATION_CREDENTIALS`, `TRACKER_SHEET_ID`,
`TRACKER_NOTION_TOKEN`, and `TRACKER_NOTION_DB` when their backends are
chosen. Put these lines in your private shell profile and load it before
running a lane. Spreadsheet and database URLs are normalized by the
tracker's existing parsers. Keep credential output private.

## Non-interactive setup and preview

```bash
python3 setup_wizard.py --yes --dry-run
python3 setup_wizard.py --yes
python3 setup_wizard.py --yes --force
```

`--yes` uses defaults without stdin or browser launches. With a fresh
config, defaults are two lanes at base port 9445 and the JSONL tracker.
`--dry-run` prints the browser/config plan and credential exports without
writing any files or launching browsers. Facts checks and config
overwrite protection still apply.

After setup, start the model backup and browsers as described in
[AGENTS.md](../AGENTS.md), then review one application with
`python3 lane_greenhouse.py --workdir /tmp/jobpilot/lane1 --dry-run`.
It prints the exact answer map and saves `dryrun.png` before stopping.
Consent always requires explicit approval per application, location and
validity checks still block unsafe runs, and the email code gate remains
a human step. See [the queue protocol](queue.md) for heartbeats and code
coordination.
