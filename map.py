#!/usr/bin/env python3
"""Step 2: map a form schema onto applicant facts via the local model.

Usage: map.py --schema schema.json [--facts facts.json] [--out map.json]
Output: {"map": [{"field","action","value","guard"?}...],
         "skipped_by_guard": [...], "unmapped": [...]}

Actions: fill | select | click | upload | location | skip
Guard: sponsorship/visa/authorization, background-check, assessment, criminal,
drug-test questions are ALWAYS forced to "skip" for Muse review, no matter
what the model returns. The model never decides those.
"""
import argparse
import json
import os
import re
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import model

HERE = os.path.dirname(os.path.abspath(__file__))

SYSTEM_PROMPT = """You map a job applicant's known facts onto a web form's fields. Output ONLY a strict JSON array. No commentary, no markdown fences, no extra keys. Minimize internal reasoning; be direct and fast.

Each element: {"field": "<field key exactly as given>", "action": "<fill|select|click|upload|location|skip>", "value": "<value or empty string>"}.

Actions:
- "fill": type text into a text/email/tel/url/number/textarea input.
- "select": choose a dropdown option (native select or custom dropdown); value must be the EXACT option text from the form's option list.
- "click": radio button, checkbox, or yes/no button; the "field" is the key of the specific option to click, value is its option label/value.
- "upload": resume file field; value is the resume file path given in the facts.
- "location": autocomplete location field; value is the city to type (e.g. "Austin, Texas").
- "skip": field has no known answer, is optional, or needs the applicant's judgment (assessments, background-check consent, cover letters, motivation essays, salary negotiation).

Rules:
1. Use ONLY the facts in APPLICANT FACTS. Never invent experience, skills, dates, compensation history, education, or work authorization.
2. If a field asks something not in the facts, or offers no matching option, use action "skip".
3. Phone: digits only, e.g. 5551234567.
4. Demographics: veteran = "I am not a veteran" (or closest option);
   disability = "no" (or "I don't have a disability" / equivalent);
   ethnicity = "not Hispanic or Latino" (never pick "Hispanic or Latino").
   Race and gender: NEVER choose a specific race or gender (do not infer from
   name, photo, or anything else); click "Decline to self-identify" when that
   option exists, otherwise skip.
5. Pronouns: "I prefer not to say" when that option exists.
6. "How did you hear about us": use the job_source in the facts. If it is a
   checkbox group (one field per source), use action "click" on ONLY the
   checkbox whose label matches the job_source (e.g. LinkedIn); leave the
   others unmapped. If it is a select, choose the matching option text.
7. Previously employed at the company: No.
8. Education: use the facts.json "education" entries (school, degree, end year).
9. Free-text questions about motivation, "why this company", or anything subjective: skip.
10. Cover letter fields: skip.
11. Referral: facts['referral'] names a referrer for X/xAI ONLY. For any other
    company, or when the facts name no referrer for THIS company: the
    "were you referred" question gets action "select" value "No" (or "skip"
    if unsure), and the referrer-name question gets "skip". NEVER invent a
    referrer.
12. Copy "field" keys EXACTLY as given in the FORM FIELDS list.
13. Willingness/commitment questions (hybrid/in-office/relocate/travel,
    flexible start): answer per the facts willing_hybrid, willing_in_office,
    willing_relocate, willing_travel, willing_start_date_flexible (all true):
    click/select "Yes". "When can you start a new role?" / start-date fields:
    the facts have no start date, so skip (never invent a date).
14. Ashby yes/no questions appear as PAIRED button fields (keys ending
    [data-option="yes"] and [data-option="no"] with the same question label):
    use action "click" on the correct option's key ONLY, never both.
    Radio/checkbox groups appear as one field per option (option_label set):
    use action "click" on ONLY the matching option's key. EEO demographic
    radios: pronouns = "I prefer not to say" if present; veteran = the
    "not a veteran" option; disability = "no"; ethnicity = "not Hispanic or
    Latino"; race and gender: NEVER a specific race/gender - click "Decline
    to self-identify" when present, otherwise skip. Legal consent checkboxes
    (arbitration agreement, "certify ... true and correct", background-check
    or assessment consent): skip - they are forced to skip for review anyway.
15. "How many years of experience" numeric fields: answer with a plain
    integer (8), never "8+" or a range - LinkedIn rejects non-numeric input
    and the step will not advance."""

