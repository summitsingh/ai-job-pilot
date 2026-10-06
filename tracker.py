#!/usr/bin/env python3
"""Application tracker with pluggable backends.

Every submitted (or skipped/blocked) application is recorded as one
record:

  {
    "date": "2026-01-15",
    "company": "Acme", "title": "Senior Backend Engineer",
    "location": "Austin, TX", "salary": "$180K-$220K",
    "source": "Greenhouse", "ats": "Greenhouse",
    "url": "https://job-boards.greenhouse.io/acme/jobs/123",
    "status": "Applied",            # Applied|Skipped|Blocked
    "confirmation": "confirmation page verified",
    "sponsorship": "Yes",           # how the sponsorship Q was answered
    "eeo": "Declined",
    "resume": "Firstname_Lastname_Resume.pdf",
    "lane": "mac-mini:9445",
    "notes": ""
  }

Backends (chosen with --backend, comma-separated; default "jsonl"):
  jsonl   append to a local JSONL file (default ./applications.jsonl)
  sheets  append a row to a Google Sheet tab (needs google-api-python-client)
  notion  create a row in a Notion database (stdlib only, via Notion API)

Secrets come from env vars, never from code or the record:
  TRACKER_SHEET_ID, TRACKER_SHEET_TAB        (sheets backend)
  GOOGLE_APPLICATION_CREDENTIALS              (path to service-account JSON)
  TRACKER_NOTION_TOKEN, TRACKER_NOTION_DB    (notion backend)

TRACKER_SHEET_ID and TRACKER_NOTION_DB accept either a bare ID or the
full URL of the sheet / database (see parse_sheets_id, parse_notion_db_id).

Setup for Sheets and Notion is documented in docs/tracking.md.

Usage:
  python3 tracker.py --company Acme --title "Senior SWE" \
      --url https://job-boards.greenhouse.io/acme/jobs/123 \
      --backend jsonl
  python3 tracker.py --from-json result.json --backend jsonl,sheets,notion
  python3 tracker.py update --url https://example.com/job --status interview \
      --response-date 2026-01-20 --jsonl-path applications.jsonl

The update command edits the latest matching local JSONL record by URL or
--id. It preserves the first response_date and furthest_stage; remote
backend rows are not updated by this local command.
"""
import argparse
import datetime
import json
import os
import re
import sys
import tempfile
import uuid

from job_queue import Store, norm_url
import urllib.parse
import urllib.request

HERE = os.path.dirname(os.path.abspath(__file__))

FIELDS = ["date", "company", "title", "location", "salary", "source", "ats",
          "url", "status", "confirmation", "sponsorship", "eeo",
          "resume", "lane", "notes", "id", "response_date", "furthest_stage"]


VALID_STATUSES = {"Applied", "Responded", "Screening", "Interview", "Offer",
                  "Rejected", "Withdrawn", "Skipped", "Blocked", "Dead", "Ready"}
STAGE_RANK = {"Applied": 0, "Responded": 1, "Screening": 2,
              "Interview": 3, "Offer": 4}


def furthest_stage(status, previous=""):
    """Preserve known progress; a rejection is itself a response."""
    floor = "Responded" if status == "Rejected" else "Applied"
    stage = status if status in STAGE_RANK else floor
    previous = str(previous or "").strip().title()
    if previous in STAGE_RANK and STAGE_RANK[previous] > STAGE_RANK[stage]:
        stage = previous
    return stage if status not in {"Skipped", "Blocked", "Dead", "Ready"} else ""



def build_record(**kw):
    rec = {f: "" for f in FIELDS}
    rec["date"] = datetime.date.today().isoformat()
    rec["status"] = "Applied"
    for k, v in kw.items():
        if k in rec:
            rec[k] = v if v is not None else ""
    rec["status"] = str(rec["status"]).strip().title()
    if rec["status"] not in VALID_STATUSES:
        raise ValueError("status must be one of %s, got %r"
                         % (sorted(VALID_STATUSES), rec["status"]))
    if not rec["url"]:
        raise ValueError("record requires a url")
    rec["id"] = rec["id"] or uuid.uuid4().hex
    rec["furthest_stage"] = furthest_stage(rec["status"], rec["furthest_stage"])
    return rec


_SHEETS_ID_RE = re.compile(r"^[A-Za-z0-9_-]{20,}$")
_HEX32_RE = re.compile(r"^[0-9a-fA-F]{32}$")
_NOTION_TAIL_RE = re.compile(
    r"(?:^|-)([0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-"
    r"[0-9a-fA-F]{4}-[0-9a-fA-F]{12}|[0-9a-fA-F]{32})$")


