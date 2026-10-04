#!/usr/bin/env python3
"""Step 2+ (Indeed): walk the Indeed Apply flow step by step.

Flow per step:
  1. dump_step()            -> current step schema (indeed_dump)
  2. map.map_fields()       -> one strict-JSON field map (local model)
     + modal_common.apply_disclosure_policy() pins AI-disclosure = No
  3. fill.apply_fill(..., no_submit=True) -> deterministic fill + readback
  4. click Continue (or "Review your application") -> verify the step
     CHANGED (title differs) before continuing
  5. at the review step: dump + screenshot, STOP. The final "Submit your
     application" button is NEVER clicked unless BOTH:
       --submit was passed AND env ATS_APPROVE_SUBMIT=1 is set.

Hard line for Indeed: NO account creation. If the flow gates on an Indeed
sign-in / "Create your account" wall, the runner stops with status
"blocked-login" BEFORE any account form is touched. Documenting where the
line is: everything up to (and including) the login-wall detection is
validated --no-submit; nothing past it runs without the applicant's own Indeed
credentials.

Stops early on:
  - login/account wall -> status "blocked-login"
  - CAPTCHA -> status "blocked-captcha"
  - Perplexity URLs -> refused before any browser action
  - "Apply on company site" postings -> "not-indeed-apply"

Usage:
  indeed_fill.py --url <indeed job URL> --port 9226
      [--workdir /tmp/in-run1] [--submit] [--max-steps 8]

status: ready-for-submit | submitted | blocked-login | blocked-captcha |
        not-indeed-apply | failed
"""
import argparse
import json
import os
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from common import ensure_driver, cdp_ok, cdp, b64
from indeed_dump import dump_step, open_apply, flow_open, APPLY_ROOT_CANDIDATES
from map import map_fields
from fill import apply_fill
from modal_common import (
    guard_perplexity, submit_approved, require_no_auto_submit,
    apply_disclosure_policy, detect_captcha, emit_result, wait_settled)

HERE = os.path.dirname(os.path.abspath(__file__))

NAV_TEXTS = {
    "next": ("Continue",),
    "review": ("Review your application",),
}


def click_nav(port, nav, nav_text_only, which):
    sel = (nav or {}).get(which)
    if sel:
        cdp_ok(port, "fclick", b64(sel), timeout=60)
        return True
    if not (nav_text_only or {}).get(which):
        return False
    want = NAV_TEXTS[which][0]
    roots = json.dumps(APPLY_ROOT_CANDIDATES)
    r = cdp_ok(port, "evalb64", b64(
        "(() => { const roots = " + roots + ";"
        " const root = roots.map(s => document.querySelector(s))"
        "   .find(m => m && m.offsetParent) || document;"
        " const b = Array.from(root.querySelectorAll('button')).find(x =>"
        "   { try { return x.offsetParent && !x.disabled &&"
        f" x.innerText.trim() === {json.dumps(want)}; }} catch(e){{ return false; }} }});"
        " if (!b) return {error: 'not-found'};"
        " const r = b.getBoundingClientRect();"
        " return {x: r.x + r.width/2, y: r.y + r.height/2}; })()"),
        timeout=60)["result"]
    if r.get("error"):
        return False
    cdp_ok(port, "fclickel", b64(
        f"(() => ({{x: {r['x']}, y: {r['y']}, inView: true}})())"),
        timeout=60)
    return True


