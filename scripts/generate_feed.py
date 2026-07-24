"""Central feed generator — fetches raw content from Twitter + podcasts + arXiv.

Runs on GitHub Actions daily. Outputs raw content (no LLM summarization).
Subscribers pull the feed JSON and use their own LLM to generate digests.

Feeds are stateless rolling-window snapshots: every run publishes ALL content
inside each source's lookback window, so extra manual runs never eat content.
Per-user "already seen" dedup happens client-side in prepare_digest.py.

Usage:
    python scripts/generate_feed.py [--twitter-only | --podcasts-only | --arxiv-only | --people-only]

--people-only refreshes just the person-appearance searches (config
podcasts.people) and keeps existing channel episodes in feed-podcasts.json.

Env vars:
    TWITTER_COOKIES — browser cookie string for twscrape auth
"""

import asyncio
import argparse
import json
import os
import re
import sys
import time
import xml.etree.ElementTree as ET
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timedelta, timezone
from email.utils import parsedate_to_datetime
from html import unescape
from html.parser import HTMLParser
from pathlib import Path
from urllib.parse import quote_plus, urljoin, urlparse

import httpx

from podcast_transcripts import externalize_transcripts, hydrate_transcripts

SCRIPT_DIR = Path(__file__).parent
ROOT_DIR = SCRIPT_DIR.parent
FEEDS_DIR = ROOT_DIR / "feeds"

UA = "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36"
MIN_TRANSCRIPT_CHARS = 600
MAX_TRANSCRIPT_CHARS = int(os.environ.get("MAX_TRANSCRIPT_CHARS", "500000"))
MIN_TRANSCRIPT_CHARS_PER_MIN = int(os.environ.get("MIN_TRANSCRIPT_CHARS_PER_MIN", "150"))

DEFAULT_TWEET_CORE_KEYWORDS = [
    "ai", "artificial intelligence", "agi", "agent", "agents", "agentic",
    "llm", "llms", "language model", "foundation model", "models", "world model",
    "claude", "openai", "anthropic", "deepmind", "gemini", "gpt", "llama",
    "fable", "opus", "sonnet", "haiku",
    "inference", "training", "fine-tuning", "eval", "benchmark", "reasoning",
    "token", "tokens", "context window", "prompt", "rag", "embedding",
    "gpu", "h100", "h200", "b200", "gb200", "nvidia", "cuda", "chip",
    "semiconductor", "datacenter", "data center", "compute", "cluster",
    "robot", "robotics", "automation",
    "cursor", "copilot", "codegen", "code generation", "ai engineer",
    "aie", "aidotengineer", "claude code", "claude tag", "computer use",
    "cli", "clis",
    "mcp", "tool use", "video generation",
    "research", "paper", "arxiv", "math", "alignment", "safety",
]

DEFAULT_TWEET_CONTEXT_KEYWORDS = [
    "developer tool", "developer tools", "devtools", "sdk", "api",
    "dockerfile", "docker", "sandbox", "microvm", "microvms", "fuse",
    "deploy", "deployment", "rollback", "serverless", "full stack",
    "workflow", "productivity", "artifact", "artifacts",
]

DEFAULT_TWEET_PLATFORM_KEYWORDS = [
    "vercel", "replit", "cursor", "copilot", "next.js", "react",
]

DEFAULT_TWEET_EXCLUDE_KEYWORDS = [
    "independence day", "july 4", "4th of july", "fourth of july",
    "🇺🇸", "🦅", "freedom 250", "holiday", "happy birthday",
    "merry christmas", "happy new year", "thanksgiving", "halloween",
    "baby", "dinner", "vacation", "wedding",
]


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


def load_feed(filename):
    path = FEEDS_DIR / filename
    if not path.exists():
        return None
    try:
        return json.loads(path.read_text("utf-8"))
    except Exception:
        return None


def write_json(path, data):
    path.write_text(json.dumps(clean_data(data), ensure_ascii=False, indent=2), encoding="utf-8")


def load_sources():
    with open(ROOT_DIR / "config" / "sources.json", "r", encoding="utf-8") as f:
        return json.load(f)


def log(msg):
    print(msg, file=sys.stderr)


class TextHTMLParser(HTMLParser):
    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.parts = []
        self.skip_depth = 0

    def handle_starttag(self, tag, attrs):
        if tag in {"script", "style", "noscript", "svg"}:
            self.skip_depth += 1
            return
        if self.skip_depth:
            return
        if tag in {"p", "div", "section", "article", "br", "li", "h1", "h2", "h3", "tr"}:
            self.parts.append("\n")

    def handle_endtag(self, tag):
        if tag in {"script", "style", "noscript", "svg"} and self.skip_depth:
            self.skip_depth -= 1
            return
        if self.skip_depth:
            return
        if tag in {"p", "div", "section", "article", "li", "h1", "h2", "h3", "tr"}:
            self.parts.append("\n")

    def handle_data(self, data):
        if not self.skip_depth and data.strip():
            self.parts.append(data)

    def text(self):
        text = unescape(" ".join(self.parts))
        text = re.sub(r"[ \t\r\f\v]+", " ", text)
        text = re.sub(r"\n\s+", "\n", text)
        text = re.sub(r"\n{3,}", "\n\n", text)
        return clean_text(text).strip()


class YouTubePublishedDateParser(HTMLParser):
    """Read YouTube's structured publish date without scraping visible text."""

    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.values = []

    def handle_starttag(self, tag, attrs):
        if tag != "meta":
            return
        attrs = dict(attrs)
        if attrs.get("itemprop") in {"datePublished", "uploadDate"} and attrs.get("content"):
            self.values.append(attrs["content"])


def html_to_text(html):
    parser = TextHTMLParser()
    try:
        parser.feed(html or "")
    except Exception:
        return clean_text(re.sub(r"<[^>]+>", " ", html or "")).strip()
    return parser.text()


def strip_html_fragment(value):
    return html_to_text(value or "")


def normalize_text(value):
    value = unescape(value or "")
    value = clean_text(value)
    value = re.sub(r"\s+", " ", value).strip()
    return value


def keyword_match(text, keywords):
    lower = normalize_text(text).lower()
    for keyword in keywords:
        keyword = keyword.lower().strip()
        if not keyword:
            continue
        pattern = rf"(?<![a-z0-9]){re.escape(keyword)}(?![a-z0-9])"
        if re.search(pattern, lower):
            return True
    return False


def is_relevant_tweet(text, twitter_cfg):
    """Keep AI/devtools/investing signal, drop pure social or holiday posts."""
    text = normalize_text(text)
    if not text:
        return False

    exclude_keywords = twitter_cfg.get("exclude_keywords") or DEFAULT_TWEET_EXCLUDE_KEYWORDS
    if keyword_match(text, exclude_keywords):
        return False

    custom_keywords = twitter_cfg.get("relevance_keywords")
    if custom_keywords:
        return keyword_match(text, custom_keywords)

    if keyword_match(text, DEFAULT_TWEET_CORE_KEYWORDS):
        return True

    has_platform = keyword_match(text, DEFAULT_TWEET_PLATFORM_KEYWORDS)
    has_context = keyword_match(text, DEFAULT_TWEET_CONTEXT_KEYWORDS)
    return has_platform and has_context


def tweet_author_handle(tweet):
    """Return the canonical author handle for a twscrape Tweet when available.

    X search can return the original Tweet object for a repost even for a
    ``from:<handle>`` query. In that case ``rawContent`` no longer starts with
    ``RT @``, so author identity (or the canonical URL) is the reliable guard.
    """
    user = getattr(tweet, "user", None)
    for attr in ("username", "screenName"):
        value = getattr(user, attr, None) if user is not None else None
        if value:
            return str(value).lstrip("@")

    url = str(getattr(tweet, "url", "") or "")
    try:
        parsed = urlparse(url)
        parts = [part for part in parsed.path.split("/") if part]
        if parsed.netloc.lower() in {"x.com", "www.x.com", "twitter.com", "www.twitter.com"}:
            if len(parts) >= 3 and parts[1].lower() == "status":
                return parts[0].lstrip("@")
    except Exception:
        pass
    return ""


def tweet_engagement_score(tweet):
    return (
        int(getattr(tweet, "likeCount", 0) or 0)
        + int(getattr(tweet, "retweetCount", 0) or 0) * 2
        + int(getattr(tweet, "replyCount", 0) or 0)
    )


def is_reply_tweet(tweet):
    if getattr(tweet, "inReplyToTweetId", None):
        return True
    return str(getattr(tweet, "rawContent", "") or "").lstrip().startswith("@")


def fetch_text_url(url, timeout=30):
    resp = httpx.get(url, headers={"User-Agent": UA}, timeout=timeout, follow_redirects=True)
    resp.raise_for_status()
    return resp.text, str(resp.url), resp.headers.get("content-type", "")


