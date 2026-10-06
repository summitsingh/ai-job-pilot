#!/usr/bin/env python3
"""Post-fill location verifier: did the autocomplete pick the intended city?

Location autocompletes can pick a lookalike. A real run once picked
"Austintown, Ohio" when the target was "Austin, Texas", and the lane would
have submitted it blindly. This module compares the field's FINAL value
(read back after the fill) against an allowlist of target metros and fails
closed on any doubt.

Target metro descriptors (JOBPILOT_TARGET_METROS, see config.example.json):
  "Austin, TX"      city + state: the value must contain BOTH the city and
                    the state (abbreviation or full name)
  "Remote (US)"     remote: the value must say remote and, when a
                    qualifier is given, carry that qualifier ("US")
The env var is a semicolon-separated list ("Austin, TX; Remote (US)"). A
JSON array in config.json also works. A plain comma list is accepted too:
a state fragment after a comma is glued back onto the preceding city.

THE TRAP: plain substring matching on "austin" accepts "Austintown, Ohio".
City tokens are matched as whole words, and the state token is required,
so "Austintown" fails on the word boundary and "Austin, Minnesota" fails on
the state.

Fail closed: an empty value, an empty target list, or a descriptor that
carries no state and is not a remote entry never matches.

Usage:
  python3 location_check.py "Austin, Texas" --metros "Austin, TX; Remote (US)"
"""
import argparse
import json
import re
import sys

from config import get

DEFAULT_METROS = ""  # Operators must configure applicant locations before claims.

STATES = {
    "al": "alabama", "ak": "alaska", "az": "arizona", "ar": "arkansas",
    "ca": "california", "co": "colorado", "ct": "connecticut",
    "de": "delaware", "dc": "district of columbia", "fl": "florida",
    "ga": "georgia", "hi": "hawaii", "id": "idaho", "il": "illinois",
    "in": "indiana", "ia": "iowa", "ks": "kansas", "ky": "kentucky",
    "la": "louisiana", "me": "maine", "md": "maryland",
    "ma": "massachusetts", "mi": "michigan", "mn": "minnesota",
    "ms": "mississippi", "mo": "missouri", "mt": "montana",
    "ne": "nebraska", "nv": "nevada", "nh": "new hampshire",
    "nj": "new jersey", "nm": "new mexico", "ny": "new york",
    "nc": "north carolina", "nd": "north dakota", "oh": "ohio",
    "ok": "oklahoma", "or": "oregon", "pa": "pennsylvania",
    "ri": "rhode island", "sc": "south carolina", "sd": "south dakota",
    "tn": "tennessee", "tx": "texas", "ut": "utah", "vt": "vermont",
    "va": "virginia", "wa": "washington", "wv": "west virginia",
    "wi": "wisconsin", "wy": "wyoming",
}
_NAME_TO_ABBR = {v: k for k, v in STATES.items()}


def _norm(s):
    """Lowercase, collapse punctuation to single spaces, unify US aliases."""
    s = (s or "").lower().replace("u.s.", "us")
    s = re.sub(r"[^a-z0-9]+", " ", s).strip()
    s = re.sub(r"\bunited states( of america)?\b|\busa\b", "us", s)
    return " " + s + " "


def _has_phrase(norm_value, phrase):
    """Whole-word phrase match on normalized text (no substring hits)."""
    p = _norm(phrase).strip()
    return bool(p) and (" " + p + " ") in norm_value


def _state_abbr(token):
    t = _norm(token).strip()
    if t in STATES:
        return t
    return _NAME_TO_ABBR.get(t)


def parse_metro(desc):
    """Descriptor -> {"kind": "remote"|"city", ...} or None if unusable."""
    d = (desc or "").strip()
    if not d:
        return None
    if re.search(r"\bremote\b", d, re.I):
        m = re.search(r"\(([^)]*)\)", d)
        qual = _norm(m.group(1)).strip() if m else ""
        return {"kind": "remote", "qualifier": qual, "desc": d}
    parts = [p.strip() for p in d.split(",") if p.strip()]
    if len(parts) >= 2 and _state_abbr(parts[1]):
        return {"kind": "city", "city": parts[0],
                "state": _state_abbr(parts[1]), "desc": d}
    words = d.split()
    if len(words) >= 2 and _state_abbr(words[-1]):
        return {"kind": "city", "city": " ".join(words[:-1]),
                "state": _state_abbr(words[-1]), "desc": d}
    return None  # no state token: never matches (see module docstring)


