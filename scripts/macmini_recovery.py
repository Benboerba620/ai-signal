"""Allowlisted local repairs. Never infer commands from logs or reset a queue."""
from datetime import datetime, timedelta, timezone
from feed_policy import arxiv_enabled
import hashlib
import fcntl
import json
import os
from pathlib import Path
import tempfile
import subprocess
import time

import cloud_whisper_pilot as pilot
from feed_resilience import OperationError


def now():
    return datetime.now(timezone.utc)


def content_digest(data):
    return hashlib.sha256(json.dumps(data, sort_keys=True, ensure_ascii=False,
                                     separators=(',', ':')).encode()).hexdigest()


def stamp(value):
    date = datetime.fromisoformat(str(value).replace('Z', '+00:00'))
    if date.tzinfo is None:
        raise ValueError('Timezone required')
    return date


def atomic_bytes(path, content):
    path = Path(path)
    fd, temporary = tempfile.mkstemp(prefix=path.name + '.', dir=path.parent)
    try:
        with os.fdopen(fd, 'wb') as file:
            file.write(content)
            file.flush()
            os.fsync(file.fileno())
        os.replace(temporary, path)
    finally:
        Path(temporary).unlink(missing_ok=True)


def backup(path, directory):
    """Keep exact original bytes outside the checkout before any repair."""
    path, directory = Path(path), Path(directory)
    directory.mkdir(parents=True, exist_ok=True, mode=0o700)
    content = path.read_bytes()
    destination = directory / (hashlib.sha256(content).hexdigest()[:16] + '-' + path.name)
    if not destination.exists():
        atomic_bytes(destination, content)
    return str(destination)


def timestamp_only(root, command):
    """Recognize exactly one unstaged queue timestamp edit; refuse everything else."""
    if command(['git', 'status', '--porcelain'], root) != 'M feeds/whisper-queue.json':
        # command() strips the first status column's leading space.
        return None
    path = root/'feeds/whisper-queue.json'
    if path.is_symlink():
        return None
    original = command(['git', 'show', 'HEAD:feeds/whisper-queue.json'], root)
    before, after = json.loads(original), json.loads(path.read_text())
    if before.get('schema_version') != 1 or not isinstance(before.get('entries'), dict):
        return None
    old, new = before.pop('updated_at', None), after.pop('updated_at', None)
    if before != after or not old or not new or stamp(new) < stamp(old):
        return None
    content = original.encode() + b'\n'
    blob = hashlib.sha1(b'blob ' + str(len(content)).encode() + b'\0' + content).hexdigest()
    if blob != command(['git', 'rev-parse', 'HEAD:feeds/whisper-queue.json'], root):
        return None
    return content


def repair_timestamp(root, directory, command, actions):
    content = timestamp_only(root, command)
    if content is None:
        raise OperationError('unrecognized_local_edits')
    path = root/'feeds/whisper-queue.json'
    saved = backup(path, directory)
    atomic_bytes(path, content)
    actions.append({'operation': 'queue', 'kind': 'timestamp_only_edit_restored', 'backup': saved})


def prune_retention(root, directory, actions, at=None):
    """Apply the existing 14-day policy before the pre-generation checkpoint.

    Current feed timestamps are never touched. Active sidecars keep their
    existing bytes; verified active entries may renew the existing 14-day
    window. Inactive expired sidecars are backed up first. Unknown data stops.
    """
    at = at or now()
    index_path = root/'feeds/feed-transcripts-index.json'
    if not index_path.exists():
        return False
    if index_path.is_symlink() or root.resolve() not in (root/'feeds/transcripts').resolve().parents:
        raise OperationError('invalid_artifacts')
    index = json.loads(index_path.read_text())
    feed = json.loads((root/'feeds/feed-podcasts.json').read_text())
    if index.get('retention_days') != 14 or not isinstance(index.get('transcripts'), list):
        raise OperationError('invalid_artifacts')
    active = {e.get('transcript_path'): e for e in feed['podcasts'] if e.get('transcript_available')}
    kept, expired, renewed = [], [], 0
    for record in index['transcripts']:
        relative = record.get('transcript_path', '')
        path = root/relative
        if (not relative.startswith('feeds/transcripts/') or
                '/' in relative[len('feeds/transcripts/'):] or path.suffix != '.txt' or
                path.is_symlink() or path.resolve().parent != (root/'feeds/transcripts').resolve()):
            raise OperationError('invalid_artifacts')
        expiry = stamp(record.get('expires_at'))
        if expiry <= at and relative not in active:
            expired.append(record)
        else:
            if expiry <= at:
                if not path.is_file():
                    raise OperationError('invalid_artifacts')
                text = path.read_text('utf-8')
                if not text.strip() or '\x00' in text or '\ufffd' in text:
                    raise OperationError('invalid_artifacts')
                for value in (record, active[relative]):
                    if ((value.get('transcript_chars') and len(text) != value['transcript_chars']) or
                            (value.get('transcript_sha256') and pilot.digest(path) != value['transcript_sha256'])):
                        raise OperationError('invalid_artifacts')
                record.update(last_seen_at=at.isoformat(), expires_at=(at+timedelta(days=14)).isoformat())
                renewed += 1
            kept.append(record)
    if not expired and not renewed:
        return False
    # Save every affected original before changing the index or sidecars.
    backup(index_path, directory)
    retained_paths = {r['transcript_path'] for r in kept} | set(active)
    deletions = {r['transcript_path'] for r in expired} - retained_paths
    for relative in deletions:
        if (root/relative).is_file():
            backup(root/relative, directory)
    index.update(transcripts=kept, generated_at=at.isoformat())
    pilot.atomic_json(index_path, index)
    for relative in deletions:
        (root/relative).unlink(missing_ok=True)
    actions.append({'operation': 'retention', 'kind': 'expired_inactive_transcripts_pruned',
                    'count': len(expired), 'active_renewed': renewed, 'backup': str(directory)})
    return True


