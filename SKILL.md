---
name: ai-signal
description: AI Signal daily digest for Agent users — tracks top AI builders on X, podcasts, and arXiv papers, then remixes central JSON feeds into a personalized digest. Use when the user wants AI/investing insights or invokes /ai-signal. No content API keys required.
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
posts, podcast transcripts/descriptions, arXiv papers) is fetched centrally and
served via public JSON feeds. Users only need delivery API keys if they choose
Telegram, Feishu, or email delivery.

Default mode is **JSON-first**. Do not depend on central Chinese summaries.
Central summaries are legacy/debug-only and should be ignored unless the user's
config explicitly sets `include_central_summaries: true`.

## Auto-Install (Zero Command Line)

When a user asks you to install ai-signal (e.g. "帮我安装 https://github.com/Benboerba620/ai-signal"
or "set up ai signal"), run these steps automatically — the user should NOT need
to touch the terminal:

1. Detect platform and choose install path:
   - OpenClaw: `~/skills/ai-signal`
   - Claude Code: `~/.claude/skills/ai-signal`
   - Other: `~/ai-signal`

2. Clone and install:
```bash
git clone https://github.com/Benboerba620/ai-signal.git <install_path>
cd <install_path>/scripts && pip install -r ../requirements.txt
```

3. If clone or install fails, diagnose and retry (missing git? missing pip?
   network issue?). Fix it yourself — do not ask the user to run commands.
   If github.com is unreachable (common in mainland China without a proxy),
   retry the clone through a mirror prefix, e.g.
   `git clone https://gh-proxy.com/https://github.com/Benboerba620/ai-signal.git <install_path>`
   (or another gh-proxy-style service if that one is down). Daily feed
   fetching does NOT need a proxy afterwards — prepare_digest.py falls back
   to the jsDelivr CDN mirror automatically.

4. Proceed directly to the Onboarding flow below.

The user's only action is telling you to install. Everything else is your job.

---

## Detecting Platform

Before doing anything, detect which platform you're running on. The question
that matters is: **can you, the Agent, schedule a task that re-invokes yourself
daily?**

```bash
which openclaw 2>/dev/null && echo "PLATFORM=openclaw" || echo "PLATFORM=other"
```

- **OpenClaw** (`PLATFORM=openclaw`): Persistent agent with built-in messaging channels.
  Delivery is automatic via OpenClaw's channel system. Cron uses `openclaw cron add`.

- **Other persistent agent** (e.g. Tencent WorkBuddy or any platform with a
  scheduled-task / 定时任务 feature that re-runs the Agent — not just a bare
  shell command): treat yourself as persistent. In Step 8, use your platform's
  scheduler and make the scheduled instruction "run the ai-signal skill digest
  workflow", so the Agent remix step is included in every scheduled run.

- **Non-persistent** (Claude Code, Cursor, Codex, etc.): can generate digests
  on demand only. Do not set a plain system cron that pipes JSON directly to
  delivery; that skips the Agent remix and sends raw JSON.

Save it in config.json as `"platform": "openclaw"`, `"platform": "persistent"`,
or `"platform": "other"`.

**Windows note:** the bash snippets in this file are examples, not literal
requirements. On Windows, translate them to PowerShell (write files with your
file-writing tool instead of heredocs; use `$env:TEMP` instead of `/tmp`; the
command is `python`, not `python3`). The Python scripts themselves are
cross-platform.

---

## First Run — Onboarding

Check if `~/.ai-signal/config.json` exists and has `onboardingComplete: true`.
If NOT, run the onboarding flow.

**Hard rule: ask Steps 2–6 as separate questions, in order. Do not skip or
merge any of them.** In particular, always ask Step 2 (frequency + delivery
time + timezone) even if you cannot schedule tasks yourself — save the answers
to config.json anyway; they take effect as soon as the user runs this skill on
a platform with a scheduler. Skipping the delivery-time question is the most
common onboarding mistake.