def rss_url_candidates(channel):
    urls = []
    for key in ("rss_url",):
        if channel.get(key):
            urls.append(channel[key])
    for url in channel.get("fallback_rss_urls", []):
        if url:
            urls.append(url)
    return list(dict.fromkeys(urls))


def fetch_rss_with_fallback(channel, attempts=3):
    errors = []
    for url in rss_url_candidates(channel):
        for attempt in range(1, attempts + 1):
            try:
                resp = httpx.get(url, headers={"User-Agent": UA}, timeout=45, follow_redirects=True)
                resp.raise_for_status()
                return resp.text, str(resp.url), None
            except Exception as e:
                errors.append(f"{url} attempt {attempt}/{attempts}: {e}")
                if attempt < attempts:
                    time.sleep(1.5 * attempt)
    return None, None, " | ".join(errors[-5:]) or "No RSS URL configured"


def extract_links(html, base_url=""):
    links = []
    for match in re.finditer(r"""<a\b[^>]*?href=["']([^"']+)["'][^>]*>(.*?)</a>""", html or "", re.I | re.S):
        href = unescape(match.group(1)).strip()
        label = strip_html_fragment(match.group(2))
        if not href or href.startswith(("#", "mailto:", "tel:")):
            continue
        links.append({"url": urljoin(base_url, href), "text": label})
    return links


def find_transcript_links(html, base_url=""):
    candidates = []
    for link in extract_links(html, base_url):
        joined = f"{link['text']} {link['url']}".lower()
        if "transcript" in joined or "full-text" in joined or "full text" in joined:
            candidates.append(link["url"])
    return list(dict.fromkeys(candidates))


def transcript_result(text=None, source=None, url=None, error=None, video_id=None):
    return {
        "text": text,
        "source": source,
        "url": url,
        "video_id": video_id,
        "error": error,
    }


def looks_like_transcript(text):
    text = normalize_text(text)
    if len(text) < MIN_TRANSCRIPT_CHARS:
        return False
    lower = text.lower()
    if "access the full transcript" in lower or "log in to view episode transcripts" in lower:
        return False
    speaker_marks = len(re.findall(r"\b[A-Z][A-Za-z .'-]{1,40}:\s", text))
    return (
        "transcript" in lower
        or speaker_marks >= 3
    )


def extract_probable_transcript_text(html):
    html = html or ""
    is_gated = bool(re.search(r"access the full transcript|log in to view episode transcripts", html, re.I))
    patterns = [
        r"""<article\b[^>]*>(.*?)</article>""",
        r"""<div\b[^>]*(?:class|id)=["'][^"']*(?:transcript|entry-content|post-content|article|body)[^"']*["'][^>]*>(.*?)</div>""",
        r"""<section\b[^>]*(?:class|id)=["'][^"']*(?:transcript|article|body)[^"']*["'][^>]*>(.*?)</section>""",
    ]
    candidates = []
    for pattern in patterns:
        for match in re.finditer(pattern, html, re.I | re.S):
            text = html_to_text(match.group(1))
            if len(text) > 500:
                candidates.append(text)

    full_text = html_to_text(html)
    lower = full_text.lower()
    idx = lower.find("transcript")
    if idx >= 0:
        candidates.append(full_text[idx:])
    candidates.append(full_text)

    candidates.sort(key=len, reverse=True)
    for text in candidates:
        text = clean_transcript_text(text)
        if is_gated and len(text) < 10_000:
            continue
        if looks_like_transcript(text):
            return text
    return None


def clean_transcript_text(text):
    text = clean_text(text or "")
    stripped = text.lstrip()
    if stripped.startswith("{") or stripped.startswith("["):
        try:
            payload = json.loads(stripped)
            parts = []

            def collect(value):
                if isinstance(value, str):
                    if len(value.strip()) > 2:
                        parts.append(value.strip())
                elif isinstance(value, list):
                    for item in value:
                        collect(item)
                elif isinstance(value, dict):
                    for key in ("text", "transcript", "body", "content", "utterance"):
                        if key in value:
                            collect(value[key])
                    if not any(key in value for key in ("text", "transcript", "body", "content", "utterance")):
                        for item in value.values():
                            collect(item)

            collect(payload)
            text = "\n".join(parts)
        except Exception:
            pass
    elif "<" in text:
        text = html_to_text(text)
    text = unescape(text)
    text = re.sub(r"(?m)^(WEBVTT|Kind:.*|Language:.*)$", "", text)
    text = re.sub(r"(?m)^\d+$", "", text)
    text = re.sub(r"\d{2}:\d{2}:\d{2}[.,]\d{3}\s+-->\s+\d{2}:\d{2}:\d{2}[.,]\d{3}.*", "", text)
    text = re.sub(r"\n?\s*(Share|Subscribe|Listen to this episode|Download|Open in Apple Podcasts)\s*\n?", "\n", text, flags=re.I)
    text = re.sub(r"[ \t\r\f\v]+", " ", text)
    text = re.sub(r"\n\s+", "\n", text)
    text = re.sub(r"\n{3,}", "\n\n", text)
    text = text.strip()
    if len(text) > MAX_TRANSCRIPT_CHARS:
        text = text[:MAX_TRANSCRIPT_CHARS].rstrip() + "\n\n[Transcript truncated for feed size]"
    return text


# ── Twitter fetching ──────────────────────────────────────────────────────────

def detect_proxy():
    proxy = os.environ.get("SOCKS_PROXY", "")
    if proxy:
        return proxy
    if sys.platform == "win32":
        try:
            import subprocess
            CF = 0x08000000
            netstat = subprocess.run(["netstat", "-ano"], capture_output=True, text=True,
                                     timeout=5, encoding="utf-8", errors="replace", creationflags=CF)
            tasklist = subprocess.run(["tasklist", "/FI", "IMAGENAME eq ww-ss-local.exe", "/FO", "CSV", "/NH"],
                                      capture_output=True, text=True, timeout=5,
                                      encoding="utf-8", errors="replace", creationflags=CF)
            pids = set()
            for line in tasklist.stdout.strip().split("\n"):
                parts = line.strip().strip('"').split('","')
                if len(parts) >= 2:
                    try: pids.add(parts[1].strip('"'))
                    except (IndexError, ValueError): pass
            if pids:
                for line in netstat.stdout.split("\n"):
                    if "LISTENING" in line:
                        parts = line.split()
                        if len(parts) >= 5 and parts[4] in pids:
                            port = int(parts[1].rsplit(":", 1)[1])
                            return f"socks5h://127.0.0.1:{port}"
        except Exception:
            pass
        import socket
        for port in [12345, 12346, 12347]:
            try:
                s = socket.create_connection(("127.0.0.1", port), timeout=2)
                s.close()
                return f"socks5h://127.0.0.1:{port}"
            except Exception:
                continue
    return ""


