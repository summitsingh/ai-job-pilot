#!/usr/bin/env python3
"""Orchestrator: dump schema -> map -> fill -> (submit) -> verify.

Usage:
  ats_fill.py --url <application URL> --port 9226 [--ats greenhouse|ashby|lever]
              [--no-submit] [--workdir /tmp/harness/run1] [--force]

Emits result JSON to stdout and <workdir>/result.json:
  {status, confirmation_evidence, fields_filled, fields_skipped, notes}

status: confirmed | filled-no-submit | blocked | failed
Respects the pool lock files browser-<port>.lock (fresh <15min blocks unless --force).
"""
import argparse
import json
import os
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from schema_dump import dump_schema
from map import map_fields
from fill import apply_fill
from verify import verify

HERE = os.path.dirname(os.path.abspath(__file__))
GOAL_HIDDEN = os.path.dirname(HERE)


def lock_path(port):
    return os.path.join(GOAL_HIDDEN, f"browser-{port}.lock")


def acquire_lock(port, force):
    lp = lock_path(port)
    if os.path.exists(lp) and not force:
        age = time.time() - os.path.getmtime(lp)
        if age < 15 * 60:
            raise RuntimeError(f"browser {port} locked ({int(age)}s old); use --force")
    with open(lp, "w") as f:
        f.write(f"harness pid={os.getpid()} ts={int(time.time())}\n")


def release_lock(port):
    try:
        os.remove(lock_path(port))
    except OSError:
        pass


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--url", required=True)
    ap.add_argument("--port", type=int, default=9226)
    ap.add_argument("--ats", default="greenhouse")
    ap.add_argument("--no-submit", action="store_true")
    ap.add_argument("--workdir", default="")
    ap.add_argument("--force", action="store_true")
    a = ap.parse_args()

    wd = a.workdir or f"/tmp/harness/run-{a.port}-{int(time.time())}"
    os.makedirs(wd, exist_ok=True)
    result = {"url": a.url, "port": a.port, "ats": a.ats,
              "no_submit": a.no_submit, "status": "failed",
              "confirmation_evidence": "", "fields_filled": [],
              "fields_skipped": [], "notes": []}
    try:
        acquire_lock(a.port, a.force)
        schema = dump_schema(a.url, a.port)
        schema["ats_hint"] = a.ats
        json.dump(schema, open(f"{wd}/schema.json", "w"), indent=1)
        result["notes"].append(f"schema: {len(schema['fields'])} fields")

        facts_path = os.environ.get("JOBPILOT_FACTS",
                                    os.path.join(HERE, "facts.json"))
        if not os.path.isfile(facts_path):
            raise SystemExit(
                "facts.json not found. Copy facts.example.json to facts.json "
                "and fill in your details first.")
        facts = json.load(open(facts_path))
        # Per-run resume override (batch_apply --resume-map); never
        # modifies the user's facts.json on disk.
        if os.environ.get("JOBPILOT_RESUME"):
            facts["resume_path"] = os.environ["JOBPILOT_RESUME"]
        fmap = map_fields(schema, facts)
        json.dump(fmap, open(f"{wd}/map.json", "w"), indent=1)
        result["notes"].append(
            f"map: {len(fmap['map'])} mapped, "
            f"{len(fmap['skipped_by_guard'])} guarded, "
            f"{len(fmap['unmapped'])} unmapped")

        fill_res = apply_fill(schema, fmap, a.port, a.no_submit,
                              f"{wd}/fill.png")
        json.dump(fill_res, open(f"{wd}/fill.json", "w"), indent=1)
        result["fields_filled"] = fill_res["fields_filled"]
        result["fields_skipped"] = (fill_res["fields_skipped"] +
                                    [{"field": u, "guard": ""}
                                     for u in fmap["unmapped"]])
        result["notes"].extend(fill_res["notes"])
        if fill_res["fields_mismatched"]:
            result["notes"].append(
                "MISMATCHED (needs Muse review): " +
                ", ".join(fill_res["fields_mismatched"]))

        if a.no_submit:
            result["status"] = "filled-no-submit"
        elif fill_res["submitted"]:
            v = verify(a.port)
            json.dump(v, open(f"{wd}/verify.json", "w"), indent=1)
            result["confirmation_evidence"] = v["evidence"]
            result["status"] = "confirmed" if v["confirmed"] else "blocked"
            if not v["confirmed"]:
                result["notes"].append("submit clicked but not confirmed")
        else:
            result["status"] = "blocked"
            result["notes"].append("submit step did not run")
    except Exception as e:
        result["notes"].append(f"ERROR {type(e).__name__}: {e}")
    finally:
        release_lock(a.port)
        json.dump(result, open(f"{wd}/result.json", "w"), indent=1)
    print(json.dumps(result, indent=1))


if __name__ == "__main__":
    main()
