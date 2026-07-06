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
# Tried in order. raw.githubusercontent.com is blocked in some regions
# (notably mainland China), and cdn.jsdelivr.net itself is unreliable there
# since its mainland nodes were shut down. The extra jsDelivr endpoints serve
# the same repo content through different CDN networks (Fastly / Gcore /
# Cloudflare), so at least one is usually reachable without a proxy.
# Override with AI_SIGNAL_BASE_URLS="https://base1,https://base2" if needed.
MIRROR_BASES = [
    RAW_BASE,
    "https://cdn.jsdelivr.net/gh/Benboerba620/ai-signal@main",
    "https://fastly.jsdelivr.net/gh/Benboerba620/ai-signal@main",
    "https://gcore.jsdelivr.net/gh/Benboerba620/ai-signal@main",
    "https://testingcf.jsdelivr.net/gh/Benboerba620/ai-signal@main",
]
PROMPT_FILES = [
    "summarize-podcast.md",
    "summarize-tweets.md",
    "summarize-papers.md",
    "summarize-articles.md",
    "digest-intro.md",
    "translate.md",
]

USER_DIR = Path.home() / ".ai-signal"
CONFIG_PATH = USER_DIR / "config.json"
SEEN_PATH = USER_DIR / "seen.json"
DEFAULT_PAYLOAD_DIR = USER_DIR / "payload"
DEFAULT_DELIVERY_MARK = "delivery-mark.json"
SEEN_RETENTION_DAYS = 14
FEED_STALE_AFTER_HOURS = 30


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


def normalize_granularity(value):
    raw = str(value or "summary").strip().lower()
    aliases = {
        "highlights": "highlights",
        "highlight": "highlights",
        "short": "highlights",
        "brief": "highlights",
        "精华": "highlights",
        "简短": "highlights",
        "summary": "summary",
        "standard": "summary",
        "medium": "summary",
        "标准": "summary",
        "full": "full",
        "deep": "full",
        "detailed": "full",
        "完整": "full",
        "详细": "full",
    }
    return aliases.get(raw, "summary")


def build_output_contract(config):
    language = normalize_language(config.get("language", "en"))
    granularity = normalize_granularity(config.get("granularity", "summary"))

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
            "For official blog articles, keep source name, title, link, and a short summary of what was announced.",
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
    for key in ("tweets", "episodes", "papers", "articles"):
        seen.setdefault(key, {})
    return seen


def save_seen(seen):
    cutoff = (datetime.now(timezone.utc) - timedelta(days=SEEN_RETENTION_DAYS)).isoformat()
    for key in ("tweets", "episodes", "papers", "articles"):
        seen[key] = {k: v for k, v in seen.get(key, {}).items() if v > cutoff}
    SEEN_PATH.parent.mkdir(parents=True, exist_ok=True)
    SEEN_PATH.write_text(json.dumps(seen, indent=2), encoding="utf-8")


def episode_key(episode):
    return episode.get("guid") or episode.get("link") or episode.get("title") or ""


def filter_unseen(feed_x, feed_podcasts, papers, articles, seen):
    now = datetime.now(timezone.utc).isoformat()
    new_ids = {"tweets": [], "episodes": [], "papers": [], "articles": []}

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

    fresh_articles = []
    for article in articles:
        aid = article.get("id") or article.get("url") or ""
        if aid and aid in seen["articles"]:
            continue
        if aid:
            new_ids["articles"].append(aid)
        fresh_articles.append(article)

    marks = {kind: {i: now for i in ids} for kind, ids in new_ids.items()}
    return accounts, episodes, fresh_papers, fresh_articles, marks


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


# Fail fast on unreachable hosts (connect=5s) so a blocked mirror costs
# seconds, not a 30s hang per file.
HTTP_TIMEOUT = httpx.Timeout(20.0, connect=5.0)

# Once a base succeeds, later fetches try it first instead of re-walking
# the mirror list from a base that already failed once.
_preferred_base = None


def fetch_json(url):
    try:
        resp = httpx.get(url, timeout=HTTP_TIMEOUT, follow_redirects=True)
        resp.raise_for_status()
        text = resp.content.decode("utf-8", errors="replace")
        return clean_data(json.loads(clean_text(text)))
    except Exception:
        return None


def fetch_text(url):
    try:
        resp = httpx.get(url, timeout=HTTP_TIMEOUT, follow_redirects=True)
        resp.raise_for_status()
        return clean_text(resp.content.decode("utf-8", errors="replace"))
    except Exception:
        return None


def candidate_bases():
    env = os.environ.get("AI_SIGNAL_BASE_URLS")
    if env:
        bases = [b.strip().rstrip("/") for b in env.split(",") if b.strip()]
    else:
        bases = []
    if not bases:
        bases = list(MIRROR_BASES)
    if _preferred_base in bases:
        bases.remove(_preferred_base)
        bases.insert(0, _preferred_base)
    return bases


def fetch_json_any(path):
    """Fetch a repo-relative JSON file, trying each mirror base in order."""
    global _preferred_base
    for base in candidate_bases():
        url = f"{base}/{path}"
        data = fetch_json(url)
        if data is not None:
            _preferred_base = base
            return data, url
    return None, f"{candidate_bases()[0]}/{path}"


