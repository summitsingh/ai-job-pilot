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
the lane parks the job blocked and coordinates one human-code wait.
Verified confirmation marks Applied and records Applied. Only failed or
expired attempts may requeue for a later run, up to 3 attempts; no-input
and ambiguous results stay blocked. Never guess or brute-force codes.

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

from job_queue import (claim, next_job, release, mark, requeue, heartbeat,
                       begin_code_gate, load_queue, read_claims, norm_url, default_lane)
from tracker import build_record, track
from config import load_config
from map import GUARD_RE
from location_check import verify_location, verify_location_component, load_target_metros, validate_metros
from modal_common import guard_perplexity
from submit_watchdog import check_submit, load_watchdog_cfg, CODE_GATE_RE, CONFIRM_RE, classify
from terminal_output import sanitize_terminal
from codegate import coordinate, CliNotifier

DEAD_RE = re.compile(r"page not found|no longer active|job not found",
                     re.I)

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

    def __init__(self, cdp_url, applied_check=None):
        self.cdp_url = cdp_url
        self.applied_check = applied_check

    def current_url(self):
        cur = cdp_value(cdp(self.cdp_url, "url"))
        return str(cur.get("url", cur) if isinstance(cur, dict) else cur)

    def page_text(self, limit):
        page = cdp_value(cdp(self.cdp_url, "text", str(limit)))
        return str(page.get("text", page) if isinstance(page, dict)
                   else page)

    def submission_state(self):
        signal = cdp_value(cdp(self.cdp_url, "evalb64", b64("""(() => {
          const button = document.querySelector('#submit_app, button[type=submit], input[type=submit]');
          return {in_flight: !!(button && button.disabled) ||
            !!document.querySelector('[aria-busy="true"], [role="progressbar"]'),
            retry_safe: false};
        })()""")))
        # Enabled controls alone do not establish that a submission failed.
        return signal if isinstance(signal, dict) else {"retry_safe": False}

    def click_submit(self, mode):
        if self.applied_check and self.applied_check():
            return {"clicked": False, "note": "historical Applied; submit skipped"}
        if mode == "js":
            r = cdp_value(cdp(self.cdp_url, "evalb64", b64(SUBMIT_JS)))
            return r if isinstance(r, dict) else {"clicked": False}
        r = cdp(self.cdp_url, "fclick", b64(NORMAL_CLICK_SELECTOR))
        clicked = bool(r.get("ok"))
        miss = not clicked and str(r.get("error", "")).startswith("no such element:")
        return {"clicked": clicked, "found": clicked, "miss": miss,
                "ambiguous": not clicked and not miss}


def initial_submit(adapter, started_url, pre_click_text=""):
    """Fallback only for an explicit selector miss, never a lost response."""
    clicked = adapter.click_submit("normal")
    if clicked.get("clicked") or not clicked.get("miss"):
        return clicked
    url, text = adapter.current_url(), adapter.page_text(6000)
    state = classify(url, text, started_url, pre_click_text)
    signal = adapter.submission_state()
    if state != "pending" or signal.get("in_flight"):
        return {"clicked": False, "ambiguous": True, "state": state}
    return adapter.click_submit("js")


