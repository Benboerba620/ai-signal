# Daily Digest

AI-powered daily digest of top builders, analysts, and executives across AI, energy, and investing — aggregated from podcasts and Twitter/X.

## Trigger

When the user says any of: "daily digest", "set up daily digest", "run digest", "show my digest", "configure digest", or runs `/ai-signal`.

## Setup flow

If `~/.ai-signal/config.json` doesn't exist, guide the user through setup:

1. Ask their **language** preference: English, Chinese, or bilingual
2. Ask their **granularity**: highlights (2-3 bullets per item), summary (paragraph per item), or full (detailed analysis with quotes)
3. Ask their **domains**: AI, energy, investing — pick any combination
4. Ask their **delivery method**: Telegram (need bot token + chat ID), Feishu (need webhook URL), email (need Resend API key + address), or just show in chat
5. Save config to `~/.ai-signal/config.json`
6. Store any API keys in `~/.ai-signal/.env`

## Running the digest

1. Run: `python scripts/prepare_digest.py` — this fetches the latest feeds from the central repo and outputs a JSON blob
2. Parse the JSON output. It contains:
   - `podcasts`: array of recent podcast episodes with `title`, `channel`, `description`, `transcript` (may be null), `link`, `pub_date`
   - `x`: array of Twitter accounts, each with `handle`, `name`, `tweets` (array of `text`, `like_count`, `url`)
   - `config`: the user's preferences
   - `prompts`: summarization instructions
   - `stats`: counts of content available
3. Based on the user's `granularity` setting, generate the digest:

### Granularity: highlights
For each podcast with a transcript: 1-2 sentence takeaway.
For each Twitter account with tweets: the single most important tweet or insight.
Total digest: fits in one screen.

### Granularity: summary
For each podcast: 3-5 sentence summary covering core claims, data points, and implications.
For each Twitter account: 2-3 sentence summary of their key themes, with 1 notable tweet quoted.
Total digest: 2-3 screens.

### Granularity: full
For each podcast: structured analysis — Core Conclusions (3-5 sentences), Key Data, Notable Quotes (verbatim), and implications.
For each Twitter account: all significant tweets grouped by theme, with engagement metrics and links.
Total digest: comprehensive reference document.

4. Apply the user's `language` setting:
   - `en`: English output
   - `zh`: Chinese output (translate English content)
   - `bilingual`: English original with Chinese translation below each section

5. Filter by `domains`: only include content tagged with the user's selected domains.

6. Deliver the digest:
   - `stdout`: just print it in the chat
   - `telegram`: run `python scripts/deliver.py --message "DIGEST_TEXT"`
   - `feishu`: run `python scripts/deliver.py --message "DIGEST_TEXT"`
   - `email`: run `python scripts/deliver.py --message "DIGEST_TEXT"`

## Modifying preferences

Users can change settings conversationally:
- "Switch to Chinese" → update `language` in config
- "Make it more detailed" → change `granularity` to `full`
- "Only show me AI stuff" → update `domains` to `["ai"]`
- "Push to Telegram instead" → update delivery method

## Customizing prompts

Users can customize how content is summarized:
- "Make summaries more concise" → update `~/.ai-signal/prompts/summarize-podcast.md`
- "Focus on actionable insights" → update prompts
- "Use a more casual tone" → update `~/.ai-signal/prompts/digest-intro.md`

## Content sources

Central feed is updated daily at 6am UTC with content from:
- **15 podcast channels**: Dwarkesh Patel, Lex Fridman, Latent Space, All-In, a16z, No Priors, Lenny's Podcast, Google DeepMind, Lightcone (YC), Macro Voices, Super-Spiked, Columbia Energy Exchange, Invest Like the Best, Capital Allocators, The Acquirers Podcast
- **20 Twitter/X accounts**: Karpathy, Swyx, Dylan Patel, Sam Altman, Dario Amodei, Amanda Askell, Boris Cherny, Alex Albert, Guillermo Rauch, Amjad Masad, Javier Blas, and more

No API keys needed for content — all feeds are fetched centrally.
