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
posting text can never become a spreadsheet formula.

## Notion

No extra dependencies; the Notion backend uses stdlib `urllib` only.

1. Create a Notion integration at notion.so/my-integrations and copy the
   internal integration token.
2. Create a database with these properties (names must match exactly):
   Title (title), Company, Location, Salary, Source (select), ATS
   (select), URL (url), Status (select), Date (date), Confirmation
   (rich_text), Sponsorship (rich_text), EEO (rich_text), Resume
   (rich_text), Lane (rich_text), Notes (rich_text).
3. Share the database with your integration (Share menu in Notion).
4. Set env vars:

```bash
export TRACKER_NOTION_TOKEN="ntn_..."
export TRACKER_NOTION_DB="<database id>"
export TRACKER_BACKENDS="jsonl,notion"
```

Long text values are clipped to 2000 characters per property (Notion's
limit).

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
- Never commit service-account keys, tokens, or spreadsheet IDs.
  They live in env vars or a local `.env` (gitignored).