def location_values(per, schema):
    """Final filled values of location fields: [(field_key, value)].

    Check applicant address/location fields, excluding employer office,
    relocation and willingness questions. A location fill action also counts.
    Uses the post-fill readback ("actual"), not the typed string.
    """
    labels = {f.get("key"): (f.get("label") or "")
              for f in schema.get("fields", [])}
    out = []
    for p in per:
        key = p.get("field") or ""
        label = labels.get(key, "").lower().strip().rstrip(" *:?")
        # Postal readback is checked by fill; a ZIP alone cannot identify a metro.
        if re.fullmatch(r"(?:your |current |home |applicant |residential )?(?:zip(?: code)?|postal(?: code)?)", label) or re.fullmatch(r"(?:current_|home_|applicant_|residential_)?(?:zip|zip_code|postal|postal_code)", key.lower()):
            continue
        employer_question = re.search(r"\b(our|office|open to|willing|relocat|work at|work from)\b", label)
        applicant_label = (re.fullmatch(r"(?:your |current |home |applicant |residential )?(?:location|city|state|zip(?: code)?|postal(?: code)?|country|city/state|city and state)", label)
                           or re.search(r"\bwhere (?:are you|do you (?:live|reside))\b", label))
        applicant_key = re.fullmatch(r"(?:current_|home_|applicant_|residential_)?(?:location|city|state|zip|postal_code|country)", key.lower())
        if not employer_question and (p.get("action") == "location" or applicant_label or applicant_key):
            actual = p.get("actual")
            if actual in (None, "<not-found>"):
                actual = ""
            out.append((key, str(actual)))
    # Bind split city/state fields together, so two configured metros cannot
    # lend different components to a third, unconfigured city/state pair.
    cities = [(k, v) for k, v in out if _location_component(k, labels.get(k, "")) == "city"]
    states = [(k, v) for k, v in out if _location_component(k, labels.get(k, "")) == "state"]
    if len(cities) == 1 and len(states) == 1:
        city, state = cities[0], states[0]
        out = [(k, v) for k, v in out if k not in {city[0], state[0]}]
        out.append((city[0] + "+" + state[0], city[1] + ", " + state[1]))
    return out


def _location_component(key, label):
    label = (label or "").lower().strip().rstrip(" *:?")
    for component in ("city", "state", "country"):
        if (re.fullmatch(r"(?:your |current |home |applicant |residential )?" + component, label)
                or key.lower() in {component, "current_" + component, "home_" + component, "applicant_" + component, "residential_" + component}):
            return component
    return None


def verify_applicant_location(key, value, schema, metros):
    """Validate split address components without treating each as a full metro."""
    field = next((f for f in schema.get("fields", []) if f.get("key") == key), {})
    component = _location_component(key, field.get("label"))
    if component:
        return verify_location_component(value, metros, component)
    return verify_location(value, metros)


def log(*a):
    print(*(sanitize_terminal(value) for value in a), flush=True)


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
    rows = [tuple(sanitize_terminal(v.replace("\n", "\\n").replace("\r", "\\r")
                  .replace("\t", "\\t")) for v in row) for row in rows]
    width = max(len(row[0]) for row in rows)
    return "\n".join([rows[0][0].ljust(width) + " | " + rows[0][1],
                      "-" * width + "-+-" + "-" * len(rows[0][1])] +
                     [key.ljust(width) + " | " + value
                      for key, value in rows[1:]])


def already_applied(url, cfg):
    """Historical Applied wins over a mistakenly requeued or stale job."""
    identity = norm_url(url)
    if any(norm_url(j["url"]) == identity and j.get("status", "").lower() == "applied"
           for j in load_queue(cfg["queue"])):
        return True
    claims, _ = read_claims(cfg["claims"])
    if any(norm_url(c["url"]) == identity and c.get("status", "").lower() == "applied"
           for c in claims):
        return True
    path = cfg.get("tracker_jsonl") or os.environ.get("TRACKER_JSONL_PATH", "applications.jsonl")
    if os.path.exists(path):
        with open(path) as f:
            for line in f:
                if not line.strip():
                    continue
                record = json.loads(line)
                if (norm_url(record.get("url", "")) == identity and
                        record.get("status", "").lower() in
                        {"applied", "responded", "screening", "interview", "offer", "rejected", "withdrawn"}):
                    return True
    return False


