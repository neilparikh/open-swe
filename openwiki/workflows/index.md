# Files

- [Context assembly and repository guidance](context-engineering.md) - How Open SWE converts inbound content into attributed model messages and combines it with prompt layers, repository guidance, skills, sender data, and privacy-scoped recent-thread context.
- [Follow-ups, interruptions, and deferred delivery](follow-up-messages.md) - How Open SWE routes follow-up work to durable runs or a database-backed thread queue, drains queued messages before model calls, and handles stop and completion delivery without losing thread continuity.
- [Inbound Invocation to Durable Run](invocation.md) - How dashboard, desktop, webhook, and scheduled inputs are authenticated, routed and normalized into durable LangGraph runs, then reconciled when they finish.
- [Coding delivery and pull-request creation](pr-creation.md) - How Open SWE turns sandbox changes into attributed GitHub pull requests, protects workflow-file pushes, records delivery on a thread, and supports CI, review, and human pull-request controls.
- [Pull-request review lifecycle](pr-review.md) - How Open SWE admits GitHub and explicit PR-review requests, prepares a diff-grounded reviewer run, persists and publishes findings, and follows later pushes and human feedback.
- [Schedules, automations, and PR babysitting](scheduling-and-baby-sit.md) - How Open SWE stores and dispatches scheduled and event-triggered automations, recovers stuck work, monitors background commands, and provides opt-in, evidence-based PR CI babysitting.
