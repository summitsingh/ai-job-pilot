#!/usr/bin/env python3
"""Scoreboard: per-lane day/week totals of applied/skipped/blocked/dead.

Backend is one JSON file:
  {"events": [{"ts": "2026-01-15T12:00:00+00:00", "lane": "mac-mini:9445",
               "kind": "applied", "company": "Acme", "url": "https://..."}]}

Lane names are free-form strings from config (JOBPILOT_LANE), for example
"mac-mini:9445". Nothing is hardcoded. Appends run inside job_queue.Store
(sidecar lock file), so concurrent lanes on one host do not lose events;
the file is replaced atomically on save.

DB path precedence: --db > JOBPILOT_SCOREBOARD (env or config.json) >
scoreboard.json next to this script.

Usage:
  python3 scoreboard.py record --lane mac-mini:9445 --kind applied \
      --company Acme --url https://job-boards.greenhouse.io/acme/jobs/1
  python3 scoreboard.py show                  # today (UTC), all lanes
  python3 scoreboard.py show --period week    # trailing 7 days
  python3 scoreboard.py show --lane mac-mini:9445
"""
import argparse
import datetime
import json
import os
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import config
from job_queue import Store, utcnow, _parse_ts

HERE = os.path.dirname(os.path.abspath(__file__))
KINDS = ("applied", "skipped", "blocked", "dead")
PERIODS = ("day", "week")


def default_db():
    return config.get("JOBPILOT_SCOREBOARD",
                      os.path.join(HERE, "scoreboard.json"))


def load_events(db):
    """Read events. Missing file is empty; malformed JSON raises."""
    if not os.path.exists(db):
        return []
    with open(db) as f:
        text = f.read().strip()
    if not text:
        return []
    data = json.loads(text)
    if not isinstance(data, dict) or not isinstance(
            data.get("events", []), list):
        raise ValueError("scoreboard file must hold {\"events\": [...]}: %s"
                         % db)
    return [e for e in data.get("events", []) if isinstance(e, dict)]


def _save(db, events):
    fd, tmp = tempfile.mkstemp(prefix=".scoreboard-",
                               dir=os.path.dirname(os.path.abspath(db)))
    with os.fdopen(fd, "w") as f:
        json.dump({"events": events}, f, indent=1)
        f.flush()
        os.fsync(f.fileno())
    os.replace(tmp, db)


def record(db, lane, kind, company="", url="", ts=None):
    """Append one event and return it. Unknown kinds raise ValueError."""
    if kind not in KINDS:
        raise ValueError("kind must be one of %s, got %r"
                         % (list(KINDS), kind))
    if not lane or not str(lane).strip():
        raise ValueError("lane is required")
    event = {"ts": ts or utcnow(), "lane": str(lane).strip(), "kind": kind,
             "company": company or "", "url": url or ""}
    db = os.path.abspath(db)
    with Store(db):
        events = load_events(db)
        events.append(event)
        _save(db, events)
    return event


def totals(events, period="day", lane=None, now=None):
    """Count events in the period. Pure.

    period "day" is the current UTC calendar day; "week" is the trailing
    7 days up to now. Returns {"lanes": {lane: {kind: n, "total": n}},
    "overall": {kind: n, "total": n}}. Events with an unparseable ts or
    an unknown kind are ignored.
    """
    if period not in PERIODS:
        raise ValueError("period must be one of %s" % list(PERIODS))
    now = now or datetime.datetime.now(datetime.timezone.utc)
    if period == "day":
        start = now.replace(hour=0, minute=0, second=0, microsecond=0)
    else:
        start = now - datetime.timedelta(days=7)
    lanes = {}
    overall = {k: 0 for k in KINDS}
    overall["total"] = 0
    for e in events:
        ts = _parse_ts(e.get("ts", ""))
        kind = e.get("kind")
        if ts is None or kind not in KINDS or ts < start or ts > now:
            continue
        name = e.get("lane", "")
        if lane and name != lane:
            continue
        row = lanes.setdefault(name, dict({k: 0 for k in KINDS}, total=0))
        row[kind] += 1
        row["total"] += 1
        overall[kind] += 1
        overall["total"] += 1
    return {"lanes": lanes, "overall": overall}


def format_table(result, period="day"):
    """Plain-text table, one row per lane plus an overall row."""
    cols = list(KINDS) + ["total"]
    rows = [(n, result["lanes"][n]) for n in sorted(result["lanes"])]
    rows.append(("OVERALL", result["overall"]))
    width = max([len("lane")] + [len(n) for n, _ in rows])
    head = "%-*s  %s" % (width, "lane", "  ".join("%7s" % c for c in cols))
    out = ["period: %s" % period, head, "-" * len(head)]
    for n, r in rows:
        out.append("%-*s  %s" % (width, n, "  ".join(
            "%7d" % r[c] for c in cols)))
    return "\n".join(out)


def main():
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--db", default="", help="scoreboard JSON file")
    sub = ap.add_subparsers(dest="cmd", required=True)

    p = sub.add_parser("record", help="append one event")
    p.add_argument("--lane", default=config.get("JOBPILOT_LANE", ""),
                   help="lane name (default: JOBPILOT_LANE)")
    p.add_argument("--kind", required=True, choices=KINDS)
    p.add_argument("--company", default="")
    p.add_argument("--url", default="")

    p = sub.add_parser("show", help="print totals")
    p.add_argument("--period", choices=PERIODS, default="day")
    p.add_argument("--lane", default="")

    a = ap.parse_args()
    db = a.db or default_db()
    if a.cmd == "record":
        try:
            ev = record(db, a.lane, a.kind, a.company, a.url)
        except ValueError as e:
            ap.error(str(e))
        print(json.dumps(ev))
    else:
        res = totals(load_events(db), a.period, a.lane or None)
        print(format_table(res, a.period))


if __name__ == "__main__":
    main()
