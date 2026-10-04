#!/usr/bin/env python3
"""Step 1: dump an application form's schema as JSON via the browser host pool.

Usage: schema_dump.py --url <application URL> --port <9226-9231> [--out schema.json]
Output: {"fields": [...], "submit": <selector|null>, "url": ..., "title": ...}
Each field: {key, tag, type, label, required, options, value, ...}
  key = stable CSS selector (id escaped, else [name=], [data-field-path], path)
  type in: text,email,tel,url,number,password,textarea,select,radio,checkbox,
           file,custom-select,yesno-button,location-ish variants
Read-only: never fills or submits.
"""
import argparse
import json
import sys
import os

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from common import ensure_driver, cdp, cdp_ok, b64

DUMP_JS = r"""(() => {
  const escQ = s => String(s).replace(/\\/g,'\\\\').replace(/"/g,'\\"');
  const labelFor = el => {
    const rawType = (el.getAttribute('type') || '').toLowerCase();
    const isOption = rawType === 'radio' || rawType === 'checkbox';
    // Ashby: the question title lives in the [data-field-path] container
    // and is more accurate than placeholders ("Start typing...", "Pick date...").
    // For grouped radio/checkbox options the per-option label comes from the
    // option's own <label> (handled below); the group title still feeds f.label.
    try {
      const scope = el.closest ? el.closest('[data-field-path]') : null;
      if (scope && !isOption) {
        const qt = scope.querySelector('.ashby-application-form-question-title');
        if (qt && qt.innerText.trim()) return qt.innerText.trim().slice(0,280);
      }
    } catch(e){}
    try {
      if (el.labels && el.labels.length) {
        const t = el.labels[0].innerText.trim();
        if (t) return t.slice(0,280);
      }
    } catch(e){}
    const al = el.getAttribute('aria-label');
    if (al) return al.trim().slice(0,280);
    const alb = el.getAttribute('aria-labelledby');
    if (alb) {
      const ref = document.getElementById(alb.split(/\s+/)[0]);
      if (ref && ref.innerText) return ref.innerText.trim().slice(0,280);
    }
    if (el.placeholder) return el.placeholder.trim().slice(0,280);
    if (el.title) return el.title.trim().slice(0,280);
    // fallback: label inside the closest field container
    try {
      const box = el.closest('div[class*="field"], fieldset, .question');
      if (box) {
        const lab = box.querySelector('label');
        if (lab && lab.innerText.trim()) return lab.innerText.trim().slice(0,280);
      }
    } catch(e){}
    return '';
  };
  const dfpScope = el => (el.closest ? el.closest('[data-field-path]') : null);
  const cleanLabel = s => String(s || '').replace(/✱/g, '').replace(/\s+/g, ' ').trim();
  // Group question text for radio/checkbox options: the option's own label
  // is often just "Yes"/"No"; the real question lives in an ancestor
  // container (fieldset legend, Ashby question title, or a flex row).
  // Strip the option labels so the question text stands alone.
  const groupQuestion = (el, dfp) => {
    const t = (el.getAttribute('type') || '').toLowerCase();
    if (t !== 'radio' && t !== 'checkbox') return '';
    let peers = [];
    try {
      // Prefer the data-field-path scope: some forms (Pinecone's custom
      // demographics) put the OPTION TEXT in the name attribute, so a
      // same-name lookup would mix unrelated questions.
      if (dfp) {
        const scope = document.querySelector(
          '[data-field-path="' + String(dfp).replace(/"/g, '\\"') + '"]');
        if (scope) peers = Array.from(scope.querySelectorAll(
          'input[type="' + t + '"]'));
      }
      if (!peers.length && el.name) peers = Array.from(
        document.querySelectorAll('input[type="' + t + '"][name="' +
          el.name.replace(/"/g, '\\"') + '"]'));
    } catch(e){}
    const optTxts = peers.map(p => {
      try { return (p.labels && p.labels[0] ? p.labels[0].innerText : '').trim(); }
      catch(e){ return ''; }
    }).filter(Boolean);
    let n = el.parentElement;
    for (let d = 0; d < 5 && n && n !== document.body; d++, n = n.parentElement) {
      let txt = (n.innerText || '').replace(/\s+/g, ' ').trim();
      for (const o of optTxts) { if (o) txt = txt.split(o).join(' '); }
      txt = txt.replace(/\b(Yes|No)\b/g, ' ').replace(/\s+/g, ' ').trim();
      if (txt.length >= 12 && txt.length <= 280) return txt;
    }
    return '';
  };
  // Lever: custom-question definitions are embedded as JSON in a hidden
  // input per card. Map "cards[<uuid>][field<N>]" -> question text.
  const cardQuestions = {};
  document.querySelectorAll('input[type=hidden]').forEach(h => {
    let j = null;
    try { j = JSON.parse(h.value); } catch(e){ return; }
    if (!j || !Array.isArray(j.fields)) return;
    let scope = h.parentElement, cardInputs = [];
    while (scope && scope !== document.body) {
      cardInputs = Array.from(scope.querySelectorAll(
        'input[name^="cards["], textarea[name^="cards["]'));
      if (cardInputs.length) break;
      scope = scope.parentElement;
    }
    if (!cardInputs.length) return;
    const m = (cardInputs[0].name || '').match(/^cards\[([^\]]+)\]/);
    if (!m) return;
    j.fields.forEach((f, i) => {
      if (f && f.text)
        cardQuestions['cards[' + m[1] + '][field' + i + ']'] =
          cleanLabel(f.text).slice(0, 280);
    });
  });
  const selectorFor = el => {
    const tag = el.tagName.toLowerCase();
    const scope = dfpScope(el);
    const q = scope ? scope.getAttribute('data-field-path') : '';
    // Ashby: stable question-scoped keys. data-field-entry-id carries a
    // per-load session UUID prefix, but data-field-path is stable per posting.
    if (q && /^(input|textarea|select|button)$/i.test(el.tagName)) {
      const t = (el.getAttribute('type') || '').toLowerCase();
      const opt = el.getAttribute('data-option');
      if (opt) return '[data-field-path="' + escQ(q) + '"] [data-option="' + escQ(opt) + '"]';
      const sel = tag + (t ? '[type="' + t + '"]' : '');
      const sibs = Array.from(scope.querySelectorAll(sel));
      const idx = sibs.indexOf(el);
      if (sibs.length > 1 && idx >= 0)
        return '[data-field-path="' + escQ(q) + '"] ' + sel + ':nth-of-type(' + (idx+1) + ')';
      return '[data-field-path="' + escQ(q) + '"] ' + sel;
    }
    // Ashby submit button: semantic class is stable across builds
    // (the hashed _submitButton_xxx class is not).
    if (/ashby-application-form-submit-button/.test(String(el.className || '')))
      return '.ashby-application-form-submit-button';
    if (el.id) return '#' + CSS.escape(el.id);
    if (el.name) return '[name="' + escQ(el.name) + '"]';
    const dfp = el.getAttribute('data-field-path');
    if (dfp) return '[data-field-path="' + escQ(dfp) + '"]';
    const tid = el.getAttribute('data-testid');
    if (tid) return '[data-testid="' + escQ(tid) + '"]';
    let path = [], n = el;
    while (n && n !== document.body && path.length < 7) {
      let idx = 1, sib = n;
      while ((sib = sib.previousElementSibling)) if (sib.tagName === n.tagName) idx++;
      path.unshift(n.tagName.toLowerCase() + ':nth-of-type(' + idx + ')');
      n = n.parentElement;
    }
    return path.join(' > ');
  };
  const fields = [];
  const seen = new Set();
  const push = f => {
    const k = f.key + '|' + f.label + '|' + (f.option_value || '') + '|' + (f.option_label || '');
    if (seen.has(k)) return; seen.add(k); fields.push(f);
  };
  document.querySelectorAll('input,select,textarea').forEach(el => {
    if (el.disabled) return;
    const rawType = (el.getAttribute('type') || el.tagName).toLowerCase();
    if (rawType === 'hidden' || rawType === 'submit' || rawType === 'button' || rawType === 'image') return;
    // Ashby "Autofill from resume" helper upload: id-less, nameless, labelless,
    // not required. The real resume field is separate; uploading here would
    // trigger autofill and clobber filled fields, so exclude it entirely.
    if (rawType === 'file' && !el.id && !el.name &&
        !(el.labels && el.labels.length) && !el.required) {
      let p = el, aux = false;
      for (let i = 0; i < 6 && p; i++) {
        p = p.parentElement;
        if (p && /autofill/i.test(p.innerText || '')) { aux = true; break; }
      }
      if (aux) return;
    }
    // skip Ashby yes/no mirror inputs (hidden checkbox behind the buttons;
    // interaction and verification go through button[data-option])
    if ((rawType === 'checkbox' || rawType === 'radio') &&
        el.getAttribute('tabindex') === '-1' &&
        el.closest && el.closest('[class*="yesno"]')) return;
    // skip internal widget inputs (react-select/intl-tel-input internals):
    // no id, no name, no data attrs, and no explicit type attribute.
    // Exception: role=combobox inputs (Ashby location autocomplete) are real.
    if (!el.id && !el.name && !el.getAttribute('data-field-path') &&
        !el.getAttribute('data-testid') && !el.getAttribute('type') &&
        el.getAttribute('role') !== 'combobox') return;
    // skip invisible (recaptcha etc.), but never skip file inputs
    if (rawType !== 'file') {
      try {
        if (!el.offsetParent && getComputedStyle(el).position !== 'fixed') return;
      } catch(e){}
    }
    const tag = el.tagName.toLowerCase();
    let type = rawType;
    if (tag === 'select') type = 'select';
    if (tag === 'textarea') type = 'textarea';
    // Greenhouse react-select comboboxes render as text inputs: treat as custom-select
    if (el.getAttribute('role') === 'combobox') type = 'custom-select';
    const f = {
      key: selectorFor(el), tag, type,
      label: cleanLabel((el.name && el.name.indexOf('cards[') === 0 &&
                         cardQuestions[el.name]) || labelFor(el)),
      required: !!(el.required || el.getAttribute('aria-required') === 'true'),
      options: [], value: String(el.value || '').slice(0,200),
    };
    if (type === 'select') {
      f.options = Array.from(el.options).map(o => (o.text || '').trim()).filter(Boolean).slice(0,60);
    }
    if (type === 'radio' || type === 'checkbox') {
      f.option_value = el.value || '';
      f.option_label = labelFor(el) || f.option_value;
      f.checked = !!el.checked;
      f.group = el.name || '';
      // Ashby: group options under one stable data-field-path; fill.py
      // resolves the clicked option by label at fill time.
      const scope = el.closest ? el.closest('[data-field-path]') : null;
      if (scope && scope.querySelectorAll(
            'input[type="' + rawType + '"]').length > 1) {
        f.dfp = scope.getAttribute('data-field-path');
        f.grouped = true;
      }
      // Group question text (the option label alone is often just
      // "Yes"/"No"); templates.py prefers this for group matching.
      f.question = groupQuestion(el, f.dfp || '');
      // Consent checkboxes often pair a short <label> ("I confirm I have read
      // the above.") with the actual attestation in the container text
      // ("I hereby certify..."). Include the container statement so the
      // guard regex sees the consent language.
      if (rawType === 'checkbox' && scope && !f.grouped) {
        const ctx = (scope.innerText || '').trim().replace(/\s+/g, ' ').slice(0, 280);
        if (ctx && ctx.length > f.option_label.length + 40) {
          f.label = (ctx.toLowerCase().indexOf(f.option_label.toLowerCase().slice(0, 24)) >= 0)
            ? ctx : ctx + ' || ' + f.option_label;
        }
      }
      // Lever: same-name checkbox/radio groups (e.g. "how did you hear").
      // Each option gets a unique pseudo-key; fill.py resolves the clicked
      // option by label text at fill time.
      if (!f.grouped && el.name) {
        let same = [];
        try {
          same = Array.from(document.querySelectorAll(
            'input[type="' + rawType + '"][name="' + escQ(el.name) + '"]'));
        } catch(e){}
        if (same.length > 1) {
          f.key = '[name="' + escQ(el.name) + '"][data-gidx="' +
                  same.indexOf(el) + '"]';
          f.grouped = true;
          f.group_scope = null;
          f.group_inputs = 'input[type="' + rawType + '"][name="' +
                           escQ(el.name) + '"]';
        }
      }
    }
    if (type === 'file') f.accept = el.getAttribute('accept') || '';
    push(f);
  });
  document.querySelectorAll('[role="combobox"]').forEach(el => {
    if (/^(input|select|textarea)$/i.test(el.tagName)) return;
    push({key: selectorFor(el), tag: el.tagName.toLowerCase(), type: 'custom-select',
      label: labelFor(el) || (el.innerText || '').trim().slice(0,80),
      required: el.getAttribute('aria-required') === 'true',
      options: [], value: (el.innerText || '').trim().slice(0,120)});
  });
  document.querySelectorAll('button[data-option]').forEach(el => {
    const opt = el.getAttribute('data-option');
    let q = '';
    const scope = el.closest('[data-field-path]') || el.parentElement;
    if (scope) {
      const leg = scope.querySelector('legend');
      q = (leg ? leg.innerText : scope.innerText).trim().slice(0,140);
    }
    push({key: selectorFor(el), tag: 'button', type: 'yesno-button',
      label: q || 'Yes/No question', required: false,
      options: [opt], value: (el.innerText || '').trim().slice(0,40)});
  });
  // Submit button. Captcha widgets (hCaptcha/reCAPTCHA) render their own
  // submit-ish buttons - exclude anything captcha-related, or the harness
  // would "submit" into the captcha instead of the form.
  const isCaptchaBtn = el => /captcha/i.test(
    (el.id || '') + ' ' + (el.className || '') + ' ' + (el.getAttribute('name') || ''));
  const visBtns = Array.from(document.querySelectorAll('button')).filter(b => {
    try { return b.offsetParent && !b.disabled && !isCaptchaBtn(b); } catch(e){ return false; }
  });
  let sub = visBtns.find(b =>
      /submit\s*(application|your application|my application)?/i.test((b.innerText||'').trim())) ||
    document.querySelector('button[type="submit"]:not([id*="captcha" i]):not([class*="captcha" i]),' +
                           'input[type="submit"]:not([id*="captcha" i]):not([class*="captcha" i])');
  if (sub && isCaptchaBtn(sub)) sub = null;
  // Fallback: any visible button whose text reads like a submit action.
  if (!sub) {
    sub = visBtns.find(b => /^(apply|continue)$/i.test((b.innerText||'').trim())) || null;
  }
  return {fields, submit: sub ? selectorFor(sub) : null,
          submit_label: sub ? (sub.innerText||'').trim().slice(0,60) : null,
          url: location.href, title: document.title};
})()"""


