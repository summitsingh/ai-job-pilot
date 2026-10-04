#!/usr/bin/env python3
"""Test hard_patterns.py against live Greenhouse forms. Never submits.

Usage: python3 test_hard_patterns.py --port 9226
Runs fill_react_select on 2+ demographic dropdowns, fill_grouped_checkboxes
on the Twilio 'How did you hear' group, upload_resume on both forms.
Prints PASS/FAIL per helper. --no-submit always: no submit button is
ever clicked.
"""
import argparse
import json
import os
import re
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from common import ensure_driver, cdp, cdp_ok, b64, container_click_js
from hard_patterns import (fill_react_select, fill_grouped_checkboxes,
                            upload_resume)

TWILIO = "https://job-boards.greenhouse.io/twilio/jobs/8065043"
LAUNCHDARKLY = "https://job-boards.greenhouse.io/launchdarkly/jobs/7920039003"
# Fallback postings if a primary is closed (report the substitution).
FALLBACKS = [
    "https://job-boards.greenhouse.io/duolingo/jobs/7674994002",
    "https://job-boards.greenhouse.io/anthropic/jobs/4051565008",
]
RESUME = "/tmp/sample_resume.pdf"
CLOSED_MARKERS = ["no longer available", "no longer accepting",
                  "job posting is no longer", "page not found", "job not found",
                  "couldn't find that"]

DEMO_KEYWORDS = ["gender", "race", "ethnicity", "veteran", "hispanic",
                 "disability"]


def escape_css_id(id_):
    """Build the CSS-escaped '#...' key for an id (digit-leading ids need it)."""
    if id_ and id_[0].isdigit():
        return "#\\" + format(ord(id_[0]), "x") + " " + id_[1:]
    return "#" + id_


def goto_fresh(port, url):
    cdp_ok(port, "reset")
    cdp_ok(port, "goto", b64(url), timeout=120)
    time.sleep(3)


def is_live(port):
    t = cdp(port, "text", timeout=60).get("text", "").lower()
    return not any(m in t for m in CLOSED_MARKERS)


def ensure_live(port, primary, label):
    """Goto primary; if closed, try fallbacks. Returns (url, substituted)."""
    goto_fresh(port, primary)
    if is_live(port):
        return primary, False
    for fb in FALLBACKS:
        goto_fresh(port, fb)
        if is_live(port):
            print(f"  SUBSTITUTION: {label} posting closed, using {fb}")
            return fb, True
    raise RuntimeError(f"no live posting found for {label}")


DISCOVER_SELECTS_JS = """(() => {
  const out = [];
  document.querySelectorAll('.select__control').forEach(ctl => {
    const input = ctl.querySelector('input');
    let label = '', n = ctl.parentElement, d = 0;
    while (n && d < 6 && !label) {
      const labEl = n.querySelector(':scope > label');
      if (labEl && labEl.innerText.trim()) { label = labEl.innerText.trim(); break; }
      n = n.parentElement; d++;
    }
    if (!label) {
      let m = ctl.parentElement, e = 0;
      while (m && e < 6 && !label) {
        const t = (m.innerText || '').trim();
        if (t && t.length > ((ctl.innerText||'').length) && t.length <= 160)
          label = t.split('\\n')[0].trim();
        m = m.parentElement; e++;
      }
    }
    const root = ctl.closest('[class*=select--]') || ctl;
    out.push({id: input ? (input.id || '') : '',
              label: label,
              multi: ((root.className || '').toString().includes('is-multi')) ||
                     !!ctl.querySelector('.select__multi-value')});
  });
  return out;
})()"""

DISCOVER_GROUPS_JS = """(() => {
  const seen = {};
  document.querySelectorAll('input[type="checkbox"][name]').forEach(el => {
    seen[el.name] = (seen[el.name] || 0) + 1;
  });
  const out = [];
  for (const name of Object.keys(seen)) {
    const first = document.querySelector('input[type="checkbox"][name="' + name + '"]');
    let label = '', n = first.parentElement, d = 0;
    while (n && d < 6 && !label) {
      const t = (n.innerText || '').trim();
      if (t && t.length <= 200) label = t.split('\\n')[0].trim();
      n = n.parentElement; d++;
    }
    const labels = Array.from(
      document.querySelectorAll('input[type="checkbox"][name="' + name + '"]')).map(el => {
        try { if (el.labels && el.labels[0]) return el.labels[0].innerText.trim(); }
        catch(e) {}
        const l = el.closest('label');
        return l ? l.innerText.trim() : '';
      });
    out.push({name: name, count: seen[name], label: label, options: labels});
  }
  return out;
})()"""

MENU_OPTIONS_JS = ("(() => Array.from(document.querySelectorAll(" +
                   "'.select__menu [role=option], " +
                   "[class*=select__menu] [role=option]'))" +
                   ".filter(o => o.offsetParent !== null)" +
                   ".map(o => o.innerText.trim()).filter(t => t))()")