def last_failure(state_dir, scope):
    # A circuit_open report is not the cause; inspect the last actual failure.
    reports = sorted(Path(state_dir).glob('*/report.json'), reverse=True)[:90]
    for path in reports:
        report = json.loads(path.read_text())
        if report.get('scope') == scope and report.get('status') == 'completed':
            return None
        if report.get('scope') == scope and report.get('status') == 'failed':
            return report.get('failure_kind')
    return None


def allowed(root, state_dir, scope, command):
    kind = last_failure(state_dir, scope)
    if kind is None:
        return not command(['git', 'status', '--porcelain'], root)
    if kind == 'unrecognized_local_edits':
        return timestamp_only(root, command) is not None
    return kind in {'temporary_network', 'command_timeout', 'process_timeout',
                    'generation_failed', 'stale_artifacts', 'expired_retention'}


def available(state_dir, scope, at=None):
    at = at or now()
    path = Path(state_dir)/f'recovery-{scope}.json'
    if path.exists():
        previous = json.loads(path.read_text())
        if at - stamp(previous['reserved_at']) < timedelta(hours=24):
            return False
    return True


def reserve(state_dir, scope, at=None):
    """Caller holds run.lock. Reserve before work, including crash/no-ack cases."""
    at = at or now()
    path = Path(state_dir)/f'recovery-{scope}.json'
    if not available(state_dir, scope, at):
        return False
    pilot.atomic_json(path, {'reserved_at': at.isoformat(), 'scope': scope})
    return True


def recover_x(state_dir, home=None, at=None):
    """Reuse the existing X job only after a proven proxy failure has cleared.

    Never restart the VPN, read cookies, run a second X writer, or retry a
    failed login. A kickstart is an attempt, not evidence of fresh publication.
    """
    from zoneinfo import ZoneInfo
    home, at = Path(home or Path.home()), at or now()
    with (Path(state_dir)/'x-recovery.lock').open('a') as lock:
        try:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            return {'status': 'already_running'}
        local = at.astimezone(ZoneInfo('Asia/Shanghai'))
        expected = local.replace(hour=4, minute=20, second=0, microsecond=0)
        if local < expected + timedelta(minutes=30):
            return {'status': 'not_due'}
        result = subprocess.run(['gh', 'api', 'repos/Benboerba620/ai-signal/contents/feeds/feed-x.json',
                                 '-H', 'Accept:application/vnd.github.raw+json'],
                                capture_output=True, text=True, timeout=20)
        if result.returncode:
            return {'status': 'publication_check_unavailable'}
        published = json.loads(result.stdout)
        generated_at = stamp(published['generated_at'])
        if expected <= generated_at <= at+timedelta(minutes=5):
            receipt = Path(state_dir)/'x-recovery-result.json'
            if receipt.exists() and json.loads(receipt.read_text()).get('status') == 'attempt_started':
                pilot.atomic_json(receipt, {'status': 'recovered', 'verified_at': at.isoformat(),
                                             'generated_at': generated_at.isoformat()})
            return {'status': 'ok', 'generated_at': generated_at.isoformat(),
                    'content_sha256': content_digest(published)}
        if generated_at > at+timedelta(minutes=5):
            return {'status': 'invalid_feed_timestamp'}
        guard = home/'.local/share/yibo-wawa-reconnect'
        if (guard/'PAUSED').exists() or not (guard/'state.json').is_file():
            return {'status': 'proxy_not_ready'}
        state = json.loads((guard/'state.json').read_text())
        checked = datetime.fromtimestamp(state.get('checked_at', 0), timezone.utc)
        if not (timedelta(0) <= at-checked < timedelta(minutes=3) and
                state.get('ready', {}).get('proxy') and
                state.get('checks', {}).get('google') and state.get('checks', {}).get('telegram')):
            return {'status': 'proxy_not_ready'}
        log = home/'Library/Logs/yibo-automation/x-feed.log'
        if not log.is_file():
            return {'status': 'unknown_x_failure'}
        tail = '\n'.join(log.read_text(errors='replace').splitlines()[-12:])
        if not any(message in tail for message in ('端口开着但出不去，先修代理', '本机没有可用的 SOCKS5 出口')):
            return {'status': 'unknown_x_failure'}
        label = f'gui/{os.getuid()}/com.yibo.x-feed'
        current = subprocess.run(['launchctl', 'print', label], capture_output=True, text=True, timeout=10)
        if current.returncode:
            return {'status': 'job_unavailable'}
        if 'state = running' in current.stdout:
            return {'status': 'job_running'}
        if not reserve(state_dir, 'x', at):
            return {'status': 'cooldown'}
        result = subprocess.run(['launchctl', 'kickstart', label], capture_output=True, text=True, timeout=10)
        outcome = {'status': 'attempt_started' if result.returncode == 0 else 'start_failed',
                   'at': at.isoformat(), 'previous_generated_at': generated_at.isoformat()}
        pilot.atomic_json(Path(state_dir)/'x-recovery-result.json', outcome)
        return outcome