async def fetch_twitter(sources):
    twitter_cfg = sources.get("twitter", {})
    accounts = twitter_cfg.get("accounts", [])
    lookback = twitter_cfg.get("lookback_hours", 48)
    max_per_user = twitter_cfg.get("max_tweets_per_user", 5)

    cookies = os.environ.get("TWITTER_COOKIES", "")
    if not cookies:
        log("⚠️ TWITTER_COOKIES not set, skipping Twitter")
        return {"x": [], "errors": ["TWITTER_COOKIES not set"]}

    from twscrape import API, gather
    proxy = detect_proxy()
    if proxy:
        log(f"🌐 Twitter proxy: {proxy}")
        try:
            import twscrape.xclid as _xclid
            from twscrape.http import make_client as _mc
            _xclid._make_client = lambda cookies=None: _mc(
                proxy=proxy,
                headers={"user-agent": "@chrome"},
                cookies=cookies,
            )
        except Exception:
            pass

    db_path = str(SCRIPT_DIR / "twitter_accounts.db")
    api = API(db_path, proxy=proxy) if proxy else API(db_path)
    acc = await api.pool.get_account("feed_bot")
    if acc is None:
        await api.pool.add_account_cookies("feed_bot", cookies)
        await api.pool.set_active("feed_bot", True)

    since = datetime.now(timezone.utc) - timedelta(hours=lookback)
    results = []
    errors = []
    global_seen_ids = set()
    accounts_with_raw_results = 0

    for account in accounts:
        handle = account["handle"]
        min_engagement = int(account.get("min_engagement", twitter_cfg.get("min_engagement", 0)))
        include_replies = bool(account.get("include_replies", twitter_cfg.get("include_replies", False)))
        log(f"📥 @{handle}...")
        try:
            raw = await gather(api.search(f"from:{handle}", limit=max_per_user * 3, kv={"product": "Latest"}))
        except Exception as e:
            log(f"  ⚠️ {e}")
            errors.append(f"@{handle}: {e}")
            continue

        if raw:
            accounts_with_raw_results += 1

        tweets = []
        seen_ids = set()
        filtered_count = 0
        repost_count = 0
        reply_count = 0
        low_engagement_count = 0
        for t in raw:
            if t.date and t.date.replace(tzinfo=timezone.utc) < since:
                continue
            if t.rawContent.startswith("RT @"):
                continue
            author_handle = tweet_author_handle(t)
            if author_handle and author_handle.casefold() != handle.casefold():
                repost_count += 1
                continue
            if not include_replies and is_reply_tweet(t):
                reply_count += 1
                continue
            engagement = tweet_engagement_score(t)
            if engagement < min_engagement:
                low_engagement_count += 1
                continue
            tid = str(t.id)
            if tid in seen_ids or tid in global_seen_ids:
                continue
            seen_ids.add(tid)
            global_seen_ids.add(tid)
            if not is_relevant_tweet(t.rawContent, twitter_cfg):
                filtered_count += 1
                continue
            tweets.append({
                "id": tid,
                "text": t.rawContent,
                "created_at": t.date.isoformat() if t.date else "",
                "like_count": t.likeCount or 0,
                "retweet_count": t.retweetCount or 0,
                "reply_count": t.replyCount or 0,
                "engagement_score": engagement,
                "url": t.url or "",
            })

        tweets.sort(key=lambda x: x["engagement_score"], reverse=True)
        tweets = tweets[:max_per_user]

        if tweets:
            details = []
            if filtered_count:
                details.append(f"filtered {filtered_count}")
            if repost_count:
                details.append(f"skipped {repost_count} reposts")
            if reply_count:
                details.append(f"skipped {reply_count} replies")
            if low_engagement_count:
                details.append(f"skipped {low_engagement_count} below engagement {min_engagement}")
            suffix = f", {', '.join(details)}" if details else ""
            log(f"  ✅ {len(tweets)} tweets{suffix}")
        else:
            details = []
            if filtered_count:
                details.append(f"filtered {filtered_count}")
            if repost_count:
                details.append(f"skipped {repost_count} reposts")
            if reply_count:
                details.append(f"skipped {reply_count} replies")
            if low_engagement_count:
                details.append(f"skipped {low_engagement_count} below engagement {min_engagement}")
            suffix = f" ({', '.join(details)})" if details else ""
            log(f"  ⏭️ nothing new{suffix}")

        results.append({
            "handle": handle,
            "name": account["name"],
            "domain": account.get("domain", "ai"),
            "tier": account.get("tier", ""),
            "tweets": tweets,
        })

    if accounts and accounts_with_raw_results == 0:
        raise RuntimeError(
            f"Twitter health check failed: all {len(accounts)} account queries "
            "returned no raw results"
        )

    return {"x": results, "errors": errors if errors else None}


# ── Podcast fetching ──────────────────────────────────────────────────────────

def parse_rss(xml_text):
    episodes = []
    try:
        root = ET.fromstring(xml_text)
    except ET.ParseError:
        return episodes
    ns = {
        "itunes": "http://www.itunes.com/dtds/podcast-1.0.dtd",
        "content": "http://purl.org/rss/1.0/modules/content/",
    }
    for item in root.iter("item"):
        title = item.findtext("title", "").strip()
        guid = item.findtext("guid", title).strip()
        pub_date_str = item.findtext("pubDate", "")
        link = item.findtext("link", "")
        desc = item.findtext("description", "")
        content = item.findtext("content:encoded", "", ns)
        enc = item.find("enclosure")
        audio = enc.get("url", "") if enc is not None else ""
        try:
            audio_bytes = int(enc.get("length", "0") or 0) if enc is not None else 0
        except ValueError:
            audio_bytes = 0
        dur_el = item.find("itunes:duration", ns)
        duration = dur_el.text.strip() if dur_el is not None and dur_el.text else ""
        transcript_urls = []
        for child in list(item):
            tag = child.tag.rsplit("}", 1)[-1].lower()
            if tag == "transcript":
                transcript_url = child.get("url") or child.get("href") or (child.text or "")
                transcript_url = transcript_url.strip()
                if transcript_url:
                    transcript_urls.append(transcript_url)

        parsed_date = None
        for fmt in ("%a, %d %b %Y %H:%M:%S %z", "%a, %d %b %Y %H:%M:%S %Z",
                    "%Y-%m-%dT%H:%M:%S%z", "%Y-%m-%d"):
            try:
                parsed_date = datetime.strptime(pub_date_str.strip(), fmt)
                if parsed_date.tzinfo is None:
                    parsed_date = parsed_date.replace(tzinfo=timezone.utc)
                break
            except ValueError:
                continue

        episodes.append({
            "title": title, "guid": guid, "pub_date": parsed_date,
            "link": link, "audio_url": audio, "audio_bytes": audio_bytes,
            "duration": duration,
            "description": desc[:2000],
            "raw_description": desc,
            "content": content,
            "transcript_urls": list(dict.fromkeys(transcript_urls)),
        })

    # Fallback: YouTube Atom feed format
    if not episodes:
        atom = "http://www.w3.org/2005/Atom"
        media = "http://search.yahoo.com/mrss/"
        yt = "http://www.youtube.com/xml/schemas/2015"
        for entry in root.iter(f"{{{atom}}}entry"):
            title = (entry.findtext(f"{{{atom}}}title") or "").strip()
            vid_el = entry.find(f"{{{yt}}}videoId")
            vid_id = vid_el.text.strip() if vid_el is not None and vid_el.text else ""
            guid = vid_id or (entry.findtext(f"{{{atom}}}id") or title).strip()

            pub_str = (entry.findtext(f"{{{atom}}}published") or "").strip()
            parsed_date = None
            if pub_str:
                try:
                    parsed_date = datetime.fromisoformat(pub_str.replace("Z", "+00:00"))
                    if parsed_date.tzinfo is None:
                        parsed_date = parsed_date.replace(tzinfo=timezone.utc)
                except ValueError:
                    pass

            link = ""
            for link_el in entry.findall(f"{{{atom}}}link"):
                if link_el.get("rel") == "alternate":
                    link = link_el.get("href", "")
                    break
            if not link and vid_id:
                link = f"https://www.youtube.com/watch?v={vid_id}"

            desc_el = entry.find(f"{{{media}}}group/{{{media}}}description")
            desc = desc_el.text.strip() if desc_el is not None and desc_el.text else ""

            episodes.append({
                "title": title, "guid": guid, "pub_date": parsed_date,
                "link": link, "audio_url": "", "audio_bytes": 0, "duration": "",
                "description": desc[:2000],
                "raw_description": desc,
                "content": "",
                "transcript_urls": [],
            })

    return episodes


def _youtube_video_id(link):
    if not link:
        return None
    parsed = urlparse(link)
    if "youtube.com" in parsed.netloc:
        m = re.search(r"[?&]v=([a-zA-Z0-9_-]{11})", link)
        if m:
            return m.group(1)
        m = re.search(r"/(?:shorts|embed|live)/([a-zA-Z0-9_-]{11})", parsed.path)
        return m.group(1) if m else None
    if "youtu.be" in parsed.netloc:
        vid = parsed.path.strip("/")[:11]
        return vid if len(vid) == 11 else None
    return None


def _yt_transcript_by_id(vid):
    try:
        from youtube_transcript_api import YouTubeTranscriptApi
        proxy = detect_proxy()
        kwargs = {}
        if proxy:
            from youtube_transcript_api.proxies import GenericProxyConfig
            p = proxy.replace("socks5h://", "socks5://")
            kwargs["proxy_config"] = GenericProxyConfig(http_url=p, https_url=p)
        api = YouTubeTranscriptApi(**kwargs)
        segs = api.fetch(vid)
        text = " ".join(s.text for s in segs)
        if len(text) > 200:
            return {
                "text": text,
                "source": "youtube_transcript_api",
                "video_id": vid,
                "error": None,
            }
        return {
            "text": None,
            "source": "youtube_transcript_api",
            "video_id": vid,
            "error": "Transcript too short",
        }
    except Exception as e:
        return {
            "text": None,
            "source": "youtube_transcript_api",
            "video_id": vid,
            "error": str(e),
        }


# youtube_transcript_api raises this exact phrase when the video IS reachable
# but has no English caption track — used as a fallback signal when list() below
# raises instead of returning.
NO_ENGLISH_TRACK_MARKER = "No transcripts were found for any of the requested language codes"


def _no_english_track(error):
    return bool(error) and NO_ENGLISH_TRACK_MARKER in error


