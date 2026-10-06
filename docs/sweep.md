# Sweep: discovery to queue

Discovery (finding postings) is source-specific and intentionally not
in this repo: every job board has its own API, HTML, and rate limits,
and scrapers rot fast. What IS in the repo is the part that stays
stable: the vetting funnel.

## The funnel

```
raw postings (JSON list, your source)
  -> sweep.py filter
  -> queue.json (job_queue.py schema)
  -> lane_greenhouse.py claims and works each Greenhouse job
     (other ATS jobs: ai-job-pilot / batch_apply.py, see docs/queue.md)
  -> tracker.py records the outcome
  -> next sweep dedups against the tracker (--applied)
```

## sweep.py

Takes a JSON list of raw postings:

```json
[
  {"company": "Acme", "title": "Senior Backend Engineer",
   "location": "Austin, TX", "url": "https://...",
   "description": "...", "salary_min": 180000, "salary_max": 220000,
   "workplace": "onsite"}
]
```

and applies, in order:

1. **Title** (`TITLE_LEVEL`, `TITLE_ROLE`, `TITLE_NO` in `sweep.py`):
   must be senior-level (senior, sr, staff, lead, principal) AND a
   backend/platform/infra/AI/ML role. Rejects frontend-only,
   mobile-only, junior, intern, and manager-track titles. Note: "lead"
   is accepted as a seniority marker, so a "Team Lead" posting with
   management duties can pass; check the description before queueing.
   These are hard-coded defaults tuned for one applicant's search;
   edit the regex constants to retune.
2. **Location** (`LOC_OK`, `LOC_NONUS`): allowed metros are Seattle,
   Bay Area (incl. Peninsula/South Bay), Los Angeles, NYC, Austin,
   and nearby cities (Redmond, Bellevue, Kirkland, Irvine, Oakland,
   Berkeley, Fremont, San Diego, Santa Monica), or Remote. Rejects
   non-US locations. A bare "Remote" passes with its original text;
   only an explicit US marker normalizes it to "Remote (US)".
3. **Sponsorship language** (`SPONSOR_NO`): rejects postings whose
   text rules out visa sponsorship ("no sponsorship", "US citizens
   only", ...). This filter exists for applicants who need
   sponsorship; if you do not, delete or bypass the check.
4. **Salary floor** (`--min-salary`, default 100000): rejects postings
   whose stated max is below the floor. String amounts like "$90,000"
   and "90k" are parsed. Undisclosed salary passes.
5. **Dedup**: rejects URLs already in the applied log
   (`--applied`, default none; pass the same
   `./applications.jsonl` the tracker writes) and duplicates within
   the batch.

```bash
python3 sweep.py --in raw.json --applied applications.jsonl \
  --queue queue.json --min-salary 100000 --source "sweep 2026-01-15"
python3 sweep.py --in raw.json --dry-run   # stats only, no writes
```

`--queue` defaults to `./queue.json`. Output is queue-shaped job dicts
(see `docs/queue.md`), appended with dedup against what is already
queued.

All filter functions (`title_ok`, `location_ok`, `sponsorship_ok`,
`salary_ok`, `parse_salary`, `load_applied_urls`, `filter_postings`)
are pure and covered by the offline test suite, so tuning the policy
is safe. `filter_postings` takes an optional `now` timestamp to keep
it deterministic.

## Writing a discovery source

Keep sources small and read-only: fetch, normalize to the posting
shape above, save raw JSON. Do NOT filter in the fetcher; let
`sweep.py` own the policy so every source is judged by the same
rules. Proven source shapes:

- A JSON API you can query with `urllib` (paged, with a polite
  User-Agent and small sleeps between pages).
- Greenhouse board pages:
  `https://boards-api.greenhouse.io/v1/boards/<board>/jobs?content=true`
  returns JSON, no scraping needed. The `?content=true` matters: without
  it there is no description, and the sponsorship filter silently
  passes everything.
- Ashby:
  `https://api.ashbyhq.com/posting-api/job-board/<board>?includeCompensation=true`
  returns the board's postings as JSON (add the compensation flag if
  salary matters).
