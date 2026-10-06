#!/usr/bin/env python3
"""Greenhouse application lane: work a shared queue, one job at a time.

One lane drives one browser (one CDP port). Multiple lanes on different
machines/ports share the queue file; job_queue.py claims stop two lanes from
working the same job.

Pipeline per job:
  1. claim next pending job (job_queue.py)
  2. fresh browser tab, navigate to the posting
  3. dead-posting check (404 / inactive text)
  4. schema_dump -> map (deterministic hard_patterns/templates first,
     local model only for unmapped or low-confidence fields)
  5. fill.apply_fill (no model calls inside the fill)
  6. validity gate: form.checkValidity() must be clean
  7. submit click (skipped with --no-submit)
  8. outcome detection: code gate | submitted | unconfirmed
  9. tracker.py record + job_queue.py mark applied/skipped/blocked/dead

Config (env vars, see config.example.json):
  JOBPILOT_CDP_URL    host:port of this lane's Chrome (e.g. 127.0.0.1:9445)
  JOBPILOT_MODEL_URL  OpenAI-compatible model endpoint (backup only)
  JOBPILOT_FACTS      path to facts.json
  JOBPILOT_QUEUE      queue.json path
  JOBPILOT_CLAIMS     claims.jsonl path
  JOBPILOT_LANE       lane name, e.g. mac-mini:9445
  TRACKER_BACKENDS    comma-separated tracker backends (default: jsonl)

The Greenhouse code gate is human-in-the-loop by design: when it appears
the lane releases its claim, writes result.json with status "code-gate",
and exits. Run code_gate.py (or enter the code manually), then mark the
job applied. Never guess or brute-force codes.

Usage:
  python3 lane_greenhouse.py --workdir /tmp/jobpilot/lane1 [--no-submit]
  python3 lane_greenhouse.py --workdir /tmp/jobpilot/lane1 --max-jobs 10
"""
import argparse
import base64
import datetime
import json
import os
import re
import subprocess
import sys
import time
import urllib.parse

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)

from job_queue import claim, next_job, release, mark, requeue
from tracker import build_record, track
from config import load_config
from map import GUARD_RE

DEAD_RE = re.compile(r"page not found|no longer active|job not found",
                     re.I)
CODE_GATE_RE = re.compile(
    r"verification code was sent|8-character|security code", re.I)
CONFIRM_RE = re.compile(
    r"thank you for applying|application has been received|"
    r"successfully been received|your application was submitted", re.I)

VALIDITY_GATE_JS = """(() => {
  const bad = [];
  for (const el of document.querySelectorAll('input,select,textarea')) {
    if (!el.checkValidity()) {
      const label = (el.labels && el.labels[0] && el.labels[0].innerText) ||
                    el.getAttribute('aria-label') || el.name || el.id || el.type;
      bad.push(label.trim().slice(0, 80) + ' :: ' + el.validationMessage);
    }
  }
  return bad;
})()"""

SUBMIT_JS = """(() => {
  const form = document.querySelector('form');
  const scope = form || document;
  const el = scope.querySelector('#submit_app') ||
             scope.querySelector('input[type=submit]') ||
             scope.querySelector('button[type=submit]') ||
             [...scope.querySelectorAll('button')]
               .find(b => /submit application/i.test(b.innerText));
  if (!el) return {found: false, clicked: false};
  el.scrollIntoView({block: 'center'});
  el.click();
  return {found: true, clicked: true};
})()"""


def log(*a):
    print(*a, flush=True)


def b64(s):
    return base64.b64encode(s.encode()).decode()


def cdp(cdp_url, cmd, *args, timeout=120):
    r = subprocess.run(
        [sys.executable, os.path.join(HERE, "cdp_direct.py"),
         cdp_url, cmd, *args],
        capture_output=True, text=True, timeout=timeout)
    for line in reversed(r.stdout.strip().split("\n")):
        line = line.strip()
        if line.startswith("{"):
            try:
                return json.loads(line)
            except ValueError:
                pass
    return {"ok": False, "raw": r.stdout[-300:], "err": r.stderr[-300:]}


def cdp_value(res):
    """Unwrap CDP eval results, which may nest under result.value."""
    v = res.get("result", res)
    if isinstance(v, dict) and "value" in v:
        return v["value"]
    return v