def _yt_english_track_status(vid):
    """Does the video have an English caption track?

    Returns 'has_en' / 'no_en' / 'unknown'. Enumerates the actual caption tracks
    via list(), so the verdict doesn't depend on parsing a fetch error message.
    A Korean variety show or a Hindi dub returns 'no_en' even when its title is
    written in English (which the non-Latin script filter can't catch). Network
    or IP-block failures return 'unknown' so the caller keeps the entry for a
    later retry instead of dropping a real English interview.
    """
    try:
        from youtube_transcript_api import YouTubeTranscriptApi
        proxy = detect_proxy()
        kwargs = {}
        if proxy:
            from youtube_transcript_api.proxies import GenericProxyConfig
            p = proxy.replace("socks5h://", "socks5://")
            kwargs["proxy_config"] = GenericProxyConfig(http_url=p, https_url=p)
        tlist = YouTubeTranscriptApi(**kwargs).list(vid)
        for t in tlist:
            if (t.language_code or "").lower().startswith("en"):
                return "has_en"
        return "no_en"
    except Exception as e:
        # A "no transcripts in the requested languages" style error still means
        # the video was reachable and lacks English; anything else is transient.
        return "no_en" if _no_english_track(str(e)) else "unknown"


def get_youtube_transcript(link):
    vid = _youtube_video_id(link)
    if vid:
        result = _yt_transcript_by_id(vid)
        if result["text"]:
            return result
        return transcript_result(
            source="youtube_transcript_api",
            video_id=vid,
            error=result["error"],
        )
    return transcript_result(
        source="youtube_transcript_api",
        video_id=vid,
        error="No YouTube video id in link",
    )


def transcript_from_url(url, source="transcript_url"):
    try:
        body, final_url, content_type = fetch_text_url(url, timeout=45)
        lower_type = (content_type or "").lower()
        if "json" in lower_type:
            try:
                payload = json.loads(body)
                body = json.dumps(payload, ensure_ascii=False)
            except Exception:
                pass
        text = clean_transcript_text(body)
        if looks_like_transcript(text):
            return transcript_result(text=text, source=source, url=final_url)
        return transcript_result(
            source=source,
            url=final_url,
            error=f"Fetched text was too short or did not look like a transcript ({len(text)} chars)",
        )
    except Exception as e:
        return transcript_result(source=source, url=url, error=str(e))


def is_spotify_episode_page(url):
    host = urlparse(url or "").netloc.lower()
    return host in {"anchor.fm", "podcasters.spotify.com", "open.spotify.com"}


def transcript_from_episode_page(url):
    if not url:
        return transcript_result(source="episode_page", error="No episode link")
    if is_spotify_episode_page(url):
        return transcript_result(
            source="episode_page",
            url=url,
            error="Spotify/Anchor catalog pages are show notes, not transcripts",
        )
    try:
        html, final_url, _ = fetch_text_url(url, timeout=45)
    except Exception as e:
        return transcript_result(source="episode_page", url=url, error=str(e))

    errors = []
    for candidate in find_transcript_links(html, final_url):
        result = transcript_from_url(candidate, source="episode_transcript_link")
        if result["text"]:
            return result
        if result["error"]:
            errors.append(f"{candidate}: {result['error']}")

    text = extract_probable_transcript_text(html)
    if text:
        return transcript_result(text=text, source="episode_page", url=final_url)
    return transcript_result(
        source="episode_page",
        url=final_url,
        error="No transcript link or transcript-like page text found"
        + (f"; link errors: {' | '.join(errors[:3])}" if errors else ""),
    )


def duration_minutes(duration):
    parts = str(duration or "").strip().split(":")
    try:
        parts = [int(float(p)) for p in parts if p != ""]
    except ValueError:
        return 0
    if len(parts) == 3:
        return parts[0] * 60 + parts[1]
    if len(parts) == 2:
        return parts[0]
    if len(parts) == 1:
        return parts[0] // 60
    return 0


def transcript_too_sparse(text, duration):
    """Reject show-notes text masquerading as a transcript.

    Real English speech transcribes to roughly 700-900 chars/min; page text
    that passes the transcript heuristics but is far below that is show notes,
    not a transcript. Only applies when the episode duration is known.
    """
    minutes = duration_minutes(duration)
    if not text or minutes < 10:
        return False
    return len(text) / minutes < MIN_TRANSCRIPT_CHARS_PER_MIN


def get_podcast_transcript(ep):
    errors = []
    duration = ep.get("duration")

    def usable(result, source_name):
        if not result["text"]:
            if result["error"]:
                errors.append(f"{source_name}: {result['error']}")
            return False
        if transcript_too_sparse(result["text"], duration):
            errors.append(
                f"{source_name}: text too sparse to be a transcript "
                f"({len(result['text'])} chars for {duration_minutes(duration)} min)"
            )
            return False
        return True

    for url in ep.get("transcript_urls", []):
        result = transcript_from_url(url, source="rss_transcript")
        if usable(result, "rss_transcript"):
            return result

    for source_name, html in (
        ("description_transcript_link", ep.get("raw_description") or ep.get("description") or ""),
        ("content_transcript_link", ep.get("content") or ""),
    ):
        for url in find_transcript_links(html, ep.get("link") or ""):
            result = transcript_from_url(url, source=source_name)
            if usable(result, source_name):
                return result

    page_result = transcript_from_episode_page(ep.get("link"))
    if usable(page_result, "episode_page"):
        return page_result

    youtube_result = get_youtube_transcript(ep.get("link"))
    if usable(youtube_result, "youtube"):
        return youtube_result

    return transcript_result(
        source=None,
        error="; ".join(errors[:5]) or "Transcript unavailable",
        video_id=youtube_result.get("video_id"),
    )


def fetch_channel(channel, lookback_hours, transcript_cache):
    name = channel["name"]
    channel_lookback = int(channel.get("lookback_hours", lookback_hours))
    since = datetime.now(timezone.utc) - timedelta(hours=channel_lookback)
    log(f"📻 {name}...")

    rss_text, final_url, rss_error = fetch_rss_with_fallback(channel)
    if rss_error:
        log(f"  ⚠️ RSS failed: {rss_error}")
        return [], rss_error

    episodes = parse_rss(rss_text)
    if not episodes:
        error = f"No episodes parsed from RSS: {final_url}"
        log(f"  ⚠️ {error}")
        return [], error

    results = []
    for ep in episodes:
        if ep["pub_date"] and ep["pub_date"] < since:
            continue

        cached = transcript_cache.get(ep["guid"]) or transcript_cache.get(ep["link"])
        if cached is not None:
            log(f"  ♻️ {ep['title'][:60]} (transcript reused)")
            entry = dict(cached)
            entry["guid"] = ep["guid"]
            results.append(entry)
            continue

        log(f"  🆕 {ep['title'][:60]}...")

        fetched = get_podcast_transcript(ep)
        transcript = fetched["text"]
        if transcript:
            log(f"    ✅ transcript ({len(transcript)} chars, {fetched['source']})")
        else:
            log(f"    ⏭️ transcript unavailable: {fetched['error']}")

        results.append({
            "channel": name,
            "domain": channel.get("domain", "ai"),
            "guid": ep["guid"],
            "title": ep["title"],
            "pub_date": ep["pub_date"].isoformat() if ep["pub_date"] else "",
            "link": ep["link"],
            "audio_url": ep["audio_url"],
            "audio_bytes": ep.get("audio_bytes", 0),
            "duration": ep["duration"],
            "description": ep["description"],
            "transcript": transcript,
            "transcript_available": bool(transcript),
            "transcript_source": fetched["source"] if transcript else None,
            "transcript_url": fetched.get("url") if transcript else None,
            "transcript_video_id": fetched["video_id"],
            "transcript_error": fetched["error"] if not transcript else None,
        })

    if not results:
        log(f"  ⏭️ nothing in window")
    return results, None


# ── Person-appearance search (YouTube via yt-dlp) ────────────────────────────
# Tracks specific people (lab execs, analysts, founders) as podcast/interview
# GUESTS across all of YouTube, complementing the fixed channel RSS list.
# Filters keep the feed consistent with channel content: the person's name must
# appear in the video title (cleanest false-positive guard — YouTube search
# happily returns videos matching only the company keywords), short clips are
# dropped by minimum duration, routine market-news briefings are skipped, and
# channels below min_channel_subscribers are rejected (small channels are
# mostly re-upload accounts that pollute the source).

DAILY_BRIEFING_RE = re.compile(
    r"\bmorning markets?\b|\bmarket (?:wrap|close|open)\b|\b(?:opening|closing) bell\b"
    r"|\bdaily (?:briefing|update|wrap|recap|rundown)\b|\b(?:before|after) the bell\b"
    r"|\bpre[- ]?market\b|\bafter[- ]?hours? (?:wrap|recap)\b"
    r"|\bbloomberg (?:daybreak|surveillance)\b",
    re.IGNORECASE,
)

