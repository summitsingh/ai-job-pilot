# Machine setup: visible browsers, one per lane

Every lane drives exactly one Chrome. The rules below exist because
violating any of them caused real failures: shared browsers wedged
each other's tabs, headless browsers drew extra bot scrutiny, and
temp profiles lost sessions.

## Rules

1. One dedicated Chrome per lane: its own remote-debugging port and
   its own profile directory. Never share a browser between two lanes.
2. Always visible on the machine's main screen. Never headless.
   Headless is only marginally faster at rendering (not the
   bottleneck), and in our experience it draws more bot scrutiny.
3. Use a persistent, dedicated profile per lane (e.g.
   `~/.chrome-jobpilot-9445`), signed into the applicant's accounts
   once. Fresh/temp profiles lose sessions on every launch, and two
   lanes cannot share one profile directory (Chrome locks it).
4. Never mix headless and headful on one profile: a headless run can
   corrupt the profile's Preferences and kill later headful launches.
5. Keep the browser window visible and tiled so the operator can watch
   and take over. (Tiling itself is environment-specific; one proven
   method is CDP `Browser.setWindowBounds` per target.)

## Port topology pattern

Two application browsers per machine is the proven shape. Ports ascend
from a base port (default 9445):

| Machine | Port | Lane |
|---|---|---|
| `<MACHINE_A>` | 9445 | Greenhouse |
| `<MACHINE_A>` | 9446 | Ashby / Lever / Wellfound / direct |
| `<MACHINE_B>` | 9445 | Greenhouse |
| `<MACHINE_B>` | 9446 | Ashby / Lever / Wellfound / direct |

(The quick-start examples elsewhere use port 9226 for a single ad-hoc
run; lanes use the topology above.) A third browser opens only while
actively fixing an issue, then closes. Model inference (if any) runs
on its own machine, separate from the browsers.

## Launching: the easy path

```bash
python3 launch_browsers.py
# How many Chrome browsers do you want to open for job applications? (recommended: 2)
```

The launcher (console script `ai-job-pilot-launch`) validates the
count, checks each port is free, assigns lane 1 to Greenhouse and lane
2 to other boards (lane 3+ asks for a purpose), applies the per-OS
recipe below, polls `127.0.0.1:<port>/json/version` until each
instance answers, and prints a summary table. It fails loudly if any
instance does not come up.

Non-interactive:

```bash
python3 launch_browsers.py --lanes 2 --base-port 9445
python3 launch_browsers.py --lanes 3 --purpose greenhouse --purpose other \
    --purpose research --dry-run   # print the plan only
```

## Manual recipes (what the launcher does under the hood)

### macOS

```bash
/Applications/Google\ Chrome.app/Contents/MacOS/Google\ Chrome \
  --user-data-dir="$HOME/.chrome-jobpilot-9445" \
  --remote-debugging-port=9445 \
  --remote-allow-origins='http://127.0.0.1:*' \
  --no-first-run --no-default-browser-check \
  --password-store=basic &
```

Repeat with a different `--user-data-dir` and port per lane.

### Linux

```bash
google-chrome \
  --user-data-dir="$HOME/.chrome-jobpilot-9445" \
  --remote-debugging-port=9445 \
  --remote-allow-origins='http://127.0.0.1:*' \
  --no-first-run --no-default-browser-check \
  --password-store=basic &
```

Over SSH, strip any stale D-Bus address first or Chrome's network
service can deadlock:
`env -u DBUS_SESSION_BUS_ADDRESS google-chrome ...`

### Windows (visible on the main screen)

`Win32_Process.Create` lands in session 0 (invisible). Launch in the
interactive console session instead, via a scheduled task with `/IT`:

```cmd
schtasks /create /tn jobpilot-9445 /tr "C:\jobpilot\chrome-9445.cmd" /sc once /st 23:59 /it /f
schtasks /run /tn jobpilot-9445
schtasks /delete /tn jobpilot-9445 /f
```

where `chrome-9445.cmd` holds the Chrome command line with the same
flags as above. Notes:

- WMI mangles double quotes inside paths with spaces; use a
  space-free profile dir like `C:\chrome-jobpilot-9445`, or the 8.3
  short path (`C:\Users\<USER~1>\...`) with quotes.
- Chrome's lockfile in the profile dir tells whether that profile is
  already in use; check before launching a second instance on it.

## Security note

The debug port plus `--remote-allow-origins` exposes full browser
control. Bind to loopback (the recipes above only listen on
`127.0.0.1`), and reach a remote browser host over an SSH tunnel,
never by exposing the port to the network. The launcher restricts
origins to `http://127.0.0.1:*` for the same reason.

## Driving the browser

The repo's `cdp_direct.py` is stdlib-only: no venv needed on the
browser host. Commands: `reset`, `goto`, `newtab`, `eval`, `evalb64`,
`text`, `shot`, `click`, `cookies`, `url`, `wait`, `info`.

```bash
python3 cdp_direct.py 127.0.0.1:9445 reset
python3 cdp_direct.py 127.0.0.1:9445 goto "$(echo -n 'https://example.com' | base64)"
```

(`goto` takes the URL base64-encoded; use `evalb64` the same way for
JavaScript containing quotes.)

Before every lane run: `reset` (fresh tab, closes wedged ones).
A tab that stops responding to CDP will hang every later command;
resetting first is the cheapest fix.

React-select style custom dropdowns: mouse clicks often fail.
Focus the control then send ArrowDown/Enter via keyboard instead
(see `docs/greenhouse-quirks.md`).

## Env wiring per lane

```bash
export JOBPILOT_CDP_URL="127.0.0.1:9445"
export JOBPILOT_MODEL_URL="http://<MODEL_HOST>:1234"
export JOBPILOT_FACTS="$HOME/jobpilot/facts.json"   # cp facts.example.json facts.json first
export JOBPILOT_QUEUE="$HOME/jobpilot/queue.json"
export JOBPILOT_CLAIMS="$HOME/jobpilot/claims.jsonl"
export JOBPILOT_LANE="<MACHINE_A>:9445"
export TRACKER_BACKENDS="jsonl,sheets"
python3 lane_greenhouse.py --workdir /tmp/jobpilot/lane-9445 --no-submit
```

`lane_greenhouse.py` also reads `config.json` (copy of
`config.example.json`) for the same keys; env vars override the file,
which overrides built-in defaults (see `config.py`). The workdir is
auto-created; note `/tmp` is cleared on reboot, which loses
screenshots and `result.json`, so point it somewhere durable for
records you need to keep.

The Greenhouse email code gate needs a human at the keyboard: when
the lane hits it, it marks the job `blocked` with a `code-gate`
reason and exits. Enter the code (`code_gate.py`), then return the
job to the queue with `python3 job_queue.py requeue <url>`.

All placeholders (`<MACHINE_A>`, `<MODEL_HOST>`) are yours to fill in;
the repo never carries real hostnames, IPs, or account names.
