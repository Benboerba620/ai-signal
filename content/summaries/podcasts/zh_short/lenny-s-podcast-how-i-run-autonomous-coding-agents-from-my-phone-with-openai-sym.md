# How I run autonomous coding agents from my phone with OpenAI Symphony + Linear | Alessio Fanelli (Kernel Labs)

- Type: podcast
- Profile: zh_short
- Model: deepseek-v4-pro
- Generated: 2026-07-06T23:03:16.953582+00:00
- Channel: Lenny's Podcast
- Source: https://www.lennysnewsletter.com/p/how-i-run-autonomous-coding-agents

## Summary

Alessio Fanelli 展示了两种自主 AI 工作流：一是用 OpenAI Symphony 结合 Linear 作为状态机，从手机端完全自主管理编码代理的完整开发周期；二是用 Codex 自动浏览 eBay，抓取 PSA 证书编号，为他的卡牌店实时筛选被低估的 Pokémon 卡片。

**核心要点**
- **代理管理者而非提示词工程师**：应将自身定位为“代理管理者”，负责设定目标与约束，而非逐句编写提示词。
- **Linear 作为状态机**：Linear 负责追踪任务状态与流转，Symphony 据此调度代理执行，实现无需人工干预的开发闭环。
- **云端扩展性**：本地 Mac Mini 无法规模化，迁移至云端 VPS 是运行大规模并行代理的关键。
- **精简指令文件**：CLAUDE.md 等技能文件需要定期彻底清理，过多的指令反而会降低代理性能。

**值得展开的细节**
- 系统可追踪每个任务的 token 消耗，例如 2.21 亿 token 的实际购买力。
- Glimpse 工具通过增强代理的“感官”，能显著延长其自主运行时间。
- Codex 可自主浏览网页、提取结构化数据并标记交易机会，展示了 AI 赋能的新型小生意模式。

**对 AI 与产品的启示**
- 将项目管理工具改造为代理状态机，是构建可靠自主系统的可行路径。
- 自主代理正在催生超低成本、高杠杆的新型小生意，如自动化的收藏品套利。
- 产品设计应从“如何让用户输入提示词”转向“如何让用户设定目标并监控结果”。

**来源**：https://www.lennysnewsletter.com/p/how-i-run-autonomous-coding-agents