# YouTube matches common Chinese names loosely and returns dramas/anime
# compilations; these tokens never appear in a real interview title.
CN_TITLE_SKIP_RE = re.compile(
    r"(MULTI\s?SUB|MULTISUB|多语字幕|"
    r"动漫|番剧|玄幻|热血|逆袭|神豪|舔狗|"
    r"最新合集|大合集|EP\d+\s*[-~～至]\s*\d+|第\s*\d+\s*[-~～至]\s*\d+\s*集|"
    r"短剧|爽剧|霸总|穿越)",
    re.IGNORECASE,
)

# Foreign-audience re-upload / reaction channels (Chinese dubs, Hindi "kissa"
# recaps, Korean subs) clear the subscriber gate — some have 1M+ subs — but
# carry no English transcript and aren't real interviews. They give themselves
# away by naming the channel or writing the title in a non-Latin script.
# Applied only to overseas people; region:"cn" voices legitimately appear in
# Chinese-titled interviews and are handled by CN_TITLE_SKIP_RE instead.
FOREIGN_SCRIPT_RE = re.compile(
    r"[一-鿿"      # CJK (Chinese / kanji)
    r"぀-ヿ"       # Japanese kana
    r"가-힯"       # Korean hangul
    r"ऀ-ॿ"       # Devanagari (Hindi)
    r"؀-ۿ"       # Arabic
    r"฀-๿"       # Thai
    r"Ѐ-ӿ]"      # Cyrillic
)


def _person_in_topic_position(person, title):
    """True when the title's grammar marks the person as subject matter rather
    than a speaker. A guest title puts the person in speaking position ("Sam
    Altman on the future of AI", "OpenAI President Greg Brockman: ..."); coverage
    ABOUT the person puts the name in the object position of on/about/versus
    ("Journalist Karen Hao on Sam Altman, OpenAI & ...") or frames them with
    commentary verbs ("exposes / slams / the truth about <Person>"). The feed
    only wants videos where the person actually appears — being talked about,
    however insightfully, does not count.
    """
    p = re.escape(person)
    m = re.search(rf"\b(?:on|about|against|versus|vs\.?)\s+(.{{0,40}}?)\b{p}\b",
                  title, re.IGNORECASE)
    # "with / ft." inside the gap flips it back to a guest marker
    # ("a conversation on AGI with Sam Altman").
    if m and not re.search(r"\b(?:with|w/|ft\.?|feat(?:uring)?)\b", m.group(1),
                           re.IGNORECASE):
        return True
    return bool(re.search(
        rf"\b(?:exposes?|expos[ée]|slams?|criticiz\w+|debunk\w*|reacts?\s+to|"
        rf"the\s+(?:truth|story|case|rise|fall|cult|myth|problem)\s+"
        rf"(?:of|about|against|behind|with))\s+.{{0,40}}?\b{p}\b"
        rf"|\b{p}(?:['’]s)?\s+(?:documentary|expos[ée]|scandal|controversy)\b",
        title, re.IGNORECASE))


def _run_ytdlp(args, timeout=300):
    import subprocess
    cmd = [sys.executable, "-m", "yt_dlp", "--no-warnings"]
    proxy = detect_proxy()
    if proxy:
        cmd += ["--proxy", proxy.replace("socks5h://", "socks5://")]
    return subprocess.run(cmd + args, capture_output=True, text=True,
                          encoding="utf-8", errors="replace", timeout=timeout)


# YouTube's server-side "upload date" search filters (the sp= URL param).
# Applying one replaces client-side date filtering, which is unreliable here
# because flat search omits upload_date and per-video metadata extraction is
# bot-checked from datacenter IPs.
RECENCY_SP = {
    "hour": "EgIIAQ%3D%3D",
    "day": "EgIIAg%3D%3D",
    "week": "EgIIAw%3D%3D",
    "month": "EgIIBA%3D%3D",
    "year": "EgIIBQ%3D%3D",
}


def run_ytdlp_search(query, max_n, recency=None, timeout=300):
    """Flat search: one request to the results page, no per-video extraction.

    YouTube bot-checks per-video metadata extraction from datacenter IPs
    (GitHub Actions), but serves the search results page itself. Flat entries
    lack upload_date/description; fetch_video_meta() backfills them
    best-effort for the few candidates that survive filtering.

    recency ("hour"/"day"/"week"/"month"/"year") applies YouTube's own
    upload-date filter server-side, so only videos published inside that
    window come back at all.
    """
    if recency in RECENCY_SP:
        target = (f"https://www.youtube.com/results?search_query={quote_plus(query)}"
                  f"&sp={RECENCY_SP[recency]}")
        extra = ["--playlist-items", f"1:{max_n}"]
    else:
        target = f"ytsearch{max_n}:{query}"
        extra = []
    proc = _run_ytdlp(["--flat-playlist", "-J", *extra, target],
                      timeout=timeout)
    try:
        data = json.loads(proc.stdout or "{}")
    except json.JSONDecodeError:
        data = {}
    entries = data.get("entries") or []
    if not entries and proc.returncode != 0:
        detail = (proc.stderr or "").strip().splitlines()
        raise RuntimeError(detail[-1] if detail else f"yt-dlp exit {proc.returncode}")
    return [{
        "id": e.get("id") or "",
        "title": e.get("title") or "",
        "channel": e.get("channel") or e.get("uploader") or "YouTube",
        "channel_url": e.get("channel_url") or e.get("uploader_url") or "",
        "upload_date": "",
        "duration": e.get("duration") or 0,  # seconds; may be missing in flat mode
        "description": e.get("description") or "",
    } for e in entries if e]


# Channel subscriber counts, cached per run: searches for different people
# often surface the same channels, and each lookup is a full page fetch.
# Benign races under the search ThreadPoolExecutor — worst case a duplicate fetch.
_channel_subs_cache = {}


def fetch_channel_subscribers(channel_url, timeout=90):
    """Subscriber count from the channel page (flat, single entry, no video
    extraction). Returns None when unknown (missing URL, bot-check, hidden
    count) — callers decide the failure policy."""
    if not channel_url:
        return None
    if channel_url in _channel_subs_cache:
        return _channel_subs_cache[channel_url]
    subs = None
    try:
        proc = _run_ytdlp(["--flat-playlist", "-J", "--playlist-items", "1",
                           channel_url], timeout=timeout)
        data = json.loads(proc.stdout or "{}")
        subs = data.get("channel_follower_count")
    except Exception:
        subs = None
    _channel_subs_cache[channel_url] = subs
    return subs


def fetch_video_meta(vid, timeout=120):
    """Full per-video metadata; returns None when blocked (datacenter IPs)."""
    try:
        proc = _run_ytdlp(["--dump-json", "--skip-download",
                           f"https://www.youtube.com/watch?v={vid}"], timeout=timeout)
        data = json.loads(proc.stdout.strip().splitlines()[-1])
        return {
            "upload_date": data.get("upload_date") or "",
            "duration": data.get("duration") or 0,
            "description": data.get("description") or "",
        }
    except Exception:
        return None


def parse_youtube_published_date(html):
    parser = YouTubePublishedDateParser()
    try:
        parser.feed(html or "")
    except Exception:
        return None
    for value in parser.values:
        try:
            parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
        except ValueError:
            continue
        if parsed.tzinfo is None:
            parsed = parsed.replace(tzinfo=timezone.utc)
        return parsed.astimezone(timezone.utc)
    return None


def fetch_video_page_published_date(vid, timeout=30):
    """Fallback for datacenter runs where yt-dlp's full metadata is blocked."""
    try:
        resp = httpx.get(
            f"https://www.youtube.com/watch?v={vid}",
            timeout=timeout,
            headers={"User-Agent": UA},
            follow_redirects=True,
        )
        resp.raise_for_status()
    except Exception:
        return None
    return parse_youtube_published_date(resp.text)


def format_hms(seconds):
    seconds = int(seconds or 0)
    if seconds >= 3600:
        return f"{seconds // 3600}:{seconds % 3600 // 60:02d}:{seconds % 60:02d}"
    return f"{seconds // 60}:{seconds % 60:02d}"


