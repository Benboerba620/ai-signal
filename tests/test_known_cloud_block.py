import json
from pathlib import Path
import sys
import tempfile
import unittest
from datetime import datetime, timezone
from unittest.mock import patch
sys.path.insert(0, str(Path(__file__).resolve().parents[1]/'scripts'))
import generate_feed
from generate_feed import is_known_cloud_block
from public_feed_watchdog import inspect


class KnownCloudBlockTests(unittest.TestCase):
    def test_only_marked_channels_and_only_403(self):
        marked = {'name': 'YC', 'cloud_403_expected': True}
        self.assertTrue(is_known_cloud_block(marked, 'https://anchor.fm/s/x/podcast/rss: HTTP 403'))
        self.assertFalse(is_known_cloud_block({'name': 'YC'}, 'https://a/rss: HTTP 403'))
        self.assertFalse(is_known_cloud_block(marked, 'https://a/rss: HTTP 404'))
        self.assertFalse(is_known_cloud_block(marked, 'https://a/rss: ConnectTimeout'))
        self.assertFalse(is_known_cloud_block(marked, 'https://a/rss: HTTP 403 | https://b/rss: HTTP 500'))
        self.assertFalse(is_known_cloud_block(marked, 'No episodes parsed from RSS: https://a/rss'))
        self.assertFalse(is_known_cloud_block(marked, None))

    def test_channel_loop_separates_known_blocks_from_errors(self):
        channels = [
            {'name': 'Blocked', 'rss_url': 'https://anchor.fm/s/1/podcast/rss', 'cloud_403_expected': True},
            {'name': 'Broken', 'rss_url': 'https://anchor.fm/s/2/podcast/rss', 'cloud_403_expected': True},
            {'name': 'Plain', 'rss_url': 'https://example.com/rss'},
        ]
        results = {'Blocked': ([], 'https://anchor.fm/s/1/podcast/rss: HTTP 403'),
                   'Broken': ([], 'https://anchor.fm/s/2/podcast/rss: HTTP 404'),
                   'Plain': ([], 'https://example.com/rss: HTTP 403')}
        sources = {'podcasts': {'channels': channels, 'lookback_hours': 72}}
        with patch.object(generate_feed, 'fetch_channel', lambda ch, *a, **k: results[ch['name']]), \
                patch.object(generate_feed, 'fetch_people', lambda *a, **k: ([], [])), \
                patch.object(generate_feed, 'load_feed', lambda *a, **k: {}), \
                patch.object(generate_feed, 'load_local', lambda *a, **k: []):
            feed = generate_feed.fetch_podcasts(sources)
        self.assertEqual([e.split(':')[0] for e in feed['known_blocked']], ['Blocked'])
        self.assertEqual(sorted(e.split(':')[0] for e in feed['errors']), ['Broken', 'Plain'])

    def test_published_file_redacts_known_blocks_and_watchdog_stays_quiet(self):
        with tempfile.TemporaryDirectory() as d:
            root = Path(d); (root/'feeds').mkdir()
            at = datetime(2026, 11, 8, 0, 0, tzinfo=timezone.utc)
            stamp = datetime(2026, 11, 7, 20, 12, tzinfo=timezone.utc).isoformat()
            generate_feed.write_json(root/'feeds/feed-podcasts.json', {
                'podcasts': [], 'errors': None, 'generated_at': stamp,
                'known_blocked': ['YC: https://anchor.fm/s/8c1524bc/podcast/rss: HTTP 403']})
            for name, field in [('blogs', 'articles'), ('x', 'x')]:
                (root/f'feeds/feed-{name}.json').write_text(json.dumps({field: [], 'generated_at': stamp}))
            data = json.loads((root/'feeds/feed-podcasts.json').read_text())
            self.assertNotIn('anchor.fm', json.dumps(data))
            self.assertEqual(len(data['known_blocked']), 1)
            self.assertNotIn('podcasts:upstream_errors', inspect(root, at)[0])


if __name__ == '__main__':
    unittest.main()