def consent_blockers(schema):
    """Fields that need explicit human approval.

    Independent of the mapper: arbitration agreements, certifications /
    attestations, background checks, drug tests, and assessments are
    NEVER auto-answered. Returns the offending field labels.
    """
    bad = []
    for f in schema.get("fields", []):
        blob = "%s %s %s" % (f.get("label", ""), f.get("key", ""),
                             f.get("option_label", ""))
        if GUARD_RE.search(blob):
            bad.append((f.get("label") or f.get("key") or "?")[:80])
    return bad


def slug(url):
    s = re.sub(r"[^a-z0-9]+", "-", url.rstrip("/").split("/")[-1]
               .split("?")[0].lower()).strip("-")
    return (s or "job")[:40]


def run_job(job, cfg, no_submit):
    """Work one claimed job. Returns a result dict."""
    cdp_url, port = cfg["cdp_url"], cfg["port"]
    work = cfg.get("job_workdir") or os.path.join(
        cfg["workdir"], "job-" + slug(job["url"]))
    os.makedirs(work, exist_ok=True)
    out = {"url": job["url"], "company": job.get("company", ""),
           "title": job.get("title", ""), "lane": cfg["lane"]}

    # 1-2. fresh tab + navigate
    cdp(cdp_url, "reset")
    cdp(cdp_url, "goto", b64(job["url"]))
    time.sleep(8)
    cur = cdp_value(cdp(cdp_url, "url"))
    cur_url = cur.get("url", cur) if isinstance(cur, dict) else str(cur)
    want_host = (urllib.parse.urlsplit(job["url"]).hostname or "").lower()
    got_host = (urllib.parse.urlsplit(cur_url).hostname or "").lower()
    if not got_host or got_host != want_host:
        out.update(status="blocked",
                   reason="navigation check failed: at %s, expected %s"
                   % (got_host or "?", want_host))
        return out

    # 3. dead posting check
    txt = (cdp_value(cdp(cdp_url, "text", "2000")) or "")
    if isinstance(txt, dict):
        txt = txt.get("text", "")
    if DEAD_RE.search(str(txt)):
        out.update(status="dead", reason="posting not found / inactive")
        return out

    # 4. schema + map (deterministic-first inside map_fields)
    schema_path = os.path.join(work, "schema.json")
    r = subprocess.run(
        [sys.executable, os.path.join(HERE, "schema_dump.py"),
         "--url", job["url"], "--port", str(port), "--out", schema_path],
        capture_output=True, text=True, timeout=180)
    if r.returncode != 0:
        out.update(status="blocked",
                   reason="schema dump failed: " + r.stderr[-300:])
        return out
    schema = json.load(open(schema_path))
    log("schema fields:", len(schema.get("fields", [])))
    blockers = consent_blockers(schema)
    if blockers:
        out.update(status="blocked",
                   reason="consent fields need human approval: " +
                   "; ".join(blockers[:5]))
        return out

    from map import map_fields
    facts = json.load(open(cfg["facts"]))
    fmap = map_fields(schema, facts, ats="greenhouse")
    guarded = fmap.get("skipped_by_guard", [])
    log("mapped:", len(fmap["map"]), "guarded:", len(guarded),
        "unmapped:", len(fmap.get("unmapped", [])))
    if guarded:
        out["guarded"] = [g.get("field") for g in guarded]
        out.update(status="blocked",
                   reason="guarded fields need applicant review: " +
                   ", ".join(out["guarded"][:5]))
        return out

    # 5. fill
    from fill import apply_fill
    res = apply_fill(schema, fmap, port, True,
                     os.path.join(work, "fill.png"))
    per = res.get("per_field", [])
    applied_n = sum(1 for p in per if p.get("applied"))
    log("fill: %d/%d applied" % (applied_n, len(per)))
    not_applied = [p for p in per if not p.get("applied")]
    if not_applied:
        out["not_applied"] = [
            {"field": p.get("field"), "note": str(p.get("note", ""))[:120]}
            for p in not_applied[:10]]

    # 6. validity gate: fail closed on any evaluation problem
    eval_res = cdp(cdp_url, "evalb64", b64(VALIDITY_GATE_JS))
    bad = cdp_value(eval_res)
    if not isinstance(bad, list):
        out.update(status="blocked",
                   reason="validity gate could not evaluate the form")
        return out
    if bad:
        out.update(status="blocked",
                   reason="validity gate: " + "; ".join(bad[:5]))
        return out
    log("validity gate clean")

    # 6b. required fields must all be filled
    by_key = {f["key"]: f for f in schema.get("fields", [])}
    filled = {p["field"] for p in per if p.get("applied")}
    missing = [k for k, f in by_key.items()
               if f.get("required") and k not in filled]
    if missing:
        out.update(status="blocked",
                   reason="required fields unfilled: " +
                   ", ".join(missing[:5]))
        return out

    if no_submit:
        out.update(status="ready", work=work)
        return out

    # 7. submit: one form-scoped find+click, verified
    clicked = cdp_value(cdp(cdp_url, "evalb64", b64(SUBMIT_JS)))
    if not (isinstance(clicked, dict) and clicked.get("clicked")):
        out.update(status="blocked",
                   reason="submit control not found or not clicked")
        return out
    time.sleep(10)

    # 8. outcome
    page = cdp_value(cdp(cdp_url, "text", "6000"))
    page_txt = str(page.get("text", page) if isinstance(page, dict)
                   else page)
    url_after = json.dumps(cdp(cdp_url, "url"))
    if CODE_GATE_RE.search(page_txt):
        out.update(status="code-gate",
                   reason="Greenhouse 8-char email code gate; "
                   "operator must enter the code, then mark applied")
    elif CONFIRM_RE.search(page_txt) or "/confirmation" in url_after:
        out.update(status="submitted",
                   confirmation_evidence=page_txt[:300])
    else:
        out.update(status="unconfirmed",
                   reason="no confirmation text or URL after submit")
    return out


