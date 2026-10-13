import argparse
from contextlib import redirect_stdout
from datetime import datetime, timedelta, timezone
import fcntl
import io
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

import httpx
sys.path.insert(0, str(Path(__file__).resolve().parents[1]/'scripts'))
import feed_resilience as r
import macmini_feed as runner
import macmini_health as h
import cloud_whisper_queue as q
import cloud_whisper_pilot as p


class RecoveryTests(unittest.TestCase):
    def test_retry_after_delta_date_invalid(self):
        now = datetime(2026, 10, 13, tzinfo=timezone.utc)
        self.assertEqual(r.retry_after('60', now), now+timedelta(seconds=60))
        self.assertEqual(r.retry_after('Tue, 13 Oct 2026 00:01:00 GMT', now), now+timedelta(seconds=60))
        self.assertIsNone(r.retry_after('broken', now))

    def response(self, status, headers=None):
        return httpx.Response(status, headers=headers, request=httpx.Request('GET', 'https://example.org/rss'))

    def test_http_transient_bounded_and_forbidden_terminal(self):
        for status, count in ((503, 3), (429, 3), (403, 1), (401, 1), (404, 1)):
            with self.subTest(status=status), patch.object(r.httpx, 'get', return_value=self.response(status)) as get, patch.object(r.time, 'sleep'):
                r.bounded_get('https://example.org/rss')
                self.assertEqual(get.call_count, count)

    def test_retry_after_not_shortened_and_persists_across_calls(self):
        with tempfile.TemporaryDirectory() as d, patch.dict(os.environ, {'AI_SIGNAL_HTTP_STATE': str(Path(d)/'http.json')}), patch.object(r.httpx, 'get', return_value=self.response(429, {'Retry-After': '7200'})) as get, patch.object(r.time, 'sleep') as sleep:
            r.bounded_get('https://example.org/rss')
            r.bounded_get('https://example.org/other')
            self.assertEqual(get.call_count, 1)
            sleep.assert_not_called()
            self.assertNotIn('example.org', (Path(d)/'http.json').read_text())

    def test_unknown_http_error_never_retried(self):
        with patch.object(r.httpx, 'get', side_effect=ValueError('bad')) as get:
            with self.assertRaises(ValueError):
                r.bounded_get('https://example.org')
            self.assertEqual(get.call_count, 1)

    def test_transport_retries_three_times(self):
        with patch.object(r.httpx, 'get', side_effect=httpx.ConnectError('private URL')) as get, patch.object(r.time, 'sleep'):
            with self.assertRaises(httpx.ConnectError):
                r.bounded_get('https://example.org')
            self.assertEqual(get.call_count, 3)

    def test_audio_retry_after_survives_worker_and_queue_failure(self):
        now = h.now()
        error = p.TemporaryAudioError(429, '86400')
        kind, retryable, text = q.classify_error(error)
        entry = {'attempts': 1}
        q.record_failure(entry, now, text, kind, retryable, error.retry_not_before)
        self.assertEqual(entry['status'], 'retry')
        self.assertGreaterEqual(h.date(entry['next_retry_at']), now+timedelta(hours=23))
        entry['attempts'] = 3
        q.record_failure(entry, now, text, kind, retryable, error.retry_not_before)
        self.assertEqual(entry['status'], 'failed')

    def test_unknown_worker_and_budget_errors_are_terminal(self):
        self.assertFalse(q.classify_error(ValueError('secret'))[1])
        self.assertEqual(q.classify_error(p.PilotError('Pilot exceeded its 30s total wall-time budget'))[:2], ('resource_budget', False))

    def test_command_failure_classification_and_redaction(self):
        self.assertTrue(r.command_failure('Connection reset').retryable)
        self.assertFalse(r.command_failure('Permission denied').retryable)
        self.assertFalse(r.command_failure('CONFLICT').retryable)
        self.assertFalse(r.command_failure('arbitrary instructions').retryable)
        clean = r.redact('https://user:pw@host/x?token=abcdef Authorization: secret ghp_ABC123')
        for token in ('user:pw', 'abcdef', 'secret', 'ABC123'):
            self.assertNotIn(token, clean)

    def test_process_timeout_kills_descendant(self):
        with tempfile.TemporaryDirectory() as d:
            marker = Path(d)/'late'
            child = "import time; from pathlib import Path; time.sleep(1); Path(%r).write_text('bad')" % str(marker)
            parent = 'import subprocess,sys,time; subprocess.Popen([sys.executable,"-c",%r]); time.sleep(10)' % child
            with self.assertRaises(subprocess.TimeoutExpired):
                r.process([sys.executable, '-c', parent], cwd=d, timeout=.1)
            import time
            time.sleep(1.1)
            self.assertFalse(marker.exists())

    def args(self, state, publish=False):
        return argparse.Namespace(state_dir=str(state), publish=publish, scope='arxiv', asr_python=sys.executable,
                                  transcriber=None, model_cache=None, audio_proxy=None, notifier_module=None)

    def test_preflight_failure_report_and_three_failure_circuit(self):
        with tempfile.TemporaryDirectory() as d:
            root, state = Path(d)/'repo', Path(d)/'state'
            root.mkdir()
            with patch.object(runner, 'preflight_publish', side_effect=r.OperationError('credentials_or_permission')) as preflight, redirect_stdout(io.StringIO()):
                for _ in range(3):
                    self.assertEqual(runner.run(self.args(state, True), root), 1)
                self.assertEqual(runner.run(self.args(state, True), root), 75)
                self.assertEqual(preflight.call_count, 3)
            self.assertTrue(h.circuit(state, 'arxiv')['open'])
            self.assertEqual(h.read(state/'latest-arxiv.json')['status'], 'circuit_open')
            self.assertEqual(len(list(state.glob('*/diagnostic.json'))), 3)
            self.assertFalse((state/'active.json').exists())

    def test_lock_contention_does_not_overwrite_report(self):
        with tempfile.TemporaryDirectory() as d:
            state, root = Path(d)/'state', Path(d)/'repo'
            state.mkdir(); root.mkdir()
            (state/'latest.json').write_text('original')
            with (state/'run.lock').open('a') as lock, redirect_stdout(io.StringIO()):
                fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
                self.assertEqual(runner.run(self.args(state), root), 75)
            self.assertEqual((state/'latest.json').read_text(), 'original')

    def test_freshness_rejects_old_and_future_artifacts(self):
        with tempfile.TemporaryDirectory() as d:
            root = Path(d); (root/'feeds').mkdir()
            for delta in (-24*60, 10):
                p.atomic_json(root/'feeds/feed-arxiv.json', {'papers': [], 'generated_at': (h.now()+timedelta(minutes=delta)).isoformat()})
                with self.assertRaisesRegex(r.OperationError, 'stale_artifacts'):
                    h.accept(root, 'arxiv', h.now().isoformat())
            p.atomic_json(root/'feeds/feed-arxiv.json', {'papers': [], 'generated_at': h.now().isoformat()})
            self.assertEqual(h.accept(root, 'arxiv', h.now().isoformat())['arxiv']['items'], 0)

    def test_success_resets_pipeline_circuit_despite_source_degradation(self):
        with tempfile.TemporaryDirectory() as d:
            state = Path(d)
            report = {'scope': 'all', 'status': 'failed', 'finished_at': h.now().isoformat()}
            h.record(state, report)
            report.update(status='completed', source_health='degraded')
            h.record(state, report)
            self.assertEqual(h.circuit(state, 'all')['consecutive_failures'], 0)
            self.assertEqual(h.circuit(state, 'all')['source_health'], 'degraded')

    def test_notification_dedup_and_ambiguous_delivery_no_resend(self):
        with tempfile.TemporaryDirectory() as d:
            state = Path(d); module = state/'notifier.py'; sent = state/'sent.txt'
            module.write_text('from pathlib import Path\ndef send(text):\n    p=Path(%r)\n    p.write_text((p.read_text() if p.exists() else "") + text + "\\n")\n    return {"status":"delivery_unknown"}\n' % str(sent))
            report = {'scope':'all', 'publish':True, 'status':'failed', 'failure_kind':'temporary_network'}
            h.notify(state, report, str(module)); before = sent.read_text()
            h.notify(state, report, str(module))
            self.assertEqual(sent.read_text(), before)
            self.assertEqual(h.read(state/'alert-all.json')['delivery'], 'delivery_unknown')
            report.update(status='completed', source_health='ok')
            h.notify(state, report, str(module))
            self.assertIn('已验收恢复', sent.read_text())

    def test_monitor_weekend_and_active_deadline(self):
        with tempfile.TemporaryDirectory() as d:
            state = Path(d)
            at = datetime(2026,10,13,1,40,tzinfo=timezone.utc)
            p.atomic_json(state/'active.json', {'scope':'arxiv', 'deadline':(at+timedelta(minutes=10)).isoformat()})
            self.assertEqual(h.monitor(state, state, at=at)['arxiv'], 'running')
            self.assertEqual(h.monitor(state, state, at=at+timedelta(minutes=30))['arxiv'], 'missed_or_failed_schedule')

    def test_journal_mismatch_preserves_user_edits(self):
        with tempfile.TemporaryDirectory() as d:
            journal = Path(d)/'pending.json'
            p.atomic_json(journal, {'base':'abc', 'files':{'feeds/feed-arxiv.json':'old'}})
            with patch.object(runner, 'JOURNAL', journal), patch.object(runner, 'command', side_effect=['main','abc']), patch.object(runner, 'manifest', return_value={'feeds/feed-arxiv.json':'user-edit'}), patch.object(runner, 'checkpoint') as checkpoint:
                with self.assertRaisesRegex(r.OperationError, 'needs_review'):
                    runner.recover_publication(Path(d))
                checkpoint.assert_not_called()
            self.assertTrue(journal.exists())

    def test_publication_ack_loss_resumes_without_duplicate_commit(self):
        with tempfile.TemporaryDirectory() as d:
            root, remote = Path(d)/'repo', Path(d)/'remote.git'
            root.mkdir()
            def git(*args):
                return runner.command(['git', *args], root)
            runner.command(['git', 'init', '--bare', str(remote)], root)
            git('init', '-b', 'main'); git('config','user.name','Test'); git('config','user.email','test@example.invalid')
            (root/'scripts').mkdir(); (root/'scripts/validate_feeds.py').write_text('raise SystemExit(0)')
            (root/'feeds/transcripts').mkdir(parents=True)
            (root/'.github/workflows').mkdir(parents=True)
            (root/'.github/workflows/daily-digest.yml').write_text('on:\n  workflow_dispatch:\n')
            for path in runner.PATHS[:-1]:
                (root/path).write_text('{}')
            git('add','.'); git('commit','-m','Initial'); git('remote','add','origin',str(remote)); git('push','-u','origin','main')
            (root/'feeds/feed-arxiv.json').write_text('{"papers":[]}')
            original = runner.command
            def lost_ack(args, cwd=runner.ROOT, timeout=120):
                result = original(args, cwd, timeout)
                if args[:2] == ['git','push']:
                    raise r.OperationError('temporary_network', True)
                return result
            journal = Path(d)/'publication.json'
            with patch.object(runner, 'JOURNAL', journal):
                with patch.object(runner,'command',side_effect=lost_ack):
                    with self.assertRaises(r.OperationError):
                        runner.checkpoint(root)
                published = git('rev-parse','HEAD')
                count = git('rev-list','--count','HEAD')
                def verified(args, cwd=runner.ROOT, timeout=120):
                    return '[]' if args[0]=='gh' else original(args,cwd,timeout)
                with patch.object(runner,'command',side_effect=verified):
                    runner.recover_publication(root)
                self.assertEqual(git('rev-parse','HEAD'), published)
                self.assertEqual(git('rev-list','--count','HEAD'), count)
                self.assertEqual(git('rev-parse','origin/main'), published)
                self.assertFalse(journal.exists())

    def test_queue_worker_serializes_throttle_without_caption_fallback(self):
        with tempfile.TemporaryDirectory() as d:
            root = Path(d); output = root/'output'; output.mkdir()
            task = root/'task.json'
            p.atomic_json(task, {'output':str(output), 'request':{'guid':'one'}, 'episode':{}, 'policy':{}})
            with patch.object(q,'fetch_public',side_effect=p.TemporaryAudioError(429,'86400')), patch.object(p,'worker') as audio:
                self.assertEqual(q.worker(task),1)
                audio.assert_not_called()
            result=h.read(output/'result.json')
            self.assertEqual(result['failure_kind'],'temporary_http')
            self.assertTrue(result['retryable'])
            self.assertGreater(h.date(result['retry_not_before']),h.now()+timedelta(hours=23))
            self.assertNotIn('public_caption_unavailable',result)

    def test_hash_tampering_fails_real_feed_acceptance(self):
        with tempfile.TemporaryDirectory() as d:
            root=Path(d); (root/'feeds/transcripts').mkdir(parents=True)
            stamp=h.now().isoformat()
            for name,key in [('podcasts','podcasts'),('arxiv','papers'),('blogs','articles')]:
                p.atomic_json(root/f'feeds/feed-{name}.json', {'generated_at':stamp,key:[]})
            transcript=root/'feeds/transcripts/test.txt'; transcript.write_text('original\n')
            record={'transcript_path':'feeds/transcripts/test.txt','transcript_chars':9,'transcript_sha256':p.digest(transcript)}
            p.atomic_json(root/'feeds/feed-transcripts-index.json',{'transcripts':[record]})
            p.atomic_json(root/'feeds/whisper-queue.json',{'entries':{}})
            h.accept(root,'all',stamp)
            transcript.write_text('tampered\n')
            with self.assertRaisesRegex(r.OperationError,'invalid_artifacts'):
                h.accept(root,'all',stamp)


if __name__ == '__main__':
    unittest.main()
