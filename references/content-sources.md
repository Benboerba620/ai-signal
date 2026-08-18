# Content Sources

Central feed is updated daily at 6am Beijing time (UTC 22:00) with:

### Podcasts (14 channels)
Dwarkesh Patel, Lex Fridman, Latent Space, All-In Podcast, a16z, Naval, No Priors,
SemiAnalysis (Dylan Patel), Google DeepMind, Y Combinator Startup Podcast, Lenny's Podcast,
Invest Like the Best, Capital Allocators, The Acquirers Podcast

### People tracking (28 people, YouTube-wide guest search)
Beyond the fixed channels, the central feed searches YouTube daily for these
people appearing as podcast/interview **guests** anywhere, limited server-side
to videos uploaded in the past week. Channels under 50k subscribers are
rejected (small re-upload accounts), and for overseas people, channels or
titles in a non-Latin script are rejected too (large foreign-language
dub/reaction channels carry no English transcript and aren't real interviews).
As a definitive backstop, an overseas-person video with no English caption
track at all is rejected — this catches foreign shows that use an English title
(e.g. Jensen Huang on the Korean variety show You Quiz on the Block, captions
only in Korean). Only English originals get through. Videos that merely talk
ABOUT the person are rejected too — a title whose grammar puts the name in
topic position ("Journalist Karen Hao on Sam Altman...", "the truth about X")
is coverage, not an appearance; only videos where the person actually speaks
count. Hits merge into the same
podcast feed with a `person` field (and `region: "cn"` for China AI voices,
which are exempt from both filters).

**Overseas:** Sundar Pichai, Greg Brockman, Sam Altman, Demis Hassabis, Jensen Huang,
Satya Nadella, Mark Zuckerberg; Anthropic (Dario/Daniela Amodei, Krishna Rao,
Mike Krieger, Sholto Douglas, Amanda Askell, Boris Cherny, Cat Wu, Alex Albert);
Kevin Weil (OpenAI), Ivan Zhao (Notion), Dylan Patel (SemiAnalysis), Gavin Baker (Atreides),
Naval Ravikant

**China AI:** 闫俊杰 (MiniMax), 杨植麟 (Moonshot), 梁文锋 (DeepSeek), 唐杰 (智谱),
罗福莉, 李广密 (拾象), 肖弘 (Manus)

### Twitter/X (18 accounts)
**Analysts:** Karpathy, Swyx, Dylan Patel (SemiAnalysis), Irrational Analysis, Naval Ravikant,
Jim Keller, Gavin Baker (Atreides Management)
**Executives:** Sam Altman, Dario Amodei, Demis Hassabis (Google DeepMind), Tang Jie (Z.ai),
Jensen Huang (NVIDIA CEO)
**Builders:** Amanda Askell, Boris Cherny (Claude Code), Cat Wu, Alex Albert, Guillermo Rauch (Vercel), Josh Woodward (Google Labs)

Analysts and Executives are *judgment tiers* (`judgment_tiers` in
`config/sources.json`): their posts skip the topic keyword gate and only social
noise is dropped, because judgment is written in plain language. Builders keep
the keyword gate — they post product announcements, which always name a product.
Quote tweets are fetched together with the post they quote, and the quoted text
participates in both filtering and rendering.

### arXiv Papers (daily, up to 30)
cs.AI (Artificial Intelligence), cs.CL (Computation and Language), cs.LG (Machine Learning)

All feeds are fetched centrally. **No API keys needed for content.**
