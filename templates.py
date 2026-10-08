#!/usr/bin/env python3
"""Frozen deterministic field-mapping templates per ATS.

Usage: templates.py --schema schema.json --ats ashby|lever|indeed|wellfound
                     [--facts facts.json] [--out map.json]

Takes a schema dump (schema_dump.py / indeed_dump.py output shape) and
emits the same {"map": [...], "skipped_by_guard": [...], "unmapped": [...]}
shape as map.py, with ZERO model calls. The mapping is fully deterministic:
an ordered pattern table, stable answers from facts.json only.

Architecture:
- GUARD_RE and the work-auth / previously-employed / source-dedupe
  post-processors are imported from map.py, so guard behavior is identical
  to the model path. Sponsorship phrasing, background-check consent,
  drug-test consent, arbitration/certification checkboxes, and skills
  assessments always land in skipped_by_guard for Muse review.
- Each pattern is (name, regex, handler). The regex matches a label blob
  (label + option_label + key + type), the same blob map.py guards on.
  First matching pattern wins; unknown fields -> skip ("template-no-pattern").
- Option questions (Ashby yes/no buttons, radio groups, Lever same-name
  checkbox groups, demographic selects) are handled as one question: the
  option whose label matches the wanted answer is clicked/picked, the rest
  are skipped. Reuses map.dedupe_radio_clicks / dedupe_source_checkboxes.

Screening answers (standing facts only, from facts.json):
- years of experience -> numeric value; years selects -> best "N+"-style option.
- current title/employer and previous title/employer from facts.json.
- education (schools, degrees) from facts.json "education" entries.
- work authorization -> standard "Yes" patterns via map.work_auth_override
  (hardcoded, the model never decides this).
- email/phone from facts.json (phone digits only).
- salary expectation text from facts.json; selects pick the matching option.
- veteran/disability/ethnicity answers from facts.json; race/gender declined;
  pronouns from facts.json.
- signature: facts.json "full_name".
- self-assessment 1-10 scales -> "10".
- default YES on willingness (relocate, onsite/hybrid, travel, flexible start).
- AI-agent disclosure -> No. Previously employed here -> No.
- background-check consent, drug-test consent, assessments -> skip.
- Never invent experience/skills/credentials; tech-specific years -> skip.

Wellfound is PROVISIONAL: built against the logged-out job-page structure
documented from public sources; the logged-in apply dialog was not reachable
in testing. It is marked provisional in the output note.
"""
import argparse
import json
import os
import re
import sys

# Privacy-ack safety: legal-weight language must NEVER be auto-acknowledged.
# Privacy/data-processing acknowledgements are fine; arbitration, background
# checks, drug tests, assessments, certifications, and attestations always stop
# for human review.
ACK_BAD_RE = re.compile(r"arbitrat|background.?check|drug.?test|assessment|"
                        r"criminal|credit.?check|security.?clearance|"
                        r"true and correct|certif|attest",
                        re.IGNORECASE)
LEGAL_CONSENT_RE = ACK_BAD_RE

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from map import (GUARD_RE, apply_work_auth_overrides,
                 apply_prev_employed_override, dedupe_source_checkboxes,
                 dedupe_radio_clicks, coerce_action)
from fill import matches

HERE = os.path.dirname(os.path.abspath(__file__))

def _load_facts():
    for name in (os.environ.get("JOBPILOT_FACTS", ""),
                 os.path.join(HERE, "facts.json"),
                 os.path.join(HERE, "facts.example.json")):
        if name and os.path.isfile(name):
            try:
                with open(name) as fh:
                    return json.load(fh)
            except Exception:
                continue
    return {}

FACTS = _load_facts()

PROVISIONAL = {"wellfound"}

# Resolver result shapes:
#   ("do", action, value, note)  - ready-to-use action
#   ("opt", wants, note)         - pick the option whose label matches a want:
#                                   select -> pick option text; option group ->
#                                   click the matching option
#   None                          - pattern does not apply after all


def label_blob(f):
    return " ".join([f.get("label", ""), f.get("option_label", ""),
                     f.get("key", ""), f.get("type", ""),
                     f.get("question", "")])


def pick_option(field, wants):
    """Pick an option from field["options"] matching any want string.

    Exact case-insensitive first, then matches() contains. Returns the
    option TEXT (for select value) or None."""
    opts = field.get("options") or []
    for w in wants:
        wl = w.strip().lower()
        for o in opts:
            if o.strip().lower() == wl:
                return o
    for w in wants:
        for o in opts:
            if matches(w, o):
                return o
    return None


def group_id(f):
    """Stable question group id for option groups.

    Ashby: dfp (data-field-path). Lever same-name groups: name. Ashby
    yes/no buttons: dfp parsed from the key. Everything else: singleton."""
    if f.get("dfp"):
        return ("dfp", f["dfp"])
    if f.get("group"):
        return ("name", f["group"])
    if f.get("type") == "yesno-button":
        m = re.match(r'\[data-field-path="([^"]+)"\]', f.get("key", ""))
        if m:
            return ("dfp", m.group(1))
    return ("single", f["key"])


