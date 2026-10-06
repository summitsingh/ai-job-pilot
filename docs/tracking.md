# Application tracking

`tracker.py` records every application (and every skip/block) as one
record with a fixed field set: date, company, title, location, salary,
source, ats, url, status, confirmation, sponsorship, eeo, resume, lane,
notes.

Pick backends with `--backend` (comma-separated is accepted) or the
`TRACKER_BACKENDS` env var. Secrets always come from env vars, never
from code.

## JSONL (default, no setup)

```bash
python3 tracker.py --company Acme --title "Senior Backend Engineer" \
  --location "Austin, TX" --url https://job-boards.greenhouse.io/acme/jobs/123 \
  --backend jsonl --jsonl-path ./applications.jsonl
```

Appends one JSON object per line. This is the durable local record;
keep it even when you also use Sheets or Notion. `sweep.py --applied`
reads this same file (default `./applications.jsonl`) to avoid
re-queueing jobs you already touched.

## Google Sheets

Prerequisites: `pip install google-api-python-client google-auth`
(the rest of the repo is stdlib-only; this backend is the exception).

1. Create a Google Cloud project and enable the Google Sheets API on
   it (without this, writes fail with a confusing 403).
2. Create a service account in that project and download its JSON key.
3. Create your spreadsheet, add the header row manually with the field
   list above (in order), and share the spreadsheet with the service
   account email (Editor).
4. Set env vars:

```bash
export TRACKER_SHEET_ID="<your spreadsheet id>"
export TRACKER_SHEET_TAB="Applications"   # optional, default Applications
export GOOGLE_APPLICATION_CREDENTIALS="/path/to/service-account.json"
export TRACKER_BACKENDS="jsonl,sheets"
```

`tracker.py` appends one row per record with `RAW` input mode, so
posting text is stored as text rather than interpreted as a spreadsheet formula.
The harness has no CSV export command. If you export this data elsewhere and
open the CSV in Excel or another spreadsheet application, cells beginning with
`=`, `+`, `-`, or `@` may be interpreted as formulas. Import those columns as text
or neutralize formula prefixes in the export before opening it.

## Notion

No extra dependencies; the Notion backend uses stdlib `urllib` only.

1. Create a Notion integration at notion.so/my-integrations and copy the
   internal integration token.
2. Create a database with these properties (names must match exactly):
   Title (title), Company, Location, Salary, Source (select), ATS
   (select), URL (url), Status (select), Date (date), Confirmation
   (rich_text), Sponsorship (rich_text), EEO (rich_text), Resume
   (rich_text), Lane (rich_text), Notes (rich_text), ID (rich_text),
   Response Date (date), Furthest Stage (rich_text).
3. Share the database with your integration (Share menu in Notion).
4. Set env vars:

```bash
export TRACKER_NOTION_TOKEN="ntn_..."
export TRACKER_NOTION_DB="<database id>"
export TRACKER_BACKENDS="jsonl,notion"
```

Long text values are clipped to 2000 characters per property (Notion's
limit).

## IDs or URLs

`TRACKER_SHEET_ID` and `TRACKER_NOTION_DB` accept either a bare ID or
the full HTTPS URL you copy from the browser. URLs without a scheme are
interpreted as HTTPS; explicit HTTP, FTP, and other schemes are rejected.
The ID is extracted for you.

- Sheets: `https://docs.google.com/spreadsheets/d/<ID>/edit...` gives
  `<ID>` (the segment after `/d/`). A value with no `/` and no `.` is
  treated as a bare ID, which must contain at least 20 ASCII letters, digits,
  underscores, or hyphens. Published `/d/e/` URLs are rejected.
- Notion: a `notion.so` / `notion.site` URL whose last path segment ends
  in the 32-hex database ID (hyphenated or not) gives the hyphenated ID.
  The `?v=` query value is the view ID, not the database ID, and is
  ignored. A bare 32-hex ID or canonical `8-4-4-4-12` UUID also works;
  arbitrary hyphen placement is rejected.

