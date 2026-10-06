#!/usr/bin/env python3
"""Deterministic helpers for hard job-application form patterns.

Covers three patterns the local-model prototype could not close on real
Greenhouse forms (see ../local-agent-proto/REPORT.md and REPORT-v2.md):

1. react-select dropdowns with CSS-escaped numeric IDs: click the
   .select__control container center (never the 2-4px combobox input),
   then pick options from the opened menu.
2. grouped "How did you hear" checkboxes: resolve options by label text
   (the harness data-gidx pseudo-attribute does not exist in the DOM)
   and click the associated <label> natively.
3. resume upload: key re-discovery, longer polling, multiple success
   signals, one retry.

All helpers are self-verifying and raise RuntimeError naming the failed
step. No Submit clicks anywhere.
"""
import json
import os
import re
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from common import (cdp, cdp_ok, b64, container_click_js, ensure_driver,
                    host_test_file)


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


def _eval_js(port, js, timeout=60):
    """Run JS via evalb64, return the result value (raises on transport error)."""
    return cdp_ok(port, "evalb64", b64(js), timeout=timeout)["result"]


# --- Sponsorship hard-pattern (2026-10-06) ---
# Work-authorization questions must resolve to Yes / will-require-sponsorship
# and NEVER to a country-specific visa option. A prior run picked
# "Netherlands Highly Skilled Migrant Visa" on one employer's sponsorship
# question via substring fallback. This pattern guarantees sponsorship
# questions ALWAYS pick a safe option and fail loudly when none exists.
SPONSORSHIP_WANTS = [
    "Yes",
    "Yes, I will require sponsorship",
    "Yes, but will require sponsorship in the future",
    "will require sponsorship",
    "Yes, I require sponsorship",
]

COUNTRY_VISA_RE = re.compile(
    r"\b(netherlands|germany|france|ireland|united kingdom|\buk\b|canada|"
    r"australia|singapore|japan|india|spain|italy|sweden|switzerland|"
    r"poland|portugal|belgium|austria|denmark|norway|finland)\b.{0,40}\bvisa\b"
    r"|\bvisa\b.{0,40}\b(netherlands|germany|france|ireland|united kingdom|\buk\b|"
    r"canada|australia|singapore|japan|india|spain|italy|sweden|switzerland|"
    r"poland|portugal|belgium|austria|denmark|norway|finland)\b"
    r"|\bhighly skilled migrant\b|\bknowledge migrant\b|\bskilled worker\b",
    re.IGNORECASE)


def is_country_visa_option(text):
    """True if the option text names a country-specific visa/work permit."""
    return bool(COUNTRY_VISA_RE.search(text or ""))


def is_sponsorship_field(field_key, wanted_value=""):
    """True if the field looks like a sponsorship/visa question."""
    hay = ((field_key or "") + " " + (wanted_value or "")).lower()
    return ("sponsor" in hay or "visa" in hay or "work permit" in hay
            or "work authorization" in hay)


def pick_sponsorship_option(options, wants=None):
    """Pick the safe sponsorship option from a list of option texts.

    Returns (index, text). NEVER returns a country-specific visa option;
    returns (None, None) when no safe option exists so the caller fails
    loudly instead of mis-picking.
    """
    wants = wants or SPONSORSHIP_WANTS
    wl = [w.lower() for w in wants]
    for i, t in enumerate(options):  # pass 1: exact match
        text = (t or "").strip()
        if not text or is_country_visa_option(text):
            continue
        if text.lower() in wl:
            return i, t
    for i, t in enumerate(options):  # pass 2: substring match
        text = (t or "").strip()
        if not text or is_country_visa_option(text):
            continue
        tl = text.lower()
        for w in wl:
            if w in tl or tl in w:
                return i, t
    return None, None


def _unescape_css_id(key):
    """Turn CSS escapes into the plain id: '#\\34 011230003' -> '#4011230003'.

    Handles backslash + 1-6 hex digits + optional whitespace terminator,
    then any remaining single-char escapes like \\[ or \\].
    """
    def num_esc(m):
        return chr(int(m.group(1), 16))
    s = re.sub(r"\\([0-9a-fA-F]{1,6})\s?", num_esc, key)
    s = re.sub(r"\\(.)", r"\1", s)
    return s


