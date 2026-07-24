"""Transcribe selected podcast episodes that do not have public transcripts.

This is an optional central maintenance step. It reads feeds/feed-podcasts.json,
selects episodes whose source policy allows transcription, submits their public
audio URLs to Volcengine AUC ASR, polls for completion, then writes transcript
text back into the feed.

Usage:
    python scripts/transcribe_missing_podcasts.py --limit 2
    python scripts/transcribe_missing_podcasts.py --dry-run

Env:
    VOLC_ASR_API_KEY - required unless --dry-run is used
"""

import argparse
import json
import os
import re
import subprocess
import sys
import time
import uuid
from pathlib import Path
from urllib.parse import urlparse

import httpx

import generate_feed
from podcast_transcripts import externalize_transcripts

SCRIPT_DIR = Path(__file__).parent
ROOT_DIR = SCRIPT_DIR.parent
FEED_PATH = ROOT_DIR / "feeds" / "feed-podcasts.json"
SOURCES_PATH = ROOT_DIR / "config" / "sources.json"

SUBMIT_URL = "https://openspeech.bytedance.com/api/v3/auc/bigmodel/submit"
QUERY_URL = "https://openspeech.bytedance.com/api/v3/auc/bigmodel/query"
UA = "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36"
RESOURCE_ID = "volc.seedasr.auc"
DONE = "20000000"
RUNNING = {"20000001", "20000002"}
SILENT = "20000003"


def configure_stdio():
    for stream in (sys.stdout, sys.stderr):
        if hasattr(stream, "reconfigure"):
            stream.reconfigure(encoding="utf-8", errors="replace")


def log(message):
    print(message, file=sys.stderr)


def load_json(path, default):
    if not path.exists():
        return default
    return json.loads(path.read_text("utf-8"))


def write_json(path, data):
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")


def channel_policy_map(sources):
    return {
        item.get("name"): item
        for item in sources.get("podcasts", {}).get("channels", [])
        if item.get("name")
    }


def audio_format(url):
    path = urlparse(url or "").path.lower()
    for ext in ("mp3", "m4a", "mp4", "wav", "aac", "ogg", "opus", "flac"):
        if path.endswith(f".{ext}"):
            return "ogg" if ext == "opus" else ext
    return "mp3"


def is_youtube_url(url):
    host = urlparse(url or "").netloc.lower()
    return "youtube.com" in host or "youtu.be" in host


def resolve_redirects(url):
    """Podcast enclosure URLs sit behind tracking redirects (chtbl/megaphone);
    Volc's downloader chokes on them, so hand it the final signed URL."""
    try:
        with httpx.Client(timeout=30, follow_redirects=True, headers={"User-Agent": UA}) as client:
            resp = client.head(url)
            if resp.status_code >= 400:
                resp = client.get(url, headers={"User-Agent": UA, "Range": "bytes=0-0"})
            return str(resp.url)
    except Exception as exc:
        log(f"  redirect resolution failed, using original URL: {exc}")
        return url


def resolve_audio_url(item):
    if item.get("audio_url"):
        return resolve_redirects(item["audio_url"])
    link = item.get("link") or ""
    if not is_youtube_url(link):
        return ""
    try:
        proc = subprocess.run(
            [
                "yt-dlp",
                "--no-playlist",
                "-f",
                "bestaudio/best",
                "-g",
                link,
            ],
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=60,
            check=True,
        )
    except Exception as exc:
        log(f"  yt-dlp audio URL resolution failed for {link}: {exc}")
        return ""
    urls = [line.strip() for line in proc.stdout.splitlines() if line.strip().startswith("http")]
    return urls[0] if urls else ""


SPONSOR_MARKERS = (
    "thanks to our partners",
    "brought to you by",
    "this episode is sponsored",
    "sponsors:",
    "our sponsors",
)


def strip_sponsor_tail(text):
    lower = text.lower()
    cut = min((lower.find(m) for m in SPONSOR_MARKERS if m in lower), default=-1)
    return text[:cut] if cut >= 0 else text