def parse_metros(raw):
    """Config value (list, semicolon string, or comma string) -> [str]."""
    if raw is None:
        raw = DEFAULT_METROS
    if isinstance(raw, (list, tuple)):
        return [str(x).strip() for x in raw if str(x).strip()]
    s = str(raw).strip()
    if s.startswith("["):
        return parse_metros(json.loads(s))
    if ";" in s:
        return [p.strip() for p in s.split(";") if p.strip()]
    out = []
    for p in (x.strip() for x in s.split(",") if x.strip()):
        if out and _state_abbr(p) and parse_metro(out[-1]) is None:
            out[-1] = out[-1] + ", " + p
        else:
            out.append(p)
    return out


def validate_metros(raw):
    """Validate configuration once before claiming any jobs."""
    try:
        descriptors = parse_metros(raw)
    except (ValueError, TypeError) as e:
        raise ValueError("invalid target metros JSON: %s" % e) from e
    bad = [desc for desc in descriptors if parse_metro(desc) is None]
    for desc in bad:
        print("WARNING: unusable location descriptor: %r" % desc, file=sys.stderr)
    if bad or not descriptors:
        raise ValueError("unusable target metros; use City, ST or Remote (US): %s" % "; ".join(bad))
    return descriptors


def load_target_metros(cfg=None):
    """Env var JOBPILOT_TARGET_METROS > config.json > empty (fail closed)."""
    return parse_metros(get("JOBPILOT_TARGET_METROS", DEFAULT_METROS, cfg))


def verify_location(field_value, target_metros):
    """Check a filled location value against the target metros.

    Returns {"ok": bool, "reason": str}. ok=False means flag for review,
    do not submit.
    """
    metros = parse_metros(target_metros) if not isinstance(
        target_metros, (list, tuple)) else list(target_metros)
    value = (field_value or "").strip()
    if not value:
        return {"ok": False,
                "reason": "location value is empty; expected one of: "
                          + "; ".join(metros)}
    if not metros:
        return {"ok": False,
                "reason": "no target metros configured; cannot verify "
                          "location %r" % value}
    if re.search(r"\b(?:not|except|excluding)\b", value, re.I):
        return {"ok": False, "reason": "negated location requires human review: " + value}
    # Lowercase "or" separates alternatives, except after a comma (state
    # context). Uppercase OR is a state abbreviation, not a separator.
    segments = re.split(r";|\||(?<![,\w])\s+or\s+", re.sub(r"\s+", " ", value))
    for desc in metros:
        m = parse_metro(desc)
        if m is None:
            print("WARNING: unusable location descriptor: %r" % desc, file=sys.stderr)
            continue
        for segment in segments:
            nv = _norm(segment)
            if m["kind"] == "remote":
                if _has_phrase(nv, "remote") and (not m["qualifier"] or _has_phrase(nv, m["qualifier"])):
                    return {"ok": True, "reason": "matched " + m["desc"]}
            else:
                # Whole city phrase immediately followed by state tokens.
                if _has_phrase(nv, m["city"] + " " + STATES[m["state"]]):
                    return {"ok": True, "reason": "matched " + m["desc"]}
                abbr = m["state"]
                if _has_phrase(nv, m["city"] + " " + abbr):
                    if abbr in {"or", "in", "me", "ok", "hi"}:
                        city = re.escape(m["city"])
                        clear_state = (re.search(r"\b" + city + r"\s*,\s*" + abbr + r"\b", segment, re.I)
                                       or re.search(r"\b(?i:" + city + r")\s+" + abbr.upper() + r"\b", segment))
                        if not clear_state:
                            continue
                    return {"ok": True, "reason": "matched " + m["desc"]}
    return {"ok": False,
            "reason": "location %r does not match expected metros: %s"
                      % (value, "; ".join(metros))}


def verify_location_component(value, target_metros, component):
    """Check a split city/state/country value against configured descriptors."""
    normalized = _norm(value).strip()
    allowed = set()
    for desc in parse_metros(target_metros):
        metro = parse_metro(desc)
        if metro is None:
            continue
        if component == "city" and metro["kind"] == "city":
            allowed.add(_norm(metro["city"]).strip())
        elif component == "state" and metro["kind"] == "city":
            allowed.update((metro["state"], STATES[metro["state"]]))
        elif component == "country":
            if metro["kind"] == "city":
                allowed.add("us")  # City descriptors use the US STATES table.
            elif metro["qualifier"]:
                allowed.add(metro["qualifier"])
    ok = bool(normalized and normalized in allowed)
    return {"ok": ok, "reason": ("matched applicant " + component if ok else
             "applicant %s %r does not match configured metros" % (component, value))}


def main():
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("value")
    ap.add_argument("--metros", default=None,
                    help="override JOBPILOT_TARGET_METROS")
    a = ap.parse_args()
    metros = parse_metros(a.metros) if a.metros else load_target_metros()
    res = verify_location(a.value, metros)
    print(json.dumps(res))
    sys.exit(0 if res["ok"] else 1)


if __name__ == "__main__":
    main()
