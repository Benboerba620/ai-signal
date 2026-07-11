"""On-demand transcript fetch for the "expand this podcast" flow.

The daily digest ships a slim payload (metadata only). Full transcripts are
kept as individual central sidecars for a limited retention window and are not
downloaded during daily digest preparation.

Instead of caching every transcript, this resolves the episode in the slim
central feed, then downloads only its transcript sidecar. Legacy inline feeds
remain supported during migration.

Usage:
    python scripts/fetch_transcript.py --guid <guid>
    python scripts/fetch_transcript.py --title "<title substring>"
    python scripts/fetch_transcript.py --link  "<episode url>"
    # add --out FILE to write to a file instead of stdout

Exit codes: 0 found, 2 found-but-no-transcript, 3 not-found, 4 feed-unreachable.
"""

import argparse
import sys
from pathlib import Path

SCRIPT_DIR = Path(__file__).parent
sys.path.insert(0, str(SCRIPT_DIR))

from prepare_digest import (  # noqa: E402
    clean_text,
    configure_stdio,
    fetch_feed,
    fetch_text_any,
    load_local_text,
)


def log(msg):
    sys.stderr.write(msg + "\n")


def norm(s):
    return (s or "").strip().lower()


def match_episode(episodes, guid=None, title=None, link=None):
    """guid is exact; title/link match case-insensitively (substring either way)."""
    if guid:
        for ep in episodes:
            if norm(ep.get("guid")) == norm(guid):
                return ep
    if link:
        for ep in episodes:
            if norm(ep.get("link")) == norm(link):
                return ep
    if title:
        t = norm(title)
        # exact-ish first, then loose substring so a partial title still resolves
        for ep in episodes:
            if norm(ep.get("title")) == t:
                return ep
        for ep in episodes:
            et = norm(ep.get("title"))
            if et and (t in et or et in t):
                return ep
    return None


def transcript_for_episode(episode):
    inline = episode.get("transcript")
    if inline:
        return clean_text(inline)
    path = episode.get("transcript_path")
    if not path:
        return ""
    return clean_text(fetch_text_any(path) or load_local_text(path) or "")


def main():
    configure_stdio()
    ap = argparse.ArgumentParser(description="Fetch one podcast transcript from the central feed.")
    ap.add_argument("--guid", help="episode guid (from payload.json, most reliable)")
    ap.add_argument("--title", help="episode title or a substring of it")
    ap.add_argument("--link", help="episode link/url")
    ap.add_argument("--out", help="write transcript here instead of stdout")
    args = ap.parse_args()

    if not (args.guid or args.title or args.link):
        ap.error("give at least one of --guid / --title / --link")

    feed, meta = fetch_feed("feed-podcasts.json", "podcasts")
    episodes = (feed or {}).get("podcasts", [])
    if feed:
        log(f"↪ feed source={meta.get('source')} generated_at={meta.get('generated_at')} "
            f"({len(episodes)} episodes)")

    ep = match_episode(episodes, args.guid, args.title, args.link)
    index = None
    if not ep:
        index, index_meta = fetch_feed("feed-transcripts-index.json", "transcripts")
        indexed_episodes = (index or {}).get("transcripts", [])
        ep = match_episode(indexed_episodes, args.guid, args.title, args.link)
        if ep:
            log(
                f"↪ transcript index source={index_meta.get('source')} "
                f"generated_at={index_meta.get('generated_at')} "
                f"expires_at={ep.get('expires_at')}"
            )
    if not ep:
        if not feed and not index:
            log("✗ central feed and transcript index are unreachable")
            return 4
        log("✗ no episode matched in the current feed or retained transcript index.")
        log("  Available current-feed titles:")
        for e in episodes:
            log(f"    [{e.get('guid')}] {e.get('channel')} | {e.get('title')}")
        return 3

    title = ep.get("title")
    transcript = transcript_for_episode(ep)
    if not transcript:
        why = ep.get("transcript_error") or (
            "this channel is not configured for transcript capture and the RSS "
            "carried none")
        log(f"✗ 「{title}」has no central transcript — {why}")
        log("  (it was pushed on metadata only; there is nothing to expand)")
        return 2

    text = clean_text(transcript)
    if args.out:
        Path(args.out).write_text(text, encoding="utf-8")
        log(f"✓ 「{title}」— {len(text)} chars → {args.out} "
            f"(source={ep.get('transcript_source')})")
    else:
        log(f"✓ 「{title}」— {len(text)} chars (source={ep.get('transcript_source')})")
        sys.stdout.write(text)
        sys.stdout.write("\n")
    return 0


if __name__ == "__main__":
    sys.exit(main())