### Step 1: Introduction

Tell the user:

"我是你的 AI Signal 日报。我追踪 AI 一线的声音——做事的人、写代码的人、
下注的人，不是二手转述。

目前我追踪：
- [N] 个 Twitter/X 账号（分析师、决策者、建造者）
- [M] 个播客频道
- arXiv 最新 AI/ML/NLP 论文

这些信息源由中央统一维护，自动更新，你不需要做任何事。"

(Replace [N] and [M] with actual counts from sources.json)

### Step 2: Frequency

Ask: "你希望多久收到一次？"
- 每天（推荐）
- 每周

Then ask: "几点推送？你在哪个时区？"
(Example: "早上 8 点，北京时间" → deliveryTime: "08:00", timezone: "Asia/Shanghai")

For weekly, also ask which day.

### Step 3: Language

Ask: "你希望用什么语言？"
- 中文（翻译英文内容）→ save as `"language": "zh"`
- English → save as `"language": "en"`
- 双语（中英对照，逐段交替）→ save as `"language": "bilingual"`

Do not save display labels such as `"中文"` or `"English"` if you can avoid it.
If they already exist, `prepare_digest.py` will normalize them, but canonical
config values are `zh`, `en`, and `bilingual`.

### Step 4: Granularity

Ask: "你希望什么详细程度？"
- **精华** — 每条内容 1-2 句话，一屏看完
- **标准**（推荐）— 每条 3-5 句话，重点数据 + 关键观点
- **完整** — 结构化分析，含原文引用和数据

### Step 5: Domains

Ask: "你关注哪些领域？"
- AI（播客 + 推特 + 论文）
- 投资（播客 + 推特）
- 全部（推荐）

### Step 6: Delivery Method

**If OpenClaw:** SKIP this step. OpenClaw delivers via its built-in channels.
Set `delivery.method` to `"stdout"` and move on.

**If another persistent agent (WorkBuddy etc.) with its own chat channel:**
same as OpenClaw — set `delivery.method` to `"stdout"` and let the scheduled
Agent run deliver the digest in its own channel. Only configure Telegram/Feishu/
email if the user explicitly wants delivery outside the platform.

**If non-persistent agent (Claude Code, Cursor, etc.):**

Tell the user:

"你现在不是在持久化 Agent 上，所以我可以帮你生成当下这份日报，但不能保证每天自动运行。

如果你想每天自动收到，需要使用支持定时任务的 Agent（例如 OpenClaw）。如果只是手动查看，每次输入 /ai-signal 就行。"

You may still configure Telegram, Feishu, or email as a delivery target for
manual runs, but do not promise unattended daily delivery unless a persistent
Agent scheduler is available.

**If Telegram:**
Guide step by step:
1. 打开 Telegram 搜索 @BotFather
2. 发送 /newbot，取个名字（如 "AI Signal"）
3. 取个 username（如 "my_aisignal_bot"），必须以 bot 结尾
4. BotFather 会给你一个 token（如 "7123456789:AAH..."），复制下来
5. 打开你的新 bot 对话，随便发一条消息（如 "hi"）——**必须先发消息，否则推送不了**

然后获取 chat ID:
```bash
curl -s "https://api.telegram.org/bot<TOKEN>/getUpdates" | python3 -c "import sys,json; d=json.load(sys.stdin); print(d['result'][0]['message']['chat']['id'])" 2>/dev/null || echo "没找到消息——确认你已经给 bot 发了一条消息"
```

Save token to `.env`, chat ID to config.json.

**If Feishu:**
Guide step by step:
1. 在飞书群里添加一个自定义机器人
2. 复制 webhook URL（格式如 `https://open.feishu.cn/open-apis/bot/v2/hook/xxx`）

Save webhook URL to config.json `delivery.webhook_url`.

**If Email:**
Ask for email address, then guide Resend setup:
1. 访问 https://resend.com 注册（免费版每天 100 封，够用）
2. 在 Dashboard 创建 API Key，复制下来