def qlabel(gfields):
    """Question label for a group: the dump's question text when present,
    else the longest non-empty label."""
    for f in gfields:
        q = (f.get("question") or "").strip()
        if q:
            return q
    cands = [f.get("label", "") for f in gfields if f.get("label")]
    return max(cands, key=len) if cands else ""


def group_options(gfields):
    """Return [(field, option_label)] for option-group members."""
    out = []
    for f in gfields:
        ol = f.get("option_label") or ""
        if f.get("type") == "yesno-button":
            m = re.search(r'data-option="([^"]+)"', f.get("key", ""))
            ol = (m.group(1) if m else ol) or (f.get("value") or "")
        out.append((f, ol))
    return out


def click_option_in_group(gfields, wants, note):
    """Pick the group field whose option label best matches wants.

    Returns {key: ("click", label, note)} for every member: the winner gets
    click, the rest get skip."""
    opts = group_options(gfields)
    win = None
    for w in wants:
        for f, ol in opts:
            if ol.strip().lower() == w.strip().lower():
                win = f
                break
        if win:
            break
    if not win:
        for w in wants:
            for f, ol in opts:
                if matches(w, ol):
                    win = f
                    break
            if win:
                break
    res = {}
    for f, ol in opts:
        if win and f["key"] == win["key"]:
            res[f["key"]] = ("click", ol or win["key"], note)
        else:
            res[f["key"]] = ("skip", "", "template-group-not-picked")
    return res


def opt_singleton(f, wants, note):
    """Resolve an ("opt", wants) directive for a non-grouped field."""
    ftype = (f.get("type") or "").lower()
    if ftype in ("select", "custom-select"):
        opt = pick_option(f, wants)
        if opt:
            return ("select", opt, note)
        if ftype == "custom-select" and not f.get("options"):
            # react-select style: options resolve at fill time from the
            # opened menu, so pass the top want and let the fill helper
            # match it (exact, then contains).
            return ("select", wants[0], note + "-menu-resolve")
        return ("skip", "", "template-no-matching-option")
    if ftype in ("checkbox", "radio"):
        ol = (f.get("option_label") or "").strip().lower()
        if any(ol == w.strip().lower() or matches(w, ol) for w in wants):
            return ("click", f.get("option_label") or "", note)
        return ("skip", "", "template-no-matching-option")
    return ("skip", "", "template-no-pattern")


# ---------------------------------------------------------------------------
# Answer resolvers: (field_or_probe, facts, ats) -> ("do"|"opt"|None, ...)
# ---------------------------------------------------------------------------

def r_full_name(f, facts, ats):
    return ("do", "fill", facts["full_name"], "identity")


def r_first_name(f, facts, ats):
    return ("do", "fill", facts["first_name"], "identity")


def r_last_name(f, facts, ats):
    return ("do", "fill", facts["last_name"], "identity")


def r_email(f, facts, ats):
    return ("do", "fill", facts["email"], "identity")


def r_phone(f, facts, ats):
    return ("do", "fill", facts["phone"], "identity")


def r_location(f, facts, ats):
    # Autocomplete comboboxes (Ashby, Wellfound downshift) need the
    # type+wait+pick-suggestion flow; plain text inputs (Lever) take a
    # direct fill (their suggestion pick clears free text on Enter).
    if (f.get("type") or "").lower() == "custom-select":
        return ("do", "location", facts["location"], "identity")
    return ("do", "fill", facts["location"], "identity")


def r_country(f, facts, ats):
    return ("opt", [facts["country"], "USA", "United States of America"],
            "identity")


def r_zip_code(f, facts, ats):
    return ("do", "fill",
            facts.get("zip_code") or facts.get("postal_code") or "",
            "identity")


def r_prev_employed(f, facts, ats):
    # "Have you previously worked here" -> facts, default No.
    want = (facts.get("previously_employed_here") or "No").strip()
    return ("opt", [want, "I have not", "No"], "prev-employed")


def r_work_eligible(f, facts, ats):
    # "Legally eligible to work" -> Yes (facts work_authorization).
    blob = label_blob(f)
    if ACK_BAD_RE.search(blob):
        return None
    return ("opt", ["Yes"], "work-eligible-yes")


def r_linkedin(f, facts, ats):
    return ("do", "fill", facts["linkedin"], "identity")


def r_github(f, facts, ats):
    return ("do", "fill", facts["github"], "identity")


def r_website(f, facts, ats):
    return ("do", "fill", facts["website"], "identity")


def r_resume(f, facts, ats):
    return ("do", "upload", facts["resume_path"], "identity")


def r_current_company(f, facts, ats):
    return ("do", "fill", facts["current_employer"], "employment")


def r_current_title(f, facts, ats):
    return ("do", "fill", facts["current_title"], "employment")


def r_years_numeric(f, facts, ats):
    # plain integer, never "8+" (some ATS reject non-numeric input)
    return ("do", "fill", "8", "years-experience")


