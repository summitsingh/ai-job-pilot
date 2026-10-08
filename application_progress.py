#!/usr/bin/env python3
"""Conservative, read-only Gmail application progress export.

Input is a JSON array of Gmail search result objects (id, threadId, from,
subject, date, snippet). No email bodies or credentials are saved. Ambiguous
company matches are kept in an unmatched file, never assigned to a job.
"""
import argparse
import csv
import fcntl
import json
import os
import re
import subprocess
import sys
import tempfile
from pathlib import Path

COLUMNS = ('message_id', 'thread_id', 'date', 'company', 'title', 'job_url',
           'stage', 'subject', 'sender', 'evidence')
QUERY = 'after:2024/01/01 {"application" "interview" "recruiting" "offer" "position"}'
STAGES = (
    ('rejected', r'\b(?:regret to inform|not (?:be )?moving forward|will not (?:be )?moving forward|not selected|other candidates|unsuccessful application)\b'),
    ('offer', r'\b(?:pleased to (?:extend|offer)|offer of employment|job offer)\b'),
    ('interview', r'\b(?:schedule (?:an? )?interview|interview invitation|invite you to interview|interview availability|next round interview)\b'),
    ('assessment', r'\b(?:complete (?:the|an?) (?:coding |technical )?(?:assessment|challenge)|technical assessment invitation)\b'),
    ('under review', r'\b(?:application (?:is |has been )?under review|reviewing your application)\b'),
    ('received', r'\b(?:thank you for applying|application (?:has been )?received|application confirmation|application successfully submitted)\b'),
)


def normalize(s):
    return re.sub(r'[^a-z0-9]+', ' ', str(s or '').casefold()).strip()


def stage_for(message):
    text = ' '.join(str(message.get(k) or '') for k in ('subject', 'snippet'))
    for stage, pattern in STAGES:
        if re.search(pattern, text, re.I):
            return stage
    return None


def eligible(app):
    return str(app.get('Status', '')).casefold() in ('submitted', 'applied',
        'under review', 'interview', 'rejected', 'closed')


def match_job(message, apps):
    # Only match explicit company names in subject or sender display-name.
    # Never guess based on a shared email domain, recruiter, or generic subject.
    hay = normalize((message.get('subject') or '') + ' ' + (message.get('from') or ''))
    hits = [a for a in apps if eligible(a) and len(normalize(a.get('Company'))) >= 4
            and re.search(r'(?<!\w)' + re.escape(normalize(a['Company'])) + r'(?!\w)', hay)]
    if len(hits) == 1:
        return hits[0]
    if len(hits) > 1:
        subject = normalize(message.get('subject'))
        by_title = [a for a in hits if normalize(a.get('Title'))
                    and normalize(a.get('Title')) in subject]
        if len(by_title) == 1:
            return by_title[0]
    return None


def events_from(messages, apps):
    events, unresolved = [], []
    for msg in messages:
        stage = stage_for(msg)
        if not stage or not msg.get('id'):
            continue
        app = match_job(msg, apps)
        if not app:
            unresolved.append({'message_id': msg['id'], 'date': msg.get('date', ''),
                               'subject': msg.get('subject', ''), 'stage': stage,
                               'reason': 'no unique application match'})
            continue
        events.append(dict(zip(COLUMNS, (msg['id'], msg.get('threadId', ''),
            msg.get('date', ''), app.get('Company', ''), app.get('Title', ''),
            app.get('Job URL', ''), stage, msg.get('subject', ''),
            msg.get('from', ''), 'subject/snippet'))))
    return events, unresolved


def atomic(path, text):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(prefix='.' + path.name + '-', dir=path.parent)
    try:
        with os.fdopen(fd, 'w', encoding='utf-8', newline='') as f:
            f.write(text)
            f.flush()
            os.fsync(f.fileno())
        os.replace(tmp, path)
    finally:
        if os.path.exists(tmp):
            os.unlink(tmp)


def save(prefix, events, unresolved):
    prefix = Path(prefix)
    with open(str(prefix) + '.lock', 'a') as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)
        path = Path(str(prefix) + '.json')
        old = json.loads(path.read_text(encoding='utf-8')) if path.exists() else []
        by_id = {e['message_id']: e for e in old}
        for event in events:
            by_id[event['message_id']] = event
        rows = sorted(by_id.values(), key=lambda r: (r['date'], r['message_id']))
        atomic(path, json.dumps(rows, indent=2, ensure_ascii=False) + '\n')
        import io
        buf = io.StringIO(newline='')
        writer = csv.DictWriter(buf, fieldnames=COLUMNS)
        writer.writeheader()
        writer.writerows(rows)
        atomic(str(prefix) + '.csv', buf.getvalue())
        atomic(str(prefix) + '-unmatched.json',
               json.dumps(unresolved, indent=2, ensure_ascii=False) + '\n')
        return len(rows)


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--applications', required=True)
    parser.add_argument('--out-prefix', required=True)
    source = parser.add_mutually_exclusive_group(required=True)
    source.add_argument('--messages', help='offline Gmail search-result JSON array')
    source.add_argument('--live', action='store_true', help='read Gmail via Hermes OAuth')
    parser.add_argument('--google-api', default=str(Path.home() /
        '.hermes/skills/productivity/google-workspace/scripts/google_api.py'))
    args = parser.parse_args(argv)
    with open(args.applications, encoding='utf-8') as f:
        apps = json.load(f)
    if args.live:
        cmd = [sys.executable, args.google_api, 'gmail', 'search', QUERY, '--max', '500']
        p = subprocess.run(cmd, capture_output=True, text=True)
        if p.returncode:
            print('Gmail unavailable: ' + p.stderr.strip()[:300], file=sys.stderr)
            return 2
        messages = json.loads(p.stdout)
    else:
        with open(args.messages, encoding='utf-8') as f:
            messages = json.load(f)
    if not isinstance(messages, list) or not isinstance(apps, list):
        raise ValueError('applications and messages must be JSON arrays')
    events, unresolved = events_from(messages, apps)
    total = save(args.out_prefix, events, unresolved)
    print(json.dumps({'stored_events': total, 'matched_this_run': len(events),
                      'unmatched_this_run': len(unresolved)}))
    return 0


if __name__ == '__main__':
    sys.exit(main())
