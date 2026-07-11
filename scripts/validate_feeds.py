"""Fail fast when generated feeds contain duplicate stable identifiers."""

import json
import sys
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path


ROOT_DIR = Path(__file__).resolve().parent.parent
FEEDS_DIR = ROOT_DIR / "feeds"


def load_items(filename, key):
    path = FEEDS_DIR / filename
    data = json.loads(path.read_text("utf-8"))
    return data.get(key, [])


def load_optional_items(filename, key):
    path = FEEDS_DIR / filename
    if not path.is_file():
        return []
    return json.loads(path.read_text("utf-8")).get(key, [])


def duplicate_values(values):
    counts = Counter(str(value) for value in values if value)
    return sorted(value for value, count in counts.items() if count > 1)


def transcript_sidecar_failures(podcasts):
    failures = []
    root = ROOT_DIR.resolve()
    for episode in podcasts:
        label = episode.get("guid") or episode.get("title") or "unknown episode"
        if episode.get("transcript"):
            failures.append(f"{label}: transcript is still inline")
        path_text = episode.get("transcript_path")
        if not path_text:
            if episode.get("transcript_available"):
                failures.append(f"{label}: marked available without transcript_path")
            continue
        path = (ROOT_DIR / str(path_text)).resolve()
        try:
            path.relative_to(root)
        except ValueError:
            failures.append(f"{label}: transcript_path escapes repository")
            continue
        if not path.is_file() or path.stat().st_size == 0:
            failures.append(f"{label}: transcript sidecar missing or empty")
    return failures


def transcript_index_failures(podcasts, index_items, now=None):
    now = now or datetime.now(timezone.utc)
    failures = []
    indexed_paths = {item.get("transcript_path") for item in index_items if item.get("transcript_path")}
    for episode in podcasts:
        path = episode.get("transcript_path")
        if episode.get("transcript_available") and path not in indexed_paths:
            label = episode.get("guid") or episode.get("title") or "unknown episode"
            failures.append(f"{label}: active transcript missing from retention index")

    for item in index_items:
        label = item.get("guid") or item.get("title") or "unknown episode"
        try:
            expires_at = datetime.fromisoformat(str(item.get("expires_at", "")).replace("Z", "+00:00"))
            if expires_at.tzinfo is None:
                expires_at = expires_at.replace(tzinfo=timezone.utc)
        except ValueError:
            failures.append(f"{label}: invalid expires_at")
            continue
        if expires_at <= now:
            failures.append(f"{label}: expired transcript remains indexed")

    disk_paths = {
        path.relative_to(ROOT_DIR).as_posix()
        for path in (FEEDS_DIR / "transcripts").glob("*.txt")
    }
    for path in sorted(disk_paths - indexed_paths):
        failures.append(f"{path}: orphan transcript sidecar")
    return failures


def validate():
    tweets = [
        tweet
        for account in load_items("feed-x.json", "x")
        for tweet in account.get("tweets", [])
    ]
    podcasts = load_items("feed-podcasts.json", "podcasts")
    papers = load_items("feed-arxiv.json", "papers")
    articles = load_items("feed-blogs.json", "articles")
    transcript_index = load_optional_items("feed-transcripts-index.json", "transcripts")

    checks = {
        "tweet IDs": duplicate_values(tweet.get("id") or tweet.get("url") for tweet in tweets),
        "podcast keys": duplicate_values(
            episode.get("guid") or episode.get("link") or episode.get("title")
            for episode in podcasts
        ),
        "arXiv IDs": duplicate_values(paper.get("arxiv_id") for paper in papers),
        "blog IDs": duplicate_values(article.get("id") or article.get("url") for article in articles),
        "podcast transcript sidecars": transcript_sidecar_failures(podcasts),
        "transcript index IDs": duplicate_values(
            item.get("guid") or item.get("link") or item.get("title") for item in transcript_index
        ),
        "transcript retention index": (
            transcript_sidecar_failures(transcript_index)
            + transcript_index_failures(podcasts, transcript_index)
        ),
    }
    failures = {name: values for name, values in checks.items() if values}
    if failures:
        for name, values in failures.items():
            print(f"Invalid {name}: {', '.join(values)}", file=sys.stderr)
        return False

    print(
        "Feed uniqueness OK: "
        f"{len(tweets)} tweets, {len(podcasts)} podcasts, "
        f"{len(papers)} papers, {len(articles)} blog articles"
    )
    return True


if __name__ == "__main__":
    raise SystemExit(0 if validate() else 1)
