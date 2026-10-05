#!/usr/bin/env python3
"""Step 3: deterministically apply a field map to the loaded form.

Usage: fill.py --schema schema.json --map map.json --port 9226 [--no-submit]
  [--shot /tmp/jobpilot/fill.png] [--out fill-result.json]

Strategy (all React-proof, from proven sweeps):
- text-like "fill" actions: ONE batched JS pass (native value setter +
  input/change events), zero per-field round trips.
- "select"/"custom-select": scroll, real click, type exact option, Enter.
- "location": scroll, real click, type city, wait, ArrowDown, Enter.
- "click" (radio/checkbox/yesno-button): scroll + real click.
- "upload": CDP setFileInputFiles with the resume on the browser host.
Then read back every field, diff vs expected, screenshot.
--no-submit: fill + verify only, never clicks submit (safe testing).
"""
import argparse
import json
import os
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from common import (ensure_driver, cdp_ok, cdp, b64, host_ssh,
                    host_test_file, is_direct)

TEXT_TYPES = {"text", "email", "tel", "url", "number", "password", "textarea"}


def js_decode_b64(var="B64"):
    return (f'JSON.parse(new TextDecoder().decode(Uint8Array.from(atob("{var}"), '
            'c => c.charCodeAt(0))))')


def batch_fill_js(items_b64):
    return ("(() => { const items=" + js_decode_b64("B64").replace("B64", items_b64) + ";" + r"""
const out=[];
for (const it of items) {
  const el=document.querySelector(it.key);
  if(!el){out.push({key:it.key,ok:false,why:'not-found'});continue;}
  try{
    el.scrollIntoView({block:'center',behavior:'instant'});
    const proto=el.tagName==='TEXTAREA'?HTMLTextAreaElement.prototype:HTMLInputElement.prototype;
    const desc=Object.getOwnPropertyDescriptor(proto,'value');
    if(desc&&desc.set)desc.set.call(el,it.value);else el.value=it.value;
    el.dispatchEvent(new Event('input',{bubbles:true}));
    el.dispatchEvent(new Event('change',{bubbles:true}));
    out.push({key:it.key,ok:true});
  }catch(e){out.push({key:it.key,ok:false,why:String(e).slice(0,120)});}
}
return out; })()""")


def readback_js(items_b64):
    # items: [{key, type, grouped?, gscope?, ginputs?, option_label?}]
    return ("(() => { const items=" + js_decode_b64("B64").replace("B64", items_b64) + ";" + r"""
const out=[];
const lab = s => (s||'').trim().toLowerCase();
const labelOf = el => { try { if (el.labels && el.labels[0]) { const lt = el.labels[0].innerText.trim(); if (lt) return lt; } } catch(e){}
  const l = el.closest('label'); if (l && l.innerText.trim()) return l.innerText;
  let n = el.parentElement, d = 0;
  while (n && d < 4) { const t = (n.innerText||'').trim(); if (t && t.length <= 80) return t; n = n.parentElement; d++; }
  return ''; };
for(const it of items){
  if (it.grouped) {
    // grouped option: resolve by label inside the scope
    const scope = it.gscope ? document.querySelector(it.gscope) : document;
    let val = 'not-found';
    if (scope) {
      const inputs = Array.from(scope.querySelectorAll(it.ginputs));
      const want = lab(it.option_label);
      const el = inputs.find(i => { const t = lab(labelOf(i));
        return t && (t === want || t.includes(want) || want.includes(t)); });
      if (el) val = el.checked ? 'checked' : 'unchecked';
    }
    out.push({key:it.key, found: val!=='not-found', value: val});
    continue;
  }
  const el=document.querySelector(it.key);
  if(!el){out.push({key:it.key,found:false});continue;}
  const tag=el.tagName.toLowerCase();
  const type=(el.getAttribute('type')||tag).toLowerCase();
  let val='';
  if(it.type==='custom-select'){
    // Greenhouse react-select: value lives in .select__single-value;
    // Ashby autocomplete: the combobox IS the input, value is el.value.
    const ctl=el.closest('.select__control');
    const sv=ctl?ctl.querySelector('.select__single-value'):null;
    val=(sv&&sv.innerText.trim())?sv.innerText.trim().slice(0,200)
        :((el.value!==undefined?el.value:'')+'').trim().slice(0,200);
  }
  else if(type==='checkbox'||type==='radio')val=el.checked?'checked':'unchecked';
  else if(tag==='select'){const o=el.options[el.selectedIndex];val=o?o.text.trim():'';}
  else if(type==='file')val=el.files&&el.files.length?('files:'+el.files.length):'no-file';
  else val=(el.value!==undefined?el.value:(el.innerText||'')).trim().slice(0,200);
  const ap=el.getAttribute('aria-pressed');
  out.push({key:it.key,found:true,value:val,ariaPressed:ap,cls:(el.className||'').toString().slice(0,80)});
}
return out; })()""")


