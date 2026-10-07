"""Fail-closed audit between form filling and any submit click."""


def audit_form(initial, live, mapping, mismatched, invalid=()):
    """Return blockers for newly required, skipped, mismatched or invalid fields.

    A conditional field appearing after the initial schema always needs review,
    even if it appears prefilled. Do not use a checkbox's HTML value (often
    ``on``) as proof that it was actually selected.
    """
    original = {f['key'] for f in initial.get('fields', [])}
    mapped = {f['field']: f for f in mapping.get('map', [])}
    blockers = []
    for field in live.get('fields', []):
        if not field.get('required'):
            continue
        key = field['key']
        if key not in original:
            blockers.append('new-required:' + key)
            continue
        action = mapped.get(key, {}).get('action')
        if action and action != 'skip':
            continue
        satisfied = (bool(field.get('checked')) if field.get('type') in ('checkbox', 'radio')
                     else bool(str(field.get('value') or '').strip()))
        if not satisfied:
            blockers.append('unanswered-required:' + key)
    blockers.extend('mismatch:' + key for key in mismatched)
    blockers.extend('invalid:' + key for key in invalid)
    return list(dict.fromkeys(blockers))
