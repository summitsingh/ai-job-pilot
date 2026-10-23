"""Truthful offline resume PDFs. Usage: resume_tailor.py build --base FILE
--profile platform --out FILE; check --pdf FILE; profiles.

Tailoring reorders and emphasizes existing facts only; it never invents
experience, employers, dates, titles, metrics, or technologies. Profiles select
an existing summary and order base strings; they cannot supply resume prose.

Base JSON (or YAML when PyYAML is installed) schema:
{name: str, location: str, phone: str, email: str,
 links: {website: {label: str, url: str}, linkedin: {label: str, url: str},
         github: {label: str, url: str}},
 summaries: {default: str, '<profile-name>': str},
 skills: [{category: str, items: [str]}],
 projects: [{name: str, meta: str, url: str|null, tags: [str],
             bullets: [str]}],
 experience: [{org: str, location: str, title: str, dates: str, tags: [str],
               bullets: [{text: str, tags: [str]}]}],
 publications: [{title: str, venue: str, url: str}],
 education: [str], certifications: [str], awards: [str]}

Project string bullets inherit project tags. Tagged bullet dictionaries are
also accepted for projects. QA allows fixed structural section labels and
punctuation, plus combinations of base fields used in contact and role lines.
The stdlib PDF fallback supports the literal/hex Tj and TJ text emitted by
fpdf2, Flate streams, page objects and URI strings; pdftotext is preferred.
"""
import argparse
import copy
import json
from pathlib import Path
import re
import shutil
import subprocess
import sys
from urllib.parse import urlparse
import zlib

from fpdf import FPDF

MARGIN = 13.5
HEADINGS = ('Summary', 'Skills', 'Projects', 'Experience', 'Publications',
            'Education', 'Certifications', 'Awards')
PROFILE_PATH = Path(__file__).with_name('resume_profiles.json')


def load_data(path):
    path = Path(path)
    text = path.read_text(encoding='utf-8')
    if path.suffix.lower() in ('.yaml', '.yml'):
        try:
            import yaml
        except ImportError as exc:
            raise ValueError('YAML requires PyYAML; use JSON instead') from exc
        return yaml.safe_load(text)
    return json.loads(text)


def load_profiles():
    return load_data(PROFILE_PATH)


def overlap(tags, emphasis):
    return tuple(-int(tag in tags) for tag in emphasis)


def bullet_text(bullet):
    return bullet['text'] if isinstance(bullet, dict) else bullet


def tailor_resume(base, profile):
    if isinstance(profile, str):
        profile = load_profiles()[profile]
    result = copy.deepcopy(base)
    result['summary'] = base['summaries'][profile['summary']]
    tags = profile.get('emphasis_tags', [])

    def rank(value, order):
        return order.index(value) if value in order else len(order)

    result['skills'].sort(key=lambda s: rank(s['category'], profile.get('skill_order', [])))
    result['projects'].sort(key=lambda p: (rank(p['name'], profile.get('project_order', [])),
                                          overlap(p.get('tags', []), tags)))
    for item in result['experience'] + result['projects']:
        item['bullets'].sort(key=lambda b: overlap(
            b.get('tags', []) if isinstance(b, dict) else item.get('tags', []), tags))
    return result


def profile_for_jd(base, jd, profiles=None):
    profiles = profiles or load_profiles()
    words = set(re.findall(r'[\w-]+', jd.casefold()))

    def hit(keyword):
        return bool(re.search(r'(?<!\w)' + re.escape(keyword.casefold()) + r'(?!\w)', jd.casefold()))

    name = max(profiles, key=lambda key: sum(hit(k) for k in profiles[key].get('keywords', [])))
    profile = copy.deepcopy(profiles[name])
    tags = []
    for item in base['experience'] + base['projects']:
        tags.extend(item.get('tags', []))
        for bullet in item['bullets']:
            if isinstance(bullet, dict):
                tags.extend(bullet.get('tags', []))
    matched = [tag for tag in dict.fromkeys(tags)
               if tag.casefold() in words or hit(tag)]
    profile['emphasis_tags'] = list(dict.fromkeys(matched + profile.get('emphasis_tags', [])))
    return name, profile


def contact_parts(base):
    parts = [(base['location'], None), (base['phone'], None),
             (base['email'], 'mailto:' + base['email'])]
    parts.extend((link['label'], link['url']) for link in base['links'].values())
    return parts


