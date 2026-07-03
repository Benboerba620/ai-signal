"""Subscriber-side: fetch central feeds + user config, prepare digest payload.

Pulls feed JSONs from the central GitHub repo, combines them with the user's
local config and prompt preferences, then:

1. Filters out items this user has already been shown (~/.ai-signal/seen.json).
   Central feeds are rolling-window snapshots; per-user dedup happens here.
2. Writes the full payload to files (default ~/.ai-signal/payload/):
   - payload.json      — everything except transcript full text
   - transcripts/*.txt — one file per podcast episode
3. Prints a compact JSON manifest to stdout (stats, config, output contract,
   item overview, file paths). The manifest is intentionally small so any
   agent can read it from stdout; the big content is read from files.

Usage:
    python scripts/prepare_digest.py [--out DIR] [--include-seen] [--mark-seen]
"""

import argparse
import json
import os
import re
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

import httpx

SCRIPT_DIR = Path(__file__).parent
ROOT_DIR = SCRIPT_DIR.parent

RAW_BASE = "https://raw.githubusercontent.com/Benboerba620/ai-signal/main"
FEED_BASE = f"{RAW_BASE}/feeds"
FEED_X_URL = f"{FEED_BASE}/feed-x.json"
FEED_PODCASTS_URL = f"{FEED_BASE}/feed-podcasts.json"
FEED_ARXIV_URL = f"{FEED_BASE}/feed-arxiv.json"
FEED_SUMMARIES_URL = f"{FEED_BASE}/feed-summaries.json"

PROMPTS_BASE = "https://raw.githubusercontent.com/Benboerba620/ai-signal/main/prompts"
PROMPT_FILES = [
    "summarize-podcast.md",
    "summarize-tweets.md",
    "summarize-papers.md",
    "digest-intro.md",
    "translate.md",
]

USER_DIR = Path.home() / ".ai-signal"
CONFIG_PATH = USER_DIR / "config.json"
SEEN_PATH = USER_DIR / "seen.json"
DEFAULT_PAYLOAD_DIR = USER_DIR / "payload"
DEFAULT_DELIVERY_MARK = "delivery-mark.json"
SEEN_RETENTION_DAYS = 14


def configure_stdio():
    for stream in (sys.stdout, sys.stderr):
        if hasattr(stream, "reconfigure"):
            stream.reconfigure(encoding="utf-8", errors="replace")


def clean_text(text):
    return "".join(ch for ch in text if not 0xD800 <= ord(ch) <= 0xDFFF)


def clean_data(value):
    if isinstance(value, str):
        return clean_text(value)
    if isinstance(value, list):
        return [clean_data(item) for item in value]
    if isinstance(value, dict):
        return {clean_data(k): clean_data(v) for k, v in value.items()}
    return value


def normalize_language(value):
    raw = str(value or "en").strip().lower().replace("_", "-")
    aliases = {
        "zh": "zh",
        "zh-cn": "zh",
        "cn": "zh",
        "chinese": "zh",
        "simplified chinese": "zh",
        "simplified-chinese": "zh",
        "中文": "zh",
        "简体中文": "zh",
        "简中": "zh",
        "en": "en",
        "english": "en",
        "英文": "en",
        "英语": "en",
        "bilingual": "bilingual",
        "dual": "bilingual",
        "zh-en": "bilingual",
        "en-zh": "bilingual",
        "中英": "bilingual",
        "双语": "bilingual",
        "中英双语": "bilingual",
    }
    return aliases.get(raw, "en")


