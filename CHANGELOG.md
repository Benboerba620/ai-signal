# Changelog

记录 AI Signal 面向用户的变更。每日的 feed 数据更新（`Feed update` commit）不在此列。

## 2026-07-10

### 修复

- 修复 X/Twitter 转发被误认成账号原创、导致同一条推文重复进入日报：抓取时核验推文真实作者，并在所有追踪账号之间做全局 ID 去重；消费端 `prepare_digest.py` 再做一层批次内去重，即使中央 feed 出现脏数据也不会重复推送。
- GitHub Actions 新增回归测试和 feed 唯一性校验，重复 tweet ID、播客 key、arXiv ID 或博客 ID 会在提交前直接失败。
- 修复 `feed-blogs.json` 未被每日工作流加入 Git 提交的问题，官方博客 feed 现在会随其他 feed 一起更新。

## 2026-07-08

### 新增

- 新增 Naval Ravikant：加入 X/Twitter 账号追踪、YouTube 人物访谈搜索，以及 Naval 自己的 RSS 播客频道（`https://nav.al/feed`）。Naval 频道单独使用 14 天抓取窗口，避免低频长节目被全局 72 小时窗口漏掉；本次已抓入 `Live in the Future`（2026-07-03，`https://nav.al/future`）。

### 修复

- 人物追踪剔除"被谈论但本人没出场"的视频：此前只要名字出现在标题里就算命中，导致记者评论某人的节目混进 feed（实例：Karen Hao 谈 Sam Altman 那集 Democracy Now!，Altman 本人并未出场）。新增标题语法守卫——名字出现在 on/about/versus 的宾语位置（"Journalist Karen Hao **on Sam Altman**"）或跟在 exposes/slams/the truth about 这类评论动词后面的，判为"被谈论"拒收；"a conversation on AGI **with** Sam Altman" 这种 with/ft. 结构仍算本人出场。已在 feed 里的旧误收条目下次运行时同规则清除（`scripts/generate_feed.py`）。
  之所以这样改：产品定义是"27 人上任何播客都会被抓到"，指的是本人开口说话的访谈——被别人高质量地谈论也许值得看，但那是另一个产品。

- 定时任务默认时间预算拉到 15 分钟，修"日报跑到一半被杀、无限重启、永远送不到"：OpenClaw 的 `cron add` 命令模板加上 `--timeout-seconds 900`（并检查 `agents.defaults.timeoutSeconds` 不低于该值）；其他平台（WorkBuddy 等）的定时任务设置里明确要求任务限时 ≥10 分钟（推荐 15）。SKILL.md 另加一节故障排查，症状是"定时日报反复重启不出货"时直接对症到超时预算。
  之所以这样改：日报要通读播客全文字幕（单集可超 10 万字符）——这是刻意设计，全文精读的总结质量更好——正常一轮就可能超过 5 分钟。而"已读"只在成功交付后才标记（防漏内容的正确设计），所以时间预算太短时每次重启都是全量重做，形成死循环。7/8 实测：用户侧 5 分钟截断 + 定时任务反复重启，中央管线全绿，问题全在消费端时间预算。

### 新增

