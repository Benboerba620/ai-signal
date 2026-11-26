"""One-click deep-read links.

Each digest item gets a `deep_read_url`. Opening it starts a new chat in the
reader's own Claude (or ChatGPT) account with a deep-read prompt already filled
in. Nothing runs on our side and nothing is sent anywhere until the reader
presses send; the prompt lives only in the URL.

For podcasts with a published transcript the prompt points at the transcript
on jsDelivr, because the episode page is often audio only. For X posts the
prompt carries the tweet text itself, because x.com needs a login.

Config: "deep_read": "claude" (default) | "chatgpt" | "off".
"""
from urllib.parse import quote

TRANSCRIPT_BASE = "https://cdn.jsdelivr.net/gh/Benboerba620/ai-signal@main/"
TARGETS = {
    "claude": "https://claude.ai/new?q=",
    "chatgpt": "https://chatgpt.com/?q=",
}
MAX_TWEET_CHARS = 600

PROMPTS = {
    "zh": ("请精读下面这条内容，用中文回答：1. 核心观点；2. 关键事实和数字；"
           "3. 为什么重要；4. 有哪些疑点或反方观点。只依据原文，原文没有的不要编。\n"
           "来源：{source}\n标题：{title}\n{body}"),
    "en": ("Deep-read the item below: 1. core argument; 2. key facts and numbers; "
           "3. why it matters; 4. open questions or counterpoints. Use only the source; "
           "do not invent anything it does not say.\n"
           "Source: {source}\nTitle: {title}\n{body}"),
}
LABELS = {
    "zh": {"link": "原文：", "transcript": "全文转录：", "text": "原文内容：\n"},
    "en": {"link": "Original: ", "transcript": "Full transcript: ", "text": "Text:\n"},
}


def target_base(config):
    choice = str(config.get("deep_read", "claude")).strip().lower()
    if choice in ("off", "false", "none", ""):
        return None
    return TARGETS.get(choice, TARGETS["claude"])


def _lang(language):
    return "zh" if str(language).startswith("zh") or language == "bilingual" else "en"


def _url(base, lang, source, title, lines):
    prompt = PROMPTS[lang].format(source=source, title=title or "-", body="\n".join(lines))
    return base + quote(prompt, safe="")


def tweet_url(base, lang, account, tweet):
    labels = LABELS[lang]
    text = (tweet.get("text") or "").strip()
    if len(text) > MAX_TWEET_CHARS:
        text = text[:MAX_TWEET_CHARS] + "…"
    lines = [labels["text"] + text]
    if tweet.get("url"):
        lines.append(labels["link"] + tweet["url"])
    source = account.get("name") or account.get("handle") or "X"
    return _url(base, lang, f"{source} (X)", "", lines)


def episode_url(base, lang, episode):
    labels = LABELS[lang]
    lines = []
    if episode.get("transcript_available") and episode.get("transcript_path"):
        lines.append(labels["transcript"] + TRANSCRIPT_BASE + episode["transcript_path"].lstrip("/"))
    if episode.get("link"):
        lines.append(labels["link"] + episode["link"])
    return _url(base, lang, episode.get("channel") or "Podcast", episode.get("title"), lines)


def article_url(base, lang, article):
    labels = LABELS[lang]
    lines = [labels["link"] + article["url"]] if article.get("url") else []
    return _url(base, lang, article.get("source_name") or article.get("source") or "Blog",
                article.get("title"), lines)


def attach_deep_read_urls(config, language, x_accounts, episodes, articles):
    """Add `deep_read_url` to every tweet, episode and article in place."""
    base = target_base(config)
    if base is None:
        return
    lang = _lang(language)
    for account in x_accounts:
        for tweet in account.get("tweets", []):
            tweet["deep_read_url"] = tweet_url(base, lang, account, tweet)
    for episode in episodes:
        episode["deep_read_url"] = episode_url(base, lang, episode)
    for article in articles:
        article["deep_read_url"] = article_url(base, lang, article)
