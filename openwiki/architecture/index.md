# Files

- [Coding-agent graph assembly](agent-graph.md) - Per-run construction of the Open SWE Deep Agent graph, from tolerant run configuration and thread-bound sandbox selection through models, prompts, tools, skills, subagents, and execution middleware.
- [Agent middleware and failure boundaries](middleware-stack.md) - Ordering-sensitive middleware around Open SWE coding-agent and reviewer graph loops. Covers run preparation, transcript and follow-up delivery, tool and workflow safety, reply obligations, model routing and recovery, and accounting.
- [Runtime architecture and public entrypoints](overview.md) - How Open SWE deploys LangGraph graph factories behind a composed FastAPI application, with PostgreSQL services and dashboard, desktop, CLI, and webhook entrypoints.
- [Review, review-scout, and style-learning graphs](reviewer-and-analyzer.md) - How Open SWE prepares and performs PR reviews, persists and reconciles findings, produces an optional review walkthrough, and learns repository-specific review guidance.
- [Thread-bound sandbox lifecycle](sandbox-lifecycle.md) - How Open SWE binds a thread to a hosted sandbox or a bridged local machine, reconnects it safely, and carries work across explicit moves. Covers workspace boot configuration, task sharing, GitHub proxy refresh, checkout handoff, and unreachable-machine behavior.
