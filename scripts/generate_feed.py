"""Central feed generator — fetches raw content from Twitter + podcasts + arXiv.

Runs on GitHub Actions daily. Outputs raw content (no LLM summarization).
Subscribers pull the feed JSON and use their own LLM to generate digests.

Feeds are stateless rolling-window snapshots: every run publishes ALL content
inside each source's lookback window, so extra manual runs never eat content.
Per-user "already seen" dedup happens client-side in prepare_digest.py.

Usage:
    python scripts/generate_feed.py [--twitter-only | --podcasts-only | --arxiv-only]

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
from html import unescape
from html.parser import HTMLParser
from pathlib import Path
from urllib.parse import urljoin, urlparse

import httpx

SCRIPT_DIR = Path(__file__).parent
ROOT_DIR = SCRIPT_DIR.parent
FEEDS_DIR = ROOT_DIR / "feeds"

UA = "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36"
MIN_TRANSCRIPT_CHARS = 600
MAX_TRANSCRIPT_CHARS = int(os.environ.get("MAX_TRANSCRIPT_CHARS", "500000"))


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
            _xclid._make_client = lambda: _mc(proxy=proxy, headers={"user-agent": "@chrome"})
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

    for account in accounts:
        handle = account["handle"]
        log(f"📥 @{handle}...")
        try:
            raw = await gather(api.search(f"from:{handle}", limit=max_per_user * 3, kv={"product": "Latest"}))
        except Exception as e:
            log(f"  ⚠️ {e}")
            errors.append(f"@{handle}: {e}")
            continue

        tweets = []
        seen_ids = set()
        for t in raw:
            if t.date and t.date.replace(tzinfo=timezone.utc) < since:
                continue
            if t.rawContent.startswith("RT @"):
                continue
            tid = str(t.id)
            if tid in seen_ids:
                continue
            seen_ids.add(tid)
            tweets.append({
                "id": tid,
                "text": t.rawContent,
                "created_at": t.date.isoformat() if t.date else "",
                "like_count": t.likeCount or 0,
                "retweet_count": t.retweetCount or 0,
                "reply_count": t.replyCount or 0,
                "url": t.url or "",
            })

        tweets.sort(key=lambda x: x["like_count"] + x["retweet_count"] * 2, reverse=True)
        tweets = tweets[:max_per_user]

        if tweets:
            log(f"  ✅ {len(tweets)} tweets")
        else:
            log(f"  ⏭️ nothing new")

        results.append({
            "handle": handle,
            "name": account["name"],
            "domain": account.get("domain", "ai"),
            "tier": account.get("tier", ""),
            "tweets": tweets,
        })

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
            "link": link, "audio_url": audio, "duration": duration,
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

            desc_el = entry.find(f"{{{media}}}group/{{{media}}}description")
            desc = desc_el.text.strip() if desc_el is not None and desc_el.text else ""

            episodes.append({
                "title": title, "guid": guid, "pub_date": parsed_date,
                "link": link, "audio_url": "", "duration": "",
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


def transcript_from_episode_page(url):
    if not url:
        return transcript_result(source="episode_page", error="No episode link")
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


def get_podcast_transcript(ep):
    errors = []

    for url in ep.get("transcript_urls", []):
        result = transcript_from_url(url, source="rss_transcript")
        if result["text"]:
            return result
        if result["error"]:
            errors.append(f"rss_transcript: {result['error']}")

    for source_name, html in (
        ("description_transcript_link", ep.get("raw_description") or ep.get("description") or ""),
        ("content_transcript_link", ep.get("content") or ""),
    ):
        for url in find_transcript_links(html, ep.get("link") or ""):
            result = transcript_from_url(url, source=source_name)
            if result["text"]:
                return result
            if result["error"]:
                errors.append(f"{source_name}: {result['error']}")

    page_result = transcript_from_episode_page(ep.get("link"))
    if page_result["text"]:
        return page_result
    if page_result["error"]:
        errors.append(f"episode_page: {page_result['error']}")

    youtube_result = get_youtube_transcript(ep.get("link"))
    if youtube_result["text"]:
        return youtube_result
    if youtube_result["error"]:
        errors.append(f"youtube: {youtube_result['error']}")

    return transcript_result(
        source=None,
        error="; ".join(errors[:5]) or "Transcript unavailable",
        video_id=youtube_result.get("video_id"),
    )


def fetch_channel(channel, lookback_hours, transcript_cache):
    name = channel["name"]
    since = datetime.now(timezone.utc) - timedelta(hours=lookback_hours)
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


def fetch_podcasts(sources):
    podcast_cfg = sources.get("podcasts", {})
    channels = podcast_cfg.get("channels", [])
    lookback = podcast_cfg.get("lookback_hours", 72)

    # Reuse transcripts already fetched by a previous run: episodes still inside
    # the window keep their entry instead of being re-scraped. Entries without a
    # transcript are retried each run.
    transcript_cache = {}
    existing = load_feed("feed-podcasts.json") or {}
    for entry in existing.get("podcasts", []):
        if not entry.get("transcript"):
            continue
        for key in (entry.get("guid"), entry.get("link")):
            if key:
                transcript_cache[key] = entry

    all_episodes = []
    errors = []

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
    url = (f"https://export.arxiv.org/api/query?search_query={cat_query}"
           f"&sortBy=submittedDate&sortOrder=descending&max_results={max_papers * 2}")

    log(f"\n━━━ arXiv Papers ━━━")
    log(f"🔬 Categories: {', '.join(c['id'] for c in categories)}")

    try:
        resp = httpx.get(url, timeout=30, headers={"User-Agent": UA})
        resp.raise_for_status()
    except Exception as e:
        log(f"  ⚠️ arXiv API failed: {e}")
        return {"papers": [], "errors": [str(e)]}

    ns = {
        "atom": "http://www.w3.org/2005/Atom",
        "arxiv": "http://arxiv.org/schemas/atom",
    }

    try:
        root = ET.fromstring(resp.text)
    except ET.ParseError as e:
        log(f"  ⚠️ XML parse error: {e}")
        return {"papers": [], "errors": [str(e)]}

    since = datetime.now(timezone.utc) - timedelta(hours=lookback)
    papers = []
    seen_ids = set()

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
    return {"papers": papers, "errors": None}


# ── Main ──────────────────────────────────────────────────────────────────────

async def main():
    configure_stdio()
    parser = argparse.ArgumentParser()
    parser.add_argument("--twitter-only", action="store_true")
    parser.add_argument("--podcasts-only", action="store_true")
    parser.add_argument("--arxiv-only", action="store_true")
    args = parser.parse_args()

    sources = load_sources()
    now = datetime.now(timezone.utc)
    FEEDS_DIR.mkdir(parents=True, exist_ok=True)

    run_all = not (args.twitter_only or args.podcasts_only or args.arxiv_only)

    if run_all or args.twitter_only:
        log("\n━━━ Twitter/X ━━━")
        twitter_feed = await fetch_twitter(sources)
        twitter_feed["generated_at"] = now.isoformat()
        write_json(FEEDS_DIR / "feed-x.json", twitter_feed)
        active = sum(1 for a in twitter_feed["x"] if a["tweets"])
        log(f"✅ feed-x.json ({active}/{len(twitter_feed['x'])} accounts with content)")

    if run_all or args.podcasts_only:
        log("\n━━━ Podcasts ━━━")
        podcast_feed = fetch_podcasts(sources)
        podcast_feed["generated_at"] = now.isoformat()
        write_json(FEEDS_DIR / "feed-podcasts.json", podcast_feed)
        with_transcript = sum(1 for e in podcast_feed["podcasts"] if e.get("transcript"))
        log(f"✅ feed-podcasts.json ({len(podcast_feed['podcasts'])} episodes, {with_transcript} with transcript)")

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

    log("\n🎉 Feed generation complete")


if __name__ == "__main__":
    asyncio.run(main())
