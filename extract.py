#!/usr/bin/env python3
"""Step 0: extract structured data from a job posting via the local model.

Replaces Muse page reads during role discovery. Input is posting text
(from schema_dump, browser text, or search-result snippets) plus the URL.

Usage: extract.py --text posting.txt --url <posting-url> [--out extract.json]
Output: {"title","company","salary_min","salary_max","salary_raw",
         "location_raw","locations","work_mode","sponsorship_quotes",
         "sponsorship_signal","requirements","seniority","notes"}

Rules enforced by prompt: never invent values; null/empty when not stated.
Sponsorship wording is QUOTED verbatim, never interpreted (Muse interprets).
"""
import argparse
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import model

SYSTEM = """You extract structured data from a job posting. Output ONLY strict JSON matching the schema. Rules:
- Copy values verbatim from the posting; never invent, infer, or embellish.
- If a field is not stated, use null (strings) or [] (lists). Never use "N/A" or "unknown" strings.
- salary_min/salary_max: integers in USD annual base. Parse "$180K-$220K" -> 180000, 220000. Hourly/monthly/stock: leave min/max null and put the raw text in salary_raw.
- work_mode: one of "remote", "hybrid", "onsite". Use null if not stated.
- sponsorship_quotes: VERBATIM quotes of every sentence mentioning sponsorship, visas, work authorization, citizenship, or green cards. Empty list if none.
- sponsorship_signal: "explicit_no" ONLY if the posting clearly excludes sponsorship (e.g. "no sponsorship", "US citizens only"); "likely_ok" if it affirmatively mentions sponsorship/transfer support; otherwise "unclear".
- requirements: list of the posting's stated must-have requirements (short strings, max 12). Empty list if none listed.
- seniority: "junior", "mid", "senior", "staff", "principal", or null.
- notes: anything odd (e.g. "salary listed in GBP", "multiple locations with different bands")."""

SCHEMA = {
    "type": "object",
    "properties": {
        "title": {"type": ["string", "null"]},
        "company": {"type": ["string", "null"]},
        "salary_min": {"type": ["integer", "null"]},
        "salary_max": {"type": ["integer", "null"]},
        "salary_raw": {"type": ["string", "null"]},
        "location_raw": {"type": ["string", "null"]},
        "locations": {"type": "array", "items": {"type": "string"}},
        "work_mode": {"type": ["string", "null"]},
        "sponsorship_quotes": {"type": "array", "items": {"type": "string"}},
        "sponsorship_signal": {"type": "string"},
        "requirements": {"type": "array", "items": {"type": "string"}},
        "seniority": {"type": ["string", "null"]},
        "notes": {"type": ["string", "null"]},
    },
    "required": ["title", "company", "salary_min", "salary_max",
                 "salary_raw", "location_raw", "locations", "work_mode",
                 "sponsorship_quotes", "sponsorship_signal",
                 "requirements", "seniority", "notes"],
    "additionalProperties": False,
}


def extract(text, url):
    ok, info = model.probe()
    if not ok:
        raise RuntimeError(f"model unreachable: {info}")
    user = f"POSTING URL: {url}\n\nPOSTING TEXT (truncated at 12000 chars):\n{text[:12000]}"
    return model.chat(SYSTEM, user, "extract", SCHEMA,
                      max_tokens=1500, temperature=0.0)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--text", required=True, help="file with posting text")
    ap.add_argument("--url", required=True)
    ap.add_argument("--out", default="")
    a = ap.parse_args()
    with open(a.text) as f:
        text = f.read()
    r = extract(text, a.url)
    s = json.dumps(r, indent=1)
    if a.out:
        with open(a.out, "w") as f:
            f.write(s)
    print(s)


if __name__ == "__main__":
    main()
