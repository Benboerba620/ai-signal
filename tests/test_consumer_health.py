import io
import json
import sys
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'scripts'))
import prepare_digest as p
import render_digest


class ConsumerHealthTests(unittest.TestCase):
    def test_selects_newest_valid_mirror_after_origin_failure(self):
        now = datetime.now(timezone.utc)
        old = {'generated_at': (now - timedelta(hours=24)).isoformat(), 'podcasts': []}
        new = {'generated_at': now.isoformat(), 'podcasts': []}
        future = {'generated_at': (now + timedelta(days=1)).isoformat(), 'podcasts': []}
        responses = {p.RAW_BASE: None, 'old': old, 'new': new, 'future': future,
                     'invalid': {'generated_at': now.isoformat()}}
        with mock.patch.object(p, 'candidate_bases', return_value=list(responses)), \
             mock.patch.object(p, 'fetch_json', side_effect=lambda url: responses[url.rsplit('/feeds/', 1)[0]]), \
             mock.patch.object(p, '_preferred_base', None):
            data, url = p.fetch_json_any('feeds/feed-podcasts.json')
        self.assertEqual(data, new)
        self.assertEqual(url, 'new/feeds/feed-podcasts.json')

    def test_origin_fast_path_does_not_wait_for_mirrors(self):
        data = {'generated_at': datetime.now(timezone.utc).isoformat(), 'papers': []}
        with mock.patch.object(p, 'candidate_bases', return_value=['mirror', p.RAW_BASE]), \
             mock.patch.object(p, 'fetch_json', return_value=data) as fetch, \
             mock.patch.object(p, '_preferred_base', None):
            self.assertEqual(p.fetch_json_any('feeds/feed-arxiv.json')[0], data)
        fetch.assert_called_once_with(p.RAW_BASE + '/feeds/feed-arxiv.json')

    def test_valid_empty_remote_does_not_resurrect_cached_content(self):
        remote = {'generated_at': datetime.now(timezone.utc).isoformat(), 'papers': []}
        with mock.patch.object(p, 'fetch_json_any', return_value=(remote, 'origin')), \
             mock.patch.object(p, 'load_local_json', return_value={'papers': [{'old': True}]}):
            data, meta = p.fetch_feed('feed-arxiv.json', 'papers')
        self.assertEqual(data['papers'], [])
        self.assertEqual(meta['source'], 'remote')

    def test_upstream_failure_survives_payload_and_rendering(self):
        feed = {'generated_at': datetime.now(timezone.utc).isoformat(),
                'errors': ['Latent Space: 403 Forbidden'], 'podcasts': []}
        sources, warnings = p.annotate_feed_sources(
            {'podcasts': {'source': 'remote'}}, {'podcasts': feed})
        self.assertEqual(sources['podcasts']['health'], 'partial')
        self.assertIn('Latent Space', warnings[0])
        output = io.StringIO()
        with mock.patch('sys.stdin', io.StringIO(json.dumps({'warnings': warnings}))), \
             mock.patch('sys.stdout', output):
            render_digest.main()
        self.assertIn('Latent Space', output.getvalue())

    def test_empty_success_is_not_a_failure(self):
        feed = {'generated_at': datetime.now(timezone.utc).isoformat(), 'errors': None}
        sources, warnings = p.annotate_feed_sources({'x': {'source': 'remote'}}, {'x': feed})
        self.assertEqual(sources['x']['health'], 'ok')
        self.assertEqual(warnings, [])