def run_indeed_apply(port, job_url, workdir, do_submit=False, max_steps=8):
    guard_perplexity(job_url)
    require_no_auto_submit(do_submit)
    ensure_driver()
    os.makedirs(workdir, exist_ok=True)
    result = {"url": job_url, "port": port, "status": "failed",
              "steps": [], "fields_filled": [], "fields_skipped": [],
              "notes": [], "screenshot": ""}

    cdp_ok(port, "reset")
    cdp_ok(port, "goto", b64(job_url), timeout=120)
    wait_settled(port)

    page_text = cdp(port, "text", "4000", timeout=60).get("text", "")
    if detect_captcha(page_text):
        result.update(status="blocked-captcha",
                      notes=["CAPTCHA/verification wall on job page; no bypass attempted"])
        return result

    try:
        open_apply(port)
    except RuntimeError as e:
        msg = str(e)
        result["notes"].append(f"open_apply: {msg}")
        result["status"] = ("not-indeed-apply" if "not an Indeed Apply" in msg
                            else "failed")
        return result
    result["notes"].append("Indeed Apply flow opened")

    facts = json.load(open(os.path.join(HERE, "facts.json")))
    prev_title = None
    for step_no in range(1, max_steps + 1):
        time.sleep(2)
        if not flow_open(port):
            result["notes"].append("apply flow closed unexpectedly at step "
                                   f"{step_no}")
            result["status"] = "failed"
            break
        schema = dump_step(port)
        if schema.get("login_wall"):
            result["notes"].append(
                f"Indeed sign-in/account wall at step {step_no}: stopping "
                "BEFORE any account creation (hard line). Fields visible "
                f"pre-wall: {len(schema['fields'])}.")
            result["status"] = "blocked-login"
            break
        title = schema["step"].get("title", "")
        if title == prev_title and step_no > 1:
            result["notes"].append(
                f"step did not advance after nav click (still '{title}'); stopping")
            result["status"] = "failed"
            break
        prev_title = title
        json.dump(schema, open(f"{workdir}/step{step_no}.json", "w"), indent=1)

        fmap = map_fields(schema, facts)
        fmap = apply_disclosure_policy(fmap, schema, facts)
        json.dump(fmap, open(f"{workdir}/step{step_no}.map.json", "w"), indent=1)

        fill_res = apply_fill(schema, fmap, port, True,
                               f"{workdir}/step{step_no}.png")
        result["steps"].append({
            "n": step_no,
            "title": title,
            "fields_total": fill_res["fields_total"],
            "filled": fill_res["fields_filled"],
            "mismatched": fill_res["fields_mismatched"],
            "skipped": fill_res["fields_skipped"],
        })
        result["fields_filled"].extend(fill_res["fields_filled"])
        result["fields_skipped"].extend(fill_res["fields_skipped"])
        result["notes"].append(
            f"step {step_no} ({title}): "
            f"{len(fill_res['fields_filled'])} filled+verified, "
            f"{len(fill_res['fields_mismatched'])} mismatched, "
            f"{len(fill_res['fields_skipped'])} skipped")
        if fill_res["fields_mismatched"]:
            result["notes"].append(
                f"READBACK MISMATCHES at step {step_no}: "
                f"{fill_res['fields_mismatched']} - stopping for review")
            result["status"] = "failed"
            break

        nav = schema.get("nav", {})
        if nav.get("submit"):
            shot = f"{workdir}/review.png"
            cdp_ok(port, "shot", shot, timeout=60)
            result["screenshot"] = shot
            if submit_approved(do_submit):
                cdp_ok(port, "fclick", b64(nav["submit"]), timeout=60)
                time.sleep(6)
                after = cdp(port, "text", "2000", timeout=60).get("text", "")
                result["status"] = "submitted"
                result["notes"].append(
                    "SUBMIT CLICKED under explicit approval. "
                    f"Post-submit text head: {after[:200]}")
            else:
                result["status"] = "ready-for-submit"
                result["notes"].append(
                    "REVIEW STEP REACHED. Submit NOT clicked: final submit "
                    "requires --submit plus ATS_APPROVE_SUBMIT=1 (supervised).")
            break
        nxt = "review" if (nav.get("review") or
                           (schema.get("nav_text_only", {}).get("review"))) else "next"
        if not click_nav(port, nav, schema.get("nav_text_only"), nxt):
            result["notes"].append(
                f"no continue/review nav at step {step_no}; stopping")
            result["status"] = "failed"
            break
        time.sleep(3)
    else:
        result["notes"].append(f"hit max_steps={max_steps}; stopping")
        result["status"] = "failed"

    return result


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--url", required=True)
    ap.add_argument("--port", type=int, default=9226)
    ap.add_argument("--workdir", default="")
    ap.add_argument("--submit", action="store_true",
                    help="ALLOW the final submit click (also needs "
                         "ATS_APPROVE_SUBMIT=1)")
    ap.add_argument("--max-steps", type=int, default=8)
    a = ap.parse_args()
    wd = a.workdir or f"/tmp/jobpilot/in-{a.port}-{int(time.time())}"
    result = run_indeed_apply(a.port, a.url, wd, a.submit, a.max_steps)
    emit_result(f"{wd}/result.json", result)
    print(json.dumps(result, indent=1))


if __name__ == "__main__":
    main()
