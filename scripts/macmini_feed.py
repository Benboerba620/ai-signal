"""Local feed runner. Preview by default; --publish requires a manual-only cloud workflow.

X remains with its existing 04:20 writer. No LLM/API-key integration.
Use a dedicated checkout and keep --state-dir outside the repository.
"""
import argparse
from datetime import datetime, timezone, timedelta
import fcntl
from functools import partial
import json
import os
import signal
from pathlib import Path
import subprocess
import sys
import time

import cloud_whisper_queue as queue
import cloud_whisper_pilot as pilot
import macmini_health as health
from feed_resilience import OperationError, command_failure, process, redact

ROOT = Path(__file__).resolve().parents[1]
PATHS = ['feeds/feed-podcasts.json', 'feeds/feed-arxiv.json', 'feeds/feed-blogs.json',
         'feeds/feed-transcripts-index.json', 'feeds/whisper-queue.json', 'feeds/transcripts']


DEADLINE = None
ACTIONS = []
EVIDENCE = None
JOURNAL = None


def command(args, cwd=ROOT, timeout=120):
    network = args[0] in ('git', 'gh') and any(a in args for a in ('pull', 'push', 'fetch', 'list'))
    for attempt in range(3 if network else 1):
        remaining = DEADLINE - time.monotonic() if DEADLINE else timeout
        if remaining <= 0:
            raise OperationError('run_timeout')
        try:
            result = process(args, cwd=cwd, timeout=min(timeout, remaining))
        except subprocess.TimeoutExpired:
            error = OperationError('command_timeout', network)
        else:
            if not result.returncode:
                return result.stdout.strip()
            error = command_failure(result.stdout)
            if EVIDENCE:
                # Do not save raw credential-bearing Git/HTTP error output.
                with EVIDENCE.open('a') as evidence:
                    evidence.write(redact(result.stdout[-8000:]) + '\n')
        if not network or not error.retryable or attempt == 2:
            raise error
        ACTIONS.append({'operation': args[0], 'kind': error.kind, 'retry': attempt + 1})
        time.sleep(2 ** attempt)


def cloud_is_manual(workflow):
    return not any(line.startswith(('  schedule:', '  push:')) for line in workflow.splitlines())


def preflight_publish(root):
    if JOURNAL and JOURNAL.exists():
        recover_publication(root)
    if command(['git', 'status', '--porcelain'], root):
        raise OperationError('unrecognized_local_edits')
    if command(['git', 'branch', '--show-current'], root) != 'main':
        raise OperationError('publication_branch_changed')
    command(['git', 'pull', '--ff-only', 'origin', 'main'], root)
    if not cloud_is_manual((root/'.github/workflows/daily-digest.yml').read_text()):
        raise OperationError('cloud_writer_enabled')
    runs = json.loads(command(['gh', 'run', 'list', '-R', 'Benboerba620/ai-signal',
                               '--workflow', 'daily-digest.yml', '--limit', '100',
                               '--json', 'status,databaseId'], root))
    if any(run['status'] != 'completed' for run in runs):
        raise OperationError('cloud_writer_active')


def owned(path):
    return path in PATHS[:-1] or (path.startswith('feeds/transcripts/') and
                                 '/' not in path[len('feeds/transcripts/'):] and Path(path).suffix == '.txt')


def manifest(root):
    paths = command(['git', 'diff', 'HEAD', '--name-only'], root).splitlines()
    paths += command(['git', 'ls-files', '--others', '--exclude-standard'], root).splitlines()
    if any(not owned(path) for path in paths):
        raise OperationError('unrecognized_local_edits')
    return {path: pilot.digest(root/path) if (root/path).is_file() else None for path in sorted(set(paths))}


def recover_publication(root):
    pending = health.read(JOURNAL)
    if command(['git', 'branch', '--show-current'], root) != 'main':
        raise OperationError('publication_branch_changed')
    # Never clear a Git conflict, reset files, or stage unrecorded changes.
    head = command(['git', 'rev-parse', 'HEAD'], root)
    current = manifest(root)
    if head == pending['base'] and current == pending['files']:
        pass
    elif not current and head == pending.get('commit'):
        pass
    else:
        raise OperationError('publication_recovery_needs_review')
    if not cloud_is_manual((root/'.github/workflows/daily-digest.yml').read_text()):
        raise OperationError('cloud_writer_enabled')
    runs = json.loads(command(['gh', 'run', 'list', '-R', 'Benboerba620/ai-signal',
                              '--workflow', 'daily-digest.yml', '--limit', '100',
                              '--json', 'status,databaseId'], root))
    if any(run['status'] != 'completed' for run in runs):
        raise OperationError('cloud_writer_active')
    checkpoint(root, pending.get('scope', 'all'))
    ACTIONS.append({'operation': 'publication', 'kind': 'verified_journal_resumed'})


