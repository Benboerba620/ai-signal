# Changelog

记录 AI Signal 面向用户的变更。每日的 feed 数据更新（`Feed update` commit）不在此列。

## 2026-07-05

### 新增

- X/Twitter 抓取加入主题过滤：节日祝福、生活动态、纯社交回复等非 AI 信号不再进入 feed。
  之所以这样改：7 月 4 日美国国庆的刷屏推挤占了 feed 位额。
- 过滤关键词用当日真实 feed 校准：覆盖主流模型名（Claude / Fable / GPT / Gemini 等）、AIE、CLI 等一线语汇，宁可多留不错杀，互动排序兜底。

### 文档

- 新增本 CHANGELOG 与 README「最近更新」段。

## 2026-07-04

### 修复

- 用户安装依赖瘦身：`requirements.txt` 只剩 `httpx[socks]`；中央抓取依赖（twscrape 等）移到 `requirements-central.txt`。
  之所以这样改：新用户在 macOS 系统 Python 3.9 上装 twscrape 直接失败；SOCKS 代理环境缺 socksio 时，远端 feed 拉取会静默回退到本地缓存。
- 定时任务恢复每日刷新中央摘要缓存（feed-summaries.json 此前停更了三天）。

### 改进

- manifest 新增 `feed_sources`：标注每个 feed 来自远端还是本地缓存、是否过期，Agent 可如实提示用户。
- 日报支持播客深读展开（"展开第 2 个播客"），有全文字幕时优先读 transcript。

## 2026-07-03

### 修复

- 已读标记改为「日报确认展示 / 送达后」才写入 seen.json，生成或推送失败不再吞掉用户没看过的内容。

## 2026-07-02

### 新增

- 播客无公开字幕时的 ASR 转录兜底（火山引擎），并拒绝把 show notes 误当成 transcript。
