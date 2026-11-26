# Configuration Handling

When the user says something that sounds like a settings change:

### Source Changes
Sources are curated centrally and update automatically.
If a user asks to add or remove sources: "信息源由中央统一维护，自动更新。
如果你想推荐一个信息源，可以到 https://github.com/Benboerba620/ai-signal 提 issue。"

### Schedule Changes
- "改成每周" → update `frequency`
- "改到早上 9 点" → update `deliveryTime`; if using OpenClaw, update the Agent cron job
- "时区改成东部时间" → update `timezone`; if using OpenClaw, update the Agent cron job

### Language Changes
- "切换成中文" → update `language` to `"zh"`
- "切换成英文" → update `language` to `"en"`
- "切换成双语" → update `language` to `"bilingual"`

### Granularity Changes
- "更简短一些" → change `granularity` to `highlights`
- "更详细一些" → change `granularity` to `full`
- "标准就好" → change `granularity` to `summary`

### Domain Changes
- "只看 AI" → update `domains` to `["ai"]`
- "加上投资" → update `domains` to `["ai", "invest"]`

### Anonymous Usage Count
- "关掉匿名统计" / "turn off the usage ping" → set `"anonymous_ping": false`
- "精读链接用 ChatGPT" / "deep read in ChatGPT" → set `"deep_read": "chatgpt"`; "关掉精读链接" / "turn off deep-read links" → `"deep_read": "off"`
- If asked what it sends: at most one request per day for the static file
  `feeds/ping.txt` on jsDelivr, with no ID, config, or query string. jsDelivr
  sees the IP address, as it does for feed downloads.

### Delivery Changes
- "推到 Telegram / 飞书" → update `delivery.method`, guide setup if needed
- "换个邮箱" → update `delivery.email`
- "直接在这里看" → set `delivery.method` to `"stdout"`

### Prompt Changes
When a user wants to customize how their digest sounds, copy the relevant prompt
to `~/.ai-signal/prompts/` and edit the copy. User prompts always override the
repo defaults and will not be overwritten by central updates.

```bash
mkdir -p ~/.ai-signal/prompts
cp ${SKILL_DIR}/prompts/<filename>.md ~/.ai-signal/prompts/<filename>.md
```

Then edit `~/.ai-signal/prompts/<filename>.md` with the user's requested change.
Examples:
- "短一点" → edit `digest-intro.md` and the relevant summarization prompt.
- "更像投资简报" → edit `digest-intro.md`, `summarize-podcast.md`, and `summarize-papers.md`.
- "推特只要翻译和原文" → edit `summarize-tweets.md`.
- "恢复默认" → delete the user prompt file.

### Info Requests
- "看看我的设置" → display config.json
- "我追踪了哪些源？" → list all sources from sources.json
- "看看我的 prompt" → display prompt files

After any change, confirm what was changed.

---
