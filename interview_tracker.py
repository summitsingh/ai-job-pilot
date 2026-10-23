"""Offline interview reminders. Usage: interview_tracker.py [--store FILE] due.

Processes are keyed by company and role. Application rejections are separate
records and never trigger feedback reminders. Dates are ISO calendar dates.
"""
import argparse
import datetime
import json
import os
from pathlib import Path
import tempfile
import uuid

OUTCOMES = ('pending', 'thanked', 'rejected', 'feedback_requested')


def load_store(path):
    path = Path(path)
    return json.loads(path.read_text(encoding='utf-8')) if path.exists() else {'events': []}


def save_store(path, store):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(prefix='.' + path.name, dir=path.parent)
    try:
        with os.fdopen(fd, 'w', encoding='utf-8') as stream:
            json.dump(store, stream, indent=2)
            stream.write('\n')
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(tmp, path)
    finally:
        if os.path.exists(tmp):
            os.unlink(tmp)


def iso_date(value):
    return datetime.date.fromisoformat(str(value)).isoformat()


def add_interview(store, company, role, round, date, contact):
    event = dict(id=uuid.uuid4().hex, company=company, role=role, round=round,
                 date=iso_date(date), contact=contact, interviewed=True, outcome='pending')
    store['events'].append(event)
    return event


def set_outcome(store, event_id, outcome):
    if outcome not in OUTCOMES:
        raise ValueError('Invalid outcome: ' + outcome)
    for event in store['events']:
        if event.get('id') == event_id and event.get('type') != 'application_rejection':
            event['outcome'] = outcome
            return event
    raise ValueError('Unknown interview id: ' + event_id)


def add_application_rejection(store, company, role, date):
    event = dict(type='application_rejection', company=company, role=role, date=iso_date(date))
    store['events'].append(event)
    return event


def due_reminders(store, today):
    today = iso_date(today)
    processes = {}
    for event in store['events']:
        if (event.get('type') != 'application_rejection' and event.get('interviewed')
                and iso_date(event['date']) <= today):
            processes.setdefault((event['company'], event['role']), []).append(event)
    reminders = []
    for (company, role), events in processes.items():
        latest = sorted(events, key=lambda e: e['date'])[-1]
        outcome = latest['outcome']
        if outcome not in ('pending', 'rejected'):
            continue
        kind = 'thank_you' if outcome == 'pending' else 'feedback'
        message = (f"Send a thank-you note to the interviewer/recruiter: {latest['contact']}."
                   if kind == 'thank_you' else f'Ask {company} for feedback on the interview process.')
        reminders.append(dict(kind=kind, company=company, role=role,
                              contact=latest['contact'], message=message))
    return reminders


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--store', default='./interviews.json')
    subs = parser.add_subparsers(dest='command', required=True)
    log = subs.add_parser('log')
    rejection = subs.add_parser('reject-application')
    for sub in (log, rejection):
        for field in ('company', 'role', 'date'):
            sub.add_argument('--' + field, required=True)
    log.add_argument('--round', required=True)
    log.add_argument('--contact', required=True)
    outcome = subs.add_parser('outcome')
    outcome.add_argument('--id', required=True)
    outcome.add_argument('--set', choices=OUTCOMES[1:], required=True)
    due = subs.add_parser('due')
    due.add_argument('--today', default=datetime.date.today().isoformat())
    subs.add_parser('list')
    args = parser.parse_args(argv)
    try:
        store = load_store(args.store)
        if args.command == 'log':
            result = add_interview(store, args.company, args.role, args.round, args.date, args.contact)
        elif args.command == 'outcome':
            result = set_outcome(store, args.id, args.set)
        elif args.command == 'reject-application':
            result = add_application_rejection(store, args.company, args.role, args.date)
        elif args.command == 'due':
            result = due_reminders(store, args.today)
        else:
            result = store['events']
        if args.command not in ('due', 'list'):
            save_store(args.store, store)
        print(json.dumps(result, indent=2))
        return 0
    except (ValueError, OSError) as exc:
        parser.error(str(exc))


if __name__ == '__main__':
    raise SystemExit(main())
