#!/usr/bin/env python3
"""Funnel analytics over the tracker JSONL (applications.jsonl).

Reads records written by tracker.py (company, title, date, status, url...)
and reports how far applications progress through the funnel.

Status vocabulary (case-insensitive): applied, responded, screening,
interview, offer, rejected, withdrawn. Any other status is counted as
"other" and listed in the report, never dropped silently.

Funnel stages: applied -> responded -> screening -> interview -> offer.
A record at a later stage counts toward every earlier stage (an offer
implies it was applied to, responded to, screened and interviewed).
rejected and withdrawn are terminal outcomes: they count as applied only.
Each record is one application, so the status is its latest known stage.

Time to response: median days from the record's date (the applied date)
to its first non-applied status. The tracker stores one record per
application with a single date, so this is only computable when a record
carries "response_date" (YYYY-MM-DD); records without it are left out of
the median rather than guessed.

Usage:
  python3 analytics.py                       # ./applications.jsonl
  python3 analytics.py --in applications.jsonl --json
"""
import argparse
import datetime
import json
import statistics

STATUSES = ("applied", "responded", "screening", "interview", "offer",
            "rejected", "withdrawn")
STAGES = ("applied", "responded", "screening", "interview", "offer")
# Terminal outcomes that reached no later funnel stage than "applied".
_RANK = {"applied": 0, "responded": 1, "screening": 2, "interview": 3,
         "offer": 4, "rejected": 0, "withdrawn": 0}


def parse_records(lines):
    """Parse JSONL lines. Returns (records, malformed_count). Pure.

    Blank lines are skipped; non-JSON lines and non-object lines count as
    malformed so a corrupt log is visible in the report.
    """
    recs, bad = [], 0
    for line in lines:
        line = line.strip()
        if not line:
            continue
        try:
            r = json.loads(line)
        except ValueError:
            bad += 1
            continue
        if isinstance(r, dict):
            recs.append(r)
        else:
            bad += 1
    return recs, bad


def _day(value):
    try:
        return datetime.date.fromisoformat(str(value)[:10])
    except ValueError:
        return None


def _bucket():
    return {"counts": {s: 0 for s in STATUSES}, "other": 0,
            "stages": {s: 0 for s in STAGES}, "days": []}


def _add(b, status, days):
    if status in _RANK:
        b["counts"][status] += 1
        for s in STAGES:
            if _RANK[s] <= _RANK[status]:
                b["stages"][s] += 1
    else:
        b["other"] += 1
    if days is not None:
        b["days"].append(days)


def _rates(stages):
    out = {}
    for prev, cur in zip(STAGES, STAGES[1:]):
        out["%s->%s" % (prev, cur)] = (
            round(stages[cur] / stages[prev], 4) if stages[prev] else None)
    return out


def _finish(b):
    return {"counts": b["counts"], "other": b["other"],
            "stages": b["stages"], "conversion": _rates(b["stages"]),
            "median_days_to_response": (
                statistics.median(b["days"]) if b["days"] else None),
            "response_samples": len(b["days"])}


def funnel_stats(records):
    """Overall and per-company funnel. Pure.

    Returns {"total", "overall", "companies": {name: stats},
    "unknown_statuses": {raw: n}}.
    """
    overall = _bucket()
    comps = {}
    unknown = {}
    for r in records:
        raw = str(r.get("status", "") or "").strip()
        status = raw.lower()
        if status not in _RANK:
            unknown[raw or "(empty)"] = unknown.get(raw or "(empty)", 0) + 1
        days = None
        if status in _RANK and status != "applied":
            a, b = _day(r.get("date", "")), _day(r.get("response_date", ""))
            if a and b and b >= a:
                days = (b - a).days
        name = str(r.get("company", "") or "").strip() or "(unknown)"
        c = comps.setdefault(name.lower(), dict(_bucket(), name=name))
        for bucket in (overall, c):
            _add(bucket, status, days)
    out_c = {}
    for k in sorted(comps):
        d = _finish(comps[k])
        d["name"] = comps[k]["name"]
        out_c[k] = d
    return {"total": len(records), "overall": _finish(overall),
            "companies": out_c, "unknown_statuses": unknown}


def _pct(v):
    return "n/a" if v is None else "%.1f%%" % (v * 100)


def format_report(stats, malformed=0):
    o = stats["overall"]
    out = ["records: %d (malformed lines: %d)" % (stats["total"], malformed),
           "", "funnel (records that reached each stage):"]
    for s in STAGES:
        out.append("  %-10s %5d" % (s, o["stages"][s]))
    out.append("  rejected   %5d   withdrawn %5d   other %5d" % (
        o["counts"]["rejected"], o["counts"]["withdrawn"], o["other"]))
    out += ["", "conversion:"]
    for k, v in o["conversion"].items():
        out.append("  %-24s %s" % (k, _pct(v)))
    m = o["median_days_to_response"]
    out.append("median days to first response: %s (n=%d)" % (
        "n/a" if m is None else "%g" % m, o["response_samples"]))
    if stats["unknown_statuses"]:
        out += ["", "unknown statuses (counted as other):"]
        for k in sorted(stats["unknown_statuses"]):
            out.append("  %r: %d" % (k, stats["unknown_statuses"][k]))
    out += ["", "per company:",
            "  %-24s %7s %9s %9s %9s %6s %8s" % (
                "company", "applied", "responded", "screening",
                "interview", "offer", "med.days")]
    for c in stats["companies"].values():
        s = c["stages"]
        md = c["median_days_to_response"]
        out.append("  %-24s %7d %9d %9d %9d %6d %8s" % (
            c["name"][:24], s["applied"], s["responded"], s["screening"],
            s["interview"], s["offer"], "n/a" if md is None else "%g" % md))
    return "\n".join(out)


def main():
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--in", dest="inp", default="applications.jsonl",
                    help="tracker JSONL file (default applications.jsonl)")
    ap.add_argument("--json", action="store_true",
                    help="machine-readable output")
    a = ap.parse_args()
    with open(a.inp) as f:
        recs, bad = parse_records(f)
    stats = funnel_stats(recs)
    stats["malformed_lines"] = bad
    if a.json:
        print(json.dumps(stats, indent=1))
    else:
        print(format_report(stats, bad))


if __name__ == "__main__":
    main()
