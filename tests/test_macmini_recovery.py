from datetime import datetime, timedelta, timezone
import copy
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch
import argparse
from contextlib import redirect_stdout
import io

sys.path.insert(0, str(Path(__file__).resolve().parents[1]/'scripts'))
import macmini_recovery as repair
import macmini_feed as runner
import cloud_whisper_queue as queue
import cloud_whisper_pilot as pilot
import macmini_health as health


class AutomaticRepairTests(unittest.TestCase):
    def test_coverage_includes_only_current_pending_transcripts(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory); (root/'feeds').mkdir()
            items = [{'channel': 'one', 'guid': 'a'},
                     {'channel': 'one', 'guid': 'b', 'transcript_available': True}]
            pilot.atomic_json(root/'feeds/feed-podcasts.json', {'podcasts': items})
            entries = {queue.identity(items[0]): {'status': 'pending'},
                       queue.identity(items[1]): {'status': 'completed'},
                       'historical': {'status': 'pending'}}
            self.assertEqual(runner.transcript_coverage(root, {'entries': entries}),
                             {'podcast_items': 2, 'full_transcripts': 1, 'pending_transcripts': 1})

    def repository(self, root):
        def git(*args):
            return runner.command(['git', *args], root)
        git('init', '-b', 'main')
        git('config', 'user.name', 'Test')
        git('config', 'user.email', 'test@example.invalid')
        (root/'feeds').mkdir()
        pilot.atomic_json(root/'feeds/whisper-queue.json',
                          {'schema_version': 1, 'entries': {}, 'updated_at': '2026-10-14T00:00:00+00:00'})
        git('add', '.'); git('commit', '-m', 'Initial')
        return git

    def test_timestamp_repair_preserves_exact_backup_and_git_blob(self):
        with tempfile.TemporaryDirectory() as directory:
            root, backups = Path(directory)/'repo', Path(directory)/'backup'
            root.mkdir(); git = self.repository(root)
            path = root/'feeds/whisper-queue.json'
            baseline = path.read_bytes()
            state = json.loads(path.read_text()); state['updated_at'] = '2026-10-15T00:00:00+00:00'
            pilot.atomic_json(path, state); changed = path.read_bytes()
            actions = []
            repair.repair_timestamp(root, backups, runner.command, actions)
            self.assertEqual(path.read_bytes(), baseline)
            self.assertEqual(Path(actions[0]['backup']).read_bytes(), changed)
            self.assertEqual(git('status', '--porcelain'), '')

    def test_real_queue_changes_other_files_and_staged_edits_are_never_restored(self):
        for mode in ['attempt', 'other-file', 'staged', 'backward-time']:
            with self.subTest(mode=mode), tempfile.TemporaryDirectory() as directory:
                root = Path(directory); git = self.repository(root)
                path = root/'feeds/whisper-queue.json'
                state = json.loads(path.read_text()); state['updated_at'] = '2026-10-15T00:00:00+00:00'
                if mode == 'attempt': state['entries']['one'] = {'attempts': 2}
                if mode == 'backward-time': state['updated_at'] = '2026-10-13T00:00:00+00:00'
                pilot.atomic_json(path, state)
                if mode == 'other-file': (root/'notes.txt').write_text('user work')
                if mode == 'staged': git('add', '.')
                original = path.read_bytes()
                self.assertIsNone(repair.timestamp_only(root, runner.command))
                self.assertEqual(path.read_bytes(), original)

    def test_queue_noop_does_not_touch_time_but_new_entry_is_saved(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory); self.repository(root)
            path = root/'feeds/whisper-queue.json'
            original = path.read_bytes(); state = queue.load_state(root)
            queue.save_state(root, state, repair.now())
            self.assertEqual(path.read_bytes(), original)
            state['entries']['new'] = {'attempts': 1}
            queue.save_state(root, state, repair.now())
            self.assertNotEqual(path.read_bytes(), original)
            self.assertEqual(json.loads(path.read_text())['entries']['new']['attempts'], 1)

    def retention(self, root, at):
        (root/'feeds/transcripts').mkdir(parents=True)
        old = root/'feeds/transcripts/old.txt'; old.write_text('old original\n')
        current = root/'feeds/transcripts/current.txt'; current.write_text('current original\n')
        records = [{'transcript_path': str(path.relative_to(root)), 'expires_at': (at-timedelta(days=2)).isoformat(),
                    'transcript_chars': len(path.read_text()), 'transcript_sha256': pilot.digest(path)}
                   for path in (old, current)]
        pilot.atomic_json(root/'feeds/feed-transcripts-index.json', {'retention_days': 14, 'transcripts': records})
        pilot.atomic_json(root/'feeds/feed-podcasts.json', {'generated_at': '2026-10-14T00:00:00+00:00',
                          'podcasts': [{**records[1], 'transcript_available': True}]})
        return old, current

    def test_expiry_repair_preserves_active_text_and_does_not_fake_feed_freshness(self):
        with tempfile.TemporaryDirectory() as directory:
            root, backups = Path(directory)/'repo', Path(directory)/'backup'
            at = repair.now(); old, current = self.retention(root, at)
            feed = (root/'feeds/feed-podcasts.json').read_bytes()
            current_bytes = current.read_bytes(); old_bytes = old.read_bytes()
            actions = []
            self.assertTrue(repair.prune_retention(root, backups, actions, at))
            self.assertFalse(old.exists()); self.assertEqual(current.read_bytes(), current_bytes)
            self.assertEqual((root/'feeds/feed-podcasts.json').read_bytes(), feed)
            self.assertTrue(any(p.read_bytes() == old_bytes for p in backups.iterdir()))
            records = json.loads((root/'feeds/feed-transcripts-index.json').read_text())['transcripts']
            self.assertEqual(len(records), 1)
            self.assertEqual(repair.stamp(records[0]['expires_at']), at+timedelta(days=14))
            self.assertFalse(repair.prune_retention(root, backups, actions, at))

    def test_corrupt_active_text_and_unsafe_paths_stop_before_any_pruning(self):
        for mode in ['hash', 'path']:
            with self.subTest(mode=mode), tempfile.TemporaryDirectory() as directory:
                root = Path(directory); at = repair.now(); old, current = self.retention(root, at)
                index_path = root/'feeds/feed-transcripts-index.json'
                if mode == 'hash': current.write_text('corrupted')
                else:
                    index = json.loads(index_path.read_text())
                    index['transcripts'][0]['transcript_path'] = '../../private.txt'
                    pilot.atomic_json(index_path, index)
                before = index_path.read_bytes()
                with self.assertRaisesRegex(RuntimeError, 'invalid_artifacts'):
                    repair.prune_retention(root, root/'backup', [], at)
                self.assertTrue(old.exists()); self.assertEqual(index_path.read_bytes(), before)

    def test_recovery_is_once_per_day_and_does_not_retry_unknown_errors(self):
        with tempfile.TemporaryDirectory() as directory:
            state = Path(directory); at = repair.now()
            self.assertTrue(repair.reserve(state, 'all', at))
            self.assertFalse(repair.reserve(state, 'all', at+timedelta(hours=23)))
            self.assertTrue(repair.reserve(state, 'arxiv', at))
            self.assertTrue(repair.reserve(state, 'all', at+timedelta(hours=24)))
            report = state/'20261018T000000Z/report.json'; report.parent.mkdir()
            for kind, expected in [('credentials_or_permission', False), ('publication_conflict', False),
                                   ('unknown_command_failure', False), ('temporary_network', True),
                                   ('expired_retention', True), ('process_timeout', True)]:
                pilot.atomic_json(report, {'scope': 'all', 'status': 'failed', 'failure_kind': kind})
                self.assertEqual(repair.allowed(state, state, 'all', runner.command), expected)

    def test_open_circuit_known_timestamp_fault_really_publishes_and_recovers(self):
        with tempfile.TemporaryDirectory() as directory:
            root, state, remote = (Path(directory)/name for name in ('repo', 'state', 'remote.git'))
            root.mkdir(); git = self.repository(root); state.mkdir()
            subprocess.run(['git', 'init', '--bare', str(remote)], check=True, capture_output=True)
            (root/'scripts').mkdir(); (root/'.github/workflows').mkdir(parents=True)
            (root/'.github/workflows/daily-digest.yml').write_text('on:\n  workflow_dispatch:\n')
            (root/'scripts/validate_feeds.py').write_text('raise SystemExit(0)\n')
            generator = '''import json,sys\nfrom pathlib import Path\nfrom datetime import datetime,timezone\nnames={'--arxiv-only':('arxiv','papers'),'--blogs-only':('blogs','articles'),'--podcasts-only':('podcasts','podcasts')}\nname,key=names[sys.argv[1]]\nPath('feeds/feed-'+name+'.json').write_text(json.dumps({key:[],'generated_at':datetime.now(timezone.utc).isoformat()}))\n'''
            (root/'scripts/generate_feed.py').write_text(generator)
            (root/'feeds/transcripts').mkdir()
            for name, key in [('podcasts', 'podcasts'), ('blogs', 'articles'), ('arxiv', 'papers')]:
                pilot.atomic_json(root/f'feeds/feed-{name}.json', {key: [], 'generated_at': '2026-10-14T00:00:00+00:00'})
            pilot.atomic_json(root/'feeds/feed-transcripts-index.json', {'retention_days': 14, 'transcripts': []})
            git('add', '.'); git('commit', '-m', 'Fixture'); git('remote', 'add', 'origin', str(remote)); git('push', '-u', 'origin', 'main')
            queue_path = root/'feeds/whisper-queue.json'
            value = json.loads(queue_path.read_text()); value['updated_at'] = repair.now().isoformat()
            pilot.atomic_json(queue_path, value)
            pilot.atomic_json(state/'health-all.json', {'open': True, 'consecutive_failures': 3})
            (state/'20261017T000000Z').mkdir()
            pilot.atomic_json(state/'20261017T000000Z/report.json', {'scope': 'all', 'status': 'failed', 'failure_kind': 'unrecognized_local_edits'})
            args = argparse.Namespace(state_dir=str(state), publish=True, scope='all', asr_python=sys.executable,
                                      transcriber=str(root/'scripts/generate_feed.py'), model_cache=str(root),
                                      audio_proxy=None, notifier_module=None, auto_repair=True)
            original = runner.command
            def command(arguments, cwd=runner.ROOT, timeout=120):
                return '[]' if arguments[0] == 'gh' else original(arguments, cwd, timeout)
            with patch.object(runner, 'command', side_effect=command), patch.object(queue, 'run', return_value=0), redirect_stdout(io.StringIO()):
                self.assertEqual(runner.run(args, root), 0)
            report = json.loads((state/'latest-all.json').read_text())
            self.assertEqual(report['status'], 'completed')
            self.assertFalse(report['circuit']['open'])
            self.assertEqual(git('status', '--porcelain'), '')
            self.assertEqual(git('rev-parse', 'HEAD'), git('rev-parse', 'origin/main'))
            self.assertTrue(list(state.glob('*/repairs/*whisper-queue.json')))

    def test_x_recovery_never_kicks_a_job_without_fresh_proxy_proof(self):
        import subprocess
        with tempfile.TemporaryDirectory() as directory:
            home = Path(directory); state = home/'state'; state.mkdir()
            stale = json.dumps({'generated_at': '2026-10-14T00:00:00+00:00'})
            result = subprocess.CompletedProcess([], 0, stdout=stale)
            at = datetime(2026, 10, 18, 12, tzinfo=timezone.utc)
            log = home/'Library/Logs/yibo-automation/x-feed.log'; log.parent.mkdir(parents=True)
            log.write_text('ConnectError; username=feed_bot')
            with patch.object(repair.subprocess, 'run', return_value=result) as call:
                self.assertEqual(repair.recover_x(state, home, at)['status'], 'proxy_not_ready')
                self.assertEqual(call.call_count, 1)

    def test_x_uses_existing_job_once_and_waits_for_real_publication(self):
        with tempfile.TemporaryDirectory() as directory:
            home = Path(directory); state = home/'state'; state.mkdir()
            guard = home/'.local/share/yibo-wawa-reconnect'; guard.mkdir(parents=True)
            at = datetime(2026, 10, 18, 12, tzinfo=timezone.utc)
            pilot.atomic_json(guard/'state.json', {'checked_at': at.timestamp(), 'ready': {'proxy': True},
                                                  'checks': {'google': True, 'telegram': True}})
            log = home/'Library/Logs/yibo-automation/x-feed.log'; log.parent.mkdir(parents=True)
            log.write_text('端口开着但出不去，先修代理\n')
            def result(args, **kwargs):
                value = json.dumps({'generated_at': '2026-10-17T00:00:00+00:00'}) if args[0] == 'gh' else 'state = waiting'
                return subprocess.CompletedProcess(args, 0, stdout=value)
            with patch.object(repair.subprocess, 'run', side_effect=result) as call:
                self.assertEqual(repair.recover_x(state, home, at)['status'], 'attempt_started')
                self.assertEqual(repair.recover_x(state, home, at)['status'], 'cooldown')
                kicks = [c.args[0] for c in call.call_args_list if 'kickstart' in c.args[0]]
                self.assertEqual(len(kicks), 1); self.assertNotIn('-k', kicks[0])
                log.write_text('Cookie authentication failed HTTP 403\n')
                self.assertEqual(repair.recover_x(state, home, at)['status'], 'unknown_x_failure')

    def test_hourly_proxy_proof_uses_live_x_probe_and_transport_retry(self):
        with tempfile.TemporaryDirectory() as directory:
            home=Path(directory); state=home/'state'; state.mkdir()
            guard=home/'.local/share/yibo-wawa-reconnect'; guard.mkdir(parents=True)
            at=datetime(2026,10,26,1,tzinfo=timezone.utc)
            pilot.atomic_json(guard/'state.json', {'checked_at': (at-timedelta(minutes=55)).timestamp(),
                'ready': {'proxy': True}, 'checks': {'google': True, 'telegram': True}})
            log=home/'Library/Logs/yibo-automation/x-feed.log'; log.parent.mkdir(parents=True)
            log.write_text('━━━ 抓 X socks5h://127.0.0.1:12345\nConnectError; cooling account for 60s')
            calls=[]
            def run(args, **kwargs):
                calls.append(args)
                body=json.dumps({'generated_at':'2026-10-24T20:20:06+00:00'}) if args[0]=='gh' else 'state = waiting'
                return subprocess.CompletedProcess(args, 0, stdout=body)
            with patch.object(repair.subprocess, 'run', side_effect=run):
                self.assertEqual(repair.recover_x(state,home,at)['status'],'attempt_started')
            self.assertEqual(len([a for a in calls if a[0]=='curl']),1)
            self.assertEqual(len([a for a in calls if 'kickstart' in a]),1)
            log.write_text('旧的 ConnectError;\n━━━ 抓 X\nCookie authentication failed HTTP 403')
            calls.clear()
            with patch.object(repair.subprocess, 'run', side_effect=run):
                result=repair.recover_x(state,home,at)
                self.assertEqual(result['status'],'unknown_x_failure')
                self.assertEqual(result['generated_at'],'2026-10-24T20:20:06+00:00')
            self.assertFalse(any(a[0]=='curl' or 'kickstart' in a for a in calls))

    def test_x_recovery_requires_a_publication_after_the_attempt(self):
        with tempfile.TemporaryDirectory() as d:
            state=Path(d); at=datetime(2026,10,26,2,tzinfo=timezone.utc)
            pilot.atomic_json(state/'x-recovery-result.json', {'status':'attempt_started',
                'at':at.isoformat(), 'previous_generated_at':'2026-10-24T20:20:06+00:00'})
            old_success=json.dumps({'generated_at':'2026-10-26T01:09:08+00:00'})
            with patch.object(repair.subprocess,'run',return_value=subprocess.CompletedProcess([],0,stdout=old_success)):
                self.assertEqual(repair.recover_x(state,at=at)['status'],'attempt_pending')
                self.assertEqual(repair.recover_x(state,at=at+timedelta(minutes=21))['status'],'recovery_not_published')
            new_success=json.dumps({'generated_at':(at+timedelta(seconds=10)).isoformat()})
            with patch.object(repair.subprocess,'run',return_value=subprocess.CompletedProcess([],0,stdout=new_success)):
                self.assertEqual(repair.recover_x(state,at=at+timedelta(minutes=22))['status'],'ok')
            self.assertEqual(json.loads((state/'x-recovery-result.json').read_text())['status'],'recovered')

    def test_x_classification_changes_do_not_repeat_notification(self):
        with tempfile.TemporaryDirectory() as d:
            state=Path(d)
            with patch.object(health, 'deliver', wraps=health.deliver) as delivery, patch.object(health.importlib.util, 'spec_from_file_location'):
                # Seed the existing incident: no sender import/network is allowed.
                pilot.atomic_json(state/'alert-x.json', {'key':'x_publication_outage', 'problem':True,
                    'delivery':'sent', 'at':'2026-10-26T00:00:00+00:00'})
                for kind in ('x_proxy_not_ready','x_unknown_x_failure','x_publication_check_unavailable'):
                    self.assertIsNone(health._notify_locked(state, {'scope':'x','publish':True,
                        'status':'failed','failure_kind':kind}, 'unused'))
                self.assertEqual(delivery.call_count,3)

    def test_accepted_daily_arxiv_refresh_prevents_false_weekend_outage(self):
        with tempfile.TemporaryDirectory() as directory:
            state = Path(directory); (state/'feeds').mkdir()
            at = datetime(2026, 10, 18, 13, tzinfo=timezone.utc)
            generated = (at-timedelta(minutes=5)).isoformat()
            pilot.atomic_json(state/'latest-all.json', {'status': 'completed', 'finished_at': generated,
                              'feeds': {'arxiv': {'generated_at': generated}}})
            pilot.atomic_json(state/'feeds/feed-arxiv.json', {'papers': [], 'generated_at': generated})
            pilot.atomic_json(state/'health-arxiv.json', {'last_success': '2026-10-15T01:30:00+00:00'})
            with patch.object(health, 'notify') as notify:
                outcomes = health.monitor(state, state, at=at)
            self.assertEqual(outcomes['arxiv'], 'ok')
            self.assertNotIn('arxiv', [call.args[1]['scope'] for call in notify.call_args_list])

    def test_runtime_integrity_failure_alerts_once_without_running_producer(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory); repo = root/'repo'; repo.mkdir()
            (repo/'source.py').write_text('changed')
            entry = root/'entry.py'; entry.write_text((Path(runner.__file__).parent/'macmini_runtime.py').read_text())
            (root/'signal_alerts.py').write_text((Path(runner.__file__).parent/'signal_alerts.py').read_text())
            (root/'manifest.json').write_text(json.dumps({'repository': str(repo), 'baselines': {'source.py': 'wrong'}}))
            sent = root/'sent.txt'; notifier = root/'notifier.py'
            notifier.write_text('from pathlib import Path\ndef send(text):\n p=Path(%r)\n p.write_text((p.read_text() if p.exists() else "")+"sent\\n")\n return {"status":"sent"}\n' % str(sent))
            for _ in range(2):
                result = subprocess.run([sys.executable, str(entry), '--state-dir', str(root/'state'),
                                         '--notifier-module', str(notifier)], capture_output=True, text=True)
                self.assertEqual(result.returncode, 1)
            self.assertEqual(sent.read_text(), 'sent\n')
            self.assertEqual(json.loads((root/'state/runtime-error.json').read_text())['delivery'], 'sent')

    def test_cache_purge_ack_is_not_success_and_is_rate_limited(self):
        for fresh_after_purge in [False, True]:
            with self.subTest(fresh_after_purge=fresh_after_purge), tempfile.TemporaryDirectory() as directory:
                root = Path(directory)/'repo'; root.mkdir(); state = Path(directory)/'state'; state.mkdir()
                at = repair.now().isoformat()
                pilot.atomic_json(root/'feed.json', {})
                (root/'feeds').mkdir()
                pilot.atomic_json(root/'feeds/feed-podcasts.json', {'generated_at': at})
                calls = []
                def request(args, **kwargs):
                    if args[0] == 'git': return subprocess.CompletedProcess(args, 0, stdout='')
                    calls.append(args[-1])
                    if 'purge.jsdelivr.net' in args[-1]: data = {'status': 'finished'}
                    else:
                        fresh = fresh_after_purge and any('purge.' in url for url in calls)
                        data = {'generated_at': at if fresh else '2026-10-14T00:00:00+00:00'}
                    return subprocess.CompletedProcess(args, 0, stdout=json.dumps(data))
                with patch.object(repair.subprocess, 'run', side_effect=request):
                    first = repair.sync_public_cache(root, state)
                    second = repair.sync_public_cache(root, state)
                self.assertEqual(first['podcasts'], 'verified' if fresh_after_purge else 'pending')
                self.assertEqual(second['podcasts'], first['podcasts'])
                self.assertEqual(sum('purge.' in url for url in calls), 1)
                if fresh_after_purge: self.assertEqual(len(calls), 3)

    def test_cache_detects_transcript_changes_without_new_capture_timestamp(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)/'repo'; root.mkdir(); state = Path(directory)/'state'; state.mkdir()
            (root/'feeds').mkdir()
            at = repair.now().isoformat()
            old = {'generated_at': at, 'transcript_available': False}
            new = {**old, 'transcript_available': True}
            pilot.atomic_json(root/'feeds/feed-podcasts.json', new)
            pilot.atomic_json(state/'public-cache.json', {'podcasts': {
                'expected': at, 'expected_content_sha256': repair.content_digest(old), 'status': 'verified'}})
            calls = []
            def request(args, **kwargs):
                if args[0] == 'git': return subprocess.CompletedProcess(args, 0, stdout='')
                calls.append(args[-1])
                data = {'status': 'finished'} if 'purge.' in args[-1] else (new if len(calls) > 1 else old)
                return subprocess.CompletedProcess(args, 0, stdout=json.dumps(data))
            with patch.object(repair.subprocess, 'run', side_effect=request):
                self.assertEqual(repair.sync_public_cache(root, state), {'podcasts': 'verified'})
                self.assertEqual(repair.sync_public_cache(root, state), {'podcasts': 'verified'})
            self.assertEqual(len(calls), 3)
            self.assertTrue(all('@main' not in url for url in calls))
            self.assertEqual(json.loads((state/'public-cache.json').read_text())['podcasts']['expected_content_sha256'],
                             repair.content_digest(new))

    def test_stale_alias_uses_verified_immutable_consumer_route(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)/'repo'; root.mkdir(); state = Path(directory)/'state'; state.mkdir()
            (root/'feeds').mkdir(); at = repair.now().isoformat(); revision = 'b'*40
            data = {'generated_at': at, 'podcasts': []}
            pilot.atomic_json(root/'feeds/feed-podcasts.json', data)
            calls = []
            def request(args, **kwargs):
                if args[0] == 'git': return subprocess.CompletedProcess(args, 0, stdout='')
                url = args[-1]; calls.append(url)
                if '/commits/main' in url: body = {'sha': revision}
                elif '@'+revision+'/' in url: body = data
                elif 'purge.' in url: body = {'status': 'finished'}
                else: body = {'generated_at': '2026-10-14T00:00:00+00:00'}
                return subprocess.CompletedProcess(args, 0, stdout=json.dumps(body))
            with patch.object(repair.subprocess, 'run', side_effect=request):
                self.assertEqual(repair.sync_public_cache(root, state), {'podcasts': 'verified'})
                self.assertEqual(repair.sync_public_cache(root, state), {'podcasts': 'verified'})
            record = json.loads((state/'public-cache.json').read_text())['podcasts']
            self.assertEqual(record['delivery'], 'commit_pinned')
            self.assertEqual(record['alias_status'], 'pending')
            self.assertEqual(record['pinned_commit'], revision)
            self.assertEqual(sum('purge.' in url for url in calls), 1)

    def test_checked_in_runtime_routes_workers_through_same_patched_entry(self):
        import macmini_runtime as runtime
        with patch.object(queue, 'RUNTIME_ENTRY', None), patch.object(queue, 'LOCAL_VALIDATION_REPAIR', False):
            loaded, recovery = runtime.load(Path(runner.__file__).parent)
            self.assertIs(loaded, runner)
            self.assertIs(recovery, repair)
            self.assertEqual(queue.RUNTIME_ENTRY.name, 'macmini_runtime.py')
            self.assertTrue(queue.LOCAL_VALIDATION_REPAIR)

    def test_unknown_x_failure_alerts_without_retrying_or_hiding_failure(self):
        import macmini_runtime as runtime
        with tempfile.TemporaryDirectory() as directory, \
             patch.object(sys, 'argv', ['entry', '--health-only', '--auto-repair', '--state-dir', directory,
                                        '--notifier-module', 'existing-notifier.py']), \
             patch.object(runtime, 'load', return_value=(runner, repair)), \
             patch.object(repair, 'recover_x', return_value={'status': 'unknown_x_failure'}), \
             patch.object(repair, 'sync_public_cache', return_value={}), \
             patch.object(runner, 'main', return_value=0), \
             patch.object(health, 'notify') as notify, redirect_stdout(io.StringIO()):
            self.assertEqual(runtime.main(), 0)
            report = notify.call_args.args[1]
            self.assertEqual(report['scope'], 'x')
            self.assertEqual(report['status'], 'failed')
            self.assertEqual(report['failure_kind'], 'x_unknown_x_failure')
            notify.reset_mock()
            with patch.object(repair, 'recover_x', return_value={'status': 'not_due'}):
                self.assertEqual(runtime.main(), 0)
            notify.assert_not_called()


if __name__ == '__main__':
    unittest.main()
