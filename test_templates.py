#!/usr/bin/env python3
"""Test frozen templates (templates.py) against live ATS forms. Never submits.

Usage:
  test_templates.py --ats ashby --url <application-url> --port 9226 [--out dir]
  test_templates.py --suite   # runs the full built-in suite (ashby x2, lever x2,
                              # indeed x2, wellfound provisional)

Flow per form: dump schema -> templates.map_template (timed) ->
fill.apply_fill(--no-submit, timed) -> per-form PASS/FAIL + field accuracy.
--no-submit always: no submit button is ever clicked.
"""
import argparse
import json
import os
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from common import cdp_ok, cdp, b64
from schema_dump import dump_schema
from templates import map_template, FACTS
from fill import apply_fill

HERE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
OUT_DIR = os.path.join(HERE, "template-validation-2026-10-03")

CLOSED_MARKERS = ["no longer available", "no longer accepting",
                  "job posting is no longer", "page not found",
                  "job not found", "couldn't find that",
                  "this job has expired", "position has been filled"]

SUITE = [
    # (ats, url, port)
    ("ashby",
     "https://jobs.ashbyhq.com/pinecone/4ef4269b-94c4-4c7c-93ee-15a882caa767/application",
     9226),
    ("ashby",
     "https://jobs.ashbyhq.com/pinecone/7ef089cb-a721-4ad8-a6d0-c390e64991d2/application",
     9227),
    ("lever",
     "https://jobs.lever.co/nimblerx/dd6e3bde-6ff5-4adc-8b49-ba2d85cbf29e/apply",
     9228),
    ("lever",
     "https://jobs.lever.co/limitbreak/1be0b22d-eedb-45d2-9b8e-e06d75deaf83/apply",
     9231),
]
FALLBACKS = {
    "ashby": [
        "https://jobs.ashbyhq.com/pinecone/8beaa81a-20be-4b3c-a38c-b85d13bf1df9/application",
    ],
    "lever": [
        "https://jobs.lever.co/ivo/3ce9dc16-90fd-4b99-b4b9-dfd48ec1a50d/apply",
    ],
}


def goto_fresh(port, url):
    cdp_ok(port, "reset")
    cdp_ok(port, "goto", b64(url), timeout=120)
    time.sleep(4)


def is_live(port):
    t = cdp(port, "text", timeout=60).get("text", "").lower()
    return not any(m in t for m in CLOSED_MARKERS)


def ensure_live(port, primary, ats):
    goto_fresh(port, primary)
    if is_live(port):
        return primary, False
    for fb in FALLBACKS.get(ats, []):
        goto_fresh(port, fb)
        if is_live(port):
            print(f"  SUBSTITUTION: primary closed, using {fb}")
            return fb, True
    raise RuntimeError(f"no live posting found for {ats} (primary {primary})")


def summarize(form_label, schema, fmap, result, map_secs, fill_secs):
    attempted = result["fields_attempted"]
    filled = len(result["fields_filled"])
    mism = result["fields_mismatched"]
    skipped = result["fields_skipped"]
    guarded = len(fmap.get("skipped_by_guard", []))
    unmapped = len(fmap.get("unmapped", []))
    status = "PASS" if not mism else "FAIL"
    print(f"\n[{status}] {form_label}")
    print(f"  fields total={result['fields_total']} attempted={attempted} "
          f"verified={filled} mismatched={len(mism)}")
    print(f"  skipped={len(skipped)} (guard={guarded}, no-pattern="
          f"{len(skipped) - guarded}) unmapped={unmapped}")
    print(f"  map={map_secs:.1f}s fill={fill_secs:.1f}s "
          f"total={map_secs + fill_secs:.1f}s")
    if mism:
        for m in mism:
            pf = next((p for p in result["per_field"]
                       if p["field"] == m), {})
            print(f"  MISMATCH {m}: expected={pf.get('expected')!r} "
                  f"actual={pf.get('actual')!r} note={pf.get('note')!r}")
    return {"form": form_label, "status": status,
            "total": result["fields_total"], "attempted": attempted,
            "verified": filled, "mismatched": mism,
            "skipped": len(skipped), "guarded": guarded,
            "map_secs": round(map_secs, 1), "fill_secs": round(fill_secs, 1)}


def test_one(ats, url, port, out_dir):
    label = f"{ats} {url.split('/')[3]} {url.split('/')[-2][:8]}"
    print(f"\n=== {label} (port {port}) ===")
    url, sub = ensure_live(port, url, ats)
    if sub:
        label += " [substituted]"
    t0 = time.time()
    schema = dump_schema(url, port)
    t1 = time.time()
    print(f"  dumped {len(schema['fields'])} fields "
          f"(dump={t1 - t0:.1f}s)")
    t2 = time.time()
    fmap = map_template(schema, FACTS, ats)
    t3 = time.time()
    n_map = len([e for e in fmap["map"] if e["action"] != "skip"])
    print(f"  template mapped {n_map}, skipped "
          f"{len(fmap['map']) - n_map} (guard="
          f"{len(fmap['skipped_by_guard'])})")
    json.dump(schema, open(os.path.join(out_dir, f"schema-{ats}-{port}.json"),
                           "w"), indent=1)
    json.dump(fmap, open(os.path.join(out_dir, f"map-{ats}-{port}.json"),
                         "w"), indent=1)
    t4 = time.time()
    result = apply_fill(schema, fmap, port, no_submit=True,
                        shot_path=f"/tmp/harness/tpl-{ats}-{port}.png")
    t5 = time.time()
    json.dump(result, open(os.path.join(out_dir, f"fill-{ats}-{port}.json"),
                           "w"), indent=1)
    return summarize(label, schema, fmap, result, t3 - t2, t5 - t4)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--ats", default="ashby",
                    choices=["ashby", "lever", "indeed", "wellfound"])
    ap.add_argument("--url", default="")
    ap.add_argument("--port", type=int, default=9226)
    ap.add_argument("--suite", action="store_true")
    ap.add_argument("--out", default=OUT_DIR)
    a = ap.parse_args()
    os.makedirs(a.out, exist_ok=True)
    results = []
    if a.suite:
        for ats, url, port in SUITE:
            try:
                results.append(test_one(ats, url, port, a.out))
            except Exception as e:
                print(f"\n[ERROR] {ats} {url}: {e}")
                results.append({"form": f"{ats} {url}", "status": "ERROR",
                                "error": str(e)[:200]})
    else:
        if not a.url:
            ap.error("--url required without --suite")
        results.append(test_one(a.ats, a.url, a.port, a.out))
    n_ok = sum(1 for r in results if r["status"] == "PASS")
    print(f"\n=== SUITE: {n_ok}/{len(results)} PASS ===")
    json.dump(results, open(os.path.join(a.out, "summary.json"), "w"),
              indent=1)


if __name__ == "__main__":
    main()
