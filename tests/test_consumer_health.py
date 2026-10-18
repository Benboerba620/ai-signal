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
    def test_origin_failure_uses_one_public_revision_for_feeds_and_text(self):
        revision = 'a'*40
        base = p.MIRROR_BASES[1]
        data = {'generated_at': datetime.now(timezone.utc).isoformat(), 'podcasts': []}
        def fetch(url):
            if '/commits/main' in url: return {'sha': revision}
            if '@'+revision+'/' in url: return data
            return None
        with mock.patch.dict('os.environ', {'AI_SIGNAL_BASE_URLS': p.RAW_BASE+','+base}), \
             mock.patch.object(p, '_mirror_commit', None), \
             mock.patch.object(p, '_mirror_resolution_attempted', False), \
             mock.patch.object(p, '_preferred_base', None), \
             mock.patch.object(p, 'fetch_json', side_effect=fetch) as request, \
             mock.patch.object(p, 'fetch_text', return_value='complete transcript') as text:
            self.assertEqual(p.fetch_json_any('feeds/feed-podcasts.json'),
                             (data, base+'@'+revision+'/feeds/feed-podcasts.json'))
            self.assertEqual(p.fetch_text_any('feeds/transcripts/one.txt'), 'complete transcript')
            text.assert_called_once_with(base+'@'+revision+'/feeds/transcripts/one.txt')
            self.assertEqual(sum('/commits/main' in call.args[0] for call in request.call_args_list), 1)

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

    def test_transcription_failure_is_visible_without_calling_rss_broken(self):
        feed = {'generated_at': datetime.now(timezone.utc).isoformat(), 'podcasts': [
            {'channel': "Lenny's Podcast", 'guid': 'one', 'title': 'Josh interview',
             'transcription': {'status': 'failed', 'failure_kind': 'source_denied', 'attempts': 1}},
            {'channel': 'All-In', 'guid': 'two', 'title': 'Long episode',
             'transcription': {'status': 'failed', 'failure_kind': 'resource_budget'}},
            {'channel': 'Other', 'guid': 'three', 'title': 'Retry later',
             'transcription': {'status': 'retry', 'failure_kind': 'worker_error'}},
            {'channel': 'Complete', 'transcript_available': True,
             'transcription': {'status': 'failed', 'failure_kind': 'source_denied'}},
        ]}
        sources, warnings = p.annotate_feed_sources({'podcasts': {'source': 'remote'}}, {'podcasts': feed})
        self.assertEqual(sources['podcasts']['health'], 'partial')
        self.assertEqual(sources['podcasts']['upstream_errors'], [])
        self.assertEqual(len(sources['podcasts']['transcription_issues']), 3)
        self.assertEqual(len(warnings), 3)
        self.assertIn('拒绝访问', warnings[0])
        self.assertIn('资源上限', warnings[1])
        self.assertIn('退避', warnings[2])
        output = io.StringIO()
        with mock.patch('sys.stdin', io.StringIO(json.dumps({'warnings': warnings}))), mock.patch('sys.stdout', output):
            render_digest.main()
        self.assertIn('Josh interview', output.getvalue())
        self.assertIn('资源上限', output.getvalue())
