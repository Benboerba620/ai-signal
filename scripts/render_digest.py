"""Render prepared AI Signal JSON into a readable Markdown digest.

This is a lightweight formatter for zero-code users and scheduled delivery.
It does not call an LLM. It uses central cached summaries when available and
falls back to raw feed metadata.

Usage:
    python scripts/prepare_digest.py | python scripts/render_digest.py
"""

import json
import re
import sys
from datetime import datetime, timezone
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

AI_KEYWORDS = (
    "agent", "agents", "model", "models",
    "claude", "openai", "anthropic", "gemini", "deepmind", "gpt",
    "inference", "token", "tokens", "eval", "benchmark", "reasoning",
    "robot", "robotics", "chip", "hardware", "accelerator", "tapeout",
    "math", "startup", "startups",
    "invest", "investment", "investing", "market", "markets", "product",
    "founder", "company", "enterprise", "software", "developer",
    "人工智能", "大模型", "模型", "智能体", "推理", "芯片", "机器人",
    "投资", "市场", "产品", "创业", "公司", "软件", "开发者",
)
AI_WORD_KEYWORDS = ("ai", "agi", "llm", "llms", "gpt", "gpu", "tpu")

NOISE_PATTERNS = (
    r"^agree$",
    r"^haha",
    r"^thank",
    r"^thanks",
    r"^ty$",
    r"good morning",
    r"please report back",
)


def clean_text(text):
    return "".join(ch for ch in text if not 0xD800 <= ord(ch) <= 0xDFFF)


def configure_stdio():
    for stream in (sys.stdout, sys.stderr):
        if hasattr(stream, "reconfigure"):
            stream.reconfigure(encoding="utf-8", errors="replace")


def section_from_summary(text):
    if not text:
        return ""
    marker = "\n## Summary\n"
    if marker in text:
        text = text.split(marker, 1)[1]
    text = re.sub(r"^---\s*", "", text.strip())
    text = re.sub(r"\s*---\s*$", "", text.strip())
    return text.strip()


def clean_meta_intro(text):
    text = re.sub(r"(?m)^好的，这是.*?缓存研究简报。\s*", "", text)
    text = re.sub(
        r"本期节目是\s*(.*?)的深度对话，核心探讨了",
        r"\1讨论了",
        text,
    )
    text = re.sub(
        r"本集节目采访了\s*(.*?)。节目核心讲述了他们",
        r"\1讲述了他们",
        text,
    )
    text = re.sub(
        r"本期节目由\s*(.*?)主持，与(.*?)展开对话。核心议题是",
        r"\1与\2讨论",
        text,
    )
    replacements = [
        (r"本期节目是\s*", ""),
        (r"本期节目由\s*", ""),
        (r"本集节目采访了\s*", ""),
        (r"本集节目是\s*", ""),
        (r"这期节目是\s*", ""),
        (r"这期内容介绍[：，]?\s*", ""),
        (r"这条内容介绍[：，]?\s*", ""),
    ]
    for pattern, replacement in replacements:
        text = re.sub(pattern, replacement, text)
    text = re.sub(r"节目核心讲述了", "他们讲述了", text)
    text = re.sub(r"核心议题是", "他们讨论", text)
    return text


def short_text(text, limit):
    text = re.sub(r"\s+", " ", clean_text(text or "")).strip()
    if len(text) <= limit:
        return text
    return text[:limit].rstrip() + "..."


def digest_timezone(data):
    return (data.get("config") or {}).get("timezone") or "Asia/Shanghai"


def format_source_time(value, timezone_name):
    if not value:
        return "未验证"
    try:
        parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except (TypeError, ValueError):
        return "未验证"
    if parsed.tzinfo is None:
        return "未验证"
    try:
        target_timezone = ZoneInfo(timezone_name)
        label = "北京时间" if timezone_name == "Asia/Shanghai" else timezone_name
    except ZoneInfoNotFoundError:
        target_timezone = timezone.utc
        label = "UTC"
    return f"{parsed.astimezone(target_timezone):%Y-%m-%d %H:%M}（{label}）"


def is_noise_tweet(text):
    normalized = re.sub(r"\s+", " ", (text or "").lower()).strip()
    if len(normalized) < 25 and not re.search(r"https?://|@\w+", normalized):
        return True
    return any(re.search(pattern, normalized) for pattern in NOISE_PATTERNS)


def is_ai_related_text(text, extra=""):
    combined = f"{text or ''} {extra or ''}".lower()
    if any(keyword in combined for keyword in AI_KEYWORDS):
        return True
    return any(re.search(rf"(?<![a-z0-9]){re.escape(keyword)}(?![a-z0-9])", combined) for keyword in AI_WORD_KEYWORDS)