def scroll_to(port, key):
    cdp_ok(port, "evalb64", b64(
        f'(()=>{{const el=document.querySelector({json.dumps(key)});'
        f'if(el){{el.scrollIntoView({{block:"center"}});return 1;}}return 0;}})()'))


def group_scope_inputs(field):
    """Resolve (scope_selector, inputs_selector) for a grouped option field.

    Ashby: stable [data-field-path] container. Lever: same-name inputs across
    the document (group_scope None)."""
    if field.get("dfp"):
        return ('[data-field-path="%s"]' % field["dfp"],
                'input[type="%s"]' % field.get("type", "radio"))
    return (field.get("group_scope"), field.get("group_inputs"))


def _group_name_attr(port, f):
    # Derive the shared name attribute for a grouped checkbox field by
    # querying the first matching input in the DOM at fill time.
    scope_sel, inputs_sel = group_scope_inputs(f)
    scope_expr = ("document.querySelector(" + json.dumps(scope_sel) + ")"
                  if scope_sel else "document")
    js = ("(() => { const scope = " + scope_expr + "; if (!scope) return null; " +
          "const el = scope.querySelector(" + json.dumps(inputs_sel) + "); " +
          "return el ? (el.name || null) : null; })()")
    try:
        return cdp_ok(port, "evalb64", b64(js), timeout=60)["result"]
    except Exception:
        return None


def group_option_click_js(scope_sel, inputs_sel, label):
    """JS returning the viewport center of the grouped option (radio/checkbox)
    whose associated label matches `label`. Ashby group ids carry a per-load
    session UUID and Lever options share one name, so resolution is by label
    text at fill time, never by dump-time index."""
    scope_expr = ("document.querySelector(" + json.dumps(scope_sel) + ")"
                  if scope_sel else "document")
    return ("(() => { const scope = " + scope_expr +
            "; if (!scope) return {error:'no-scope'}; " +
            "const inputs = Array.from(scope.querySelectorAll(" +
            json.dumps(inputs_sel) + ")); " +
            "const lab = s => (s||'').trim().toLowerCase(); " +
            "const want = lab(" + json.dumps(label) + "); " +
            "const labelOf = el => { try { if (el.labels && el.labels[0]) { " +
            "const lt = el.labels[0].innerText.trim(); if (lt) return lt; } } catch(e){} " +
            "const l = el.closest('label'); if (l && l.innerText.trim()) return l.innerText; " +
            "let n = el.parentElement, d = 0; " +
            "while (n && d < 4) { const t = (n.innerText||'').trim(); " +
            "if (t && t.length <= 80) return t; n = n.parentElement; d++; } " +
            "return ''; }; " +
            "let el = inputs.find(i => { const t = lab(labelOf(i)); " +
            "return t && (t === want || t.includes(want) || want.includes(t)); }); " +
            "if (!el) return {error:'no-label-match', n: inputs.length}; " +
            "el.scrollIntoView({block:'center', behavior:'instant'}); " +
            "void el.offsetHeight; " +
            "const r = el.getBoundingClientRect(); " +
            "const inView = r.y >= 0 && r.y <= window.innerHeight; " +
            "return {x: r.x + r.width/2, y: r.y + r.height/2, inView: inView}; })()")


