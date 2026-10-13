"""Local health acceptance, latched circuit breaker and existing-channel alerts."""
from datetime import datetime, timedelta, timezone
import hashlib
import fcntl
import importlib.util
import json
from pathlib import Path

import cloud_whisper_pilot as pilot
from feed_resilience import OperationError


def now():
    return datetime.now(timezone.utc)


def read(path, default=None):
    return json.loads(Path(path).read_text()) if Path(path).exists() else default


def date(value):
    result = datetime.fromisoformat(str(value).replace('Z', '+00:00'))
    if result.tzinfo is None:
        raise ValueError('Timezone missing')
    return result


def accept(root, scope, started_at):
    """A zero exit code alone is insufficient. Validate timestamp and sidecar bytes."""
    feeds = {}
    stale = False
    for name, key in (('podcasts', 'podcasts'), ('arxiv', 'papers'), ('blogs', 'articles')):
        if scope == 'arxiv' and name != 'arxiv':
            continue
        feed = read(root/f'feeds/feed-{name}.json')
        if not isinstance(feed, dict) or not isinstance(feed.get(key), list):
            raise OperationError('invalid_artifacts')
        try:
            stamp = date(feed.get('generated_at'))
        except (ValueError, TypeError):
            raise OperationError('invalid_artifacts') from None
        if stamp > now() + timedelta(minutes=5):
            raise OperationError('invalid_artifacts')
        stale = stale or stamp < date(started_at) - timedelta(seconds=2)
        feeds[name] = {'generated_at': stamp.isoformat(), 'items': len(feed[key]),
                       'upstream_failure_count': len(feed.get('errors') or [])}
    if scope == 'all':
        index = read(root/'feeds/feed-transcripts-index.json')
        if not isinstance(index, dict) or not isinstance(index.get('transcripts'), list):
            raise OperationError('invalid_artifacts')
        for record in index['transcripts']:
            relative = str(record.get('transcript_path') or '')
            path = (root/relative).resolve()
            base = (root/'feeds/transcripts').resolve()
            if not relative or base not in path.parents or not path.is_file():
                raise OperationError('invalid_artifacts')
            text = path.read_text('utf-8')
            if not text.strip() or '\x00' in text or '\ufffd' in text:
                raise OperationError('invalid_artifacts')
            if record.get('transcript_sha256') and pilot.digest(path) != record['transcript_sha256']:
                raise OperationError('invalid_artifacts')
            # Older imports contain character counts but no digest.
            if record.get('transcript_chars') and len(text) != record['transcript_chars']:
                raise OperationError('invalid_artifacts')
        state = read(root/'feeds/whisper-queue.json')
        if not isinstance(state, dict) or not isinstance(state.get('entries'), dict):
            raise OperationError('invalid_artifacts')
        for entry in state['entries'].values():
            relative = entry.get('transcript_path')
            digest = entry.get('transcript_sha256')
            if entry.get('status') == 'completed' and relative and digest:
                path = (root/relative).resolve()
                # Expired tombstones legitimately outlive their pruned sidecars.
                if path.is_file() and ((root/'feeds/transcripts').resolve() not in path.parents or pilot.digest(path) != digest):
                    raise OperationError('invalid_artifacts')
    if stale:
        raise OperationError('stale_artifacts')
    return feeds


def circuit(state_dir, scope):
    return read(state_dir/f'health-{scope}.json', {'consecutive_failures': 0, 'open': False})


def record(state_dir, report):
    state = circuit(state_dir, report['scope'])
    if report['status'] == 'completed':
        state.update(consecutive_failures=0, open=False, last_success=report['finished_at'])
    elif report['status'] != 'circuit_open':
        state['consecutive_failures'] += 1
        state['open'] = state['consecutive_failures'] >= 3
    state.update(last_run=report['finished_at'], last_status=report['status'],
                 failure_kind=report.get('failure_kind'), source_health=report.get('source_health'))
    pilot.atomic_json(state_dir/f"health-{report['scope']}.json", state)
    report['circuit'] = state


