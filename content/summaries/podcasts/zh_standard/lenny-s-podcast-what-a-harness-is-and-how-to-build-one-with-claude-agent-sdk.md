# What a harness is and how to build one with Claude Agent SDK

- Type: podcast
- Profile: zh_standard
- Model: deepseek-v4-pro
- Generated: 2026-07-08T23:03:29.099760+00:00
- Channel: Lenny's Podcast
- Source: https://www.lennysnewsletter.com/p/what-a-harness-is-and-how-to-build

## Summary

**简要总结**

Claire Vo 在节目中现场构建了一个基于 Claude Agent SDK 的“线束”（harness），用于自动化 ChatPRD 公司的 Sentry 错误分诊流程。该线束集成了 Sentry、Linear、GitHub 和 Vercel，能自动完成证据收集、根因分析和后续工件创建，彻底省去了手动输入“请修复此错误”的提示词。节目详细拆解了线束的定义、架构、代码结构及构建过程，并给出了可复用的构建方法。

**Core takeaways**

- **线束的定义**：线束是为特定重复性工作流定制的、封装了权限、工具和输出格式的 AI 代理外壳。它不同于通用工具，核心在于将“如何做”的结构性知识固化下来。
- **何时构建线束**：当某个工作流足够结构化、重复性高，且通用工具（如 Claude Code 或 Codex）无法满足特定权限控制或输出格式要求时，就应构建线束。
- **三大必备组件**：每个线束都需要包含运行管理、任务编排、工具适配和工件产出这四个核心部分，以确保流程可追溯、可复用。
- **构建过程**：Claire 使用 GPT-5.5 和 Claude Opus 来编写线束代码，并指出两个模型在初期都有所抵触，但最终完成了构建。

**Details worth expanding**

- **具体架构**：线束架构围绕“运行、任务、工具、工件”展开。运行是一次完整的自动化流程实例；任务定义了流程中的具体步骤；工具是连接外部服务的适配器（如 Sentry 适配器用于拉取错误详情）；工件是线束产出的标准化结果，如自动创建的 Linear 工单或 GitHub issue。
- **界面实现**：线束配备了一个基于 Ink 库构建的自定义终端 UI，用于交互式展示调查过程和结果。
- **模型使用**：线束内部调用 Claude Sonnet 4.6 执行推理任务，而构建线束本身则使用了 GPT-5.5 和 Claude Opus。
- **代码结构**：节目展示了具体的代码文件布局和关键代码片段，为开发者提供了直接参考。

**Implications for AI, investing, products, or research**

- **对 AI 应用层**：线束概念标志着 AI 代理从“通用对话”向“结构化工作流自动化”的关键转变。它强调将领域知识、权限边界和输出规范固化到代理外壳中，是构建可靠企业级 AI 应用的核心模式。
- **对产品与投资**：能够为垂直行业高频、结构化场景（如 DevOps 事故响应、客服工单分诊、合规审查）构建标准化线束的公司，将拥有显著的效率和可靠性护城河。投资机会在于提供线束构建平台或垂直领域预置线束的产品。
- **对研究**：线束的架构设计（运行、任务、工具、工件）为评估 AI 代理在长周期、多步骤任务中的可靠性和可观测性提供了框架。如何让模型更好地遵循线束定义的严格流程，而非“自由发挥”，是模型对齐和可控性研究的重要方向。

**Source link**
https://www.lennysnewsletter.com/p/what-a-harness-is-and-how-to-build
