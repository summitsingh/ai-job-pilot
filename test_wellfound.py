#!/usr/bin/env python3
"""Test the frozen Wellfound template against live apply dialogs.

PROVISIONAL: Wellfound's logged-in apply dialog was not reachable in
testing; this exercises the logged-out apply dialog (which is the same
form, plus account-creation password fields that the template always
skips). Never submits, never creates an account.

Usage: test_wellfound.py --url <wellfound-job-url> --port 9229 [--suite]
Flow: goto job page -> click "Apply Now" -> dump the dialog schema ->
      templates.map_template(ats=wellfound) -> fill.apply_fill(--no-submit).
"""
import argparse
import json
import os
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from common import cdp_ok, cdp, b64
from schema_dump import DUMP_JS
from templates import map_template, FACTS
from fill import apply_fill

HERE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
OUT_DIR = os.path.join(HERE, "template-validation-2026-10-03")

SUITE = [
    ("https://wellfound.com/jobs/2609098-senior-backend-engineer",
     "Barometer Senior Backend Engineer"),
    ("https://wellfound.com/jobs/3830768-senior-backend-engineer-platform-infrastructure",
     "Prismatic Senior Backend Engineer Platform Infra"),
]

APPLY_CLICK_JS = ("(() => { const b = Array.from(document.querySelectorAll("
                  "'button,a')).find(x => (x.innerText||'').trim()==='Apply Now');"
                  " if (!b) return {error:'no-apply-btn'};"
                  " b.scrollIntoView({block:'center',behavior:'instant'});"
                  " const r = b.getBoundingClientRect();"
                  " return {x: r.x+r.width/2, y: r.y+r.height/2, inView:true}; })()")


def dump_dialog(port):
    """Dump the open Wellfound apply dialog (scoped to [role=dialog]).

    Skips option scraping: the scrape's menu-close fallback clicks outside
    the modal and dismisses the dialog. The template resolves react-select
    options at fill time anyway.
    """
    js = DUMP_JS.replace(
        "document.querySelectorAll('input,select,textarea').forEach",
        "(document.querySelector('[role=\"dialog\"]') || document)"
        ".querySelectorAll('input,select,textarea').forEach")
    r = cdp_ok(port, "evalb64", b64(js), timeout=120)["result"]
    r["ats_hint"] = "wellfound"
    return r


def test_one(url, title, port, out_dir):
    label = f"wellfound {title}"
    print(f"\n=== {label} (port {port}) ===")
    cdp_ok(port, "reset")
    cdp_ok(port, "goto", b64(url), timeout=120)
    time.sleep(6)
    r = cdp(port, "fclickel", b64(APPLY_CLICK_JS), timeout=60)
    if not r.get("ok"):
        raise RuntimeError(f"Apply Now click failed: {r.get('error')}")
    time.sleep(5)
    schema = dump_dialog(port)
    n = len(schema["fields"])
    print(f"  dialog dumped: {n} fields")
    if n < 5:
        raise RuntimeError("apply dialog did not open (too few fields)")
    t0 = time.time()
    fmap = map_template(schema, FACTS, "wellfound")
    t1 = time.time()
    n_map = len([e for e in fmap["map"] if e["action"] != "skip"])
    print(f"  template mapped {n_map}, skipped {n - n_map} (guard="
          f"{len(fmap['skipped_by_guard'])}) [{fmap['note']}]")
    slug = "".join(c if c.isalnum() else "-" for c in title[:24]).strip("-")
    json.dump(schema, open(os.path.join(out_dir, f"schema-wellfound-{slug}.json"),
                           "w"), indent=1)
    json.dump(fmap, open(os.path.join(out_dir, f"map-wellfound-{slug}.json"),
                         "w"), indent=1)
    t2 = time.time()
    result = apply_fill(schema, fmap, port, no_submit=True,
                        shot_path=f"/tmp/harness/tpl-wellfound-{port}.png")
    t3 = time.time()
    json.dump(result, open(os.path.join(out_dir, f"fill-wellfound-{slug}.json"),
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
    ap.add_argument("--url", default="")
    ap.add_argument("--title", default="wellfound-job")
    ap.add_argument("--port", type=int, default=9229)
    ap.add_argument("--suite", action="store_true")
    ap.add_argument("--out", default=OUT_DIR)
    a = ap.parse_args()
    os.makedirs(a.out, exist_ok=True)
    results = []
    if a.suite:
        for url, title in SUITE:
            try:
                results.append(test_one(url, title, a.port, a.out))
            except Exception as e:
                print(f"\n[ERROR] {title}: {e}")
                results.append({"form": f"wellfound {title}",
                                "status": "ERROR", "error": str(e)[:200]})
    else:
        if not a.url:
            ap.error("--url required without --suite")
        results.append(test_one(a.url, a.title, a.port, a.out))
    n_ok = sum(1 for r in results if r["status"] == "PASS")
    print(f"\n=== WELLFOUND: {n_ok}/{len(results)} PASS ===")
    json.dump(results, open(os.path.join(a.out, "summary-wellfound.json"),
                            "w"), indent=1)


if __name__ == "__main__":
    main()
