# Files

- [Authentication, authorization, and credential scope](auth-and-security.md) - How Open SWE authenticates dashboard users and inbound requests, chooses GitHub authority, scopes sandbox credentials, and protects persisted secrets.
- [Models, profiles, settings, and instructions](models-profiles-instructions.md) - How Open SWE resolves models and reasoning effort from deployment, instance, workspace, profile, thread, and runtime layers, then constructs provider clients. Covers persisted settings, routing and fallback, and the scoped instruction sources used to compose an agent prompt.
- [Threads, durable runs, and persistent state](threads-and-state.md) - How Open SWE identifies LangGraph threads, dispatches checkpointed runs, normalizes follow-up input, and separates LangGraph, PostgreSQL, Store, user, workspace, task, and transcript state.
- [Tool capability model](tools.md) - How Open SWE assembles graph-specific tool surfaces, loads MCP tools on demand, scopes credentials, and enforces access and safety gates. Use this page when adding a capability or changing its availability.
