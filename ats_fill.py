#!/usr/bin/env python3
"""Orchestrator: dump schema -> map -> fill -> (submit) -> verify.

Usage:
  ats_fill.py --url <application URL> --port 9226 [--ats greenhouse|ashby|lever|workable]
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
from common import cdp_ok

HERE = os.path.dirname(os.path.abspath(__file__))
GOAL_HIDDEN = os.path.dirname(HERE)

UNAVAILABLE_MARKERS = (
    "no longer available",
    "no longer accepting",
    "job posting is no longer",
    "page not found",
    "job not found",
    "couldn't find that",
    "this job has expired",
    "position has been filled",
)


def unavailable_posting_marker(page_text):
    normalized = " ".join(str(page_text or "").split()).casefold()
    return next((marker for marker in UNAVAILABLE_MARKERS if marker in normalized), None)


class PostingUnavailableError(RuntimeError):
    pass


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


def validate_mapping(schema, fmap=None):
    """Fail closed when schema extraction or field mapping yields nothing."""
    if not schema.get("fields", []):
        raise RuntimeError("schema extraction returned zero fields; refusing to continue")
    if fmap is not None and not fmap.get("map", []):
        raise RuntimeError("mapping returned zero fields; refusing to continue")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--url", required=True)
    ap.add_argument("--port", type=int, default=9226)
    ap.add_argument("--ats", default="greenhouse",
                    choices=["greenhouse", "ashby", "lever", "workable"],
                    help="ATS hint (the fill pipeline is ATS-agnostic)")
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
        if not schema.get("fields"):
            page_text = cdp_ok(a.port, "text", "3000").get("text", "")
            marker = unavailable_posting_marker(page_text)
            if marker:
                result["notes"].append("schema: 0 fields")
                raise PostingUnavailableError(marker)
        validate_mapping(schema)
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
        fmap = map_fields(schema, facts, ats=a.ats)
        validate_mapping(schema, fmap)
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
    except PostingUnavailableError as e:
        result["status"] = "blocked"
        result["notes"].append(f"posting unavailable: {e}")
    except Exception as e:
        result["notes"].append(f"ERROR {type(e).__name__}: {e}")
    finally:
        release_lock(a.port)
        with open(f"{wd}/result.json", "w") as result_file:
            json.dump(result, result_file, indent=1)
    print(json.dumps(result, indent=1))


if __name__ == "__main__":
    main()