def enter_gate_code(cdp_url, code, applied_check=None):
    """Enter one human code once, submit once, and verify the page."""
    from code_gate import GATE_JS, FILL_JS, SUBMIT_JS as GATE_SUBMIT_JS
    gate = cdp_value(cdp(cdp_url, "evalb64", b64(GATE_JS)))
    if not isinstance(gate, dict) or not gate.get("gate") or gate.get("boxes") != 8:
        return {"confirmed": False, "note": "expected eight verification boxes"}
    call = "(" + FILL_JS + ")(" + json.dumps({"keys": gate["keys"], "code": code}) + ")"
    filled = cdp_value(cdp(cdp_url, "evalb64", b64(call)))
    if not isinstance(filled, list) or len(filled) != 8 or not all(f.get("ok") for f in filled):
        return {"confirmed": False, "note": "code boxes did not accept all characters"}
    if applied_check and applied_check():
        return {"confirmed": True, "confirmation": "historical Applied; code submit skipped"}
    clicked = cdp_value(cdp(cdp_url, "evalb64", b64(GATE_SUBMIT_JS)))
    if clicked != "CLICKED":
        return {"confirmed": False, "note": "code submit control not clicked"}
    adapter = CdpSubmitAdapter(cdp_url)
    started = adapter.current_url()
    # A code submission is never re-clicked. Slow/ambiguous results block.
    for _ in range(15):
        url, text = adapter.current_url(), adapter.page_text(6000)
        match = CONFIRM_RE.search(text)
        if "/confirmation" in url or match:
            return {"confirmed": True, "url": url,
                    "confirmation": match.group(0) if match else "/confirmation"}
        failed = re.search(
            r"\b(?:invalid|expired|incorrect) (?:verification |security )?code\b|"
            r"\b(?:verification |security )?code (?:is |has )?(?:invalid|expired|incorrect)\b",
            text, re.I)
        if failed:
            return {"confirmed": False, "url": url, "note": failed.group(0)}
        time.sleep(2)
    return {"confirmed": False, "ambiguous": True, "url": adapter.current_url(),
            "note": "code submitted but confirmation unverified; human review required"}


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

    if not dry_run and not no_submit and already_applied(job["url"], cfg):
        out.update(status="already-applied", reason="historical Applied; submit skipped")
        return out

    try:
        guard_perplexity(job["url"])
    except RuntimeError as e:
        out.update(status="blocked", reason=str(e))
        return out

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
        print(format_field_map(review), flush=True)
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
        chk = verify_applicant_location(key, val, schema, metros)
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

    if already_applied(job["url"], cfg):
        out.update(status="already-applied", reason="historical Applied; submit skipped")
        return out
    if not heartbeat(cfg["claims"], job["url"], cfg["lane"]):
        out.update(status="blocked", reason="live claim lost before submit")
        return out

    # 7. submit: one click, then the watchdog owns outcome detection
    adapter = CdpSubmitAdapter(cdp_url, lambda: already_applied(job["url"], cfg))
    started_url = adapter.current_url()
    pre_click_text = adapter.page_text(6000)
    clicked = initial_submit(adapter, started_url, pre_click_text=pre_click_text)
    if not clicked.get("clicked") and not clicked.get("ambiguous"):
        out.update(status="blocked",
                   reason="submit control not found or not clicked")
        return out

    # 8. Ambiguous submit states block; recovery needs explicit safe evidence.
    wd = check_submit(adapter, started_url,
                      dict(cfg.get("watchdog") or load_watchdog_cfg(),
                           pre_click_text=pre_click_text))
    heartbeat(cfg["claims"], job["url"], cfg["lane"])
    out["watchdog"] = {"ladder": wd["ladder"], "evidence": wd["evidence"]}
    confirmation = confirmation_summary(wd["evidence"].get("final_url", ""),
                                        wd["evidence"].get("confirmation_phrase", ""))
    if wd["outcome"] == "code-gate":
        out.update(status="code-gate",
                   reason="Greenhouse 8-char email code gate; "
                   "operator must enter the code, then mark applied")
    elif wd["outcome"] == "confirmed":
        out.update(status="submitted", confirmation_evidence=confirmation)
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


def confirmation_summary(url, text):
    """Store only a recognized confirmation phrase and URL, never page copy."""
    matched = CONFIRM_RE.search(str(text or ""))
    phrase = matched.group(0) if matched else ""
    return "; ".join(part for part in (phrase, str(url or "")) if part)