class Layout:
    def __init__(self, params):
        self.params = params
        self.pdf = FPDF(format='letter')
        self.pdf.set_margins(MARGIN, MARGIN, MARGIN)
        self.pdf.set_auto_page_break(False)
        self.pdf.add_page()
        self.pdf.c_margin = 0
        self.width = self.pdf.w - 2 * MARGIN
        self.y = MARGIN
        self.links = []

    def font(self, size=None, style=''):
        self.pdf.set_font('Helvetica', style, size or self.params['body_size'])

    def text(self, text, style='', size=None, bullet=False, title_link=None):
        self.font(size, style)
        indent = 3 if bullet else 0
        height = self.params['line_height']
        lines = self.pdf.multi_cell(self.width - indent, height, text,
                                   dry_run=True, output='LINES')
        if bullet:
            self.pdf.ellipse(MARGIN + .6, self.y + 1.45, .8, .8, style='F')
        consumed = 0
        for line in lines:
            self.pdf.set_xy(MARGIN + indent, self.y)
            self.pdf.cell(self.width - indent, height, line)
            if title_link and consumed < len(title_link[0]):
                span = line[:max(0, len(title_link[0]) - consumed)]
                if span:
                    self.pdf.link(MARGIN + indent, self.y, self.pdf.get_string_width(span),
                                  height, title_link[1])
                    self.links.append((span, title_link[1]))
            consumed += len(line) + 1
            self.y += height

    def header(self, base):
        self.font(19, 'B')
        self.pdf.set_xy(MARGIN, self.y)
        self.pdf.cell(self.width, 8, base['name'], align='C')
        self.y += 8
        self.font(8.8)
        parts = contact_parts(base)
        separator = ' | '
        total = sum(self.pdf.get_string_width(t) for t, _ in parts)
        total += self.pdf.get_string_width(separator) * (len(parts) - 1)
        if total > self.width:
            raise ValueError('Contact line exceeds page width')
        x = MARGIN + (self.width - total) / 2
        for index, (text, url) in enumerate(parts):
            self.pdf.set_xy(x, self.y)
            width = self.pdf.get_string_width(text)
            self.pdf.cell(width, 5, text, link=url or '')
            if url:
                self.links.append((text, url))
            x += width
            if index < len(parts) - 1:
                self.pdf.set_xy(x, self.y)
                self.pdf.cell(self.pdf.get_string_width(separator), 5, separator)
                x += self.pdf.get_string_width(separator)
        self.y += 7

    def section(self, name):
        self.y += 2.4 * self.params['spacing_scale']
        self.text(name.upper(), 'B', 10.2)
        self.pdf.line(MARGIN, self.y, MARGIN + self.width, self.y)
        self.y += 1.3 * self.params['spacing_scale']

    def role(self, name, meta, url=None):
        self.font(style='I')
        meta_width = self.pdf.get_string_width(meta)
        self.font(style='B')
        name_width = self.pdf.get_string_width(name)
        if name_width + meta_width + 4 > self.width:
            self.text(name, 'B', title_link=(name, url) if url else None)
            self.text(meta, 'I')
            return
        height = self.params['line_height']
        self.pdf.set_xy(MARGIN, self.y)
        self.pdf.cell(name_width, height, name, link=url or '')
        if url:
            self.links.append((name, url))
        self.font(style='I')
        self.pdf.set_xy(MARGIN + self.width - meta_width, self.y)
        self.pdf.cell(meta_width, height, meta)
        self.y += height


def render_resume(base, profile, params):
    data = tailor_resume(base, profile)
    layout = Layout(params)
    layout.header(data)
    layout.section('Summary')
    layout.text(data['summary'])
    for heading in HEADINGS[1:]:
        items = data.get(heading.lower(), [])
        if not items:
            continue
        layout.section(heading)
        for item in items:
            if heading == 'Skills':
                layout.text(item['category'] + ': ' + ', '.join(item['items']))
            elif heading in ('Experience', 'Projects'):
                is_role = heading == 'Experience'
                layout.role(item['org'] if is_role else item['name'],
                            item['dates'] if is_role else item['meta'],
                            None if is_role else item.get('url'))
                if is_role:
                    layout.text(item['title'] + ' | ' + item['location'], 'I')
                for bullet in item['bullets']:
                    layout.text(bullet_text(bullet), bullet=True)
                layout.y += .8 * params['spacing_scale']
            elif heading == 'Publications':
                layout.text(item['title'] + ', ' + item['venue'], bullet=True,
                            title_link=(item['title'], item['url']))
            else:
                layout.text(item)
    return layout


