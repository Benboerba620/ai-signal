"""Durable, bounded notifications. Unknown delivery is not success."""
import json
import os
import tempfile
from datetime import datetime, timezone, timedelta
from pathlib import Path


def save(path, value):
    fd, tmp = tempfile.mkstemp(dir=path.parent)
    try:
        with os.fdopen(fd, 'w') as f:
            json.dump(value, f, ensure_ascii=False, indent=2)
            f.flush(); os.fsync(f.fileno())
        os.replace(tmp, path)
    finally:
        Path(tmp).unlink(missing_ok=True)


def deliver(directory, scope, key, problem, text, sender, at=None):
    """Caller serializes sends. Hourly follow-up after uncertainty, 6h reminder."""
    at = at or datetime.now(timezone.utc)
    directory = Path(directory)
    directory.mkdir(parents=True, exist_ok=True)
    path = directory / f'alert-{scope}.json'
    previous = json.loads(path.read_text()) if path.exists() else {}
    same = previous.get('key') == key
    if not problem and not previous:
        return None
    if same:
        if not problem and previous.get('delivery') == 'sent':
            return None
        interval = timedelta(hours=6 if previous.get('delivery') == 'sent' else 1)
        if at - datetime.fromisoformat(previous['at']) < interval:
            return None
        text = ('【持续故障提醒】\n' if previous.get('delivery') == 'sent' else
                '【状态复核：上次提醒未确认送达，本条以当前检查为准】\n') + text
    receipt = {'key': key, 'at': at.isoformat(), 'delivery': 'reserved',
               'scope': scope, 'problem': problem,
               'attempt': previous.get('attempt', 0) + 1 if same else 1}
    save(path, receipt)
    journal = directory/'notification-history.jsonl'
    def append(event):
        with journal.open('a') as f:
            f.write(json.dumps(dict(receipt, event=event), ensure_ascii=False)+'\n')
            f.flush(); os.fsync(f.fileno())
    append('attempt')
    try:
        result = sender(text)
        receipt['delivery'] = result.get('status', 'delivery_unknown')
        # Never retain token, destination, message body or arbitrary remote errors.
        for field in ('message_id', 'message_ids', 'text_verified', 'transport_code', 'error_code', 'error_type'):
            if field in result:
                receipt[field] = result[field]
    except Exception as exc:
        receipt.update(delivery='local_error', error_type=type(exc).__name__)
    save(path, receipt)
    append('result')
    return receipt
