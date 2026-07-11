"""Store podcast transcripts as per-episode sidecars instead of inline feed data."""

import hashlib
import json
from datetime import datetime, timedelta, timezone
from pathlib import Path


ROOT_DIR = Path(__file__).resolve().parent.parent
DEFAULT_FEED_PATH = ROOT_DIR / "feeds" / "feed-podcasts.json"
DEFAULT_INDEX_PATH = ROOT_DIR / "feeds" / "feed-transcripts-index.json"
TRANSCRIPT_DIR = ROOT_DIR / "feeds" / "transcripts"
TRANSCRIPT_RETENTION_DAYS = 14


def transcript_id(episode):
    identity = "\n".join(
        str(episode.get(key) or "") for key in ("guid", "link", "channel", "title")
    )
    return hashlib.sha256(identity.encode("utf-8")).hexdigest()[:24]


def episode_key(episode):
    return str(
        episode.get("guid")
        or episode.get("link")
        or episode.get("title")
        or transcript_id(episode)
    )


def parse_datetime(value):
    if not value:
        return None
    try:
        parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed


def load_index(root_dir=ROOT_DIR):
    path = Path(root_dir) / "feeds" / "feed-transcripts-index.json"
    if not path.is_file():
        return {"transcripts": []}
    try:
        return json.loads(path.read_text("utf-8"))
    except (OSError, json.JSONDecodeError):
        return {"transcripts": []}


def safe_repo_path(path_text, root_dir=ROOT_DIR):
    if not path_text:
        return None
    root = Path(root_dir).resolve()
    candidate = (root / str(path_text)).resolve()
    try:
        candidate.relative_to(root)
    except ValueError:
        return None
    return candidate


def read_local_transcript(episode, root_dir=ROOT_DIR):
    inline = episode.get("transcript")
    if inline:
        return str(inline)
    path = safe_repo_path(episode.get("transcript_path"), root_dir)
    if not path or not path.is_file():
        return ""
    return path.read_text("utf-8", errors="replace")


def normalize_transcript_text(text):
    return "\n".join(line.rstrip() for line in str(text).splitlines()).strip() + "\n"


def hydrate_transcripts(feed, root_dir=ROOT_DIR):
    """Restore sidecar text in memory for central fetch/cache operations."""
    for episode in (feed or {}).get("podcasts", []):
        if not episode.get("transcript"):
            text = read_local_transcript(episode, root_dir)
            if text:
                episode["transcript"] = text
    return feed


def index_record(episode, cached_at, last_seen_at, expires_at):
    return {
        "guid": episode.get("guid"),
        "link": episode.get("link"),
        "channel": episode.get("channel"),
        "title": episode.get("title"),
        "pub_date": episode.get("pub_date"),
        "transcript_path": episode.get("transcript_path"),
        "transcript_chars": episode.get("transcript_chars", 0),
        "transcript_source": episode.get("transcript_source"),
        "cached_at": cached_at.isoformat(),
        "last_seen_at": last_seen_at.isoformat(),
        "expires_at": expires_at.isoformat(),
    }


def externalize_transcripts(
    feed,
    root_dir=ROOT_DIR,
    prune=True,
    retention_days=TRANSCRIPT_RETENTION_DAYS,
    now=None,
):
    """Write sidecars, retain an expiry index, and return a metadata-only feed."""
    root = Path(root_dir)
    transcript_dir = root / "feeds" / "transcripts"
    transcript_dir.mkdir(parents=True, exist_ok=True)
    now = now or datetime.now(timezone.utc)
    if now.tzinfo is None:
        now = now.replace(tzinfo=timezone.utc)

    existing_records = {
        episode_key(record): record
        for record in load_index(root).get("transcripts", [])
        if episode_key(record)
    }
    retained_records = {}
    for key, record in existing_records.items():
        expires_at = parse_datetime(record.get("expires_at"))
        path = safe_repo_path(record.get("transcript_path"), root)
        if expires_at and expires_at > now and path and path.is_file():
            retained_records[key] = record

    for episode in (feed or {}).get("podcasts", []):
        key = episode_key(episode)
        text = str(episode.pop("transcript", "") or "")
        existing_path = safe_repo_path(episode.get("transcript_path"), root)
        if text:
            text = normalize_transcript_text(text)
            path = transcript_dir / f"{transcript_id(episode)}.txt"
            path.write_text(text, encoding="utf-8")
            episode["transcript_path"] = path.relative_to(root).as_posix()
            episode["transcript_chars"] = len(text)
            episode["transcript_available"] = True
        elif existing_path and existing_path.is_file():
            text = normalize_transcript_text(
                existing_path.read_text("utf-8", errors="replace")
            )
            existing_path.write_text(text, encoding="utf-8")
            episode["transcript_path"] = existing_path.relative_to(root.resolve()).as_posix()
            episode["transcript_chars"] = len(text)
            episode["transcript_available"] = True
        else:
            episode.pop("transcript_path", None)
            episode["transcript_chars"] = 0
            episode["transcript_available"] = False

        if episode.get("transcript_available") and episode.get("transcript_path"):
            old = existing_records.get(key) or {}
            cached_at = parse_datetime(old.get("cached_at")) or now
            last_seen_at = now
            expires_at = last_seen_at + timedelta(days=retention_days)
            retained_records[key] = index_record(
                episode, cached_at, last_seen_at, expires_at
            )

    records = []
    retained_paths = set()
    for record in retained_records.values():
        expires_at = parse_datetime(record.get("expires_at"))
        path = safe_repo_path(record.get("transcript_path"), root)
        if not expires_at or expires_at <= now or not path or not path.is_file():
            continue
        records.append(record)
        retained_paths.add(path.resolve())

    records.sort(key=lambda record: record.get("cached_at", ""), reverse=True)
    index = {
        "generated_at": now.isoformat(),
        "retention_days": retention_days,
        "transcripts": records,
    }
    index_path = root / "feeds" / "feed-transcripts-index.json"
    index_path.write_text(json.dumps(index, ensure_ascii=False, indent=2), encoding="utf-8")

    if prune:
        for path in transcript_dir.glob("*.txt"):
            if path.resolve() not in retained_paths:
                path.unlink()
    return feed


def main():
    feed = json.loads(DEFAULT_FEED_PATH.read_text("utf-8"))
    externalize_transcripts(feed)
    DEFAULT_FEED_PATH.write_text(
        json.dumps(feed, ensure_ascii=False, indent=2), encoding="utf-8"
    )


if __name__ == "__main__":
    main()
