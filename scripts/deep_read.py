"""One-click deep-read links.

Each podcast episode and blog article gets a short `deep_read_url` pointing at
docs/r.html on GitHub Pages. That page looks the item up in the public feed,
builds the deep-read prompt, and hands it to the reader's own chat model.
DeepSeek has no prefill URL, so for DeepSeek the page copies the prompt plus the
full transcript and opens the site; Kimi, Claude and ChatGPT get the prompt
prefilled. Nothing is sent until the reader presses send. X posts get no link:
they are short enough to read in the digest.

The link stays short (about 100 characters) because chat apps and bots drop or
truncate long URLs; a URL holding the whole prompt was about 900 characters.

Config: "deep_read": "deepseek" (default) | "kimi" | "claude" | "chatgpt" | "off".
"""
from pathlib import PurePosixPath
from urllib.parse import urlencode

PAGE = "https://benboerba620.github.io/ai-signal/r.html"
CHOICES = ("deepseek", "kimi", "claude", "chatgpt")
DEFAULT = "deepseek"


def target(config):
    choice = str(config.get("deep_read", DEFAULT)).strip().lower()
    if choice in ("off", "false", "none", ""):
        return None
    return choice if choice in CHOICES else DEFAULT


def _lang(language):
    return "zh" if str(language).startswith("zh") or language == "bilingual" else "en"


def _link(params, lang, via):
    params = {k: v for k, v in params.items() if v}
    if lang != "zh":
        params["lang"] = lang
    if via != DEFAULT:
        params["via"] = via
    return PAGE + "#" + urlencode(params)


def episode_link(episode, lang="zh", via=DEFAULT):
    tr = ""
    if episode.get("transcript_available") and episode.get("transcript_path"):
        tr = PurePosixPath(episode["transcript_path"]).stem
    return _link({"k": "p", "u": episode.get("link") or episode.get("guid"), "tr": tr}, lang, via)


def article_link(article, lang="zh", via=DEFAULT):
    return _link({"k": "b", "u": article.get("url")}, lang, via)


def attach_deep_read_urls(config, language, x_accounts, episodes, articles):
    """Add `deep_read_url` to every episode and article in place (not to X posts)."""
    via = target(config)
    if via is None:
        return
    lang = _lang(language)
    for episode in episodes:
        episode["deep_read_url"] = episode_link(episode, lang, via)
    for article in articles:
        article["deep_read_url"] = article_link(article, lang, via)
