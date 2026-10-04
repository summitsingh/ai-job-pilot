#!/usr/bin/env python3
"""Handle the Greenhouse email verification code gate.

Usage: code_gate.py --port 9226 --workdir /tmp/jobpilot/run1

After the application submit is clicked, Greenhouse shows an 8-character
email verification gate. This helper:
  1. Waits for the gate to appear (page text mentions "verification code").
  2. Prompts the operator for the code on stdin (paste it from email).
  3. Enters one character per box, clicks submit, verifies confirmation.

Writes workdir/code_gate.json: {entered, confirmed, url}.

The gate is a human-in-the-loop step by design: codes are single-use and
expire quickly. Never guess or brute-force; if entry fails, ask the
operator for a fresh code.
"""
import argparse
import base64
import json
import os
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from common import cdp_ok

GATE_JS = """(() => {
  const text = (document.body.innerText || '').toLowerCase();
  if (!text.includes('verification code')) return {gate: false};
  const inputs = Array.from(document.querySelectorAll('input')).filter(el => {
    try {
      if (el.offsetParent === null || el.disabled) return false;
      const ml = el.getAttribute('maxlength');
      const tp = (el.getAttribute('type') || 'text').toLowerCase();
      const cls = el.className || '';
      return (ml === '1' || /code|otp|digit|verify/i.test(cls)) &&
             ['text', 'tel', 'number'].includes(tp);
    } catch (e) { return false; }
  });
  const keys = inputs.map((el, i) => {
    if (!el.id) el.id = 'jobpilot-code-' + i;
    return '#' + el.id;
  });
  return {gate: true, boxes: keys.length, keys: keys};
})()"""

FILL_JS = """(payload => {
  const {keys, code} = payload;
  const out = [];
  const setter = Object.getOwnPropertyDescriptor(
    window.HTMLInputElement.prototype, 'value').set;
  keys.forEach((key, i) => {
    const el = document.querySelector(key);
    if (!el) { out.push({key, ok: false}); return; }
    el.focus();
    setter.call(el, code[i] || '');
    el.dispatchEvent(new Event('input', {bubbles: true}));
    el.dispatchEvent(new Event('change', {bubbles: true}));
    out.push({key, ok: el.value === (code[i] || '')});
  });
  return out;
})"""

SUBMIT_JS = """(() => {
  const btns = Array.from(document.querySelectorAll('button')).filter(b => {
    try { return b.offsetParent && !b.disabled; } catch (e) { return false; }
  });
  const sub = btns.find(b =>
    /submit\\s*application/i.test((b.innerText || '').trim()));
  if (!sub) return 'NOBTN';
  sub.scrollIntoView({behavior: 'instant', block: 'center'});
  sub.click();
  return 'CLICKED';
})()"""


def b64(s):
    return base64.b64encode(s.encode()).decode()


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--port", type=int, required=True)
    ap.add_argument("--workdir", required=True)
    ap.add_argument("--timeout", type=int, default=300,
                    help="seconds to wait for the gate to appear")
    a = ap.parse_args()
    os.makedirs(a.workdir, exist_ok=True)
    result = {"entered": False, "confirmed": False, "url": ""}

    # 1. wait for the gate
    deadline = time.time() + a.timeout
    gate = None
    while time.time() < deadline:
        r = cdp_ok(a.port, "evalb64", b64(GATE_JS), timeout=60)
        gate = r.get("result") or {}
        if gate.get("gate"):
            break
        time.sleep(5)
    if not gate or not gate.get("gate"):
        result["note"] = "no verification gate appeared within timeout"
        json.dump(result, open(os.path.join(a.workdir, "code_gate.json"),
                               "w"), indent=1)
        print(json.dumps(result, indent=1))
        return

    n = gate.get("boxes", 0)
    print(f"Verification gate found with {n} code boxes.")
    if n != 8:
        result["note"] = f"expected 8 code boxes, found {n}; aborting"
        json.dump(result, open(os.path.join(a.workdir, "code_gate.json"),
                               "w"), indent=1)
        print(json.dumps(result, indent=1))
        return

    # 2. prompt the operator (human-in-the-loop; never guess)
    code = input("Enter the 8-character verification code from email: "
                 ).strip()
    if len(code) != 8:
        result["note"] = f"code must be 8 characters, got {len(code)}"
        json.dump(result, open(os.path.join(a.workdir, "code_gate.json"),
                               "w"), indent=1)
        print(json.dumps(result, indent=1))
        return

    # 3. fill the boxes, one character each
    call = ("(" + FILL_JS + ")(" +
            json.dumps({"keys": gate["keys"], "code": code}) + ")")
    r = cdp_ok(a.port, "evalb64", b64(call), timeout=60)
    filled = r.get("result") or []
    if not all(f.get("ok") for f in filled):
        result["note"] = "not all code boxes accepted their character"
        json.dump(result, open(os.path.join(a.workdir, "code_gate.json"),
                               "w"), indent=1)
        print(json.dumps(result, indent=1))
        return
    result["entered"] = True

    # 4. submit and verify
    r = cdp_ok(a.port, "evalb64", b64(SUBMIT_JS), timeout=120)
    print("submit:", r.get("result"))
    time.sleep(15)
    url = cdp_ok(a.port, "url", timeout=60).get("url", "")
    text = cdp_ok(a.port, "text", "4000", timeout=60).get("text", "")
    result["url"] = url
    result["confirmed"] = ("/confirmation" in url or
                           "thank you for applying" in text.lower())
    json.dump(result, open(os.path.join(a.workdir, "code_gate.json"), "w"),
              indent=1)
    print(json.dumps(result, indent=1))


if __name__ == "__main__":
    main()