def build_output_contract(config):
    language = normalize_language(config.get("language", "en"))
    granularity = config.get("granularity", "summary")

    if language == "zh":
        language_policy = {
            "target": "Simplified Chinese",
            "must_translate": True,
            "final_digest_rule": (
                "Write all user-facing analysis, summaries, section headings, and connective text "
                "in natural Simplified Chinese. Keep original tweet text, titles, URLs, names, "
                "company names, model names, and common technical terms unchanged when appropriate."
            ),
            "forbidden": "Do not output an English-only digest.",
        }
    elif language == "bilingual":
        language_policy = {
            "target": "Bilingual English and Simplified Chinese",
            "must_translate": True,
            "final_digest_rule": (
                "Interleave English and Simplified Chinese item by item. Do not put all English "
                "first and all Chinese later. Keep each URL only once."
            ),
            "forbidden": "Do not output English-only sections without the matching Chinese version.",
        }
    else:
        language_policy = {
            "target": "English",
            "must_translate": False,
            "final_digest_rule": "Write the digest in English.",
            "forbidden": "Do not translate the whole digest into Chinese unless the user asks.",
        }

    return {
        "role": "You are the user's Agent-side AI Signal digest writer.",
        "source_of_truth": "Use only the JSON fields in this payload. Do not browse the web or call external APIs.",
        "language": language_policy,
        "granularity": granularity,
        "content_rules": [
            "Select only AI/product/research/infrastructure/investing-relevant items.",
            "Every included item must keep its original URL.",
            "For X/Twitter, keep each selected tweet as its own item and preserve the original text.",
            "For podcasts, use transcript first and description only when transcript is missing.",
            "For papers, keep title, arXiv link, and a short summary.",
            "Do not fabricate quotes, numbers, claims, or source details.",
        ],
    }


# ── Per-user seen state ───────────────────────────────────────────────────────

def load_seen():
    seen = {}
    if SEEN_PATH.exists():
        try:
            seen = json.loads(SEEN_PATH.read_text("utf-8"))
        except Exception:
            seen = {}
    for key in ("tweets", "episodes", "papers"):
        seen.setdefault(key, {})
    return seen


def save_seen(seen):
    cutoff = (datetime.now(timezone.utc) - timedelta(days=SEEN_RETENTION_DAYS)).isoformat()
    for key in ("tweets", "episodes", "papers"):
        seen[key] = {k: v for k, v in seen.get(key, {}).items() if v > cutoff}
    SEEN_PATH.parent.mkdir(parents=True, exist_ok=True)
    SEEN_PATH.write_text(json.dumps(seen, indent=2), encoding="utf-8")


def episode_key(episode):
    return episode.get("guid") or episode.get("link") or episode.get("title") or ""


def filter_unseen(feed_x, feed_podcasts, papers, seen):
    now = datetime.now(timezone.utc).isoformat()
    new_ids = {"tweets": [], "episodes": [], "papers": []}

    accounts = []
    for account in (feed_x or {}).get("x", []):
        tweets = [t for t in account.get("tweets", []) if t.get("id") not in seen["tweets"]]
        new_ids["tweets"].extend(t["id"] for t in tweets if t.get("id"))
        accounts.append({**account, "tweets": tweets})

    episodes = []
    for ep in (feed_podcasts or {}).get("podcasts", []):
        key = episode_key(ep)
        if key and key in seen["episodes"]:
            continue
        if key:
            new_ids["episodes"].append(key)
        episodes.append(ep)

    fresh_papers = []
    for paper in papers:
        pid = paper.get("arxiv_id") or ""
        if pid and pid in seen["papers"]:
            continue
        if pid:
            new_ids["papers"].append(pid)
        fresh_papers.append(paper)

    marks = {kind: {i: now for i in ids} for kind, ids in new_ids.items()}
    return accounts, episodes, fresh_papers, marks


# ── Payload files ─────────────────────────────────────────────────────────────

def slugify(text, max_len=60):
    text = re.sub(r"[^a-zA-Z0-9]+", "-", (text or "").lower()).strip("-")
    return text[:max_len].rstrip("-") or "untitled"