def finish_job(job, result, cfg):
    """Track + mark the queue. Returns the terminal queue status."""
    status = result.get("status")
    url = job["url"]
    if result.get("dry_run") or cfg.get("dry_run"):
        release(cfg["claims"], url, cfg["lane"])
        return status
    if status == "already-applied":
        mark(cfg["queue"], cfg["claims"], url, "applied", result.get("reason", ""), lane=cfg["lane"])
        return "applied"
    if status == "code-gate":
        if already_applied(url, cfg):
            return finish_job(job, {"status": "already-applied"}, cfg)
        attempts = begin_code_gate(cfg["queue"], cfg["claims"], url, cfg["lane"])
        reason = "code-gate: waiting for human code"
        if not mark(cfg["queue"], cfg["claims"], url, "blocked", reason, lane=cfg["lane"]):
            raise RuntimeError("could not block code-gate job")
        if attempts is None:
            gate = {"status": "blocked", "reason": "code-gate attempt cap or ownership check failed"}
        else:
            def code_callback(code):
                if already_applied(url, cfg):
                    return {"confirmed": True, "confirmation": "historical Applied"}
                return enter_gate_code(cfg["cdp_url"], code,
                                       applied_check=lambda: already_applied(url, cfg))

            def retry():
                return requeue(cfg["queue"], cfg["claims"], url,
                               reason="failed or expired code-gate attempt")

            gate = coordinate(cfg.get("notifier") or CliNotifier(),
                              cfg.get("code_timeout_s", 300), code_callback, retry,
                              attempts=attempts)
            if (gate["status"] in {"failed", "invalid-code"} and attempts < 3
                    and not gate.get("result", {}).get("ambiguous")):
                gate["requeued"] = retry()
        result["code_gate"] = gate
        if gate["status"] == "confirmed":
            evidence = gate["result"]
            result.update(status="submitted", confirmation_evidence=
                          confirmation_summary(evidence.get("url", ""), evidence.get("confirmation", "")))
            return finish_job(job, result, cfg)
        reason = "code-gate attempt %s/3: %s" % (attempts or 3, gate["status"])
        reason += "; " + gate.get("result", {}).get("note", gate.get("reason", "human review required"))
        result.update(status="code-requeued" if gate.get("requeued") else "blocked", reason=reason)
        if gate.get("requeued"):
            return "code-requeued"
        return finish_job(job, result, cfg)
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
    ok = mark(cfg["queue"], cfg["claims"], url, qstatus,
              result.get("reason", ""), lane=cfg["lane"])
    if not ok:
        raise RuntimeError("could not mark %s (not owner or not found)" % url)
    for r in track(record, cfg["tracker_backends"]):
        if r.get("error"):
            log("TRACKER ERROR:", r)
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
                               file_cfg.get("JOBPILOT_LANE") or default_lane()),
        "workdir": a.workdir, "dry_run": a.dry_run,
        "tracker_backends": [
            b.strip() for b in
            os.environ.get("TRACKER_BACKENDS",
                           file_cfg.get("TRACKER_BACKENDS", "jsonl")
                           ).split(",") if b.strip()],
    }
    try:
        cfg["target_metros"] = validate_metros(load_target_metros(file_cfg))
    except ValueError as e:
        log("FATAL: location configuration: %s" % e)
        sys.exit(2)
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
        cfg["job_workdir"] = work
        try:
            heartbeat(cfg["claims"], job["url"], cfg["lane"])
            os.makedirs(work, exist_ok=True)
            result = run_job(job, cfg, a.no_submit, dry_run=a.dry_run)
        except Exception as e:
            result = {"url": job["url"], "status": "blocked",
                      "reason": "lane exception: %r" % e}
            if a.dry_run:
                result["dry_run"] = True
            log("EXCEPTION:", e)
        finalized = False
        try:
            terminal = finish_job(job, result, cfg)
            finalized = True
            with open(os.path.join(work, "result.json"), "w") as f:
                json.dump(result, f, indent=1)
        except Exception as e:
            log("FINALIZE ERROR:", e)
            # A code-gate job was parked blocked before waiting. Keep it
            # blocked if callbacks or persistence fail; never retry a submit.
            release(cfg["claims"], job["url"], cfg["lane"])
            terminal = "finalize-error"
        finally:
            if a.dry_run and not finalized:
                release(cfg["claims"], job["url"], cfg["lane"])
        log("RESULT:", result.get("status"), "->", terminal)
        if a.dry_run:
            done += 1
            break
        if terminal == "code-requeued":
            log("CODE GATE: failed or expired attempt requeued; retry on a later run")
            break
        done += 1
    log("lane finished: %d jobs worked" % done)


if __name__ == "__main__":
    main()