def sync_public_cache(root, state_dir, *, proxy=None, x_generated_at=None, x_content_sha256=None):
    """Verify/purge only our public feed aliases; no requests after verification.

    Fixed 45s total budget, 8s per request, at most one purge per changed feed
    per six hours. Purge acknowledgement never counts as fresh data.
    """
    root, state_dir = Path(root), Path(state_dir)
    if (state_dir/'active.json').exists():
        return {'status': 'producer_running'}
    status = subprocess.run(['git', 'status', '--porcelain'], cwd=root, capture_output=True,
                            text=True, timeout=10)
    if status.returncode or status.stdout.strip():
        return {'status': 'unpublished_local_changes'}
    with (state_dir/'cache.lock').open('a') as lock:
        try:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            return {'status': 'already_running'}
        path = state_dir/'public-cache.json'
        state = json.loads(path.read_text()) if path.exists() else {}
        deadline = time.monotonic()+45
        outcomes = {}
        revision = None
        def get(url):
            remaining = deadline-time.monotonic()
            if remaining <= 0:
                raise TimeoutError('Cache budget exhausted')
            seconds = min(8, remaining)
            args = ['curl', '--silent', '--show-error', '--fail', '--max-time', str(seconds)]
            if proxy:
                args.extend(['--proxy', proxy])
            response = subprocess.run([*args, url], capture_output=True, text=True, timeout=seconds+1)
            if response.returncode:
                raise OSError('Public cache transport failed')
            return json.loads(response.stdout)
        for name in ('podcasts', 'arxiv', 'blogs', 'transcripts-index', 'x'):
            if name == 'arxiv' and not arxiv_enabled(root):
                continue
            source = root/f'feeds/feed-{name}.json'
            if name == 'x' and not x_generated_at:
                continue
            if not source.exists():
                continue
            local = json.loads(source.read_text())
            expected = x_generated_at if name == 'x' else local['generated_at']
            expected_digest = x_content_sha256 if name == 'x' else content_digest(local)
            if name == 'x' and expected_digest is None and stamp(local['generated_at']) == stamp(expected):
                expected_digest = content_digest(local)
            previous = state.get(name, {})
            same_version = (previous.get('expected') == expected and
                            previous.get('expected_content_sha256') == expected_digest)
            if same_version and previous.get('status') == 'verified':
                outcomes[name] = 'verified'
                continue
            record = previous if same_version else {'expected': expected, 'expected_content_sha256': expected_digest}
            relative = f'/gh/Benboerba620/ai-signal/feeds/feed-{name}.json'
            def fresh(data):
                value = stamp(data.get('generated_at'))
                return (stamp(expected) <= value <= now()+timedelta(minutes=5) and
                        (expected_digest is None or content_digest(data) == expected_digest))
            try:
                try:
                    if not fresh(get('https://cdn.jsdelivr.net'+relative)):
                        last = record.get('purge_at')
                        if not last or now()-stamp(last) >= timedelta(hours=6):
                            record.update(status='pending', purge_at=now().isoformat())
                            state[name] = record
                            pilot.atomic_json(path, state)
                            get('https://purge.jsdelivr.net'+relative)
                        else:
                            raise ValueError('Mirror pending; purge cooldown active')
                        if not fresh(get('https://cdn.jsdelivr.net'+relative)):
                            raise ValueError('Mirror remains stale')
                    record.update(delivery='branch_alias', alias_status='verified')
                except Exception:
                    # Same route as the consumer's origin-failure fallback.
                    # A purge acknowledgement alone never reaches this state.
                    if revision is None:
                        revision = get('https://api.github.com/repos/Benboerba620/ai-signal/commits/main').get('sha')
                    if not isinstance(revision, str) or len(revision) != 40 or any(c not in '0123456789abcdef' for c in revision):
                        raise ValueError('Invalid public revision')
                    pinned = f'/gh/Benboerba620/ai-signal@{revision}/feeds/feed-{name}.json'
                    if not fresh(get('https://cdn.jsdelivr.net'+pinned)):
                        raise ValueError('Pinned mirror does not match published content')
                    record.update(delivery='commit_pinned', alias_status='pending', pinned_commit=revision)
                record.update(status='verified', verified_at=now().isoformat())
                record.pop('error_type', None)
            except Exception as exc:
                record.update(status='pending', error_type=type(exc).__name__)
            state[name] = record
            outcomes[name] = record['status']
            pilot.atomic_json(path, state)
        return outcomes