def write_payload(out_dir, output, episodes):
    out_dir.mkdir(parents=True, exist_ok=True)
    transcripts_dir = out_dir / "transcripts"
    transcripts_dir.mkdir(exist_ok=True)
    for old in transcripts_dir.glob("*.txt"):
        old.unlink()

    slim_episodes = []
    transcript_files = []
    for i, ep in enumerate(episodes, 1):
        slim = {k: v for k, v in ep.items() if k != "transcript"}
        transcript = ep.get("transcript")
        if transcript:
            fname = f"{i:02d}-{slugify(ep.get('channel'))}-{slugify(ep.get('title'))}.txt"
            path = transcripts_dir / fname
            path.write_text(clean_text(transcript), encoding="utf-8")
            slim["transcript_file"] = str(path)
            slim["transcript_chars"] = len(transcript)
            transcript_files.append(str(path))
        slim_episodes.append(slim)

    payload = {**output, "podcasts": slim_episodes}
    payload_path = out_dir / "payload.json"
    payload_path.write_text(
        json.dumps(clean_data(payload), ensure_ascii=True, indent=2), encoding="utf-8"
    )
    return payload_path, slim_episodes, transcript_files


def write_delivery_mark(out_dir, marks, generated_at):
    out_dir.mkdir(parents=True, exist_ok=True)
    path = out_dir / DEFAULT_DELIVERY_MARK
    payload = {
        "generated_at": generated_at,
        "prepared_at": datetime.now(timezone.utc).isoformat(),
        "ids": marks,
        "counts": {kind: len(ids) for kind, ids in marks.items()},
    }
    path.write_text(json.dumps(clean_data(payload), ensure_ascii=True, indent=2), encoding="utf-8")
    return path


def fetch_json(url):
    try:
        resp = httpx.get(url, timeout=30, follow_redirects=True)
        resp.raise_for_status()
        text = resp.content.decode("utf-8", errors="replace")
        return clean_data(json.loads(clean_text(text)))
    except Exception:
        return None


def fetch_text(url):
    try:
        resp = httpx.get(url, timeout=15, follow_redirects=True)
        resp.raise_for_status()
        return clean_text(resp.content.decode("utf-8", errors="replace"))
    except Exception:
        return None


def load_local_json(filename):
    path = ROOT_DIR / "feeds" / filename
    if not path.exists():
        return None
    try:
        return clean_data(json.loads(clean_text(path.read_text("utf-8", errors="replace"))))
    except Exception:
        return None


def load_local_text(path_text):
    path = ROOT_DIR / path_text
    if not path.exists():
        return None
    try:
        return clean_text(path.read_text("utf-8", errors="replace"))
    except Exception:
        return None


def fetch_feed(url, filename, content_key=None):
    remote = fetch_json(url)
    local = load_local_json(filename)
    if remote and (not content_key or remote.get(content_key)):
        return remote
    return local or remote


def choose_summary_profile(config):
    explicit = config.get("summary_profile")
    if explicit:
        return explicit

    language = normalize_language(config.get("language", "en"))
    granularity = config.get("granularity", "summary")

    if language == "zh":
        if granularity in ("highlights", "short"):
            return "zh_short"
        if granularity in ("full", "deep"):
            return "zh_deep"
        return "zh_standard"
    if language == "bilingual":
        return "bilingual_short"
    return "en_standard"


def wants_central_summaries(config):
    value = config.get("include_central_summaries", False)
    if isinstance(value, bool):
        return value
    if isinstance(value, str):
        return value.strip().lower() in ("1", "true", "yes", "on")
    return False


def filter_summary_items(items, domains):
    if not domains:
        return items
    return [item for item in items if item.get("domain", "ai") in domains]


def attach_summary_text(items):
    results = []
    for item in items:
        summary_path = item.get("summary_path")
        enriched = dict(item)
        if summary_path:
            text = fetch_text(f"{RAW_BASE}/{summary_path}") or load_local_text(summary_path)
            if text:
                enriched["summary_text"] = text
        results.append(enriched)
    return results


