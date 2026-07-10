"""Fail fast when generated feeds contain duplicate stable identifiers."""

import json
import sys
from collections import Counter
from pathlib import Path


ROOT_DIR = Path(__file__).resolve().parent.parent
FEEDS_DIR = ROOT_DIR / "feeds"


def load_items(filename, key):
    path = FEEDS_DIR / filename
    data = json.loads(path.read_text("utf-8"))
    return data.get(key, [])


def duplicate_values(values):
    counts = Counter(str(value) for value in values if value)
    return sorted(value for value, count in counts.items() if count > 1)


def validate():
    tweets = [
        tweet
        for account in load_items("feed-x.json", "x")
        for tweet in account.get("tweets", [])
    ]
    podcasts = load_items("feed-podcasts.json", "podcasts")
    papers = load_items("feed-arxiv.json", "papers")
    articles = load_items("feed-blogs.json", "articles")

    checks = {
        "tweet IDs": duplicate_values(tweet.get("id") or tweet.get("url") for tweet in tweets),
        "podcast keys": duplicate_values(
            episode.get("guid") or episode.get("link") or episode.get("title")
            for episode in podcasts
        ),
        "arXiv IDs": duplicate_values(paper.get("arxiv_id") for paper in papers),
        "blog IDs": duplicate_values(article.get("id") or article.get("url") for article in articles),
    }
    failures = {name: values for name, values in checks.items() if values}
    if failures:
        for name, values in failures.items():
            print(f"Duplicate {name}: {', '.join(values)}", file=sys.stderr)
        return False

    print(
        "Feed uniqueness OK: "
        f"{len(tweets)} tweets, {len(podcasts)} podcasts, "
        f"{len(papers)} papers, {len(articles)} blog articles"
    )
    return True


if __name__ == "__main__":
    raise SystemExit(0 if validate() else 1)