Save API key to `.env`, email to config.json.

**If on-demand:**
Set `delivery.method` to `"stdout"`. Tell them:
"好的，每次想看时输入 /ai-signal 就行。"

### Step 7: Save Config & API Keys

```bash
mkdir -p ~/.ai-signal
```

Save config:
```bash
cat > ~/.ai-signal/config.json << 'EOF'
{
  "platform": "<openclaw or other>",
  "language": "<en, zh, or bilingual>",
  "granularity": "<highlights, summary, or full>",
  "domains": ["ai", "invest"],
  "timezone": "<IANA timezone>",
  "frequency": "<daily or weekly>",
  "deliveryTime": "<HH:MM>",
  "weeklyDay": "<day, only if weekly>",
  "delivery": {
    "method": "<stdout, telegram, feishu, or email>",
    "chat_id": "<telegram chat ID, only if telegram>",
    "webhook_url": "<feishu webhook, only if feishu>",
    "email": "<email address, only if email>"
  },
  "onboardingComplete": true
}
EOF
```

If Telegram or Email, save API key:
```bash
cat > ~/.ai-signal/.env << 'EOF'
# Only uncomment the one you need
# TELEGRAM_BOT_TOKEN=paste_your_token_here
# RESEND_API_KEY=paste_your_key_here
EOF
```

### Step 8: Set Up Cron

**OpenClaw:**

Build cron expression from user preferences (daily 8am → `"0 8 * * *"`).

Detect current channel and target ID, then:
```bash
openclaw cron add \
  --name "AI Signal" \
  --cron "<cron expression>" \
  --tz "<user timezone>" \
  --session isolated \
  --message "Run the ai-signal skill: execute prepare_digest.py, remix the content into a digest following the prompts, then deliver via deliver.py" \
  --announce \
  --channel <channel name> \
  --to "<target ID>" \
  --exact
```

Verify with:
```bash
openclaw cron list
openclaw cron run <jobId>
```

Wait for test run to complete before proceeding.

**Other persistent agent (WorkBuddy etc.):**

Create a scheduled task with your platform's own scheduler at the user's
`deliveryTime` / `timezone`. The scheduled instruction must re-invoke the Agent
with: "Run the ai-signal skill: execute prepare_digest.py, remix the content
into a digest following the prompts, then deliver it." Run it once as a test
before confirming to the user.

**Non-persistent agent:**

Do not create a system cron or Windows Task Scheduler job that runs
`prepare_digest.py | deliver.py`. That delivers raw JSON and bypasses the Agent.
Set `delivery.method` to `"stdout"` by default and tell the user:
"每次想看时输入 /ai-signal。我会读取最新 JSON，然后在这里生成日报。"

**Non-persistent agent + on-demand only:**
Skip cron. Tell the user: "每次想看时输入 /ai-signal 就行。"

### Step 9: Welcome Digest

**DO NOT skip this step.** Immediately generate the first digest so the user
sees what it looks like.

"让我现在就生成今天的内容，你先看看效果。"

Run the full Content Delivery workflow below. After delivering, ask:

"这是你的第一份 AI Signal！
- 长度合适吗？想要更短还是更长？
- 有什么想多看或少看的？
告诉我，我来调整。"

Then confirm their next automatic delivery time (or remind them to use /ai-signal).

---

## Content Delivery — Digest Run

This workflow runs when a persistent Agent scheduler triggers it, or when the
user invokes `/ai-signal` manually.

### Step 1: Load Config

Read `~/.ai-signal/config.json` for user preferences.

### Step 2: Run prepare script

```bash
cd ${SKILL_DIR}/scripts && python prepare_digest.py 2>/dev/null
```