Notion page and database URLs share the same ID shape. Offline parsing cannot
distinguish them or verify access; copy the database URL and share that database
with the integration. A page ID will be rejected later by the Notion API.
Custom-domain Notion URLs are unsupported because their host does not prove they
belong to Notion. Use the canonical `notion.so` / `notion.site` URL or the database
ID instead.

Anything that does not parse raises `ValueError` at startup (fail
closed). Parsing validates the ID shape and host; it does not verify which
sheet or database the ID identifies or whether the integration has access.
The pure helpers are `parse_sheets_id` and
`parse_notion_db_id` in `tracker.py`.

## Scoreboard and funnel analytics

- `ai-job-pilot-scoreboard record --lane <name> --kind applied|skipped|blocked|dead`
  appends an event to a JSON file (`JOBPILOT_SCOREBOARD`, `--db`, or
  `scoreboard.json` next to the script); `show [--period day|week]
  [--lane <name>]` prints per-lane and overall totals (UTC day, or
  trailing 7 days). Timestamps and the current time are normalized to UTC.
  Events up to five minutes ahead of the clock are counted to tolerate clock
  skew; a day report never includes events from the next UTC day. Lane names are
  free-form, e.g. `JOBPILOT_LANE`. Concurrent writers use POSIX locks on one host;
  Windows locking is a no-op, so run only one writer there. Atomic replacements
  preserve the existing file permission mode and clean up temporary files on error.
- `ai-job-pilot-analytics [--in applications.jsonl] [--json]` reports
  applied -> responded -> screening -> interview -> offer conversion,
  overall and per company. Repeated URLs are normalized with `job_queue.norm_url`
  and counted once using the last record in file order; records without URLs
  remain separate. The default input is `TRACKER_JSONL_PATH` when set, otherwise
  `applications.jsonl` next to the script, independent of the working directory.
  Tracker CLI writes still default to the current directory; use `--in PATH`
  when reading a log there or elsewhere. Missing files produce a clear CLI error.
  Statuses are case-insensitive; anything
  outside the fixed vocabulary is counted as `other` and listed. The
  median time-to-response needs a `response_date` (YYYY-MM-DD) on the
  record next to `date`; records without one are left out of the median.

## Reading a record from JSON

`--from-json result.json` builds the record from a JSON file, so lanes
can track without repeating every field on the command line. The JSON
must supply the record fields (company, title, url, ...); a lane's raw
`result.json` (`{status, confirmation_evidence, ...}`) does not have
them, so merge them in first or pass the fields as flags.

## Notes

- The fan-out validates every backend's configuration before writing
  anything; a failing backend is reported in its result instead of
  aborting the others.
- Tracking failures never lose an application: the lane logs tracker
  errors and keeps the submission result on disk.
- Terminal tables strip ANSI escape sequences and control characters from
  company and lane names; stored records retain their original text.
- Never commit service-account keys, tokens, or spreadsheet IDs.
  They live in env vars or a local `.env` (gitignored).

## Updating application progress

The tracker accepts Applied, Responded, Screening, Interview, Offer, Rejected,
Withdrawn, Skipped, Blocked, Dead, and Ready, case-insensitively. Records include
`id`, `response_date`, and `furthest_stage` in addition to the existing fields.
Sheets appends these three columns after the existing fields. Existing Notion
databases need the three new properties listed above before new rows can be recorded.
Update the latest local JSONL record by normalized URL or by id:

```bash
python3 tracker.py update --jsonl-path applications.jsonl --url URL --status interview --response-date 2026-10-06
python3 tracker.py update --jsonl-path applications.jsonl --id ID --status rejected
```

This command updates local JSONL only; it does not update Sheets or Notion rows.
It preserves the first response date, defaulting to today for a response status,
and the furthest stage reached. Rejecting an Interview therefore retains Interview
in the funnel; a rejection itself establishes Responded. Analytics counts Skipped,
Blocked, Dead, and Ready in an `excluded` bucket. Other unknown statuses remain
reported as `other`. Published Sheets `/spreadsheets/d/e/` links cannot identify
an editable spreadsheet; use an edit URL or an ID of at least 20 characters.
