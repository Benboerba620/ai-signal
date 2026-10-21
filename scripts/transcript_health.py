"""Separate normal coverage gaps from actionable transcript failures."""
from datetime import datetime, timedelta
import hashlib
import json


def stamp(value):
    result = datetime.fromisoformat(str(value).replace('Z', '+00:00'))
    if result.tzinfo is None:
        raise ValueError('Timezone required')
    return result


def assess(root, at, verify_files=True):
    feed = json.loads((root/'feeds/feed-podcasts.json').read_text())
    path = root/'feeds/whisper-queue.json'
    queue = json.loads(path.read_text()).get('entries', {}) if path.exists() else {}
    entries = {(e.get('episode', {}).get('channel'), e.get('episode', {}).get('guid')): e
               for e in queue.values()}
    counts = {'full_transcripts':0, 'pending_transcripts':0, 'coverage_only':0,
              'overdue_transcripts':0, 'failed_transcripts':0}
    issues = set()
    for item in feed.get('podcasts', []):
        entry = entries.get((item.get('channel'), item.get('guid')), {})
        relative = item.get('transcript_path')
        if item.get('transcript_available') and relative:
            if verify_files:
                path = (root/relative).resolve()
                if ((root/'feeds/transcripts').resolve() not in path.parents or not path.is_file()
                        or not path.stat().st_size or (item.get('transcript_sha256') and
                        hashlib.sha256(path.read_bytes()).hexdigest() != item['transcript_sha256'])):
                    issues.add('transcript_corrupt')
                    continue
            counts['full_transcripts'] += 1
            continue
        status = entry.get('status')
        if status in ('failed', 'retry'):
            counts['failed_transcripts'] += 1
            issues.add('transcript_failed')
        elif status in ('pending', 'running', 'processing'):
            counts['pending_transcripts'] += 1
            # first_seen_at is immutable across retries and daily feed refreshes.
            try:
                overdue = at - stamp(entry.get('first_seen_at')) >= timedelta(hours=24)
            except (ValueError, TypeError):
                issues.add('transcript_queue_invalid')
                continue
            if overdue:
                counts['overdue_transcripts'] += 1
                issues.add('transcript_overdue')
        elif status == 'completed':
            issues.add('transcript_corrupt')
        else:
            # The producer's eligibility policy decides queue membership. A show
            # without ASR enabled is coverage information, not a failed capture.
            counts['coverage_only'] += 1
    return sorted(issues), counts