# Years options, ordered by truthfulness for 8+ years of experience.
# "10+"-style options are EXCLUDED: he has 8+, not 10+; claiming 10+
# would invent experience. "8 Years" covers Wellfound's numeric options.
YEARS_WANTS = ["8+", "8-10", "7+", "7-10", "6+", "6-10", "5+", "5-10",
               "4+", "3+", "8 Years"]


def r_years_select(f, facts, ats):
    if ats == "wellfound":
        # Wellfound's years react-select does not open via standard
        # interactions (menu never renders); provisional: skip for manual.
        return ("do", "skip", "", "wellfound-years-select-manual")
    return ("opt", YEARS_WANTS, "years-experience")


def r_school(f, facts, ats):
    lab = (f.get("label") or "").lower()
    current = "current" in lab or "last attend" in lab
    schools = [e.get("school", "") for e in facts.get("education", [])
               if e.get("school")]
    # Prefer the current/most-recent school first, then the rest.
    ordered, seen = [], set()
    seq = schools if current else list(reversed(schools))
    for s in seq + schools:
        if s and s not in seen:
            ordered.append(s)
            seen.add(s)
    if f.get("type") in ("select", "custom-select"):
        return ("opt", ordered + ["Other (School Not Listed)", "Other"],
                "education")
    return ("do", "fill", ordered[0] if ordered else "", "education")


def r_degree(f, facts, ats):
    if f.get("type") in ("select", "custom-select"):
        return ("opt", ["Bachelor's", "Bachelor", "Bachelors",
                        "Undergraduate", "BS"], "education")
    return ("do", "fill", "BS Computer Science", "education")


def r_major(f, facts, ats):
    if f.get("type") == "select":
        return ("opt", ["Computer Science", "Computer science", "CS", "Other"],
                "education")
    return ("do", "fill", "Computer Science", "education")


def r_gradyear(f, facts, ats):
    if f.get("type") in ("select", "custom-select"):
        return ("opt", ["2021"], "education")
    return ("do", "fill", "2021", "education")


def r_edu_level(f, facts, ats):
    return ("opt", ["Bachelor's", "Bachelor", "Bachelors", "Undergraduate"],
            "education")


def r_skip(f, facts, ats):
    return ("do", "skip", "", "template-no-answer")


def r_salary_text(f, facts, ats):
    lab = (f.get("label") or "").lower()
    if re.search(r"current salary|previous salary|salary history", lab):
        return ("do", "skip", "", "salary-history-no-fact")
    if (f.get("type") or "").lower() == "number":
        # numeric salary field: midpoint of the facts.json salary range
        return ("do", "fill", str(facts.get("salary_midpoint", "")),
                "salary-midpoint-of-range")
    return ("do", "fill", facts["salary_expectation"], "salary")


def r_salary_select(f, facts, ats):
    opts = f.get("options") or []
    for o in opts:
        if "180" in o:
            return ("do", "select", o, "salary")
    return ("do", "skip", "", "salary-no-matching-band")


def r_self_assess(f, facts, ats):
    """1-10 proficiency scales -> "10" (his stated self-assessment is 9-10).

    Only fires when the options are numeric 1-10 or the field is numeric."""
    opts = [o.strip() for o in (f.get("options") or [])]
    if opts and all(re.fullmatch(r"\d{1,2}", o) for o in opts):
        if "10" in opts:
            return ("opt", ["10"], "self-assessment-9-10")
        return None
    if f.get("type") == "number":
        return ("do", "fill", "10", "self-assessment-9-10")
    return None


def r_yes(f, facts, ats):
    return ("opt", ["Yes"], "willingness-yes")


def r_no(f, facts, ats):
    return ("opt", ["No", "no"], "no")


def r_check(f, facts, ats):
    """Check a single opt-in checkbox (e.g. "I'm open to work remotely")."""
    return ("do", "click", f.get("option_label") or f.get("label") or "",
            "opt-in-check")


def r_password_skip(f, facts, ats):
    # Account-creation password fields: never invent or store passwords.
    return ("do", "skip", "", "account-password-needs-user")


def r_how_hear(f, facts, ats):
    return ("opt", [facts["job_source"]], "job-source")


def r_demo_veteran(f, facts, ats):
    return ("opt", ["I am not a veteran", "I do not identify as a veteran",
                    "not a veteran", "No", "I AM NOT A VETERAN"],
            "demographics")


def r_demo_disability(f, facts, ats):
    return ("opt", ["I don't have a disability", "I do not have a disability",
                    "no disability", "No", "NO"], "demographics")


def r_demo_ethnicity(f, facts, ats):
    return ("opt", ["not Hispanic or Latino", "No, not Hispanic or Latino",
                    "Not Hispanic or Latino", "NO"], "demographics")


DECLINE_WANTS = ["Decline to self-identify", "I prefer not to answer",
                 "Prefer not to say", "Decline", "Choose not to disclose",
                 "I don't wish to answer"]


def r_demo_race(f, facts, ats):
    return ("opt", DECLINE_WANTS, "demographics")