def zh_from_summary(text):
    text = section_from_summary(text)
    if not text:
        return ""
    text = re.sub(r"^#+\s*.*$", "", text, flags=re.M).strip()
    text = re.sub(r"(?im)^[-*]?\s*(原文|链接|url|source|source link|original).*?$", "", text).strip()
    paragraphs = [p.strip(" -*\n") for p in re.split(r"\n{2,}", text) if p.strip()]
    if not paragraphs:
        return short_text(text, 180)
    return short_text(paragraphs[0], 220)


def simple_paper_summary(text, abstract=""):
    summary = section_from_summary(text)
    if summary:
        summary = re.sub(r"^#+\s*.*$", "", summary, flags=re.M)
        summary = re.sub(r"(?im)^[-*]?\s*(来源|source|arxiv|pdf|链接).*?$", "", summary)
        sentences = re.split(r"(?<=[。！？.!?])\s+", re.sub(r"\s+", " ", summary).strip())
        for sentence in sentences:
            sentence = sentence.strip(" -*")
            if 25 <= len(sentence) <= 260:
                return sentence
        return short_text(summary, 220)
    return short_text(abstract, 220)


def selected_tweets(data):
    accounts = data.get("x") or []
    selected = []
    for account in accounts:
        for tweet in account.get("tweets", []):
            original = tweet.get("text", "")
            if is_noise_tweet(original):
                continue
            if not is_ai_related_text(original):
                continue
            selected.append((account, tweet))
    return selected


def selected_papers(data):
    central = data.get("central_summaries") or {}
    central_papers = central.get("papers") or []
    if central_papers:
        return [
            item for item in central_papers
            if is_ai_related_text(item.get("title", ""), item.get("summary_text", ""))
        ][:8]
    return [
        paper for paper in data.get("papers", [])
        if is_ai_related_text(paper.get("title", ""), paper.get("abstract", ""))
    ][:8]


def podcast_lookup(data):
    lookup = {}
    for item in data.get("podcasts", []) or []:
        keys = [
            item.get("link", ""),
            f"{item.get('channel', '')}\n{item.get('title', '')}",
            item.get("title", ""),
        ]
        for key in keys:
            if key:
                lookup[key] = item
    return lookup


def is_relevant_podcast_item(item, raw_item=None):
    raw_item = raw_item or {}
    text = " ".join(
        [
            item.get("title", ""),
            raw_item.get("title", ""),
            raw_item.get("description", ""),
            (raw_item.get("transcript") or "")[:2000],
        ]
    )
    return is_ai_related_text(text)


def selected_podcasts(data):
    central = data.get("central_summaries") or {}
    raw_lookup = podcast_lookup(data)
    podcasts = central.get("podcasts") or []
    if podcasts:
        selected = []
        for item in podcasts:
            raw_item = (
                raw_lookup.get(item.get("source_url", ""))
                or raw_lookup.get(f"{item.get('channel', '')}\n{item.get('title', '')}")
                or raw_lookup.get(item.get("title", ""))
            )
            if is_relevant_podcast_item(item, raw_item):
                selected.append(item)
        return selected
    return [item for item in data.get("podcasts", []) if is_relevant_podcast_item(item, item)]


def render_podcasts(data, lines):
    central = data.get("central_summaries") or {}
    timezone_name = digest_timezone(data)
    raw_lookup = podcast_lookup(data)
    podcasts = selected_podcasts(data) if (central.get("podcasts") or []) else []
    if podcasts:
        lines.append("## 播客精选")
        for item in podcasts:
            title = item.get("title", "Untitled")
            url = item.get("source_url", "")
            channel = item.get("channel", "")
            raw_item = (
                raw_lookup.get(url)
                or raw_lookup.get(f"{channel}\n{title}")
                or raw_lookup.get(title)
                or {}
            )
            lines.append(f"### {channel} - {title}" if channel else f"### {title}")
            lines.append(
                f"发布时间：{format_source_time(item.get('pub_date') or raw_item.get('pub_date'), timezone_name)}"
            )
            if url:
                lines.append(f"原文：{url}")
            summary = section_from_summary(item.get("summary_text", ""))
            lines.append(clean_meta_intro(summary) if summary else "中央摘要暂不可用。")
            lines.append("")
        return

    raw = selected_podcasts(data)
    if raw:
        lines.append("## 播客更新")
        for item in raw[:5]:
            title = item.get("title", "Untitled")
            channel = item.get("channel", "")
            url = item.get("link", "")
            desc = short_text(item.get("description", ""), 260)
            lines.append(f"- {channel} - {title}")
            lines.append(f"  发布时间：{format_source_time(item.get('pub_date'), timezone_name)}")
            if desc:
                lines.append(f"  {desc}")
            if url:
                lines.append(f"  {url}")
        lines.append("")


