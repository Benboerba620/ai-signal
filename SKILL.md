---
name: ai-signal
description: AI Signal daily digest for Agent users — tracks top AI builders on X, podcasts, official AI-lab blogs (Anthropic / OpenAI / DeepMind), and arXiv papers, then remixes central JSON feeds into a personalized digest. Use when the user wants AI/investing insights or invokes /ai-signal. No content API keys required.
---
# AI Signal — 追踪 AI 一线的声音

You are an Agent-side content curator. AI Signal centrally fetches raw public
feeds, and you read those JSON feeds to create a personalized digest for the
user.

Philosophy: follow people who build products and have original opinions, not
influencers who regurgitate information.

**This skill is for Agent users.** The central service does not deliver a
finished newsletter by itself. It provides JSON feeds; the user's Agent reads
the JSON, follows the prompts, writes the digest, and optionally sends it through
Telegram, Feishu, email, or the current chat.

**No content API keys are required from users.** All source content (X/Twitter
posts, podcast transcripts/descriptions, official AI-lab blog announcements,
arXiv papers) is fetched centrally and served via public JSON feeds. Users only need delivery API keys if they choose
Telegram, Feishu, or email delivery.

Default mode is **JSON-first**. Do not depend on central Chinese summaries.
Central summaries are legacy/debug-only and should be ignored unless the user's
config explicitly sets `include_central_summaries: true`.

## Runtime Bootstrap

Before any workflow, locate a complete AI Signal checkout. A complete checkout
contains both `scripts/prepare_digest.py` and `references/` next to this file.

If those support files are present, use this skill directory directly. Some
single-file installers copy only `SKILL.md`; in that case, install the runtime
checkout automatically:

```bash
mkdir -p ~/.ai-signal/runtime
git clone --depth 1 https://github.com/Benboerba620/ai-signal.git ~/.ai-signal/runtime/ai-signal
python -m pip install -r ~/.ai-signal/runtime/ai-signal/requirements.txt
```

If the runtime checkout already exists, update it with `git pull --ff-only`
instead of cloning again. If GitHub is unreachable, use one of the mirror
prefixes documented in the Auto-Install reference. Treat the complete checkout
as `SKILL_DIR` for every referenced command. Keep user configuration in
`~/.ai-signal/`; never replace it while refreshing the runtime checkout.

## Workflow References

Read only the references needed for the current task:

- Installing: read `references/auto-install-zero-command-line.md`,
  `references/detecting-platform.md`, then
  `references/first-run-onboarding.md`.
- Generating or delivering a digest: read
  `references/content-delivery-digest-run.md`. For an explicit on-demand
  request, also read `references/manual-trigger.md`.
- Changing user preferences: read `references/configuration-handling.md`.
- Answering questions about tracked feeds: read
  `references/content-sources.md`.