def _id_from_key(key):
    """Raw id for '#'-prefixed keys (unescaped); None otherwise."""
    if key.startswith("#"):
        return _unescape_css_id(key[1:])
    return None


def _input_expr(key, raw_id):
    """JS expression resolving the field input, escaped key or plain id."""
    expr = "document.querySelector(" + json.dumps(key) + ")"
    if raw_id:
        expr = "(" + expr + " || document.getElementById(" + json.dumps(raw_id) + "))"
    return expr


def _control_point_js_for_id(raw_id):
    """Container-center point JS using getElementById (fallback when the
    CSS-escaped key does not resolve via querySelector). Mirrors
    common.container_click_js."""
    return ("(() => { const input = document.getElementById(" +
            json.dumps(raw_id) + "); if (!input) return {error:'not-found'}; " +
            "const ctl = input.closest('.select__control') || " +
            "input.closest('[class*=control]') || input.parentElement; " +
            "const center = () => { " +
            "ctl.scrollIntoView({block:'center', behavior:'instant'}); " +
            "void ctl.offsetHeight; " +
            "return ctl.getBoundingClientRect(); }; " +
            "let r = center(); " +
            "if (r.y < 0 || r.y > window.innerHeight) { " +
            "window.scrollTo(0, Math.max(0, r.y + window.scrollY - window.innerHeight/2)); " +
            "void ctl.offsetHeight; r = ctl.getBoundingClientRect(); } " +
            "const inView = r.y >= 0 && r.y <= window.innerHeight; " +
            "return {x: r.x + r.width/2, y: r.y + r.height/2, inView: inView}; })()")


_MENU_OPEN_JS = ("(() => { const opts = Array.from(document.querySelectorAll(" +
                "'.select__menu [role=option], [class*=select__menu] [role=option]'));" +
                " return opts.some(o => o.offsetParent !== null); })()")


def _option_point_js(option):
    """Point JS for the visible menu option matching `option`
    (exact case-insensitive, then contains); skips already-selected ones."""
    return ("(() => { const want = " + json.dumps(option) + ".toLowerCase().trim(); " +
            "const lab = s => (s||'').trim().toLowerCase(); " +
            "const cands = Array.from(document.querySelectorAll(" +
            "'.select__menu [role=option], [class*=select__menu] [role=option]'))" +
            ".filter(o => o.offsetParent !== null && " +
            "o.getAttribute('aria-selected') !== 'true'); " +
            "let el = cands.find(o => lab(o.innerText) === want); " +
            "if (!el) el = cands.find(o => { const t = lab(o.innerText); " +
            "return t && (t.includes(want) || want.includes(t)); }); " +
            "if (!el) return {error:'no-option'}; " +
            "el.scrollIntoView({block:'center', behavior:'instant'}); " +
            "void el.offsetHeight; " +
            "const r = el.getBoundingClientRect(); " +
            "const inView = r.y >= 0 && r.y <= window.innerHeight && " +
            "r.x >= 0 && r.x <= window.innerWidth; " +
            "return {x: r.x + r.width/2, y: r.y + r.height/2, inView: inView}; })()")


def _select_value_js(input_expr):
    """Read back the react-select value: single-value text and multi labels."""
    return ("(() => { const input = " + input_expr + "; " +
            "if (!input) return {error:'not-found'}; " +
            "const ctl = input.closest('.select__control') || " +
            "input.closest('[class*=control]') || input.parentElement; " +
            "const sv = ctl ? ctl.querySelector('.select__single-value') : null; " +
            "const multi = ctl ? Array.from(ctl.querySelectorAll(" +
            "'.select__multi-value__label')).map(e => e.innerText.trim()) : []; " +
            "const isMulti = multi.length > 0 || " +
            "(ctl && ctl.closest && !!ctl.closest('[class*=is-multi]')); " +
            "return {single: sv ? sv.innerText.trim() : '', multi: multi, " +
            "isMulti: isMulti}; })()")


def _menu_open(port):
    return bool(_eval_js(port, _MENU_OPEN_JS))


