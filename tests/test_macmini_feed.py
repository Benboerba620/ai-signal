import json
import os
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]/'scripts'))
import macmini_feed as local
import macmini_whisper as whisper
import cloud_whisper_pilot as pilot
import subprocess
import socket


class LocalRunnerTests(unittest.TestCase):
    def test_local_checkpoint_publishes_only_owned_feed_paths(self):
        with tempfile.TemporaryDirectory() as d:
            remote = Path(d)/'remote.git'
            root = Path(d)/'repo'
            root.mkdir()
            def git(*args):
                return local.command(['git', *args], root)
            local.command(['git', 'init', '--bare', str(remote)], root)
            git('init', '-b', 'main')
            git('config', 'user.name', 'Test')
            git('config', 'user.email', 'test@example.invalid')
            (root/'scripts').mkdir()
            (root/'scripts/validate_feeds.py').write_text('raise SystemExit(0)\n')
            (root/'feeds/transcripts').mkdir(parents=True)
            for path in local.PATHS[:-1]:
                (root/path).write_text('{}\n')
            (root/'feeds/feed-x.json').write_text('{"original":true}\n')
            git('add', '.')
            git('commit', '-m', 'Initial')
            git('remote', 'add', 'origin', str(remote))
            git('push', '-u', 'origin', 'main')
            (root/'feeds/feed-podcasts.json').write_text('{"fresh":true}\n')
            (root/'feeds/transcripts/new.txt').write_text('New transcript\n')
            (root/'feeds/transcripts/.partial.txt.tmp').write_text('Do not publish')
            (root/'unrelated.tmp').write_text('Do not publish')
            head = local.checkpoint(root)
            self.assertEqual(head, git('rev-parse', 'origin/main'))
            self.assertNotIn('unrelated.tmp', git('ls-tree', '-r', '--name-only', 'HEAD'))
            self.assertNotIn('.partial.txt.tmp', git('ls-tree', '-r', '--name-only', 'HEAD'))
            self.assertEqual(git('show', 'HEAD:feeds/feed-x.json'), '{"original":true}')
            self.assertEqual(git('show', 'HEAD:feeds/transcripts/new.txt'), 'New transcript')

    def test_local_proxy_exception_does_not_allow_private_audio_destinations(self):
        address = [(socket.AF_INET, socket.SOCK_STREAM, 6, '', ('127.0.0.1', 12343))]
        with patch.object(pilot.socket, 'getaddrinfo', return_value=address):
            with pilot.public_network('http://127.0.0.1:12343'):
                self.assertEqual(socket.getaddrinfo('127.0.0.1', 12343), address)
                with self.assertRaises(pilot.PilotError):
                    pilot.public_url('https://127.0.0.1/audio')
        with self.assertRaises(pilot.PilotError), pilot.public_network('http://external.example:8080'):
            pass

    def test_mac_memory_watchdog_sums_only_worker_process_group(self):
        result = subprocess.CompletedProcess([], 0, stdout='  12 100\n  12 250\n 99 999\n')
        with patch.object(pilot.sys, 'platform', 'darwin'), patch.object(pilot.subprocess, 'run', return_value=result):
            self.assertEqual(pilot.process_group_rss_bytes(12), 350*1024)

    def test_cloud_automatic_writers_block_publication(self):
        self.assertFalse(local.cloud_is_manual('on:\n  schedule:\n    - cron: x\n'))
        self.assertFalse(local.cloud_is_manual('on:\n  push:\n  workflow_dispatch:\n'))
        self.assertTrue(local.cloud_is_manual('on:\n  workflow_dispatch:\n'))

    def test_actual_workflow_retains_only_explicit_manual_production(self):
        workflow = (local.ROOT/'.github/workflows/daily-digest.yml').read_text()
        self.assertTrue(local.cloud_is_manual(workflow))
        self.assertIn('confirm_local_paused == true', workflow)
        self.assertIn('  workflow_dispatch:', workflow)
        self.assertIn("steps.whisper.outputs.failed == '1'", workflow)

    def test_publish_refuses_dirty_checkout_before_pull(self):
        with patch.object(local, 'command', return_value=' M user-file') as command:
            with self.assertRaisesRegex(RuntimeError, 'edits'):
                local.preflight_publish(local.ROOT)
        self.assertEqual(command.call_count, 1)

    def test_publish_refuses_active_cloud_run(self):
        with patch.object(local, 'command', side_effect=['', 'main', '', '[{"status":"in_progress"}]']):
            with self.assertRaisesRegex(RuntimeError, 'active'):
                local.preflight_publish(local.ROOT)

    def test_checkpoint_whitelist_does_not_own_x_or_inbox(self):
        self.assertNotIn('feeds/feed-x.json', local.PATHS)
        self.assertNotIn('feeds/podcast-inbox', local.PATHS)
        self.assertIn('feeds/whisper-queue.json', local.PATHS)

    def test_local_engine_uses_existing_cache_offline_without_credentials(self):
        with tempfile.TemporaryDirectory() as d:
            work = Path(d)
            def inference(args, **kwargs):
                Path(args[-1]).write_text(json.dumps({'text':'test transcript', 'backend_label':'mlx-whisper medium / Apple GPU',
                                                    'transcribe_time_sec':1.5, 'language':'en'}))
            engine = {'python':'/usr/bin/python3', 'transcriber':'/existing/helper.py', 'model_cache':'/existing/cache',
                      'user_site':'/existing/python/site-packages'}
            with patch.dict(os.environ, {'API_KEY':'secret','HF_TOKEN':'secret','HTTPS_PROXY':'secret'}), \
                 patch.object(whisper.subprocess, 'run', side_effect=inference) as process:
                text, segments, stats = whisper.transcribe_local(work/'audio.wav', 'en', engine, work)
            self.assertEqual(text, 'test transcript')
            env = process.call_args.kwargs['env']
            self.assertEqual(env['HF_HUB_OFFLINE'], '1')
            self.assertEqual(env['HF_HUB_CACHE'], '/existing/cache')
            self.assertEqual(env['WHISPER_DEVICE'], 'mlx')
            self.assertEqual(env['PYTHONPATH'], '/existing/python/site-packages')
            for name in ('API_KEY','HF_TOKEN','HTTPS_PROXY'):
                self.assertNotIn(name, env)


if __name__ == '__main__':
    unittest.main()
