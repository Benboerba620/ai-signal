"""Merge allowlisted local transcripts without letting producers overwrite feeds."""
import argparse
import copy
import hashlib
import json
import re
from datetime import datetime, timedelta, timezone
from pathlib import Path
from urllib.parse import urlsplit, urlunsplit, parse_qs

from podcast_transcripts import hydrate_transcripts, externalize_transcripts, parse_datetime

ROOT = Path(__file__).resolve().parent.parent
PUBLIC_FIELDS = {'channel', 'domain', 'guid', 'title', 'pub_date', 'link', 'audio_url',
                 'duration', 'description', 'transcript', 'transcript_source',
                 'transcript_url', 'transcript_sha256'}


def seconds(value):
    try:
        result = 0
        for part in str(value).rstrip('s').split(':'):
            result = result * 60 + int(part)
        return result
    except (ValueError, TypeError):
        return 0


def url_key(value):
    try:
        p = urlsplit(str(value or ''))
        if not p.hostname:
            return ''
        host = p.hostname.lower()
        if host in ('youtube.com', 'www.youtube.com', 'm.youtube.com', 'youtu.be'):
            vid = p.path.strip('/') if host == 'youtu.be' else parse_qs(p.query).get('v', [''])[0]
            if vid:
                return 'youtube:' + vid
        return urlunsplit((p.scheme.lower(), p.netloc.lower(), p.path.rstrip('/'), p.query, ''))
    except ValueError:
        return ''


def identities(ep):
    keys = {url_key(ep.get(k)) for k in ('link', 'audio_url', 'transcript_url')}
    if ep.get('guid'):
        keys.add('guid:' + str(ep['guid']))
    if ep.get('transcript_video_id'):
        keys.add('youtube:' + ep['transcript_video_id'])
    return keys - {''}


def title_key(title):
    title = re.sub(r'^ep\.?\s*\d+\s*[-–—:]?\s*', '', title.lower())
    return re.sub(r'[^\w]', '', title)


def same_episode(a, b):
    if a.get('channel') != b.get('channel'):
        # Person-search records can name the YouTube channel differently.
        return bool({k for k in identities(a) & identities(b) if not k.startswith('guid:')})
    if identities(a) & identities(b):
        return True
    da, db = parse_datetime(a.get('pub_date')), parse_datetime(b.get('pub_date'))
    return bool(da and db and abs((da-db).total_seconds()) <= 86400
                and title_key(a.get('title', '')) == title_key(b.get('title', ''))
                and title_key(a.get('title', '')))


def load_local(root=ROOT, now=None):
    root = Path(root)
    cfg = json.loads((root/'config/podcast-publication.json').read_text())
    if not cfg.get('enabled'):
        return []
    now = now or datetime.now(timezone.utc)
    cutoff = now - timedelta(days=cfg['lookback_days'])
    result = []
    for path in sorted((root/'feeds/podcast-inbox').glob('*.json')):
        ep = json.loads(path.read_text())
        if set(ep) - PUBLIC_FIELDS or ep.get('channel') not in cfg['channels']:
            raise ValueError(f'Unexpected fields or non-public channel: {path.name}')
        date = parse_datetime(ep.get('pub_date'))
        if not date or date > now + timedelta(hours=24):
            raise ValueError(f'Invalid publication date: {path.name}')
        if date < cutoff:
            continue
        if not ep.get('guid') or not ep.get('title') or ep.get('domain') != 'ai':
            raise ValueError(f'Missing public identity: {path.name}')
        for key in ('link', 'audio_url', 'transcript_url'):
            value = ep.get(key, '')
            p = urlsplit(value)
            if value and (p.scheme != 'https' or not p.hostname or p.username or p.password):
                raise ValueError(f'Non-public URL: {path.name}')
        text = ep.get('transcript', '')
        minimum = max(600, seconds(ep.get('duration')) / 60 * 150)
        if not isinstance(text, str) or not minimum <= len(text) <= 1000000:
            raise ValueError(f'Sparse or invalid transcript: {path.name}')
        digest = hashlib.sha256(text.encode()).hexdigest()
        if digest != ep.get('transcript_sha256'):
            raise ValueError(f'Transcript hash mismatch: {path.name}')
        if ep.get('transcript_source') not in ('local_youtube', 'local_whisper'):
            raise ValueError(f'Unexpected transcript provenance: {path.name}')
        result.append(ep)
    return result


def merge_local(feed, local):
    """Preserve cloud identity and good text; local text fills only absent/sparse text."""
    feed = copy.deepcopy(feed)
    items = feed.setdefault('podcasts', [])
    stats = {'candidates': len(local), 'added': 0, 'filled': 0, 'reused': 0}
    for candidate in local:
        matches = [ep for ep in items if same_episode(ep, candidate)]
        if len(matches) > 1:
            # Consolidate duplicate cloud/person records, favor the RSS identity.
            matches.sort(key=lambda ep: bool(ep.get('person')))
            for duplicate in matches[1:]:
                if len(duplicate.get('transcript') or '') > len(matches[0].get('transcript') or ''):
                    for key in ('transcript', 'transcript_source', 'transcript_url', 'transcript_sha256'):
                        if key in duplicate:
                            matches[0][key] = duplicate[key]
                items.remove(duplicate)
        target = matches[0] if matches else None
        if target is None:
            target = copy.deepcopy(candidate)
            items.append(target)
            stats['added'] += 1
        elif len(target.get('transcript') or '') >= max(600, seconds(target.get('duration'))/60*150):
            stats['reused'] += 1
            continue
        else:
            for key in ('transcript', 'transcript_source', 'transcript_url', 'transcript_sha256'):
                target[key] = candidate[key]
            stats['filled'] += 1
        target['transcript_available'] = True
        target['transcript_error'] = None
    items.sort(key=lambda ep: ep.get('pub_date', ''), reverse=True)
    feed['local_import'] = stats
    return feed


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('--root', type=Path, default=ROOT)
    p.add_argument('--check', action='store_true')
    args = p.parse_args()
    local = load_local(args.root)
    feed_path = args.root/'feeds/feed-podcasts.json'
    feed = json.loads(feed_path.read_text())
    hydrate_transcripts(feed, args.root)
    merged = merge_local(feed, local)
    print(json.dumps(merged['local_import']))
    if not args.check:
        # Do not restamp generated_at: importing a local episode is not a fresh cloud scan.
        externalize_transcripts(merged, args.root)
        feed_path.write_text(json.dumps(merged, ensure_ascii=False, indent=2))


if __name__ == '__main__':
    main()
