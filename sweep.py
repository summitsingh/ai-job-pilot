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
  - fuzzy dedup: withholds the same role reposted under a different URL
    or board (same company, near-identical title, same location) and
    lists the pair for human review (see near_duplicate)

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
import unicodedata
import tempfile
from collections import defaultdict

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
    "do not sponsor", "doesn't sponsor", "not offer visa sponsorship",
    "not provide visa sponsorship", "sponsorship not available",
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


def sponsorship_ok(description, require_positive=False):
    dl = (description or "").lower().replace("\n", " ")
    if any(p in dl for p in SPONSOR_NO):
        return False
    if re.search(r"\bno\b.{0,25}\bopt\b", dl):
        return False
    if require_positive:
        return bool(re.search(
            r"\b(?:visa sponsorship|sponsorship (?:available|provided|offered|supported)|"
            r"sponsor (?:visas?|candidates|employees)|h[ -]?1b (?:transfer|sponsorship)|"
            r"visa (?:support|transfer))\b", dl))
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
    for e in load_history(applied_path):
        if isinstance(e, str):
            u = e
        elif isinstance(e, dict):
            u = e.get("job_url") or e.get("url") or ""
        else:
            continue
        if u:
            urls.add(norm_url(u))
    return urls


def load_history(applied_path):
    """Read JSON/JSONL history without retaining applicant answers."""
    if not applied_path or not os.path.exists(applied_path):
        return []
    with open(applied_path) as f:
        text = f.read().strip()
    if not text:
        return []
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
    return entries


def fingerprint(posting):
    """Only normalized role identity and source URL belong in history."""
    return {"company": normalize_company(posting.get("company")),
            "title": " ".join(title_tokens(posting.get("title"))),
            "location": normalize_location(posting.get("location")),
            "url": posting.get("url") or posting.get("job_url") or ""}


# --- fuzzy dedup -----------------------------------------------------------
# Thresholds (kept here so they are easy to audit and tune):
#   company:  normalized strings must be EQUAL
#   title:    Jaccard similarity of meaningful token sets >= TITLE_JACCARD
#   location: normalized strings must be EQUAL
# All three must hold. Fail-closed note: a near-duplicate NEVER auto-merges
# into the applied log and is never silently dropped; it is only withheld
# from the queue and reported in stats["fuzzy_dup_review"] so a human
# decides whether it is really the same role.
TITLE_JACCARD = 0.8

COMPANY_SUFFIXES = {"inc", "incorporated", "llc", "ltd", "limited", "corp",
                    "corporation", "co", "company", "gmbh", "plc", "lp",
                    "llp", "pbc"}
# Seniority remains part of identity, even for otherwise similar titles.
SENIORITY_WORDS = {"senior", "sr", "staff", "lead", "principal", "junior",
                   "jr", "mid", "associate", "entry", "i", "ii", "iii",
                   "iv", "v"}
TITLE_STOPWORDS = {"a", "an", "and", "the", "of", "for", "to", "in", "at",
                   "on", "with", "or", "remote", "hybrid", "us", "usa"}


def _tokens(text):
    folded = unicodedata.normalize("NFKD", (text or "").casefold())
    folded = "".join(c for c in folded if not unicodedata.combining(c))
    return re.findall(r"[^\W_]+(?:\+\+|#)?", folded)


def normalize_company(company):
    """Lowercase, drop punctuation and trailing legal suffixes."""
    toks = _tokens(company)
    while len(toks) > 1 and toks[-1] in COMPANY_SUFFIXES:
        toks.pop()
    return " ".join(toks)


def title_tokens(title):
    """Sorted meaningful title tokens, preserving levels and C++/C#."""
    aliases = {"sr": "senior", "jr": "junior"}
    return sorted({aliases.get(t, t) for t in _tokens(title)
                   if t not in TITLE_STOPWORDS})


