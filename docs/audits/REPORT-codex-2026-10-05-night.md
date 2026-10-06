# Codex audit report - 2026-10-05 night

**Scope:** `fill.py` `do_select` react-select detection fix (CDP RemoteObject
dict vs string comparison), plus consistency check across the harness.
**Model:** gpt-6.1-sol (Codex 0.160.1) on summits-mac-mini. Audit-only, no files modified.
**Verdict: APPROVE**

## Method

Codex traced the CDP return paths through the local harness drivers,
ran the normalization logic against 17 in-memory cases (bare strings,
RemoteObject dicts, numbers, booleans, null, lists, missing `value`,
nested dicts, bytes - no crashes, only string `"rs"` selects the React
helper), and grepped the harness for similar comparisons.

## Findings

- **Minor, fill.py (comment over the fix):** The comment overstated
  transport behavior. The inspected harness drivers already unwrap
  successful evaluations via `res.get("value", res)`, so with those
  drivers `"rs"` arrives as a bare string.
  **Resolution:** Reworded the fix as a defensive `_cdp_value()`
  helper that accepts both bare values and raw RemoteObject dicts,
  instead of asserting the dict shape is what always arrives.
- **Consistency note:** Other direct string comparisons existed at
  fill.py:243, 254, 267 (submit-button detectors comparing
  `r.get("result") == "ok"`). They work with unwrapping drivers, but
  would need the same normalization under a raw-dict driver.
  **Resolution:** Applied `_cdp_value()` at all three sites, plus the
  location-settle poll and two `.get()`-on-result sites, so every
  `Runtime.evaluate` comparison in `fill.py` is now driver-agnostic.
- **Selector note:** `.select__control` depends on the widget's class
  naming; a different class prefix can miss detection. Existing
  constraint, documented for Greenhouse, not a regression. No change.

## Verdict

**APPROVE.** No functional defect in the normalization; all edge cases
pass; the consistency hardening is applied.
