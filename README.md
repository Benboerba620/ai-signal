# AI Signal

追踪 AI 一线的声音——做事的人、写代码的人、下注的人，不是二手转述。

这是一份精心筛选的信息源清单。每天自动抓取这些人的播客和推文原文，你的 AI 帮你按自己的口味生成摘要。

**这份清单本身就是产品。**

---

## 你会得到什么

每天一份推送（Telegram / 飞书 / 邮件 / 直接在聊天里看），包含：

- 一线播客的最新内容（含全文字幕，不是摘要的摘要）
- 精选推特账号的当日观点
- 按你的偏好定制：中文 / 英文 / 双语，精华 / 标准 / 完整
- 不需要任何 API key——所有内容由中央服务统一抓取

## 信息源

### 播客（12 个频道）

| 频道 | 为什么选 |
|------|----------|
| [Dwarkesh Patel](https://www.dwarkesh.com) | 最深度的 AI 一对一访谈，嘉宾全是一线研究者 |
| [Lex Fridman](https://lexfridman.com/podcast/) | 覆盖面最广的 AI 长对话 |
| [Latent Space](https://www.latent.space) | AI 工程师生态的脉搏，Swyx 主理 |
| [All-In Podcast](https://www.allinpodcast.co) | 四个顶级 VC 的周度辩论，AI + 宏观 |
| [a16z](https://a16z.com/podcasts/) | 硅谷最大 VC 的一手投资视角 |
| [No Priors](https://www.youtube.com/@NoPriorsPodcast) | Sarah Guo + Elad Gil，AI infra 创始人密度最高 |
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

## 快速开始

### 安装（Claude Code）

```bash
git clone https://github.com/Benboerba620/ai-signal.git ~/.claude/skills/ai-signal
cd ~/.claude/skills/ai-signal/scripts && pip install -r ../requirements.txt
```

然后告诉 Claude：**"set up ai signal"**——它会引导你设置语言、详细程度和推送方式。

### 手动运行

```bash
python scripts/prepare_digest.py  # 拉最新 feed，输出给 LLM 处理
```

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
- `digest-intro.md` — 整体语气和格式

纯文本指令，不是代码。改完下次推送生效。

## 工作原理

```
中央服务（本 repo，GitHub Actions 每天自动跑）
  └── generate_feed.py
      → 抓推文原文 + 播客 RSS + YouTube 全文字幕
      → feed-x.json、feed-podcasts.json（commit 到 repo）

你的机器（Claude Code skill）
  └── prepare_digest.py → 从本 repo 拉 feed
      → 你的 Claude 按你的偏好生成摘要
      → deliver.py → 推送到你的 Telegram / 飞书 / 邮件
```

**你不需要任何 API key。** 内容抓取在中央完成，摘要由你自己的 Claude 生成。

## 隐私

- 不采集任何用户数据
- 你的配置和偏好只存在你自己的机器上（`~/.ai-signal/`）
- 只聚合公开内容（公开推文、公开播客）

## 关于

这份清单来自一个二级市场研究员的日常信息源。筛选标准只有一个：**这个人说的话，值不值得我每天花时间看。**

公众号「奔波儿r」· [GitHub](https://github.com/Benboerba620)

## License

MIT