US_STATES = dict(zip(
    "al ak az ar ca co ct de fl ga hi id il in ia ks ky la me md ma mi mn ms "
    "mo mt ne nv nh nj nm ny nc nd oh ok or pa ri sc sd tn tx ut vt va wa "
    "wv wi wy dc".split(),
    ("Alabama|Alaska|Arizona|Arkansas|California|Colorado|Connecticut|"
     "Delaware|Florida|Georgia|Hawaii|Idaho|Illinois|Indiana|Iowa|Kansas|"
     "Kentucky|Louisiana|Maine|Maryland|Massachusetts|Michigan|Minnesota|"
     "Mississippi|Missouri|Montana|Nebraska|Nevada|New Hampshire|New Jersey|"
     "New Mexico|New York|North Carolina|North Dakota|Ohio|Oklahoma|Oregon|"
     "Pennsylvania|Rhode Island|South Carolina|South Dakota|Tennessee|"
     "Texas|Utah|Vermont|Virginia|Washington|West Virginia|Wisconsin|"
     "Wyoming|District of Columbia").lower().split("|")))


def normalize_location(location):
    """Normalize punctuation and a trailing US state name/abbreviation."""
    text = " ".join(_tokens(location))
    for short, full in US_STATES.items():
        if text == full:
            return short
        if text.endswith(" " + full):
            return text[:-len(full)] + short
    return text


def _jaccard(a, b):
    a, b = set(a), set(b)
    if not a or not b:
        return 0.0
    return len(a & b) / len(a | b)


def _role_key(posting):
    tokens = frozenset(title_tokens(posting.get("title")))
    return (normalize_company(posting.get("company")),
            normalize_location(posting.get("location")),
            frozenset(tokens & SENIORITY_WORDS), tokens)


def _same_role(a, b):
    languages_a = {t for t in a[3] if t.endswith(("++", "#"))}
    languages_b = {t for t in b[3] if t.endswith(("++", "#"))}
    return bool(a[0] and a[:3] == b[:3] and languages_a == languages_b
                and _jaccard(a[3], b[3]) >= TITLE_JACCARD)


def near_duplicate(a, b):
    """Same company, location and level, with near-identical title tokens."""
    return _same_role(_role_key(a), _role_key(b))


def find_duplicates(candidates):
    """Index pairs (i, j), i < j, using precomputed role buckets."""
    buckets = defaultdict(list)
    pairs = []
    for j, candidate in enumerate(candidates):
        key = _role_key(candidate)
        for i, earlier in buckets[key[:3]]:
            if _same_role(earlier, key):
                pairs.append((i, j))
        buckets[key[:3]].append((j, key))
    return sorted(pairs)


def filter_postings(raw, applied_urls=(), min_salary=100000,
                    source="sweep", now=None, history=(), require_sponsorship=False):
    """Apply all filters; return (candidates, stats). Pure.

    applied_urls: normalized URLs already touched (from the tracker).
    now: ISO timestamp for queued_at; defaults to current UTC time.

    Near-duplicates of an already-accepted candidate are not queued;
    stats["fuzzy_dup"] counts them and stats["fuzzy_dup_review"] lists
    (accepted_url, withheld_url) pairs for human review.
    """
    stats = {"scanned": len(raw), "title_fail": 0, "loc_fail": 0,
             "sponsor_fail": 0, "salary_fail": 0, "dup": 0, "no_url": 0,
             "fuzzy_dup": 0, "fuzzy_dup_review": []}
    cands = []
    buckets = defaultdict(list)
    for previous in history:
        if isinstance(previous, dict):
            key = _role_key(previous)
            buckets[key[:3]].append((previous, key))
    seen = set()
    history_urls = set()
    url_less_names = set()
    for past in history:
        if not isinstance(past, dict):
            continue
        past_url = norm_url(past.get('url') or past.get('job_url') or past.get('Job URL') or '')
        if past_url:
            history_urls.add(past_url)
        else:
            url_less_names.add((normalize_company(past.get('company') or past.get('Company')),
                                tuple(title_tokens(past.get('title') or past.get('Title')))))
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
        if (nu in applied_urls or nu in seen or nu in history_urls or
                (url_less_names and
                 (normalize_company(j.get('company')),
                  tuple(title_tokens(j.get('title')))) in url_less_names)):
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
        if not sponsorship_ok(j.get("description"), require_sponsorship):
            stats["sponsor_fail"] += 1
            continue
        if not salary_ok(j.get("salary_min"), j.get("salary_max"),
                         min_salary):
            stats["salary_fail"] += 1
            continue
        smin, smax = j.get("salary_min"), j.get("salary_max")
        salary = ("%s - %s" % (smin, smax)).strip(" -") \
            if (smin or smax) else "salary undisclosed"
        cand = {
            "company": j.get("company", ""), "title": j.get("title", ""),
            "location": loc, "salary": salary, "url": url,
            "ats": ats_of(url), "notes": j.get("notes", ""),
            "queued_at": now, "source": source, "status": "pending",
            "skip_reason": "",
        }
        key = _role_key(cand)
        twin = next((c for c, previous_key in buckets[key[:3]]
                     if _same_role(previous_key, key)), None)
        if twin is not None:
            stats["fuzzy_dup"] += 1
            stats["fuzzy_dup_review"].append((twin.get("url") or twin.get("job_url") or "", url))
            continue
        cands.append(cand)
        buckets[key[:3]].append((cand, key))
    return cands, stats


