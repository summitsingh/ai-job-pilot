#!/usr/bin/env python3
"""Shared helpers for the multi-step modal adapters (LinkedIn Easy Apply,
Indeed Apply) built on top of the deterministic ATS fill harness.

Conventions shared by both adapters:
- A "step schema" has the exact same shape as schema_dump output:
  {"fields": [...], "submit": sel|null, "submit_label": ..., "url": ..., "title": ...}
  plus step metadata: "step": {title, progress_text, index, total},
  "nav": {next, review, submit, back, dismiss} (selectors or null),
  "modal_open": bool.
  Because the shape matches, map.map_fields() and fill.apply_fill() are
  reused unchanged for per-step mapping and filling.
- The FINAL submit is NEVER clicked unless BOTH conditions hold:
    1. the runner was started with --submit, AND
    2. env ATS_APPROVE_SUBMIT=1 is set in the calling environment.
  Default mode is read-only-until-review: fill every step, verify readback,
  stop at the review/submit step, screenshot, emit status "ready-for-submit".
- Perplexity roles are OFF-LIMITS for automation: any URL containing
  "perplexity" raises before any browser action.
- AI-agent disclosure: facts.json carries "ai_agent_disclosure": "No"
  (applicant's policy). apply_disclosure_policy() forces that answer on any
  field whose label asks about AI assistance, so the model cannot
  misinterpret it.
- CAPTCHA/reCAPTCHA: detected via page text; the runner stops with
  status "blocked-captcha". No bypasses are attempted, ever.
"""
import json
import os
import re
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from common import cdp, b64

APPROVE_ENV = "ATS_APPROVE_SUBMIT"


def guard_perplexity(url):
    """Perplexity roles are off-limits for automation. Raise on match."""
    if "perplexity" in (url or "").lower():
        raise RuntimeError(
            "refusing: Perplexity roles are off-limits for automation")


def submit_approved(args_submit):
    """Explicit approval gate for the final submit click."""
    return bool(args_submit) and os.environ.get(APPROVE_ENV) == "1"


def require_no_auto_submit(args_submit):
    if args_submit and os.environ.get(APPROVE_ENV) != "1":
        raise RuntimeError(
            f"--submit was passed but {APPROVE_ENV}=1 is not set. "
            "The final submit requires explicit approval: set the env var "
            "in the same command that runs the supervised submit.")


DISCLOSURE_RE = re.compile(
    r"\b(ai|artificial intelligence)\b.*\b(assist|tool|agent|generat|use)\b"
    r"|\b(assist|tool|agent)\b.*\b(ai|artificial intelligence)\b",
    re.IGNORECASE)


def apply_disclosure_policy(fmap, schema, facts):
    """Force facts['ai_agent_disclosure'] ("No") on AI-assistance questions.

    map.py's guard list covers sponsorship/background/assessments; the
    AI-disclosure answer lives in facts.json but the label wording varies,
    so pin it deterministically instead of trusting the model.
    """
    want = facts.get("ai_agent_disclosure", "No")
    by_key = {f["key"]: f for f in schema["fields"]}
    for e in fmap.get("map", []):
        f = by_key.get(e["field"], {})
        if DISCLOSURE_RE.search(f.get("label", "")):
            ftype = f.get("type", "")
            if ftype == "select":
                opts = f.get("options", [])
                pick = next((o for o in opts
                             if o.strip().lower() == want.lower()),
                            None) or next(
                    (o for o in opts if want.lower() in o.lower()), None)
                e["action"] = "select" if pick else "skip"
                e["value"] = pick or ""
            elif ftype in ("radio", "checkbox", "yesno-button"):
                e["action"] = "click"
                e["value"] = want
            else:
                e["action"] = "fill"
                e["value"] = want
            e["note"] = "disclosure policy: facts['ai_agent_disclosure']"
    return fmap


CAPTCHA_RE = re.compile(
    r"recaptcha|captcha|verify you are (a )?human|"
    r"unusual traffic|please confirm you are not a robot",
    re.IGNORECASE)


def detect_captcha(page_text):
    return bool(CAPTCHA_RE.search(page_text or ""))


LOGIN_WALL_RE = re.compile(
    r"sign in to (apply|continue)|log in to apply|"
    r"create (an|your) (indeed )?account|join (linkedin|now) to apply",
    re.IGNORECASE)


def detect_login_wall(page_text, url=""):
    if LOGIN_WALL_RE.search(page_text or ""):
        return True
    return "/login" in (url or "") or "/signin" in (url or "")


def js_click_element(find_js):
    """Build a JS snippet that finds one element and clicks it in-page.
    Uses a single evaluate (no separate point computation): avoids the
    flaky computed-point path. The caller must verify the click by OUTCOME
    (e.g. modal opened), not by this snippet's return value.
    find_js: JS expression evaluating to the element (or null).
    """
    return ("(() => { const el = (" + find_js + ");"
            " if (!el) return 'not-found';"
            " el.scrollIntoView({block:'center', behavior:'instant'});"
            " el.click(); return 'clicked'; })()")


def click_once(port, find_js):
    """Best-effort in-page click. Returns True if the element existed."""
    r = cdp(port, "evalb64", b64(js_click_element(find_js)),
            timeout=60).get("result")
    return r == "clicked"


def wait_settled(port, timeout_s=30):
    """Wait for the page to finish loading AND settle.

    CDP Runtime.evaluate on a freshly-navigated page (notably indeed.com
    viewjob pages) can transiently return garbage results (observed:
    {"type": "function", "value": {}} for a plain object-literal IIFE).
    Waiting for readyState=complete plus a fixed settle delay before any
    click-point computation eliminates the race.
    """

    from common import cdp_ok, b64
    deadline = time.time() + timeout_s
    while time.time() < deadline:
        try:
            rs = cdp_ok(port, "evalb64",
                        b64("(() => document.readyState)()"),
                        timeout=30)["result"]
            if rs == "complete":
                break
        except Exception:
            pass
        time.sleep(1)
    time.sleep(3)


def emit_result(path, result):
    with open(path, "w") as f:
        json.dump(result, f, indent=1)