def search_person_appearances(search, people_cfg, since, known_ids):
    person = search["person"]
    recency = people_cfg.get("search_recency")
    if recency in RECENCY_SP:
        # Server-side date filter already bounds the window; a year anchor in
        # the query would only distort ranking (titles rarely contain the year).
        query = search["query"]
    else:
        query = f"{search['query']} {datetime.now(timezone.utc).year}"
    max_n = int(people_cfg.get("max_results_per_search", 3))
    min_seconds = int(people_cfg.get("min_duration_minutes", 20)) * 60
    min_subs = int(people_cfg.get("min_channel_subscribers", 0))
    log(f"🔍 {person}: {query}" + (f" [{recency}]" if recency in RECENCY_SP else ""))

    kept = []
    for v in run_ytdlp_search(query, max_n, recency=recency):
        title = v["title"]
        if not v["id"] or v["id"] in known_ids:
            continue
        if person.lower() not in title.lower():
            log(f"  ⏭️ name not in title: {title[:60]}")
            continue
        if _person_in_topic_position(person, title):
            log(f"  ⏭️ talked about, not appearing: {title[:60]}")
            continue
        if DAILY_BRIEFING_RE.search(title) or (
                search.get("region") == "cn" and CN_TITLE_SKIP_RE.search(title)):
            log(f"  ⏭️ title blacklist: {title[:60]}")
            continue
        # Foreign-audience re-upload / reaction channel: non-Latin channel name
        # or title on an overseas person. These clear the subscriber gate but
        # carry no English transcript and aren't real interviews.
        if search.get("region") != "cn" and (
                FOREIGN_SCRIPT_RE.search(v.get("channel") or "")
                or FOREIGN_SCRIPT_RE.search(title)):
            log(f"  ⏭️ foreign re-upload ({v.get('channel')}): {title[:50]}")
            continue
        if v["duration"] and v["duration"] < min_seconds:
            log(f"  ⏭️ too short ({v['duration']}s, likely a clip): {title[:60]}")
            continue
        # Small channels are mostly re-upload/clip accounts; require a real
        # audience before accepting the video. Fail-open when the count is
        # unavailable (bot-checked channel page) so an infra hiccup doesn't
        # silently kill the whole feature — the log line keeps it auditable.
        if min_subs:
            subs = fetch_channel_subscribers(v.get("channel_url"))
            if subs is not None and subs < min_subs:
                log(f"  ⏭️ channel too small ({subs:,} subs < {min_subs:,}): "
                    f"{v['channel']} | {title[:50]}")
                continue
            if subs is None:
                log(f"  ⚠️ subscriber count unknown, kept: {v['channel']}")
        # Backfill date/description for the few survivors. If full metadata is
        # blocked, the structured date on the watch page is checked below.
        meta = fetch_video_meta(v["id"])
        if meta:
            v = {**v, **meta}
            if v["duration"] and v["duration"] < min_seconds:
                log(f"  ⏭️ too short ({v['duration']}s, likely a clip): {title[:60]}")
                continue
        pub_date = None
        pub_date_source = "unverified"
        if v["upload_date"]:
            try:
                pub_date = datetime.strptime(v["upload_date"], "%Y%m%d").replace(tzinfo=timezone.utc)
                pub_date_source = "video_metadata"
            except ValueError:
                pass
        if pub_date is None:
            pub_date = fetch_video_page_published_date(v["id"])
            if pub_date is not None:
                pub_date_source = "youtube_page"
        # Unknown dates remain eligible, but any date exposed by the watch page
        # must pass the real publication window instead of using discovery time.
        if pub_date and pub_date < since:
            log(f"  ⏭️ outside publication window ({pub_date.date()}): {title[:60]}")
            continue
        v["pub_date_source"] = pub_date_source
        kept.append((v, pub_date))
    return kept


def _person_video_ids(entries):
    ids = set()
    for entry in entries:
        vid = entry.get("transcript_video_id") or _youtube_video_id(entry.get("link"))
        if vid:
            ids.add(vid)
    return ids


def fetch_people(sources, existing_feed, known_video_ids):
    people_cfg = sources.get("podcasts", {}).get("people", {})
    searches = people_cfg.get("searches", [])
    if not searches:
        return [], []

    now = datetime.now(timezone.utc)
    since = now - timedelta(hours=people_cfg.get("lookback_hours", 168))

    # Rolling-window guarantee: previous person hits stay in the feed while
    # inside the window, even when today's YouTube search ranking no longer
    # surfaces them. Entries without a transcript retry captions each run.
    carried = []
    for entry in (existing_feed or {}).get("podcasts", []):
        if not entry.get("person"):
            continue
        stamp = entry.get("pub_date") or entry.get("first_seen") or ""
        try:
            stamp_dt = datetime.fromisoformat(stamp)
        except ValueError:
            continue
        if stamp_dt.tzinfo is None:
            stamp_dt = stamp_dt.replace(tzinfo=timezone.utc)
        if stamp_dt < since:
            continue
        vid = entry.get("transcript_video_id") or _youtube_video_id(entry.get("link"))
        if not entry.get("pub_date"):
            entry = dict(entry)
            entry["pub_date_source"] = "unverified"
            entry["publish_date_unverified"] = True
            if vid:
                verified_date = fetch_video_page_published_date(vid)
                if verified_date is not None:
                    if verified_date < since:
                        log(f"  ⏭️ carried old entry dropped ({verified_date.date()}): "
                            f"{entry.get('title','')[:50]}")
                        continue
                    entry["pub_date"] = verified_date.isoformat()
                    entry["pub_date_source"] = "youtube_page"
                    entry["publish_date_unverified"] = False
        # Purge entries accepted before the topic-position gate existed (or
        # through any earlier gap): being talked about is not an appearance.
        if _person_in_topic_position(entry["person"], entry.get("title", "")):
            log(f"  ⏭️ carried topic-not-guest entry dropped: "
                f"{entry.get('title','')[:50]}")
            continue
        if not entry.get("transcript") and entry.get("transcript_video_id"):
            vid = entry["transcript_video_id"]
            if entry.get("region") != "cn" and _yt_english_track_status(vid) == "no_en":
                # Foreign original/dub that slipped in before the gate, or that a
                # network fluke let through on an earlier run — drop it from the
                # carry set so it stops recurring.
                log(f"  ⏭️ carried foreign entry dropped (no English track): "
                    f"{entry.get('title','')[:50]}")
                continue
            retried = _yt_transcript_by_id(vid)
            if retried["text"]:
                entry = dict(entry)
                entry["transcript"] = clean_transcript_text(retried["text"])
                entry["transcript_available"] = True
                entry["transcript_source"] = retried["source"]
                entry["transcript_error"] = None
        carried.append(entry)

    seen = set(known_video_ids) | _person_video_ids(carried)
    errors = []
    candidates = []
    known_ids = frozenset(seen)
    with ThreadPoolExecutor(max_workers=4) as pool:
        futures = {pool.submit(search_person_appearances, s, people_cfg, since, known_ids): s
                   for s in searches}
        for fut in as_completed(futures):
            search = futures[fut]
            try:
                for v, pub_date in fut.result():
                    candidates.append((search, v, pub_date))
            except Exception as e:
                errors.append(f"person search {search['person']}: {e}")

    # Dedupe, newest first, then cap new entries per run. The cap bounds the
    # digest burst on the first run (7-day lookback can surface a dozen hits at
    # once) and on any unusually busy day; overflow is logged, and whatever is
    # still fresh gets another chance when tomorrow's searches re-surface it.
    fresh = []
    for search, v, pub_date in candidates:
        if v["id"] in seen:
            continue
        seen.add(v["id"])
        fresh.append((search, v, pub_date))
    epoch = datetime(1970, 1, 1, tzinfo=timezone.utc)
    fresh.sort(key=lambda item: item[2] or epoch, reverse=True)
    max_new = int(people_cfg.get("max_new_per_run", 5))
    if len(fresh) > max_new:
        for search, v, _ in fresh[max_new:]:
            log(f"  ⏸️ over daily cap ({max_new}), deferred: [{search['person']}] {v['title'][:60]}")
        fresh = fresh[:max_new]

    episodes = []
    for search, v, pub_date in fresh:
        vid = v["id"]
        log(f"  🆕 [{search['person']}] {v['title'][:60]}")
        # English-original gate: for overseas people, reject a video whose only
        # caption tracks are non-English (foreign original / dub) — even when the
        # title is written in English, which the script filter can't catch. Only
        # a definitive 'no_en' verdict skips; 'unknown' (network/IP block) falls
        # through so a real English interview is never dropped on a fluke. cn
        # voices are exempt (their real interviews are in Chinese).
        if search.get("region") != "cn" and _yt_english_track_status(vid) == "no_en":
            log(f"    ⏭️ no English track (foreign original/dub), skipped: {v['title'][:50]}")
            continue
        fetched = _yt_transcript_by_id(vid)
        transcript = clean_transcript_text(fetched["text"]) if fetched["text"] else None
        if transcript:
            log(f"    ✅ transcript ({len(transcript)} chars)")
        else:
            log(f"    ⏭️ transcript unavailable (kept for retry): {(fetched['error'] or '')[:80]}")
        entry = {
            "channel": v["channel"],
            "domain": search.get("domain", "ai"),
            "person": search["person"],
            "search_query": search["query"],
            "guid": f"yt:{vid}",
            "title": v["title"],
            "pub_date": pub_date.isoformat() if pub_date else "",
            "pub_date_source": v.get("pub_date_source", "unverified"),
            "publish_date_unverified": pub_date is None,
            "first_seen": now.isoformat(),
            "link": f"https://www.youtube.com/watch?v={vid}",
            "audio_url": "",
            "duration": format_hms(v["duration"]),
            "description": v["description"][:2000],
            "transcript": transcript,
            "transcript_available": bool(transcript),
            "transcript_source": fetched["source"] if transcript else None,
            "transcript_url": None,
            "transcript_video_id": vid,
            "transcript_error": fetched["error"] if not transcript else None,
        }
        if search.get("region"):
            entry["region"] = search["region"]
        episodes.append(entry)

    return carried + episodes, errors


