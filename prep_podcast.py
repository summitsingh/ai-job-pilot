"""Build deterministic offline prep scripts and optional local speech.

Usage: prep_podcast.py build --brief FILE --out-dir DIR [--tts none|espeak]
JSON briefs contain title, candidate, company, role, two hosts, and sections
with heading and points. Markdown uses '# Title', 'Candidate: ...',
'Company: ...', 'Role: ...', 'Hosts: Sam, Priya', then ## headings and - bullets.
"""
import argparse
import json
from pathlib import Path
import shutil
import subprocess
import tempfile
import wave


def load_brief(path):
    path = Path(path)
    text = path.read_text(encoding='utf-8')
    if path.suffix.lower() == '.json':
        return json.loads(text)
    brief = {'sections': []}
    section = None
    for line in text.splitlines():
        line = line.strip()
        if line.startswith('## '):
            section = {'heading': line[3:].strip(), 'points': []}
            brief['sections'].append(section)
        elif line.startswith('# '):
            brief['title'] = line[2:].strip()
        elif line.startswith('- ') and section is not None:
            section['points'].append(line[2:])
        elif ':' in line and section is None:
            key, value = line.split(':', 1)
            brief[key.lower().strip()] = value.strip()
    if isinstance(brief.get('hosts'), str):
        brief['hosts'] = [host.strip() for host in brief['hosts'].split(',')]
    return brief


def build_script(brief):
    hosts = brief['hosts']
    if len(hosts) != 2 or hosts[0] == hosts[1]:
        raise ValueError('Exactly two distinct hosts are required')
    turns = []

    def add(text):
        turns.append({'speaker': hosts[len(turns) % 2], 'text': text})

    add(f"Let's prepare {brief['candidate']} for {brief['role']} at {brief['company']}.")
    connectors = ('First,', 'Another one:', 'Also,', 'And finally,')
    for section in brief['sections']:
        # Keep heading introductions with host A while preserving alternation.
        if len(turns) % 2:
            add("Let's work through the next section together.")
        add(section['heading'])
        for index, point in enumerate(section['points']):
            add(connectors[index % len(connectors)] + ' ' + point)
    add('That concludes our preparation. Take a breath and review your notes.')
    return turns


class TTSEngine:
    name = 'none'

    def synthesize(self, turns, out_path):
        raise NotImplementedError


class UnconfiguredTTS(TTSEngine):
    def synthesize(self, turns, out_path):
        return {'engine': self.name, 'audio': None, 'note': 'TTS not configured; script only'}


class EspeakTTS(TTSEngine):
    name = 'espeak'

    def __init__(self):
        self.executable = shutil.which('espeak-ng') or shutil.which('espeak')
        if not self.executable:
            raise ValueError('espeak is not available')

    def synthesize(self, turns, out_path):
        out_path = Path(out_path)
        out_path.parent.mkdir(parents=True, exist_ok=True)
        with tempfile.TemporaryDirectory() as temp:
            paths = []
            for index, turn in enumerate(turns):
                path = Path(temp) / f'{index}.wav'
                subprocess.run([self.executable, '-w', str(path), '--stdin'],
                               input=turn['text'], text=True, check=True, capture_output=True)
                paths.append(path)
            if not paths:
                raise ValueError('No turns to synthesize')
            with wave.open(str(out_path), 'wb') as output:
                expected = None
                for path in paths:
                    with wave.open(str(path), 'rb') as source:
                        params = source.getparams()
                        signature = (params.nchannels, params.sampwidth, params.framerate, params.comptype)
                        if expected is None:
                            output.setparams(params)
                            expected = signature
                        elif signature != expected:
                            raise ValueError('Incompatible WAV formats')
                        output.writeframes(source.readframes(params.nframes))
        return {'engine': self.name, 'audio': str(out_path)}


def select_tts(name=None):
    if name not in (None, 'none', 'espeak'):
        raise ValueError('Unknown TTS engine')
    if name == 'none':
        return UnconfiguredTTS()
    if name == 'espeak' or shutil.which('espeak-ng') or shutil.which('espeak'):
        return EspeakTTS()
    return UnconfiguredTTS()


def build_podcast(brief, out_dir, tts=None):
    turns = build_script(brief)
    out_dir = Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    script = out_dir / 'script.txt'
    script.write_text('\n'.join(f"{t['speaker']}: {t['text']}" for t in turns) + '\n', encoding='utf-8')
    result = (tts if tts is not None else select_tts()).synthesize(turns, out_dir / 'audio.wav')
    words = sum(len(turn['text'].split()) for turn in turns)
    manifest = {key: brief[key] for key in ('title', 'company', 'role', 'hosts')}
    manifest.update(turns=len(turns), total_words=words,
                    estimated_duration_seconds=words / 150 * 60, script=str(script),
                    audio=result['audio'], tts_engine=result['engine'], tts_note=result.get('note', ''))
    path = out_dir / 'manifest.json'
    path.write_text(json.dumps(manifest, indent=2) + '\n', encoding='utf-8')
    return path


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    subs = parser.add_subparsers(dest='command', required=True)
    build = subs.add_parser('build')
    build.add_argument('--brief', required=True)
    build.add_argument('--out-dir', required=True)
    build.add_argument('--tts', choices=('espeak', 'none'))
    args = parser.parse_args(argv)
    try:
        print(build_podcast(load_brief(args.brief), args.out_dir, select_tts(args.tts)))
        return 0
    except (ValueError, OSError, subprocess.CalledProcessError) as exc:
        parser.error(str(exc))


if __name__ == '__main__':
    raise SystemExit(main())
