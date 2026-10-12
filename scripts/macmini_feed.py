"""Local feed runner. Preview by default; --publish requires a manual-only cloud workflow.

X remains with its existing 04:20 writer. No LLM/API-key integration.
Use a dedicated checkout and keep --state-dir outside the repository.
"""
import argparse
from datetime import datetime, timezone
import fcntl
from functools import partial
import json
from pathlib import Path
import subprocess
import sys
import time

import cloud_whisper_queue as queue
import cloud_whisper_pilot as pilot

ROOT = Path(__file__).resolve().parents[1]
PATHS = ['feeds/feed-podcasts.json', 'feeds/feed-arxiv.json', 'feeds/feed-blogs.json',
         'feeds/feed-transcripts-index.json', 'feeds/whisper-queue.json', 'feeds/transcripts']


def command(args, cwd=ROOT, timeout=120):
    result = subprocess.run(args, cwd=cwd, text=True, stdout=subprocess.PIPE,
                            stderr=subprocess.STDOUT, timeout=timeout)
    if result.returncode:
        # Do not print remote errors/URLs that might include source credentials.
        raise RuntimeError(f'{Path(str(args[0])).name} failed with exit {result.returncode}')
    return result.stdout.strip()


def cloud_is_manual(workflow):
    return not any(line.startswith(('  schedule:', '  push:')) for line in workflow.splitlines())


def preflight_publish(root):
    if command(['git', 'status', '--porcelain'], root):
        raise RuntimeError('Publication checkout has edits; preserve them for inspection')
    if command(['git', 'branch', '--show-current'], root) != 'main':
        raise RuntimeError('Publication requires dedicated main checkout')
    command(['git', 'pull', '--ff-only', 'origin', 'main'], root)
    if not cloud_is_manual((root/'.github/workflows/daily-digest.yml').read_text()):
        raise RuntimeError('Cloud automatic production is still enabled; local publication refused')
    runs = json.loads(command(['gh', 'run', 'list', '-R', 'Benboerba620/ai-signal',
                               '--workflow', 'daily-digest.yml', '--limit', '100',
                               '--json', 'status,databaseId'], root))
    if any(run['status'] != 'completed' for run in runs):
        raise RuntimeError('A cloud production run is still active/queued; local publication refused')


def checkpoint(root):
    command([sys.executable, str(root/'scripts/validate_feeds.py'), '--scope', 'all'], root)
    paths = PATHS[:-1]
    sidecars = ':(glob)feeds/transcripts/*.txt'
    if command(['git', 'ls-files', '--', sidecars], root) or list((root/'feeds/transcripts').glob('*.txt')):
        paths = [*paths, sidecars]
    command(['git', 'add', '-A', '--', *paths], root)
    staged = command(['git', 'diff', '--cached', '--name-only'], root).splitlines()
    if any(path not in PATHS[:-1] and not (path.startswith('feeds/transcripts/') and Path(path).suffix == '.txt') for path in staged):
        raise RuntimeError('Unexpected staged path; publication refused')
    if staged:
        command(['git', '-c', 'user.name=yibo-mini', '-c', 'user.email=gpttyb@gmail.com',
                 'commit', '-m', 'Feed update (Mac mini) '+datetime.now(timezone.utc).isoformat()], root)
    command(['git', 'pull', '--rebase', 'origin', 'main'], root)
    command(['git', 'push', 'origin', 'HEAD:main'], root)
    head = command(['git', 'rev-parse', 'HEAD'], root)
    command(['git', 'fetch', 'origin', 'main'], root)
    command(['git', 'merge-base', '--is-ancestor', head, 'origin/main'], root)
    return head