def r_demo_gender(f, facts, ats):
    return ("opt", DECLINE_WANTS, "demographics")


def r_demo_pronouns(f, facts, ats):
    return ("opt", [facts["pronouns"], "Use name only", "did not provide",
                    "Prefer not to disclose", "Decline to self-identify"],
            "demographics")


def r_signature(f, facts, ats):
    return ("do", "fill", facts["full_name"], "signature")


def r_sponsorship(f, facts, ats):
    # Work-auth sponsorship: pick the "will require sponsorship" option.
    # Truthful per facts.json work_authorization; never invent status.
    return ("opt", ["Yes, but will require sponsorship in the future",
                    "will require sponsorship", "Yes, I will require sponsorship"],
            "work-auth-sponsorship")


def r_verify_identity(f, facts, ats):
    # "Can you provide verification of your identity upon hire?" -> Yes.
    return ("opt", ["Yes"], "identity-verification-yes")


def r_authorized_yes(f, facts, ats):
    # "Legally authorized to work" -> Yes (facts work_authorization).
    # Excludes "without sponsorship" phrasing (handled by sponsorship).
    blob = label_blob(f)
    if ACK_BAD_RE.search(blob):
        return None
    return ("opt", ["Yes"], "work-auth-authorized-yes")


def r_privacy_ack_select(f, facts, ats):
    blob = label_blob(f)
    if ACK_BAD_RE.search(blob):
        return None
    return ("opt", ["Yes", "I agree", "Acknowledge"], "privacy-ack")


def r_privacy_ack_check(f, facts, ats):
    blob = label_blob(f)
    if ACK_BAD_RE.search(blob):
        return None
    return ("do", "click", f.get("option_label") or f.get("label") or "",
            "privacy-ack-check")


def _edu_entry(facts, current=False):
    """Pick the education entry: current/in-progress or the most recent."""
    edu = facts.get("education") or []
    if current:
        for e in edu:
            if "progress" in str(e.get("end_year", "")).lower():
                return e
    return edu[0] if edu else {}


def r_edu_month(f, facts, ats, which):
    # Education start/end month from facts education entries.
    e = _edu_entry(facts)
    mon = e.get("start_month" if which == "start" else "end_month", "")
    if not mon:
        return None
    return ("opt", [mon, mon[:3]], "edu-date")


def r_edu_year(f, facts, ats, which):
    e = _edu_entry(facts)
    yr = e.get("start_year" if which == "start" else "end_year", "")
    if not yr or "progress" in str(yr).lower():
        return None
    return ("do", "fill", str(yr), "edu-date")


def r_discipline(f, facts, ats):
    # Field of study / discipline from facts education entries.
    majors = []
    for e in facts.get("education") or []:
        deg = str(e.get("degree", ""))
        for part in re.split(r"[,&/]", deg):
            part = part.strip()
            if part and part not in majors:
                majors.append(part)
    if not majors:
        return None
    return ("opt", majors + ["Other"], "discipline")


def r_inoffice(f, facts, ats):
    # In-office / hybrid / onsite willingness: facts-driven, default yes.
    ftype = (f.get("type") or "").lower()
    if ftype in ("select", "custom-select"):
        return ("opt", ["Yes, in-office", "onsite", "I already live",
                        "willing to relocate"],
                "willing-in-office")
    return ("do", "fill", facts.get("willing_in_office") or
            "Yes, I am willing to work in-office.", "willing-in-office")


def _essay(facts, key):
    """Essay text from facts; None (skip) when the user has not provided it.
    Essays are never invented."""
    txt = (facts.get("essays") or {}).get(key, "")
    if txt and txt.strip():
        return txt.strip()
    return None


def r_essay_llm(f, facts, ats):
    txt = _essay(facts, "llm_experience")
    if not txt:
        return None
    return ("do", "fill", txt, "essay-llm")


def r_essay_devtools(f, facts, ats):
    txt = _essay(facts, "devtools_experience")
    if not txt:
        return None
    return ("do", "fill", txt, "essay-devtools")


def r_essay_why(f, facts, ats):
    txt = _essay(facts, "why_company")
    if not txt:
        return None
    return ("do", "fill", txt, "essay-why")


def r_military_no(f, facts, ats):
    # Not a veteran; not military spouse; not National Guard/Reserves.
    return ("opt", ["No", "I am not a veteran", "not a veteran"],
            "military-no")


def r_sms_optout(f, facts, ats):
    # SMS outreach consent: opt out (do not consent to marketing texts).
    return ("opt", ["Opt-Out", "Opt out", "No", "Decline"], "sms-opt-out")


def r_salary_numeric(f, facts, ats):
    # Salary expectation: numeric midpoint of the facts.json salary range.
    return ("do", "fill", str(facts.get("salary_midpoint", "")),
            "salary-expectation-numeric")


