#!/usr/bin/env python3
"""Submit watchdog: detect a swallowed submit click and recover.

Failure it fixes: the lane clicks submit, but the click is swallowed (an
overlay, a dead button, a JS handler that silently returns). The URL does
not change, no confirmation appears, and no error shows. Without a watchdog
the lane would just report "unconfirmed" after a fixed sleep.

Recovery ladder (in order, stopping at the first terminal outcome):
  1. wait+recheck   settle for JOBPILOT_SUBMIT_WAIT_S, then re-poll
  2. click-normal   scroll the control into view and click it again
  3. click-js       direct .click() through JS (same as SUBMIT_JS)
  4. give up        outcome "blocked" with evidence

Re-clicks happen ONLY while the page is still unchanged (same URL, no
confirmation, no code gate, no error). If the URL moved or an error is
visible the submit was not swallowed, so the ladder stops instead of risking
a double submit (a second Greenhouse submit triggers a second code email).

Adapter (duck-typed; the lane injects a CDP-backed one, tests a fake):
  current_url() -> str
  page_text(limit) -> str
  click_submit(mode) -> dict    mode is "normal" or "js"; returns
                                {"clicked": bool, ...}

check_submit(adapter, started_url, cfg=None) -> dict:
  {"outcome": "confirmed" | "code-gate" | "blocked" | "error",
   "evidence": {...}, "ladder": ["wait+recheck", ...]}
  confirmed  confirmation text or /confirmation URL seen
  code-gate  email code gate seen (human-in-the-loop, never bypassed)
  error      a form error is visible, or a click raised / found nothing
  blocked    ladder exhausted with no confirmation (state unknown)

Config (env vars, config.json also works; env wins):
  JOBPILOT_SUBMIT_WAIT_S    settle time after a click (default 10)
  JOBPILOT_SUBMIT_RECHECKS  extra polls in the first rung (default 2)
  JOBPILOT_SUBMIT_POLL_S    seconds between polls (default 3)

Usage (library only):
  from submit_watchdog import check_submit, load_watchdog_cfg
  res = check_submit(adapter, started_url, load_watchdog_cfg())
"""
import re
import time

from config import get

# Keep in sync with CODE_GATE_RE / CONFIRM_RE in lane_greenhouse.py.
CODE_GATE_RE = re.compile(
    r"verification code was sent|8-character|security code", re.I)
CONFIRM_RE = re.compile(
    r"thank you for applying|application has been received|"
    r"successfully been received|your application was submitted", re.I)
ERROR_RE = re.compile(
    r"is required|please (correct|fix|complete)|"
    r"there (was|were) (an )?errors?|something went wrong", re.I)

TEXT_LIMIT = 6000
SNIPPET = 300


def _num(key, default, cast):
    try:
        return cast(get(key, default))
    except (TypeError, ValueError):
        return default


def load_watchdog_cfg():
    return {"wait_s": _num("JOBPILOT_SUBMIT_WAIT_S", 10, float),
            "rechecks": _num("JOBPILOT_SUBMIT_RECHECKS", 2, int),
            "poll_s": _num("JOBPILOT_SUBMIT_POLL_S", 3, float)}


def classify(url, text, started_url):
    """-> "confirmed" | "code-gate" | "error" | "moved" | "pending"."""
    if CODE_GATE_RE.search(text):
        return "code-gate"
    if CONFIRM_RE.search(text) or "/confirmation" in url:
        return "confirmed"
    if ERROR_RE.search(text):
        return "error"
    if url != started_url:
        return "moved"
    return "pending"


def _observe(adapter, started_url):
    url = str(adapter.current_url() or "")
    text = str(adapter.page_text(TEXT_LIMIT) or "")
    return classify(url, text, started_url), url, text


def _settle(adapter, started_url, seconds, poll_s, sleep):
    """Poll until a non-pending state or `seconds` elapse."""
    state, url, text = _observe(adapter, started_url)
    waited = 0.0
    step = max(poll_s, 0.01)
    while state == "pending" and waited < seconds:
        d = min(step, seconds - waited)
        sleep(d)
        waited += d
        state, url, text = _observe(adapter, started_url)
    return state, url, text


def check_submit(adapter, started_url, cfg=None):
    c = dict(load_watchdog_cfg())
    c.update(cfg or {})
    sleep = c.get("sleep", time.sleep)
    ladder, clicks = [], []
    last = {"state": "pending", "url": started_url, "text": ""}

    def result(outcome, note=None):
        ev = {"started_url": started_url, "final_url": last["url"],
              "page_snippet": last["text"][:SNIPPET],
              "clicks": clicks}
        if note:
            ev["note"] = note
        return {"outcome": outcome, "evidence": ev, "ladder": ladder}

    def look(seconds):
        s, u, t = _settle(adapter, started_url, seconds, c["poll_s"], sleep)
        last.update(state=s, url=u, text=t)
        return s

    def terminal():
        s = last["state"]
        if s == "confirmed" or s == "code-gate":
            return result(s)
        if s == "error":
            return result("error", "form error visible after submit")
        return None

    # Rung 1: wait + recheck
    ladder.append("wait+recheck")
    look(c["wait_s"])
    for _ in range(max(c["rechecks"], 0)):
        if last["state"] != "pending":
            break
        sleep(c["poll_s"])
        look(0)
    done = terminal()
    if done:
        return done

    # Rungs 2-3: re-click only while the page is untouched.
    for name, mode in (("click-normal", "normal"), ("click-js", "js")):
        if last["state"] != "pending":
            break
        ladder.append(name)
        try:
            r = adapter.click_submit(mode)
        except Exception as e:
            clicks.append({"mode": mode, "error": repr(e)[:120]})
            return result("error", "click raised in %s" % name)
        clicks.append({"mode": mode, "result": r})
        if not (isinstance(r, dict) and r.get("clicked")):
            return result("error", "submit control not clickable in %s"
                          % name)
        look(c["wait_s"])
        done = terminal()
        if done:
            return done

    # Rung 4: give up. A moved URL with no confirmation also lands here:
    # we cannot tell whether the application went through, so never retry.
    ladder.append("give-up")
    return result("blocked", "no confirmation, code gate, or error after "
                  "full ladder")