FIELD_MAP_SCHEMA = {
    "type": "array",
    "items": {
        "type": "object",
        "properties": {
            "field": {"type": "string"},
            "action": {"type": "string",
                       "enum": ["fill", "select", "click", "upload", "location", "skip"]},
            "value": {"type": "string"},
        },
        "required": ["field", "action", "value"],
        "additionalProperties": False,
    },
}

# Never let the model decide these: force skip for Muse review.
GUARD_RE = re.compile(
    r"sponsor|visa|authoriz|e-?verify|background.?check|assessment|"
    r"criminal|credit.?check|drug.?test|security.?clearance|"
    r"arbitrat|certif|attest|"
    r"consent to (collect|store|process)|"
    r"privacy policy|read and understood|i acknowledge",
    re.IGNORECASE)

# Narrow exceptions to the guard: standard LinkedIn work-auth questions with
# unambiguous factual answers from standing facts. The MODEL never decides
# these; the answers are hardcoded from the applicant's authorized facts.
# - Sponsorship: per facts.json work_authorization -> the matching "Yes"
# - Legally authorized: per facts.json work_authorization -> "Yes"
# SAFETY: a question phrased "authorized to work WITHOUT sponsorship" is NOT
# covered (answer would be No); those fall through to the guard for review.
WORK_AUTH_ANSWERS = [
    (re.compile(r"require sponsorship.*employment visa status", re.IGNORECASE),
     "Yes", "sponsorship-required-yes"),
    (re.compile(r"will you (now or in the future )?require .*sponsor",
                re.IGNORECASE),
     "Yes", "sponsorship-required-yes"),
    (re.compile(r"do you (currently |now )?require .*sponsor",
                re.IGNORECASE),
     "Yes", "sponsorship-required-yes"),
    (re.compile(r"(legally )?authorized to work", re.IGNORECASE),
     "Yes", "legally-authorized-yes"),
]


def work_auth_override(field):
    """Return (option_label_to_click, note) for standard work-auth questions,
    or None if the question doesn't match a known pattern."""
    from templates import ACK_BAD_RE
    label = (field.get("label") or "").lower()
    if "without sponsorship" in label or "without requiring sponsorship" in label:
        return None
    if ACK_BAD_RE.search(label):
        return None
    for pat, answer, note in WORK_AUTH_ANSWERS:
        if pat.search(label):
            return answer, note
    return None


# "Previously employed by this company" - the model has clicked "Yes" before
# despite facts saying No, so this is deterministic too, from standing facts.
PREV_EMPLOYED_RE = re.compile(
    r"previously (been |worked )?(employed|worked)|"
    r"ever (been |worked )?(for|by|at|with)|"
    r"prior employment|have you worked for|worked here before",
    re.IGNORECASE)


def apply_prev_employed_override(mapped, schema, facts):
    """Deterministically answer 'previously employed here' from facts
    (facts['previously_employed_here'], standing: "No"). Handles radio
    groups and yes/no selects."""
    want = (facts.get("previously_employed_here") or "No").strip()
    by_key = {f["key"]: f for f in schema.get("fields", [])}
    groups = {}
    for e in mapped:
        f = by_key.get(e["field"], {})
        if f.get("type") == "radio" and f.get("group"):
            groups.setdefault(("radio", f["group"]), []).append((e, f))
        elif f.get("type") == "select":
            groups.setdefault(("select", f["key"]), []).append((e, f))
    for (kind, gid), pairs in groups.items():
        qlabel = (pairs[0][1].get("label") or "")
        if not PREV_EMPLOYED_RE.search(qlabel):
            continue
        if kind == "radio":
            matched = False
            for e, f in pairs:
                ol = (f.get("option_label") or "").strip().lower()
                if ol == want.lower():
                    e["action"] = "click"; e["value"] = want
                    e["note"] = "prev-employed-no"; e.pop("guard", None)
                    matched = True
                else:
                    e["action"] = "skip"
            if not matched:
                for f in schema.get("fields", []):
                    if (f.get("type") == "radio" and f.get("group") == gid and
                            (f.get("option_label") or "").strip().lower()
                            == want.lower()):
                        mapped.append({"field": f["key"], "action": "click",
                                       "value": want, "note": "prev-employed-no"})
                        break
        else:
            e, f = pairs[0]
            e["action"] = "select"; e["value"] = want
            e["note"] = "prev-employed-no"; e.pop("guard", None)
    return mapped