def discover_selects(port):
    return cdp_ok(port, "evalb64", b64(DISCOVER_SELECTS_JS), timeout=60)["result"] or []


def menu_options(port, key):
    r = cdp(port, "fclickel", b64(container_click_js(key)))
    if not r.get("ok"):
        return []
    time.sleep(1.2)
    try:
        opts = cdp_ok(port, "evalb64", b64(MENU_OPTIONS_JS), timeout=60)["result"] or []
    except Exception:
        opts = []
    cdp(port, "fkey", "Escape")
    time.sleep(0.5)
    return opts


def pick_options(opts, multi):
    """Choose option text(s): prefer 'decline', else first options."""
    if not opts:
        return []
    decl = [o for o in opts if "decline" in o.lower()]
    if decl:
        return [decl[0]]
    if multi:
        return opts[:2]
    return [opts[0]]


def discover_groups(port):
    return cdp_ok(port, "evalb64", b64(DISCOVER_GROUPS_JS), timeout=60)["result"] or []


def run_form(port, label, primary):
    results = []
    url, substituted = ensure_live(port, primary, label)
    results.append(("live", url, "PASS (substituted)" if substituted else "PASS"))

    # --- 1) react-select dropdowns (demographic) ---
    selects = [s for s in discover_selects(port) if s.get("id")]
    demo = [s for s in selects
            if any(k in (s.get("label") or "").lower() for k in DEMO_KEYWORDS)]
    print(f"  found {len(selects)} react-selects, {len(demo)} demographic: "
          + json.dumps([s["label"] for s in demo])[:200])
    picked = 0
    for s in demo:
        key = escape_css_id(s["id"])
        opts = menu_options(port, key)
        want = pick_options(opts, s.get("multi"))
        if not want:
            results.append((f"react-select '{s['label'][:40]}'", key,
                            "FAIL: no options enumerated"))
            continue
        try:
            fill_react_select(port, key, want if len(want) > 1 else want[0])
            results.append((f"react-select '{s['label'][:40]}'", key,
                            f"PASS (picked {want})"))
            picked += 1
        except Exception as e:
            results.append((f"react-select '{s['label'][:40]}'", key,
                            f"FAIL: {e}"))
    if picked == 0 and not demo:
        results.append(("react-select", "-", "SKIP: no demographic dropdowns found"))

    # --- 2) grouped checkboxes ---
    # The group label walk-up can land on an option container, so identify
    # the "how did you hear" group by its source-like options instead of
    # relying on the label alone.
    SOURCE_HINTS = ["linkedin", "indeed", "glassdoor", "referral",
                    "careers website", "job board", "twitter"]
    groups = discover_groups(port)
    hear = [g for g in groups if g.get("count", 0) > 1 and any(
        any(h in (o or "").lower() for h in SOURCE_HINTS)
        for o in (g.get("options") or []))]
    if not hear:
        hear = [g for g in groups
                if re.search(r"how did you hear", g.get("label") or "", re.I)]
    if hear:
        g = hear[0]
        val = next((o for o in g["options"]
                    if o and "linkedin" in o.lower()), None)
        if not val and g["options"]:
            val = next((o for o in g["options"] if o), None)
        if val:
            try:
                fill_grouped_checkboxes(port, g["name"], val)
                results.append(("grouped-checkboxes", g["name"],
                                f"PASS (checked '{val}')"))
            except Exception as e:
                results.append(("grouped-checkboxes", g["name"], f"FAIL: {e}"))
        else:
            results.append(("grouped-checkboxes", g["name"],
                            "SKIP: no option labels found"))
    else:
        results.append(("grouped-checkboxes", "-",
                        "SKIP: no 'How did you hear' group found"))

    # --- 3) resume upload ---
    try:
        sig = upload_resume(port, "#resume", RESUME)
        results.append(("upload-resume", "#resume", f"PASS (signal={sig})"))
    except Exception as e:
        results.append(("upload-resume", "#resume", f"FAIL: {e}"))

    return results


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--port", type=int, default=9226)
    a = ap.parse_args()
    ensure_driver()
    all_results = []
    for label, url in [("twilio", TWILIO), ("launchdarkly", LAUNCHDARKLY)]:
        print(f"== {label}: {url}")
        try:
            all_results += run_form(a.port, label, url)
        except Exception as e:
            all_results.append((label, url, f"FAIL: {e}"))
    print("\n==== RESULTS ====")
    npass = sum(1 for r in all_results if r[2].startswith("PASS"))
    for name, key, status in all_results:
        print(f"{status.split(':')[0]:6} | {name} | {key} | {status}")
    print(f"\n{npass}/{len(all_results)} passed")


if __name__ == "__main__":
    main()