def text_blob(item):
    # sponsor reads mention "AI"/"agent" constantly; match episode content only
    description = strip_sponsor_tail(str(item.get("description") or ""))
    return " ".join([
        str(item.get("title") or ""),
        description,
        str(item.get("channel") or ""),
    ]).lower()


def is_relevant(item, keywords):
    blob = text_blob(item)
    for keyword in keywords:
        k = keyword.lower().strip()
        if not k:
            continue
        # word-boundary match: bare substring would let "ai" hit "email"/"again"
        if re.search(rf"(?<![a-z0-9]){re.escape(k)}(?![a-z0-9])", blob):
            return True
    return False


def duration_seconds(value):
    text = str(value or "").strip()
    if not text:
        return 0
    if text.isdigit():
        return int(text)

    parts = text.split(":")
    if len(parts) not in (2, 3) or not all(part.isdigit() for part in parts):
        return 0
    numbers = [int(part) for part in parts]
    if len(numbers) == 2:
        minutes, seconds = numbers
        return minutes * 60 + seconds
    hours, minutes, seconds = numbers
    return hours * 3600 + minutes * 60 + seconds


def asr_eligibility(item, policy):
    if policy.get("require_direct_audio") and not item.get("audio_url"):
        return False, "missing direct audio_url"
    if not item.get("audio_url") and not is_youtube_url(item.get("link")):
        return False, "missing audio_url"

    minimum_minutes = int(policy.get("min_transcription_duration_minutes", 0) or 0)
    item_duration = duration_seconds(item.get("duration"))
    if minimum_minutes and item_duration and item_duration < minimum_minutes * 60:
        return False, f"too short ({item_duration}s < {minimum_minutes}m)"

    minimum_bytes = int(policy.get("min_transcription_audio_bytes", 0) or 0)
    item_bytes = int(item.get("audio_bytes", 0) or 0)
    if minimum_bytes and item_bytes and item_bytes < minimum_bytes:
        return False, f"audio too small ({item_bytes} bytes < {minimum_bytes})"
    return True, "eligible for ASR"


def should_transcribe(item, policy, keywords, force=False):
    if item.get("transcript") or (
        item.get("transcript_available") and item.get("transcript_path")
    ):
        return False, "already has transcript"
    if force:
        return True, "manual channel override"

    eligible, reason = asr_eligibility(item, policy)
    if not eligible:
        return False, reason

    mode = policy.get("transcribe_missing", False)
    if mode is True:
        return True, "channel default"
    if isinstance(mode, str) and mode.lower() == "relevant":
        if is_relevant(item, keywords):
            return True, "keyword relevant"
        return False, "not relevant enough"
    return False, "channel not enabled"


def canonical_episode_title(value):
    title = str(value or "").split(" | ", 1)[0]
    return re.sub(r"[^a-z0-9]+", " ", title.casefold()).strip()


def fetch_configured_public_transcript(client, item, policy):
    rss_url = str(policy.get("transcript_rss_url") or "").strip()
    if not rss_url:
        return None

    try:
        response = client.get(rss_url, headers={"User-Agent": UA})
        response.raise_for_status()
        episodes = generate_feed.parse_rss(response.text)
    except Exception as exc:
        log(f"  public transcript feed failed: {exc}")
        return None

    target = canonical_episode_title(item.get("title"))
    match = next(
        (episode for episode in episodes
         if canonical_episode_title(episode.get("title")) == target),
        None,
    )
    if not match:
        log("  no matching episode in configured public transcript feed")
        return None

    result = generate_feed.get_youtube_transcript(match.get("link"))
    if not result.get("text"):
        log(f"  matching public transcript unavailable: {result.get('error')}")
        return None
    result["url"] = match.get("link")
    return result


