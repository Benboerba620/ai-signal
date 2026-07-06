# 🎙️ How I AI: Sonnet 5 review & How to run autonomous coding agents from your phone

- Type: podcast
- Profile: zh_standard
- Model: deepseek-v4-pro
- Generated: 2026-07-06T23:03:45.536048+00:00
- Channel: Lenny's Podcast
- Source: https://www.lennysnewsletter.com/p/how-i-ai-sonnet-5-review-and-how

## Summary

**简要总结**

Claire 对 Anthropic 新模型 Sonnet 5 进行了系统性盲测，将其与 Sonnet 4.6、Opus 4.8、GPT-5.5 和 Gemini 3 Pro 在 PRD 撰写、原型构建、智能体任务和智能体个性四个维度上做了对比。她构建了一套可复现的基准测试框架，核心发现是：Sonnet 5 定价介于前代 Sonnet 和 Opus 之间，但在她的个人偏好排名中垫底，性价比优势仅在特定场景成立。另一部分内容中，Alessio Fanelli 展示了如何通过手机管理运行在云端的自主编程智能体，核心转变是从“提示词输入者”变为“智能体管理者”，并分享了代币成本追踪、技能文件清理等实操经验。

**Core takeaways**

- Sonnet 5 定价为每百万输入 token 2 美元、每百万输出 token 10 美元，处于中间价位，但 Claire 的盲测排名显示它并未自动取代 Sonnet 4.6 或 Opus 4.8。
- 一次性“感觉检查”不可复现，真正有用的基准测试需要冻结输入、固定评分标准和相同任务。
- LLM 作为评判者过于宽容，评分集中在中间段，无法捕捉人类一眼就能发现的视觉问题（如原型崩溃、忽略线框约束）。
- Claire 的个人排名与 LLM 评判排名几乎完全相反，她通过 70% 人工权重 + 30% LLM 权重的混合指数，让 Sonnet 4.6 跃居第一。
- 按任务推荐：GPT-5.5 适合 PRD，Sonnet 4.6 适合原型和闲聊，Opus 4.8 或 Sonnet 5 适合代码库导航。
- 从“智能体提示者”到“智能体管理者”的转变是多数人尚未掌握的关键，将工作负载迁移到云 VPS 并通过 Linear 等工具异步管理才能真正释放效率。
- 代币成本追踪是智能体设置的基础能力，Alessio 展示的任务代币消耗从 1500 万到 2.21 亿不等，没有账本就无法优化。
- 技能文件需要每隔几个月清理一次，否则模型会不断追加指令而非替换，最终导致内部矛盾。
- AI 最大的未开发机会在于异构数据领域（如交易卡、古着、鱼类库存），LLM 是首个无需大量预处理即可处理这类混乱数据的技术。

**Details worth expanding**

Claire 使用 Claude Code 读取历史会话记录来生成个性化基准任务，整个过程只需一个简单提示词。她手动对 5 个模型的 64 次生成结果进行 1-5 分评分，并发现人工信号是整个基准测试中最有价值的部分。构建 HTML 评分页面并导出 JSON 仅需约 45 分钟。智能体 bug 搜寻任务因所有模型都完美通过而失去区分度，将被淘汰。

Alessio 的 Symphony 框架本质上是一个高度约束的 Markdown 规范文件，模型已足够强大来忠实遵循它。Kernel Labs 构建了 Glimpse（Playwright 扩展）来为编程智能体提供截图、视觉差异和视频等感知能力，因为瓶颈不在编排层，而在智能体遇到 UI 歧义时频繁求助。他用 Codex 加浏览器访问权限来寻找 eBay 上被低估的 PSA 评级宝可梦卡，将原本需要大量人力、时间和专业知识的套利机会压缩为一条精心编写的提示词。

**Implications for AI, investing, products, or research**

- 模型评估需要建立个人化、可复现的基准，而非依赖通用排行榜或一次性测试，这对 AI 产品选型有直接指导意义。
- LLM-as-judge 的局限性表明，视觉和主观体验维度的评估仍需人类参与，自动化评估工具需要编码更多个人偏好才能实用。
- 智能体管理范式的转变意味着工具链机会在于异步协作层（如 Linear 作为状态机）和感知层（视觉反馈），而非更复杂的编排框架。
- 代币成本追踪应成为智能体基础设施的标准功能，为优化提示词和工具选择提供数据闭环。
- 小型企业利用 AI 获得的不对称杠杆是当前被低估的投资主题，一两人团队可实现以往需要大规模组织才能达成的产出。
- 异构数据处理能力使 AI 能够进入此前无法规模化的利基市场，如收藏品套利、库存管理等。

**Source link**
https://www.lennysnewsletter.com/p/how-i-ai-sonnet-5-review-and-how
