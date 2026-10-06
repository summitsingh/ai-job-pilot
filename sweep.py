#!/usr/bin/env python3
"""Sweep: filter raw job postings into vetted queue candidates.

Discovery (fetching postings) is source-specific and intentionally NOT in
this repo. This module takes a JSON list of raw postings, one per source,
and applies the standard jobpilot filters:

  - title: senior-level (senior|sr|staff|lead|principal) AND backend /
    platform / infra / AI / ML role; rejects frontend, mobile, junior,
    intern, manager-track, and unrelated specialties
  - location: allowed metros or Remote US; rejects non-US locations
  - sponsorship: rejects postings whose text rules out visa sponsorship
  - salary: rejects postings whose stated max is below the floor
  - dedup: rejects URLs already in the applied log (normalized)

Input posting: {"title", "location", "url", "description",
                "salary_min", "salary_max", "company"}.
Output: queue-shaped job dicts (see job_queue.py), written to a queue file.

Usage:
  python3 sweep.py --in raw.json --applied applications.jsonl \
      --queue queue.json --min-salary 100000
  python3 sweep.py --in raw.json --dry-run   # prints stats only

All filter functions are pure and covered by the offline test suite.
"""
import argparse
import datetime
import json
import os
import re
import sys
import urllib.parse

from job_queue import norm_url

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

TITLE_LEVEL = re.compile(r"\b(senior|sr\.?|staff|lead|principal)\b", re.I)
TITLE_ROLE = re.compile(
    r"(software engineer|software developer|backend|platform|"
    r"infrastructure|infra|devops|sre|systems engineer|distributed|"
    r"cloud engineer|ml engineer|machine learning engineer|ai engineer|"
    r"data engineer|member of technical staff)", re.I)
TITLE_NO = re.compile(
    r"(frontend|front-end|front end|mobile|\bios\b|android|intern|junior|"
    r"manager|director|\bvp\b|vice president|data scientist|data analyst|"
    r"\bqa\b|software developer in test|\bin test\b|\bsdet\b|"
    r"support engineer|sales|recruit|designer|product manager)", re.I)

LOC_OK = re.compile(
    r"(remote|seattle|san francisco|bay area|palo alto|mountain view|"
    r"menlo park|cupertino|sunnyvale|san jose|santa clara|redwood city|"
    r"los angeles|\bnyc\b|new york|brooklyn|austin|redmond|bellevue|"
    r"kirkland|irvine|santa monica|oakland|berkeley|fremont|san diego)",
    re.I)
LOC_NONUS = re.compile(
    r"(london|berlin|paris|tokyo|singapore|bangalore|bengaluru|hyderabad|"
    r"toronto|vancouver|amsterdam|dublin|warsaw|krakow|tel aviv|\bindia\b|"
    r"canada|germany|france|\buk\b|europe|emea|apac|latin america|mexico|"
    r"brazil|argentina|colombia|philippines|australia|sydney|netherlands|"
    r"sweden|spain|italy|greece|turkey|poland|africa|south africa|"
    r"new zealand|japan|korea|china|hong kong|uae|dubai)", re.I)

# Phrases that rule a posting out for applicants who need visa
# sponsorship. Conservative by design: a false reject costs a queue slot,
# a false accept costs an application that cannot be sponsored.
SPONSOR_NO = [
    "no visa consideration", "no visa transfer", "not open to c2c",
    "not open to c-to-c", "no sponsorship available",
    "no visa sponsorship available", "cannot provide visa sponsorship",
    "sponsorship is not available", "we do not offer sponsorship",
    "will not provide sponsorship", "us persons only", "us citizens only",
    "without sponsorship", "will not sponsor", "unable to sponsor",
    "does not sponsor", "does not provide sponsorship", "no sponsorship",
    "citizenship required", "green card holders only",
    "permanent residents only", "must be a u.s. citizen",
]


def title_ok(title):
    tl = (title or "").lower()
    return bool(TITLE_LEVEL.search(tl) and TITLE_ROLE.search(tl)
                and not TITLE_NO.search(tl))


US_MARK = re.compile(r"united states|\busa?\b|u\.s\.|\(us\)", re.I)


def location_ok(location, workplace=""):
    loc = (location or "").strip()
    locl = loc.lower()
    if LOC_NONUS.search(locl) and not US_MARK.search(locl):
        return False, ""
    if "remote" in locl or (workplace or "").lower() == "remote":
        # Only claim US-remote when the posting says so; otherwise keep
        # the original text so a human can judge.
        if US_MARK.search(locl):
            return True, "Remote (US)"
        return True, loc
    if LOC_OK.search(locl):
        return True, loc
    return False, ""


def sponsorship_ok(description):
    dl = (description or "").lower().replace("\n", " ")
    if any(p in dl for p in SPONSOR_NO):
        return False
    if re.search(r"\bno\b.{0,25}\bopt\b", dl):
        return False
    return True