def checkpoint(root, scope='all'):
    command([sys.executable, str(root/'scripts/validate_feeds.py'), '--scope', scope], root)
    if JOURNAL:
        pending = {'base': command(['git', 'rev-parse', 'HEAD'], root), 'files': manifest(root), 'scope': scope}
        pilot.atomic_json(JOURNAL, pending)
    paths = ['feeds/feed-arxiv.json'] if scope == 'arxiv' else PATHS[:-1]
    sidecars = ':(glob)feeds/transcripts/*.txt'
    if scope != 'arxiv' and (command(['git', 'ls-files', '--', sidecars], root) or list((root/'feeds/transcripts').glob('*.txt'))):
        paths = [*paths, sidecars]
    command(['git', 'add', '-A', '--', *paths], root)
    staged = command(['git', 'diff', '--cached', '--name-only'], root).splitlines()
    if any(path not in PATHS[:-1] and not (path.startswith('feeds/transcripts/') and Path(path).suffix == '.txt') for path in staged):
        raise RuntimeError('Unexpected staged path; publication refused')
    if staged:
        command(['git', '-c', 'user.name=yibo-mini', '-c', 'user.email=gpttyb@gmail.com',
                 'commit', '-m', 'Feed update (Mac mini) '+datetime.now(timezone.utc).isoformat()], root)
    if JOURNAL:
        pending['commit'] = command(['git', 'rev-parse', 'HEAD'], root)
        pilot.atomic_json(JOURNAL, pending)
    command(['git', 'pull', '--rebase', 'origin', 'main'], root)
    if JOURNAL:
        pending['commit'] = command(['git', 'rev-parse', 'HEAD'], root)
        pilot.atomic_json(JOURNAL, pending)
    command(['git', 'push', 'origin', 'HEAD:main'], root)
    head = command(['git', 'rev-parse', 'HEAD'], root)
    command(['git', 'fetch', 'origin', 'main'], root)
    command(['git', 'merge-base', '--is-ancestor', head, 'origin/main'], root)
    if JOURNAL:
        JOURNAL.unlink()
    return head


def interrupted(signum, frame):
    raise OperationError('interrupted')