def group_option_native_click_js(scope_sel, inputs_sel, label):
    """One-shot JS: resolve the grouped option by label, click its <label>
    natively (label.click()), verify the input is checked.

    Used for opacity-0 custom checkboxes/radios (Pinecone's demographic
    groups) where coordinate clicks do not toggle the input. Returns
    {ok, checked, already}."""
    scope_expr = ("document.querySelector(" + json.dumps(scope_sel) + ")"
                  if scope_sel else "document")
    return ("(() => { const scope = " + scope_expr +
            "; if (!scope) return {ok:false, why:'no-scope'}; " +
            "const inputs = Array.from(scope.querySelectorAll(" +
            json.dumps(inputs_sel) + ")); " +
            "const lab = s => (s||'').trim().toLowerCase(); " +
            "const want = lab(" + json.dumps(label) + "); " +
            "const labelOf = el => { try { if (el.labels && el.labels[0]) { " +
            "const lt = el.labels[0].innerText.trim(); if (lt) return lt; } } catch(e){} " +
            "const l = el.closest('label'); if (l && l.innerText.trim()) return l.innerText; " +
            "let n = el.parentElement, d = 0; " +
            "while (n && d < 4) { const t = (n.innerText||'').trim(); " +
            "if (t && t.length <= 80) return t; n = n.parentElement; d++; } " +
            "return ''; }; " +
            "let el = inputs.find(i => { const t = lab(labelOf(i)); " +
            "return t && (t === want || t.includes(want) || want.includes(t)); }); " +
            "if (!el) return {ok:false, why:'no-label-match', n: inputs.length}; " +
            "if (el.checked) return {ok:true, already:true}; " +
            "const lbl = (el.labels && el.labels[0]) || el.closest('label'); " +
            "if (!lbl) return {ok:false, why:'no-label-el'}; " +
            "lbl.scrollIntoView({block:'center', behavior:'instant'}); " +
            "lbl.click(); " +
            "return {ok:true, checked: !!el.checked}; })()")
    """JS returning the viewport center of the grouped option (radio/checkbox)
    whose associated label matches `label`. Ashby group ids carry a per-load
    session UUID and Lever options share one name, so resolution is by label
    text at fill time, never by dump-time index."""
    scope_expr = ("document.querySelector(" + json.dumps(scope_sel) + ")"
                  if scope_sel else "document")
    return ("(() => { const scope = " + scope_expr +
            "; if (!scope) return {error:'no-scope'}; " +
            "const inputs = Array.from(scope.querySelectorAll(" +
            json.dumps(inputs_sel) + ")); " +
            "const lab = s => (s||'').trim().toLowerCase(); " +
            "const want = lab(" + json.dumps(label) + "); " +
            "const labelOf = el => { try { if (el.labels && el.labels[0]) { " +
            "const lt = el.labels[0].innerText.trim(); if (lt) return lt; } } catch(e){} " +
            "const l = el.closest('label'); if (l && l.innerText.trim()) return l.innerText; " +
            "let n = el.parentElement, d = 0; " +
            "while (n && d < 4) { const t = (n.innerText||'').trim(); " +
            "if (t && t.length <= 80) return t; n = n.parentElement; d++; } " +
            "return ''; }; " +
            "let el = inputs.find(i => { const t = lab(labelOf(i)); " +
            "return t && (t === want || t.includes(want) || want.includes(t)); }); " +
            "if (!el) return {error:'no-label-match', n: inputs.length}; " +
            # Prefer the associated <label> as the click target: custom
            # checkbox/radio inputs are often opacity-0 (Pinecone's
            # demographic checkboxes), so the input's own center is not
            # clickable; the label is the visible toggle.
            "let target = el; " +
            "try { const lbl = (el.labels && el.labels[0]) || el.closest('label'); " +
            "if (lbl) target = lbl; } catch(e){} " +
            "target.scrollIntoView({block:'center', behavior:'instant'}); " +
            "void target.offsetHeight; " +
            "const r = target.getBoundingClientRect(); " +
            "const inView = r.y >= 0 && r.y <= window.innerHeight; " +
            "return {x: r.x + r.width/2, y: r.y + r.height/2, inView: inView}; })()")


