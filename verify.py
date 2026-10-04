#!/usr/bin/env python3
"""Step 4: verify a submission. Rule check + local-model classification.

Usage: verify.py --port 9226 [--out verify.json]
Output: {"confirmed": bool, "rule_hit": bool, "model_says": bool,
         "evidence": str, "url": str}
"""
import argparse
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from common import cdp_ok
import model

SYSTEM = """You classify whether a job application was successfully submitted. Output ONLY strict JSON: {"confirmed": true|false, "evidence": "<short quote or reason>"}.
Confirmed = the page clearly states the application was received/submitted (e.g. "Thank you for applying", "application has been received", "successfully submitted"). Anything else (form still visible, errors, login walls) = false."""

SCHEMA = {
    "type": "object",
    "properties": {
        "confirmed": {"type": "boolean"},
        "evidence": {"type": "string"},
    },
    "required": ["confirmed", "evidence"],
    "additionalProperties": False,
}


def verify(port):
    url = cdp_ok(port, "url")["url"]
    text = cdp_ok(port, "text", "4000")["text"]
    tl = text.lower()
    rule_hit = ("/confirmation" in url) or ("thank you for applying" in tl) or \
               ("successfully submitted" in tl) or ("application has been received" in tl)
    ok, info = model.probe()
    if not ok:
        return {"confirmed": rule_hit, "rule_hit": rule_hit,
                "model_says": None, "evidence": "model unreachable; rule only",
                "url": url}
    res = model.chat(SYSTEM,
                     f"URL: {url}\n\nPAGE TEXT (first 4000 chars):\n{text[:4000]}",
                     "verify", SCHEMA, max_tokens=300, temperature=0.0)
    confirmed = bool(res.get("confirmed", False))
    return {"confirmed": confirmed and rule_hit or (confirmed and not rule_hit and
                                                     "error" not in tl),
            "rule_hit": rule_hit, "model_says": confirmed,
            "evidence": res.get("evidence", ""), "url": url}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--port", type=int, default=9226)
    ap.add_argument("--out", default="")
    a = ap.parse_args()
    r = verify(a.port)
    s = json.dumps(r, indent=1)
    if a.out:
        open(a.out, "w").write(s)
    print(s)


if __name__ == "__main__":
    main()
