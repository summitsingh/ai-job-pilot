#!/usr/bin/env python3
"""Batch runner: apply to a queue of postings, one at a time.

Usage: batch_apply.py --queue urls.txt --port 9226 [--log applications.jsonl]

The queue file holds one posting URL per line; blank lines and lines
starting with '#' are ignored.

For each URL:
  1. ats_fill.py --no-submit fills and verifies (never submits blind).
  2. The operator reviews the result.json summary and confirms.
  3. On confirmation: submit_only.py clicks submit, then code_gate.py
     handles the Greenhouse email verification gate (prompts for the
     code on stdin).
  4. One JSON record per application is appended to the log file
     (default ./applications.jsonl).

One port drives the whole batch; ats_fill.py's browser-<port>.lock is
respected. Ctrl-C stops cleanly between applications.
"""
import argparse
import datetime
import json
import os
import subprocess
import sys

HERE = os.path.dirname(os.path.abspath(__file__))


def run(*args):
    r = subprocess.run([sys.executable] + list(args),
                       capture_output=True, text=True)
    return r


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--queue", required=True,
                    help="text file with one posting URL per line")
    ap.add_argument("--port", type=int, required=True)
    ap.add_argument("--log", default="applications.jsonl",
                    help="JSONL log file (one record per application)")
    ap.add_argument("--workdir-base", default="/tmp/jobpilot/batch",
                    help="parent dir for per-application workdirs")
    a = ap.parse_args()

    with open(a.queue) as fh:
        urls = [ln.strip() for ln in fh
                if ln.strip() and not ln.strip().startswith("#")]
    print(f"{len(urls)} URLs in queue.")
    os.makedirs(a.workdir_base, exist_ok=True)

    for i, url in enumerate(urls, 1):
        wd = os.path.join(a.workdir_base, f"run{i:03d}")
        os.makedirs(wd, exist_ok=True)
        print(f"\n=== [{i}/{len(urls)}] {url} ===")
        rec = {"ts": datetime.datetime.now(
            datetime.timezone.utc).isoformat(),
            "url": url, "workdir": wd}

        r = run(os.path.join(HERE, "ats_fill.py"),
                "--url", url, "--port", str(a.port),
                "--no-submit", "--workdir", wd)
        if r.returncode != 0:
            rec["status"] = "fill-error"
            rec["note"] = (r.stderr or "")[-500:]
            print("fill step failed:", rec["note"])
            _log(a.log, rec)
            continue

        try:
            res = json.load(open(os.path.join(wd, "result.json")))
        except Exception as e:
            rec["status"] = "fill-error"
            rec["note"] = f"result.json unreadable: {e}"
            _log(a.log, rec)
            continue

        rec["status"] = res.get("status")
        rec["fields_filled"] = res.get("fields_filled")
        rec["fields_skipped"] = res.get("fields_skipped")
        print(f"status: {rec['status']}, "
              f"filled: {rec['fields_filled']}, "
              f"skipped: {rec['fields_skipped']}")
        for n in res.get("notes", [])[:6]:
            print("  -", n)

        if res.get("status") != "filled-no-submit":
            rec["note"] = "not in a submittable state; skipping submit"
            _log(a.log, rec)
            continue

        ans = input("Submit this application? [y]es / [s]kip / [q]uit: "
                    ).strip().lower()
        if ans == "q":
            rec["note"] = "operator quit the batch"
            _log(a.log, rec)
            print("Batch stopped by operator.")
            break
        if ans != "y":
            rec["note"] = "operator skipped submit"
            _log(a.log, rec)
            continue

        # Click submit on the already-filled form, then handle the
        # email code gate (operator pastes the code when prompted).
        run(os.path.join(HERE, "submit_only.py"),
            "--port", str(a.port), "--workdir", wd)
        r = run(os.path.join(HERE, "code_gate.py"),
                "--port", str(a.port), "--workdir", wd)
        try:
            gate = json.load(open(os.path.join(wd, "code_gate.json")))
        except Exception:
            gate = {}
        rec["code_gate_entered"] = gate.get("entered", False)
        rec["confirmed"] = gate.get("confirmed", False)
        rec["confirmation_url"] = gate.get("url", "")
        rec["status"] = ("confirmed" if rec["confirmed"]
                         else "submit-unconfirmed")
        print("confirmed:", rec["confirmed"], rec["confirmation_url"])
        _log(a.log, rec)

    print(f"\nBatch done. Log: {a.log}")


def _log(path, rec):
    with open(path, "a") as fh:
        fh.write(json.dumps(rec) + "\n")


if __name__ == "__main__":
    main()