def norm_key(k):
    k = k.strip()
    k = re.sub(r"^(id|name|key)=", "", k, flags=re.IGNORECASE)
    # unescape CSS hex escapes: "#\34 011230003" -> "#4011230003"
    k = re.sub(r"\\([0-9a-fA-F]{1,6})\s?",
               lambda m: chr(int(m.group(1), 16)), k)
    # strip remaining backslash escapes the model may drop: "\[" -> "["
    k = re.sub(r"\\(.)", r"\1", k)
    # the model sometimes swaps quote styles in attribute selectors
    k = k.replace("'", '"')
    return k.lower()


def disambiguate_by_value(cands, value):
    """Pick one candidate field using the entry's value against option labels,
    option values, and data-option markers in the key. Returns the key or None
    when there is no clear winner."""
    v = (value or "").strip().lower()
    if not v:
        return None
    scored = []
    for f in cands:
        ol = (f.get("option_label") or "").lower()
        ov = (f.get("option_value") or "").lower()
        key = f["key"].lower()
        score = 0
        if v == ol or v == ov:
            score = 3
        elif f'data-option="{v}"' in key:
            score = 3
        elif ol and (v in ol or ol in v):
            score = 2
        elif ov and (v in ov or ov in v):
            score = 2
        elif v in key:
            score = 1
        if score:
            scored.append((score, f))
    if not scored:
        return None
    scored.sort(key=lambda x: -x[0])
    if len(scored) > 1 and scored[0][0] == scored[1][0]:
        return None  # tie: ambiguous, leave unmapped
    return scored[0][1]["key"]


def resolve_key(model_key, schema, value=""):
    """Resolve a model-returned field key to a schema key.

    The model often truncates long keys (e.g. returns just the Ashby
    [data-field-path="..."] container part, dropping the trailing
    ` input[type="text"]`). Resolution: exact match, then unique prefix
    match, then prefix match disambiguated by the entry's value against
    option labels/values/data-option markers.
    """
    mk = norm_key(model_key or "")
    if not mk:
        return None
    fields = schema["fields"]
    for f in fields:
        if norm_key(f["key"]) == mk:
            return f["key"]
    cands = [f for f in fields if norm_key(f["key"]).startswith(mk)]
    if len(cands) == 1:
        return cands[0]["key"]
    if len(cands) > 1:
        return disambiguate_by_value(cands, value)
    # last resort: the schema key is a prefix of the model key
    # (model added extra junk)
    for f in fields:
        if mk.startswith(norm_key(f["key"])):
            return f["key"]
    return None


def build_field_list(schema):
    lines = []
    for f in schema["fields"]:
        opts = f.get("options") or []
        opt_s = (" options=" + json.dumps(opts[:30])) if opts else ""
        extra = ""
        if f.get("option_label"):
            extra += f" option_label={json.dumps(f['option_label'])}"
        if f.get("option_value"):
            extra += f" option_value={json.dumps(f['option_value'])}"
        lines.append(
            f"- key={f['key']} type={f['type']} label={json.dumps(f['label'])}"
            f" required={str(f.get('required', False)).lower()}{opt_s}{extra}")
    return "\n".join(lines)


SOURCE_KEYWORDS = ("linkedin", "indeed", "glassdoor", "careers website",
                   "career site", "twitter", "company website", "job board",
                   "referral", "blog", "conference")


def dedupe_source_checkboxes(mapped, schema, facts):
    """Post-process: for 'how did you hear about us' checkbox groups, keep
    only the single checkbox matching facts['job_source']. The model sometimes
    clicks extra boxes despite the prompt's ONLY instruction. Groups are
    identified by shared `group` (name attribute) + source-like labels."""
    by_key = {f["key"]: f for f in schema["fields"]}
    groups = {}
    for e in mapped:
        if e["action"] != "click":
            continue
        f = by_key.get(e["field"], {})
        if f.get("type") not in ("checkbox", "radio"):
            continue
        groups.setdefault(f.get("group") or e["field"], []).append(e)
    want = (facts.get("job_source") or "").lower()
    for _, items in groups.items():
        if len(items) < 2:
            continue
        labels = " ".join(by_key.get(e["field"], {}).get(
            "option_label", "") for e in items).lower()
        if sum(kw in labels for kw in SOURCE_KEYWORDS) < 2:
            continue
        keep = [e for e in items if want and want in e.get("value", "").lower()]
        drop = [e for e in items if e not in keep]
        if keep and drop:
            for e in drop:
                e["action"] = "skip"
                e["note"] = "dedupe: kept only job_source match"
    return mapped