def parse_salary(value):
    """Parse a salary amount to a float. Returns None when absent or
    unparseable. Handles 90000, "$90,000", "90k"."""
    if value is None:
        return None
    s = str(value).strip().lower().replace("$", "").replace(",", "")
    if not s:
        return None
    mult = 1
    if s.endswith("k"):
        mult = 1000
        s = s[:-1]
    try:
        return float(s) * mult
    except ValueError:
        return None


def salary_ok(salary_min, salary_max, floor=100000):
    """Reject postings whose stated max is below the floor.

    Undisclosed (or unparseable) salary is eligible; only an explicit low
    ceiling rejects.
    """
    smax = parse_salary(salary_max)
    if smax is not None and smax < floor:
        return False
    return True


def ats_of(url):
    try:
        host = (urllib.parse.urlsplit(url).hostname or "").lower()
    except ValueError:
        return "direct"
    if host == "boards.greenhouse.io" or host.endswith(".greenhouse.io"):
        return "greenhouse"
    if host.endswith("ashbyhq.com"):
        return "ashby"
    if host.endswith("lever.co"):
        return "lever"
    if host.endswith("workable.com"):
        return "workable"
    return "direct"


def load_applied_urls(applied_path):
    """URLs already applied/skipped, from JSONL or a JSON list. Pure."""
    urls = set()
    if not applied_path or not os.path.exists(applied_path):
        return urls
    with open(applied_path) as f:
        text = f.read().strip()
    if not text:
        return urls
    entries = []
    if text.startswith("["):
        entries = json.loads(text)
    else:
        for line in text.splitlines():
            line = line.strip()
            if line:
                try:
                    entries.append(json.loads(line))
                except ValueError:
                    continue
    for e in entries:
        if isinstance(e, str):
            u = e
        elif isinstance(e, dict):
            u = e.get("job_url") or e.get("url") or ""
        else:
            continue
        if u:
            urls.add(norm_url(u))
    return urls


def filter_postings(raw, applied_urls=(), min_salary=100000,
                    source="sweep", now=None):
    """Apply all filters; return (candidates, stats). Pure.

    applied_urls: normalized URLs already touched (from the tracker).
    now: ISO timestamp for queued_at; defaults to current UTC time.
    """
    stats = {"scanned": len(raw), "title_fail": 0, "loc_fail": 0,
             "sponsor_fail": 0, "salary_fail": 0, "dup": 0, "no_url": 0}
    cands = []
    seen = set()
    now = now or datetime.datetime.now(datetime.timezone.utc).isoformat()
    for j in raw:
        if not isinstance(j, dict):
            stats["no_url"] += 1
            continue
        url = j.get("url") or ""
        if not url:
            stats["no_url"] += 1
            continue
        nu = norm_url(url)
        if nu in applied_urls or nu in seen:
            stats["dup"] += 1
            continue
        seen.add(nu)
        if not title_ok(j.get("title")):
            stats["title_fail"] += 1
            continue
        ok, loc = location_ok(j.get("location"), j.get("workplace", ""))
        if not ok:
            stats["loc_fail"] += 1
            continue
        if not sponsorship_ok(j.get("description")):
            stats["sponsor_fail"] += 1
            continue
        if not salary_ok(j.get("salary_min"), j.get("salary_max"),
                         min_salary):
            stats["salary_fail"] += 1
            continue
        smin, smax = j.get("salary_min"), j.get("salary_max")
        salary = ("%s - %s" % (smin, smax)).strip(" -") \
            if (smin or smax) else "salary undisclosed"
        cands.append({
            "company": j.get("company", ""), "title": j.get("title", ""),
            "location": loc, "salary": salary, "url": url,
            "ats": ats_of(url), "notes": j.get("notes", ""),
            "queued_at": now, "source": source, "status": "pending",
            "skip_reason": "",
        })
    return cands, stats


def main():
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--in", dest="inp", required=True,
                    help="JSON list of raw postings")
    ap.add_argument("--applied", default="",
                    help="applied log (JSONL or JSON list) for dedup")
    ap.add_argument("--queue", default="",
                    help="append candidates to this queue file")
    ap.add_argument("--min-salary", type=float, default=100000)
    ap.add_argument("--source", default="sweep")
    ap.add_argument("--dry-run", action="store_true")
    a = ap.parse_args()

    with open(a.inp) as f:
        raw = json.load(f)
    if isinstance(raw, dict):
        raw = raw.get("jobs", raw.get("postings", []))
    applied = load_applied_urls(a.applied)
    cands, stats = filter_postings(raw, applied, a.min_salary, a.source)
    stats["candidates"] = len(cands)
    print(json.dumps(stats, indent=1))
    if a.dry_run or not a.queue:
        return
    from job_queue import load_queue, save_queue
    jobs = load_queue(a.queue)
    seen = {norm_url(j.get("url", "")) for j in jobs}
    added = 0
    for c in cands:
        if norm_url(c["url"]) not in seen:
            jobs.append(c)
            seen.add(norm_url(c["url"]))
            added += 1
    save_queue(a.queue, jobs)
    print("appended %d new candidates to %s" % (added, a.queue))


if __name__ == "__main__":
    main()
