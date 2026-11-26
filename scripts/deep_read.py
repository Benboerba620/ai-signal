"""One-click deep-read links.

Each digest item gets a short `deep_read_url` pointing at docs/r.html on GitHub
Pages. That page looks the item up in the public feed, builds the deep-read
prompt, and opens a new chat in the reader's own Claude (or ChatGPT) with the
prompt filled in. Nothing is sent until the reader presses send.

The link stays short (about 100 characters) because chat apps and bots drop or
truncate long URLs; a URL holding the whole prompt was about 900 characters.

Config: "deep_read": "claude" (default) | "chatgpt" | "off".
"""
from pathlib import PurePosixPath
from urllib.parse import urlencode

PAGE = "https://benboerba620.github.io/ai-signal/r.html"
CHOICES = ("claude", "chatgpt")


def target(config):
    choice = str(config.get("deep_read", "claude")).strip().lower()
    if choice in ("off", "false", "none", ""):
        return None
    return choice if choice in CHOICES else "claude"


def _lang(language):
    return "zh" if str(language).startswith("zh") or language == "bilingual" else "en"


def _link(params, lang, via):
    params = {k: v for k, v in params.items() if v}
    if lang != "zh":
        params["lang"] = lang
    if via != "claude":
        params["via"] = via
    return PAGE + "#" + urlencode(params)


def tweet_link(tweet, lang="zh", via="claude"):
    return _link({"k": "x", "id": str(tweet.get("id") or ""), "u": tweet.get("url")}, lang, via)


def episode_link(episode, lang="zh", via="claude"):
    tr = ""
    if episode.get("transcript_available") and episode.get("transcript_path"):
        tr = PurePosixPath(episode["transcript_path"]).stem
    return _link({"k": "p", "u": episode.get("link") or episode.get("guid"), "tr": tr}, lang, via)


def article_link(article, lang="zh", via="claude"):
    return _link({"k": "b", "u": article.get("url")}, lang, via)


def attach_deep_read_urls(config, language, x_accounts, episodes, articles):
    """Add `deep_read_url` to every tweet, episode and article in place."""
    via = target(config)
    if via is None:
        return
    lang = _lang(language)
    for account in x_accounts:
        for tweet in account.get("tweets", []):
            tweet["deep_read_url"] = tweet_link(tweet, lang, via)
    for episode in episodes:
        episode["deep_read_url"] = episode_link(episode, lang, via)
    for article in articles:
        article["deep_read_url"] = article_link(article, lang, via)
