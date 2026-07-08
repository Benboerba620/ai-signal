# [AINews] Lilian Weng summarizes 35 papers on Harness Engineering for RSI

- Type: podcast
- Profile: zh_standard
- Model: deepseek-v4-pro
- Generated: 2026-07-08T23:04:18.759954+00:00
- Channel: Latent Space
- Source: https://www.latent.space/p/ainews-lilian-weng-summarizes-35

## Summary

**简要总结**

Lilian Weng 发布了一篇关于“Harness Engineering”（外挂工程）在递归自我改进（RSI）中作用的研究综述，总结了 35 篇相关论文。核心观点是：AI 的自我改进未来将高度依赖外挂系统，而非直接修改模型权重。外挂工程正在成为智能体设计的中心，其发展方向是实现自动化研究，并催生更智能的系统。即使部分外挂功能未来会被内化到基础模型中，但定义目标与上下文的需求永远不会消失。

**Core takeaways**

- **外挂工程是 RSI 的核心**：Lilian Weng 将递归自我改进的讨论焦点从直接的权重自我修改，重新定位到外挂系统上。她认为，外挂工程将朝着自我改进的方向演进，最终实现自动化研究。
- **外挂设计趋势明确**：文章梳理了已被验证的主流外挂设计模式，并回顾了从著名的 ACE 论文到最新的“元外挂”（Meta-Harnesses）等外挂优化文献。
- **目标与上下文定义不可替代**：即便许多外挂改进最终会被整合进核心模型，但“指定目标和上下文的需求不会消失”，这为外挂工程提供了长期存在的价值基础。

**Details worth expanding**

- **外挂工程的行业共识**：不仅是 Lilian Weng，Greg Brockman 也在悄然认可智能体/外挂工程。这反映了业界从单纯追求模型能力，转向重视模型与外部工具、流程协同的系统性工程。
- **产品化趋势明显**：
    - **Anthropic** 推出了 Claude Cowork，将 Claude 定位为在后台运行任务的“队友”，而非前台聊天界面。
    - **Google** 的 Gemini API 托管智能体增加了后台执行、远程 MCP 服务器、自定义函数调用等功能。
    - **LangChain** 推出了新的 Deep Agents 课程和一个开源外挂项目。
- **外挂优化的前沿**：综述涵盖了从 ACE 到“元外挂”的最新趋势。“元外挂”可能指用于设计、优化或管理其他外挂系统的更高层级系统，这暗示了外挂工程自身的自动化和规模化潜力。
- **对 Thinky 的暗示**：Lilian Weng 作为初创公司 Thinky 的联合创始人，这篇综述可能暗示了 Thinky 的研究方向，不仅仅是“交互模型”，更可能深入到外挂工程领域。

**Implications for AI, investing, products, or research**

- **对 AI 研究与产品**：研究重心正从模型架构转向系统集成。未来的 AI 产品竞争力将更多体现在外挂系统的设计上，即如何高效、可靠地让模型调用工具、执行多步工作流并进行自我纠错。
- **对投资**：投资价值可能从单纯的基础模型层，向能够构建复杂、可靠智能体系统的中间件和工具层转移。能够解决“最后一公里”可靠性问题的外挂工程公司，如 LangChain 和 Thinky，可能具有重要战略价值。
- **对产品设计**：产品设计范式从“对话式 UI”转向“后台协作者”。用户体验的核心不再是单次交互的质量，而是智能体在后台持续、自主完成任务的能力和可信度。
- **对研究**：外挂工程自身的自动化（元外挂）和优化成为一个独立且有前景的研究方向。如何系统性地减少智能体在长流程任务中的失败模式（如 Liquid AI 的 Antidoom 方法解决推理循环），将是提升系统整体智能的关键。

**Source link**
https://www.latent.space/p/ainews-lilian-weng-summarizes-35