def _open_menu(port, field_key, click_js, seconds=8):
    """Open the react-select menu: click the container only if the menu is
    not already open (a redundant click would toggle it closed), then poll
    for the open menu."""
    if not _menu_open(port):
        r = cdp(port, "fclickel", b64(click_js))
        if not r.get("ok"):
            raise RuntimeError(
                f"react-select container click failed for key: {field_key}: "
                f"{r.get('error')}")
        time.sleep(0.8)
    deadline = time.time() + seconds
    while time.time() < deadline:
        if _menu_open(port):
            return
        time.sleep(0.4)
    # Only dismiss via Escape if a menu actually opened; a stray Escape
    # would dismiss modals (e.g. Wellfound's apply dialog).
    if _menu_open(port):
        cdp(port, "fkey", "Escape")
    raise RuntimeError(f"react-select menu did not open for key: {field_key}")


def _wait_for_option(port, option, seconds=5):
    deadline = time.time() + seconds
    while time.time() < deadline:
        r = _eval_js(port, _option_point_js(option))
        if isinstance(r, dict) and "x" in r:
            return True
        time.sleep(0.5)
    return False


def fill_react_select(port, field_key, option_text):
    """Fill a react-select dropdown (single or multi).

    field_key: selector as-is (may be CSS-escaped, e.g. '#\\34 011230003').
    option_text: str or list of str (multi-select like 'mark all that apply').
    Clicks the .select__control container center (never the 2-4px input),
    picks each option from the opened menu, Escapes, then verifies.
    """
    ensure_driver()
    wanted = [option_text] if isinstance(option_text, str) else list(option_text)
    if not wanted:
        raise RuntimeError("react-select: empty option list for key: " + field_key)
    raw_id = _id_from_key(field_key)
    input_expr = _input_expr(field_key, raw_id)

    # (a) resolve the element
    if _eval_js(port, "(() => { return " + input_expr + " ? 'ok' : null; })()") != "ok":
        raise RuntimeError(f"react-select resolve failed for key: {field_key}")

    # (b) open the menu via the control container center
    # (container_click_js already scrolls into view and verifies the point;
    # fall back to a getElementById-based point if the raw key never
    # resolved via querySelector)
    if _eval_js(port, "(() => { return document.querySelector(" +
                json.dumps(field_key) + ") ? 'ok' : null; })()") == "ok":
        click_js = container_click_js(field_key)
    else:
        click_js = _control_point_js_for_id(raw_id)
    _open_menu(port, field_key, click_js)

    # (c/d) pick each option from the opened menu
    for opt in wanted:
        _open_menu(port, field_key, click_js)
        # Type-to-filter: long option lists (e.g. school search with
        # thousands of entries) only reveal matches after typing.
        # Trusted CDP input so React sees the change.
        try:
            cdp(port, "ftype", b64(field_key), b64(opt))
            time.sleep(1.2)
        except Exception:
            pass
        if not _wait_for_option(port, opt, 5):
            if _menu_open(port):
                cdp(port, "fkey", "Escape")
            raise RuntimeError(
                f"react-select option not found: {opt} for key: {field_key}")
        r = cdp(port, "fclickel", b64(_option_point_js(opt)))
        if not r.get("ok"):
            if _menu_open(port):
                cdp(port, "fkey", "Escape")
            raise RuntimeError(
                f"react-select option click failed: {opt} for key: {field_key}: "
                f"{r.get('error')}")
        time.sleep(0.6)

    # (e) close the menu, but only if one is open (a stray Escape dismisses
    # modals like Wellfound's apply dialog)
    if _menu_open(port):
        cdp(port, "fkey", "Escape")
    time.sleep(0.5)

    # (f) verify via single-value text or multi-value labels
    ver = _eval_js(port, _select_value_js(input_expr))
    if not isinstance(ver, dict) or ver.get("error"):
        raise RuntimeError(
            f"react-select verification readback failed for key: {field_key}")
    if ver.get("isMulti") or len(wanted) > 1:
        actual = ver.get("multi") or []
        missing = [w for w in wanted
                   if not any(matches(w, a) for a in actual)]
    else:
        actual = ver.get("single") or ""
        missing = [w for w in wanted if not matches(w, actual)]
    if missing:
        raise RuntimeError(
            f"react-select verification failed for key: {field_key}: "
            f"expected {wanted}, saw {actual}")
    # Sponsorship guard (2026-10-06): never leave a country-specific
    # visa selected on a sponsorship question.
    _wanted_txt = option_text if isinstance(option_text, str) else " ".join(option_text)
    if is_sponsorship_field(field_key, _wanted_txt):
        _picked = [ver.get("single") or ""] + list(ver.get("multi") or [])
        for _p in _picked:
            if _p and is_country_visa_option(_p):
                raise RuntimeError(
                    f"sponsorship guard: country-specific visa selected "
                    f"({_p!r}) for key: {field_key}; refusing")