def coerce_action(field, entry):
    """Deterministic fixes the model gets wrong:
    1. Action/type mismatch: 'fill' on a select -> 'select'; 'select' on a
       text-like input -> 'fill'. (The model is non-deterministic here.)
    2. Already-correct: if the field's current value already matches the
       intended value, force 'skip' so pre-filled fields are left untouched.
    3. Checkbox/radio already in desired state: 'click' on an already-checked
       box would toggle it OFF; skip instead.
    """
    ftype = (field.get("type") or "").lower()
    action, value = entry.get("action"), (entry.get("value") or "").strip()
    if action == "fill" and ftype == "select":
        action = "select"
    elif action == "select" and ftype in (
            "text", "email", "tel", "url", "number", "textarea"):
        action = "fill"
    entry["action"] = action
    # checkbox/radio already checked + click-to-check => skip (avoid toggle-off)
    if action == "click" and ftype in ("checkbox", "radio") and field.get("checked"):
        # for radios in a group, "checked" means this option is selected;
        # clicking it again is harmless but pointless; skip for safety
        entry["action"] = "skip"
        entry["note"] = "already-checked"
        return entry
    if action in ("fill", "select") and value:
        cur = (field.get("value") or "").strip()
        if cur and _matches(value, cur):
            entry["action"] = "skip"
            entry["note"] = "already-correct"
        elif ftype == "select" and cur:
            # current value may be an option VALUE (e.g. "us"); check whether
            # the intended option text is among options and cur looks like
            # an already-chosen valid option
            opts = [str(o).strip().lower() for o in field.get("options", [])]
            if value.lower() in opts and cur.lower() in (
                    o.split("(")[0].strip() for o in opts):
                pass  # can't confirm; leave the model's action
    return entry


def _matches(expected, actual):
    e, a = (expected or "").strip().lower(), (actual or "").strip().lower()
    if not e or not a:
        return False
    return e == a or e in a or a in e


def resume_step_fix(schema, mapped):
    """LinkedIn resume picker: deterministically select the most recent resume.

    The radio labels carry filenames + upload dates (e.g. "PDF ...pdf 10/2/2026").
    LinkedIn pre-checks the most recent upload; if it's already checked we leave
    it alone, otherwise we click the latest-dated option. This avoids burning a
    slow model call on a mechanical choice.
    """
    fields = schema.get("fields", [])
    if not fields:
        return mapped
    if not all(f.get("type") == "radio" and ".pdf" in (f.get("label") or "").lower()
               for f in fields):
        return mapped
    def parse_date(label):
        m = re.search(r'(\d{1,2})/(\d{1,2})/(\d{4})', label or "")
        if m:
            return (int(m.group(3)), int(m.group(1)), int(m.group(2)))
        return (0, 0, 0)
    best = max(fields, key=lambda f: parse_date(f.get("label", "")))
    by_field = {e["field"]: e for e in mapped}
    if best.get("checked"):
        for e in mapped:
            e["action"] = "skip"
            e["note"] = "resume-default-ok"
    else:
        for e in mapped:
            if e["field"] == best["key"]:
                e["action"] = "click"
                e["value"] = (best.get("label") or "")[:80]
                e["note"] = "resume-latest"
            else:
                e["action"] = "skip"
    return mapped


def is_resume_step(schema):
    fields = schema.get("fields", [])
    return bool(fields) and all(
        f.get("type") == "radio" and ".pdf" in (f.get("label") or "").lower()
        for f in fields)


def dedupe_radio_clicks(mapped, schema):
    """For radio groups (same name), keep only the FIRST 'click' action.
    The model sometimes clicks both Yes and No; only one can be selected.
    """
    by_key = {f["key"]: f for f in schema.get("fields", [])}
    # group keys by radio group name
    groups = {}
    for e in mapped:
        f = by_key.get(e["field"], {})
        if f.get("type") == "radio" and f.get("group"):
            groups.setdefault(f["group"], []).append(e)
    for g, entries in groups.items():
        clicks = [e for e in entries if e["action"] == "click"]
        if len(clicks) > 1:
            # keep the first, skip the rest
            for e in clicks[1:]:
                e["action"] = "skip"
                e["note"] = "dedupe: only one radio per group"
    return mapped


