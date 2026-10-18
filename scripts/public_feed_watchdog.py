"""Independent published-feed observer; no capture, transcription or repo writes."""
import argparse
from datetime import datetime, timedelta, timezone
import hashlib
import json
import os
from pathlib import Path
import urllib.request
from zoneinfo import ZoneInfo
from signal_alerts import deliver

SHANGHAI = ZoneInfo('Asia/Shanghai')


def expected_at(at, hour, minute, grace, weekdays=False):
    local = at.astimezone(SHANGHAI)
    expected = local.replace(hour=hour, minute=minute, second=0, microsecond=0)
    if local < expected + timedelta(minutes=grace):
        expected -= timedelta(days=1)
    if weekdays:
        while expected.weekday() >= 5:
            expected -= timedelta(days=1)
    return expected


def inspect(root, at):
    problems, summary = [], {}
    for name, field in [('podcasts','podcasts'), ('blogs','articles'), ('arxiv','papers'), ('x','x')]:
        due = expected_at(at, 4, 20 if name == 'x' else 13, 60 if name == 'x' else 80)
        if name == 'arxiv':
            due = max(due, expected_at(at, 9, 30, 25, weekdays=True))
        try:
            data = json.loads((root/f'feeds/feed-{name}.json').read_text())
            stamp = datetime.fromisoformat(data['generated_at'].replace('Z','+00:00'))
            if stamp.tzinfo is None or not isinstance(data[field], list):
                raise ValueError('schema')
            summary[name] = stamp.astimezone(SHANGHAI).strftime('%m-%d %H:%M')
            if stamp < due:
                problems.append(f'{name}:stale')
            if stamp > at + timedelta(minutes=5):
                problems.append(f'{name}:future_timestamp')
            if data.get('errors'):
                problems.append(f'{name}:upstream_errors')
            if name == 'podcasts':
                missing = 0
                for item in data[field]:
                    relative = item.get('transcript_path')
                    if not item.get('transcript_available') or not relative:
                        missing += 1
                        continue
                    path = (root/relative).resolve()
                    if (root/'feeds/transcripts').resolve() not in path.parents or not path.is_file():
                        missing += 1
                    elif item.get('transcript_sha256') and hashlib.sha256(path.read_bytes()).hexdigest() != item['transcript_sha256']:
                        missing += 1
                if missing:
                    problems.append('podcasts:missing_fulltext')
                    summary['missing_fulltext'] = missing
        except Exception:
            problems.append(f'{name}:unreadable')
    return sorted(set(problems)), summary


def telegram(text):
    token, chat = os.environ.get('AI_SIGNAL_ALERT_BOT_TOKEN'), os.environ.get('AI_SIGNAL_ALERT_CHAT_ID')
    if not token or not chat:
        return {'status': 'credentials_unavailable'}
    body = json.dumps({'chat_id':chat, 'text':text, 'disable_web_page_preview':True}).encode()
    request = urllib.request.Request('https://api.telegram.org/bot'+token+'/sendMessage', data=body,
                                    headers={'Content-Type':'application/json'})
    try:
        with urllib.request.urlopen(request, timeout=20) as response:
            data = json.load(response)
        message = data.get('result', {})
        accepted = (data.get('ok') is True and str(message.get('chat',{}).get('id')) == str(chat)
                    and isinstance(message.get('message_id'), int) and message.get('text') == text)
        return {'status':'sent' if accepted else 'rejected', 'text_verified':accepted,
                'message_id':message.get('message_id')}
    except Exception as exc:
        return {'status':'delivery_unknown', 'error_type':type(exc).__name__}


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--root', type=Path, default=Path(__file__).resolve().parent.parent)
    parser.add_argument('--state-dir', type=Path, required=True)
    parser.add_argument('--test-alert', action='store_true')
    args = parser.parse_args()
    at = datetime.now(timezone.utc)
    problems, summary = inspect(args.root, at)
    test = args.test_alert
    labels = {'podcasts':'播客','blogs':'博客','arxiv':'论文','x':'X'}
    lines = [f'{labels.get(k,k)}：{v}' for k,v in summary.items()]
    text = ('【AI Signal 云端告警测试】这是主动测试，不代表发生新故障。' if test else
            '【AI Signal 数据异常】' if problems else '【AI Signal 数据已恢复】')
    text += '\n检查时间：'+at.astimezone(SHANGHAI).strftime('%m-%d %H:%M')+'\n'+'\n'.join(lines)
    if problems:
        text += '\n问题：'+', '.join(problems)+'\n旧数据不能当作今日更新。请检查 Mac mini 抓取和网络。'
    text += '\n本检查运行在 GitHub，Mac mini 关机也能检查。'
    key = hashlib.sha256(json.dumps(problems).encode()).hexdigest() if problems else 'recovered'
    receipt = deliver(args.state_dir, 'cloud-test' if test else 'public',
                      at.isoformat() if test else key, bool(problems) or test, text, telegram, at)
    report = {'at':at.isoformat(), 'problems':problems, 'feeds':summary, 'receipt':receipt}
    args.state_dir.mkdir(parents=True, exist_ok=True)
    (args.state_dir/'latest-check.json').write_text(json.dumps(report, ensure_ascii=False, indent=2))
    print(json.dumps(report, ensure_ascii=False))
    # A notification failure must be visible as a failed workflow, never a green check.
    return 1 if receipt and receipt['delivery'] != 'sent' else 0


if __name__ == '__main__':
    raise SystemExit(main())