def headers(api_key, request_id, sequence="-1"):
    result = {
        "Content-Type": "application/json",
        "X-Api-Key": api_key,
        "X-Api-Resource-Id": RESOURCE_ID,
        "X-Api-Request-Id": request_id,
    }
    # Only the submit call takes X-Api-Sequence; sending it on query breaks the API
    if sequence is not None:
        result["X-Api-Sequence"] = sequence
    return result


def status_code(resp):
    return (
        resp.headers.get("X-Api-Status-Code")
        or resp.headers.get("x-api-status-code")
        or ""
    )


def status_message(resp):
    return (
        resp.headers.get("X-Api-Message")
        or resp.headers.get("x-api-message")
        or ""
    )


def extract_text(value):
    if isinstance(value, str):
        return value.strip()
    if isinstance(value, list):
        parts = [extract_text(item) for item in value]
        return "\n".join(part for part in parts if part)
    if isinstance(value, dict):
        for key in ("text", "transcript"):
            if key in value:
                found = extract_text(value[key])
                if found:
                    return found
        for key in ("result", "data", "utterances", "sentences"):
            if key in value:
                found = extract_text(value[key])
                if found:
                    return found
        parts = []
        for item in value.values():
            found = extract_text(item)
            if found:
                parts.append(found)
        return "\n".join(parts)
    return ""


def submit_task(client, api_key, item):
    request_id = str(uuid.uuid4())
    audio_url = resolve_audio_url(item)
    if not audio_url:
        raise RuntimeError("No usable audio URL")
    payload = {
        "user": {"uid": "ai-signal"},
        "audio": {
            "url": audio_url,
            "format": audio_format(audio_url),
        },
        "request": {
            "model_name": "bigmodel",
            "enable_itn": True,
            "enable_punc": True,
            "enable_ddc": False,
            "enable_speaker_info": False,
            "enable_channel_split": False,
            "show_utterances": False,
            "vad_segment": False,
            "sensitive_words_filter": "",
        },
    }
    resp = client.post(SUBMIT_URL, headers=headers(api_key, request_id), json=payload)
    resp.raise_for_status()
    code = status_code(resp)
    if code != DONE:
        raise RuntimeError(
            f"submit failed: status={code or '(missing)'}, "
            f"message={status_message(resp) or '(none)'}, body={resp.text[:300]}"
        )
    return request_id


def query_task(client, api_key, request_id, poll_interval, max_wait):
    deadline = time.monotonic() + max_wait
    last_body = ""
    while time.monotonic() < deadline:
        resp = client.post(QUERY_URL, headers=headers(api_key, request_id, sequence=None), json={})
        resp.raise_for_status()
        code = status_code(resp)
        last_body = resp.text
        if code == DONE:
            try:
                payload = resp.json()
            except Exception as exc:
                raise RuntimeError(f"query finished but JSON parse failed: {exc}") from exc
            text = extract_text(payload)
            if not text:
                raise RuntimeError(f"query finished but transcript text was empty: {last_body[:500]}")
            return text
        if code == SILENT:
            raise RuntimeError("audio recognized as silent, no transcript")
        if code in RUNNING or not code:
            time.sleep(poll_interval)
            continue
        raise RuntimeError(
            f"query failed: status={code}, message={status_message(resp) or '(none)'}, "
            f"body={last_body[:300]}"
        )
    raise TimeoutError(f"transcription timed out after {max_wait}s; last body: {last_body[:500]}")


def candidate_items(feed, sources, force_channels=None, only_channels=None):
    podcast_cfg = sources.get("podcasts", {})
    policies = channel_policy_map(sources)
    keywords = podcast_cfg.get("transcription", {}).get("relevance_keywords", [])
    force_channels = set(force_channels or [])
    only_channels = set(only_channels or [])

    candidates = []
    skipped = []
    for index, item in enumerate(feed.get("podcasts", [])):
        channel = item.get("channel")
        if only_channels and channel not in only_channels:
            skipped.append((item, "channel not selected"))
            continue
        policy = policies.get(channel, {})
        ok, reason = should_transcribe(
            item,
            policy,
            keywords,
            force=channel in force_channels,
        )
        if ok:
            candidates.append((index, item, reason))
        else:
            skipped.append((item, reason))
    return candidates, skipped


