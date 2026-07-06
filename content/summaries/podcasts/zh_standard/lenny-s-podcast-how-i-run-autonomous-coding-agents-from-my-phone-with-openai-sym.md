# How I run autonomous coding agents from my phone with OpenAI Symphony + Linear | Alessio Fanelli (Kernel Labs)

- Type: podcast
- Profile: zh_standard
- Model: deepseek-v4-pro
- Generated: 2026-07-06T23:03:58.730770+00:00
- Channel: Lenny's Podcast
- Source: https://www.lennysnewsletter.com/p/how-i-run-autonomous-coding-agents

## Summary

**简要总结**

Alessio Fanelli 展示了两种高度自主的 AI 工作流：一是通过 OpenAI Symphony 与 Linear 构建的完全自主编码系统，Linear 充当状态机，Symphony 管理代理完成整个开发生命周期，无需人工干预；二是利用 Codex 的浏览器访问能力，在 eBay 上自主搜索、提取 PSA 证书编号，为他的卡牌店标记价值 1 万至 2 万美元的低价宝可梦卡牌。

**Core takeaways**

- **“代理管理者”优于“代理提示者”**：核心思维转变在于，不应将 AI 代理视为需要不断提示的工具，而应将其作为可管理、可委派任务的自主实体。
- **Linear 作为代理状态机**：通过将 Linear 的项目管理功能与 Symphony 的代理编排能力结合，可以构建一个闭环的自主开发系统。Linear 负责定义任务状态和流转，Symphony 驱动代理执行。
- **云端 VPS 是规模化关键**：本地 Mac Mini 无法满足多代理并行运行的需求，迁移至云端 VPS 是实现代理自主运行和规模化的必要条件。
- **精简指令优于堆砌指令**：CLAUDE.md 等技能文件不应无限增加指令，而应定期清理，仅保留最核心、最有效的上下文，以避免代理行为混乱。
- **AI 催生新型小企业**：利用 Codex 进行大规模、自动化的利基市场套利（如搜寻低价稀有卡牌），代表了一种因 AI 能力而成为可能的新型小生意模式。

**Details worth expanding**

- **Symphony + Linear 工作流**：该设置的核心是将 Linear 作为任务和状态的真实来源。Symphony 代理读取 Linear 中的任务，自主完成编码、测试、代码审查等步骤，并自动更新任务状态。这实现了从需求到部署的全生命周期“零看护”。
- **成本追踪**：Fanelli 强调了追踪每次任务的 token 消耗的重要性，并提及了一个具体数字——221 百万 tokens 所能购买的计算能力，这为评估代理运行成本提供了实际参考。
- **Glimpse 的作用**：Glimpse 工具旨在为代理提供更好的“感官”，使其能更准确地感知环境状态，从而延长自主运行的时间，减少因信息不足而中断的情况。
- **Codex 卡牌搜寻演示**：Codex 代理被赋予浏览器访问权限，在 eBay 上自主浏览列表，提取 PSA 证书号码以验证卡牌真伪和品相，并实时比对市场价格，最终标记出被低估的高价值卡牌。

**Implications for AI, investing, products, or research**

- **AI 产品开发**：将项目管理工具（如 Linear）作为代理的“状态机”这一模式，为构建更复杂、更可靠的自主 AI 系统提供了可复制的架构蓝图。未来的 AI 开发工具可能深度集成此类状态管理。
- **投资视角**：AI 代理的自主性正在从“辅助编码”向“全生命周期管理”演进，这可能会改变软件开发的成本结构和团队构成。同时，AI 驱动的利基市场套利能力，暗示了在收藏品、二手商品等非标品领域存在新的自动化商机。
- **研究启示**：代理的“感官”能力（如 Glimpse）和上下文管理（如精简 CLAUDE.md）是提升其自主性和稳定性的关键研究方向。如何让代理在长时间运行中保持专注、不偏离目标，是工程落地的核心挑战。

**Source link**
https://www.lennysnewsletter.com/p/how-i-run-autonomous-coding-agents