def _url_parts(value, what):
    """Split a URL value into (host, path); ValueError if it is not one."""
    try:
        p = urllib.parse.urlsplit(value if "://" in value
                                  else "https://" + value)
        host = (p.hostname or "").lower()
    except ValueError:
        raise ValueError("%s is not a valid URL: %r" % (what, value))
    if p.scheme.lower() != "https":
        raise ValueError("%s requires an HTTPS URL: %r" % (what, value))
    if not host:
        raise ValueError("%s is not a valid URL: %r" % (what, value))
    return host, p.path


def parse_sheets_id(value):
    """Return the spreadsheet ID from a bare ID or a Google Sheets URL.

    A string with no "/" and no "." is a bare ID. Otherwise it must be a
    docs.google.com URL of the form .../spreadsheets/d/<ID>/... Anything
    else raises ValueError (fail closed: never guess an ID).
    """
    v = (value or "").strip()
    if not v:
        raise ValueError("spreadsheet ID or URL is empty")
    if "/" not in v and "." not in v:
        if not _SHEETS_ID_RE.match(v):
            raise ValueError("invalid spreadsheet ID: %r" % v)
        return v
    host, path = _url_parts(v, "spreadsheet URL")
    if host != "docs.google.com":
        raise ValueError("not a Google Sheets URL (host %r): %r"
                         % (host, v))
    if re.search(r"/spreadsheets/(?:u/\d+/)?d/e(?:/|$)", path):
        raise ValueError("published Sheets URL does not contain a spreadsheet ID")
    m = re.search(r"/spreadsheets/(?:u/\d+/)?d/([A-Za-z0-9_-]{20,})(?:/|$)",
                  path)
    if not m:
        raise ValueError("no /spreadsheets/d/<ID> segment in URL: %r" % v)
    return m.group(1)


def parse_notion_db_id(value):
    """Return the hyphenated 32-hex Notion database ID.

    Accepts a bare ID (hyphenated or not) or a notion.so / notion.site
    URL whose last path segment ends in the ID. The query string is
    ignored on purpose: ?v=<id> is the VIEW id, not the database id.
    Anything else raises ValueError.
    """
    v = (value or "").strip()
    if not v:
        raise ValueError("Notion database ID or URL is empty")
    if "/" not in v and "." not in v:
        raw = v.replace("-", "")
        canonical = _hyphenate(raw) if _HEX32_RE.fullmatch(raw) else ""
        if not canonical or v.lower() not in (raw.lower(), canonical):
            raise ValueError("invalid Notion database ID: %r" % v)
        return _hyphenate(raw)
    host, path = _url_parts(v, "Notion URL")
    if not (host in ("notion.so", "notion.site")
            or host.endswith((".notion.so", ".notion.site"))):
        raise ValueError("not a Notion URL (host %r): %r" % (host, v))
    segs = [s for s in path.split("/") if s]
    m = _NOTION_TAIL_RE.search(segs[-1]) if segs else None
    if not m:
        raise ValueError("no 32-hex database ID at end of URL path: %r" % v)
    return _hyphenate(m.group(1).replace("-", ""))


def _hyphenate(raw32):
    r = raw32.lower()
    return "%s-%s-%s-%s-%s" % (r[:8], r[8:12], r[12:16], r[16:20], r[20:])


