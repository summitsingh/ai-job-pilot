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
  6c. location verifier: the filled location must match a target metro
  7. submit click (skipped with --no-submit or --dry-run)
  8. submit watchdog (submit_watchdog.py): detects a swallowed click and
     walks a wait / re-click / JS-click ladder; outcome is code gate |
     submitted | unconfirmed | blocked
  9. tracker.py record + job_queue.py mark applied/skipped/blocked/dead

Config (env vars, see config.example.json):
  JOBPILOT_CDP_URL    host:port of this lane's Chrome (e.g. 127.0.0.1:9445)
  JOBPILOT_MODEL_URL  OpenAI-compatible model endpoint (backup only)
  JOBPILOT_FACTS      path to facts.json
  JOBPILOT_QUEUE      queue.json path
  JOBPILOT_CLAIMS     claims.jsonl path
  JOBPILOT_LANE       lane name, e.g. mac-mini:9445
  TRACKER_BACKENDS    comma-separated tracker backends (default: jsonl)
  JOBPILOT_TARGET_METROS  allowed locations, e.g. "Austin, TX; Remote (US)"
  JOBPILOT_SUBMIT_WAIT_S / _RECHECKS / _POLL_S  submit watchdog timing

The Greenhouse code gate is human-in-the-loop by design: when it appears
the lane marks the job blocked, writes result.json with status "code-gate",
and exits. Run code_gate.py (or enter the code manually), then requeue
the job after human handling. Never guess or brute-force codes.

Usage:
  python3 lane_greenhouse.py --workdir /tmp/jobpilot/lane1 [--no-submit]
  python3 lane_greenhouse.py --workdir /tmp/jobpilot/lane1 --max-jobs 10
  python3 lane_greenhouse.py --workdir /tmp/jobpilot/lane1 --dry-run

--dry-run fills and checks the form, saves dryrun.png, and prints the field
map for first-run approval. Consent still requires explicit human approval.
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
from location_check import verify_location, load_target_metros
from submit_watchdog import check_submit, load_watchdog_cfg

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

NORMAL_CLICK_SELECTOR = ("#submit_app, form input[type=submit], "
                         "form button[type=submit]")


class CdpSubmitAdapter:
    """submit_watchdog adapter backed by this lane's CDP driver."""

    def __init__(self, cdp_url):
        self.cdp_url = cdp_url

    def current_url(self):
        cur = cdp_value(cdp(self.cdp_url, "url"))
        return str(cur.get("url", cur) if isinstance(cur, dict) else cur)

    def page_text(self, limit):
        page = cdp_value(cdp(self.cdp_url, "text", str(limit)))
        return str(page.get("text", page) if isinstance(page, dict)
                   else page)

    def click_submit(self, mode):
        if mode == "js":
            r = cdp_value(cdp(self.cdp_url, "evalb64", b64(SUBMIT_JS)))
            return r if isinstance(r, dict) else {"clicked": False}
        r = cdp(self.cdp_url, "fclick", b64(NORMAL_CLICK_SELECTOR))
        return {"clicked": bool(r.get("ok")), "found": bool(r.get("ok"))}


def location_values(per, schema):
    """Final filled values of location fields: [(field_key, value)].

    A field counts as a location field when its fill action is "location",
    its key contains "location", or its schema label mentions location.
    Uses the post-fill readback ("actual"), not the typed string.
    """
    labels = {f.get("key"): (f.get("label") or "")
              for f in schema.get("fields", [])}
    out = []
    for p in per:
        key = p.get("field") or ""
        if (p.get("action") == "location" or "location" in key.lower()
                or "location" in labels.get(key, "").lower()):
            actual = p.get("actual")
            if actual in (None, "<not-found>"):
                actual = ""
            out.append((key, str(actual)))
    return out


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


def format_field_map(fmap):
    """Render mapped answers and skips as a human-readable review table."""
    rows = [("Field label/key", "Value or SKIP + reason")]
    seen = set()
    for entry in fmap.get("map", []):
        key = str(entry.get("field") or entry.get("key") or "?")
        seen.add(key)
        label = entry.get("label")
        field = "%s [%s]" % (label, key) if label else key
        if entry.get("action") == "skip":
            value = "SKIP: " + str(entry.get("reason") or entry.get("note")
                                   or "no truthful answer available")
            if entry.get("value") not in (None, ""):
                value += " (value: %s)" % entry["value"]
        else:
            value = str(entry.get("value", ""))
        rows.append((field, value))
    for entry in fmap.get("unmapped", []):
        key = str(entry.get("field") or entry.get("key") or "?") \
            if isinstance(entry, dict) else str(entry)
        if key not in seen:
            rows.append((key, "SKIP: unmapped"))
    # Escape line breaks and tabs so each answer stays in its own row.
    rows = [tuple(v.replace("\n", "\\n").replace("\r", "\\r")
                  .replace("\t", "\\t") for v in row) for row in rows]
    width = max(len(row[0]) for row in rows)
    return "\n".join([rows[0][0].ljust(width) + " | " + rows[0][1],
                      "-" * width + "-+-" + "-" * len(rows[0][1])] +
                     [key.ljust(width) + " | " + value
                      for key, value in rows[1:]])


