# Local application progress from Gmail

`application_progress.py` reads Gmail search-result metadata (subject, sender, snippet and date), matches only unambiguous employer/job records, and writes an idempotent, local JSON and CSV event log. It never sends email or stores message bodies. Unmatched status-looking messages go to a local `-unmatched.json` review queue; ambiguous messages are **not** assigned to an application. It does not change the application submission log or infer that an application was submitted from an email.

```bash
python3 application_progress.py \
  --applications /path/to/private/applications-log.json \
  --out-prefix /path/to/private/application-progress \
  --live
```

`--live` requires Hermes Google Workspace email OAuth and the bundled `google_api.py`. Before authentication, use `--messages /path/to/gmail-search-results.json` to test or import a previously retrieved result array. The script exits without writing progress when live Gmail access fails. Review results manually for context (especially status changes). The Gmail search is bounded to 500 messages after 2024-01-01; older or beyond-limit messages need a separate backfill.

Keep all private paths **outside the public repository**. `applications-log.json`, `applications-log.csv`, `applied-applications.csv`, and `application-progress*` are gitignored as an additional safeguard.
