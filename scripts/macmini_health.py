"""Local health acceptance, latched circuit breaker and existing-channel alerts."""
from datetime import datetime, timedelta, timezone
from feed_policy import arxiv_enabled
import hashlib
import fcntl
import importlib.util
import json
from pathlib import Path

import cloud_whisper_pilot as pilot
from feed_resilience import OperationError
from signal_alerts import deliver
from transcript_health import assess as assess_transcripts


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
        if name == 'arxiv' and not arxiv_enabled(root):
            continue
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
    """Serialize delivery and retain bounded follow-ups and durable receipts."""
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
    # One incident per source scope; observations may change while it is open.
    key = f'{scope}_publication_outage' if problem else 'recovered'
    state = report.get('circuit', {})
    labels = {'all':'播客和博客', 'arxiv':'论文', 'x':'X', 'cache':'公开下载'}
    text = (f"AI Signal / {labels.get(scope, scope)}：" + (('转录需要处理' if report.get('status') == 'completed' and report.get('current_failure_kinds') and all(k.startswith('transcript_') for k in report['current_failure_kinds']) else '抓取或数据异常') if problem else '已验收恢复') + '\n'
            f"最近成功：{state.get('last_success') or report.get('feeds', {}).get('x', {}).get('generated_at') or '本次未能核实'}\n"
            f"问题分类：{report.get('failure_kind') or ', '.join(report.get('current_failure_kinds', [])) or report.get('source_health', '无')}\n")
    for name, feed in report.get('feeds', {}).items():
        text += f"{name} 数据时间：{feed.get('generated_at', '未知')}\n"
    text += (('X 抓取未更新。连接失败不代表登录失效。' if scope == 'x' else
              '请检查上述异常。转录正常排队保持安静，排队超过 24 小时才提醒。')
             if problem else '当前检查通过，无需操作。')
    def send(text):
        spec = importlib.util.spec_from_file_location('existing_telegram_direct', module_path)
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        return module.send(text)
    return deliver(state_dir, scope, key, problem, text, send, at=now())


def monitor(state_dir, root, module_path=None, at=None):
    """Read-only to the producer: alert on a missed schedule, never spawn/restart it."""
    from zoneinfo import ZoneInfo
    at = at or now()
    local = at.astimezone(ZoneInfo('Asia/Shanghai'))
    active = read(state_dir/'active.json', {})
    outcomes = {}
    for scope, hour, minute in (('all', 4, 13), ('arxiv', 9, 30)):
        if scope == 'arxiv' and not arxiv_enabled(root):
            continue
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
        if scope == 'arxiv':
            daily = read(state_dir/'latest-all.json', {})
            refreshed = daily.get('feeds', {}).get('arxiv', {}).get('generated_at')
            if daily.get('status') == 'completed' and refreshed and date(refreshed) >= expected:
                # A verified all-scope run also refreshed arXiv. Do not report
                # a false outage or start another fetch based on an older
                # arXiv-only report.
                success = daily['finished_at']
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
        if healthy:
            current = dict(report, scope=scope, publish=True, status='completed')
            if scope == 'all':
                try:
                    issues, coverage = assess_transcripts(root, at)
                except Exception:
                    issues, coverage = ['transcript_queue_invalid'], {}
                current.update(current_failure_kinds=issues, coverage=coverage, failure_kind=None,
                    source_health='degraded' if issues or any(v.get('upstream_failure_count') for v in current.get('feeds', {}).values()) else 'ok')
            if scope == 'arxiv' and success != state.get('last_success'):
                current.update(source_health='ok', failure_kind=None)
            if (state_dir/f'alert-{scope}.json').exists() or current.get('source_health') == 'degraded':
                notify(state_dir, current, module_path)
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