def run_job(job, cfg, no_submit, dry_run=False):
    """Work one claimed job. Returns a result dict."""
    cdp_url, port = cfg["cdp_url"], cfg["port"]
    work = cfg.get("job_workdir") or os.path.join(
        cfg["workdir"], "job-" + slug(job["url"]))
    os.makedirs(work, exist_ok=True)
    out = {"url": job["url"], "company": job.get("company", ""),
           "title": job.get("title", ""), "lane": cfg["lane"]}

    if dry_run:
        out["dry_run"] = True

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
    if dry_run:
        labels = {f["key"]: f.get("label", "")
                  for f in schema.get("fields", [])}
        review = dict(fmap, map=[dict(e, label=labels.get(e.get("field"), ""))
                                 for e in fmap.get("map", [])])
        log(format_field_map(review))
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

    # 5b. location verifier: autocompletes can pick a lookalike city
    # ("Austintown, Ohio" for "Austin, Texas"). Fail closed: flag for
    # review instead of submitting.
    metros = cfg.get("target_metros") or load_target_metros()
    for key, val in location_values(per, schema):
        chk = verify_location(val, metros)
        if not chk["ok"]:
            out.update(status="blocked",
                       reason="location check (flag for review): "
                       + chk["reason"])
            return out

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

    if dry_run:
        shot_path = os.path.join(work, "dryrun.png")
        shot = cdp(cdp_url, "shot", shot_path)
        if not shot.get("ok") or not os.path.isfile(shot_path):
            out.update(status="blocked", reason="dry-run screenshot failed")
            return out
        out["screenshot"] = shot_path

    if no_submit or dry_run:
        out.update(status="ready", work=work)
        return out

    # 7. submit: one click, then the watchdog owns outcome detection
    adapter = CdpSubmitAdapter(cdp_url)
    started_url = adapter.current_url()
    clicked = adapter.click_submit("normal")
    if not clicked.get("clicked"):
        clicked = adapter.click_submit("js")
    if not clicked.get("clicked"):
        out.update(status="blocked",
                   reason="submit control not found or not clicked")
        return out

    # 8. outcome. Re-clicks only happen while the page is unchanged, so a
    # slow-but-real submit is never doubled; the code gate stays human.
    wd = check_submit(adapter, started_url,
                      cfg.get("watchdog") or load_watchdog_cfg())
    out["watchdog"] = {"ladder": wd["ladder"], "evidence": wd["evidence"]}
    snippet = wd["evidence"].get("page_snippet", "")
    if wd["outcome"] == "code-gate":
        out.update(status="code-gate",
                   reason="Greenhouse 8-char email code gate; "
                   "operator must enter the code, then mark applied")
    elif wd["outcome"] == "confirmed":
        out.update(status="submitted", confirmation_evidence=snippet)
    elif wd["outcome"] == "error":
        out.update(status="blocked",
                   reason="submit watchdog: " +
                   wd["evidence"].get("note", "error after submit"))
    else:
        out.update(status="unconfirmed",
                   reason="no confirmation text or URL after submit "
                   "(watchdog ladder: %s)" % ", ".join(wd["ladder"]))
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
    ap.add_argument("--dry-run", action="store_true",
                    help="fill, check, screenshot and print answers, then stop")
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
            result = run_job(job, cfg, a.no_submit, dry_run=a.dry_run)
        except Exception as e:
            result = {"url": job["url"], "status": "blocked",
                      "reason": "lane exception: %r" % e}
            if a.dry_run:
                result["dry_run"] = True
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
        if a.dry_run:
            done += 1
            break
        if terminal == "code-gate":
            log("CODE GATE: enter the 8-char email code for the posting, "
                "then requeue it with: "
                "python3 job_queue.py requeue URL --reason 'code entered'")
            break
        done += 1
    log("lane finished: %d jobs worked" % done)


if __name__ == "__main__":
    main()