class JsonlTracker:
    def __init__(self, path="applications.jsonl"):
        self.path = os.path.expanduser(path)

    def append(self, record):
        with Store(self.path):
            return self._append(record)

    def _append(self, record):
        d = os.path.dirname(os.path.abspath(self.path))
        os.makedirs(d, exist_ok=True)
        fd = os.open(self.path, os.O_CREAT | os.O_WRONLY | os.O_APPEND,
                     0o644)
        with os.fdopen(fd, "a") as f:
            try:
                import fcntl
                fcntl.flock(f, fcntl.LOCK_EX)
            except ImportError:
                pass
            f.write(json.dumps(record) + "\n")
            f.flush()
            os.fsync(f.fileno())
        return {"backend": "jsonl", "path": self.path}


    def update(self, status, *, url=None, record_id=None, response_date=None):
        """Update the latest matching local record atomically, preserving history."""
        if bool(url) == bool(record_id):
            raise ValueError("provide exactly one of url or record_id")
        with Store(self.path):
            with open(self.path) as f:
                rows = [json.loads(line) for line in f if line.strip()]
            matches = [i for i, r in enumerate(rows) if
                       (r.get("id") == record_id if record_id else
                        norm_url(r.get("url", "")) == norm_url(url))]
            if not matches:
                raise ValueError("no matching tracker record")
            index = matches[-1]
            old = rows[index]
            fields = dict(old, status=status,
                          furthest_stage=furthest_stage(
                              str(old.get("status", "Applied")).title(),
                              old.get("furthest_stage", "")))
            rec = build_record(**fields)
            if rec["status"] in {"Responded", "Screening", "Interview", "Offer", "Rejected"}:
                if not rec["response_date"]:
                    rec["response_date"] = response_date or datetime.date.today().isoformat()
            elif response_date and not rec["response_date"]:
                rec["response_date"] = response_date
            if rec["response_date"]:
                response = datetime.date.fromisoformat(rec["response_date"])
                applied = datetime.date.fromisoformat(rec["date"])
                if response < applied:
                    raise ValueError("response_date must not precede application date")
            rows[index] = dict(old, **rec)
            fd, tmp = tempfile.mkstemp(dir=os.path.dirname(os.path.abspath(self.path)))
            try:
                with os.fdopen(fd, "w") as f:
                    for row in rows:
                        f.write(json.dumps(row) + "\n")
                    f.flush()
                    os.fsync(f.fileno())
                os.replace(tmp, self.path)
            finally:
                if os.path.exists(tmp):
                    os.unlink(tmp)
            return rec


class SheetsTracker:
    """Append rows to a Google Sheet via the Sheets API (service account)."""

    def __init__(self, spreadsheet_id=None, tab=None,
                 credentials_path=None):
        self.spreadsheet_id = (spreadsheet_id
                               or os.environ.get("TRACKER_SHEET_ID", ""))
        self.tab = (tab or os.environ.get("TRACKER_SHEET_TAB", "")
                    or "Applications")
        self.credentials_path = (credentials_path or os.environ.get(
            "GOOGLE_APPLICATION_CREDENTIALS", ""))
        if not self.spreadsheet_id:
            raise ValueError("TRACKER_SHEET_ID is not set")
        self.spreadsheet_id = parse_sheets_id(self.spreadsheet_id)
        if not self.credentials_path:
            raise ValueError("GOOGLE_APPLICATION_CREDENTIALS is not set")

    @staticmethod
    def _a1(tab, ncols):
        # Quote the tab name for A1 notation: 'My Tab'!A:O
        q = tab.replace("'", "''")
        return "'%s'!A:%s" % (q, chr(ord("A") + ncols - 1))

    def append(self, record):
        try:
            from google.oauth2 import service_account
            from googleapiclient.discovery import build
        except ImportError:
            raise RuntimeError(
                "sheets backend needs google-api-python-client: "
                "pip install google-api-python-client google-auth")
        creds = service_account.Credentials.from_service_account_file(
            self.credentials_path,
            scopes=["https://www.googleapis.com/auth/spreadsheets"])
        svc = build("sheets", "v4", credentials=creds)
        row = [record.get(f, "") for f in FIELDS]
        body = svc.spreadsheets().values().append(
            spreadsheetId=self.spreadsheet_id,
            range=self._a1(self.tab, len(FIELDS)),
            # RAW: never let untrusted posting text become a formula
            valueInputOption="RAW",
            body={"values": [row]}).execute()
        return {"backend": "sheets",
                "updated": body.get("updates", {}).get("updatedCells")}