def run(args, root=ROOT):
    global DEADLINE, ACTIONS, EVIDENCE, JOURNAL
    root, state_dir = Path(root).resolve(), Path(args.state_dir).resolve()
    if root == state_dir or root in state_dir.parents:
        raise RuntimeError('State/logs must be outside the checkout')
    state_dir.mkdir(parents=True, exist_ok=True, mode=0o700)
    os.chmod(state_dir, 0o700)
    with (state_dir/'run.lock').open('a') as lock:
        try:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            print('{"status":"already_running"}')
            return 75  # Do not overwrite another process's report or alert.
        started = time.monotonic()
        DEADLINE, ACTIONS = started + 65*60, []
        run_dir = state_dir/health.now().strftime('%Y%m%dT%H%M%S%fZ')
        run_dir.mkdir(mode=0o700)
        EVIDENCE = run_dir/'diagnostic.log'
        JOURNAL = state_dir/'publication.json' if args.publish else None
        report = {'started_at': health.now().isoformat(), 'scope': args.scope,
                  'publish': args.publish, 'commits': [], 'log_dir': str(run_dir)}
        code = 0
        previous_signal = signal.signal(signal.SIGTERM, interrupted)
        try:
            if health.circuit(state_dir, args.scope).get('open'):
                report.update(status='circuit_open', failure_kind='circuit_open')
                return 75
            pilot.atomic_json(state_dir/'active.json', {'scope': args.scope, 'pid': os.getpid(),
                              'started_at': report['started_at'],
                              'deadline': (health.now()+timedelta(minutes=65)).isoformat()})
            if args.publish:
                preflight_publish(root)
            engine = {'python': args.asr_python, 'transcriber': args.transcriber, 'model_cache': args.model_cache}
            if args.scope == 'all':
                if not all(Path(value).exists() for value in engine.values()):
                    raise OperationError('missing_local_runtime')
                engine['user_site'] = command([args.asr_python, '-c', 'import site; print(site.getusersitepackages())'])
            if args.audio_proxy:
                engine['proxy'] = args.audio_proxy
            qargs = argparse.Namespace(status=False, enqueue_only=True, only_channel=[],
                                       checkpoint_git=False, timeout_seconds=1800, max_rss_mib=6144,
                                       output_dir=str(run_dir/'queue'))
            before = queue.load_state(root)
            if args.scope == 'all':
                queue.run(qargs, root)
                if args.publish:
                    report['commits'].append(checkpoint(root))
            with (run_dir/'generation.log').open('w') as log:
                os.chmod(log.name, 0o600)
                result = process([sys.executable, str(root/'scripts/generate_feed.py'),
                                  '--arxiv-only' if args.scope == 'arxiv' else '--skip-twitter'],
                                 cwd=root, output=log, timeout=min(900, DEADLINE-time.monotonic()),
                                 env={**os.environ, 'AI_SIGNAL_HTTP_STATE': str(state_dir/'http-deferred.json')})
                if result.returncode:
                    raise OperationError('generation_failed')
            if args.scope == 'all':
                queue.run(qargs, root)
            command([sys.executable, str(root/'scripts/validate_feeds.py'), '--scope', args.scope], root)
            report['feeds'] = health.accept(root, args.scope, report['started_at'])
            if args.publish:
                report['commits'].append(checkpoint(root, args.scope))
            if args.scope == 'all':
                qargs.enqueue_only = False
                for attempt in range(2):
                    remaining = DEADLINE-time.monotonic()-180
                    if remaining < 300:
                        break
                    qargs.timeout_seconds = min(1800, remaining)
                    qargs.output_dir = str(run_dir/f'whisper-{attempt+1}')
                    queue.run(qargs, root, episode_runner=partial(queue.run_episode, local_engine=engine))
                    if args.publish:
                        report['commits'].append(checkpoint(root))
            command([sys.executable, str(root/'scripts/validate_feeds.py'), '--scope', args.scope], root)
            report['feeds'] = health.accept(root, args.scope, report['started_at'])
            after = queue.load_state(root)
            active_ids = {queue.identity(item) for item in queue.load_json(root/'feeds/feed-podcasts.json').get('podcasts', [])}
            current = [v for k, v in after['entries'].items() if v.get('status') in ('failed', 'retry')
                       and k in active_ids] if args.scope == 'all' else []
            report.update(status='completed', pipeline_health='ok',
                          queue=queue.summary(after, queue.utcnow()),
                          current_failure_kinds=sorted({v.get('failure_kind', 'unknown') for v in current}),
                          historical_failure_count=sum(v.get('status') == 'failed' and k not in active_ids
                                                       for k, v in after['entries'].items()))
            report['source_health'] = ('degraded' if current or any(
                feed['upstream_failure_count'] for feed in report['feeds'].values()) else 'ok')
            report['health'] = report['source_health']
        except (Exception, KeyboardInterrupt) as exc:
            kind = getattr(exc, 'kind', 'process_timeout' if isinstance(exc, subprocess.TimeoutExpired) else 'unknown_failure')
            report.update(status='failed', pipeline_health='failed', failure_kind=kind, error_type=type(exc).__name__)
            # Exceptions/logs are diagnostic data. Unknown errors are not rerun.
            pilot.atomic_json(run_dir/'diagnostic.json', {'kind': kind, 'error_type': type(exc).__name__,
                              'manual_action': 'Inspect local evidence; test a candidate patch in an isolated checkout.'})
            code = 1
        finally:
            report.update(finished_at=health.now().isoformat(), elapsed_seconds=round(time.monotonic()-started, 1),
                          recovery_actions=ACTIONS)
            try:
                health.record(state_dir, report)
                pilot.atomic_json(run_dir/'report.json', report)
                pilot.atomic_json(state_dir/'latest.json', report)
                pilot.atomic_json(state_dir/f'latest-{args.scope}.json', report)
                health.notify(state_dir, report, getattr(args, 'notifier_module', None))
            finally:
                (state_dir/'active.json').unlink(missing_ok=True)
                DEADLINE, EVIDENCE, JOURNAL = None, None, None
                signal.signal(signal.SIGTERM, previous_signal)
        print(json.dumps(report, ensure_ascii=False, indent=2))
        return code


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--publish', action='store_true')
    parser.add_argument('--scope', choices=['all', 'arxiv'], default='all')
    parser.add_argument('--state-dir', required=True)
    parser.add_argument('--asr-python', default='/usr/bin/python3')
    parser.add_argument('--transcriber')
    parser.add_argument('--model-cache')
    parser.add_argument('--audio-proxy', help='Existing credential-free loopback HTTP transport only')
    parser.add_argument('--notifier-module', help='Existing local telegram_direct.py; keeps its private default recipient')
    parser.add_argument('--health-only', action='store_true', help='Observe schedules; never restart the producer')
    parser.add_argument('--reset-circuit', action='store_true', help='Explicit operator reset after inspecting the cause')
    args = parser.parse_args()
    state_dir = Path(args.state_dir).resolve()
    if args.health_only:
        print(json.dumps(health.monitor(state_dir, ROOT, args.notifier_module), indent=2))
        return 0
    if args.reset_circuit:
        with (state_dir/'run.lock').open('a') as lock:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
            state = health.circuit(state_dir, args.scope)
            state.update(open=False, consecutive_failures=0, reset_at=health.now().isoformat())
            pilot.atomic_json(state_dir/f'health-{args.scope}.json', state)
        return 0
    if args.scope == 'all' and not (args.transcriber and args.model_cache):
        parser.error('all scope requires existing --transcriber and --model-cache')
    return run(args)


if __name__ == '__main__':
    raise SystemExit(main())
