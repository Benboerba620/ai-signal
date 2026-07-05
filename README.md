# AI Signal

追踪 AI 一线的声音——做事的人、写代码的人、下注的人，不是二手转述。

这是一份给 AI Agent 用户的精心筛选信息源。中央每天自动抓取播客、推文和论文；你的 Agent 读取 JSON，按你的口味生成日报。

**这份清单本身就是产品。**

## 最近更新

- `2026-07-05`：feed 拉取加多源镜像——GitHub 不可达时自动切 jsDelivr CDN，大陆无代理用户也能每天收到更新
- `2026-07-05`：推文加主题过滤，节日祝福 / 生活动态等噪音不再进 feed
- `2026-07-04`：安装瘦身——用户侧只需 `httpx[socks]`；修复 SOCKS 代理下拉取失败与 Python 3.9 装不上
- `2026-07-03`：已读标记改为送达确认后写入，生成失败不再丢内容

完整历史见 [CHANGELOG](CHANGELOG.md)。

---

## 你会得到什么

由你的 AI Agent 读取中央 JSON 后生成一份日报（可直接在聊天里看；如果你的 Agent 支持定时任务，也可以每天自动推送），包含：

- 一线播客的最新内容（含全文字幕，不是摘要的摘要）
- 精选推特账号的当日观点
- arXiv 最新 AI/ML/NLP 论文标题、链接和摘要原文
- 按你的偏好定制：中文 / 英文 / 双语，精华 / 标准 / 完整
- 不需要内容 API key——所有内容由中央服务统一抓取

> AI Signal 是 **Agent-first** 架构：中央只供料，不替每个用户生成最终日报。真正的总结、翻译、格式定制，都由用户自己的 Agent 完成。

## 日报不是终点

日报只是第一层筛选。看完以后你可以继续让 Agent 展开任意一条内容，尤其是长播客：

- “展开第 2 个播客”
- “把 Vercel agents 这期做一个 breakdown”
- “这期播客按核心观点、论证链、关键引用、投资含义展开”

如果该播客有全文字幕，Agent 会优先读取 transcript 文件，而不是只根据标题或简介发挥。

## 信息源

### 播客（13 个频道）

