# What a harness is and how to build one with Claude Agent SDK

- Type: podcast
- Profile: zh_short
- Model: deepseek-v4-pro
- Generated: 2026-07-08T23:02:37.737321+00:00
- Channel: Lenny's Podcast
- Source: https://www.lennysnewsletter.com/p/what-a-harness-is-and-how-to-build

## Summary

AI 中的“线束”（harness）不是模型本身，而是围绕模型构建的一套结构化工作流、权限和工具链。它把重复的、多步骤的任务（如 Sentry 错误分诊）固化下来，让 Agent 自动完成证据收集、根因分析和后续产物生成，无需每次手动输入提示。

**核心要点**
- 线束是“模型+流程+工具”的封装，适用于高频、结构化的业务工作流，而非一次性探索任务。
- 每个线束需包含三个组件：运行管理、任务编排、工具与产物定义。
- 通过编码特定权限，线束能安全地访问 Sentry、Linear、GitHub 等生产工具，避免通用 Agent 的权限失控。
- 产物需结构化输出（如修复 PR、分析报告），以便全团队直接使用，而非仅生成对话文本。

**值得展开的细节**
- 嘉宾使用 Claude Agent SDK 构建线束，并用 GPT-5.5 和 Claude Opus 辅助编写代码，两者在初期对某些架构选择有“抵触”。
- 终端界面基于 Ink 库构建，提供定制化交互，区别于通用 CLI 工具。
- 线束架构围绕“runs, tasks, tools, artifacts”组织，确保每次执行可追溯、可复用。

**对 AI 与产品的启示**
- 产品化 AI 的关键正从“选模型”转向“建线束”，将隐性专家流程显性化、自动化。
- 投资和研发应关注如何将领域知识编码为可复用的 Agent 工作流，而非单纯追求模型能力。
- 线束思维可应用于任何重复性认知工作，如合规审查、客户支持分诊、数据管道监控。

来源：https://www.lennysnewsletter.com/p/what-a-harness-is-and-how-to-build
