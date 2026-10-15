# Interface comparison

**Retained from the reference interface:** the file-backed JSON API over `analysis/state/` (annotations, samples, patterns, suggestions) and margin notes attached to selected text. The API keeps the error-discovery workflow intact: the coding agent reads my notes and pushes taxonomy and suggestions through the same files.

**Changed after inspecting my traces:** in the standard Langfuse view, each tool call opened in a separate pane that replaced the message, so I couldn't compare a tool result with the reply describing it. A multi-turn conversation was split across separate traces, and the role was only visible in the metadata at the bottom. My interface groups traces by the `cartwheel.session_id` attribute, because Langfuse's own `sessionId` is empty on these traces. It shows each turn in one column: user message, the agent's text, each tool call paired with a one-line result summary, then the reply. The role, user, store and scenario sit in a header bar. I rebuilt the agent's text between tool calls from the last generation's input, because the generation outputs are empty and the reference interface's loader would have dropped that text.

**Limitation remaining:** the taxonomy view is read-only. Modes are created, renamed and revised through the coding agent, which writes `patterns.json`; I can't edit a definition in the interface itself. Also, a label for a multi-turn conversation is written as one Langfuse score on the first turn's trace, so a per-trace Langfuse query sees the label only on that turn.

**Data source:** live Langfuse.