def _checkbox_scope_sel(name_attr):
    return 'input[type="checkbox"][name=' + json.dumps(name_attr) + ']'


def _generic_option_point_js(option):
    """Point JS for a visible [role=option] matching `option`
    (exact case-insensitive, then contains)."""
    return ("(() => { const want = " + json.dumps(option) + ".toLowerCase().trim(); " +
            "const lab = s => (s||'').trim().toLowerCase(); " +
            "const cands = Array.from(document.querySelectorAll('[role=option]'))" +
            ".filter(o => o.offsetParent !== null); " +
            "let el = cands.find(o => lab(o.innerText) === want); " +
            "if (!el) el = cands.find(o => { const t = lab(o.innerText); " +
            "return t && (t.includes(want) || want.includes(t)); }); " +
            "if (!el) return {error:'no-option'}; " +
            "el.scrollIntoView({block:'center', behavior:'instant'}); " +
            "void el.offsetHeight; " +
            "const r = el.getBoundingClientRect(); " +
            "const inView = r.y >= 0 && r.y <= window.innerHeight && " +
            "r.x >= 0 && r.x <= window.innerWidth; " +
            "return {x: r.x + r.width/2, y: r.y + r.height/2, inView: inView}; })()")


def _generic_menu_open_js():
    return ("(() => Array.from(document.querySelectorAll('[role=option]'))" +
            ".some(o => o.offsetParent !== null))()")


def fill_generic_combobox(port, field_key, option_text):
    """Fill a generic div[role=combobox] dropdown (Indeed's single-select).

    Clicks the combobox to open the menu, picks the [role=option] matching
    option_text (exact, then contains), clicks it, and verifies the
    combobox text reflects the choice. Raises RuntimeError on failure.
    """
    ensure_driver()
    # open the menu (click the combobox itself)
    open_js = ("(() => { const el = document.querySelector(" +
               json.dumps(field_key) + "); if (!el) return {error:'not-found'}; " +
               "el.scrollIntoView({block:'center', behavior:'instant'}); " +
               "void el.offsetHeight; const r = el.getBoundingClientRect(); " +
               "return {x: r.x + r.width/2, y: r.y + r.height/2, inView:true}; })()")
    r = cdp(port, "fclickel", b64(open_js))
    if not r.get("ok"):
        raise RuntimeError(
            f"generic combobox open failed for {field_key}: {r.get('error')}")
    deadline = time.time() + 8
    opened = False
    while time.time() < deadline:
        if _eval_js(port, _generic_menu_open_js()):
            opened = True
            break
        time.sleep(0.4)
    if not opened:
        raise RuntimeError(
            f"generic combobox menu did not open for {field_key}")
    # pick the option
    deadline = time.time() + 6
    found = False
    while time.time() < deadline:
        pt = _eval_js(port, _generic_option_point_js(option_text))
        if isinstance(pt, dict) and "x" in pt:
            found = True
            break
        time.sleep(0.4)
    if not found:
        cdp(port, "fkey", "Escape")
        raise RuntimeError(
            f"generic combobox option not found: {option_text} "
            f"for {field_key}")
    r = cdp(port, "fclickel", b64(_generic_option_point_js(option_text)))
    if not r.get("ok"):
        raise RuntimeError(
            f"generic combobox option click failed: {option_text} "
            f"for {field_key}: {r.get('error')}")
    time.sleep(1.0)
    # verify: the combobox text should now contain the option
    ver = _eval_js(
        port,
        "(() => { const el = document.querySelector(" + json.dumps(field_key) +
        "); return el ? (el.innerText || '').trim().slice(0,120) : null; })()")
    if not ver or not matches(option_text, ver):
        raise RuntimeError(
            f"generic combobox verification failed for {field_key}: "
            f"expected {option_text}, saw {ver!r}")

    return "generic-combobox-set"