def run(args, root=ROOT):
    root = Path(root).resolve()
    state_dir = Path(args.state_dir).resolve()
    if root == state_dir or root in state_dir.parents:
        raise RuntimeError('State/logs must be outside the checkout')
    state_dir.mkdir(parents=True, exist_ok=True)
    with (state_dir/'run.lock').open('a') as lock:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        if args.publish:
            preflight_publish(root)
        started = time.monotonic()
        run_dir = state_dir/datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%SZ')
        run_dir.mkdir()
        engine = {'python': args.asr_python, 'transcriber': args.transcriber, 'model_cache': args.model_cache}
        if args.scope == 'all':
            for value in engine.values():
                if not Path(value).exists():
                    raise RuntimeError('Configured local inference runtime/cache is missing')
        if args.audio_proxy:
            engine['proxy'] = args.audio_proxy
        if args.scope == 'all':
            # Mac's existing 3.9 runtime installs libraries in user site. Resolve
            # it before the inference worker isolates HOME/credentials.
            engine['user_site'] = command([args.asr_python, '-c', 'import site; print(site.getusersitepackages())'])
        qargs = argparse.Namespace(status=False, enqueue_only=True, only_channel=[],
                                   checkpoint_git=False, timeout_seconds=1800, max_rss_mib=6144,
                                   output_dir=str(run_dir/'queue'))
        report = {'started_at': datetime.now(timezone.utc).isoformat(), 'scope': args.scope,
                  'publish': args.publish, 'commits': [], 'log_dir': str(run_dir)}
        code = 0
        try:
            if args.scope == 'all':
                queue.run(qargs, root)
                if args.publish:
                    report['commits'].append(checkpoint(root))
            with (run_dir/'generation.log').open('w') as log:
                subprocess.run([sys.executable, str(root/'scripts/generate_feed.py'),
                                '--arxiv-only' if args.scope == 'arxiv' else '--skip-twitter'],
                               cwd=root, stdout=log, stderr=subprocess.STDOUT, timeout=900, check=True)
            if args.scope == 'all':
                queue.run(qargs, root)
            command([sys.executable, str(root/'scripts/validate_feeds.py'), '--scope', args.scope], root)
            if args.publish:
                report['commits'].append(checkpoint(root))
            if args.scope == 'all':
                qargs.enqueue_only = False
                for attempt in range(2):
                    remaining = 65*60 - int(time.monotonic()-started)
                    if remaining < 300:
                        break
                    qargs.timeout_seconds = min(1800, remaining)
                    qargs.output_dir = str(run_dir/f'whisper-{attempt+1}')
                    code = max(code, queue.run(qargs, root, episode_runner=partial(queue.run_episode, local_engine=engine)))
                    if args.publish:
                        report['commits'].append(checkpoint(root))
            report.update(status='partial' if code else 'completed',
                          queue=queue.summary(queue.load_state(root), queue.utcnow()))
            report['feeds'] = {}
            for name in ('podcasts', 'arxiv', 'blogs'):
                feed = queue.load_json(root/f'feeds/feed-{name}.json')
                report['feeds'][name] = {'generated_at': feed.get('generated_at'),
                                        'upstream_failure_count': len(feed.get('errors') or [])}
            report['health'] = ('partial' if code or report['queue']['counts'].get('failed') or
                                any(feed['upstream_failure_count'] for feed in report['feeds'].values()) else 'ok')
        except Exception as exc:
            report.update(status='failed', error_type=type(exc).__name__)
            code = 1
        finally:
            report['finished_at'] = datetime.now(timezone.utc).isoformat()
            report['elapsed_seconds'] = round(time.monotonic()-started, 1)
            pilot.atomic_json(run_dir/'report.json', report)
            pilot.atomic_json(state_dir/'latest.json', report)
        print(json.dumps(report, ensure_ascii=False, indent=2))
        return code


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--publish', action='store_true')
    parser.add_argument('--scope', choices=['all', 'arxiv'], default='all')
    parser.add_argument('--state-dir', required=True)
    parser.add_argument('--asr-python', default='/usr/bin/python3')
    parser.add_argument('--transcriber', required=True)
    parser.add_argument('--model-cache', required=True)
    parser.add_argument('--audio-proxy', help='Existing credential-free loopback HTTP transport only')
    return run(parser.parse_args())


if __name__ == '__main__':
    raise SystemExit(main())
