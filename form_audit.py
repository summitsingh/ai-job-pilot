"""Fail-closed audit between form filling and any submit click."""


def _group_id(field):
    if field.get('type') not in ('checkbox', 'radio'):
        return None
    if not (field.get('grouped') or field.get('group') or field.get('dfp')):
        return None
    return (field.get('type'), field.get('group') or '', field.get('dfp') or '')


def audit_form(initial, live, mapping, mismatched, invalid=()):
    """Return blockers for newly required, skipped, mismatched or invalid fields.

    A conditional field appearing after the initial schema always needs review,
    even if it appears prefilled. Do not use a checkbox's HTML value (often
    ``on``) as proof that it was actually selected.
    """
    original = {f['key'] for f in initial.get('fields', [])}
    mapped = {f['field']: f for f in mapping.get('map', [])}
    satisfied_groups = {
        _group_id(f) for f in live.get('fields', [])
        if _group_id(f) and bool(f.get('checked'))
    }
    blockers = []
    for field in live.get('fields', []):
        key = field['key']
        answer = mapped.get(key, {})
        if answer.get('guard'):
            blockers.append('human-review:' + key)
            # Preserve the fail-closed human-review blocker without also
            # reporting the intentional skip as an unanswered-field error.
            continue
        if not field.get('required'):
            continue
        if key not in original:
            blockers.append('new-required:' + key)
            continue
        # A mapped answer is a proposal; only the live field proves completion.
        gid = _group_id(field)
        satisfied = (
            (bool(field.get('checked')) or (gid in satisfied_groups))
            if field.get('type') in ('checkbox', 'radio')
            else bool(str(field.get('value') or '').strip())
        )
        if not satisfied:
            blockers.append('unanswered-required:' + key)
    required_keys = {f['key'] for f in live.get('fields', []) if f.get('required')}
    blockers.extend('mismatch:' + key for key in mismatched if key in required_keys)
    blockers.extend('invalid:' + key for key in invalid)
    return list(dict.fromkeys(blockers))