STATUS_MAP = {"submitted": "Applied", "skipped": "Skipped",
              "dead": "Dead", "blocked": "Blocked",
              "unconfirmed": "Blocked", "code-gate": "Blocked",
              "ready": "Ready"}


def finish_job(job, result, cfg):
    """Track + mark the queue. Returns the terminal queue status."""
    status = result.get("status")
    url = job["url"]
    if status == "code-gate":
        # Human-in-the-loop: the operator enters the 8-char email code
        # (code_gate.py or manually), then returns the job to the queue:
        #   python3 job_queue.py requeue <url> --reason "code entered"
        # Marking blocked (not releasing) stops another lane from
        # re-submitting and triggering a second code email.
        reason = ("code-gate: enter the email code, then run "
                  "python3 job_queue.py requeue URL")
        record = build_record(
            company=job.get("company", ""), title=job.get("title", ""),
            location=job.get("location", ""), salary=job.get("salary", ""),
            source="Greenhouse", ats="Greenhouse", url=url,
            status="Blocked", lane=cfg["lane"], notes=reason)
        for r in track(record, cfg["tracker_backends"]):
            if r.get("error"):
                log("TRACKER ERROR:", r)
        mark(cfg["queue"], cfg["claims"], url, "blocked", reason,
             lane=cfg["lane"])
        return "code-gate"
    if status == "ready":
        # Dry run: release the claim, keep the job pending for a real run.
        release(cfg["claims"], url, cfg["lane"])
        return "ready"
    qstatus = STATUS_MAP.get(status, "blocked").lower()
    if qstatus not in ("applied", "skipped", "dead", "blocked"):
        qstatus = "blocked"
    record = build_record(
        company=job.get("company", ""), title=job.get("title", ""),
        location=job.get("location", ""), salary=job.get("salary", ""),
        source="Greenhouse", ats="Greenhouse", url=url,
        status=STATUS_MAP.get(status, "Blocked"),
        confirmation=result.get("confirmation_evidence", "")[:200],
        lane=cfg["lane"], notes=result.get("reason", ""))
    for r in track(record, cfg["tracker_backends"]):
        if r.get("error"):
            log("TRACKER ERROR:", r)
    ok = mark(cfg["queue"], cfg["claims"], url, qstatus,
              result.get("reason", ""), lane=cfg["lane"])
    if not ok:
        log("WARNING: could not mark %s (not owner or not found)" % url)
    return qstatus


