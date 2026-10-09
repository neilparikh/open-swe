Send a message to the person in this Microsoft Teams direct message. This is
the only way your words reach them: a plain assistant message is never delivered.

Use this for clarifying questions, essential progress updates, and the final
answer or outcome. `response_type` says whether this reply ends your turn.
Use `"progress"` for a reply you will keep working after — the opening
acknowledgement, an interim status note. Use `"final"` for anything that leaves
the person holding the ball: the answer, the outcome, a failure, a blocking
question, an approval request. A `progress` reply settles nothing, so a turn
that ends on one is treated as an unanswered turn.

Put the complete answer to an information-only request in `message`, and do not
repeat it in a final assistant response. Keep `message` short: default to one or
two sentences with the outcome and any link. Omit greetings, preambles, recaps,
and implementation details; use bullets only when several items are essential.

Format `message` in Markdown: **bold**, _italic_, [link text](url), lists, and
fenced code blocks with a language identifier for code, commands, and logs.
Never paste long output, diffs, or multi-section write-ups; publish detail with
`save_plan` and send a one-line summary with its link.

When asking the person to choose among concrete answers or actions, pass
`options` to offer up to five one-click answer buttons, including for blocking
questions and approvals; a click is sent to you as their reply. Use short,
concrete button labels. In `message`, explain exactly what each button means
and what choosing it will do; never rely on the short label alone. They can
still reply in their own words. Do not invent choices for open-ended questions.