# Pattern table: (name, regex, handler, applies_to_types_or_None).
# Handler returns ("do", action, value, note) | ("opt", wants, note) | None.
# types=None means the handler decides (used for group probes and selects).
PATTERNS = [
    # identity (full name before first/last)
    ("full-name", re.compile(r"full.?name", re.I), r_full_name, {"text"}),
    ("first-name", re.compile(r"first.?name|given.?name|preferred.?name",
                              re.I), r_first_name, {"text"}),
    ("last-name", re.compile(r"last.?name|family.?name|surname", re.I),
     r_last_name, {"text"}),
    ("email-confirm", re.compile(r"confirm.*e-?mail", re.I), r_email,
     {"text", "email"}),
    ("email", re.compile(r"e-?mail", re.I), r_email, {"text", "email"}),
    ("phone", re.compile(r"phone|mobile|telephone", re.I), r_phone,
     {"text", "tel"}),
    ("location", re.compile(r"your location|current.?location|\blocation\b|"
                             r"\bcity\b|downshift", re.I),
     r_location, {"text", "custom-select"}),
    ("country", re.compile(r"\bcountry\b", re.I), r_country, {"select"}),
    ("zip-code", re.compile(r"zip.?code|postal.?code", re.I), r_zip_code,
     {"text", "number"}),
    ("prev-employed", re.compile(r"have you (ever |previously )?(worked|been employed) (at|for|with)|"
                                 r"previously (worked|been employed)|worked here before", re.I),
     r_prev_employed, {"select", "custom-select", "radio", "yesno-button"}),
    ("work-eligible", re.compile(r"legally eligible|eligible to work", re.I),
     r_work_eligible, {"select", "custom-select"}),
    ("linkedin", re.compile(r"linkedin", re.I), r_linkedin, {"text", "url"}),
    ("github", re.compile(r"github", re.I), r_github, {"text", "url"}),
    ("website", re.compile(r"portfolio|personal website|^website$|"
                           r"other website", re.I), r_website, {"text", "url"}),
    ("resume", re.compile(r"resume|\bcv\b|curriculum vitae", re.I), r_resume,
     {"file"}),
    # employment
    ("current-company", re.compile(r"current (company|employer)|employer name",
                                   re.I), r_current_company, {"text"}),
    ("current-title", re.compile(r"current (job )?title|job title", re.I),
     r_current_title, {"text"}),
    # years of experience: generic total only (tech-specific -> skip).
    # The .? forms also match key-embedded ids like "yearsOfExperience".
    ("years-numeric", re.compile(
        r"how many years.*(professional|work|total).*experience|"
        r"years.?of.?(professional |work |total )?experience(?! with)|"
        r"total.*years.*experience", re.I), r_years_numeric,
     {"text", "number", "tel"}),
    ("years-select", re.compile(
        r"how many years.*(professional|work|total).*experience|"
        r"years.?of.?(professional |work |total )?experience(?! with)|"
        r"total.*years.*experience", re.I), r_years_select,
     {"select", "custom-select"}),
    ("years-tech", re.compile(r"years.*experience.*with|experience.*with|"
                              r"years of .* (python|java|go|aws|kubernetes|"
                              r"react|node|sql|gcp|azure)", re.I), r_skip, None),
    # education
    ("edu-level", re.compile(r"highest.*(education|degree)|education level|"
                             r"degree.*completed", re.I), r_edu_level,
     {"select"}),
    ("school", re.compile(r"university|school|college", re.I), r_school,
     {"text", "select", "custom-select"}),
    ("degree", re.compile(r"\bdegree\b", re.I), r_degree,
     {"text", "select", "custom-select"}),
    ("major", re.compile(r"\bmajor\b|field of study|discipline", re.I),
     r_major, {"text", "select"}),
    ("gradyear", re.compile(r"graduation|end year|class of|year.*graduat",
                            re.I), r_gradyear, {"text", "select", "number"}),
    ("discipline", re.compile(r"\bdiscipline\b|field of study", re.I),
     r_discipline, {"select", "custom-select"}),
    ("edu-start-month", re.compile(r"start.*month|month.*start", re.I),
     lambda f, facts, ats: r_edu_month(f, facts, ats, "start"),
     {"select", "custom-select"}),
    ("edu-end-month", re.compile(r"end.*month|month.*end", re.I),
     lambda f, facts, ats: r_edu_month(f, facts, ats, "end"),
     {"select", "custom-select"}),
    ("edu-start-year", re.compile(r"start.*year|year.*start", re.I),
     lambda f, facts, ats: r_edu_year(f, facts, ats, "start"),
     {"text", "number"}),
    ("edu-end-year", re.compile(r"end.*year|year.*end", re.I),
     lambda f, facts, ats: r_edu_year(f, facts, ats, "end"),
     {"text", "number"}),
    ("gpa", re.compile(r"\bgpa\b|grade point", re.I), r_skip, None),
    # account creation: never invent or store passwords
    ("password", re.compile(r"password", re.I), r_password_skip, None),
    # opt-in checkboxes with a positive statement
    ("remote-ok", re.compile(r"open to.*remote|willing.*remote", re.I),
     r_check, {"checkbox"}),
    # willingness: default YES (his Oct 1 posture)
    ("willing-relocate", re.compile(r"relocat|willing to (move|change "
                                    r"location)", re.I), r_yes, None),
    ("willing-onsite", re.compile(r"on-?site|in.?office|in.?person|hybrid",
                                  re.I), r_yes, None),
    ("willing-travel", re.compile(r"\btravel\b", re.I), r_yes, None),
    ("willing-commute", re.compile(r"\bcommute\b", re.I), r_yes, None),
    ("inoffice-willingness", re.compile(r"in-office|in office|on-site|onsite|hybrid.*prefer|"
                                        r"work.*five days.*office|office.*five days", re.I),
     r_inoffice, {"text", "textarea", "select", "custom-select"}),
    ("willing-start-flex", re.compile(r"flexible.*start|start.*flexible|"
                                      r"available.*start", re.I), r_yes, None),
    ("start-date", re.compile(r"when can you start|start date", re.I),
     r_skip, None),
    # salary
    ("salary-text", re.compile(r"salary|compensation|pay expectation|"
                               r"desired.*(pay|salary)", re.I), r_salary_text,
     {"text", "number"}),
    ("salary-select", re.compile(r"salary|compensation", re.I),
     r_salary_select, {"select"}),
    # demographics
    ("demo-veteran", re.compile(r"veteran", re.I), r_demo_veteran, None),
    ("demo-disability", re.compile(r"disabilit", re.I), r_demo_disability,
     None),
    ("demo-ethnicity", re.compile(r"hispanic|ethnicity", re.I),
     r_demo_ethnicity, None),
    ("demo-race", re.compile(r"race|racial", re.I), r_demo_race, None),
    ("demo-gender", re.compile(r"gender", re.I), r_demo_gender, None),
    ("demo-pronouns", re.compile(r"pronouns", re.I), r_demo_pronouns, None),
    # sponsorship-required work auth (Indeed truncated phrasing) -> pick the
    # truthful sponsorship option, never plain "Yes"
    ("workauth-sponsorship",
     re.compile(r"sponsorship in the future|will require sponsorship|need.*sponsorship", re.I),
     r_sponsorship, None),
    # identity verification upon hire -> Yes
    ("verify-identity",
     re.compile(r"verification of your (identity|identify) upon hire|verify.*identity", re.I),
     r_verify_identity, None),
    # "legally authorized to work" (Ashby yes/no buttons, radio groups) ->
    # Yes per facts. Excludes "without sponsorship" phrasing.
    ("workauth-authorized",
     re.compile(r"(legally )?authorized to work(?!.*without sponsorship)|"
                r"have authorization to work", re.I),
     r_authorized_yes, None),
    # currently living in the country -> Yes
    ("currently-live-us",
     re.compile(r"currently live in the (united states|us|u\.s\.)|currently reside in the (united states|us)", re.I),
     r_yes, {"radio", "yesno-button", "select", "custom-select"}),
    # privacy/data-processing acknowledgements -> acknowledge. Legal-weight
    # language (arbitration, background checks, ...) is refused by the
    # resolver and stays guarded for human review.
    ("privacy-ack-select", re.compile(r"acknowledg|privacy notice|gdpr disclosure", re.I),
     r_privacy_ack_select, {"select", "custom-select"}),
    ("privacy-ack-check", re.compile(r"acknowledg.*privacy|privacy.*acknowledg|"
                                     r"read and (acknowledge|understood).*privacy", re.I),
     r_privacy_ack_check, {"checkbox", "radio", "yesno-button"}),
    # military spouse / National Guard / Reserves -> No (not a veteran)
    ("military-no",
     re.compile(r"military spouse|national guard|reserves|armed forces", re.I),
     r_military_no, None),
    # SMS outreach consent -> Opt-Out
    ("sms-consent",
     re.compile(r"via sms|sms.*application|reach out.*via.*sms|message and data rates", re.I),
     r_sms_optout, None),
    # salary expectation (numeric) -> facts.json salary_midpoint
    ("salary-expect-numeric",
     re.compile(r"salary expectation|expected salary|base salary.*expect", re.I),
     r_salary_numeric, {"text", "number"}),
    # sourcing
    ("how-hear", re.compile(r"how did you hear|how.*hear.*about", re.I),
     r_how_hear, None),
    # referral: facts only name an xAI referrer; any other company -> No
    ("referral-yn", re.compile(r"\breferr", re.I), r_no, None),
    ("referral-name", re.compile(r"referrer.?name|referred by|referral name",
                                 re.I), r_skip, None),
    # AI agent disclosure -> No (standing policy)
    ("ai-disclosure", re.compile(r"ai agent|ai assistant|artificial "
                                 r"intelligence.*(agent|assist)|used.*ai.*"
                                 r"(apply|application)", re.I), r_no, None),
    # self-assessment 1-10 scales
    ("self-assess", re.compile(r"rate your|proficiency|scale of 1|"
                               r"1\s*(-|to)\s*10|out of 10", re.I),
     r_self_assess, None),
    # signature
    ("signature", re.compile(r"signature|type your (full )?name.*(sign|"
                             r"certif)", re.I), r_signature, {"text"}),
    # subjective essays: skip
    # targeted essays: answered from facts["essays"], never invented.
    # The generic "essay" pattern below skips anything these miss.
    ("essay-llm", re.compile(r"(llm|large language model|agent framework|"
                             r"tool-?calling|workflow orchestration)", re.I),
     r_essay_llm, {"text", "textarea"}),
    ("essay-devtools", re.compile(r"experience.*(developer-?facing|developer tools|"
                                  r"\bapis?\b|\bsdks?\b|\bclis?\b).*build", re.I),
     r_essay_devtools, {"text", "textarea"}),
    ("essay-why", re.compile(r"what excites you|why do you want to work|"
                             r"why.*this (company|role|opportunity)", re.I),
     r_essay_why, {"text", "textarea"}),
    ("essay", re.compile(r"why (do you want|are you|this company)|"
                         r"motivat|cover letter|favorite project|"
                         r"proudest accomplishment|describe your experience|"
                         r"tell us about yourself", re.I), r_skip, None),
    ("additional-info", re.compile(r"additional information|anything else|"
                                   r"is there anything", re.I), r_skip, None),
    ("twitter", re.compile(r"twitter|\bx\b.*handle", re.I), r_skip, None),
    ("name-pronounce", re.compile(r"pronunciation", re.I), r_skip, None),
    ("security-clearance", re.compile(r"security clearance|clearance level",
                                      re.I), r_skip, None),
    # file inputs the dump could not label (e.g. Wellfound's id-less upload):
    # the only file asked for on application forms is the resume.
    ("file-fallback", re.compile(r"\bfile\b", re.I), r_resume, {"file"}),
]