class NotionTracker:
    """Create a row in a Notion database via the Notion API (stdlib only)."""

    def __init__(self, token=None, database_id=None):
        self.token = token or os.environ.get("TRACKER_NOTION_TOKEN", "")
        self.database_id = (database_id
                            or os.environ.get("TRACKER_NOTION_DB", ""))
        if not self.token:
            raise ValueError("TRACKER_NOTION_TOKEN is not set")
        if not self.database_id:
            raise ValueError("TRACKER_NOTION_DB is not set")
        self.database_id = parse_notion_db_id(self.database_id)

    @staticmethod
    def _clip(value, limit=2000):
        s = str(value or "")
        return s if len(s) <= limit else s[:limit - 1] + "\u2026"

    def _prop(self, name, value, ptype="rich_text"):
        if ptype == "title":
            return {name: {"title": [{"text": {
                "content": self._clip(value)}}]}}
        if ptype == "url":
            return {name: {"url": value or None}}
        if ptype == "date":
            return {name: {"date": {"start": value} if value else None}}
        if ptype == "select":
            return {name: {"select": {"name": value} if value else None}}
        return {name: {"rich_text": [{"text": {
            "content": self._clip(value)}}]}}

    def append(self, record):
        props = {}
        props.update(self._prop("Title", "%s - %s" % (
            record.get("company", ""), record.get("title", "")), "title"))
        props.update(self._prop("Company", record.get("company", "")))
        props.update(self._prop("Location", record.get("location", "")))
        props.update(self._prop("Salary", record.get("salary", "")))
        props.update(self._prop("Source", record.get("source", ""), "select"))
        props.update(self._prop("ATS", record.get("ats", ""), "select"))
        props.update(self._prop("URL", record.get("url", ""), "url"))
        props.update(self._prop("Status", record.get("status", ""), "select"))
        props.update(self._prop("Date", record.get("date", ""), "date"))
        props.update(self._prop("Confirmation", record.get("confirmation", "")))
        props.update(self._prop("Sponsorship", record.get("sponsorship", "")))
        props.update(self._prop("EEO", record.get("eeo", "")))
        props.update(self._prop("Resume", record.get("resume", "")))
        props.update(self._prop("Lane", record.get("lane", "")))
        props.update(self._prop("Notes", record.get("notes", "")))
        props.update(self._prop("ID", record.get("id", "")))
        props.update(self._prop("Response Date", record.get("response_date", ""), "date"))
        props.update(self._prop("Furthest Stage", record.get("furthest_stage", "")))
        body = json.dumps({
            "parent": {"database_id": self.database_id},
            "properties": props,
        }).encode()
        req = urllib.request.Request(
            "https://api.notion.com/v1/pages",
            data=body,
            headers={"Authorization": "Bearer " + self.token,
                     "Notion-Version": "2022-06-28",
                     "Content-Type": "application/json"})
        with urllib.request.urlopen(req, timeout=30) as r:
            out = json.load(r)
        return {"backend": "notion", "page_id": out.get("id")}


BACKENDS = {"jsonl": JsonlTracker, "sheets": SheetsTracker,
            "notion": NotionTracker}


def track(record, backends=("jsonl",), **kw):
    """Append record to each named backend.

    All backends are constructed (and validated) first; then each append
    runs independently. A failing backend is reported in its result dict
    instead of aborting the fan-out, so one bad backend never blocks the
    others. Returns a list of per-backend result dicts.
    """
    trackers = []
    for name in backends:
        cls = BACKENDS.get(name)
        if not cls:
            raise ValueError("unknown backend: %s" % name)
        cfg = kw.get(name, {})
        trackers.append((name, cls(**cfg) if isinstance(cfg, dict)
                         else cls()))
    results = []
    for name, tracker in trackers:
        try:
            results.append(tracker.append(record))
        except Exception as e:
            results.append({"backend": name, "error": "%r" % e})
    return results


def main():
    if len(sys.argv) > 1 and sys.argv[1] == "update":
        ap = argparse.ArgumentParser(description="Update a local tracker record's status")
        ap.add_argument("--jsonl-path", default="applications.jsonl")
        selector = ap.add_mutually_exclusive_group(required=True)
        selector.add_argument("--url")
        selector.add_argument("--id", dest="record_id")
        ap.add_argument("--status", required=True)
        ap.add_argument("--response-date")
        a = ap.parse_args(sys.argv[2:])
        try:
            rec = JsonlTracker(a.jsonl_path).update(a.status, url=a.url,
                record_id=a.record_id, response_date=a.response_date)
        except (ValueError, OSError) as e:
            ap.error(str(e))
        print(json.dumps({"record": rec}, indent=1))
        return
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--backend", default="jsonl",
                    help="comma-separated: jsonl,sheets,notion")
    ap.add_argument("--jsonl-path", default="applications.jsonl")
    ap.add_argument("--from-json", default="",
                    help="read record fields from a JSON file")
    for f in FIELDS:
        ap.add_argument("--" + f.replace("_", "-"), default=None)
    a = ap.parse_args()

    kw = {}
    if a.from_json:
        with open(a.from_json) as fh:
            data = json.load(fh)
        kw = {k: data.get(k) for k in FIELDS if k in data}
    for f in FIELDS:
        v = getattr(a, f, None)
        if v is not None:
            kw[f] = v
    record = build_record(**kw)

    backends = [b.strip() for b in a.backend.split(",") if b.strip()]
    per = {}
    if "jsonl" in backends:
        per["jsonl"] = {"path": a.jsonl_path}
    results = track(record, backends, **per)
    print(json.dumps({"record": record, "results": results}, indent=1))


if __name__ == "__main__":
    main()