_LABEL_OF_JS = (
    "const lab = s => (s||'').trim().toLowerCase(); " +
    "const labelOf = el => { try { if (el.labels && el.labels[0]) { " +
    "const lt = el.labels[0].innerText.trim(); if (lt) return lt; } } catch(e){} " +
    "const l = el.closest('label'); if (l && l.innerText.trim()) return l.innerText; " +
    "let n = el.parentElement, d = 0; " +
    "while (n && d < 4) { const t = (n.innerText||'').trim(); " +
    "if (t && t.length <= 80) return t; n = n.parentElement; d++; } " +
    "return ''; }; " +
    "const findByLabel = (inputs, w) => { " +
    "let el = inputs.find(i => lab(labelOf(i)) === w); " +
    "if (!el) el = inputs.find(i => { const t = lab(labelOf(i)); " +
    "return t && (t.includes(w) || w.includes(t)); }); " +
    "return el; }; ")


def _group_click_js(name_attr, value):
    """One-shot JS: resolve the checkbox by label, click its <label>,
    re-verify checked."""
    return ("(() => { const sel = " + json.dumps(_checkbox_scope_sel(name_attr)) + "; " +
            "const want = " + json.dumps(value) + ".toLowerCase().trim(); " +
            _LABEL_OF_JS +
            "const inputs = Array.from(document.querySelectorAll(sel)); " +
            "if (!inputs.length) return {ok:false, why:'no-inputs'}; " +
            "const el = findByLabel(inputs, want); " +
            "if (!el) return {ok:false, why:'no-label-match', n: inputs.length}; " +
            "if (el.checked) return {ok:true, already:true}; " +
            "const lbl = (el.labels && el.labels[0]) || el.closest('label'); " +
            "if (!lbl) return {ok:false, why:'no-label-el'}; " +
            "lbl.scrollIntoView({block:'center', behavior:'instant'}); " +
            "lbl.click(); " +
            "return {ok:true, checked: !!el.checked}; })()")


def _group_verify_js(name_attr, wanted):
    return ("(() => { const sel = " + json.dumps(_checkbox_scope_sel(name_attr)) + "; " +
            "const wants = " + json.dumps(wanted) + "; " +
            _LABEL_OF_JS +
            "const inputs = Array.from(document.querySelectorAll(sel)); " +
            "const out = {}; " +
            "for (const w of wants) { const el = findByLabel(inputs, " +
            "w.toLowerCase().trim()); " +
            "out[w] = el ? (el.checked ? 'checked' : 'unchecked') : 'no-match'; } " +
            "return out; })()")


def fill_grouped_checkboxes(port, name_attr, values):
    """Check grouped checkboxes by label text (e.g. Twilio 'How did you hear').

    name_attr: the shared name attribute, e.g. 'question_68084098[]'.
    values: str or list of str. Never touches the fake data-gidx key.
    Already-checked boxes are skipped (never toggled off). The click goes
    to the <label> element natively (label.click()), which toggles React
    checkboxes reliably.
    """
    ensure_driver()
    wanted = [values] if isinstance(values, str) else list(values)
    if not wanted:
        raise RuntimeError("grouped checkboxes: empty values for name: " + name_attr)
    n = _eval_js(port, "(() => { return document.querySelectorAll(" +
                 json.dumps(_checkbox_scope_sel(name_attr)) + ").length; })()")
    if not n:
        raise RuntimeError(
            f"grouped checkboxes: no inputs with name {name_attr}")
    for v in wanted:
        r = _eval_js(port, _group_click_js(name_attr, v))
        if not isinstance(r, dict) or not r.get("ok"):
            raise RuntimeError(
                f"grouped checkboxes: click failed for {v} in name {name_attr}: "
                f"{(r or {}).get('why')}")
        if not r.get("already") and not r.get("checked"):
            raise RuntimeError(
                f"grouped checkboxes: label click failed to check {v} "
                f"in name {name_attr}")
        time.sleep(0.3)
    ver = _eval_js(port, _group_verify_js(name_attr, wanted))
    bad = [w for w in wanted if (ver or {}).get(w) != "checked"]
    if bad:
        raise RuntimeError(
            f"grouped checkboxes: verification failed for {bad} "
            f"in name {name_attr}: {ver}")