def save_review(stats, workdir):
    """Save posting URL pairs for human review, including dry-run evidence."""
    if stats["fuzzy_dup_review"]:
        os.makedirs(workdir, exist_ok=True)
        # Unique files preserve each sweep's review evidence. The default is
        # outside the checkout; repo-local workdir/ is already gitignored.
        fd, review_path = tempfile.mkstemp(prefix="sweep-review-", suffix=".json",
                                           dir=workdir)
        with os.fdopen(fd, "w") as f:
            json.dump({"fuzzy_dup_review": stats["fuzzy_dup_review"]}, f, indent=1)
        stats["review_file"] = review_path


def main():
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--in", dest="inp", required=True,
                    help="JSON list of raw postings")
    ap.add_argument("--applied", default="",
                    help="applied log (JSONL or JSON list) for dedup")
    ap.add_argument("--queue", default="",
                    help="append candidates to this queue file")
    ap.add_argument("--fingerprints", default="",
                    help="persistent role history (default: queue stem-fingerprints.jsonl)")
    ap.add_argument("--min-salary", type=float, default=100000)
    ap.add_argument("--require-sponsorship", action="store_true",
                    help="only queue postings explicitly offering visa sponsorship")
    ap.add_argument("--source", default="sweep")
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--workdir", default="/tmp/jobpilot",
                    help="duplicate review artifacts (default: /tmp/jobpilot)")
    a = ap.parse_args()

    with open(a.inp) as f:
        raw = json.load(f)
    if isinstance(raw, dict):
        raw = raw.get("jobs", raw.get("postings", []))
    applied = load_applied_urls(a.applied)
    history_path = a.fingerprints or (os.path.splitext(a.queue)[0] +
                                    "-fingerprints.jsonl" if a.queue else "")
    history = [e for e in load_history(a.applied) if isinstance(e, dict)]
    if a.dry_run or not a.queue:
        history += load_history(history_path)
        if a.queue:
            from job_queue import load_queue
            history += load_queue(a.queue)
        cands, stats = filter_postings(raw, applied, a.min_salary, a.source,
                                      history=history,
                                      require_sponsorship=a.require_sponsorship)
        stats["candidates"] = len(cands)
        save_review(stats, a.workdir)
        print(json.dumps(stats, indent=1))
        return
    from job_queue import Store, load_queue, save_queue
    # Keep queue reads, history reads, append and save in one transaction.
    # All writers take the queue lock before the history lock.
    with Store(a.queue), Store(history_path):
        jobs = load_queue(a.queue)
        fingerprints = load_history(history_path)
        history += fingerprints + jobs
        cands, stats = filter_postings(raw, applied, a.min_salary, a.source,
                                      history=history,
                                      require_sponsorship=a.require_sponsorship)
        seen = {norm_url(j.get("url", "")) for j in jobs}
        additions = [c for c in cands if norm_url(c["url"]) not in seen]
        jobs.extend(additions)
        # Save fingerprints first: a failed queue save must fail closed.
        recorded = {norm_url(e.get("url", "")) for e in fingerprints
                    if isinstance(e, dict)}
        with open(history_path, "a") as f:
            for entry in history + additions:
                if not isinstance(entry, dict):
                    continue
                record = fingerprint(entry)
                identity = norm_url(record["url"])
                if identity and identity not in recorded:
                    f.write(json.dumps(record) + "\n")
                    recorded.add(identity)
            f.flush()
            os.fsync(f.fileno())
        save_queue(a.queue, jobs)
    save_review(stats, a.workdir)
    added = len(additions)
    stats["candidates"] = len(cands)
    print(json.dumps(stats, indent=1))
    print("appended %d new candidates to %s" % (added, a.queue))


if __name__ == "__main__":
    main()