def match_pattern(f, facts, ats):
    """Return (pattern_name, directive, payload, note) for a field/probe.

    directive "do" -> payload is (action, value); directive "opt" ->
    payload is a wants list. None when no pattern matches."""
    blob = label_blob(f)
    ftype = (f.get("type") or "").lower()
    for name, rx, handler, types in PATTERNS:
        if types is not None and ftype not in types:
            continue
        if not rx.search(blob):
            continue
        res = handler(f, facts, ats)
        if res is None:
            continue
        # "do" resolvers return ("do", action, value, note);
        # "opt" resolvers return ("opt", wants, note).
        directive = res[0]
        if directive == "do":
            payload, note = (res[1], res[2]), res[3]
        else:
            payload, note = res[1], res[2]
        return (name, directive, payload, note)
    return None


def resolve_directive(f, directive, payload, note, facts, ats):
    """Turn a matched directive into a concrete (action, value, note)."""
    if directive == "do":
        action, value = payload
        return (action, value, note)
    if directive == "opt":
        return opt_singleton(f, payload, note)
    return ("skip", "", "template-no-pattern")


def map_template(schema, facts, ats="unknown"):
    fields = schema.get("fields", [])
    by_key = {f["key"]: f for f in fields}
    entries = {}
    guarded = []

    # Normalize: react-select inner inputs dump as type text; treat them as
    # custom-select so the select patterns (and fill_react_select) apply.
    for f in fields:
        if (f.get("type") == "text" and
                "react-select" in f.get("key", "").lower()):
            f["type"] = "custom-select"

    def add(key, action, value, note="", guard=False):
        e = {"field": key, "action": action, "value": value}
        if note:
            e["note"] = note
        if guard:
            e["guard"] = "muse-review"
            guarded.append(e)
        entries[key] = e

    # 1) guard pass: identical to map.py (model never decides these).
    # Narrow exceptions:
    # - "will require sponsorship" option: truthful standing-fact answer
    #   per facts.json work_authorization; handled by the
    #   workauth-sponsorship pattern.
    # - SMS outreach consent mentioning "Visa" (the company): not a work-auth
    #   visa question; handled by the sms-consent pattern (Opt-Out).
    SPONSORSHIP_OPT_GUARD_EXC = re.compile(
        r"will require sponsorship|sponsorship in the future|"
        r"will require .*sponsor|require employment visa sponsorship|"
        r"require.*sponsors|future.*sponsor", re.IGNORECASE)
    SMS_CONSENT_GUARD_EXC = re.compile(
        r"via sms|sms.*application|message and data rates", re.IGNORECASE)
    # "Legally authorized to work" has a deterministic facts-driven answer
    # (Yes); exempt it from guard so the workauth-authorized pattern
    # answers it. Keep guarding the "without sponsorship" variant.
    AUTHORIZED_YES_GUARD_EXC = re.compile(
        r"legally authorized to work(?!.*without sponsorship)|have authorization to work",
        re.IGNORECASE)
    # Privacy-ack checkboxes have a deterministic answer (acknowledge);
    # exempt so the privacy-ack-check pattern answers them. Legal-weight
    # language stays guarded via ACK_BAD_RE in the resolver.
    PRIVACY_ACK_GUARD_EXC = re.compile(
        r"acknowledg.*privacy|privacy.*acknowledg|read and (acknowledge|understood).*privacy",
        re.IGNORECASE)
    for f in fields:
        blob = label_blob(f)
        if ACK_BAD_RE.search(blob):
            add(f["key"], "skip", "", guard=True)
            continue
        if GUARD_RE.search(blob):
            if SPONSORSHIP_OPT_GUARD_EXC.search(blob):
                continue  # handled by the workauth-sponsorship pattern
            if SMS_CONSENT_GUARD_EXC.search(blob):
                continue  # handled by the sms-consent pattern (Opt-Out)
            if AUTHORIZED_YES_GUARD_EXC.search(blob):
                continue  # handled by the workauth-authorized pattern (Yes)
            if PRIVACY_ACK_GUARD_EXC.search(blob):
                continue  # handled by the privacy-ack-check pattern
            add(f["key"], "skip", "", guard=True)

    # 2) group questions; answer per group or per singleton
    groups = {}
    for f in fields:
        gid = group_id(f)
        groups.setdefault(gid, []).append(f)
    for gid, gfields in groups.items():
        if any(f["key"] in entries and entries[f["key"]].get("guard") for f in gfields):
            for gf in gfields:
                if gf["key"] not in entries:
                    add(gf["key"], "skip", "", guard=True)
            continue
        if all(f["key"] in entries for f in gfields):
            continue
        kind, _ = gid
        if kind in ("dfp", "name"):
            q = qlabel(gfields)
            probe = {"label": q, "option_label": "",
                     "key": gfields[0]["key"], "type": "radio"}
            m = match_pattern(probe, facts, ats)
            handled = False
            if m:
                _, directive, payload, note = m
                if directive == "opt":
                    for k, (a, v, n) in click_option_in_group(
                            gfields, payload, note).items():
                        add(k, a, v, note=n)
                    handled = True
                elif directive == "do":
                    action, value = payload
                    if action == "skip":
                        for gf in gfields:
                            if gf["key"] not in entries:
                                add(gf["key"], "skip", "", note=note)
                        handled = True
            if not handled:
                # no group-level pattern: try per-field, else skip
                for gf in gfields:
                    if gf["key"] in entries:
                        continue
                    m2 = match_pattern(gf, facts, ats)
                    if m2:
                        _, directive, payload, note = m2
                        a, v, n = resolve_directive(gf, directive, payload,
                                                    note, facts, ats)
                        add(gf["key"], a, v, note=n)
                    else:
                        add(gf["key"], "skip", "",
                            note="template-no-pattern")
        else:
            f = gfields[0]
            if f["key"] in entries:
                continue
            m = match_pattern(f, facts, ats)
            if m:
                _, directive, payload, note = m
                a, v, n = resolve_directive(f, directive, payload, note,
                                            facts, ats)
                add(f["key"], a, v, note=n)
            else:
                add(f["key"], "skip", "", note="template-no-pattern")

    mapped = list(entries.values())

    # 3) deterministic overrides (same as the model path, same order).
    # Promote the dump's group question text to the label so the
    # work-auth / previously-employed matchers see the real question
    # (Wellfound option labels are bare "Yes"/"No"); restore after.
    saved_labels = {}
    for f in fields:
        q = (f.get("question") or "").strip()
        if q and len(q) > len(f.get("label", "")):
            saved_labels[f["key"]] = f.get("label", "")
            f["label"] = q
    mapped = apply_work_auth_overrides(mapped, schema)
    mapped = apply_prev_employed_override(mapped, schema, facts)
    for f in fields:
        if f["key"] in saved_labels:
            f["label"] = saved_labels[f["key"]]
    mapped = dedupe_source_checkboxes(mapped, schema, facts)
    mapped = dedupe_radio_clicks(mapped, schema)

    # 4) coerce action/type consistency per field
    for e in mapped:
        f = by_key.get(e["field"], {})
        if f and e["action"] != "skip":
            coerce_action(f, e)

    unmapped = [f["key"] for f in fields if f["key"] not in entries]
    note = "frozen-template:" + ats
    if ats in PROVISIONAL:
        note += " (provisional)"
    return {"map": mapped, "skipped_by_guard": guarded,
            "unmapped": unmapped, "note": note}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--schema", required=True)
    ap.add_argument("--ats", required=True,
                    choices=["ashby", "lever", "indeed", "wellfound"])
    ap.add_argument("--facts", default=os.path.join(HERE, "facts.json"))
    ap.add_argument("--out", default="")
    a = ap.parse_args()
    schema = json.load(open(a.schema))
    facts = json.load(open(a.facts))
    result = map_template(schema, facts, a.ats)
    n_map = len([e for e in result["map"] if e["action"] != "skip"])
    n_skip = len([e for e in result["map"] if e["action"] == "skip"])
    s = json.dumps(result, indent=1)
    if a.out:
        open(a.out, "w").write(s)
        print(f"wrote {a.out} ({n_map} mapped, {n_skip} skipped, "
              f"{len(result['skipped_by_guard'])} guarded, "
              f"{len(result['unmapped'])} unmapped) [{result['note']}]")
    else:
        print(s)


if __name__ == "__main__":
    main()