def parameter_sets():
    for step in range(11):
        scale = 1 - .018 * step
        yield dict(body_size=round(9 - .1 * step, 1),
                   line_height=3.9 * scale, line_height_factor=scale, spacing_scale=scale)


def write_fitted(renderer, out_path):
    for params in parameter_sets():
        layout = renderer(params)
        if layout.y <= layout.pdf.h - MARGIN:
            # Measure first, then render with the identical selected parameters.
            final = renderer(params)
            if final.y > final.pdf.h - MARGIN or final.pdf.page_no() != 1:
                raise ValueError('Layout exceeded one page')
            Path(out_path).parent.mkdir(parents=True, exist_ok=True)
            final.pdf.output(str(out_path))
            return dict(pages=1, parameters=params, max_y=final.y, links=final.links)
    raise ValueError('Resume content cannot fit exactly one page within typography bounds')


def build_resume(base, profile, out_path):
    return write_fitted(lambda params: render_resume(base, profile, params), out_path)


def build_cover_letter(base, company, event_note, out_path, profile):
    data = tailor_resume(base, profile)
    if isinstance(profile, str):
        profile = load_profiles()[profile]
    bullets = [bullet for role in data['experience'] for bullet in role['bullets']]
    bullets.sort(key=lambda b: overlap(b.get('tags', []), profile.get('emphasis_tags', [])))
    paragraphs = [f'Dear {company} Hiring Team,', event_note,
                  ' '.join(bullet_text(b) for b in bullets[:3]),
                  'Thank you for your consideration.', base['name']]

    def render(params):
        layout = Layout(params)
        layout.header(base)
        for paragraph in paragraphs:
            layout.y += 4 * params['spacing_scale']
            layout.text(paragraph)
        return layout

    return write_fitted(render, out_path)


PDF_STRING = rb'\((?:\\[\s\S]|[^\\()])*\)|<[0-9A-Fa-f\s]*>'


def decode_pdf_string(token):
    if token.startswith(b'<'):
        raw = bytes.fromhex(token[1:-1].decode('ascii'))
    else:
        raw = re.sub(rb'\\([0-7]{1,3}|\r\n|[\s\S])', lambda m: (
            bytes([int(m[1], 8)]) if re.fullmatch(rb'[0-7]{1,3}', m[1]) else
            {b'n': b'\n', b'r': b'\r', b't': b'\t', b'b': b'\b', b'f': b'\f',
             b'\n': b'', b'\r': b'', b'\r\n': b''}.get(m[1], m[1])), token[1:-1])
    if raw.startswith(b'\xfe\xff'):
        return raw[2:].decode('utf-16-be')
    return raw.decode('cp1252', errors='replace')


def extract_text(pdf_path):
    if shutil.which('pdftotext'):
        result = subprocess.run(['pdftotext', '-raw', str(pdf_path), '-'],
                                capture_output=True, text=True, check=True)
        return result.stdout
    raw = Path(pdf_path).read_bytes()
    lines = []
    for match in re.finditer(rb'<<(.*?)>>\s*stream\r?\n(.*?)\r?\nendstream', raw, re.S):
        data = match[2]
        if b'/FlateDecode' in match[1]:
            data = zlib.decompress(data)
        for operator in re.finditer(rb'(' + PDF_STRING + rb')\s*Tj|\[(.*?)\]\s*TJ', data, re.S):
            tokens = [operator[1]] if operator[1] else re.findall(PDF_STRING, operator[2])
            lines.append(''.join(decode_pdf_string(token) for token in tokens))
    return '\n'.join(lines)


def pdf_pages(path):
    return len(re.findall(rb'/Type\s*/Page\b', Path(path).read_bytes()))


def pdf_links(path):
    return [decode_pdf_string(m[1]) for m in re.finditer(
        rb'/URI\s*(' + PDF_STRING + rb')', Path(path).read_bytes())]


def normalized(text):
    return ' '.join(text.split())


def base_corpus(base):
    def strings(value):
        if isinstance(value, str):
            yield value
        elif isinstance(value, dict):
            for child in value.values():
                yield from strings(child)
        elif isinstance(value, list):
            for child in value:
                yield from strings(child)
    parts = list(strings(base)) + [h.upper() for h in HEADINGS]
    parts.append(' | '.join(text for text, _ in contact_parts(base)))
    for skill in base['skills']:
        parts.append(skill['category'] + ': ' + ', '.join(skill['items']))
    for role in base['experience']:
        parts.extend([role['org'] + ' ' + role['dates'], role['title'] + ' | ' + role['location']])
    for project in base['projects']:
        parts.append(project['name'] + ' ' + project['meta'])
    for pub in base['publications']:
        parts.append(pub['title'] + ', ' + pub['venue'])
    return normalized(' '.join(parts))