- 推特新增信息源 [@insane_analyst](https://x.com/insane_analyst)（Irrational Analysis）：工程视角的半导体投资分析，Substack 同名。与 Dylan Patel/SemiAnalysis 归同一档（`analyst`），补足半导体产业链一手观点。

### 修复

- arXiv 论文修复"抓不到最新论文"：此前日报里的论文自 7/4 起就冻在 7/2，看似有 30 篇实则 5 天没变。根因是 arXiv API 的 `sortBy=submittedDate` 排序索引会滞后好几天（arXiv 已知问题），返回的"最新"论文其实卡在 3-4 天前；再叠加 48 小时时间窗，把这批 stale 结果全过滤成空，兜底逻辑又默默保留上一版旧 feed，于是长期喂旧数据且无任何报错。改用 `sortBy=lastUpdatedDate`（实时排序）+ 按真实提交日期在窗口内过滤，时间窗从 48h 放宽到 72h（覆盖周末发文淡季）。实测修复后能正常抓到当天最新提交的论文（`scripts/generate_feed.py` / `config/sources.json`）。
  之所以是这个坑：`submittedDate` 和 `lastUpdatedDate` 查的是同一个 arXiv、同一批分类，只换排序字段，结果就从"卡在 3 天前"变成"新鲜到几小时前"——不换排序，光放宽时间窗也没用，因为坏掉的排序压根没把新论文返回给过滤器。

## 2026-07-06

### 新增

- 官方博客追踪：Anthropic / OpenAI / Google DeepMind 官方发布（新模型、产品、研究成果、安全框架）作为第四类信息源进入日报，编号 `B1/B2`，与推文/播客/论文一样可展开。每家每天最多 5 条，48 小时窗口，去重与已读机制复用现有管道（`feeds/feed-blogs.json`，名单在 `config/sources.json` 的 `blogs`）。
  之所以这样加：一手信号里最"一手"的就是实验室自己的发布——此前日报靠推文和播客间接覆盖，官方公告要等别人转述才能进来。
  抓取方式：OpenAI 与 DeepMind 走官方 RSS；Anthropic 没有 RSS，走官方 sitemap.xml + 逐篇文章页提取真实发布日期做时间窗过滤（sitemap 的 lastmod 会被网站重新部署批量刷新，直接用会把 2023 年的旧文当新发布——7/6 实测一次重部署刷了 18 篇旧文的 lastmod，页面可见日期才是可靠锚点）。

### 修复

- 官方博客的日期把关收紧：验证不了真实发布日期的文章一律不进 feed。此前两个口子——RSS 条目缺 pubDate 会被当成新文章放行；Anthropic 文章页的可见日期解析失败时会退回用 sitemap 的 lastmod（恰恰是会被重新部署刷新的不可信字段）——都可能把旧文当新发布推给用户，现已封死。
- 官方博客条目缺摘要时（DeepMind 的 RSS 经常不带 description），自动抓一次文章页取 meta description 补齐；页面本身没有描述的保持标题 + 链接。
- feed 镜像从 2 个扩到 5 个：GitHub raw → jsDelivr 的 4 个 CDN 入口（cdn / fastly / gcore / testingcf）。
  之所以这样改：有大陆无代理用户反馈装好后"没反应"、拉不到数据。raw.githubusercontent.com 在大陆常年被阻断，而 7/5 加的唯一兜底 cdn.jsdelivr.net 自 2022 年大陆节点撤出后同样时好时坏——两环都断时用户只能吃本地旧缓存。后 3 个入口分别走 Fastly / Gcore / Cloudflare 三张不同的 CDN 网络，封锁是按域名来的，总有能直连通的一个。
- 镜像切换提速：连接超时从 30 秒降到 5 秒，被阻断的源几秒内跳过；某个镜像成功后记住它，后续文件直接从它拉取，不再每个文件都从头把镜像列表试一遍。
  之所以这样改：日报一次要拉 4 个 feed + 5 个 prompt + 若干字幕文件，旧逻辑遇到挂起型阻断时每个文件都要先在坏源上等满 30 秒，整轮下来是分钟级的干等。

### 文档

- SKILL.md 安装一节补第二个 clone 加速前缀（ghfast.top），并注明 `AI_SIGNAL_BASE_URLS` 环境变量可自定义镜像列表。

## 2026-07-05

### 新增

- 播客人物追踪：27 位 AI 关键人物（海外高管/分析师 20 人 + 中国 AI 一线 7 人；7/8 加 Naval 后为 28 人）作为**嘉宾**上任何播客/访谈都会被抓到，不再限于 13 个订阅频道（7/8 加 Naval 后为 14 个）。每天用 yt-dlp 在 YouTube 全网按"人名 + interview/访谈"搜索，命中并入 `feed-podcasts.json`（带 `person` 字段，中国人物带 `region: "cn"`），字幕、摘要、日报管道零改动复用。
  之所以这样改：RSS 频道只覆盖主持人自己发布的节目；高管上别人节目（往往是信息量最大的场合）此前完全漏掉。
  搜索用 YouTube 服务端"本周上传"过滤器（`search_recency` 配置，可选 hour/day/week/month/year）限定时间范围——flat 搜索拿不到上传日期、逐视频取元数据又会被数据中心 IP bot-check，服务端过滤是唯一可靠的时间限制手段；旧访谈、几年前的演讲从源头进不来。
  过滤规则（保证与频道内容同等质量）：标题必须含人名（最干净的同名假阳性过滤）、时长 ≥ 15 分钟（去切片/shorts）、频道订阅数 ≥ 5 万（`min_channel_subscribers` 配置；从零测试实测 5 个命中里 3 个来自几百到几千粉的搬运号，小频道基本是搬运/切片号，直接从源头拦掉；订阅数从频道页 flat 抓取、每次运行内跨搜索缓存，取不到时放行并记日志，不让基础设施故障静默杀掉整个功能）、海外人物剔除非拉丁文字的频道名/标题（订阅数门槛挡不住大号外语搬运/二创——7/6 实测 Khabargaon 印地语二创 112 万粉、最佳拍档中文配音 22.8 万粉、中視新聞中文播报都过了订阅关，但都无英文字幕、非本人真访谈；这类频道给非英语观众看必用本语言命名或写标题，中文/天城文/韩文/阿拉伯文等一律拒；`region:"cn"` 人物豁免，其真访谈本就是中文）、海外人物要求视频有英文字幕轨作为最终兜底（脚本过滤挡不住"英文标题的外语综艺"——7/6 实测韩综 You Quiz 上的 Jensen Huang 标题是英文、频道名也是拉丁字母，但只有韩语字幕；用 `list()` 枚举视频可用字幕语言，无英文轨的海外人物视频一律拒，只放英文原版；网络/IP 故障判为 unknown 不误杀真英文访谈，carry 阶段每天复检自愈漏进的条目）、剔除例行盘面播报与影视合集标题、与频道 RSS 命中的同一期自动去重；已入 feed 的命中在 7 天窗口内保留并每天重试补字幕（实测云端拿不到的字幕，次日本地/代理环境运行可补上）。
  名单在 `config/sources.json` 的 `podcasts.people`，新增 `--people-only` 参数可单独刷新人物搜索。
  防刷屏：每次运行最多新收 5 条人物命中（`max_new_per_run`，按发布时间新的优先，超出的记日志延后）。

### 修复

- 修正 3 个 X 账号 handle：Dylan Patel `dylanpatel_`→`dylan522p`（原 handle 是同名假号，最近一条推是 7 年前的足球话题）、Leopold Aschenbrenner `leopoldaob`→`leopoldasch`（原 handle 不存在）、Jim Keller `jimkeller_`→`jimkxa`（原 handle 不存在）。三个真 handle 均已实测有近期推文。其余"0 条推文"的账号排查后确认 handle 无误，只是 48 小时窗口内没发推（每日运行 + 48h 窗口不会漏推文，属正常）。

- `granularity` 配置值归一化：此前只有 `language` 会把"中文"这类显示标签归一为规范值，`granularity` 存了"精华/标准/完整"会原样透传——"精华"会错误落入标准档摘要。现在与 language 同样处理（精华→highlights、标准→summary、完整→full），manifest 新增 `granularity_raw` 保留原始值。
- YouTube 频道的集数若缺 `rel="alternate"` 链接，改用 `yt:videoId` 拼出 watch URL 兜底。此前该集 `link` 为空串，按"无 URL 不收录"规则会被日报静默丢弃（实测 7/5 No Priors 核能这期就这样丢了）。
- `skill.md` 更名为 `SKILL.md`，符合 Agent Skills 规范的大写文件名。
  之所以这样改：Linux 环境（多数云端 Agent）文件名大小写敏感，小写文件名可能导致 Agent 找不到 skill 定义、只能照 README 即兴引导——实测 WorkBuddy 安装时漏问了推送时间。
- Onboarding 加硬规则：Step 2-6 逐条问、不许跳过；即使 Agent 自己不能定时，也必须问推送时间并存入配置。
- 平台检测从"只认 OpenClaw"改为按定时能力判断：WorkBuddy 等自带定时任务的持久 Agent 走与 OpenClaw 同级的自动推送路径（Step 6 / Step 8 新增对应分支）。
- 补 Windows 适配说明：bash 片段需换成 PowerShell 等价写法；Python 脚本本身跨平台（UTF-8 强制、无硬编码路径）。

### 新增

- feed 拉取加多源镜像：GitHub raw 不可达时自动切换 jsDelivr CDN，全部失败才落本地缓存。大陆无代理用户从此每天能正常收到更新；也可用 `AI_SIGNAL_BASE_URLS` 环境变量自定义镜像列表。
  之所以这样改：raw.githubusercontent.com 在大陆基本不可达，此前无代理用户装上后每天拉取都会失败、只能吃旧缓存。
- README 与 skill.md 补国内安装路径：clone 失败时用 gh-proxy 类镜像加速前缀。
- X/Twitter 抓取加入主题过滤：节日祝福、生活动态、纯社交回复等非 AI 信号不再进入 feed。
  之所以这样改：7 月 4 日美国国庆的刷屏推挤占了 feed 位额。
- 过滤关键词用当日真实 feed 校准：覆盖主流模型名（Claude / Fable / GPT / Gemini 等）、AIE、CLI 等一线语汇，宁可多留不错杀，互动排序兜底。

### 改进

- Onboarding 推送时间加默认值：北京时间早上 7:30。用户说"默认/都行"或没给时间就用它；中央 feed 每天北京时间 6:00 重新生成，7:30 推送刚好拿到最新数据。用户给了其他时区则默认当地 7:30。
- 日报每条内容带稳定编号（推文 X1/X2、播客 P1/P2、论文 Paper1），追问时直接说"展开 P2"、"详细讲讲 Paper1"即可。

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