def main():
    configure_stdio()
    parser = argparse.ArgumentParser()
    parser.add_argument("--out", type=str, default=str(DEFAULT_PAYLOAD_DIR),
                        help="Directory for payload.json and transcripts/ (default ~/.ai-signal/payload)")
    parser.add_argument("--include-seen", action="store_true",
                        help="Include items already delivered before (regenerate today's digest)")
    parser.add_argument("--mark-seen", action="store_true",
                        help="Legacy mode: immediately record prepared items as seen")
    parser.add_argument("--no-mark-seen", action="store_true", help=argparse.SUPPRESS)
    args = parser.parse_args()
    errors = []

    # 1. User config
    config = {"language": "en", "granularity": "summary", "delivery": {"method": "stdout"}}
    if CONFIG_PATH.exists():
        try:
            config = json.loads(CONFIG_PATH.read_text("utf-8-sig"))
        except Exception as e:
            errors.append(f"Config read error: {e}")

    # 2. Fetch feeds
    feed_x = fetch_feed(FEED_X_URL, "feed-x.json", "x")
    feed_podcasts = fetch_feed(FEED_PODCASTS_URL, "feed-podcasts.json", "podcasts")
    feed_arxiv = fetch_feed(FEED_ARXIV_URL, "feed-arxiv.json", "papers")
    include_central_summaries = wants_central_summaries(config)
    feed_summaries = fetch_feed(FEED_SUMMARIES_URL, "feed-summaries.json", "profiles") if include_central_summaries else None
    if not feed_x:
        errors.append("Could not fetch tweet feed")
    if not feed_podcasts:
        errors.append("Could not fetch podcast feed")
    if not feed_arxiv:
        errors.append("Could not fetch arXiv feed")

    # 3. Load prompts: user custom > remote > local
    prompts = {}
    user_prompts_dir = USER_DIR / "prompts"
    local_prompts_dir = ROOT_DIR / "prompts"

    for filename in PROMPT_FILES:
        key = filename.replace(".md", "").replace("-", "_")
        user_path = user_prompts_dir / filename
        local_path = local_prompts_dir / filename

        if user_path.exists():
            prompts[key] = clean_text(user_path.read_text("utf-8", errors="replace"))
            continue
        remote = fetch_text(f"{PROMPTS_BASE}/{filename}")
        if remote:
            prompts[key] = remote
            continue
        if local_path.exists():
            prompts[key] = clean_text(local_path.read_text("utf-8", errors="replace"))
        else:
            errors.append(f"Could not load prompt: {filename}")

    # 4. Per-user dedup: central feeds are rolling windows, drop what this
    #    user has already been shown
    seen = load_seen()
    if args.include_seen:
        x_accounts = (feed_x or {}).get("x", [])
        episodes = (feed_podcasts or {}).get("podcasts", [])
        papers = (feed_arxiv or {}).get("papers", [])
        marks = {"tweets": {}, "episodes": {}, "papers": {}}
    else:
        x_accounts, episodes, papers, marks = filter_unseen(
            feed_x, feed_podcasts, (feed_arxiv or {}).get("papers", []), seen
        )

    # 5. Build output
    language = normalize_language(config.get("language", "en"))
    domains = config.get("domains", ["ai", "invest"])
    summary_profile = choose_summary_profile(config)
    available_summary_profiles = sorted(((feed_summaries or {}).get("profiles") or {}).keys())
    selected_summary = ((feed_summaries or {}).get("profiles") or {}).get(summary_profile)
    if feed_summaries and not selected_summary:
        errors.append(
            f"Summary profile not available: {summary_profile}. "
            f"Available profiles: {', '.join(available_summary_profiles) or 'none'}"
        )

    central_summaries = None
    if selected_summary:
        central_summaries = {
            "profile": summary_profile,
            "available_profiles": available_summary_profiles,
            "language": selected_summary.get("language"),
            "detail": selected_summary.get("detail"),
            "x": attach_summary_text(filter_summary_items(selected_summary.get("x", []), domains)),
            "podcasts": attach_summary_text(filter_summary_items(selected_summary.get("podcasts", []), domains)),
            "papers": attach_summary_text(filter_summary_items(selected_summary.get("papers", []), domains)),
        }

    stats = {
        "podcast_episodes": len(episodes),
        "podcast_with_transcript": sum(1 for e in episodes if e.get("transcript")),
        "central_x_summaries": len((central_summaries or {}).get("x", [])),
        "central_podcast_summaries": len((central_summaries or {}).get("podcasts", [])),
        "central_paper_summaries": len((central_summaries or {}).get("papers", [])),
        "x_builders": len(x_accounts),
        "total_tweets": sum(len(a.get("tweets", [])) for a in x_accounts),
        "arxiv_papers": len(papers),
    }
    config_out = {
        "language": language,
        "language_raw": config.get("language", "en"),
        "granularity": config.get("granularity", "summary"),
        "include_central_summaries": include_central_summaries,
        "summary_profile": summary_profile,
        "available_summary_profiles": available_summary_profiles,
        "domains": domains,
        "delivery": config.get("delivery", {"method": "stdout"}),
    }
    output_contract = build_output_contract({**config, "language": language})

    output = {
        "status": "ok",
        "mode": "json_first",
        "generated_at": (feed_x or {}).get("generated_at") or (feed_podcasts or {}).get("generated_at"),
        "config": config_out,
        "output_contract": output_contract,
        "central_summaries": central_summaries,
        "podcasts": episodes,
        "x": x_accounts,
        "papers": papers,
        "stats": stats,
        "prompts": prompts,
        "errors": errors if errors else None,
    }

    # 6. Write payload files (full content) + print compact manifest (stdout)
    out_dir = Path(args.out)
    try:
        payload_path, slim_episodes, transcript_files = write_payload(out_dir, output, episodes)
    except Exception as e:
        errors.append(f"Payload write error: {e}")
        payload_path, slim_episodes, transcript_files = None, [], []

    mark_path = None
    if payload_path and not args.include_seen:
        try:
            mark_path = write_delivery_mark(out_dir, marks, output["generated_at"])
        except Exception as e:
            errors.append(f"Delivery mark write error: {e}")

    if payload_path and args.mark_seen and not args.include_seen:
        for kind, ids in marks.items():
            seen.setdefault(kind, {}).update(ids)
        save_seen(seen)

    if args.include_seen:
        seen_update = "off (--include-seen)"
    elif args.mark_seen:
        seen_update = "written (--mark-seen)"
    else:
        seen_update = "pending delivery confirmation"

    manifest = {
        "status": "ok" if payload_path else "error",
        "mode": "json_first",
        "generated_at": output["generated_at"],
        "payload_file": str(payload_path) if payload_path else None,
        "delivery_mark_file": str(mark_path) if mark_path else None,
        "config": config_out,
        "output_contract": output_contract,
        "stats": stats,
        "podcasts": [
            {
                "channel": ep.get("channel"),
                "title": ep.get("title"),
                "pub_date": ep.get("pub_date"),
                "link": ep.get("link"),
                "transcript_file": ep.get("transcript_file"),
                "transcript_chars": ep.get("transcript_chars", 0),
            }
            for ep in slim_episodes
        ],
        "x_accounts": [
            {"handle": a.get("handle"), "tweets": len(a.get("tweets", []))}
            for a in x_accounts if a.get("tweets")
        ],
        "papers_count": len(papers),
        "seen_filter": "off (--include-seen)" if args.include_seen else "on",
        "seen_update": seen_update,
        "errors": errors if errors else None,
    }
    sys.stdout.write(json.dumps(clean_data(manifest), ensure_ascii=True, indent=2))
    sys.stdout.write("\n")

if __name__ == "__main__":
    main()