The script writes the full content to files and prints a **small JSON manifest**
to stdout (a few KB — safe to read in any agent). The manifest contains:
- `payload_file` — absolute path to `payload.json` (full content minus transcripts)
- `config` — user's language, granularity, domains, delivery preferences
- `output_contract` — mandatory generation contract, especially language rules
- `feed_sources` — whether each feed came from GitHub raw (`remote`) or local cache
- `stats` — content counts
- `podcasts` — episode list with `transcript_file` paths and sizes
- `x_accounts` — accounts that have new tweets
- `seen_filter` — items already delivered before are filtered out automatically
- `delivery_mark_file` — item IDs to mark after the digest is successfully delivered
- `warnings` — stale feed or local cache warnings; show these to the user
- `errors` — non-fatal issues (IGNORE these)

Then read the actual content **from files, not stdout**:
1. Read `payload_file` (payload.json) with your file-reading tool — it has all
   tweets, paper titles/abstracts, podcast metadata, and prompts.
2. For each podcast episode you cover, read its `transcript_file`. Transcripts
   can be 100K+ characters — read in chunks (offset/limit) if your tool needs it,
   and for long transcripts it is fine to read enough to extract the core
   arguments rather than every line.

If `feed_sources` shows any feed with `source: "local_cache"` or `is_stale: true`,
or if `warnings` mentions stale/local cache data, tell the user before the digest
that the affected feed may not be the latest. Do not present local cache data as
today's fresh feed.

Per-user dedup reads `~/.ai-signal/seen.json`, but `prepare_digest.py` does **not**
mark items as seen by default. Only mark after the digest is actually shown or
sent successfully. This prevents a failed generation/delivery from hiding items
the user never saw.

If the user asks to regenerate today's digest ("重新生成" / "再看一遍今天的"), run:

```bash
cd ${SKILL_DIR}/scripts && python prepare_digest.py --include-seen 2>/dev/null
```

If the script fails entirely (no JSON output), tell the user to check internet.

### Step 3: Check for content

If all counts are 0 (no tweets, no episodes, no papers), tell the user:
"今天暂无更新，明天再看！" Then stop.

### Step 4: Filter by domains

Only include content matching the user's `config.domains`:
- `"ai"` domain: AI-related podcasts, AI builders' tweets, all arXiv papers
- `"invest"` domain: investing podcasts, investing-related tweets

### Step 5: Remix content

**Your ONLY job is to remix content from the payload files.** Do NOT fetch
anything from the web, visit URLs, or call APIs. Everything is in payload.json
and the transcript files.

Before writing the digest, read `output_contract` and obey it as the highest
priority instruction in this payload. If `output_contract.language.must_translate`
is true, translate all user-facing analysis and summaries into the requested
language. The original tweet text, titles, product names, company names, model
names, technical terms, and URLs may remain in English when appropriate.

Use the raw JSON fields as the source of truth:
- X/Twitter: use each tweet's original `text` and `url`.
- Podcasts: read each episode's `transcript_file` when present; otherwise use
  `description`.
- Papers: use each paper's `title`, `abstract`, `abs_url`, and `pdf_url`.
- If `central_summaries` exists, treat it only as optional reference material,
  not as the canonical source.

Read prompts from the `prompts` field:
- `prompts.digest_intro` — overall framing
- `prompts.summarize_podcast` — how to remix podcasts
- `prompts.summarize_tweets` — how to remix tweets
- `prompts.summarize_papers` — how to remix arXiv papers
- `prompts.translate` — how to write Chinese or bilingual output

**Tweets (process first):**
Process selected tweets one by one. Each selected tweet should be its own item.
For Chinese output, translate short tweets directly and keep the original text
plus URL. Only summarize when the tweet/thread is long enough that translation
alone would be unwieldy. Every tweet MUST include its `url`.

**Podcasts (process second):**
For each episode, summarize according to granularity:
- highlights: 1-2 sentence takeaway
- summary: 3-5 sentences covering core claims and data
- full: structured analysis with Key Data, Notable Quotes, implications
Use `channel`, `title`, `link` from the JSON — NOT from transcript text.