def resolve_submit_key(port, schema):
    """Re-discover the submit button if the dump-time key went stale.

    Ashby's submit carries the stable class .ashby-application-form-submit-button;
    fall back to a visible button whose text reads like a submit action."""
    sub = schema.get("submit")
    if sub:
        try:
            r = cdp(port, "evalb64", b64(
                "(() => { const el = document.querySelector(" + json.dumps(sub) +
                "); if (!el || !el.offsetParent || el.disabled) return null; " +
                "return 'ok'; })()"), timeout=60)
            if r.get("result") == "ok":
                return sub
        except Exception:
            pass
    for cand in (".ashby-application-form-submit-button",
                 "button[type=submit]", "input[type=submit]"):
        try:
            r = cdp(port, "evalb64", b64(
                "(() => { const el = document.querySelector(" + json.dumps(cand) +
                "); return (el && el.offsetParent && !el.disabled) ? 'ok' : null; })()"),
                timeout=60)
            if r.get("result") == "ok":
                return cand
        except Exception:
            pass
    # last resort: visible button whose text matches the dump-time label
    label = (schema.get("submit_label") or "submit").strip().split()[0]
    try:
        r = cdp(port, "evalb64", b64(
            "(() => { const w = " + json.dumps(label.lower()) + ";" +
            " const b = Array.from(document.querySelectorAll('button'))" +
            ".find(x => x.offsetParent && !x.disabled && " +
            "(x.innerText||'').toLowerCase().includes(w));" +
            " return b ? 'ok' : null; })()"), timeout=60)
        if r.get("result") == "ok":
            return "__submit_by_label__"
    except Exception:
        pass
    return None


def resolve_upload_key(port, key):
    """The dump-time file-input key can go stale if the ATS re-renders.
    If the key is missing, re-discover: prefer a file input whose
    id/label context mentions resume/CV, else the first file input."""
    js = ("(() => { if (document.querySelector(" + json.dumps(key) +
          ") ) return {key: " + json.dumps(key) + "}; " +
          "const cands=[]; " +
          "document.querySelectorAll('input[type=file]').forEach(el=>{" +
          " const lab=((el.labels&&el.labels[0]?el.labels[0].innerText:'')+' '+el.id+' '+el.name).toLowerCase();" +
          " cands.push({id: el.id, resume: /resume|\\bcv\\b/.test(lab)}); });" +
          " const r=cands.find(c=>c.resume)||cands[0];" +
          " return r&&r.id ? {key: '#'+r.id} : {key: null}; })()")
    try:
        r = cdp_ok(port, "evalb64", b64(js), timeout=60)["result"]
        return r.get("key") or key
    except Exception:
        return key


def do_native_select(port, key, value):
    """Deterministic native <select> handling: match the option whose text
    equals (or best contains) the wanted value, set selectedIndex, fire
    input/change. Typing into a native select is unreliable; this is not."""
    js = ("(() => { const el = document.querySelector(" + json.dumps(key) + ");"
          " if (!el || el.tagName !== 'SELECT') return {ok:false, why:'not-select'};"
          " const opts = Array.from(el.options);"
          " const want = " + json.dumps(value) + ".toLowerCase();"
          " let idx = opts.findIndex(o => o.text.trim().toLowerCase() === want);"
          " if (idx < 0) idx = opts.findIndex(o => { const t=o.text.trim().toLowerCase();"
          " return t && (t.includes(want) || want.includes(t)); });"
          " if (idx < 0) return {ok:false, why:'no-match',"
          " options: opts.map(o=>o.text.trim()).slice(0,20)};"
          " el.scrollIntoView({block:'center', behavior:'instant'});"
          " el.selectedIndex = idx;"
          " el.dispatchEvent(new Event('input',{bubbles:true}));"
          " el.dispatchEvent(new Event('change',{bubbles:true}));"
          " return {ok:true, picked: opts[idx].text.trim()}; })()")
    try:
        r = cdp_ok(port, "evalb64", b64(js), timeout=60)["result"]
    except Exception as e:
        return False, str(e)[:120]
    if r.get("ok"):
        return True, ""
    return False, r.get("why", "") + " options=" + json.dumps(r.get("options", []))[:160]


