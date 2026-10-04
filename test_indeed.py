#!/usr/bin/env python3
"""Test the frozen Indeed template against live Indeed Apply flows.

Never submits, never creates an account. Stops at the login wall.

Usage: test_indeed.py --jk <indeed-job-key> [--out dir] [--suite]
Flow: goto viewjob -> open_apply (clicks "Apply now") -> dump_step ->
      templates.map_template(ats=indeed) -> fill.apply_fill(--no-submit).

The Indeed lane runs on the mac-mini Chrome (port 19446) via the generic
CDP lane: ATS_CDP_URL=127.0.0.1:19446 ATS_HOST_SSH=~/workspace/bin/macmini-ssh
(the tunnel is a persistent background process on the controller).
"""
import argparse
import json
import os
import sys
import time

# Indeed lane env must be set before common is imported.
os.environ.setdefault("ATS_CDP_URL", "127.0.0.1:19446")
os.environ.setdefault("ATS_HOST_SSH",
                      os.path.expanduser("~/workspace/bin/macmini-ssh"))

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from common import cdp_ok, cdp, b64
from indeed_dump import open_apply, dump_step
from templates import map_template, FACTS
from fill import apply_fill

HERE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
OUT_DIR = os.path.join(HERE, "template-validation-2026-10-03")

SUITE = [
    ("dce975ed9c63af71", "Senior Full Stack Software Engineer"),
    ("cb4c5148b3f23bba", "Sr. SW AI Engineer"),
]
PORT = 0  # direct lane: port arg ignored, URL carries it


def advance_past_empty_steps(port, max_steps=3):
    """Click Continue while the current step has no fields (e.g. the
    'Review your resume' step). Returns the number of advances."""
    from indeed_dump import dump_step as _dump
    n = 0
    for _ in range(max_steps):
        schema = _dump(port)
        if len(schema["fields"]) > 0 or schema.get("login_wall"):
            return n, schema
        js = ("(() => { const b = Array.from(document.querySelectorAll("
              "'button')).find(x => x.offsetParent && !x.disabled && "
              "/^(continue|review your application)$/i.test((x.innerText||'')"
              ".trim())); if (!b) return {error:'no-continue'}; "
              "b.scrollIntoView({block:'center',behavior:'instant'}); "
              "const r = b.getBoundingClientRect(); "
              "return {x:r.x+r.width/2, y:r.y+r.height/2, inView:true}; })()")
        r = cdp(port, "fclickel", b64(js), timeout=60)
        if not r.get("ok"):
            return n, schema
        time.sleep(5)
        n += 1
    return n, _dump(port)


def test_one(jk, title, out_dir):
    label = f"indeed {title} ({jk[:8]})"
    print(f"\n=== {label} ===")
    url = f"https://www.indeed.com/viewjob?jk={jk}"
    cdp_ok(PORT, "reset")
    cdp_ok(PORT, "goto", b64(url), timeout=120)
    time.sleep(5)
    opened = open_apply(PORT)
    print(f"  apply flow opened: {opened}")
    time.sleep(4)
    n_adv, schema = advance_past_empty_steps(PORT)
    if n_adv:
        print(f"  advanced past {n_adv} empty step(s)")
    n = len(schema["fields"])
    print(f"  step '{schema.get('step', {}).get('title', '?')}': {n} fields, "
          f"login_wall={schema.get('login_wall')}")
    if schema.get("login_wall"):
        print("  STOPPED at login wall (no account creation)")
        return {"form": label, "status": "blocked-login", "total": n}
    t0 = time.time()
    fmap = map_template(schema, FACTS, "indeed")
    t1 = time.time()
    n_map = len([e for e in fmap["map"] if e["action"] != "skip"])
    print(f"  template mapped {n_map}, skipped {n - n_map} (guard="
          f"{len(fmap['skipped_by_guard'])})")
    json.dump(schema, open(os.path.join(out_dir, f"schema-indeed-{jk[:8]}.json"),
                           "w"), indent=1)
    json.dump(fmap, open(os.path.join(out_dir, f"map-indeed-{jk[:8]}.json"),
                         "w"), indent=1)
    t2 = time.time()
    result = apply_fill(schema, fmap, PORT, no_submit=True,
                        shot_path=f"/tmp/harness/tpl-indeed-{jk[:8]}.png")
    t3 = time.time()
    json.dump(result, open(os.path.join(out_dir, f"fill-indeed-{jk[:8]}.json"),
                           "w"), indent=1)
    attempted = result["fields_attempted"]
    filled = len(result["fields_filled"])
    mism = result["fields_mismatched"]
    status = "PASS" if not mism else "FAIL"
    print(f"\n[{status}] {label}")
    print(f"  fields total={n} attempted={attempted} verified={filled} "
          f"mismatched={len(mism)}")
    print(f"  map={t1 - t0:.1f}s fill={t3 - t2:.1f}s")
    for m in mism:
        pf = next((p for p in result["per_field"]
                   if p["field"] == m), {})
        print(f"  MISMATCH {m}: expected={pf.get('expected')!r} "
              f"actual={pf.get('actual')!r} note={pf.get('note')!r}")
    return {"form": label, "status": status, "total": n,
            "attempted": attempted, "verified": filled, "mismatched": mism,
            "map_secs": round(t1 - t0, 1), "fill_secs": round(t3 - t2, 1)}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--jk", default="")
    ap.add_argument("--title", default="indeed-job")
    ap.add_argument("--suite", action="store_true")
    ap.add_argument("--out", default=OUT_DIR)
    a = ap.parse_args()
    os.makedirs(a.out, exist_ok=True)
    results = []
    if a.suite:
        for jk, title in SUITE:
            try:
                results.append(test_one(jk, title, a.out))
            except Exception as e:
                print(f"\n[ERROR] {jk}: {e}")
                results.append({"form": f"indeed {jk}", "status": "ERROR",
                                "error": str(e)[:200]})
    else:
        if not a.jk:
            ap.error("--jk required without --suite")
        results.append(test_one(a.jk, a.title, a.out))
    n_ok = sum(1 for r in results if r["status"] == "PASS")
    print(f"\n=== INDEED: {n_ok}/{len(results)} PASS ===")
    json.dump(results, open(os.path.join(a.out, "summary-indeed.json"), "w"),
              indent=1)


if __name__ == "__main__":
    main()