def fetch_text_any(path):
    """Fetch a repo-relative text file, trying each mirror base in order."""
    global _preferred_base
    for base in candidate_bases():
        text = fetch_text(f"{base}/{path}")
        if text is not None:
            _preferred_base = base
            return text
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


def feed_meta(filename, url, source, feed, reason=None):
    return {
        "source": source,
        "filename": filename,
        "url": url,
        "generated_at": (feed or {}).get("generated_at"),
        "reason": reason,
    }


def fetch_feed(filename, content_key=None):
    remote, url = fetch_json_any(f"feeds/{filename}")
    local = load_local_json(filename)
    if remote and (not content_key or remote.get(content_key)):
        return remote, feed_meta(filename, url, "remote", remote)
    if local:
        reason = "remote_unavailable"
        if remote and content_key and not remote.get(content_key):
            reason = f"remote_missing_{content_key}"
        return local, feed_meta(filename, url, "local_cache", local, reason)
    return remote, feed_meta(filename, url, "unavailable", remote, "remote_unavailable_no_local_cache")


def choose_summary_profile(config):
    explicit = config.get("summary_profile")
    if explicit:
        return explicit

    language = normalize_language(config.get("language", "en"))
    granularity = normalize_granularity(config.get("granularity", "summary"))

    if language == "zh":
        if granularity == "highlights":
            return "zh_short"
        if granularity == "full":
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


def parse_iso_datetime(value):
    if not value:
        return None
    try:
        parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
        if parsed.tzinfo is None:
            parsed = parsed.replace(tzinfo=timezone.utc)
        return parsed
    except ValueError:
        return None


def latest_feed_datetime(*feeds):
    dates = [parse_iso_datetime((feed or {}).get("generated_at")) for feed in feeds]
    dates = [date for date in dates if date is not None]
    return max(dates) if dates else None


def summaries_are_stale(feed_summaries, *feeds):
    summary_dt = parse_iso_datetime((feed_summaries or {}).get("generated_at"))
    raw_dt = latest_feed_datetime(*feeds)
    if not feed_summaries or not raw_dt:
        return False
    if not summary_dt:
        return True
    return summary_dt < raw_dt


def feed_age_hours(feed):
    generated = parse_iso_datetime((feed or {}).get("generated_at"))
    if not generated:
        return None
    return (datetime.now(timezone.utc) - generated).total_seconds() / 3600


def annotate_feed_sources(feed_sources, feeds):
    warnings = []
    annotated = {}
    for key, meta in feed_sources.items():
        feed = feeds.get(key)
        item = dict(meta)
        age = feed_age_hours(feed)
        if age is not None:
            item["age_hours"] = round(age, 2)
            item["is_stale"] = age > FEED_STALE_AFTER_HOURS
        else:
            item["age_hours"] = None
            item["is_stale"] = bool(feed)
        if item["source"] == "local_cache":
            warnings.append(
                f"{key} feed used local cache because {item.get('reason')}; data may not be latest"
            )
        elif item["source"] == "unavailable":
            warnings.append(f"{key} feed unavailable; no remote or local cache data")
        if item["is_stale"]:
            warnings.append(
                f"{key} feed generated_at is older than {FEED_STALE_AFTER_HOURS} hours"
            )
        annotated[key] = item
    return annotated, warnings


def filter_summary_items(items, domains):
    if not domains:
        return items
    return [item for item in items if item.get("domain", "ai") in domains]


def current_content_ids(x_accounts, episodes, papers):
    tweet_ids = {
        str(tweet.get("id"))
        for account in x_accounts
        for tweet in account.get("tweets", [])
        if tweet.get("id")
    }
    episode_keys = set()
    episode_urls = set()
    episode_titles = set()
    for episode in episodes:
        key = episode_key(episode)
        if key:
            episode_keys.add(key)
        if episode.get("guid"):
            episode_keys.add(str(episode["guid"]))
        if episode.get("link"):
            episode_urls.add(str(episode["link"]))
        if episode.get("title"):
            episode_titles.add(str(episode["title"]))
    paper_ids = {
        str(paper.get("arxiv_id"))
        for paper in papers
        if paper.get("arxiv_id")
    }
    return {
        "tweets": tweet_ids,
        "episode_keys": episode_keys,
        "episode_urls": episode_urls,
        "episode_titles": episode_titles,
        "papers": paper_ids,
    }


def filter_summary_items_for_current(items, kind, ids):
    if kind == "x":
        return [
            item for item in items
            if str(item.get("tweet_id") or item.get("id") or "") in ids["tweets"]
        ]
    if kind == "podcasts":
        return [
            item for item in items
            if (
                str(item.get("id") or "") in ids["episode_keys"]
                or str(item.get("source_url") or "") in ids["episode_urls"]
                or str(item.get("title") or "") in ids["episode_titles"]
            )
        ]
    if kind == "papers":
        return [
            item for item in items
            if str(item.get("arxiv_id") or item.get("id") or "") in ids["papers"]
        ]
    return items