def notify(state_dir, report, module_path=None):
    """Reserve dedup key before sending; uncertain delivery is never resent."""
    if not module_path or not report.get('publish'):
        return
    with (state_dir/'alert.lock').open('a') as lock:
        try:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            return
        _notify_locked(state_dir, report, module_path)


def _notify_locked(state_dir, report, module_path):
    scope = report['scope']
    problem = report['status'] != 'completed' or report.get('source_health') == 'degraded'
    previous = read(state_dir/f'alert-{scope}.json', {})
    signature = json.dumps([report.get('failure_kind'), report.get('source_health'),
                            report.get('current_failure_kinds', []),
                            {k: v['upstream_failure_count'] for k, v in report.get('feeds', {}).items()},
                            bool(report.get('circuit', {}).get('open'))], sort_keys=True)
    key = hashlib.sha256(signature.encode()).hexdigest() if problem else 'recovered'
    if previous.get('key') == key or (not problem and not previous):
        return
    receipt = {'key': key, 'at': now().isoformat(), 'delivery': 'reserved'}
    destination = state_dir/f'alert-{scope}.json'
    pilot.atomic_json(destination, receipt)
    state = report.get('circuit', {})
    text = (f"AI Signal / {scope}: " + ('需要检查' if problem else '已验收恢复') + '\n'
            f"主链路: {report['status']}; 来源: {report.get('source_health', 'unknown')}\n"
            f"分类: {report.get('failure_kind') or ', '.join(report.get('current_failure_kinds', [])) or 'none'}\n"
            f"最近验收成功: {state.get('last_success', '尚无')}\n"
            f"已执行: {len(report.get('recovery_actions', []))} 次有界恢复动作；未重置历史队列。\n"
            f"人工步骤: {'检查本地诊断；修复后显式解除熔断' if state.get('open') else ('检查本地报告中的失败分类' if problem else '无需操作')}\n"
            '证据: runs/latest-' + scope + '.json（详细日志仅保留本机）')
    try:
        # Explicit operator-provided local module; never derived from remote output.
        spec = importlib.util.spec_from_file_location('existing_telegram_direct', module_path)
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        result = module.send(text)  # Keep the existing module's default private destination.
        receipt['delivery'] = result.get('status', 'delivery_unknown')
    except Exception as exc:
        receipt.update(delivery='local_error', error_type=type(exc).__name__)
    pilot.atomic_json(destination, receipt)


def monitor(state_dir, root, module_path=None, at=None):
    """Read-only to the producer: alert on a missed schedule, never spawn/restart it."""
    from zoneinfo import ZoneInfo
    at = at or now()
    local = at.astimezone(ZoneInfo('Asia/Shanghai'))
    active = read(state_dir/'active.json', {})
    outcomes = {}
    for scope, hour, minute in (('all', 4, 13), ('arxiv', 9, 30)):
        expected = local.replace(hour=hour, minute=minute, second=0, microsecond=0)
        # Permit 80 min for daily processing, 25 min for arXiv, including publication.
        grace = timedelta(minutes=80 if scope == 'all' else 25)
        if local < expected + grace:
            expected -= timedelta(days=1)
        if scope == 'arxiv':
            while expected.weekday() >= 5:
                expected -= timedelta(days=1)
        report = read(state_dir/f'latest-{scope}.json', {})
        state = circuit(state_dir, scope)
        success = state.get('last_success')
        healthy = bool(success and date(success) >= expected)
        if active.get('scope') == scope and date(active['deadline']) > at:
            outcomes[scope] = 'running'
            continue
        outcomes[scope] = 'ok' if healthy else 'missed_or_failed_schedule'
        if healthy:
            try:
                accept(root, scope, expected.isoformat())
            except Exception:
                healthy = False
                outcomes[scope] = 'invalid_or_stale_artifacts'
        if not healthy:
            if (report.get('status') in ('failed', 'circuit_open') and
                    date(report['finished_at']) >= expected):
                notify(state_dir, report, module_path)
                continue  # Same incident as the producer, same dedup key.
            diagnostic = {'scope': scope, 'publish': True, 'status': 'failed',
                          'failure_kind': outcomes[scope], 'circuit': state,
                          'source_health': report.get('source_health', 'unknown')}
            notify(state_dir, diagnostic, module_path)
    return outcomes
