"""Fixed, bounded recovery primitives. Remote output is data, never a command."""
from datetime import datetime, timedelta, timezone
from email.utils import parsedate_to_datetime
import os
import hashlib
import json
from pathlib import Path
import re
import signal
import subprocess
import time
from urllib.parse import urlsplit

import httpx

TRANSIENT_STATUS = {408, 429, 500, 502, 503, 504}


def retry_after(value, now=None):
    now = now or datetime.now(timezone.utc)
    try:
        if str(value).isdigit():
            return now + timedelta(seconds=int(value))
        date = parsedate_to_datetime(str(value))
        return date if date.tzinfo else date.replace(tzinfo=timezone.utc)
    except (ValueError, TypeError, OverflowError):
        return None


def bounded_get(url, *, attempts=3, **kwargs):
    """Retry only transport/temporary HTTP failures; never shorten Retry-After."""
    attempts = max(1, min(attempts, 3))
    state_path = os.environ.get('AI_SIGNAL_HTTP_STATE')
    state = json.loads(Path(state_path).read_text()) if state_path and Path(state_path).exists() else {}
    origin = urlsplit(url).netloc
    key = hashlib.sha256(origin.encode()).hexdigest()
    until = retry_after(state.get(key))
    if until and until > datetime.now(timezone.utc):
        return httpx.Response(429, headers={'retry-after': until.strftime('%a, %d %b %Y %H:%M:%S GMT')},
                              request=httpx.Request('GET', url))
    waited = 0
    for attempt in range(attempts):
        try:
            response = httpx.get(url, **kwargs)
        except httpx.TransportError:
            if attempt == attempts - 1:
                raise
            delay = 1.5 * 2 ** attempt
        else:
            until = retry_after(response.headers.get('retry-after'))
            if state_path and response.status_code in TRANSIENT_STATUS and until:
                from cloud_whisper_pilot import atomic_json
                state = {k: v for k, v in state.items() if retry_after(v) and retry_after(v) > datetime.now(timezone.utc)}
                state[key] = until.strftime('%a, %d %b %Y %H:%M:%S GMT')
                atomic_json(Path(state_path), state)
            if response.status_code not in TRANSIENT_STATUS or attempt == attempts - 1:
                return response
            now = datetime.now(timezone.utc)
            until = retry_after(response.headers.get('retry-after'), now)
            delay = max(1.5 * 2 ** attempt, (until - now).total_seconds() if until else 0)
            if waited + delay > 30:
                # Caller reports degradation; durable audio queue saves the date.
                return response
        time.sleep(delay)
        waited += delay


def redact(text):
    text = re.sub(r'https?://[^\s<>"\']+', '[URL redacted]', str(text))
    text = re.sub(r'(?i)(authorization|token|password|secret|api[_-]?key)(\s*[:=]\s*)\S+',
                  r'\1\2[redacted]', text)
    text = re.sub(r'\b(?:gh[pousr]_[A-Za-z0-9_]+|github_pat_[A-Za-z0-9_]+)\b', '[redacted]', text)
    return text


class OperationError(RuntimeError):
    def __init__(self, kind, retryable=False):
        super().__init__(kind)
        self.kind, self.retryable = kind, retryable


def command_failure(output):
    value = output.lower()
    if value.startswith('invalid transcript retention index:') and 'expired transcript remains indexed' in value:
        return OperationError('expired_retention')
    if any(word in value for word in ('permission denied', 'authentication failed', 'bad credentials',
                                      'could not read username', 'http 401', 'http 403')):
        return OperationError('credentials_or_permission')
    if any(word in value for word in ('conflict', 'non-fast-forward', 'divergent branches')):
        return OperationError('publication_conflict')
    if any(word in value for word in ('could not resolve', 'connection timed out', 'connection reset',
                                      'connection refused', 'remote end hung up', 'http 502', 'http 503',
                                      'http 504', 'error: 502', 'error: 503', 'error: 504')):
        return OperationError('temporary_network', True)
    return OperationError('unknown_command_failure')


def process(args, *, cwd, timeout, output=None, env=None):
    """Kill the entire subprocess tree on timeout/interruption, then reap it."""
    proc = subprocess.Popen(args, cwd=cwd, text=True, stdout=output or subprocess.PIPE,
                            stderr=subprocess.STDOUT, start_new_session=True, env=env)
    try:
        stdout, _ = proc.communicate(timeout=timeout)
        return subprocess.CompletedProcess(args, proc.returncode, stdout=stdout or '')
    finally:
        # Descendants can outlive a parent that already exited.
        try:
            os.killpg(proc.pid, signal.SIGKILL)
        except ProcessLookupError:
            pass
        proc.wait()
        if proc.stdout:
            proc.stdout.close()