def map_fields(schema, facts, ats="unknown"):
    # Fast path: LinkedIn resume picker is mechanical, skip the model call.
    if is_resume_step(schema):
        mapped = [{"field": f["key"], "action": "skip", "value": ""}
                  for f in schema["fields"]]
        mapped = resume_step_fix(schema, mapped)
        return {"map": mapped, "skipped_by_guard": [], "unmapped": [],
                "note": "resume-fast-path"}
    # Deterministic-first: templates answer every recognized field from
    # proven field data. The local model is the backup: it sees only the
    # fields templates left unmapped, or skipped without a guard marker
    # (guard-skips stay skipped; the model never overrides them).
    from templates import map_template
    tmpl = map_template(schema, facts, ats)
    candidates = list(tmpl["unmapped"]) + [
        e["field"] for e in tmpl["map"]
        if e["action"] == "skip" and not e.get("guard")
        and e["field"] not in tmpl["unmapped"]]
    if not candidates:
        return tmpl
    model_mapped, model_guarded, model_seen = _map_with_model(
        schema, facts, candidates)
    # Model answers replace template skips; template answers stand.
    skip_keys = {e["field"] for e in tmpl["map"]
                 if e["action"] == "skip" and not e.get("guard")}
    mapped = ([e for e in tmpl["map"] if e["field"] not in skip_keys]
              + model_mapped)
    guarded = tmpl["skipped_by_guard"] + model_guarded
    unmapped = [k for k in candidates if k not in model_seen]
    mapped = dedupe_source_checkboxes(mapped, schema, facts)
    mapped = dedupe_radio_clicks(mapped, schema)
    mapped = apply_work_auth_overrides(mapped, schema)
    mapped = apply_work_auth_select_overrides(mapped, schema)
    mapped = apply_acknowledge_override(mapped, schema)
    mapped = apply_prev_employed_override(mapped, schema, facts)
    return {"map": mapped, "skipped_by_guard": guarded, "unmapped": unmapped,
            "note": tmpl.get("note", "") + "+model-backup"}


def _map_with_model(schema, facts, field_keys):
    """Model backup: map only the given field keys via one strict-JSON call.

    Returns (mapped, guarded, seen). Raises if the model server is down.
    """
    sub = {"fields": [f for f in schema["fields"] if f["key"] in field_keys]}
    ok, info = model.probe()
    if not ok:
        raise RuntimeError(f"local model not reachable: {info}")
    user = ("APPLICANT FACTS:\n```json\n" + json.dumps(facts, indent=1) +
            "\n```\n\nFORM FIELDS (key, type, label, options if any):\n" +
            build_field_list(sub) +
            "\n\nMap every field. Output the JSON array only.")
    raw = model.budgeted_chat(SYSTEM_PROMPT, user, "field_map", FIELD_MAP_SCHEMA,
                     max_tokens=4000)
    # index schema keys for normalization (tolerant: the model truncates keys)
    mapped, guarded, seen = [], [], set()
    for item in raw:
        if not isinstance(item, dict):
            continue
        canon = resolve_key(item.get("field", ""), sub, item.get("value", ""))
        if not canon or canon in seen:
            continue
        field = next(f for f in sub["fields"] if f["key"] == canon)
        label_blob = f"{field.get('label','')} {field.get('key','')} {field.get('option_label','')}"
        entry = {"field": canon, "action": item.get("action", "skip"),
                 "value": item.get("value", "")}
        if GUARD_RE.search(label_blob) and entry["action"] != "skip":
            entry["action"] = "skip"
            entry["guard"] = "muse-review"
            guarded.append(entry)
        else:
            if GUARD_RE.search(label_blob):
                entry["guard"] = "muse-review"
                guarded.append(entry)
                entry["action"] = "skip"
            else:
                entry = coerce_action(field, entry)
        mapped.append(entry)
        seen.add(canon)
    return mapped, guarded, seen


