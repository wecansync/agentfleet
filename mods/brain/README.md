# brain

`/brain` opens a scrollable pane with agent-brain token usage for this session, today and this week, followed by the latest project memories when available. Use ↑/↓ or j/k to select a memory, press Enter to expand or collapse its full text, and Esc to close the pane. The mod reads agent-brain's local data through its MCP server.

When you have set model prices (with `agentfleet config prices` or a team preset), `/brain` also shows an `Est. cost:` line after "This week", estimating this week's spend from the main conversation and lane runs, with a note for any model that has no price or no cache price. Without prices the line is not shown.
