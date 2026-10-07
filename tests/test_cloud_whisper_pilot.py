import json
from pathlib import Path
import socket
import subprocess
import sys
import tempfile
import time
import unittest
from unittest.mock import patch

import httpx

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'scripts'))
import cloud_whisper_pilot as pilot


class Stream(httpx.SyncByteStream):
    def __init__(self, body):
        self.body = body

    def __iter__(self):
        yield self.body


class PilotTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)

    def download(self, body=b'ID3' + b'a' * 2045, mime='audio/mpeg', headers=None, status=200, max_bytes=4096):
        response_headers = {'content-type': mime, **(headers or {})}
        transport = httpx.MockTransport(lambda request: httpx.Response(status, headers=response_headers,
                                                                        stream=Stream(body)))
        with httpx.Client(transport=transport) as client, patch.object(pilot, 'public_url'):
            return pilot.download_audio('https://publisher.example/audio.mp3', self.root / 'audio', client,
                                        max_bytes=max_bytes)

    def test_validated_download(self):
        result = self.download(headers={'content-length': '2048'})
        self.assertEqual(result['download_bytes'], 2048)
        self.assertEqual(len(result['audio_sha256']), 64)

    def test_reject_html_content_type_even_if_status_200(self):
        with self.assertRaisesRegex(pilot.PilotError, 'Content-Type'):
            self.download(mime='text/html')

    def test_reject_html_body_with_spoofed_mime(self):
        with self.assertRaisesRegex(pilot.PilotError, 'HTML'):
            self.download(body=b'<html>' + b'a' * 2000)

    def test_reject_tiny_body(self):
        with self.assertRaises(pilot.PilotError):
            self.download(body=b'ID3')

    def test_reject_truncated_body(self):
        with self.assertRaisesRegex(pilot.PilotError, 'truncated'):
            self.download(headers={'content-length': '3000'})

    def test_declared_length_budget(self):
        with self.assertRaisesRegex(pilot.PilotError, 'Content-Length'):
            self.download(headers={'content-length': '999999999'})

    def test_streamed_body_budget_without_content_length(self):
        with self.assertRaisesRegex(pilot.PilotError, 'byte budget'):
            self.download(max_bytes=1024)

    def test_status_and_encoding_rejected(self):
        for kwargs in ({'status': 403}, {'status': 206}, {'headers': {'content-encoding': 'gzip'}}):
            with self.subTest(kwargs=kwargs), self.assertRaises(pilot.PilotError):
                self.download(**kwargs)

    def test_signed_query_removed_from_artifact(self):
        self.assertEqual(pilot.artifact_url('https://example.org/file?token=secret#x'), 'https://example.org/file')

    def test_public_destinations_only(self):
        for url in ('http://example.org/x', 'file:///x', 'https://user:pass@example.org/x', 'https://example.org:8443/x'):
            with self.subTest(url=url), self.assertRaises(pilot.PilotError):
                pilot.public_url(url)
        address = [(socket.AF_INET, socket.SOCK_STREAM, 6, '', ('127.0.0.1', 443))]
        with patch.object(socket, 'getaddrinfo', return_value=address), self.assertRaises(pilot.PilotError):
            pilot.public_url('https://localhost/x')

    def test_connect_time_dns_change_is_rejected(self):
        public = [(socket.AF_INET, socket.SOCK_STREAM, 6, '', ('8.8.8.8', 443))]
        private = [(socket.AF_INET, socket.SOCK_STREAM, 6, '', ('127.0.0.1', 443))]
        with patch.object(socket, 'getaddrinfo', side_effect=[public, private]) as original:
            with pilot.public_network():
                pilot.public_url('https://example.org/x')
                with self.assertRaisesRegex(pilot.PilotError, 'Connection resolved'):
                    socket.getaddrinfo('example.org', 443)
            self.assertIs(socket.getaddrinfo, original)

    def test_worker_env_isolates_caches_and_credentials(self):
        with patch.dict(pilot.os.environ, {'HF_TOKEN': 'secret', 'HTTPS_PROXY': 'secret',
                                          'VOLC_ASR_API_KEY': 'secret', 'HOME': '/private'}):
            env = pilot.worker_environment(self.root)
        for key in ('HF_TOKEN', 'HTTPS_PROXY', 'VOLC_ASR_API_KEY'):
            self.assertNotIn(key, env)
        for key in ('HOME', 'HF_HOME', 'HF_HUB_CACHE', 'HF_XET_CACHE', 'XDG_CACHE_HOME'):
            self.assertTrue(Path(env[key]).is_relative_to(self.root))
        self.assertEqual(env['HF_HUB_DISABLE_IMPLICIT_TOKEN'], '1')
        self.assertEqual(env['HF_HUB_DISABLE_XET'], '1')

    def test_redirects_revalidated(self):
        transport = httpx.MockTransport(lambda request: httpx.Response(302, headers={'location': 'https://127.0.0.1/x'}))
        with httpx.Client(transport=transport) as client:
            with patch.object(pilot, 'public_url', side_effect=[None, pilot.PilotError('private')]) as guard:
                with self.assertRaisesRegex(pilot.PilotError, 'private'):
                    pilot.download_audio('https://example.org/x', self.root / 'audio', client)
                self.assertEqual(guard.call_count, 2)

    def test_redirect_limit(self):
        transport = httpx.MockTransport(lambda request: httpx.Response(302, headers={'location': '/again'}))
        with httpx.Client(transport=transport) as client, patch.object(pilot, 'public_url'):
            with self.assertRaisesRegex(pilot.PilotError, 'Too many'):
                pilot.download_audio('https://example.org/x', self.root / 'audio', client)

    def test_policy_selection_has_no_force_or_youtube_download(self):
        sources = {'podcasts': {'channels': [
            {'name': 'allowed', 'transcribe_missing': True}, {'name': 'disabled', 'transcribe_missing': False}]}}
        base = {'guid': 'one', 'channel': 'allowed', 'audio_url': 'https://example.org/x'}
        feed = {'podcasts': [base, dict(base, guid='two', channel='disabled'),
                             dict(base, guid='three', audio_url='', link='https://youtube.com/watch?v=x'),
                             dict(base, guid='four', transcript='already done')]}
        self.assertEqual(pilot.select_episode(feed, sources, None), [base])
        self.assertEqual(pilot.select_episode(feed, sources, 'one'), base)
        for guid in ('two', 'three', 'four', 'missing'):
            with self.assertRaises(pilot.PilotError):
                pilot.select_episode(feed, sources, guid)
        feed['podcasts'].append(base)
        with self.assertRaises(pilot.PilotError):
            pilot.select_episode(feed, sources, 'one')

    def test_real_probe_and_decode_duration(self):
        if not pilot.shutil.which('ffmpeg'):
            self.skipTest('ffmpeg not installed')
        audio = self.root / 'sample.wav'
        subprocess.run(['ffmpeg', '-v', 'error', '-f', 'lavfi', '-i', 'sine=frequency=440:duration=11',
                        '-ar', '16000', '-ac', '1', str(audio)], check=True)
        self.assertAlmostEqual(pilot.probe_audio(audio, 11), 11, places=1)
        with self.assertRaisesRegex(pilot.PilotError, 'match'):
            pilot.probe_audio(audio, 300)

    def test_probe_rejects_nonfinite_duration_or_no_audio(self):
        for data in ({'format': {'duration': 'NaN'}, 'streams': [{'codec_type': 'audio'}]},
                     {'format': {'duration': '60'}, 'streams': [{'codec_type': 'video'}]}):
            result = subprocess.CompletedProcess([], 0, stdout=json.dumps(data))
            with patch.object(subprocess, 'run', return_value=result), self.assertRaises(pilot.PilotError):
                pilot.probe_audio('unused')

    def test_cache_requires_complete_matching_hashed_artifacts(self):
        request = {'guid': 'one', 'sample_seconds': 60}
        text = self.root / 'transcript.txt'
        segments = self.root / 'segments.json'
        text.write_text('words')
        segments.write_text('[]')
        result = {'status': 'complete', 'request': request, 'transcript_sha256': pilot.digest(text),
                  'segments_sha256': pilot.digest(segments)}
        pilot.atomic_json(self.root / 'result.json', result)
        self.assertTrue(pilot.cached_success(self.root, request))
        self.assertFalse(pilot.cached_success(self.root, {'guid': 'two'}))
        text.write_text('damaged')
        self.assertFalse(pilot.cached_success(self.root, request))
        text.write_text('words')
        result['status'] = 'failed'
        pilot.atomic_json(self.root / 'result.json', result)
        self.assertFalse(pilot.cached_success(self.root, request))

    def test_worker_failure_is_not_success(self):
        with self.assertRaisesRegex(pilot.PilotError, 'exit 3'):
            pilot.supervise([sys.executable, '-c', 'raise SystemExit(3)'], self.root, 5)

    def test_hard_timeout_kills_worker(self):
        start = time.monotonic()
        with self.assertRaisesRegex(pilot.PilotError, 'wall-time'):
            pilot.supervise([sys.executable, '-c', 'import time; time.sleep(30)'], self.root, .3)
        self.assertLess(time.monotonic() - start, 3)

    def test_storage_budget_kills_worker(self):
        (self.root / 'large').write_bytes(b'a' * 100)
        with self.assertRaisesRegex(pilot.PilotError, 'storage'):
            pilot.supervise([sys.executable, '-c', 'import time; time.sleep(30)'], self.root, 5, max_bytes=99)

    def test_failure_is_saved_and_lock_cleared(self):
        import argparse
        args = argparse.Namespace(list=False, guid='one', model='small.en', language='en', threads=4,
                                  sample_seconds=60, start_seconds=0, output_dir=str(self.root), timeout_seconds=1)
        item = {'guid': 'one', 'title': 'episode', 'channel': 'test', 'audio_url': 'https://example.org/x'}
        with patch.object(pilot, 'select_episode', return_value=item), patch.object(pilot, 'supervise', side_effect=pilot.PilotError('timeout')):
            self.assertEqual(pilot.run(args), 1)
        results = list(self.root.glob('*/result.json'))
        self.assertEqual(len(results), 1)
        self.assertEqual(json.loads(results[0].read_text())['status'], 'failed')
        self.assertFalse(list(self.root.glob('*/.running')))

    def test_initial_output_failure_does_not_leave_lock(self):
        import argparse
        args = argparse.Namespace(list=False, guid='one', model='small.en', language='en', threads=4,
                                  sample_seconds=60, start_seconds=0, output_dir=str(self.root), timeout_seconds=1)
        item = {'guid': 'one', 'title': 'episode', 'channel': 'test', 'audio_url': 'https://example.org/x'}
        with patch.object(pilot, 'select_episode', return_value=item), patch.object(pilot, 'atomic_json', side_effect=OSError('disk full')):
            self.assertEqual(pilot.run(args), 1)
        self.assertFalse(list(self.root.glob('*/.running')))

    def test_worker_writes_complete_artifacts_with_mock_inference(self):
        self.check_worker_artifacts(sample_seconds=60)

    def test_full_worker_writes_complete_artifacts_with_mock_inference(self):
        self.check_worker_artifacts(sample_seconds=0)

    def check_worker_artifacts(self, sample_seconds):
        if not pilot.shutil.which('ffmpeg'):
            self.skipTest('ffmpeg not installed')
        source = self.root / 'source.wav'
        subprocess.run(['ffmpeg', '-v', 'error', '-f', 'lavfi', '-i', 'sine=frequency=440:duration=11',
                        '-ar', '16000', '-ac', '1', str(source)], check=True)
        output = self.root / 'results'
        output.mkdir()
        request = {'start_seconds': 0, 'sample_seconds': sample_seconds, 'model': 'small.en', 'language': 'en', 'threads': 4}
        task = {'output': str(output), 'request': request, 'audio_url': 'https://example.org/audio',
                'expected_seconds': 11, 'policy': {}}
        pilot.atomic_json(self.root / 'task.json', task)

        def downloaded(url, path, client):
            pilot.shutil.copyfile(source, path)
            return {'download_bytes': source.stat().st_size}

        fake = ('Mock words', [{'start': 0, 'end': 10, 'text': 'Mock words'}], {'inference_seconds': 1})
        with patch.object(pilot, 'download_audio', side_effect=downloaded), patch.object(pilot, 'transcribe', return_value=fake), patch.object(pilot.resource, 'setrlimit'):
            pilot.worker(self.root / 'task.json')
        self.assertTrue(pilot.cached_success(output, request))
        result = json.loads((output / 'result.json').read_text())
        self.assertTrue(result['quality_review_required'])
        self.assertEqual(result['status'], 'complete')

    def test_production_output_paths_rejected(self):
        import argparse
        args = argparse.Namespace(list=False, guid='one', model='small.en', language='en', threads=4,
                                  sample_seconds=60, start_seconds=0, output_dir=str(pilot.ROOT / 'feeds'), timeout_seconds=1)
        item = {'guid': 'one', 'title': 'episode', 'channel': 'test', 'audio_url': 'https://example.org/x'}
        with patch.object(pilot, 'select_episode', return_value=item), self.assertRaisesRegex(pilot.PilotError, 'output'):
            pilot.run(args)

    def test_full_episode_requires_zero_offset(self):
        import argparse
        args = argparse.Namespace(list=False, guid='one', model='small.en', language='en', threads=4,
                                  sample_seconds=0, start_seconds=60, output_dir=str(self.root), timeout_seconds=1)
        with self.assertRaisesRegex(pilot.PilotError, 'Full-episode runs require'):
            pilot.run(args)

    def test_workflow_opt_in_and_no_write_or_secrets(self):
        source = (pilot.ROOT / '.github/workflows/whisper-pilot.yml').read_text()
        self.assertIn('workflow_dispatch:', source)
        for forbidden in ('schedule:', 'pull_request:', 'pull_request_target:', 'secrets.', 'contents: write', 'git push', 'transcribe_missing_podcasts.py'):
            self.assertNotIn(forbidden, source)
        self.assertIn("branches: ['codex/cloud-whisper-pilot']", source)
        self.assertIn("paths: ['.github/whisper-pilot-request.txt']", source)
        self.assertIn("contains(github.event.head_commit.message, '[run-whisper-full]')", source)
        self.assertIn("github.ref == 'refs/heads/codex/cloud-whisper-pilot'", source)
        self.assertIn("github.event_name == 'push' && '0'", source)
        self.assertEqual(source.count("github.event_name == 'push' && '0'"), 2)
        self.assertIn('--threads 4 --timeout-seconds 1800', source)
        self.assertIn('persist-credentials: false', source)
        self.assertIn('if: always()', source)
        self.assertIn('sudo apt-get install -y --no-install-recommends ffmpeg', source)


if __name__ == '__main__':
    unittest.main()