def dump_schema(url, port):
    ensure_driver()
    cdp_ok(port, "reset")
    cdp_ok(port, "goto", b64(url), timeout=120)
    r = cdp_ok(port, "evalb64", b64(DUMP_JS), timeout=120)
    schema = r["result"]
    scrape_custom_options(port, schema)
    return schema


OPTIONS_JS = r"""(() => {
  const seen = new Set(); const out = [];
  document.querySelectorAll('[role="option"], .select__option').forEach(el => {
    if (!el.offsetParent) return;  // only visible options (skip hidden phone list)
    const t = (el.innerText || '').trim();
    if (t && !seen.has(t) && t.length < 120) { seen.add(t); out.push(t); }
  });
  return out.slice(0, 60);
})()"""


MENU_OPEN_JS = r"""(() => {
  const opts = document.querySelectorAll('[role="option"], .select__option');
  for (const el of opts) { if (el.offsetParent) return true; }
  return false;
})()"""


def menu_open(port):
    try:
        return cdp_ok(port, "evalb64", b64(MENU_OPEN_JS), timeout=30)["result"]
    except Exception:
        return False


def close_menu(port):
    """Ensure any open dropdown menu is closed before the next scrape."""
    import time
    for _ in range(3):
        if not menu_open(port):
            return
        cdp(port, "fkey", "Escape")
        time.sleep(0.6)
    # fallback: click on neutral ground (top of page)
    cdp(port, "fclickel", b64(
        "(() => ({x: 10, y: 10, inView: true}))()"))
    time.sleep(0.6)


