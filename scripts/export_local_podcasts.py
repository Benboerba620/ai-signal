"""Export only public, RSS-verified raw transcripts; never read research summaries."""
import argparse
import ast
import hashlib
import json
import re
from datetime import datetime, timedelta, timezone
from pathlib import Path

import httpx
from generate_feed import parse_rss
from local_podcast_import import ROOT, seconds, title_key, url_key, load_local


def read_registry(path):
    for node in ast.parse(Path(path).read_text()).body:
        if isinstance(node, ast.Assign) and any(isinstance(t, ast.Name) and t.id == 'CHANNELS' for t in node.targets):
            return {x['name']: x for x in ast.literal_eval(node.value)}
    raise ValueError('No CHANNELS in registry')


def read_transcript(path):
    content = path.read_text()
    header, sep, text = content.partition('\n---\n\n')
    if not sep or not header.startswith('# '):
        return None
    meta = dict(re.findall(r'^- \*\*([^*]+)\*\*: (.*)$', header, re.M))
    if not all(meta.get(k) for k in ('Channel', 'Date', 'URL', 'Duration')):
        return None
    try:
        date = datetime.fromisoformat(meta['Date']).replace(tzinfo=timezone.utc)
    except ValueError:
        return None
    source = meta.get('Source method', '')
    youtube = url_key(meta['URL']).startswith('youtube:')
    if not youtube and 'whisper' not in source.lower():
        return None
    text = text.strip() + '\n'
    if len(text) < max(600, seconds(meta['Duration'])/60*150):
        return None
    return {'channel': meta['Channel'], 'title': header.splitlines()[0][2:],
            'date': date, 'url': meta['URL'], 'duration': seconds(meta['Duration']),
            'transcript': text, 'method': 'local_youtube' if youtube else 'local_whisper'}


def match_episode(local, episodes):
    exact = [e for e in episodes if url_key(local['url']) in
             {url_key(e.get('link')), url_key(e.get('audio_url'))} - {''}]
    if len(exact) == 1:
        return exact[0]
    # Title alone is never enough; require same date (timezone tolerance), duration,
    # and a unique match in the publisher's current RSS. No approximate title matches.
    matches = []
    for ep in episodes:
        date = ep.get('pub_date')
        duration = seconds(ep.get('duration'))
        if (date and abs((date-local['date']).total_seconds()) <= 86400
                and duration and local['duration']
                and abs(duration-local['duration']) <= max(60, duration*.05)
                and title_key(ep['title']) == title_key(local['title'])):
            matches.append(ep)
    return matches[0] if len(matches) == 1 else None


def build_exports(registry, transcript_dir, cfg, fetch, now=None):
    now = now or datetime.now(timezone.utc)
    cutoff = now - timedelta(days=cfg['lookback_days'])
    candidates = []
    # Only this exact raw-transcripts directory is scanned, never recursively.
    for path in sorted(Path(transcript_dir).glob('*.md')):
        # Filename gate avoids reading years of material and unrelated sources.
        if path.name[:10] < cutoff.date().isoformat():
            continue
        local = read_transcript(path)
        if local and local['channel'] in cfg['channels'] and cutoff <= local['date'] <= now:
            candidates.append(local)
    exports, report = {}, {'matched': 0, 'unmatched': [], 'errors': [], 'missing_local': []}
    for channel in cfg['channels']:
        try:
            episodes = parse_rss(fetch(registry[channel]['rss_url']))
            if not episodes:
                raise ValueError('No RSS episodes parsed')
        except Exception as exc:
            report['errors'].append({'channel': channel, 'error': type(exc).__name__})
            continue
        seen = set()
        for local in [x for x in candidates if x['channel'] == channel]:
            ep = match_episode(local, episodes)
            if not ep or not ep.get('audio_url') or not ep.get('pub_date') or ep['pub_date'] < cutoff:
                report['unmatched'].append({'channel': channel, 'title': local['title']})
                continue
            key = hashlib.sha256((channel+'\n'+ep['guid']).encode()).hexdigest()[:24]
            seen.add(ep['guid'])
            # Prefer publisher-channel subtitles over local ASR when both exist.
            if key in exports and exports[key]['transcript_source'] == 'local_youtube':
                continue
            exports[key] = {
                'channel': channel, 'domain': 'ai', 'guid': ep['guid'], 'title': ep['title'],
                'pub_date': ep['pub_date'].isoformat(), 'link': ep['link'],
                'audio_url': ep['audio_url'], 'duration': ep['duration'],
                'description': ep['description'], 'transcript': local['transcript'],
                'transcript_source': local['method'], 'transcript_url': local['url'],
                'transcript_sha256': hashlib.sha256(local['transcript'].encode()).hexdigest(),
            }
        report['missing_local'].extend({'channel': channel, 'title': e['title']}
            for e in episodes if e.get('audio_url') and e.get('pub_date')
            and cutoff <= e['pub_date'] <= now and e['guid'] not in seen)
    report['matched'] = len(exports)
    return exports, report


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('--registry', type=Path, required=True)
    p.add_argument('--transcript-dir', type=Path, required=True)
    p.add_argument('--root', type=Path, default=ROOT)
    p.add_argument('--proxy')
    p.add_argument('--write', action='store_true', help='Write publication inbox; default is dry run')
    p.add_argument('--report', type=Path)
    args = p.parse_args()
    cfg = json.loads((args.root/'config/podcast-publication.json').read_text())
    if not cfg.get('enabled'):
        print('Publication disabled')
        return
    with httpx.Client(proxy=args.proxy, timeout=35, follow_redirects=True) as client:
        def fetch(url):
            response = client.get(url)
            response.raise_for_status()
            return response.text
        exports, report = build_exports(read_registry(args.registry), args.transcript_dir, cfg, fetch)
    print(json.dumps(report, ensure_ascii=False, indent=2))
    if args.report:
        args.report.parent.mkdir(parents=True, exist_ok=True)
        args.report.write_text(json.dumps({'checked_at': datetime.now(timezone.utc).isoformat(), **report}, ensure_ascii=False, indent=2))
    # Fail closed before writing or deleting anything on a source outage.
    if report['errors']:
        raise SystemExit(1)
    if args.write:
        inbox = args.root/'feeds/podcast-inbox'
        inbox.mkdir(parents=True, exist_ok=True)
        for key, ep in exports.items():
            path = inbox/(key+'.json')
            temp = path.with_suffix('.tmp')
            temp.write_text(json.dumps(ep, ensure_ascii=False, indent=2)+'\n')
            temp.replace(path)
        # Retire only expired entries. A missing local file never erases a published one.
        cutoff = datetime.now(timezone.utc)-timedelta(days=cfg['lookback_days'])
        from podcast_transcripts import parse_datetime
        for path in inbox.glob('*.json'):
            if parse_datetime(json.loads(path.read_text()).get('pub_date')) < cutoff:
                path.unlink()
        load_local(args.root)  # exact consumer contract before any upload


if __name__ == '__main__':
    main()
