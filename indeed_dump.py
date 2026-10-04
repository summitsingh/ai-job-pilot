#!/usr/bin/env python3
"""Step 1 (Indeed): dump the CURRENT Indeed Apply step as a schema.

Indeed Apply is a modal/multi-step flow (contact/resume -> employer
questions -> review -> submit). This dumper captures ONE step: its fields
(same shape as schema_dump output, so map.py/fill.py work unchanged),
the step title, and the step-navigation buttons (Continue / Review your
application / Submit your application / Back).

Usage:
  indeed_dump.py --url <indeed job URL> --port 9226 [--out step.json]
                 [--click-apply]   # click "Apply now" on the job page first
                 [--step-only]     # apply flow already open; just dump it

Read-only except for the optional "Apply now" click: never fills, never
submits, never creates an account.

SELECTOR ASSUMPTIONS (documented, to re-verify against the live flow on
the first supervised run; Indeed changes markup periodically):
- "Apply now" button on the job page: inside `#applyButtonLinkContainer`;
  verified by visible text matching /apply now/i. A posting whose button
  reads "Apply on company site" is an EXTERNAL redirect, not Indeed Apply;
  the adapter refuses those.
  VALIDATED 2026-10-02: live viewjob pages render an <a> reading
  "Apply with Indeed" (not a button reading "Apply now"); the matcher is
  /apply (now|with indeed)/i over button,a. Postings whose rc/clk link
  bounces off indeed.com to the employer's ATS are refused as external.
- Clicking "Apply now" opens the apply flow, historically a modal
  (`#applyForm`) and now often a full-page/stepped flow. Apply-root
  candidates: `#applyFlow`, `[data-testid="apply-flow"]`, `#applyForm`,
  `.ia-ApplyForm`, `main` fallback.
- Step title: `h1`/`h2` inside the apply root.
- Nav buttons by visible text: "Continue", "Review your application",
  "Submit your application", "Back", "Save and exit". No stable aria-labels
  are assumed; text matching is primary, data-testid a fallback.
- Fields are standard inputs/selects/textareas/radios; resume upload is
  `input[type=file]`; phone often split into country-code select + number.
- Without an Indeed account the flow gates on a sign-in wall ("Sign in to
  apply" / "Create your account"). The runner stops there: no account
  creation, no submit. dump_step() reports `login_wall: true` in that case.
"""
import argparse
import json
import os
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from common import ensure_driver, cdp_ok, cdp, b64
from schema_dump import DUMP_JS
from modal_common import guard_perplexity, detect_login_wall, wait_settled

APPLY_ROOT_CANDIDATES = [
    "#applyFlow",
    '[data-testid="apply-flow"]',
    "#applyForm",
    ".ia-ApplyForm",
    "main",
]


def _scoped_dump_js():
    body = DUMP_JS.strip()
    assert body.startswith("(() => {") and body.endswith("})()"), \
        "DUMP_JS wrapper shape changed; update _scoped_dump_js"
    inner = body[len("(() => {"):-len("})()")]
    inner = inner.replace("document.querySelectorAll", "ROOT.querySelectorAll")
    inner = inner.replace("document.querySelector", "ROOT.querySelector")
    return (
        "(() => {\n"
        "  const ROOT = (" + json.dumps(APPLY_ROOT_CANDIDATES) +
        ".map(s => document.querySelector(s)).find(m => m && m.offsetParent));\n"
        "  if (!ROOT) return {flow_open: false, fields: [], submit: null,\n"
        "    submit_label: null, url: location.href, title: document.title};\n"
        "  const __inner = (() => {" + inner + "})();\n"
        "  return Object.assign({flow_open: true}, __inner);\n"
        "})()"
    )


SCOPED_DUMP_JS = _scoped_dump_js()

STEP_INFO_JS = r"""(() => {
  const cands = %s;
  const root = cands.map(s => document.querySelector(s))
                    .find(m => m && m.offsetParent);
  if (!root) return {flow_open: false};
  const txt = el => (el && el.innerText || '').trim().slice(0, 140);
  const head = root.querySelector('h1, h2');
  const btnByText = re => {
    const all = Array.from(root.querySelectorAll('button'))
      .filter(x => { try { return x.offsetParent && !x.disabled; } catch(e){ return false; } });
    return all.find(x => re.test(txt(x))) || null;
  };
  const selOf = b => {
    if (!b) return null;
    if (b.id) return '#' + CSS.escape(b.id);
    const dt = b.getAttribute('data-testid');
    if (dt) return 'button[data-testid="' + dt.replace(/"/g,'\\"') + '"]';
    return null;
  };
  const cont = btnByText(/^continue$/i);
  const review = btnByText(/review your application/i);
  const submit = btnByText(/submit your application/i);
  const back = btnByText(/^back$/i);
  const saveExit = btnByText(/save and exit/i);
  const t = b => txt(b);
  return {
    flow_open: true,
    step: {title: t(head)},
    nav: {
      next: selOf(cont), next_text: t(cont),
      review: selOf(review), review_text: t(review),
      submit: selOf(submit), submit_text: t(submit),
      back: selOf(back), back_text: t(back),
      save_exit: selOf(saveExit),
    },
    // nav buttons that had no stable selector: report their text so the
    // runner can click by text fallback
    nav_text_only: {
      next: !selOf(cont) && !!cont, review: !selOf(review) && !!review,
      submit: !selOf(submit) && !!submit, back: !selOf(back) && !!back,
    },
  };
})()""" % json.dumps(APPLY_ROOT_CANDIDATES)