def fetch_podcasts(sources, people_only=False):
    podcast_cfg = sources.get("podcasts", {})
    channels = podcast_cfg.get("channels", [])
    lookback = podcast_cfg.get("lookback_hours", 72)

    # Reuse transcripts already fetched by a previous run: episodes still inside
    # the window keep their entry instead of being re-scraped. Entries without a
    # transcript are retried each run.
    transcript_cache = {}
    existing = load_feed("feed-podcasts.json") or {}
    hydrate_transcripts(existing)
    for entry in existing.get("podcasts", []):
        if not entry.get("transcript"):
            continue
        if transcript_too_sparse(entry["transcript"], entry.get("duration")):
            continue  # show notes that slipped in as "transcript"; refetch
        for key in (entry.get("guid"), entry.get("link")):
            if key:
                transcript_cache[key] = entry

    all_episodes = []
    errors = []

    if people_only:
        all_episodes = [e for e in existing.get("podcasts", []) if not e.get("person")]
    else:
        with ThreadPoolExecutor(max_workers=4) as pool:
            futures = {pool.submit(fetch_channel, ch, lookback, transcript_cache): ch for ch in channels}
            for fut in as_completed(futures):
                try:
                    eps, err = fut.result()
                    all_episodes.extend(eps)
                    if err:
                        errors.append(f"{futures[fut]['name']}: {err}")
                except Exception as e:
                    errors.append(f"{futures[fut]['name']}: {e}")

    log("\n── People searches ──")
    people_episodes, people_errors = fetch_people(sources, existing, _person_video_ids(all_episodes))
    all_episodes.extend(people_episodes)
    errors.extend(people_errors)

    all_episodes.sort(key=lambda x: x.get("pub_date", ""), reverse=True)
    return {"podcasts": all_episodes, "errors": errors if errors else None}


# ── arXiv fetching ───────────────────────────────────────────────────────────

def fetch_arxiv(sources):
    arxiv_cfg = sources.get("arxiv", {})
    categories = arxiv_cfg.get("categories", [])
    max_papers = arxiv_cfg.get("max_papers", 30)
    lookback = arxiv_cfg.get("lookback_hours", 48)

    if not categories:
        return {"papers": [], "errors": ["No arXiv categories configured"]}

    cat_query = "+OR+".join(f"cat:{c['id']}" for c in categories)
    log(f"\n━━━ arXiv Papers ━━━")
    log(f"🔬 Categories: {', '.join(c['id'] for c in categories)}")

    ns = {
        "atom": "http://www.w3.org/2005/Atom",
        "arxiv": "http://arxiv.org/schemas/atom",
    }
    roots = []
    errors = []
    # submittedDate is the correct semantic order but its index can lag;
    # lastUpdatedDate is fresher but mixes old revisions into the result. Merge
    # both views, then filter and sort by the original publication timestamp.
    for query_index, sort_by in enumerate(("submittedDate", "lastUpdatedDate")):
        if query_index:
            time.sleep(3)
        url = (f"https://export.arxiv.org/api/query?search_query={cat_query}"
               f"&sortBy={sort_by}&sortOrder=descending&max_results={max_papers * 3}")
        try:
            resp = httpx.get(url, timeout=30, headers={"User-Agent": UA})
            resp.raise_for_status()
            roots.append(ET.fromstring(resp.text))
        except Exception as e:
            log(f"  ⚠️ arXiv {sort_by} query failed: {e}")
            errors.append(f"{sort_by}: {e}")

    if not roots:
        return {"papers": [], "errors": errors}

    since = datetime.now(timezone.utc) - timedelta(hours=lookback)
    papers = []
    seen_ids = set()

    for root in roots:
        for entry in root.findall("atom:entry", ns):
            id_url = entry.findtext("atom:id", "", ns)
            arxiv_id = id_url.split("/abs/")[-1] if "/abs/" in id_url else id_url

            if arxiv_id in seen_ids:
                continue
            seen_ids.add(arxiv_id)

            pub_str = entry.findtext("atom:published", "", ns)
            pub_date = None
            if pub_str:
                try:
                    pub_date = datetime.fromisoformat(pub_str.replace("Z", "+00:00"))
                except ValueError:
                    pass

            if pub_date and pub_date < since:
                continue

            title = entry.findtext("atom:title", "", ns).strip()
            title = re.sub(r"\s+", " ", title)
            abstract = entry.findtext("atom:summary", "", ns).strip()
            abstract = re.sub(r"\s+", " ", abstract)

            authors = []
            for author_el in entry.findall("atom:author", ns):
                name = author_el.findtext("atom:name", "", ns).strip()
                if name:
                    authors.append(name)

            cats = [cat.get("term", "") for cat in entry.findall("atom:category", ns) if cat.get("term")]
            primary_el = entry.find("arxiv:primary_category", ns)
            primary_cat = primary_el.get("term", "") if primary_el is not None else ""

            pdf_url = ""
            for link_el in entry.findall("atom:link", ns):
                if link_el.get("title") == "pdf":
                    pdf_url = link_el.get("href", "")
                    break

            comment = (entry.findtext("arxiv:comment", "", ns) or "").strip()

            papers.append({
                "arxiv_id": arxiv_id,
                "title": title,
                "authors": authors[:5],
                "abstract": abstract,
                "primary_category": primary_cat,
                "categories": cats,
                "pdf_url": pdf_url,
                "abs_url": f"https://arxiv.org/abs/{arxiv_id}",
                "published": pub_date.isoformat() if pub_date else pub_str,
                "comment": comment,
            })

    papers.sort(key=lambda p: p.get("published") or "", reverse=True)
    papers = papers[:max_papers]
    log(f"  ✅ {len(papers)} papers")
    return {"papers": papers, "errors": errors or None}


# ── Official blogs (Anthropic / OpenAI / DeepMind) ────────────────────────────

def parse_iso_datetime(value):
    if not value:
        return None
    try:
        dt = datetime.fromisoformat(value.strip().replace("Z", "+00:00"))
    except ValueError:
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt


def parse_rfc822_datetime(value):
    if not value:
        return None
    try:
        dt = parsedate_to_datetime(value.strip())
    except Exception:
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt


def blog_items_from_rss(xml_text, src, since):
    """Parse RSS 2.0 <item> or Atom <entry> elements into article dicts."""
    root = ET.fromstring(xml_text)
    items = []
    for el in root.iter("item"):
        title = re.sub(r"\s+", " ", (el.findtext("title") or "")).strip()
        link = (el.findtext("link") or "").strip()
        pub = parse_rfc822_datetime(el.findtext("pubDate")) or parse_iso_datetime(el.findtext("pubDate"))
        summary = html_to_text(el.findtext("description") or "")
        items.append((title, link, pub, summary))
    if not items:  # Atom fallback
        ns = {"atom": "http://www.w3.org/2005/Atom"}
        for el in root.findall("atom:entry", ns):
            title = re.sub(r"\s+", " ", (el.findtext("atom:title", "", ns) or "")).strip()
            link = ""
            for link_el in el.findall("atom:link", ns):
                if link_el.get("rel") in (None, "alternate"):
                    link = link_el.get("href", "")
                    break
            pub = parse_iso_datetime(
                el.findtext("atom:published", "", ns) or el.findtext("atom:updated", "", ns)
            )
            summary = html_to_text(
                el.findtext("atom:summary", "", ns) or el.findtext("atom:content", "", ns) or ""
            )
            items.append((title, link, pub, summary))

    articles = []
    for title, link, pub, summary in items:
        if not title or not link:
            continue
        if pub and pub < since:
            continue
        if not pub or not summary:
            # One page fetch fills both gaps: a missing summary (DeepMind's RSS
            # often ships empty descriptions) and a missing publish date.
            _, desc, page_date = blog_page_meta(link)
            summary = summary or desc
            if not pub:
                # Visible dates are day-granular; pad like the sitemap path.
                if not page_date or page_date < since - timedelta(hours=24):
                    continue  # can't verify freshness — never push undated items
                pub = page_date
        articles.append({
            "id": link,
            "source": src["id"],
            "source_name": src.get("name", src["id"]),
            "title": title,
            "url": link,
            "published": pub.isoformat() if pub else None,
            "summary": summary[:600].strip(),
        })
    return articles