def attach_summary_text(items):
    results = []
    for item in items:
        summary_path = item.get("summary_path")
        enriched = dict(item)
        if summary_path:
            text = fetch_text_any(summary_path) or load_local_text(summary_path)
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
    warnings = []

    # 1. User config
    config = {"language": "en", "granularity": "summary", "delivery": {"method": "stdout"}}
    if CONFIG_PATH.exists():
        try:
            config = json.loads(CONFIG_PATH.read_text("utf-8-sig"))
        except Exception as e:
            errors.append(f"Config read error: {e}")

    # 2. Fetch feeds
    feed_x, x_source = fetch_feed("feed-x.json", "x")
    feed_podcasts, podcast_source = fetch_feed("feed-podcasts.json", "podcasts")
    feed_arxiv, arxiv_source = fetch_feed("feed-arxiv.json", "papers")
    feed_blogs, blogs_source = fetch_feed("feed-blogs.json", "articles")
    include_central_summaries = wants_central_summaries(config)
    if include_central_summaries:
        feed_summaries, summaries_source = fetch_feed("feed-summaries.json", "profiles")
    else:
        feed_summaries = None
        summaries_source = feed_meta("feed-summaries.json", f"{RAW_BASE}/feeds/feed-summaries.json", "disabled", None)
    feed_sources, source_warnings = annotate_feed_sources(
        {
            "x": x_source,
            "podcasts": podcast_source,
            "arxiv": arxiv_source,
            "blogs": blogs_source,
            "summaries": summaries_source,
        },
        {
            "x": feed_x,
            "podcasts": feed_podcasts,
            "arxiv": feed_arxiv,
            "blogs": feed_blogs,
            "summaries": feed_summaries,
        },
    )
    warnings.extend(source_warnings)
    if feed_summaries and summaries_are_stale(feed_summaries, feed_x, feed_podcasts, feed_arxiv):
        warnings.append(
            "Central summaries are older than raw feeds; ignoring feed-summaries.json for this run"
        )
        feed_summaries = None
        feed_sources["summaries"]["ignored"] = True
        feed_sources["summaries"]["ignore_reason"] = "older_than_raw_feeds"
    if not feed_x:
        errors.append("Could not fetch tweet feed")
    if not feed_podcasts:
        errors.append("Could not fetch podcast feed")
    if not feed_arxiv:
        errors.append("Could not fetch arXiv feed")
    if not feed_blogs:
        # Newer feed: older central snapshots/mirror caches may not have it yet
        warnings.append("Could not fetch official blog feed; skipping blog articles this run")

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
        remote = fetch_text_any(f"prompts/{filename}")
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
        articles = (feed_blogs or {}).get("articles", [])
        marks = {"tweets": {}, "episodes": {}, "papers": {}, "articles": {}}
    else:
        x_accounts, episodes, papers, articles, marks = filter_unseen(
            feed_x,
            feed_podcasts,
            (feed_arxiv or {}).get("papers", []),
            (feed_blogs or {}).get("articles", []),
            seen,
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
        ids = current_content_ids(x_accounts, episodes, papers)
        summary_x = filter_summary_items_for_current(
            filter_summary_items(selected_summary.get("x", []), domains),
            "x",
            ids,
        )
        summary_podcasts = filter_summary_items_for_current(
            filter_summary_items(selected_summary.get("podcasts", []), domains),
            "podcasts",
            ids,
        )
        summary_papers = filter_summary_items_for_current(
            filter_summary_items(selected_summary.get("papers", []), domains),
            "papers",
            ids,
        )
        central_summaries = {
            "profile": summary_profile,
            "available_profiles": available_summary_profiles,
            "language": selected_summary.get("language"),
            "detail": selected_summary.get("detail"),
            "x": attach_summary_text(summary_x),
            "podcasts": attach_summary_text(summary_podcasts),
            "papers": attach_summary_text(summary_papers),
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
        "blog_articles": len(articles),
    }
    config_out = {
        "language": language,
        "language_raw": config.get("language", "en"),
        "granularity": normalize_granularity(config.get("granularity", "summary")),
        "granularity_raw": config.get("granularity", "summary"),
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
        "feed_sources": feed_sources,
        "central_summaries": central_summaries,
        "podcasts": episodes,
        "x": x_accounts,
        "papers": papers,
        "articles": articles,
        "stats": stats,
        "prompts": prompts,
        "warnings": warnings if warnings else None,
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
        "feed_sources": feed_sources,
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
        "articles": [
            {
                "source": a.get("source_name") or a.get("source"),
                "title": a.get("title"),
                "published": a.get("published"),
                "url": a.get("url"),
            }
            for a in articles
        ],
        "seen_filter": "off (--include-seen)" if args.include_seen else "on",
        "seen_update": seen_update,
        "warnings": warnings if warnings else None,
        "errors": errors if errors else None,
    }
    sys.stdout.write(json.dumps(clean_data(manifest), ensure_ascii=True, indent=2))
    sys.stdout.write("\n")

if __name__ == "__main__":
    main()
