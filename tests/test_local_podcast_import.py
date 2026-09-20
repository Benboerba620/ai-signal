import copy
import hashlib
import json
import sys
import tempfile
import unittest
from datetime import datetime, timezone, timedelta
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[1]/'scripts'))
from local_podcast_import import load_local, merge_local, same_episode
from export_local_podcasts import read_transcript, match_episode

NOW = datetime(2026, 9, 20, tzinfo=timezone.utc)


def episode():
    text = 'Public interview transcript. ' * 400
    return dict(channel='SemiAnalysis', domain='ai', guid='rss-1',
                title='Ep. 033 - 300 Data Center Bans', pub_date='2026-09-18T12:00:00+00:00',
                link='https://example.com/episode', audio_url='https://example.com/episode.mp3',
                duration='1800', description='Public description', transcript=text,
                transcript_source='local_whisper', transcript_url='https://example.com/episode.mp3',
                transcript_sha256=hashlib.sha256(text.encode()).hexdigest())


class LocalPodcastTests(unittest.TestCase):
    def test_preserves_cloud_identity_fills_missing_and_is_idempotent(self):
        local = episode()
        cloud = {**local, 'guid': 'cloud-id', 'transcript': '', 'transcript_source': None}
        feed = {'generated_at': 'old scan', 'errors': ['RSS outage'], 'podcasts': [cloud]}
        merged = merge_local(feed, [local])
        self.assertEqual(merged['podcasts'][0]['guid'], 'cloud-id')
        self.assertEqual(merged['local_import']['filled'], 1)
        self.assertEqual(merged['generated_at'], 'old scan')
        self.assertEqual(merged['errors'], ['RSS outage'])
        again = merge_local(merged, [local])
        self.assertEqual(len(again['podcasts']), 1)
        self.assertEqual(again['local_import']['reused'], 1)
        self.assertEqual(feed['podcasts'][0]['transcript'], '')

    def test_preserves_good_cloud_text(self):
        local = episode(); cloud = {**local, 'transcript': 'Official text. '*900, 'transcript_source': 'rss_transcript'}
        merged = merge_local({'podcasts': [cloud]}, [local])
        self.assertEqual(merged['podcasts'][0]['transcript_source'], 'rss_transcript')

    def test_different_title_same_audio_deduplicates(self):
        a = episode(); b = {**a, 'guid': 'other', 'title': 'Edited title'}
        self.assertTrue(same_episode(a, b))
        result = merge_local({'podcasts': [a,b]}, [a])
        self.assertEqual(len(result['podcasts']), 1)

    def test_same_title_different_date_not_duplicate(self):
        a = episode(); b = {**a, 'guid': '2', 'link': '', 'audio_url': '', 'transcript_url': '', 'pub_date': '2026-08-01'}
        self.assertFalse(same_episode(a, b))

    def test_export_match_uses_date_duration_and_unique_title(self):
        local = dict(url='https://youtu.be/abc', title='Ep. 032 - 300 Datacenter Bans',
                     date=NOW-timedelta(days=2), duration=1800)
        public = {**episode(), 'pub_date': NOW-timedelta(days=2)}
        self.assertEqual(match_episode(local, [public]), public)
        self.assertIsNone(match_episode(local, [public, public.copy()]))
        self.assertIsNone(match_episode({**local, 'duration': 20}, [public]))

    def test_reject_private_fields_hash_and_sparse_text(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp); (root/'config').mkdir(); (root/'feeds/podcast-inbox').mkdir(parents=True)
            (root/'config/podcast-publication.json').write_text(json.dumps(dict(enabled=True,lookback_days=7,channels=['SemiAnalysis'])))
            path = root/'feeds/podcast-inbox/one.json'
            path.write_text(json.dumps(episode()))
            self.assertEqual(len(load_local(root,NOW)), 1)
            for patch in [{'private_notes':'research'}, {'channel':'Private show'},
                          {'transcript_sha256':'bad'}, {'transcript':'too short'},
                          {'transcript_url':'file:///private'}, {'pub_date':'bad'}]:
                path.write_text(json.dumps({**episode(), **patch}))
                with self.assertRaises(ValueError): load_local(root,NOW)
            path.write_text(json.dumps({**episode(), 'pub_date':'2026-08-01'}))
            self.assertEqual(load_local(root,NOW), [])

    def test_research_markdown_not_a_transcript(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp)/'note.md';path.write_text('# My research\n\n---\n\nPrivate thoughts')
            self.assertIsNone(read_transcript(path))

    def test_feed_generation_has_import_before_cache_and_after_discovery(self):
        import generate_feed
        from unittest.mock import patch
        with patch.object(generate_feed, 'load_feed', return_value={'podcasts': []}), \
             patch.object(generate_feed, 'load_local', return_value=[episode()]), \
             patch.object(generate_feed, 'fetch_people', return_value=([], [])):
            result = generate_feed.fetch_podcasts({'podcasts': {'channels': []}})
        self.assertEqual(len(result['podcasts']), 1)
        self.assertEqual(result['podcasts'][0]['guid'], 'rss-1')


if __name__ == '__main__':
    unittest.main()
