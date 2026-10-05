# Troubleshooting

## Browser connection

**`cdp_driver.py` cannot connect.**
- Verify Chrome is running with `--remote-debugging-port=<port>` and
  `--remote-allow-origins='*'`.
- If using the SSH lane, check `JOBPILOT_SSH_HOST` is reachable and the
  key is loaded. The driver copies itself to the browser host on first
  use; watch for scp errors.
- If using the direct lane (`JOBPILOT_CDP_URL=host:port`), the Chrome
  must be reachable from the machine running the harness, not just from
  the browser host.

**Commands hang or time out.**
- A wedged renderer tab (heavy JS page open for hours) can deadlock the
  page-target websocket. Close or refresh tabs between batches.
- Never mix headless and headful on the same Chrome profile; it corrupts
  the profile's Preferences and later launches die on startup.
- "Profile in use" on launch: the default profile is already open in a
  normal (non-debug) Chrome. Close it first, then relaunch with
  `--remote-debugging-port`. Chrome restores the previous tabs.

**Only one model loaded at a time.**
- The harness probes `GET /v1/models` and prefers whichever chain model
  is actually loaded. Do not load two models at once; unload before
  loading the next. If the server is down, `map.py` raises: start the
  server yourself (LM Studio or Ollama) before the run rather than
  expecting the harness to recover.

## Model

**`map.py` returns invalid JSON.**
- The model must support strict JSON output. If it does not, the harness
  falls back through `JOBPILOT_BACKUP_MODELS`. Check the model server is
  up (`GET /v1/models` on `JOBPILOT_MODEL_URL`).
- Some models need the schema repeated; see `docs/ats-notes.md` for
  per-model notes.

**No model available.**
- The harness can still run the deterministic parts: `schema_dump.py`
  needs only the browser, and `fill.py` needs only a map. Write the map
  by hand (see the strict-JSON schema in `map.py`) to run fully offline.

## Forms

**Fields are skipped that should be filled.**
- The harness skips anything with no safe answer rather than guessing.
  Add the missing key to `facts.json` (see `docs/facts-schema.md`).
- For screening questions, check `hard_patterns.py` and `templates.py`;
  add a pattern if the question is a known-safe one.

**Upload fails.**
- `resume_path` must be readable from the browser host, not just the
  machine running the harness. In SSH mode the file is copied over;
  in direct mode it must already exist at that path on the browser host.

**CAPTCHA or bot challenge.**
- The harness stops instead of attempting a solve. This is intentional.
  Complete the challenge manually in the debug Chrome, then re-run with
  `--no-submit` to verify the fill before submitting.

## Greenhouse code gate

**"Invalid security code."**
- Codes are per-application and expire quickly. Use the code from the
  most recent email for that specific application, not an older one.
- There is no resend control on the gate page. If the code is lost,
  submit the form again to trigger a fresh code email.
- Never reuse a code from a different application.

## CI

**Build fails on `facts.json`.**
- The CI guard fails if `facts.json` is tracked. You committed the real
  file. Remove it from git (`git rm --cached facts.json`) and check
  `.gitignore`.