def main():
    configure_stdio()
    parser = argparse.ArgumentParser()
    parser.add_argument("--limit", type=int, default=None, help="Maximum episodes to transcribe")
    parser.add_argument("--dry-run", action="store_true", help="Print candidates without calling ASR")
    parser.add_argument("--poll-interval", type=int, default=None)
    parser.add_argument("--max-wait", type=int, default=None)
    parser.add_argument("--force-channel", action="append", default=[])
    parser.add_argument("--only-channel", action="append", default=[])
    args = parser.parse_args()

    feed = load_json(FEED_PATH, {"podcasts": []})
    sources = load_json(SOURCES_PATH, {})
    transcribe_cfg = sources.get("podcasts", {}).get("transcription", {})
    limit = args.limit if args.limit is not None else int(transcribe_cfg.get("default_limit", 2))
    poll_interval = args.poll_interval or int(transcribe_cfg.get("poll_interval_seconds", 10))
    max_wait = args.max_wait or int(transcribe_cfg.get("max_wait_seconds", 1800))

    candidates, skipped = candidate_items(
        feed,
        sources,
        force_channels=args.force_channel,
        only_channels=args.only_channel,
    )
    log(f"Candidates: {len(candidates)}; skipped: {len(skipped)}; limit: {limit}")
    for _, item, reason in candidates[:limit]:
        log(f"  ✅ {item.get('channel')} | {item.get('title')} ({reason})")

    if args.dry_run:
        return

    api_key = os.environ.get("VOLC_ASR_API_KEY")
    changed = 0
    policies = channel_policy_map(sources)
    with httpx.Client(timeout=60, follow_redirects=True) as client:
        for index, item, reason in candidates[:limit]:
            log(f"Submitting: {item.get('channel')} | {item.get('title')} ({reason})")
            policy = policies.get(item.get("channel"), {})
            public_result = fetch_configured_public_transcript(client, item, policy)
            if public_result:
                feed["podcasts"][index]["transcript"] = public_result["text"]
                feed["podcasts"][index]["transcript_available"] = True
                feed["podcasts"][index]["transcript_source"] = public_result["source"]
                feed["podcasts"][index]["transcript_url"] = public_result.get("url")
                feed["podcasts"][index]["transcript_video_id"] = public_result.get("video_id")
                feed["podcasts"][index]["transcript_error"] = None
                changed += 1
                log(f"  ✅ transcript ({len(public_result['text'])} chars, {public_result['source']})")
                continue

            asr_ok, asr_reason = asr_eligibility(item, policy)
            if not asr_ok:
                log(f"  ⏭️ ASR skipped: {asr_reason}")
                continue
            if not api_key:
                log("  ⏭️ VOLC_ASR_API_KEY is not set; ASR fallback skipped")
                continue
            try:
                request_id = submit_task(client, api_key, item)
                log(f"  request_id={request_id}")
                text = query_task(client, api_key, request_id, poll_interval, max_wait)
            except Exception as exc:
                feed["podcasts"][index]["transcript_error"] = f"volc_asr_auc: {exc}"
                log(f"  ❌ {exc}")
                continue

            feed["podcasts"][index]["transcript"] = text
            feed["podcasts"][index]["transcript_available"] = True
            feed["podcasts"][index]["transcript_source"] = "volc_asr_auc"
            feed["podcasts"][index]["transcript_url"] = None
            feed["podcasts"][index]["transcript_video_id"] = None
            feed["podcasts"][index]["transcript_error"] = None
            feed["podcasts"][index]["transcript_request_id"] = request_id
            changed += 1
            log(f"  ✅ transcript ({len(text)} chars)")

    if changed:
        externalize_transcripts(feed)
        write_json(FEED_PATH, feed)
    log(f"Done. transcripts_added={changed}")


if __name__ == "__main__":
    main()
