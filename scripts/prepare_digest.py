"""Subscriber-side: fetch central feeds + user config, output JSON for LLM.

Pulls feed-x.json and feed-podcasts.json from the central GitHub repo,
combines with the user's local config and prompt preferences,
and outputs a single JSON blob to stdout for the LLM to process.

Usage:
    python scripts/prepare_digest.py

Output: JSON to stdout (the LLM reads this and generates the digest)
"""

import json
import os
import sys
from pathlib import Path

import httpx

SCRIPT_DIR = Path(__file__).parent
ROOT_DIR = SCRIPT_DIR.parent

FEED_X_URL = "https://raw.githubusercontent.com/Benboerba620/ai-signal/main/feeds/feed-x.json"
FEED_PODCASTS_URL = "https://raw.githubusercontent.com/Benboerba620/ai-signal/main/feeds/feed-podcasts.json"

PROMPTS_BASE = "https://raw.githubusercontent.com/Benboerba620/ai-signal/main/prompts"
PROMPT_FILES = [
    "summarize-podcast.md",
    "summarize-tweets.md",
    "digest-intro.md",
]

USER_DIR = Path.home() / ".ai-signal"
CONFIG_PATH = USER_DIR / "config.json"


def fetch_json(url):
    try:
        resp = httpx.get(url, timeout=30, follow_redirects=True)
        resp.raise_for_status()
        return resp.json()
    except Exception:
        return None


def fetch_text(url):
    try:
        resp = httpx.get(url, timeout=15, follow_redirects=True)
        resp.raise_for_status()
        return resp.text
    except Exception:
        return None


def main():
    errors = []

    # 1. User config
    config = {"language": "en", "granularity": "summary", "delivery": {"method": "stdout"}}
    if CONFIG_PATH.exists():
        try:
            config = json.loads(CONFIG_PATH.read_text("utf-8"))
        except Exception as e:
            errors.append(f"Config read error: {e}")

    # 2. Fetch feeds
    feed_x = fetch_json(FEED_X_URL)
    feed_podcasts = fetch_json(FEED_PODCASTS_URL)
    if not feed_x:
        errors.append("Could not fetch tweet feed")
    if not feed_podcasts:
        errors.append("Could not fetch podcast feed")

    # 3. Load prompts: user custom > remote > local
    prompts = {}
    user_prompts_dir = USER_DIR / "prompts"
    local_prompts_dir = ROOT_DIR / "prompts"

    for filename in PROMPT_FILES:
        key = filename.replace(".md", "").replace("-", "_")
        user_path = user_prompts_dir / filename
        local_path = local_prompts_dir / filename

        if user_path.exists():
            prompts[key] = user_path.read_text("utf-8")
            continue
        remote = fetch_text(f"{PROMPTS_BASE}/{filename}")
        if remote:
            prompts[key] = remote
            continue
        if local_path.exists():
            prompts[key] = local_path.read_text("utf-8")
        else:
            errors.append(f"Could not load prompt: {filename}")

    # 4. Build output
    output = {
        "status": "ok",
        "generated_at": (feed_x or {}).get("generated_at") or (feed_podcasts or {}).get("generated_at"),
        "config": {
            "language": config.get("language", "en"),
            "granularity": config.get("granularity", "summary"),
            "domains": config.get("domains", ["ai", "energy", "invest"]),
            "delivery": config.get("delivery", {"method": "stdout"}),
        },
        "podcasts": (feed_podcasts or {}).get("podcasts", []),
        "x": (feed_x or {}).get("x", []),
        "stats": {
            "podcast_episodes": len((feed_podcasts or {}).get("podcasts", [])),
            "podcast_with_transcript": sum(1 for e in (feed_podcasts or {}).get("podcasts", []) if e.get("transcript")),
            "x_builders": len((feed_x or {}).get("x", [])),
            "total_tweets": sum(len(a.get("tweets", [])) for a in (feed_x or {}).get("x", [])),
        },
        "prompts": prompts,
        "errors": errors if errors else None,
    }

    print(json.dumps(output, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
