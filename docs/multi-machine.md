# Multi-machine operation

jobpilot's harness is split into two roles: the machine that runs the
Python (the "controller") and the machine whose Chrome actually fills
forms (the "browser host"). They can be the same box, or you can drive
several browser hosts in parallel from one controller.

## Browser host setup

Each browser host runs one or more visible Chrome instances with a
remote-debugging port:

```bash
# macOS (detached, survives SSH drops)
nohup /Applications/Google\ Chrome.app/Contents/MacOS/Google\ Chrome \
  --remote-debugging-port=9444 --remote-allow-origins='*' \
  --no-first-run --no-default-browser-check >/tmp/chrome-9444.log 2>&1 &
```

```bash
# Linux (detached, survives SSH drops)
nohup google-chrome \
  --remote-debugging-port=9444 --remote-allow-origins='*' \
  --no-first-run --no-default-browser-check >/tmp/chrome-9444.log 2>&1 &
```

```powershell
# Windows (detached via a scheduled task so it survives the SSH session)
schtasks /create /tn ChromeDbg9334 /tr "\"C:\Program Files\Google\Chrome\Application\chrome.exe\" --remote-debugging-port=9334 --remote-allow-origins=* --no-first-run --no-default-browser-check" /sc once /st 23:59 /f
schtasks /run /tn ChromeDbg9334
```

Verify with `curl http://127.0.0.1:9444/json/version` on the host.

> **Warning:** `--remote-allow-origins='*'` on a browser holding your
> real logins (the default profile) exposes CDP to the network. Keep the
> debug port on localhost and reach it over an SSH tunnel; never expose
> it to a LAN or the internet.

## Use the default Chrome profile

Launch Chrome WITHOUT `--user-data-dir` so it uses the user's default
profile. Temp/fresh profiles lose saved sessions on every restart, which
means re-logging into every site and re-doing any human verification
each run. The default profile keeps real logins (Gmail, ATS portals)
across restarts.

One hard constraint: Chrome refuses to open the same profile twice. If
the user's normal Chrome is already running on the default profile,
close it first, then relaunch with `--remote-debugging-port`. Chrome
restores the previous session's tabs on relaunch, so nothing is lost.
Do NOT launch a second instance on the same profile directory.

> **Note:** recent Chrome versions may ignore `--remote-debugging-port`
> when running on the default data directory. If
> `http://127.0.0.1:PORT/json/version` does not respond after launch,
> fall back to an explicit `--user-data-dir` (a copy of the profile)
> instead.

## As many instances as you need

There is no one-browser-per-machine rule. Open a separate debug Chrome
per lane (e.g. one for Indeed, one for ATS boards, one for
human-in-the-loop code gates), each on its own port. Partition work
across instances so the same posting is never attempted twice, and keep
per-site pacing sane on each machine.

## Tab hygiene

A wedged renderer tab (a heavy JS page left open for 10+ hours) can
deadlock the page-target websocket and stall ALL CDP commands on that
instance. Close or refresh tabs between batches; never leave heavy
pages open for hours. See `docs/troubleshooting.md`.

## Never mix headless and headful on one profile

A headless launch on a profile writes to its `Preferences` in a way that
can make later headful launches crash on startup. A profile used
headfully must never be launched headless, not even as a fallback.
Pick one mode per profile and keep it.

## The model server is shared infrastructure

One model server serves all machines. Run it on whichever box has the
GPU/RAM (LM Studio exposes an OpenAI-compatible API; Ollama is the
backup) and point every controller at it with `JOBPILOT_MODEL_URL`.

The pipeline is local-model-first by design: `map.py` makes one
strict-JSON model call per form and raises if the server is unreachable,
so keep the model server up before a run. Only one model needs to be
loaded at a time; the probe in `model.chat()` prefers whichever chain
model is actually loaded. The deterministic `hard_patterns.py` /
`templates.py` are a separate offline path for template testing (see
`test_templates.py`), not an automatic fallback inside the fill
pipeline.

## Dedup across machines

When several machines apply in parallel, one component must own
dedup. `batch_apply.py --dedup` skips posting URLs already present in
the applications log, so a queue can be re-run safely, but the log
itself must be shared (or merged) across machines. Never let two lanes
write to independent logs for the same employer. Note the remaining
race: the dedup set is loaded once at startup, so two lanes started at
the same time can both accept the same URL. For strict guarantees, run
one queue owner (or a file lock) rather than two concurrent writers.