**Podcast follow-up expansion:**
The digest is only the first filter. When the user asks to expand a podcast
("展开第 2 个播客" / "把 Vercel agents 这期做 breakdown" / "深读这期播客"),
use the existing `payload_file` and the episode's `transcript_file` when
available. Do not fetch the web. Produce a deeper breakdown in the user's
language with:
- one-sentence thesis
- core claims
- argument chain
- key evidence or quotes that are actually present in the transcript
- practical implications for AI products, infrastructure, research, or investing
- questions worth verifying

At the end of every digest, before delivery attribution, add one short line
telling the user they can pick any podcast, tweet, or paper to expand. For
Chinese output, use wording like: "想深读的话，可以直接说：展开第 2 个播客。"

**Papers (process third):**
For each arXiv paper, summarize according to granularity:
- highlights: one sentence on key contribution
- summary: 2-3 sentences on problem, approach, result
- full: Problem / Approach / Results / Significance, with benchmark numbers
Include `abs_url` for each paper. Group by theme when papers overlap.

**ABSOLUTE RULES:**
- NEVER invent or fabricate content. Only use what's in the JSON.
- Every piece of content MUST have its URL. No URL = do not include.
- Do NOT visit x.com, arxiv.org, or any website.

### Step 6: Apply language

Read `config.language`:
- **"en":** Entire digest in English.
- **"zh":** Entire digest in Simplified Chinese. Translate all English content
  that you write for the user. Keep original tweet text and links under an
  "原文" label, but do not leave analysis, summaries, section headings, or
  explanations in English.
- **"bilingual":** Interleave English and Chinese paragraph by paragraph.
  For each section: English version, then Chinese translation directly below.
  Do NOT output all English first then all Chinese.

If the user selected Chinese and your draft is mostly English, rewrite it before
delivery. That is a failed digest, not a valid English fallback.

### Step 7: Deliver

Read `config.delivery.method`:

**If "telegram", "feishu", or "email":**
```bash
echo '<digest text>' > /tmp/ai-signal-digest.txt
cd ${SKILL_DIR}/scripts && python deliver.py --file /tmp/ai-signal-digest.txt --mark-delivered-file "<delivery_mark_file>" 2>/dev/null
```
If delivery fails, show the digest in terminal as fallback.

**If "stdout" (default):**
Output the digest directly. After the digest has been written to the user,
confirm delivery state with:
```bash
cd ${SKILL_DIR}/scripts && python mark_delivered.py --file "<delivery_mark_file>" 2>/dev/null
```

Do not run `mark_delivered.py` if digest generation failed or the content was
not shown/sent.

---

## Configuration Handling

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

## Manual Trigger

When the user invokes `/ai-signal` or asks for their digest:
1. Skip cron — run immediately
2. Same fetch → remix → deliver flow
3. Tell the user you're fetching fresh content

---

## Content Sources

Central feed is updated daily at 6am Beijing time (UTC 22:00) with:

### Podcasts (13 channels)
Dwarkesh Patel, Lex Fridman, Latent Space, All-In Podcast, a16z, No Priors,
SemiAnalysis (Dylan Patel), Google DeepMind, Lightcone (YC), Lenny's Podcast,
Invest Like the Best, Capital Allocators, The Acquirers Podcast

### Twitter/X (14 accounts)
**Analysts:** Karpathy, Swyx, Dylan Patel (SemiAnalysis), Leopold Aschenbrenner, Jim Keller
**Executives:** Sam Altman, Dario Amodei
**Builders:** Amanda Askell, Boris Cherny (Claude Code), Cat Wu, Alex Albert, Guillermo Rauch (Vercel), Amjad Masad (Replit), Josh Woodward (Google Labs)

### arXiv Papers (daily, up to 30)
cs.AI (Artificial Intelligence), cs.CL (Computation and Language), cs.LG (Machine Learning)

All feeds are fetched centrally. **No API keys needed for content.**