| 频道 | 为什么选 |
|------|----------|
| [Dwarkesh Patel](https://www.dwarkesh.com) | 最深度的 AI 一对一访谈，嘉宾全是一线研究者 |
| [Lex Fridman](https://lexfridman.com/podcast/) | 覆盖面最广的 AI 长对话 |
| [Latent Space](https://www.latent.space) | AI 工程师生态的脉搏，Swyx 主理 |
| [All-In Podcast](https://www.allinpodcast.co) | 四个顶级 VC 的周度辩论，AI + 宏观 |
| [a16z](https://a16z.com/podcasts/) | 硅谷最大 VC 的一手投资视角 |
| [No Priors](https://www.youtube.com/@NoPriorsPodcast) | Sarah Guo + Elad Gil，AI infra 创始人密度最高 |
| [SemiAnalysis](https://www.youtube.com/@SemiAnalysis) | Dylan Patel，半导体与 AI 基础设施最深度的独立分析 |
| [Google DeepMind](https://deepmind.com/podcast) | DeepMind 官方，前沿研究视角 |
| [Lightcone (YC)](https://www.youtube.com/@ycombinator) | YC 合伙人看 AI 创业生态 |
| [Lenny's Podcast](https://www.lennysnewsletter.com/) | AI 产品落地的一线反馈 |
| [Invest Like the Best](https://www.joincolossus.com/episodes) | 顶级投资人的思维框架 |
| [Capital Allocators](https://capitalallocators.com/podcast/) | 机构投资者视角 |
| [The Acquirers Podcast](https://acquirersmultiple.com/podcast/) | 价值投资方法论 |

### Twitter/X（14 个账号）

**分析师/研究者**：[@karpathy](https://x.com/karpathy)、[@swyx](https://x.com/swyx)、[@dylanpatel_](https://x.com/dylanpatel_)（SemiAnalysis）、[@leopoldaob](https://x.com/leopoldaob)、[@jimkeller_](https://x.com/jimkeller_)

**决策者**：[@sama](https://x.com/sama)、[@DarioAmodei](https://x.com/DarioAmodei)

**建造者**：[@AmandaAskell](https://x.com/AmandaAskell)、[@bcherny](https://x.com/bcherny)（Claude Code）、[@_catwu](https://x.com/_catwu)、[@alexalbert__](https://x.com/alexalbert__)、[@rauchg](https://x.com/rauchg)（Vercel）、[@amasad](https://x.com/amasad)（Replit）、[@joshwoodward](https://x.com/joshwoodward)（Google Labs）

> 选人标准：在一线做事 / 有独立判断 / 用真金白银下注。不选搬运号、评论员、流量账号。

### arXiv 论文（每日最多 30 篇）

| 分类 | 覆盖范围 |
|------|----------|
| cs.AI | 人工智能 |
| cs.CL | 计算语言学（LLM / NLP 论文主阵地） |
| cs.LG | 机器学习 |

> 每天抓取最近 24 小时新提交的论文标题 + 摘要，订阅者的 AI 按需生成解读。

## 快速开始

打开你的 AI Agent（OpenClaw / Claude Code / Cursor / WorkBuddy / Codex 等），说一句话：

> **帮我安装 https://github.com/Benboerba620/ai-signal**

AI 会自动完成安装，然后引导你设置推送频率和时间、语言、详细程度和输出方式。设置完**立刻生成第一份日报**。

不需要敲命令、不需要内容 API key。你需要一个能运行这个 skill 的 AI Agent。

<details>
<summary>手动安装（如果你的 Agent 不支持自动安装）</summary>

```bash
# OpenClaw
git clone https://github.com/Benboerba620/ai-signal.git ~/skills/ai-signal
cd ~/skills/ai-signal/scripts && pip install -r ../requirements.txt

# Claude Code
git clone https://github.com/Benboerba620/ai-signal.git ~/.claude/skills/ai-signal
cd ~/.claude/skills/ai-signal/scripts && pip install -r ../requirements.txt

# 其他
git clone https://github.com/Benboerba620/ai-signal.git
cd ai-signal/scripts && pip install -r ../requirements.txt
```

**国内网络 clone 失败？** 用镜像加速前缀（示例，失效就换一个同类服务）：

```bash
git clone https://gh-proxy.com/https://github.com/Benboerba620/ai-signal.git
# 或
git clone https://ghfast.top/https://github.com/Benboerba620/ai-signal.git
```

安装后的每日 feed 更新不依赖代理：GitHub 直连失败时自动切换 jsDelivr CDN 镜像。

安装完成后告诉你的 Agent：**"set up ai signal"**

</details>

## 定制

所有偏好都可以用对话修改：

| 设置 | 选项 | 对话示例 |
|------|------|----------|
| 语言 | 中文 / 英文 / 双语 | "切换成中文" |
| 详细程度 | 精华 / 标准 / 完整 | "我要更详细的" |
| 领域 | AI / 投资 | "只看 AI 的" |
| 推送 | Telegram / 飞书 / 邮件 / 聊天 | "推到 Telegram" |

### 自定义摘要风格

编辑 `~/.ai-signal/prompts/` 下的文件：

- `summarize-podcast.md` — 播客怎么总结
- `summarize-tweets.md` — 推文怎么提炼
- `summarize-papers.md` — 论文怎么摘要
- `digest-intro.md` — 整体语气和格式

纯文本指令，不是代码。改完下次推送生效。

## 工作原理

```
中央服务（本 repo，GitHub Actions 每天自动跑）
  └── generate_feed.py
      → 抓推文原文 + 播客 RSS + YouTube 全文字幕 + arXiv 论文
      → X/Twitter 先做主题过滤，丢掉节日祝福、生活动态、纯社交表达等非 AI 信号
      → feed-x.json、feed-podcasts.json、feed-arxiv.json（commit 到 repo）
      → 同步生成 feed-summaries.json 中文摘要缓存（如果配置了 LLM keys）
      → feed = 最近 24-72 小时的滚动窗口快照，无状态，多跑几次也不会丢内容

你的机器（任意 AI Agent：OpenClaw / Claude Code / Cursor / WorkBuddy / Codex）
  └── prepare_digest.py → 从本 repo 拉 feed（多源：GitHub raw → jsDelivr CDN，大陆无代理也可用）
      → 读取 ~/.ai-signal/seen.json，过滤你已经看过的内容
      → 如果中央摘要缓存比 raw feed 旧，自动忽略旧摘要
      → manifest 标明每个 feed 来自 GitHub raw 还是本地缓存，并提示是否过期
      → 完整内容写到 ~/.ai-signal/payload/（payload.json + 每集单独的 transcript 文件）
      → stdout 只输出一个几 KB 的 manifest，任何 Agent 都能直接读
      → 你的 AI 按需读取文件，按你的偏好生成摘要
      → 直接显示，或由支持定时/推送的 Agent 发送到 Telegram / 飞书 / 邮件
      → 确认展示/发送成功后，mark_delivered.py 才把本次内容写入 seen.json
```

**你不需要任何内容 API key。** 内容抓取在中央完成，摘要由你自己的 AI Agent 读取 JSON 后生成。

默认是 **JSON-first**：中央只提供原始 feed，不生成中文版日报。这能减少中文、emoji、长播客字幕在命令行、定时任务和推送链路里的编码问题。中央 LLM 摘要能力仍保留为手动调试选项，但不是默认用户路径。

## 要求

- 一个 AI Agent（OpenClaw、Claude Code、Cursor、WorkBuddy、Codex 等均可）
- 网络连接（拉取中央 feed；不需要 VPN——GitHub 不可达时自动走 jsDelivr CDN 镜像）

就这些。不需要内容 API key。所有内容由中央统一抓取，每天自动更新。若要无人值守地每天自动收到，需要使用支持定时任务的 Agent；普通非持久 Agent 更适合手动输入 `/ai-signal` 查看。

## 隐私

- 不采集任何用户数据
- 你的配置和偏好只存在你自己的机器上（`~/.ai-signal/`）
- 只聚合公开内容（公开推文、公开播客、公开论文）

## 关于

这份清单来自一个二级市场研究员的日常信息源。筛选标准只有一个：**这个人说的话，值不值得我每天花时间看。**

公众号「奔波儿r」· [GitHub](https://github.com/Benboerba620)

## License

MIT