def do_select(port, key, value, field_type="select", is_location=False):
    from common import container_click_js
    if field_type == "select":
        return do_native_select(port, key, value)
    if field_type == "custom-select" and not is_location:
        # Greenhouse react-select: use the deterministic helper (container
        # click + menu pick + self-verify). Detect by .select__control
        # container; Ashby autocomplete comboboxes (no such container) and
        # location actions keep the click + type + Enter path below.
        try:
            kind = cdp_ok(port, "evalb64", b64(
                '(()=>{const el=document.querySelector(' + json.dumps(key) +
                ');return (el && el.closest(".select__control"))?"rs":"cb";})()'),
                timeout=60)["result"]
        except Exception:
            kind = "cb"
        if kind == "rs":
            from hard_patterns import fill_react_select
            try:
                fill_react_select(port, key, value)
                return True, ""
            except RuntimeError as e:
                return False, str(e)[:200]
        # Non-react custom combobox: two flavors.
        # (a) Indeed-style div[role=combobox] with a [role=option] menu:
        #     click to open, pick the option, verify.
        # (b) Ashby autocomplete combobox: click + type + Enter.
        # Try (a) first; if no option menu opens, fall back to (b).
        from hard_patterns import fill_generic_combobox
        try:
            fill_generic_combobox(port, key, value)
            return True, ""
        except RuntimeError as e:
            if "menu did not open" not in str(e):
                return False, str(e)[:200]
        # (b) Ashby combobox: fall through to click + type + Enter
        r = cdp(port, "fclickel", b64(container_click_js(key)))
    else:
        scroll_to(port, key)
        # location autocomplete: clear first so a re-run/partial value
        # doesn't get appended to (ftype uses Input.insertText)
        if is_location:
            cdp_ok(port, "evalb64", b64(
                '(()=>{const el=document.querySelector(' + json.dumps(key) +
                ');if(!el)return 0;el.focus();'
                'const d=Object.getOwnPropertyDescriptor(' +
                'Object.getPrototypeOf(el),"value");'
                'if(d&&d.set)d.set.call(el,"");else el.value="";'
                'el.dispatchEvent(new Event("input",{bubbles:true}));'
                'return 1;})()'), timeout=60)
        r = cdp(port, "fclick", b64(key))
    if not r.get("ok"):
        return False, r.get("error")
    time.sleep(0.8)
    r = cdp(port, "ftype", b64(key), b64(value))
    if not r.get("ok"):
        return False, r.get("error")
    time.sleep(2.2 if is_location else 1.5)
    if is_location:
        cdp(port, "fkey", "ArrowDown")
        time.sleep(0.6)
    cdp(port, "fkey", "Enter")
    time.sleep(1.0)
    if is_location:
        # Location autocompletes resolve asynchronously (Lever rewrites the
        # input to its canonical form, e.g. "Austin, TX, USA", after the
        # suggestion is picked). Poll for a settled non-empty value so the
        # readback below sees the final state, not the mid-flight one.
        deadline = time.time() + 12
        while time.time() < deadline:
            try:
                v = cdp_ok(port, "evalb64", b64(
                    '(()=>{const el=document.querySelector(' +
                    json.dumps(key) + ');return el ? el.value : null;})()'),
                    timeout=30)["result"]
            except Exception:
                v = None
            if v:
                break
            time.sleep(1.0)
    return True, ""


def matches(expected, actual):
    e, a = (expected or "").strip().lower(), (actual or "").strip().lower()
    if not e:
        return True
    if not a:
        return False
    # phone widgets reformat digits: compare digit-stripped too
    ed, ad = "".join(ch for ch in e if ch.isdigit()), "".join(ch for ch in a if ch.isdigit())
    if ed and ad and (ed == ad or ed in ad or ad in ed):
        return True
    return e == a or e in a or a in e


