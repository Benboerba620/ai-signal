import argparse
import copy
from datetime import datetime, timedelta, timezone
import hashlib
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

import httpx

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'scripts'))
import cloud_whisper_queue as queue
import cloud_whisper_pilot as pilot

NOW = datetime(2026, 10, 8, tzinfo=timezone.utc)


def episode(guid='one', channel='Allowed', **kwargs):
    return {'guid': guid, 'channel': channel, 'title': 'AI interview', 'description': 'AI episode',
            'audio_url': 'https://publisher.example/episode.mp3', 'duration': '2100',
            'pub_date': (NOW-timedelta(days=2)).isoformat(), 'link': 'https://publisher.example/episode', **kwargs}


class QueueTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name) / 'repository'
        self.output = Path(self.temporary.name) / 'results'
        (self.root/'feeds/transcripts').mkdir(parents=True)
        (self.root/'config').mkdir()
        self.item = episode()
        self.sources = {'podcasts': {'channels': [{'name': 'Allowed', 'transcribe_missing': True},
                                               {'name': 'Chinese', 'transcribe_missing': True, 'language': 'zh'},
                                               {'name': 'Relevant', 'transcribe_missing': 'relevant'}],
                                    'transcription': {'relevance_keywords': ['ai']}}}
        self.write('config/sources.json', self.sources)
        self.write(queue.FEED_PATH, {'generated_at': 'preserve', 'errors': ['existing error'], 'podcasts': [self.item]})
        self.write(queue.INDEX_PATH, {'transcripts': []})
        self.args = argparse.Namespace(status=False, enqueue_only=False, process_one=True,
                                       output_dir=str(self.output), timeout_seconds=1800,
                                       max_rss_mib=6144, only_channel=[], checkpoint_git=False)

    def write(self, path, data):
        pilot.atomic_json(self.root/path, data)

    def state(self):
        return queue.load_state(self.root)

    def entry(self, item=None):
        return self.state()['entries'][queue.identity(item or self.item)]

    def run_queue(self, **kwargs):
        args = copy.copy(self.args)
        for key, value in kwargs.items():
            setattr(args, key, value)
        with patch('builtins.print'):
            return queue.run(args, self.root, NOW)

    def successful_run(self, **kwargs):
        with patch.object(queue, 'run_episode', return_value=('Full transcript content.\n', {'transcript_source': 'local_whisper'})) as runner:
            result = self.run_queue(**kwargs)
        return result, runner

    def test_snapshots_survive_feed_ageout_and_publish_without_resurrection(self):
        self.run_queue(enqueue_only=True)
        snapshot = self.entry()['episode']
        self.write(queue.FEED_PATH, {'generated_at': 'new', 'podcasts': []})
        result, runner = self.successful_run()
        self.assertEqual(result, 0)
        self.assertEqual(runner.call_args.args[0]['episode'], snapshot)
        self.assertEqual(self.entry()['status'], 'completed')
        self.assertEqual(json.loads((self.root/queue.FEED_PATH).read_text()), {'generated_at': 'new', 'podcasts': []})
        index = json.loads((self.root/queue.INDEX_PATH).read_text())
        self.assertEqual(index['retention_days'], 14)
        self.assertEqual(queue.parse_datetime(index['transcripts'][0]['expires_at']), NOW+timedelta(days=14))
        self.assertTrue((self.root/index['transcripts'][0]['transcript_path']).is_file())

    def test_success_idempotent_and_never_overwrites_unrelated_sidecars(self):
        unrelated = self.root/'feeds/transcripts/old.txt'
        unrelated.write_text('  Original bytes\n\n')
        self.assertEqual(self.successful_run()[0], 0)
        content = unrelated.read_bytes()
        self.assertEqual(self.successful_run()[1].call_count, 0)
        self.assertEqual(unrelated.read_bytes(), content)
        feed = json.loads((self.root/queue.FEED_PATH).read_text())
        self.assertEqual(feed['generated_at'], 'preserve')
        self.assertEqual(feed['errors'], ['existing error'])
        item = feed['podcasts'][0]
        self.assertTrue(item['transcript_available'])
        self.assertNotIn('transcript', item)
        self.assertEqual(item['transcript_sha256'], hashlib.sha256((self.root/item['transcript_path']).read_bytes()).hexdigest())

    def test_completed_tombstone_survives_pruned_index_file_and_title_change(self):
        self.successful_run()
        path = self.entry()['transcript_path']
        (self.root/path).unlink()
        self.write(queue.INDEX_PATH, {'transcripts': []})
        self.write(queue.FEED_PATH, {'podcasts': [{**self.item, 'title': 'Renamed'}]})
        result, runner = self.successful_run()
        self.assertEqual(result, 0)
        runner.assert_not_called()
        self.assertEqual(self.entry()['status'], 'completed')
        report = json.loads((self.output/'run-report.json').read_text())
        self.assertTrue(report['warnings'])

    def test_existing_index_success_deduplicates_by_source_and_guid(self):
        path = 'feeds/transcripts/existing.txt'
        (self.root/path).write_text('Already transcribed\n')
        self.write(queue.INDEX_PATH, {'transcripts': [{**self.item, 'title': 'Old title', 'transcript_path': path}]})
        result, runner = self.successful_run()
        self.assertEqual(result, 0)
        runner.assert_not_called()
        self.assertEqual(self.entry()['status'], 'completed')
        self.assertTrue(json.loads((self.root/queue.FEED_PATH).read_text())['podcasts'][0]['transcript_available'])

    def test_existing_success_repairs_stale_availability_flag(self):
        self.successful_run()
        feed = json.loads((self.root/queue.FEED_PATH).read_text())
        feed['podcasts'][0]['transcript_available'] = False
        self.write(queue.FEED_PATH, feed)
        _, runner = self.successful_run()
        runner.assert_not_called()
        self.assertTrue(json.loads((self.root/queue.FEED_PATH).read_text())['podcasts'][0]['transcript_available'])

    def test_orphan_sidecar_is_deduplicated(self):
        path = self.root/f'feeds/transcripts/{queue.transcript_id(self.item)}.txt'
        path.write_text('Recovered complete transcript\n')
        result, runner = self.successful_run()
        runner.assert_not_called()
        self.assertEqual(self.entry()['status'], 'completed')
        index = json.loads((self.root/queue.INDEX_PATH).read_text())
        self.assertEqual(len(index['transcripts']), 1)
        self.assertTrue(json.loads((self.root/queue.FEED_PATH).read_text())['podcasts'][0]['transcript_available'])

    def test_missing_empty_invalid_utf8_hash_mismatch_cache_is_not_success(self):
        for content, extra in ((None, {}), (b'', {}), (b'\xff', {}), (b'words\n', {'transcript_sha256': 'bad'}),
                               (b'words\n', {'transcript_chars': 1000})):
            with self.subTest(content=content, extra=extra):
                (self.root/queue.QUEUE_PATH).unlink(missing_ok=True)
                path = self.root/'feeds/transcripts/stale.txt'
                path.unlink(missing_ok=True)
                if content is not None:
                    path.write_bytes(content)
                item = {**self.item, 'transcript_available': True, 'transcript_path': 'feeds/transcripts/stale.txt', **extra}
                self.write(queue.FEED_PATH, {'podcasts': [item]})
                self.write(queue.INDEX_PATH, {'transcripts': [item]})
                self.run_queue(enqueue_only=True)
                self.assertEqual(self.entry()['status'], 'pending')

    def test_failure_persists_attempt_retry_backoff_and_independent_next_item(self):
        self.write(queue.FEED_PATH, {'podcasts': [self.item, episode('two')]})
        with patch.object(queue, 'run_episode', side_effect=queue.QueueError('Model failed', 'model_error', True)):
            self.assertEqual(self.run_queue(), 1)
        entry = self.entry()
        self.assertEqual(entry['status'], 'retry')
        self.assertEqual(entry['attempts'], 1)
        self.assertEqual(queue.parse_datetime(entry['next_retry_at']), NOW+timedelta(hours=6))
        result, runner = self.successful_run()
        self.assertEqual(result, 0)
        self.assertEqual(runner.call_args.args[0]['episode']['guid'], 'two')
        self.assertEqual(self.entry()['attempts'], 1)

    def test_retry_backoff_grows_and_exhausts(self):
        entry = {'attempts': 2}
        queue.record_failure(entry, NOW, 'failed', 'model_error', True)
        self.assertEqual(queue.parse_datetime(entry['next_retry_at']), NOW+timedelta(hours=12))
        entry['attempts'] = 3
        queue.record_failure(entry, NOW, 'failed', 'model_error', True)
        self.assertEqual(entry['status'], 'failed')
        self.assertIsNone(entry['next_retry_at'])

    def test_denial_is_terminal_and_never_falls_back_or_retries(self):
        with patch.object(queue, 'run_episode', side_effect=queue.QueueError('Audio HTTP status 403', 'source_denied')):
            self.assertEqual(self.run_queue(), 1)
        self.assertEqual(self.entry()['status'], 'failed')
        self.assertEqual(self.entry()['failure_kind'], 'source_denied')
        result, runner = self.successful_run()
        runner.assert_not_called()

    def test_running_attempt_recovered_with_backoff_not_immediate_retry(self):
        self.run_queue(enqueue_only=True)
        state = self.state()
        state['entries'][queue.identity(self.item)].update(status='running', attempts=1)
        self.write(queue.QUEUE_PATH, state)
        _, runner = self.successful_run()
        runner.assert_not_called()
        self.assertEqual(self.entry()['failure_kind'], 'interrupted')
        self.assertEqual(self.entry()['status'], 'retry')

    def test_policy_rechecked_after_ageout(self):
        self.run_queue(enqueue_only=True)
        self.write(queue.FEED_PATH, {'podcasts': []})
        self.sources['podcasts']['channels'][0]['transcribe_missing'] = False
        self.write('config/sources.json', self.sources)
        _, runner = self.successful_run()
        runner.assert_not_called()
        self.assertEqual(self.entry()['status'], 'skipped')
        self.assertEqual(self.entry()['failure_kind'], 'source_policy')

    def test_language_policy_chinese_english_unknown(self):
        self.assertEqual(queue.model_policy(episode(), self.sources), ('small.en', 'en'))
        self.assertEqual(queue.model_policy(episode(channel='Chinese'), self.sources), ('small', 'zh'))
        self.sources['podcasts']['channels'][0]['language'] = 'ja'
        self.write('config/sources.json', self.sources)
        _, runner = self.successful_run()
        runner.assert_not_called()
        self.assertEqual(self.entry()['status'], 'skipped')
        self.assertEqual(self.entry()['failure_kind'], 'unsupported_language')
        self.assertEqual(self.entry()['attempts'], 0)

    def test_excluded_relevance_and_youtube_never_enqueued(self):
        items = [episode(channel='Excluded'), episode(channel='Relevant', title='Vacation', description=''),
                 episode(guid='youtube', audio_url='', link='https://youtube.com/watch?v=abcdefghijk'),
                 episode(guid='youtube-audio', audio_url='https://youtube.com/watch?v=abcdefghijk')]
        self.write(queue.FEED_PATH, {'podcasts': items})
        _, runner = self.successful_run()
        runner.assert_not_called()
        self.assertFalse(self.state()['entries'])

    def test_no_guid_cannot_accidentally_dedupe_unrelated_episodes(self):
        self.write(queue.FEED_PATH, {'podcasts': [episode(guid='')]})
        self.run_queue(enqueue_only=True)
        self.assertFalse(self.state()['entries'])

    def test_filter_does_not_force_disabled_source(self):
        self.write(queue.FEED_PATH, {'podcasts': [episode(channel='Excluded'), self.item]})
        _, runner = self.successful_run(only_channel=['Excluded'])
        runner.assert_not_called()
        self.assertEqual(self.entry()['status'], 'pending')

    def test_status_read_only_no_network_or_report_writes(self):
        with patch.object(queue, 'refresh') as refresh, patch.object(queue, 'run_episode') as worker:
            self.run_queue(status=True)
        refresh.assert_not_called()
        worker.assert_not_called()
        self.assertFalse((self.root/queue.QUEUE_PATH).exists())
        self.assertFalse(self.output.exists())

    def test_corrupt_state_fails_closed(self):
        (self.root/queue.QUEUE_PATH).write_text('{bad')
        with patch.object(queue, 'run_episode') as worker, self.assertRaises(json.JSONDecodeError):
            self.run_queue()
        worker.assert_not_called()
        self.assertEqual((self.root/queue.QUEUE_PATH).read_text(), '{bad')

    def test_state_checkpoint_failure_prevents_external_work(self):
        with patch.object(queue, 'save_state', side_effect=OSError('disk')), patch.object(queue, 'run_episode') as worker:
            with self.assertRaises(OSError):
                self.run_queue()
        worker.assert_not_called()

    def test_git_checkpoint_precedes_work_and_failure_blocks_it(self):
        with patch.object(queue, 'checkpoint_git', side_effect=queue.QueueError('checkpoint failed', 'checkpoint_error', True)), \
             patch.object(queue, 'run_episode') as worker:
            self.assertEqual(self.run_queue(checkpoint_git=True), 1)
        worker.assert_not_called()
        self.assertEqual(self.entry()['failure_kind'], 'checkpoint_error')

    def test_running_state_is_persisted_before_work(self):
        def check(entry, *args):
            self.assertEqual(self.entry()['status'], 'running')
            self.assertEqual(self.entry()['attempts'], 1)
            return 'content\n', {'transcript_source': 'local_whisper'}
        with patch.object(queue, 'run_episode', side_effect=check):
            self.assertEqual(self.run_queue(), 0)

    def test_index_publication_failure_preserves_index_and_recovers_without_model(self):
        old_index = (self.root/queue.INDEX_PATH).read_bytes()
        original = pilot.atomic_json
        def fail_index(path, data):
            if Path(path) == self.root/queue.INDEX_PATH:
                raise OSError('disk full')
            return original(path, data)
        with patch.object(pilot, 'atomic_json', side_effect=fail_index):
            self.assertEqual(self.successful_run()[0], 1)
        self.assertEqual((self.root/queue.INDEX_PATH).read_bytes(), old_index)
        self.assertNotEqual(self.entry()['status'], 'completed')
        self.assertIn('publication', self.entry())
        _, runner = self.successful_run()
        runner.assert_not_called()
        self.assertEqual(self.entry()['status'], 'completed')
        self.assertTrue(json.loads((self.root/queue.FEED_PATH).read_text())['podcasts'][0]['transcript_available'])

    def test_sidecar_failure_recovers_from_durable_embedded_text(self):
        original = pilot.atomic_text
        def fail_sidecar(path, text):
            if Path(path).suffix == '.txt':
                raise OSError('disk full')
            return original(path, text)
        with patch.object(pilot, 'atomic_text', side_effect=fail_sidecar):
            self.assertEqual(self.successful_run()[0], 1)
        entry = self.entry()
        self.assertNotEqual(entry['status'], 'completed')
        self.assertEqual(entry['publication_text'], 'Full transcript content.\n')
        self.assertFalse((self.root/entry['publication']['transcript_path']).exists())
        _, runner = self.successful_run()
        runner.assert_not_called()
        self.assertEqual(self.entry()['status'], 'completed')
        self.assertNotIn('publication_text', self.entry())

    def test_feed_publication_failure_repairs_from_intent_and_keeps_old_feed(self):
        old_feed = (self.root/queue.FEED_PATH).read_bytes()
        original = pilot.atomic_json
        def fail_feed(path, data):
            if Path(path) == self.root/queue.FEED_PATH:
                raise OSError('disk full')
            return original(path, data)
        with patch.object(pilot, 'atomic_json', side_effect=fail_feed):
            self.assertEqual(self.successful_run()[0], 1)
        self.assertEqual((self.root/queue.FEED_PATH).read_bytes(), old_feed)
        _, runner = self.successful_run()
        runner.assert_not_called()
        self.assertTrue(json.loads((self.root/queue.FEED_PATH).read_text())['podcasts'][0]['transcript_available'])

    def test_oversize_preflight_is_skipped_not_model_failure(self):
        self.write(queue.FEED_PATH, {'podcasts': [{**self.item, 'duration': '7201'}]})
        _, runner = self.successful_run()
        runner.assert_not_called()
        self.assertEqual(self.entry()['status'], 'skipped')
        self.assertEqual(self.entry()['failure_kind'], 'resource_policy')

    def test_measured_policy_skip_is_terminal_and_distinct(self):
        with patch.object(queue, 'run_episode', side_effect=queue.QueueError('Measured audio too short', 'source_policy')):
            self.assertEqual(self.run_queue(), 0)
        self.assertEqual(self.entry()['status'], 'skipped')
        self.assertEqual(self.successful_run()[1].call_count, 0)

    def test_error_classification_denied_memory_model(self):
        self.assertEqual(queue.classify_error(pilot.PilotError('Audio HTTP status 403; no bypass or retry'))[:2], ('source_denied', False))
        self.assertEqual(queue.classify_error(pilot.PilotError('Worker exceeded its RSS memory budget'))[:2], ('resource_budget', False))
        self.assertEqual(queue.classify_error(ValueError('secret signed URL'), 'model_and_inference'), ('model_error', True, 'ValueError'))

    def test_public_transcript_first_avoids_audio_worker(self):
        output = self.output/'public'
        output.mkdir(parents=True)
        task = {'output': str(output), 'request': {'guid': 'one'}, 'episode': self.item, 'policy': {}}
        path = self.root/'task.json'
        pilot.atomic_json(path, task)
        public = {'text': 'Public captions content', 'source': 'youtube_transcript_api', 'url': 'https://youtube.com/watch?v=abc'}
        with patch.object(queue, 'fetch_public', return_value=public), patch.object(pilot, 'worker') as worker:
            self.assertEqual(queue.worker(path), 0)
        worker.assert_not_called()
        self.assertTrue(pilot.cached_success(output, task['request']))

    def test_public_denial_stops_captions_but_allows_original_publisher_audio(self):
        output = self.output/'public'
        output.mkdir(parents=True)
        path = self.root/'task.json'
        task = {'output': str(output), 'request': {}, 'episode': self.item, 'policy': {},
                'audio_url': self.item['audio_url']}
        pilot.atomic_json(path, task)
        def audio_worker(task_path):
            actual = json.loads(task_path.read_text())
            self.assertEqual(actual['audio_url'], self.item['audio_url'])
            pilot.atomic_json(output/'result.json', {'status': 'complete', 'stage': 'complete'})
        with patch.object(queue, 'fetch_public', side_effect=queue.QueueError('Denied', 'source_denied')), \
             patch.object(pilot, 'worker', side_effect=audio_worker) as worker:
            self.assertEqual(queue.worker(path), 0)
        worker.assert_called_once()
        result = json.loads((output/'result.json').read_text())
        self.assertEqual(result['status'], 'complete')
        self.assertEqual(result['public_caption_unavailable']['failure_kind'], 'source_denied')
        self.assertEqual(result['public_caption_unavailable']['fallback'], 'original_public_rss_audio')
        task['public_caption_unavailable'] = result['public_caption_unavailable']
        pilot.atomic_json(path, task)
        with patch.object(queue, 'fetch_public') as captions, patch.object(pilot, 'worker', side_effect=audio_worker):
            self.assertEqual(queue.worker(path), 0)
        captions.assert_not_called()  # The denied resource is never probed again.

    def test_audio_denial_still_terminal_after_caption_failure(self):
        output = self.output/'public'
        output.mkdir(parents=True)
        path = self.root/'task.json'
        pilot.atomic_json(path, {'output': str(output), 'request': {}, 'episode': self.item, 'policy': {},
                                'audio_url': self.item['audio_url']})
        with patch.object(queue, 'fetch_public', side_effect=queue.QueueError('Denied captions', 'source_denied')), \
             patch.object(pilot, 'worker', side_effect=pilot.PilotError('Audio HTTP status 403; no bypass or retry')):
            self.assertEqual(queue.worker(path), 1)
        result = json.loads((output/'result.json').read_text())
        self.assertEqual(result['failure_kind'], 'source_denied')
        self.assertFalse(result['retryable'])
        self.assertEqual(result['public_caption_unavailable']['error'], 'Denied captions')

    def test_run_episode_enforces_cpu_full_episode_limits_and_language(self):
        entry = {'episode': episode(channel='Chinese')}
        output = self.output/'worker'
        def supervise(command, work, timeout, **kwargs):
            task = json.loads((work/'task.json').read_text())
            request = task['request']
            self.assertEqual(request['model'], 'small')
            self.assertEqual(request['language'], 'zh')
            self.assertEqual(request['sample_seconds'], 0)
            self.assertEqual(request['start_seconds'], 0)
            self.assertEqual(request['device'], 'cpu')
            self.assertEqual(timeout, 1800)
            self.assertEqual(kwargs['max_rss_bytes'], 6144*1024*1024)
            self.assertNotIn('VOLC_ASR_API_KEY', kwargs['env'])
            pilot.atomic_text(output/'transcript.txt', '全文逐字稿\n')
            pilot.atomic_json(output/'segments.json', [{'start': 0, 'end': 2000, 'text': '全文逐字稿'}])
            pilot.atomic_json(output/'result.json', {'status': 'complete', 'request': request,
                                                     'transcript_sha256': pilot.digest(output/'transcript.txt'),
                                                     'segments_sha256': pilot.digest(output/'segments.json')})
            return 1
        with patch.object(pilot, 'supervise', side_effect=supervise):
            text, metadata = queue.run_episode(entry, self.sources, output, 1800, 6144)
        self.assertEqual(text, '全文逐字稿\n')
        self.assertEqual(metadata['transcript_source'], 'local_whisper')
        self.assertEqual(metadata['transcript_model'], 'small')
        self.assertEqual(metadata['transcript_language'], 'zh')

    def test_corrupt_worker_artifacts_are_never_published(self):
        output = self.output/'worker'
        with patch.object(pilot, 'supervise', return_value=1):
            with self.assertRaisesRegex(queue.QueueError, 'verification'):
                queue.run_episode({'episode': self.item}, self.sources, output, 1800, 6144)

    def test_public_caption_http_denial_and_html_do_not_fall_back(self):
        real_client = httpx.Client
        for status, mime, body in ((403, 'text/html', '<html>Denied</html>'),
                                    (200, 'text/html', '<html>Login</html>'),
                                    (200, 'text/xml', '<html>Login</html>')):
            with self.subTest(status=status, mime=mime):
                transport = httpx.MockTransport(lambda request: httpx.Response(status, headers={'content-type': mime}, text=body))
                client = real_client(transport=transport)
                with patch.object(queue.httpx, 'Client', return_value=client), \
                     patch.object(pilot, 'public_url'), self.assertRaises(queue.QueueError) as raised:
                    queue.fetch_public(self.item, {'transcript_rss_url': 'https://example.org/feed'})
                self.assertEqual(raised.exception.kind, 'source_denied')
                self.assertFalse(raised.exception.retryable)

    def test_public_caption_blocked_api_terminal_but_no_captions_allows_whisper(self):
        real_client = httpx.Client
        feed = '<rss><channel></channel></rss>'
        for error, should_raise in (('YouTube is blocking requests from your IP', True),
                                     ('No transcripts were found for any of the requested language codes', False)):
            transport = httpx.MockTransport(lambda request: httpx.Response(200, text=feed))
            client = real_client(transport=transport)
            with patch.object(queue.httpx, 'Client', return_value=client), \
                 patch.object(pilot, 'public_url'), patch.object(queue.generate_feed, 'parse_rss', return_value=[self.item]), \
                 patch.object(queue.generate_feed, 'get_youtube_transcript', return_value={'error': error}):
                if should_raise:
                    with self.assertRaises(queue.QueueError) as raised:
                        queue.fetch_public(self.item, {'transcript_rss_url': 'https://example.org/feed'})
                    self.assertEqual(raised.exception.kind, 'source_denied')
                else:
                    self.assertIsNone(queue.fetch_public(self.item, {'transcript_rss_url': 'https://example.org/feed'}))

    def test_rss_watchdog_kills_before_memory_exhaustion(self):
        with patch.object(pilot, 'process_group_rss_bytes', return_value=6145*1024*1024):
            with self.assertRaisesRegex(pilot.PilotError, 'RSS memory'):
                pilot.supervise([sys.executable, '-c', 'import time; time.sleep(30)'], self.root, 5,
                                max_rss_bytes=6144*1024*1024)

    def test_no_paid_or_youtube_audio_paths_and_sanitized_environment(self):
        source = (queue.ROOT/'scripts/cloud_whisper_queue.py').read_text()
        for forbidden in ('submit_task(', 'query_task(', 'resolve_audio_url(', 'transcribe_local(', 'yt-dlp', 'VOLC_ASR_API_KEY'):
            self.assertNotIn(forbidden, source)
        with patch.dict('os.environ', {'VOLC_ASR_API_KEY': 'secret', 'HTTP_PROXY': 'http://secret', 'HF_TOKEN': 'secret'}):
            environment = pilot.worker_environment(self.root)
        for key in ('VOLC_ASR_API_KEY', 'HTTP_PROXY', 'HF_TOKEN'):
            self.assertNotIn(key, environment)


if __name__ == '__main__':
    unittest.main()