APPLY_NOW_CLICK_JS = r"""(() => {
  // Validated 2026-10-02 on live viewjob pages: the entry point is an <a>
  // reading "Apply with Indeed" (not a button reading "Apply now").
  const box = document.querySelector('#applyButtonLinkContainer');
  const scope = box || document;
  const els = Array.from(scope.querySelectorAll('button, a'));
  const now = els.find(b => /apply (now|with indeed)/i.test((b.innerText || '')));
  if (now) {
    const r = now.getBoundingClientRect();
    return {found: true, x: r.x + r.width/2, y: r.y + r.height/2};
  }
  const ext = els.find(b => /apply on company site/i.test((b.innerText || '')));
  return {found: false, external_apply: !!ext};
})()"""


def flow_open(port):
    r = cdp_ok(port, "evalb64", b64(STEP_INFO_JS), timeout=60)["result"]
    return bool(r.get("flow_open"))


APPLY_FIND_JS = """(() => {
  const box = document.querySelector('#applyButtonLinkContainer');
  const scope = box || document;
  const els = Array.from(scope.querySelectorAll('button, a'));
  return els.find(b => /apply (now|with indeed)/i.test((b.innerText || ''))) || null;
})()"""

APPLY_EXTERNAL_JS = """(() => {
  const els = Array.from(document.querySelectorAll('button, a'));
  return !!els.find(b => /apply on company site/i.test((b.innerText || '')));
})()"""


def open_apply(port, timeout_s=25):
    """Click "Apply now" / "Apply with Indeed" on the job page; wait for the
    apply flow.

    Refuses pages that navigated off indeed.com (external ATS redirect) and
    postings whose button reads "Apply on company site". Clicks via a
    single in-page evaluate (el.click()), verified by OUTCOME (flow opens),
    because the computed-point click path (evaluate -> coords -> fclickel)
    intermittently receives garbage evaluate results on freshly loaded
    indeed.com pages (Chrome returns {"type":"function","value":{}} for a
    plain object-literal IIFE; transient, heals after minutes).
    """
    from modal_common import click_once
    url = cdp_ok(port, "url", timeout=60).get("url", "")
    if "indeed.com" not in url:
        raise RuntimeError(
            f"not an Indeed Apply posting (navigated off indeed.com to "
            f"{url[:80]}); refusing")
    last_err = None
    for _ in range(4):
        if click_once(port, APPLY_FIND_JS):
            break
        ext = cdp(port, "evalb64", b64(APPLY_EXTERNAL_JS),
                  timeout=60).get("result")
        if ext:
            raise RuntimeError(
                "not an Indeed Apply posting ('Apply on company site' -> "
                "external ATS); refusing")
        last_err = "apply entry point not found/clicked"
        time.sleep(2)
    else:
        raise RuntimeError(f"Apply-now click failed after retries: {last_err}")
    deadline = time.time() + timeout_s
    while time.time() < deadline:
        if flow_open(port):
            return True
        time.sleep(1.5)
    raise RuntimeError("Indeed Apply flow did not open after click")


def dump_step(port):
    """Dump the apply flow's CURRENT step. Returns the step schema dict."""
    ensure_driver()
    info = cdp_ok(port, "evalb64", b64(STEP_INFO_JS), timeout=60)["result"]
    if not info.get("flow_open"):
        raise RuntimeError("Indeed Apply flow is not open")
    dump = cdp_ok(port, "evalb64", b64(SCOPED_DUMP_JS), timeout=120)["result"]
    url = cdp_ok(port, "url", timeout=60).get("url", "")
    page_text = cdp(port, "text", "3000", timeout=60).get("text", "")
    schema = {
        "fields": dump.get("fields", []),
        "submit": dump.get("submit"),
        "submit_label": dump.get("submit_label"),
        "url": url,
        "title": "",
        "ats_hint": "indeed-apply",
        "flow_open": True,
        "step": info.get("step", {}),
        "nav": info.get("nav", {}),
        "nav_text_only": info.get("nav_text_only", {}),
        "login_wall": detect_login_wall(page_text, url),
    }
    return schema


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--url", required=True)
    ap.add_argument("--port", type=int, default=9226)
    ap.add_argument("--out", default="")
    ap.add_argument("--click-apply", action="store_true",
                    help='click "Apply now" on the job page first')
    ap.add_argument("--step-only", action="store_true",
                    help="apply flow already open; just dump the current step")
    a = ap.parse_args()
    guard_perplexity(a.url)

    ensure_driver()
    if not a.step_only:
        cdp_ok(a.port, "reset")
        cdp_ok(a.port, "goto", b64(a.url), timeout=120)
        wait_settled(a.port)
    if a.click_apply:
        open_apply(a.port)
    schema = dump_step(a.port)
    s = json.dumps(schema, indent=1)
    if a.out:
        open(a.out, "w").write(s)
        print(f"wrote {a.out} ({len(schema['fields'])} fields, "
              f"step={schema['step'].get('title')}, "
              f"login_wall={schema['login_wall']})")
    else:
        print(s)


if __name__ == "__main__":
    main()