def render_tweets(data, lines):
    central = data.get("central_summaries") or {}
    timezone_name = digest_timezone(data)
    x_by_id = {str(item.get("tweet_id") or item.get("id")): item for item in central.get("x", [])}
    selected = selected_tweets(data)

    if selected:
        lines.append("## X / Twitter 动态")
        for account, tweet in selected:
            name = account.get("name") or account.get("handle") or "Unknown"
            handle = account.get("handle", "")
            tweet_id = str(tweet.get("id", ""))
            item = x_by_id.get(tweet_id, {})
            url = item.get("source_url") or tweet.get("url", "")
            original = item.get("original_text") or tweet.get("text", "")
            lines.append(f"### {name}" + (f" (@{handle})" if handle else ""))
            lines.append(
                f"发布时间：{format_source_time(item.get('created_at') or tweet.get('created_at'), timezone_name)}"
            )
            translation = zh_from_summary(item.get("summary_text", ""))
            if translation:
                prefix = "中文：" if len(original) <= 280 else "简要说明："
                lines.append(f"{prefix}{translation}")
            else:
                lines.append("中文：中央翻译暂不可用，先保留原文。")
            lines.append("")
            lines.append("原文：")
            lines.append(f"> {original.replace(chr(10), chr(10) + '> ')}")
            if url:
                lines.append(f"链接：{url}")
            lines.append("")
        return

    accounts = data.get("x") or []
    active = [account for account in accounts if account.get("tweets")]
    if not active:
        return
    lines.append("## X / Twitter 动态")
    for account in active[:8]:
        name = account.get("name") or account.get("handle")
        lines.append(f"### {name}")
        for tweet in account.get("tweets", [])[:2]:
            if is_noise_tweet(tweet.get("text", "")):
                continue
            if not is_ai_related_text(tweet.get("text", "")):
                continue
            text = short_text(tweet.get("text", ""), 260)
            url = tweet.get("url", "")
            lines.append(f"- 原文：{text}")
            lines.append(f"  发布时间：{format_source_time(tweet.get('created_at'), timezone_name)}")
            if url:
                lines.append(f"  {url}")
        lines.append("")


def render_papers(data, lines):
    central = data.get("central_summaries") or {}
    timezone_name = digest_timezone(data)
    raw_lookup = {}
    for paper in data.get("papers") or []:
        for key in (paper.get("arxiv_id"), paper.get("title")):
            if key:
                raw_lookup[str(key)] = paper
    central_papers = selected_papers(data) if (central.get("papers") or []) else []
    filtered_papers = central_papers
    if filtered_papers:
        lines.append("## 论文精选")
        for item in filtered_papers[:8]:
            title = item.get("title", "Untitled")
            url = item.get("source_url", "")
            raw_item = raw_lookup.get(str(item.get("arxiv_id") or "")) or raw_lookup.get(title) or {}
            lines.append(f"### {title}")
            lines.append(
                f"首次提交：{format_source_time(item.get('published') or raw_item.get('published'), timezone_name)}"
            )
            if url:
                lines.append(f"arXiv：{url}")
            summary = simple_paper_summary(item.get("summary_text", ""))
            lines.append(f"一句话：{summary or '中央论文摘要暂不可用。'}")
            lines.append("")
        return

    papers = data.get("papers") or []
    if papers:
        lines.append("## arXiv 新论文")
        filtered = selected_papers(data)
        for paper in filtered[:8]:
            title = paper.get("title", "Untitled")
            abstract = short_text(paper.get("abstract", ""), 220)
            url = paper.get("abs_url", "")
            lines.append(f"### {title}")
            lines.append(f"首次提交：{format_source_time(paper.get('published'), timezone_name)}")
            if url:
                lines.append(f"arXiv：{url}")
            if abstract:
                lines.append(f"一句话：{abstract}")
            lines.append("")
        lines.append("")


def main():
    configure_stdio()
    raw = sys.stdin.read()
    if not raw.strip():
        raise SystemExit("No input JSON")
    data = json.loads(raw)
    cfg = data.get("config") or {}
    stats = data.get("stats") or {}
    now = datetime.now().strftime("%Y-%m-%d")
    display_podcasts = len(selected_podcasts(data))
    display_tweets = len(selected_tweets(data))
    display_papers = len(selected_papers(data))

    lines = [
        f"# AI Signal 日报 - {now}",
        "",
        f"版本：{cfg.get('summary_profile', 'raw')} | 语言：{cfg.get('language', 'en')} | 详细度：{cfg.get('granularity', 'summary')}",
        "",
        (
            f"今日内容：播客 {display_podcasts} 条，"
            f"X / Twitter 动态 {display_tweets} 条，"
            f"论文 {display_papers} 篇。"
        ),
        "",
    ]

    if data.get("errors"):
        lines.append("> 非致命提示：" + "; ".join(data["errors"]))
        lines.append("")

    render_podcasts(data, lines)
    render_tweets(data, lines)
    render_papers(data, lines)

    if len(lines) <= 7:
        lines.append("今天暂时没有可展示的新内容。")

    sys.stdout.write(clean_text("\n".join(line.rstrip() for line in lines)).strip() + "\n")


if __name__ == "__main__":
    main()
