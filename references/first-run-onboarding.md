# First Run — Onboarding

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

Then ask: "几点推送？你在哪个时区？（默认早上 7:30）"
(Example: "早上 8 点，北京时间" → deliveryTime: "08:00", timezone: "Asia/Shanghai")

**Default: `deliveryTime: "07:30"`, `timezone: "Asia/Shanghai"`.** If the user
says "默认" / "都行" / doesn't give a time, use 07:30 Beijing time. The central
feed regenerates daily at 06:00 Beijing time (22:00 UTC), so 07:30 delivery
picks up the freshest X, podcast, and blog feeds. arXiv gets a second weekday
refresh around 09:30 Beijing time after its daily release; deliveries before
then may contain the previous paper batch. If the user gives a timezone but no
time, default to 07:30 in their timezone.

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
4. BotFather 会给你一个 token，复制下来
5. 打开你的新 bot 对话，随便发一条消息（如 "hi"）——**必须先发消息，否则推送不了**

然后通过 Telegram Bot API 的 `getUpdates` 获取 chat ID。使用不会记录请求
URL 或凭证的 HTTP 客户端，从最新消息的 `message.chat.id` 字段取值。
如果没有返回消息，让用户先给 bot 发一条消息后重试。不要在终端或聊天
中回显 token。

Save token to `.env` without displaying it, and save chat ID to config.json.

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

**Anonymous count (tell, do not ask):** Say once, in the user's language:
"AI Signal 每天会匿名报到一次（只下载一个小文件，不带任何个人信息），用来统计大约有多少人在用。不想要的话说一声'关掉匿名统计'就行。"
If the user declines, save `"anonymous_ping": false`.

**Deep-read model (ask):** Ask: "播客和文章旁边会有一个「🔍 一键精读」，点了用哪个模型？"
- DeepSeek（推荐，默认）：点了会复制精读提示词和全文，打开 DeepSeek 后粘贴发送 → `"deep_read": "deepseek"`
- Kimi：打开 Kimi，提示词已经填好 → `"kimi"`
- Claude：打开 Claude，提示词已经填好 → `"claude"`
- ChatGPT：打开 ChatGPT，提示词已经填好 → `"chatgpt"`
- 不要这个链接 → `"off"`
Tell the user the jump page also shows buttons for the other models, so the
choice only sets the default.

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
  "anonymous_ping": true,
  "deep_read": "deepseek",
  "onboardingComplete": true
}
EOF
```

If Telegram or Email is selected, use the Agent's file-writing tool to create
`~/.ai-signal/.env` without printing its contents. Store only the required key:
`TELEGRAM_BOT_TOKEN` or `RESEND_API_KEY`. Restrict the file to the current user
when the platform supports file permissions.

### Step 8: Set Up Cron

**OpenClaw:**

Build cron expression from user preferences (default daily 7:30am → `"30 7 * * *"`; e.g. daily 8am → `"0 8 * * *"`).

Detect current channel and target ID, then:
```bash
openclaw cron add \
  --name "AI Signal" \
  --cron "<cron expression>" \
  --tz "<user timezone>" \
  --session isolated \
  --timeout-seconds 900 \
  --message "Run the ai-signal skill: execute prepare_digest.py, remix the content into a digest following the prompts, then deliver via deliver.py" \
  --announce \
  --channel <channel name> \
  --to "<target ID>" \
  --exact
```

**`--timeout-seconds 900` is recommended.** The daily digest no longer downloads
full podcast transcripts, but it still processes several feeds and may generate
dozens of summaries. A generous limit prevents slow model calls or networks from
being killed and relaunched mid-run.

Also check that the agent-turn timeout is not shorter than the cron budget
(some users lower it globally):
```bash
openclaw config get agents.defaults.timeoutSeconds
```
If it prints a value below 900, raise it:
```bash
openclaw config set agents.defaults.timeoutSeconds 900
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

If the platform lets you set a per-task time limit, set it to **at least 10
minutes (15 recommended)**. The transcript payload is now on demand, but feed
fetching and Agent generation can still be slow enough that a short limit kills
and relaunches the task before delivery.

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