MONTH_DATE_RE = re.compile(
    r"\b(Jan(?:uary)?|Feb(?:ruary)?|Mar(?:ch)?|Apr(?:il)?|May|Jun(?:e)?|Jul(?:y)?|"
    r"Aug(?:ust)?|Sep(?:tember)?|Oct(?:ober)?|Nov(?:ember)?|Dec(?:ember)?)\.?\s+"
    r"(\d{1,2}),\s+(20\d{2})\b"
)


def parse_visible_date(html):
    """First 'Sep 19, 2023'-style date on the page — the visible publish date."""
    m = MONTH_DATE_RE.search(html or "")
    if not m:
        return None
    month = m.group(1)[:3].title()
    try:
        return datetime.strptime(f"{month} {m.group(2)} {m.group(3)}", "%b %d %Y").replace(
            tzinfo=timezone.utc
        )
    except ValueError:
        return None


def blog_page_meta(url):
    """Fetch an article page: <title>, meta description, visible publish date."""
    try:
        resp = httpx.get(url, timeout=20, headers={"User-Agent": UA}, follow_redirects=True)
        resp.raise_for_status()
    except Exception:
        return "", "", None
    html = resp.text
    title = ""
    m = re.search(r"<title[^>]*>(.*?)</title>", html, re.IGNORECASE | re.DOTALL)
    if m:
        title = re.sub(r"\s+", " ", unescape(m.group(1))).strip()
        # Drop site-name suffixes like "... \ Anthropic" or "... | Anthropic"
        title = re.sub(r"\s*[\\|·—-]\s*Anthropic\s*$", "", title)
    desc = ""
    m = re.search(
        r'<meta[^>]+(?:property="og:description"|name="description")[^>]+content="([^"]*)"',
        html, re.IGNORECASE,
    ) or re.search(
        r'<meta[^>]+content="([^"]*)"[^>]+(?:property="og:description"|name="description")',
        html, re.IGNORECASE,
    )
    if m:
        desc = re.sub(r"\s+", " ", unescape(m.group(1))).strip()
    return title, desc, parse_visible_date(html)


def blog_items_from_sitemap(xml_text, src, since, max_items):
    """Sites without RSS (Anthropic): official sitemap.xml gives URL + lastmod.
    lastmod is only a cheap pre-filter — site redeploys bump it on old posts in
    bulk — so fetch each candidate page and gate on its visible publish date."""
    ns = {"sm": "http://www.sitemaps.org/schemas/sitemap/0.9"}
    root = ET.fromstring(xml_text)
    prefixes = src.get("include_prefixes", [])
    hits = []
    for url_el in root.findall("sm:url", ns):
        loc = (url_el.findtext("sm:loc", "", ns) or "").strip()
        if prefixes and not any(loc.startswith(p) for p in prefixes):
            continue
        lastmod = parse_iso_datetime(url_el.findtext("sm:lastmod", "", ns))
        if not lastmod or lastmod < since:
            continue
        hits.append((lastmod, loc))
    hits.sort(reverse=True)

    articles = []
    # Visible publish dates are day-granular; pad the cutoff so a post from
    # the lookback window's starting day is not dropped by its 00:00 timestamp.
    day_since = since - timedelta(hours=24)
    for lastmod, loc in hits:
        if len(articles) >= max_items:
            break
        title, desc, page_date = blog_page_meta(loc)
        if not page_date:
            # lastmod alone is untrustworthy (redeploys bump it on old posts);
            # without a verifiable publish date, never push the item.
            continue
        if page_date < day_since:
            continue  # old post whose lastmod was bumped by a redeploy/edit
        if not title:
            # Fall back to a de-slugged URL tail so the item is still usable
            title = loc.rstrip("/").split("/")[-1].replace("-", " ").strip().title()
        articles.append({
            "id": loc,
            "source": src["id"],
            "source_name": src.get("name", src["id"]),
            "title": title,
            "url": loc,
            "published": page_date.isoformat(),
            "summary": desc[:600],
        })
    return articles


def fetch_blogs(sources):
    blogs_cfg = sources.get("blogs", {})
    blog_sources = blogs_cfg.get("sources", [])
    lookback = blogs_cfg.get("lookback_hours", 48)
    max_per_source = blogs_cfg.get("max_per_source", 5)

    log(f"\n━━━ Official Blogs ━━━")
    if not blog_sources:
        return {"articles": [], "errors": ["No blog sources configured"]}

    since = datetime.now(timezone.utc) - timedelta(hours=lookback)
    articles = []
    errors = []

    for src in blog_sources:
        name = src.get("name", src.get("id", "?"))
        try:
            resp = httpx.get(src["url"], timeout=30, headers={"User-Agent": UA}, follow_redirects=True)
            resp.raise_for_status()
            if src.get("type") == "sitemap":
                found = blog_items_from_sitemap(resp.text, src, since, max_per_source)
            else:
                found = blog_items_from_rss(resp.text, src, since)
            found.sort(key=lambda a: a.get("published") or "", reverse=True)
            found = found[:max_per_source]
            articles.extend(found)
            log(f"  ✅ {name}: {len(found)} articles")
        except Exception as e:
            errors.append(f"{name}: {e}")
            log(f"  ⚠️ {name} failed: {e}")

    articles.sort(key=lambda a: a.get("published") or "", reverse=True)
    return {"articles": articles, "errors": errors or None}


# ── Main ──────────────────────────────────────────────────────────────────────

async def main():
    configure_stdio()
    parser = argparse.ArgumentParser()
    parser.add_argument("--twitter-only", action="store_true")
    parser.add_argument("--podcasts-only", action="store_true")
    parser.add_argument("--arxiv-only", action="store_true")
    parser.add_argument("--blogs-only", action="store_true")
    parser.add_argument("--people-only", action="store_true",
                        help="refresh person-appearance searches only; keep channel episodes as-is")
    args = parser.parse_args()

    sources = load_sources()
    now = datetime.now(timezone.utc)
    FEEDS_DIR.mkdir(parents=True, exist_ok=True)

    run_all = not (args.twitter_only or args.podcasts_only or args.arxiv_only
                   or args.blogs_only or args.people_only)

    if run_all or args.twitter_only:
        log("\n━━━ Twitter/X ━━━")
        twitter_feed = await fetch_twitter(sources)
        twitter_feed["generated_at"] = now.isoformat()
        write_json(FEEDS_DIR / "feed-x.json", twitter_feed)
        active = sum(1 for a in twitter_feed["x"] if a["tweets"])
        log(f"✅ feed-x.json ({active}/{len(twitter_feed['x'])} accounts with content)")

    if run_all or args.podcasts_only or args.people_only:
        log("\n━━━ Podcasts ━━━")
        podcast_feed = fetch_podcasts(sources, people_only=args.people_only)
        podcast_feed["generated_at"] = now.isoformat()
        with_transcript = sum(1 for e in podcast_feed["podcasts"] if e.get("transcript"))
        externalize_transcripts(podcast_feed)
        write_json(FEEDS_DIR / "feed-podcasts.json", podcast_feed)
        person_hits = sum(1 for e in podcast_feed["podcasts"] if e.get("person"))
        log(f"✅ feed-podcasts.json ({len(podcast_feed['podcasts'])} episodes, "
            f"{with_transcript} with transcript, {person_hits} person hits)")

    if run_all or args.arxiv_only:
        arxiv_feed = fetch_arxiv(sources)
        arxiv_feed["generated_at"] = now.isoformat()
        if not arxiv_feed["papers"]:
            existing_arxiv = load_feed("feed-arxiv.json")
            if existing_arxiv and existing_arxiv.get("papers"):
                log("ℹ️  arXiv fetch returned nothing; keeping existing feed-arxiv.json")
                arxiv_feed = existing_arxiv
        write_json(FEEDS_DIR / "feed-arxiv.json", arxiv_feed)
        log(f"✅ feed-arxiv.json ({len(arxiv_feed['papers'])} papers)")

    if run_all or args.blogs_only:
        blogs_feed = fetch_blogs(sources)
        blogs_feed["generated_at"] = now.isoformat()
        if not blogs_feed["articles"] and blogs_feed.get("errors"):
            existing_blogs = load_feed("feed-blogs.json")
            if existing_blogs and existing_blogs.get("articles"):
                log("ℹ️  Blog fetch failed everywhere; keeping existing feed-blogs.json")
                blogs_feed = existing_blogs
        write_json(FEEDS_DIR / "feed-blogs.json", blogs_feed)
        log(f"✅ feed-blogs.json ({len(blogs_feed['articles'])} articles)")

    log("\n🎉 Feed generation complete")


if __name__ == "__main__":
    asyncio.run(main())