def main():
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--workdir", required=True)
    ap.add_argument("--no-submit", action="store_true")
    ap.add_argument("--max-jobs", type=int, default=0,
                    help="0 = run until the queue is empty")
    a = ap.parse_args()

    file_cfg = load_config()
    cdp_url = os.environ.get("JOBPILOT_CDP_URL",
                             file_cfg.get("JOBPILOT_CDP_URL",
                                          "127.0.0.1:9226"))
    host = cdp_url.rsplit(":", 1)[0]
    if host not in ("127.0.0.1", "localhost"):
        # Sibling modules address the browser by port only; a remote host
        # here would silently drive the wrong browser. Fail closed.
        log("FATAL: JOBPILOT_CDP_URL must be local (127.0.0.1:<port>); "
            "for a remote browser host, run the lane on that host.")
        sys.exit(2)
    port = int(cdp_url.rsplit(":", 1)[-1])
    cfg = {
        "cdp_url": cdp_url, "port": port,
        "facts": os.environ.get("JOBPILOT_FACTS",
                                file_cfg.get("JOBPILOT_FACTS",
                                             os.path.join(HERE,
                                                          "facts.json"))),
        "queue": os.environ.get("JOBPILOT_QUEUE",
                                file_cfg.get("JOBPILOT_QUEUE",
                                             os.path.join(HERE,
                                                          "queue.json"))),
        "claims": os.environ.get("JOBPILOT_CLAIMS",
                                 file_cfg.get("JOBPILOT_CLAIMS",
                                              os.path.join(HERE,
                                                           "claims.jsonl"))),
        "lane": os.environ.get("JOBPILOT_LANE",
                               file_cfg.get("JOBPILOT_LANE", "lane-1")),
        "workdir": a.workdir,
        "tracker_backends": [
            b.strip() for b in
            os.environ.get("TRACKER_BACKENDS",
                           file_cfg.get("TRACKER_BACKENDS", "jsonl")
                           ).split(",") if b.strip()],
    }
    if not os.path.exists(cfg["facts"]):
        log("FATAL: facts not found: %s (cp facts.example.json facts.json "
            "and fill it in)" % cfg["facts"])
        sys.exit(2)
    os.environ.setdefault("ATS_CDP_URL", cdp_url)
    if os.environ.get("JOBPILOT_MODEL_URL"):
        os.environ.setdefault("ATS_MODEL_URL",
                              os.environ["JOBPILOT_MODEL_URL"])
    os.makedirs(cfg["workdir"], exist_ok=True)

    done, n = 0, 0
    while True:
        if a.max_jobs and n >= a.max_jobs:
            break
        job = next_job(cfg["queue"], cfg["claims"], cfg["lane"],
                         ats="greenhouse")
        if not job:
            log("queue empty (or all remaining jobs claimed); done")
            break
        job = claim(cfg["queue"], cfg["claims"], job["url"], cfg["lane"],
                    ats="greenhouse")
        if not job:
            continue
        n += 1
        log("== job %d: %s - %s" %
            (n, job.get("company", ""), job.get("title", "")))
        work = os.path.join(cfg["workdir"],
                            "job-%s-%d" % (slug(job["url"]), n))
        os.makedirs(work, exist_ok=True)
        cfg["job_workdir"] = work
        try:
            result = run_job(job, cfg, a.no_submit)
        except Exception as e:
            result = {"url": job["url"], "status": "blocked",
                      "reason": "lane exception: %r" % e}
            log("EXCEPTION:", e)
        try:
            json.dump(result, open(os.path.join(work, "result.json"), "w"),
                      indent=1)
            terminal = finish_job(job, result, cfg)
        except OSError as e:
            # Finalization itself failed: release so the job is not stuck
            # claimed, and keep the error loud.
            log("FINALIZE ERROR:", e)
            release(cfg["claims"], job["url"], cfg["lane"])
            terminal = "finalize-error"
        log("RESULT:", result.get("status"), "->", terminal)
        if terminal == "code-gate":
            log("CODE GATE: enter the 8-char email code for the posting, "
                "then requeue it with: "
                "python3 job_queue.py requeue URL --reason 'code entered'")
            break
        done += 1
    log("lane finished: %d jobs worked" % done)


if __name__ == "__main__":
    main()