def apply_work_auth_overrides(mapped, schema):
    """Apply hardcoded standing-fact answers to standard work-auth questions,
    overriding guard skips. Groups fields by radio group; for each group whose
    question matches a known pattern, click the matching option."""
    by_key = {f["key"]: f for f in schema.get("fields", [])}
    # group mapped entries by radio group
    groups = {}
    for e in mapped:
        f = by_key.get(e["field"], {})
        if f.get("type") == "radio" and f.get("group"):
            groups.setdefault(f["group"], []).append((e, f))
    for g, pairs in groups.items():
        # check if the question matches a known work-auth pattern
        qlabel = (pairs[0][1].get("label") or "")
        override = work_auth_override({"label": qlabel})
        if not override:
            continue
        want_opt, note = override
        matched = False
        for e, f in pairs:
            ol = (f.get("option_label") or "").strip().lower()
            if ol == want_opt.lower():
                e["action"] = "click"
                e["value"] = want_opt
                e["note"] = note
                # remove guard flag if present
                e.pop("guard", None)
                matched = True
            else:
                e["action"] = "skip"
        if not matched:
            # The model emitted no entry for the wanted option (it only
            # mapped one option per question). Add a click entry for the
            # schema field whose option_label matches.
            for f in schema.get("fields", []):
                if (f.get("type") == "radio" and f.get("group") == g and
                        (f.get("option_label") or "").strip().lower()
                        == want_opt.lower()):
                    mapped.append({"field": f["key"], "action": "click",
                                   "value": want_opt, "note": note})
                    matched = True
                    break
    return mapped


def apply_work_auth_select_overrides(mapped, schema):
    """Extend work-auth overrides to select/custom-select fields (Greenhouse
    uses custom dropdowns for sponsorship questions). For each select whose
    label matches a known work-auth pattern, choose the option matching the
    facts-driven answer."""
    by_key = {f["key"]: f for f in schema.get("fields", [])}
    seen = {e["field"] for e in mapped}
    for f in schema.get("fields", []):
        ftype = (f.get("type") or "").lower()
        if ftype not in ("select", "custom-select"):
            continue
        override = work_auth_override({"label": f.get("label") or ""})
        if not override:
            continue
        want_opt, note = override
        opts = [str(o) for o in (f.get("options") or [])]
        # pick the option that is exactly the want, else starts with it
        pick = None
        for o in opts:
            if o.strip().lower() == want_opt.lower():
                pick = o
                break
        if not pick:
            for o in opts:
                if o.strip().lower().startswith(want_opt.lower()):
                    pick = o
                    break
        if not pick:
            continue
        key = f["key"]
        if key in seen:
            for e in mapped:
                if e["field"] == key:
                    e["action"] = "select"
                    e["value"] = pick
                    e["note"] = note
                    e.pop("guard", None)
        else:
            mapped.append({"field": key, "action": "select",
                           "value": pick, "note": note})
    return mapped


# Narrow exception: "Acknowledge/Confirm" privacy checkbox. Standard
# privacy-policy acknowledgement (required to submit). The model never
# decides this; we click ONLY when the option label is exactly
# "Acknowledge/Confirm" and the label carries no arbitration, background
# check, drug test, or assessment language.
ACK_RE = re.compile(r"^acknowledge/confirm$", re.IGNORECASE)
from templates import ACK_BAD_RE


def apply_acknowledge_override(mapped, schema):
    """Click the narrow "Acknowledge/Confirm" privacy checkbox; legal-weight
    language stays skipped for human review."""
    by_key = {f["key"]: f for f in schema.get("fields", [])}
    for e in mapped:
        f = by_key.get(e["field"], {})
        if (f.get("type") or "").lower() != "checkbox":
            continue
        ol = (f.get("option_label") or "").strip()
        lab = (f.get("label") or "")
        if ACK_RE.match(ol) and not ACK_BAD_RE.search(lab):
            e["action"] = "click"
            e["value"] = ol
            e["note"] = "privacy-ack"
            e.pop("guard", None)
    return mapped


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--schema", required=True)
    ap.add_argument("--facts", default=os.path.join(HERE, "facts.json"))
    ap.add_argument("--out", default="")
    a = ap.parse_args()
    schema = json.load(open(a.schema))
    facts = json.load(open(a.facts))
    result = map_fields(schema, facts)
    s = json.dumps(result, indent=1)
    if a.out:
        open(a.out, "w").write(s)
        n = len(result["map"])
        print(f"wrote {a.out} ({n} mapped, {len(result['skipped_by_guard'])} guarded, "
              f"{len(result['unmapped'])} unmapped)")
    else:
        print(s)


if __name__ == "__main__":
    main()
