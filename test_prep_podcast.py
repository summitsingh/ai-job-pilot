"""Deterministic script, Markdown, manifest and offline TTS checks."""
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
import wave

import prep_podcast as podcast

SAMPLE = Path(__file__).parent / 'examples/sample_prep_brief.json'


class PodcastTests(unittest.TestCase):
    def setUp(self):
        self.brief = podcast.load_brief(SAMPLE)

    def test_script(self):
        turns = podcast.build_script(self.brief)
        self.assertEqual(turns, podcast.build_script(self.brief))
        self.assertIn(self.brief['company'], turns[0]['text'])
        self.assertIn(self.brief['role'], turns[0]['text'])
        self.assertIn(self.brief['candidate'], turns[0]['text'])
        for left, right in zip(turns, turns[1:]):
            self.assertNotEqual(left['speaker'], right['speaker'])
        for section in self.brief['sections']:
            heading = next(t for t in turns if t['text'] == section['heading'])
            self.assertEqual(heading['speaker'], self.brief['hosts'][0])
            for point in section['points']:
                self.assertTrue(any(point in t['text'] for t in turns))

    def test_manifest_unconfigured(self):
        with tempfile.TemporaryDirectory() as temp:
            path = podcast.build_podcast(self.brief, temp, podcast.UnconfiguredTTS())
            manifest = json.loads(path.read_text())
            self.assertTrue(Path(manifest['script']).exists())
            self.assertIsNone(manifest['audio'])
            self.assertEqual(manifest['tts_note'], 'TTS not configured; script only')
            for key in ('title', 'company', 'role', 'hosts', 'turns', 'total_words',
                        'estimated_duration_seconds', 'script', 'audio', 'tts_engine', 'tts_note'):
                self.assertIn(key, manifest)
            self.assertGreater(manifest['estimated_duration_seconds'], 0)
            self.assertAlmostEqual(manifest['estimated_duration_seconds'], manifest['total_words'] * .4)
            self.assertFalse((Path(temp) / 'audio.wav').exists())

    def test_markdown(self):
        text = '\n'.join(['# ' + self.brief['title']] + [
            f'{key.title()}: {self.brief[key]}' for key in ('candidate', 'company', 'role')]
            + ['Hosts: ' + ', '.join(self.brief['hosts'])])
        for section in self.brief['sections']:
            text += '\n## ' + section['heading'] + '\n'
            text += '\n'.join('- ' + p for p in section['points'])
        with tempfile.TemporaryDirectory() as temp:
            path = Path(temp) / 'brief.md'
            path.write_text(text)
            self.assertEqual(podcast.load_brief(path), self.brief)

    def test_select(self):
        with patch('prep_podcast.shutil.which', return_value=None):
            self.assertIsInstance(podcast.select_tts(), podcast.UnconfiguredTTS)
            with self.assertRaises(ValueError):
                podcast.select_tts('espeak')
        self.assertIsInstance(podcast.select_tts('none'), podcast.UnconfiguredTTS)

    def test_wav_concat(self):
        def synthesize(command, **kwargs):
            path = command[command.index('-w') + 1]
            with wave.open(path, 'wb') as output:
                output.setparams((1, 2, 22050, 0, 'NONE', 'not compressed'))
                output.writeframes(b'\x00\x00' * 100)
        with tempfile.TemporaryDirectory() as temp:
            with patch('prep_podcast.shutil.which', return_value='/fictional/espeak'), patch(
                    'prep_podcast.subprocess.run', side_effect=synthesize):
                result = podcast.EspeakTTS().synthesize(podcast.build_script(self.brief), Path(temp) / 'audio.wav')
            with wave.open(result['audio']) as audio:
                self.assertEqual(audio.getnframes(), len(podcast.build_script(self.brief)) * 100)


if __name__ == '__main__':
    unittest.main()
