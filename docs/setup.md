# Setup wizard

Run `ai-job-pilot-setup` after installing, or `python3 setup_wizard.py`
from the repo. The wizard checks setup, writes a non-secret `config.json`,
and leaves applicant facts to you.

1. Choose the browser count and base port, defaulting to 2 lanes starting
   at 9445. Allocation reuses the launcher checks. Browsers launch only
   after an explicit `yes` in interactive mode, and startup is verified.
2. Choose queue and claims paths, defaulting to the current working directory. Missing
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
be overwritten in a real run; `--dry-run` previews the replacement without
`--force` and reports that it would overwrite. `--directory` selects a
different setup directory;
`JOBPILOT_CONFIG` can select the config file. The default directory is your
current working directory, including after pip installation. When the written
config differs from `config.py`'s default path next to the scripts, the wizard
prints `export JOBPILOT_CONFIG=<absolute path>`; load that export before running
the tools so they find the config.

Configure applicant locations explicitly with `JOBPILOT_TARGET_METROS`, for
example `Austin, TX; Remote (US)`. The default is empty. Lane startup validates
this setting before claiming a job and stops if it is empty or malformed.

## Settings and secrets

The wizard writes only `JOBPILOT_CDP_URL`, `JOBPILOT_QUEUE`,
`JOBPILOT_CLAIMS`, `TRACKER_BACKENDS`, and, when set,
`JOBPILOT_TARGET_METROS`. It writes `JOBPILOT_LANE` only when explicitly
provided by the environment or existing config, leaving each process to
generate its own default lane identity otherwise. Environment variables override answers and
config defaults, matching `config.py`.

Credentials are never saved to config.json or printed raw to stdout. Export
lines for `GOOGLE_APPLICATION_CREDENTIALS`, `TRACKER_SHEET_ID`,
`TRACKER_NOTION_TOKEN`, and `TRACKER_NOTION_DB` use `<your value>` placeholders,
including when `--yes` reads the values from your environment. Replace them
privately in your shell profile. Model settings (`JOBPILOT_MODEL_*`,
`JOBPILOT_API_FLAVOR`, `JOBPILOT_BACKUP_MODELS`) and tracker credentials/options
(`TRACKER_*` except `TRACKER_BACKENDS`, plus `GOOGLE_APPLICATION_CREDENTIALS`)
are environment-only; putting them in `config.json` has no effect on those
consumers. `config.example.json` lists these names in an ignored comment key.
Spreadsheet and database URLs are normalized by the tracker's parsers; published Sheets `/d/e/` URLs are rejected.

## Non-interactive setup and preview

```bash
python3 setup_wizard.py --yes --dry-run
python3 setup_wizard.py --yes
python3 setup_wizard.py --yes --force
```

`--yes` uses defaults without stdin or browser launches. With a fresh
config, defaults are two lanes at base port 9445 and the JSONL tracker.
`--dry-run` prints the browser/config plan and credential exports without
writing any files or launching browsers. Facts checks still apply. Existing
config is shown as a planned overwrite without requiring `--force`.

Real setup publishes config atomically after queue initialization and requested
browser launches succeed. Failed initialization or startup preserves the old
config. Temporary config files are removed if serialization or publication
fails. Queue files initialized or browsers started before a failure may remain;
existing queue and claims files are kept.

Model telemetry contains metadata only, with no prompt or response text. It
rotates at 5 MiB and keeps two prior generations. Rotation is synchronized
within one process only; there is no cross-process lock, so use a separate
`JOBPILOT_MODEL_TELEMETRY` path for each concurrent process. Zero, negative,
non-finite or invalid wall budgets use the 300-second default for both the
environment setting and an explicit `wall_timeout` argument.

After setup, start the model backup and browsers as described in
[AGENTS.md](../AGENTS.md), then review one application with
`python3 lane_greenhouse.py --workdir /tmp/jobpilot/lane1 --dry-run`.
It prints the exact answer map and saves `dryrun.png` before stopping.
Consent always requires explicit approval per application, location and
validity checks still block unsafe runs, and the email code gate remains
a human step. See [the queue protocol](queue.md) for heartbeats and code
coordination.