def apply_fill(schema, fmap, port, no_submit, shot_path):
    ensure_driver()
    by_key = {f["key"]: f for f in schema["fields"]}
    actions = [m for m in fmap["map"] if m["action"] != "skip"]
    per_field = []
    notes = []

    # 1) batched text fills
    text_items = [{"key": m["field"], "value": m["value"]} for m in actions
                  if m["action"] == "fill" and
                  by_key.get(m["field"], {}).get("type") in TEXT_TYPES]
    if text_items:
        r = cdp_ok(port, "evalb64", b64(batch_fill_js(b64(json.dumps(text_items)))),
                   timeout=120)
        for it, res in zip(text_items, r["result"]):
            # res is {key, ok, why} from batch_fill_js
            per_field.append({"field": it["key"], "action": "fill",
                              "expected": it["value"],
                              "applied": bool(res.get("ok")),
                              "note": res.get("why", "")})

    # 2) selects + locations (per-field: click, type, Enter)
    for m in actions:
        f = by_key.get(m["field"], {})
        if m["action"] == "select" or (m["action"] == "fill" and
                                       f.get("type") not in TEXT_TYPES):
            ok, err = do_select(port, m["field"], m["value"],
                                field_type=f.get("type", "select"),
                                is_location=(m["action"] == "location" or
                                             "location" in m["field"].lower()))
            per_field.append({"field": m["field"], "action": m["action"],
                              "expected": m["value"], "applied": ok,
                              "note": err})
        elif m["action"] == "location":
            ok, err = do_select(port, m["field"], m["value"],
                                field_type=f.get("type", "select"),
                                is_location=True)
            per_field.append({"field": m["field"], "action": "location",
                              "expected": m["value"], "applied": ok,
                              "note": err})

    # 3) clicks (radio / checkbox / yes-no buttons)
    for m in actions:
        if m["action"] == "click":
            f = by_key.get(m["field"], {})
            if f.get("grouped"):
                if f.get("type") == "checkbox":
                    # Grouped checkboxes: prefer the dfp scope when present.
                    # Some forms (Pinecone's Ashby demographics) put the OPTION
                    # TEXT in the name attribute, so the shared-name helper
                    # would mix unrelated questions; the dfp scope is exact.
                    # Native label.click() toggles opacity-0 custom inputs
                    # that coordinate clicks miss.
                    if f.get("dfp"):
                        scope_sel, inputs_sel = group_scope_inputs(f)
                        r = cdp_ok(port, "evalb64",
                                   b64(group_option_native_click_js(
                                       scope_sel, inputs_sel,
                                       f.get("option_label", "") or m["value"])),
                                   timeout=60)["result"]
                        ok2b = bool(r.get("ok") and (r.get("checked") or
                                                     r.get("already")))
                        per_field.append({"field": m["field"],
                                          "action": "click",
                                          "expected": m["value"],
                                          "applied": ok2b,
                                          "note": r.get("why", "")})
                    else:
                        # grouped checkboxes: deterministic helper (label-text
                        # resolution, native label click, self-verify). Falls
                        # back to the label-resolution path if the shared name
                        # attribute cannot be resolved from the DOM.
                        name_attr = _group_name_attr(port, f)
                        if name_attr:
                            from hard_patterns import fill_grouped_checkboxes
                            try:
                                fill_grouped_checkboxes(
                                    port, name_attr,
                                    f.get("option_label", "") or m["value"])
                                ok2, err2 = True, ""
                            except RuntimeError as e:
                                ok2, err2 = False, str(e)[:200]
                            per_field.append({"field": m["field"],
                                              "action": "click",
                                              "expected": m["value"],
                                              "applied": ok2, "note": err2})
                        else:
                            scope_sel, inputs_sel = group_scope_inputs(f)
                            r = cdp_ok(port, "evalb64",
                                       b64(group_option_native_click_js(
                                           scope_sel, inputs_sel,
                                           f.get("option_label", "") or m["value"])),
                                       timeout=60)["result"]
                            ok2b = bool(r.get("ok") and (r.get("checked") or
                                                         r.get("already")))
                            per_field.append({"field": m["field"],
                                              "action": "click",
                                              "expected": m["value"],
                                              "applied": ok2b,
                                              "note": r.get("why", "")})
                else:
                    # grouped radios (and others): native label click
                    # (Ashby per-load session UUIDs; Lever shared names).
                    # Native clicks toggle opacity-0 custom inputs that
                    # coordinate clicks miss.
                    scope_sel, inputs_sel = group_scope_inputs(f)
                    r = cdp_ok(port, "evalb64",
                               b64(group_option_native_click_js(
                                   scope_sel, inputs_sel,
                                   f.get("option_label", "") or m["value"])),
                               timeout=60)["result"]
                    ok2c = bool(r.get("ok") and (r.get("checked") or
                                                 r.get("already")))
                    per_field.append({"field": m["field"], "action": "click",
                                      "expected": m["value"],
                                      "applied": ok2c,
                                      "note": r.get("why", "")})
            else:
                # Singleton click: for checkboxes, use native label.click()
                # (opacity-0 custom inputs ignore coordinate clicks).
                # For other types (buttons, radios), use coordinate fclick.
                ftype = (f.get("type") or "").lower() if f else ""
                if ftype == "checkbox":
                    # native click via the associated label
                    js = ("(() => { const el = document.querySelector(" +
                          json.dumps(m["field"]) + "); if (!el) return {ok:false, why:'not-found'}; " +
                          "if (el.checked) return {ok:true, already:true}; " +
                          "const lbl = (el.labels && el.labels[0]) || el.closest('label'); " +
                          "if (!lbl) { el.click(); return {ok:true, checked: !!el.checked}; } " +
                          "lbl.scrollIntoView({block:'center', behavior:'instant'}); " +
                          "lbl.click(); return {ok:true, checked: !!el.checked}; })()")
                    r = cdp_ok(port, "evalb64", b64(js), timeout=60)["result"]
                    ok_cb = bool(r.get("ok") and (r.get("checked") or r.get("already")))
                    per_field.append({"field": m["field"], "action": "click",
                                      "expected": m["value"],
                                      "applied": ok_cb,
                                      "note": r.get("why", "")})
                else:
                    scroll_to(port, m["field"])
                    r = cdp(port, "fclick", b64(m["field"]))
                    per_field.append({"field": m["field"], "action": "click",
                                      "expected": m["value"],
                                      "applied": bool(r.get("ok")),
                                      "note": r.get("error", "")})

    # 4) uploads (deterministic helper: key re-discovery, polling,
    # retry, self-verify; raises RuntimeError on failure)
    for m in actions:
        if m["action"] == "upload":
            from hard_patterns import upload_resume
            try:
                sig = upload_resume(port, m["field"], m["value"])
                ok3, err3 = True, "signal=" + sig
            except RuntimeError as e:
                ok3, err3 = False, str(e)[:200]
            per_field.append({"field": m["field"], "action": "upload",
                              "expected": m["value"], "applied": ok3,
                              "note": err3})

    # 5) readback + diff
    rb_items = []
    for m in actions:
        f = by_key.get(m["field"], {})
        gscope, ginputs = (group_scope_inputs(f) if f.get("grouped")
                           else (None, None))
        rb_items.append({"key": m["field"],
                         "type": f.get("type", ""),
                         "grouped": bool(f.get("grouped")),
                         "gscope": gscope, "ginputs": ginputs,
                         "option_label": f.get("option_label", "")})
    rb = cdp_ok(port, "evalb64", b64(readback_js(b64(json.dumps(rb_items)))),
                timeout=120)["result"]
    rb_by_key = {r["key"]: r for r in rb}
    filled, mismatched = [], []
    for pf in per_field:
        r = rb_by_key.get(pf["field"], {})
        pf["actual"] = r.get("value", "") if r.get("found") else "<not-found>"
        if pf["action"] == "click":
            ftype = by_key.get(pf["field"], {}).get("type", "")
            if ftype in ("checkbox", "radio"):
                # strict: a toggled-off box must not pass
                ok = pf["actual"] == "checked"
            else:
                # yes/no buttons: no checked state; use aria-pressed/active class
                cls = (r.get("cls") or "").lower()
                ok = (r.get("ariaPressed") == "true" or "active" in cls
                      or "selected" in cls or pf["applied"])
        elif pf["action"] == "upload":
            # Greenhouse removes the file input after attach and shows the
            # filename instead; fall back to a filename-in-text check
            ok = pf["actual"].startswith("files:")
            if not ok and pf["applied"]:
                base = os.path.basename(pf["expected"] or "")
                if base:
                    # Retry a few times: the filename text can lag the CDP attach
                    import time as _t
                    for _ in range(3):
                        t = cdp(port, "text", timeout=60).get("text", "")
                        if base.lower() in t.lower():
                            ok = True
                            pf["actual"] = base
                            break
                        _t.sleep(1.5)
        elif pf["action"] == "location":
            # Location autocompletes canonicalize the typed value
            # ("Austin, Texas, United States" -> "Austin, TX, USA"); verify
            # the city token landed, not the exact typed string.
            city = (pf["expected"] or "").split(",")[0].strip().lower()
            ok = bool(city) and city in (pf["actual"] or "").lower()
        else:
            ok = matches(pf["expected"], pf["actual"])
        pf["verified"] = bool(ok)
        (filled if ok else mismatched).append(pf["field"])

    # 6) screenshot (saved on the browser host in the SSH lane; saved on the
    # controller in the direct lane, where the driver runs locally)
    if shot_path:
        try:
            if is_direct():
                cdp_ok(port, "shot", shot_path)
                notes.append(f"screenshot: {shot_path} (on controller)")
            else:
                host_ssh("mkdir", "-p", "/tmp/jobpilot/shots")
                remote_shot = f"/tmp/jobpilot/shots/fill-{port}-{int(time.time())}.png"
                cdp_ok(port, "shot", remote_shot)
                notes.append(f"screenshot: {remote_shot} (on the browser host)")
        except Exception as e:
            notes.append(f"screenshot failed: {e}")

    # 7) submit
    submitted = False
    if not no_submit:
        sub = resolve_submit_key(port, schema)
        if sub == "__submit_by_label__":
            # resolved by label: click via JS text match
            label = (schema.get("submit_label") or "submit").strip().split()[0]
            r = cdp(port, "fclickel", b64(
                "(() => { const w = " + json.dumps(label.lower()) + ";" +
                " const b = Array.from(document.querySelectorAll('button'))" +
                ".find(x => x.offsetParent && !x.disabled && " +
                "(x.innerText||'').toLowerCase().includes(w));" +
                " if (!b) return {error:'not-found'};" +
                " b.scrollIntoView({block:'center',behavior:'instant'});" +
                " const r = b.getBoundingClientRect();" +
                " return {x:r.x+r.width/2, y:r.y+r.height/2, inView:true}; })()"))
            submitted = bool(r.get("ok"))
            notes.append(f"submit click (label match) ok={submitted} {r.get('error','')}")
            time.sleep(8)
        elif sub:
            scroll_to(port, sub)
            r = cdp(port, "fclick", b64(sub))
            submitted = bool(r.get("ok"))
            notes.append(f"submit click ok={submitted} {r.get('error','')}")
            time.sleep(8)
        else:
            notes.append("no submit selector found; not submitted")

    skipped = [m for m in fmap["map"] if m["action"] == "skip"]
    return {"fields_total": len(schema["fields"]),
            "fields_attempted": len(per_field),
            "fields_filled": filled,
            "fields_mismatched": mismatched,
            "fields_skipped": [{"field": s["field"],
                                "guard": s.get("guard", "")} for s in skipped],
            "submitted": submitted,
            "per_field": per_field,
            "notes": notes,
            "screenshot": shot_path}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--schema", required=True)
    ap.add_argument("--map", required=True)
    ap.add_argument("--port", type=int, default=9226)
    ap.add_argument("--no-submit", action="store_true")
    ap.add_argument("--shot", default="")
    ap.add_argument("--out", default="")
    a = ap.parse_args()
    schema = json.load(open(a.schema))
    fmap = json.load(open(a.map))
    shot = a.shot or f"/tmp/jobpilot/fill_{a.port}.png"
    result = apply_fill(schema, fmap, a.port, a.no_submit, shot)
    s = json.dumps(result, indent=1)
    if a.out:
        open(a.out, "w").write(s)
    print(s)


if __name__ == "__main__":
    main()