def scrape_custom_options(port, schema):
    """Open each custom-select once to harvest its option list.

    Clicks the control container's center (not the 2-4px input).
    Only visible options are counted; menus are verified closed between fields.
    """
    import time
    from common import container_click_js
    for f in schema["fields"]:
        if f["type"] != "custom-select" or f.get("options"):
            continue
        try:
            close_menu(port)
            r = cdp(port, "fclickel", b64(container_click_js(f["key"])), timeout=60)
            if not r.get("ok"):
                print(f"  [scrape] {f['key']}: click failed", file=sys.stderr)
                continue
            time.sleep(1.2)
            if not menu_open(port):
                print(f"  [scrape] {f['key']}: menu did not open", file=sys.stderr)
                continue
            o = cdp_ok(port, "evalb64", b64(OPTIONS_JS), timeout=60)
            f["options"] = o["result"] or []
            close_menu(port)
        except Exception as e:
            print(f"  [scrape] {f['key']}: {e}", file=sys.stderr)
            continue


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--url", required=True)
    ap.add_argument("--port", type=int, default=9226)
    ap.add_argument("--out", default="")
    a = ap.parse_args()
    schema = dump_schema(a.url, a.port)
    schema["ats_hint"] = ("greenhouse" if "greenhouse" in a.url else
                          "ashby" if "ashby" in a.url else
                          "lever" if "lever" in a.url else "unknown")
    s = json.dumps(schema, indent=1)
    if a.out:
        open(a.out, "w").write(s)
        print(f"wrote {a.out} ({len(schema['fields'])} fields)")
    else:
        print(s)


if __name__ == "__main__":
    main()