def _resolve_upload_key(port, field_key):
    """Re-discover the file input: dump-time key may be stale after an ATS
    re-render. Prefer a file input whose id/name/label mentions resume/CV,
    else the first file input. Returns the key or None."""
    js = ("(() => { if (document.querySelector(" + json.dumps(field_key) +
          ")) return {key: " + json.dumps(field_key) + "}; " +
          "const cands = []; " +
          "document.querySelectorAll('input[type=file]').forEach(el => { " +
          "const lab = ((el.labels && el.labels[0] ? el.labels[0].innerText : '') " +
          "+ ' ' + el.id + ' ' + el.name).toLowerCase(); " +
          "cands.push({id: el.id, name: el.name, " +
          "resume: /resume|\\bcv\\b/.test(lab)}); }); " +
          "const r = cands.find(c => c.resume) || cands[0]; " +
          "if (r && r.id) return {key: '#' + r.id}; " +
          "if (r && r.name) return {key: 'input[type=file][name=\"' + " +
          "r.name.replace(/\"/g, '') + '\"]'}; " +
          "return {key: null}; })()")
    try:
        return _eval_js(port, js).get("key")
    except Exception:
        return None


def _upload_status_js(key, base_lower):
    return ("(() => { const el = document.querySelector(" + json.dumps(key) +
            "); " +
            "const txt = (document.body ? document.body.innerText : '').toLowerCase(); " +
            "return {fileOk: !!(el && el.files && el.files.length > 0), " +
            "inDom: !!el, nameInText: txt.indexOf(" + json.dumps(base_lower) +
            ") !== -1}; })()")


def _poll_upload(port, key, base_lower, seconds):
    """Poll for any success signal. Returns (signal_name, last_status)
    or (None, last_status)."""
    deadline = time.time() + seconds
    last = {}
    while time.time() < deadline:
        last = _eval_js(port, _upload_status_js(key, base_lower))
        if last.get("inDom") and last.get("fileOk"):
            return "files-length", last
        if last.get("nameInText"):
            sig = ("input-removed-with-filename" if not last.get("inDom")
                   else "filename-in-text")
            return sig, last
        time.sleep(0.5)
    return None, last


def _ffile(port, key, pdf_path):
    r = cdp(port, "ffile", b64(key), pdf_path)
    if not r.get("ok"):
        raise RuntimeError(
            f"resume upload: ffile failed for {key}: {r.get('error')}")


def upload_resume(port, field_key, pdf_path):
    """Attach the resume PDF via CDP setFileInputFiles with verification.

    Re-discovers the key if the dump-time one went stale, polls up to 15s
    for any success signal (files.length>0, filename in page text, or the
    input removed from the DOM after attach), retries the ffile once on
    failure. Returns the success signal name.
    """
    ensure_driver()
    if not host_test_file(pdf_path):
        raise RuntimeError(f"resume not found on browser host: {pdf_path}")
    base_lower = os.path.basename(pdf_path).lower()
    key = _resolve_upload_key(port, field_key)
    if not key:
        raise RuntimeError(
            "resume upload: no file input found (key stale and rediscovery empty)")
    _ffile(port, key, pdf_path)
    sig, _ = _poll_upload(port, key, base_lower, 15)
    if not sig:
        # retry once with a freshly re-discovered key (ATS may re-render)
        key = _resolve_upload_key(port, field_key)
        if not key:
            raise RuntimeError(
                f"resume upload unverified for {pdf_path}: input gone after "
                f"first attempt and no filename evidence")
        _ffile(port, key, pdf_path)
        sig, _ = _poll_upload(port, key, base_lower, 10)
    if not sig:
        raise RuntimeError(
            f"resume upload unverified for {pdf_path}: "
            f"ffile ok but no success signal")
    return sig