def valid_url(url):
    try:
        parsed = urlparse(url)
        if any(c.isspace() for c in url):
            return False
        if parsed.scheme == 'mailto':
            return bool(re.fullmatch(r'[^@\s]+@[^@\s]+\.[^@\s]+', parsed.path))
        return parsed.scheme in ('https', 'http') and bool(parsed.hostname) and parsed.port != 0
    except ValueError:
        return False


def qa_pdf(pdf_path, base=None, expected_links=None, pii_terms=None):
    failures = []
    try:
        pages = pdf_pages(pdf_path)
        if pages != 1:
            failures.append(f'pages: expected 1, found {pages}')
        text = extract_text(pdf_path)
        if not text.strip() or (base is not None and normalized(base['name']) not in normalized(text)):
            failures.append('text: missing extractable text or applicant name')
        actual = pdf_links(pdf_path)
        expected = actual if expected_links is None else expected_links
        for item in expected:
            url = item[1] if isinstance(item, (list, tuple)) else item
            if url not in actual or not valid_url(url):
                failures.append('links: missing or malformed URL: ' + url)
        for url in actual:
            if not valid_url(url):
                failures.append('links: malformed annotation URL: ' + url)
        if base is not None:
            corpus = base_corpus(base)
            for line in text.splitlines():
                line = normalized(line)
                if line and line not in corpus:
                    failures.append('truthfulness: text outside base: ' + line)
        for term in pii_terms or []:
            if term and term.casefold() in text.casefold():
                failures.append('pii: denylisted term: ' + term)
    except (OSError, ValueError, zlib.error, subprocess.CalledProcessError) as exc:
        failures.append('text: cannot parse PDF: ' + str(exc))
    return failures


def load_pii(path):
    if not path:
        return []
    text = Path(path).read_text(encoding='utf-8')
    if text.lstrip().startswith('['):
        terms = json.loads(text)
        if not isinstance(terms, list) or not all(isinstance(t, str) for t in terms):
            raise ValueError('PII JSON must be a list of strings')
        return terms
    return [line.strip() for line in text.splitlines() if line.strip()]


def print_qa(failures):
    for check in ('pages', 'text', 'links', 'truthfulness', 'pii'):
        details = [f for f in failures if f.startswith(check + ':')]
        print(('FAIL ' + '; '.join(details)) if details else ('PASS ' + check))


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    subs = parser.add_subparsers(dest='command', required=True)
    build = subs.add_parser('build')
    build.add_argument('--base', required=True)
    mode = build.add_mutually_exclusive_group(required=True)
    mode.add_argument('--profile', choices=list(load_profiles()))
    mode.add_argument('--jd')
    build.add_argument('--out', required=True)
    build.add_argument('--cover-letter', action='store_true')
    build.add_argument('--company')
    build.add_argument('--event-note')
    build.add_argument('--pii-file')
    check = subs.add_parser('check')
    check.add_argument('--pdf', required=True)
    check.add_argument('--base')
    check.add_argument('--pii-file')
    subs.add_parser('profiles')
    args = parser.parse_args(argv)
    try:
        if args.command == 'profiles':
            print('\n'.join(load_profiles()))
            return 0
        base = load_data(args.base) if args.base else None
        pii = load_pii(args.pii_file)
        if args.command == 'check':
            failures = qa_pdf(args.pdf, base, pii_terms=pii)
        else:
            profile = (profile_for_jd(base, Path(args.jd).read_text(encoding='utf-8'))[1]
                       if args.jd else load_profiles()[args.profile])
            if args.cover_letter:
                if not args.company or args.event_note is None:
                    parser.error('--cover-letter requires --company and --event-note')
                report = build_cover_letter(base, args.company, args.event_note, args.out, profile)
            else:
                report = build_resume(base, profile, args.out)
            failures = qa_pdf(args.out, None if args.cover_letter else base, report['links'], pii)
            print(json.dumps(report))
        print_qa(failures)
        return 1 if failures else 0
    except (ValueError, KeyError, OSError) as exc:
        print('FAIL build: ' + str(exc), file=sys.stderr)
        return 1


if __name__ == '__main__':
    raise SystemExit(main())
