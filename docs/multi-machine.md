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
# Windows: launch via a scheduled task with /IT so Chrome opens in the
# interactive console session, visible on the main screen (WMI process
# creation lands in session 0, invisible). Write the chrome command
# line to a .cmd file first to dodge WMI quote-mangling; see
# "Windows playbook" below for the full procedure.
schtasks /create /tn ChromeDbg9334 /tr "C:\Temp\chrome9334.cmd" /sc once /st 23:59 /it /f
schtasks /run /tn ChromeDbg9334
schtasks /delete /tn ChromeDbg9334 /f
```

Verify with `curl http://127.0.0.1:9444/json/version` on the host.

> **Warning:** `--remote-allow-origins='*'` on a browser holding your
> real logins (the default profile) exposes CDP to the network. Keep the
> debug port on localhost and reach it over an SSH tunnel; never expose
> it to a LAN or the internet.

## Chrome profiles: one folder per browser

Chrome locks a profile directory to one browser process, so two debug
Chromes on the same machine cannot share the exact same folder. The
default profile suits at most one browser: if the user's normal Chrome
is already running on it, close it first, then relaunch with
`--remote-debugging-port` (Chrome restores the previous tabs, so
nothing is lost). Every additional worker gets its own
`--user-data-dir`, signed into the same Google account, so all browsers
share logins, bookmarks, and sessions. Temp/fresh profiles lose saved
sessions on every restart; prefer the default folder for one browser
and account-signed folders for the rest.

> **Note:** recent Chrome versions may ignore `--remote-debugging-port`
> when running on the default data directory. If
> `http://127.0.0.1:PORT/json/version` does not respond after launch,
> fall back to an explicit `--user-data-dir` instead.

## Browser topology: one browser per worker

Give every worker its own Chrome: its own port and its own profile
directory, as above. Never share a browser between two workers; they
slow each other down and fight over tabs. A working layout is two
application browsers per machine, plus a third opened only while
actively fixing an issue (closed afterwards). Partition work across
instances so the same posting is never attempted twice, and keep
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
Only one model needs to be loaded at a time; the probe in
`model.chat()` prefers whichever chain model is actually loaded.

The pipeline is deterministic-first: `hard_patterns.py` /
`templates.py` answer every field they recognize, backed by 500+
submitted applications of proven field data. The local model is the
backup, called at most once per form and only for fields the
deterministic pass leaves unmapped or marks low-confidence. The offline
template path is exercised by `test_templates.py`.

## Keeping browsers visible

All job browsers stay visible on the main screen, always. Headless is
only marginally faster at rendering, which is not the bottleneck (the
slow parts are network, model inference, and the CDP round trips), and
bot detection flags it. Never headless.

### Tiling windows on screen

When several debug Chromes run on one host, tile them so each is
visible. Do it over CDP (`Browser.getWindowForTarget` then
`Browser.setWindowBounds`), not AppleScript: over SSH, osascript is
denied assistive access and cannot move windows, while CDP needs no
special permission. `setWindowBounds` with `windowState: "normal"` also
restores minimized windows.

## Windows playbook

Launching a visible Chrome on Windows from SSH:

- WMI/`Win32_Process.Create` lands in session 0 (invisible). Use a
  scheduled task with `/IT` so Chrome opens in the interactive console
  session, visible on the main screen:
  write a `.cmd` with the chrome command line,
  `schtasks /create /tn <name> /tr "C:\Temp\file.cmd" /sc once /st 23:59 /it /f`,
  then `schtasks /run /tn <name>`, then delete the task.
- WMI/CIM mangles double quotes inside paths with spaces (they show as
  `\"` in the real command line and Chrome silently ignores the
  profile). Use the 8.3 short path instead, e.g.
  `C:\Users\USERNA~1\AppData\Local\Google\Chrome\USERDA~1` (run
  `dir /x` in the parent folder to find your real short names), which
  needs no quotes.
- Remote PowerShell over OpenSSH preserves single quotes literally in
  `-File` script args; strip one layer of surrounding quotes in the
  wrapper, or write `.ps1` files locally, copy them over, and run with
  `powershell -File`.
- Chrome 154 uses a `lockfile` (not `SingletonLock`) in the profile dir
  to tell whether the default profile is already held by another
  Chrome. Check it before launching against the default profile.
- Stale invisible Chromes (e.g. leftovers in session 0 on a dead port)
  hog resources; verify `http://127.0.0.1:<port>/json/version` returns
  200 and the process is in the interactive session before trusting a
  lane.

## Dedup across machines

When several machines apply in parallel, one component must own
dedup. `batch_apply.py --dedup` skips posting URLs already present in
the applications log, so a queue can be re-run safely, but the log
itself must be shared (or merged) across machines. Never let two lanes
write to independent logs for the same employer. Note the remaining
race: the dedup set is loaded once at startup, so two lanes started at
the same time can both accept the same URL. For strict guarantees, run
one queue owner (or a file lock) rather than two concurrent writers.
