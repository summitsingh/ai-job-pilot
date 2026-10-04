# jobpilot

Deterministic, low-token autofill for ATS job application forms (Greenhouse,
Ashby, Lever). Fill a form with ~50x fewer LLM calls and ~99% fewer tokens
than a naive agent loop, by doing everything deterministic except the one
step that actually needs a model.

## How it works

```
posting URL -> schema_dump -> map -> fill -> verify -> submit
```

1. **schema_dump.py** - loads the posting in a debug-Chrome browser and dumps
   every form field (label, type, options, required) as JSON.
2. **map.py** - a local model maps each field to an answer from your
   `facts.json`, using strict JSON schema output. One model call per form.
   Fields with no safe answer map to `skip`.
3. **hard_patterns.py / templates.py** - deterministic pattern matchers that
   answer the long tail of screening questions (work auth, demographics,
   salary, education, yes/no willingness) with zero model calls. First
   matching pattern wins; unknown fields are skipped, never guessed.
4. **fill.py** - applies the field map through CDP: batched JS for text
   fields, real clicks for selects/radios/uploads. Reads every field back
   and diffs against expected values.
5. **verify.py / submit_only.py** - confirm the fill, then submit.

The one hard rule: the harness never invents facts. Anything not in
`facts.json` is skipped.

## Quick start

```bash
cp facts.example.json facts.json   # then fill in YOUR details
# serve a local model (LM Studio default http://127.0.0.1:1234),
# or point JOBPILOT_MODEL_URL at any OpenAI-compatible endpoint
export JOBPILOT_SSH_HOST="user@browser-host"   # host running debug Chrome
python3 ats_fill.py --url <greenhouse|ashby|lever posting URL> \
    --port 9226 --no-submit
```

`--no-submit` fills and verifies without submitting. Drop it to actually
submit. Every run emits `result.json` with `{status, confirmation_evidence,
fields_filled, fields_skipped, notes}`.

## Configuration

| Variable | Default | Purpose |
|---|---|---|
| `JOBPILOT_SSH_HOST` | (required) | ssh destination of the browser host |
| `JOBPILOT_SSH_CMD` | `ssh` | ssh command |
| `JOBPILOT_SCP_CMD` | `scp` | scp command (copies the CDP driver) |
| `JOBPILOT_CDP_URL` | (unset) | `host:port` of a directly reachable debug Chrome; when set, the driver runs locally |
| `JOBPILOT_HOST_SSH` | (unset) | ssh helper for browser-host file checks in direct mode |
| `JOBPILOT_MODEL_URL` | `http://127.0.0.1:1234` | model server base URL |
| `JOBPILOT_API_FLAVOR` | `openai` | `openai` (LM Studio) or `ollama` (native API) |
| `JOBPILOT_MODEL_NAME` | `qwen/qwen3-coder-next` | primary model id |
| `JOBPILOT_BACKUP_MODELS` | (see model.py) | comma-separated fallback model ids |

Legacy `ATS_*` names for these variables are also honored.

## Browser setup

The harness drives Chrome over the DevTools protocol. Start Chrome with
remote debugging on your browser host:

```bash
google-chrome --remote-debugging-port=9226 --remote-allow-origins='*' \
  --no-first-run --no-default-browser-check
```

`ensure_driver()` copies the stdlib-only `cdp_driver.py` to the browser
host automatically; no venv needed there.

## facts.json

All answers come from `facts.json` (gitignored; start from
`facts.example.json`). It holds identity, contact, work history,
education, work authorization, salary expectations, and screening
posture (willingness defaults, disclosure answers, demographics).
`templates.py` documents every supported key. Keep this file private.

## Layout

- `ats_fill.py` - orchestrator: dump -> map -> fill -> verify -> submit
- `extract.py`, `schema_dump.py` - form introspection
- `map.py` - model-based field-to-fact mapping (strict JSON, one call)
- `hard_patterns.py`, `templates.py` - deterministic screening answers
- `fill.py`, `set_select.py`, `set_select2.py`, `modal_common.py` - fill primitives
- `verify.py`, `submit_only.py` - verification and submission
- `cdp_driver.py`, `cdp_direct.py`, `common.py` - CDP transport
- `model.py` - local model client with fallback chain
- `indeed_dump.py`, `indeed_fill.py` - Indeed application lane
- `test_*.py` - pattern and template tests

## Notes

- Greenhouse shows an email verification code on submit; the runner pauses
  for it and resumes when you provide the code.
- CAPTCHA/reCAPTCHA detection stops the run instead of attempting a solve.
- Some employers are blocklisted from automation in the default config;
  see `modal_common.py`.

## License

MIT. See LICENSE.
